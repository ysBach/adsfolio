"""Sidecar JSON management for PDF identification metadata."""

import copy
import json
import shutil
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .identify_settings import IdentifySettings

SIDECAR_FILENAME = ".adsfolio-pdfs.json"
SIDECAR_VERSION = 2

__all__ = ["SIDECAR_FILENAME", "SIDECAR_VERSION", "Sidecar", "SidecarError"]


class SidecarError(Exception):
    """Raised when sidecar data cannot be loaded or validated."""


def _normalize_unicode(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_unicode(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_unicode(item) for key, item in value.items()}
    return value


class Sidecar:
    """Manage ``.adsfolio-pdfs.json`` files for a single directory."""

    def __init__(self, directory: Path, filename: str = SIDECAR_FILENAME):
        self.directory = Path(directory)
        self.path = self.directory / filename
        self._data = self._load()
        self._sha_index: dict[str, list[str]] = {}
        self._current_filename_index: dict[str, list[str]] = {}
        self._rebuild_indexes()

    @property
    def data(self) -> dict[str, Any]:
        """Return a copy of the current sidecar payload."""
        return copy.deepcopy(self._data)

    @property
    def files(self) -> dict[str, dict[str, Any]]:
        """Return a copy of file records keyed by original filename."""
        return copy.deepcopy(self._data["files"])

    @property
    def identify_profile(self) -> dict[str, Any] | None:
        """Return copied identify profile metadata if present."""
        profile = self._data.get("identify_profile")
        if profile is None:
            return None
        return copy.deepcopy(profile)

    @property
    def identify_settings(self) -> IdentifySettings | None:
        """Return reusable Identify behavior when explicitly present."""
        raw = self._data.get("identify_settings")
        if isinstance(raw, Mapping):
            return IdentifySettings.from_mapping(raw)
        return None

    def _default_payload(self) -> dict[str, Any]:
        return {"version": SIDECAR_VERSION, "files": {}}

    def _normalize_profile_year(self, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _normalize_profile_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(text)
        return normalized

    def _normalize_identify_profile(self, profile: Any) -> dict[str, Any] | None:
        if not isinstance(profile, Mapping):
            return None

        end_key = "end_year" if "end_year" in profile else "retire_year"
        normalized = {
            "orcid": str(profile.get("orcid", "")).strip(),
            "query_names": self._normalize_profile_list(profile.get("query_names", [])),
            "debut_year": self._normalize_profile_year(profile.get("debut_year")),
            "library_ids": self._normalize_profile_list(profile.get("library_ids", [])),
        }
        normalized[end_key] = self._normalize_profile_year(
            profile.get(end_key, profile.get("retire_year"))
        )
        if not (
            normalized["orcid"]
            or normalized["query_names"]
            or normalized["debut_year"] is not None
            or normalized[end_key] is not None
            or normalized["library_ids"]
        ):
            return None
        return normalized

    def _load(self) -> dict[str, Any]:
        load_path = self.path
        migrated_path = self.path.with_name(f"{self.path.name}.migrated")
        if not load_path.exists() and migrated_path.exists():
            load_path = migrated_path
        if not load_path.exists():
            return self._default_payload()

        try:
            raw = json.loads(load_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SidecarError(f"Invalid sidecar JSON: {self.path}") from exc

        return self._migrate_payload(raw)

    def reload(self) -> dict[str, Any]:
        """Reload the sidecar from disk and return a copy."""
        self._data = self._load()
        self._rebuild_indexes()
        return self.data

    def _add_index_entry(
        self,
        index: dict[str, list[str]],
        key: str,
        original_filename: str,
    ) -> None:
        bucket = index.setdefault(key, [])
        if original_filename not in bucket:
            bucket.append(original_filename)

    def _remove_index_entry(
        self,
        index: dict[str, list[str]],
        key: str,
        original_filename: str,
    ) -> None:
        bucket = index.get(key)
        if bucket is None:
            return
        filtered = [item for item in bucket if item != original_filename]
        if filtered:
            index[key] = filtered
        else:
            del index[key]

    def _record_current_filename(self, original: str, record: Mapping[str, Any]) -> str:
        return unicodedata.normalize(
            "NFC",
            str(record.get("current_filename", original)),
        )

    def _index_record(self, original: str, record: Mapping[str, Any]) -> None:
        original_nfc = unicodedata.normalize("NFC", original)
        sha256 = str(record.get("sha256", "")).strip()
        if sha256:
            self._add_index_entry(self._sha_index, sha256, original_nfc)

        current = self._record_current_filename(original_nfc, record)
        self._add_index_entry(self._current_filename_index, current, original_nfc)
        self._add_index_entry(self._current_filename_index, original_nfc, original_nfc)

    def _deindex_record(self, original: str, record: Mapping[str, Any]) -> None:
        original_nfc = unicodedata.normalize("NFC", original)
        sha256 = str(record.get("sha256", "")).strip()
        if sha256:
            self._remove_index_entry(self._sha_index, sha256, original_nfc)

        current = self._record_current_filename(original_nfc, record)
        self._remove_index_entry(self._current_filename_index, current, original_nfc)
        self._remove_index_entry(
            self._current_filename_index, original_nfc, original_nfc
        )

    def _rebuild_indexes(self) -> None:
        self._sha_index = {}
        self._current_filename_index = {}
        for original, record in self._data["files"].items():
            self._index_record(original, record)

    def _infer_source(self, record: Mapping[str, Any]) -> str:
        source = str(record.get("source", "")).strip().lower()
        if source in {"arxiv", "published", "unknown"}:
            return source

        bibcode = str(record.get("bibcode", "")).strip().lower()
        if "arxiv" in bibcode:
            return "arxiv"

        bibstem = record.get("bibstem")
        if isinstance(bibstem, list):
            lowered = [str(item).lower() for item in bibstem]
            if any("arxiv" in item for item in lowered):
                return "arxiv"

        doi = str(record.get("doi", "")).strip().lower()
        if doi.startswith("10.48550/arxiv."):
            return "arxiv"

        if bibcode:
            return "published"
        return "unknown"

    def _infer_upgrade_status(self, source: str) -> str:
        if source == "arxiv":
            return "arxiv_only"
        if source == "published":
            return "published"
        return "arxiv_only"

    def _normalize_record(
        self, original: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        record = _normalize_unicode(record)
        record.setdefault("current_filename", original)
        source = self._infer_source(record)
        record["source"] = source
        record.setdefault("upgrade_status", self._infer_upgrade_status(source))
        record.setdefault("upgraded_at", None)
        record.setdefault("arxiv_backup", None)
        record.setdefault("annotations_ported", 0)
        record.setdefault("annotations_failed", 0)
        record.setdefault("unported_annotations_file", None)

        if source == "arxiv":
            record.setdefault("original_bibcode", record.get("bibcode"))
        else:
            record.setdefault("original_bibcode", None)
        return record

    def _migrate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise SidecarError("Sidecar payload must be a JSON object.")

        files: dict[str, dict[str, Any]]
        if isinstance(payload.get("files"), dict):
            files = payload["files"]
        elif "version" not in payload:
            files = payload
        else:
            files = {}

        normalized = self._normalize_files(files)
        migrated: dict[str, Any] = {"version": SIDECAR_VERSION, "files": normalized}
        profile = self._normalize_identify_profile(payload.get("identify_profile"))
        if profile is not None:
            migrated["identify_profile"] = profile
        settings = payload.get("identify_settings")
        if isinstance(settings, Mapping):
            migrated["identify_settings"] = IdentifySettings.from_mapping(
                settings
            ).to_mapping()
        return migrated

    def _normalize_files(self, files: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in files.items():
            if not isinstance(value, dict):
                continue
            original = unicodedata.normalize("NFC", str(key))
            record = self._normalize_record(original, copy.deepcopy(value))
            normalized[original] = record
        return normalized

    def save(self) -> Path:
        """Persist current sidecar payload to disk."""
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            backup = self.path.with_name(f"{self.path.name}.bak")
            shutil.copy2(self.path, backup)

        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        text = json.dumps(self._data, indent=2, sort_keys=True, ensure_ascii=False)
        tmp_path.write_text(text + "\n", encoding="utf-8")
        tmp_path.replace(self.path)
        return self.path

    def get_file(self, original_filename: str) -> dict[str, Any] | None:
        """Return metadata for an original filename, if present."""
        original_filename = unicodedata.normalize("NFC", original_filename)
        record = self._data["files"].get(original_filename)
        if record is None:
            return None
        return copy.deepcopy(record)

    def upsert_file(
        self, original_filename: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Insert or replace a file metadata record."""
        original_filename = unicodedata.normalize("NFC", original_filename)
        existing = self._data["files"].get(original_filename)
        if existing is not None:
            self._deindex_record(original_filename, existing)
        record = self._normalize_record(
            original_filename, copy.deepcopy(dict(metadata))
        )
        self._data["files"][original_filename] = record
        self._index_record(original_filename, record)
        return copy.deepcopy(record)

    def set_current_filename(
        self, original_filename: str, current_filename: str
    ) -> bool:
        """Update current filename for an existing record."""
        original_filename = unicodedata.normalize("NFC", original_filename)
        current_filename = unicodedata.normalize("NFC", current_filename)
        if original_filename not in self._data["files"]:
            return False
        current_record = self._data["files"][original_filename]
        current_existing = self._record_current_filename(
            original_filename, current_record
        )
        if current_existing == current_filename:
            return True
        self._remove_index_entry(
            self._current_filename_index,
            current_existing,
            original_filename,
        )
        self._data["files"][original_filename]["current_filename"] = current_filename
        self._add_index_entry(
            self._current_filename_index,
            current_filename,
            original_filename,
        )
        return True

    def set_identify_profile(self, profile: Mapping[str, Any] | None) -> bool:
        """
        Set identify-profile metadata.

        Returns True when the sidecar payload changes.
        """
        normalized = self._normalize_identify_profile(profile)
        existing = self._data.get("identify_profile")
        if normalized is None:
            if existing is None:
                return False
            del self._data["identify_profile"]
            return True

        if existing == normalized:
            return False
        self._data["identify_profile"] = normalized
        return True

    def set_identify_settings(self, settings: IdentifySettings | None) -> bool:
        """Set reusable Identify settings and report whether they changed."""
        normalized = settings.to_mapping() if settings is not None else None
        existing = self._data.get("identify_settings")
        if existing == normalized:
            return False
        if normalized is None:
            self._data.pop("identify_settings", None)
        else:
            self._data["identify_settings"] = normalized
        return True

    def remove_file(self, original_filename: str) -> bool:
        """Remove record for original filename."""
        original_filename = unicodedata.normalize("NFC", original_filename)
        record = self._data["files"].get(original_filename)
        if record is None:
            return False
        self._deindex_record(original_filename, record)
        del self._data["files"][original_filename]
        return True

    def find_by_sha256(self, sha256: str) -> tuple[str, dict[str, Any]] | None:
        """Find a sidecar record by SHA-256 digest."""
        names = self._sha_index.get(str(sha256), [])
        for original in names:
            record = self._data["files"].get(original)
            if record is None:
                continue
            return original, copy.deepcopy(record)
        return None

    def find_by_current_filename(
        self, filename: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Find a record by current filename (or original key fallback)."""
        filename = unicodedata.normalize("NFC", filename)
        names = self._current_filename_index.get(filename, [])
        for original in names:
            record = self._data["files"].get(original)
            if record is None:
                continue
            return original, copy.deepcopy(record)
        return None

    def duplicates_by_sha256(self) -> dict[str, list]:
        """Return SHA digests that map to multiple original files."""
        return {
            sha256: list(names)
            for sha256, names in self._sha_index.items()
            if len(names) > 1
        }
