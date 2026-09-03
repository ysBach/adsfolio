"""arXiv-to-published PDF upgrade workflow."""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config
from .annotations import PortingReport, port_annotations
from .sidecar import Sidecar
from .upgrade_utils import (
    PublishedMatch,
    UpgradeResult,
    _relative_path,
    _safe_bibcode_name,
    _utc_now_iso,
    check_published_version,
    check_published_versions,
    detect_arxiv_entries,
    is_arxiv_bibcode,
    is_arxiv_doi,
    is_arxiv_entry,
)

if TYPE_CHECKING:
    from ..client import ADSClient

__all__ = [
    "PublishedMatch",
    "UpgradeResult",
    "is_arxiv_bibcode",
    "is_arxiv_doi",
    "is_arxiv_entry",
    "detect_arxiv_entries",
    "check_published_versions",
    "check_published_version",
    "PdfUpgrader",
    "batch_upgrade",
]

_UNSET_PUBLISHED_MATCH = object()


class PdfUpgrader:
    """Upgrade manager for arXiv PDFs tracked in sidecar."""

    def __init__(self, client: "ADSClient", logger: logging.Logger | None = None):
        self.client = client
        self.logger = logger or logging.getLogger(__name__)

    def _write_unported_annotations(
        self, sidecar_dir: Path, bibcode: str, unported: list[dict[str, Any]]
    ) -> Path | None:
        if not unported:
            return None
        out_dir = sidecar_dir / ".adsapi-pdfs-unported-annots"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{_safe_bibcode_name(bibcode)}.json"
        payload = {"bibcode": bibcode, "unported": unported}
        out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return out_path

    def _backup_original(self, source_path: Path, backup_dir: Path) -> Path:
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / source_path.name
        if not target.exists():
            shutil.copy2(source_path, target)
            return target

        stem = source_path.stem
        suffix = source_path.suffix
        counter = 1
        while True:
            candidate = backup_dir / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                shutil.copy2(source_path, candidate)
                return candidate
            counter += 1

    def _merge_document_fields(
        self, entry: dict[str, Any], document: dict[str, Any]
    ) -> dict[str, Any]:
        updated = dict(entry)
        for key in (
            "doi",
            "title",
            "author",
            "year",
            "bibstem",
            "volume",
            "page",
            "pub",
        ):
            if key not in document:
                continue
            value = document.get(key)
            if value in (None, "", []):
                continue
            updated[key] = value
        return updated

    def upgrade_pdf(
        self,
        sidecar: Sidecar,
        original_filename: str,
        apply: bool = False,
        overwrite_arxiv_with_official: bool = False,
        arxiv_backup_if_overwritten: Path | None = None,
        skip_annotation_port: bool = False,
        force: bool = False,
        published_match: PublishedMatch | None | object = _UNSET_PUBLISHED_MATCH,
    ) -> UpgradeResult:
        """Upgrade one arXiv sidecar record if published PDF exists."""
        entry = sidecar.get_file(original_filename)
        if entry is None:
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=original_filename,
                old_bibcode="",
                new_bibcode=None,
                status="skipped",
                message="Sidecar entry not found.",
            )

        current_filename = str(entry.get("current_filename", original_filename))
        old_bibcode = str(entry.get("bibcode", "")).strip()
        current_path = sidecar.directory / current_filename
        if not old_bibcode:
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=None,
                status="skipped",
                message="Entry has no bibcode.",
            )
        if not current_path.exists():
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=None,
                status="skipped",
                message=f"Tracked PDF does not exist: {current_path}",
            )

        if published_match is _UNSET_PUBLISHED_MATCH:
            published = check_published_version(self.client, old_bibcode)
        else:
            published = published_match

        if published is None:
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=None,
                status="arxiv-only",
                message="Published version not found.",
            )

        if (
            not force
            and published.bibcode == old_bibcode
            and str(entry.get("source", "")) == "published"
        ):
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=published.bibcode,
                status="skipped",
                message="Already marked as published.",
            )

        if not apply:
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=published.bibcode,
                status="dry-run",
                message="Upgrade preview only.",
            )

        downloaded = self.client.download_pdf(
            published.bibcode,
            output_dir=sidecar.directory,
        )
        if downloaded is None:
            return UpgradeResult(
                original_filename=original_filename,
                current_filename=current_filename,
                old_bibcode=old_bibcode,
                new_bibcode=published.bibcode,
                status="failed",
                message="Failed to download official PDF.",
            )

        temp_ported_path = (
            sidecar.directory / f"{downloaded.stem}__ported{downloaded.suffix}"
        )
        merge_source = downloaded
        annotations_ported = 0
        annotations_failed = 0
        unported_file_path: Path | None = None
        if not skip_annotation_port:
            try:
                report: PortingReport = port_annotations(
                    source_path=current_path,
                    target_path=downloaded,
                    output_path=temp_ported_path,
                )
                merge_source = Path(report.output_path)
                annotations_ported = report.ported
                annotations_failed = report.failed
                unported_file_path = self._write_unported_annotations(
                    sidecar.directory,
                    published.bibcode,
                    report.unported,
                )
            except RuntimeError as exc:
                self.logger.warning("Skipping annotation porting: %s", exc)

        backup_path: Path | None = None
        final_filename = current_filename
        if overwrite_arxiv_with_official:
            if arxiv_backup_if_overwritten is not None:
                backup_path = self._backup_original(
                    current_path, arxiv_backup_if_overwritten
                )
            shutil.copy2(merge_source, current_path)
            final_filename = current_filename
            if downloaded != current_path and downloaded.exists():
                downloaded.unlink()
        else:
            final_filename = downloaded.name
            if merge_source != downloaded:
                shutil.copy2(merge_source, downloaded)

        if merge_source == temp_ported_path and temp_ported_path.exists():
            temp_ported_path.unlink()

        updated = self._merge_document_fields(entry, published.document)
        updated["original_bibcode"] = updated.get("original_bibcode") or old_bibcode
        updated["bibcode"] = published.bibcode
        updated["source"] = "published"
        updated["upgrade_status"] = "upgraded"
        updated["upgraded_at"] = _utc_now_iso()
        updated["current_filename"] = final_filename
        updated["annotations_ported"] = annotations_ported
        updated["annotations_failed"] = annotations_failed
        if backup_path is not None:
            updated["arxiv_backup"] = _relative_path(sidecar.directory, backup_path)
        if unported_file_path is not None:
            updated["unported_annotations_file"] = _relative_path(
                sidecar.directory, unported_file_path
            )

        sidecar.upsert_file(original_filename, updated)
        sidecar.save()

        return UpgradeResult(
            original_filename=original_filename,
            current_filename=final_filename,
            old_bibcode=old_bibcode,
            new_bibcode=published.bibcode,
            status="upgraded",
            message="Official PDF downloaded and sidecar updated.",
            downloaded_path=str(downloaded),
            backup_path=_relative_path(sidecar.directory, backup_path),
            annotations_ported=annotations_ported,
            annotations_failed=annotations_failed,
            unported_annotations_file=_relative_path(
                sidecar.directory, unported_file_path
            ),
        )

    def batch_upgrade(
        self,
        sidecar: Sidecar,
        apply: bool = False,
        overwrite_arxiv_with_official: bool = False,
        arxiv_backup_if_overwritten: Path | None = None,
        skip_annotation_port: bool = False,
        force: bool = False,
        delay_seconds: float = 0.0,
    ) -> list[UpgradeResult]:
        """Run upgrade checks for all arXiv entries in sidecar."""
        results: list[UpgradeResult] = []
        arxiv_entries = detect_arxiv_entries(sidecar)
        prefetch_bibcodes: list[str] = []
        for original_filename in arxiv_entries:
            entry = sidecar.get_file(original_filename)
            if entry is None:
                continue
            old_bibcode = str(entry.get("bibcode", "")).strip()
            current_filename = str(entry.get("current_filename", original_filename))
            current_path = sidecar.directory / current_filename
            if old_bibcode and current_path.exists():
                prefetch_bibcodes.append(old_bibcode)

        published_matches = check_published_versions(self.client, prefetch_bibcodes)

        for original_filename in arxiv_entries:
            entry = sidecar.get_file(original_filename)
            old_bibcode = (
                str(entry.get("bibcode", "")).strip() if entry is not None else ""
            )
            current_filename = (
                str(entry.get("current_filename", original_filename))
                if entry is not None
                else original_filename
            )
            current_path = sidecar.directory / current_filename

            if old_bibcode and current_path.exists():
                published_match = published_matches.get(old_bibcode)
            else:
                published_match = _UNSET_PUBLISHED_MATCH

            if not Config.check_rate_limit():
                self.logger.warning(
                    "Rate limit is low; continuing cautiously with upgrade checks."
                )
            result = self.upgrade_pdf(
                sidecar=sidecar,
                original_filename=original_filename,
                apply=apply,
                overwrite_arxiv_with_official=overwrite_arxiv_with_official,
                arxiv_backup_if_overwritten=arxiv_backup_if_overwritten,
                skip_annotation_port=skip_annotation_port,
                force=force,
                published_match=published_match,
            )
            results.append(result)
            if delay_seconds > 0:
                time.sleep(delay_seconds)
        return results


def batch_upgrade(
    client: "ADSClient",
    sidecar: Sidecar,
    apply: bool = False,
    overwrite_arxiv_with_official: bool = False,
    arxiv_backup_if_overwritten: Path | None = None,
    skip_annotation_port: bool = False,
    force: bool = False,
    delay_seconds: float = 0.0,
) -> list[UpgradeResult]:
    """Convenience wrapper for batch upgrade operations."""
    upgrader = PdfUpgrader(client=client)
    return upgrader.batch_upgrade(
        sidecar=sidecar,
        apply=apply,
        overwrite_arxiv_with_official=overwrite_arxiv_with_official,
        arxiv_backup_if_overwritten=arxiv_backup_if_overwritten,
        skip_annotation_port=skip_annotation_port,
        force=force,
        delay_seconds=delay_seconds,
    )
