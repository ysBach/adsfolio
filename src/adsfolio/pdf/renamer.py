"""Template-based PDF renaming engine."""

import logging
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..formatting import html_to_text
from .cli_common import is_resolved_record
from .files import sha256_file
from .readiness import METADATA_SHA256_KEY, metadata_ready
from .sidecar import Sidecar
from .tracked_move import move_tracked_file

if TYPE_CHECKING:
    from ..client import ADSClient

DEFAULT_SCHEME = "{authors}_{bibcode}-{title}"
DEFAULT_NONBIBCODE_SCHEME = "{authors}_{year}-{title}"
_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ALLOWED_COMPONENT_PUNCT = {".", "_", "+", ",", "-"}
_ALLOWED_CHAR_POLICIES = {"ascii", "keep"}
_MAX_AUTHORS_LIMIT = 99
_NONDECOMPOSABLE_LATIN_ASCII = str.maketrans(
    {
        "Æ": "AE",
        "Ð": "D",
        "Đ": "D",
        "Ħ": "H",
        "Ł": "L",
        "Ø": "O",
        "Œ": "OE",
        "Ŋ": "N",
        "Þ": "Th",
        "Ŧ": "T",
        "ß": "ss",
        "æ": "ae",
        "ð": "d",
        "đ": "d",
        "ħ": "h",
        "ı": "i",
        "ł": "l",
        "ø": "o",
        "œ": "oe",
        "ŋ": "n",
        "þ": "th",
        "ŧ": "t",
        "ẞ": "SS",
    }
)

__all__ = ["DEFAULT_NONBIBCODE_SCHEME", "DEFAULT_SCHEME", "PdfRenamer"]


def _resolve_rename_template(template: str, metadata: Mapping[str, Any]) -> str:
    has_bibcode = bool(
        str(metadata.get("bibcode") or "").strip()
    ) and is_resolved_record(dict(metadata))
    if not has_bibcode and template == DEFAULT_SCHEME:
        return DEFAULT_NONBIBCODE_SCHEME
    return template


class PdfRenamer:
    """Render rename templates and optionally apply them to disk."""

    def __init__(self, client: "ADSClient | None" = None):
        self.client = client
        self.logger = logging.getLogger(__name__)

    def sanitize_filename(self, name: str, max_bytes: int = 240) -> str:
        """Normalize a filename to a filesystem-safe form."""
        cleaned = _INVALID_FILENAME_CHARS.sub("_", name.strip())
        cleaned = re.sub(r"\s+", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned)
        cleaned = cleaned.strip(" ._")
        if not cleaned:
            return "untitled.pdf"

        encoded = cleaned.encode("utf-8")
        if len(encoded) > max_bytes:
            if cleaned.lower().endswith(".pdf"):
                stem = cleaned[:-4]
                ext = ".pdf"
            else:
                stem = cleaned
                ext = ""
            max_stem_bytes = max_bytes - len(ext.encode("utf-8"))
            stem_bytes = stem.encode("utf-8")[:max_stem_bytes]
            stem_truncated = stem_bytes.decode("utf-8", errors="ignore").rstrip(" ._")
            cleaned = f"{stem_truncated}{ext}"

        return cleaned

    def _first_list_value(self, value: Any) -> str:
        if isinstance(value, list):
            if not value:
                return ""
            return str(value[0])
        if value is None:
            return ""
        return str(value)

    def _normalize_component(self, value: str, char_policy: str) -> str:
        if not value:
            return ""
        if char_policy not in _ALLOWED_CHAR_POLICIES:
            raise ValueError(
                f"Invalid char_policy: {char_policy}. Use 'ascii' or 'keep'."
            )

        text = unicodedata.normalize("NFC", html_to_text(value).strip())
        if char_policy == "ascii":
            text = (
                unicodedata.normalize(
                    "NFKD", text.translate(_NONDECOMPOSABLE_LATIN_ASCII)
                )
                .encode("ascii", "ignore")
                .decode("ascii")
            )

        normalized_chars: list[str] = []
        for char in text:
            if char.isalnum() or char in _ALLOWED_COMPONENT_PUNCT:
                normalized_chars.append(char)
                continue
            if char_policy == "keep" and unicodedata.category(char).startswith("M"):
                normalized_chars.append(char)
                continue
            normalized_chars.append("_")

        text = "".join(normalized_chars)
        text = re.sub(r"\s+", "_", text)
        text = re.sub(r"_+", "_", text)
        return text.strip("._")

    def _split_author(self, author: str) -> tuple[str, str]:
        if "," in author:
            last, first = author.split(",", 1)
            return last.strip(), first.strip()
        parts = author.split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[-1], " ".join(parts[:-1])

    def _author_surname_initials(self, author: str, char_policy: str) -> str:
        last, first = self._split_author(author)
        initials = "".join(part[0] for part in re.findall(r"[^\W\d_]+", first))
        return self._normalize_component(f"{last}{initials}", char_policy)

    def _format_single_author(
        self,
        author: str,
        author_format: str,
        char_policy: str,
    ) -> str:
        last, first = self._split_author(author)
        if author_format == "surname":
            return self._normalize_component(last, char_policy)
        if author_format == "last_comma_first":
            first_clean = re.sub(r"\s+", "_", first)
            return self._normalize_component(f"{last},_{first_clean}", char_policy)
        return self._author_surname_initials(author, char_policy)

    def _format_authors_ads_custom(
        self,
        bibcode: str,
        format_string: str,
        char_policy: str,
    ) -> str:
        if self.client is None:
            raise ValueError("author-format ads_custom requires an ADSClient instance.")
        custom = self.client.export.custom([bibcode], format_string=format_string)
        return self._normalize_component(custom.strip(), char_policy)

    def _format_authors(
        self,
        metadata: Mapping[str, Any],
        author_format: str,
        max_authors: int,
        authors_when_truncated: int,
        char_policy: str,
        author_separator: str,
        author_truncation_suffix: str,
    ) -> tuple[str, str]:
        authors = [
            str(name) for name in metadata.get("author", []) if str(name).strip()
        ]
        if not authors:
            return "", ""

        if author_format.startswith("ads_custom:"):
            fmt = author_format.split(":", 1)[1].strip()
            custom = self._format_authors_ads_custom(
                self._first_list_value(metadata.get("bibcode")),
                fmt,
                char_policy,
            )
            return custom, custom

        tokens = [
            self._format_single_author(name, author_format, char_policy)
            for name in authors
        ]
        truncated = len(tokens) > max_authors
        limited = tokens[: authors_when_truncated if truncated else len(tokens)]
        joined = author_separator.join(token for token in limited if token)
        if truncated and joined:
            return f"{joined}{author_truncation_suffix}", joined
        return joined, joined

    def _template_values(
        self,
        metadata: Mapping[str, Any],
        author_format: str,
        max_authors: int,
        authors_when_truncated: int,
        char_policy: str,
        author_separator: str,
        author_truncation_suffix: str,
    ) -> dict[str, str]:
        first_author_full = ""
        first_author = ""
        if metadata.get("author"):
            first_author_raw = str(metadata["author"][0])
            first_author = self._split_author(first_author_raw)[0]
            first_author_full = self._author_surname_initials(
                first_author_raw,
                char_policy,
            )

        title = self._normalize_component(
            self._first_list_value(metadata.get("title")),
            char_policy,
        )
        bibcode = self._first_list_value(metadata.get("bibcode"))
        journal = self._first_list_value(metadata.get("bibstem"))
        if not journal:
            journal = self._first_list_value(metadata.get("pub"))
        page = self._first_list_value(metadata.get("page"))
        authors, authors_n = self._format_authors(
            metadata,
            author_format,
            max_authors,
            authors_when_truncated,
            char_policy,
            author_separator,
            author_truncation_suffix,
        )
        return {
            "bibcode": bibcode,
            "year": self._first_list_value(metadata.get("year")),
            "title": title,
            "first_author": self._normalize_component(first_author, char_policy),
            "first_author_full": first_author_full,
            "authors": authors,
            "authors_N": authors_n,
            "journal": self._normalize_component(journal, char_policy),
            "volume": self._normalize_component(
                self._first_list_value(metadata.get("volume")),
                char_policy,
            ),
            "page": self._normalize_component(page, char_policy),
        }

    def render(
        self,
        template: str,
        metadata: Mapping[str, Any],
        author_format: str = "surname_initials",
        max_authors: int = 3,
        authors_when_truncated: int = 1,
        char_policy: str = "keep",
        author_separator: str = "+",
        author_truncation_suffix: str = "+",
    ) -> str:
        """Render a filename from metadata and rename template."""
        self._validate_author_limits(max_authors, authors_when_truncated)
        values = self._template_values(
            metadata,
            author_format,
            max_authors,
            authors_when_truncated,
            char_policy,
            author_separator,
            author_truncation_suffix,
        )
        try:
            rendered = template.format(**values)
        except KeyError as exc:
            raise ValueError(f"Unknown template key: {exc}") from exc

        if not rendered.lower().endswith(".pdf"):
            rendered = f"{rendered}.pdf"
        return self.sanitize_filename(rendered)

    def batch_rename(
        self,
        sidecar: Sidecar,
        template: str = DEFAULT_SCHEME,
        dry_run: bool = True,
        author_format: str = "surname_initials",
        max_authors: int = 3,
        authors_when_truncated: int = 1,
        char_policy: str = "keep",
        move_resolved_from: Path | None = None,
        move_resolved_to: Path | None = None,
        author_separator: str = "+",
        author_truncation_suffix: str = "+",
    ) -> list[tuple[str, str]]:
        """Rename tracked PDFs with verified ADS or ready local metadata.

        If both move directories are supplied, resolved files below
        ``move_resolved_from`` are placed in ``move_resolved_to`` after the
        new filename is rendered. Non-ADS records are renamed in place.
        """
        self._validate_author_limits(max_authors, authors_when_truncated)
        plans: list[tuple[str, str]] = []
        for original_name, metadata in sidecar.files.items():
            if not (is_resolved_record(dict(metadata)) or metadata_ready(metadata)):
                continue
            current_name = metadata.get("current_filename", original_name)
            source_path = sidecar.directory / current_name
            if not source_path.exists():
                self.logger.warning("Missing file for rename: %s", source_path)
                continue

            stored_sha = str(
                metadata.get("sha256") or metadata.get(METADATA_SHA256_KEY) or ""
            ).strip()
            if stored_sha:
                try:
                    actual_sha = sha256_file(source_path)
                except OSError:
                    continue
                if actual_sha != stored_sha:
                    self.logger.warning(
                        "Skipping rename due to hash mismatch: %s", source_path
                    )
                    continue

            effective_template = _resolve_rename_template(template, metadata)
            new_name = self.render(
                effective_template,
                metadata,
                author_format,
                max_authors,
                authors_when_truncated,
                char_policy,
                author_separator,
                author_truncation_suffix,
            )
            target_directory = source_path.parent
            if (
                is_resolved_record(dict(metadata))
                and move_resolved_from is not None
                and move_resolved_to is not None
            ):
                try:
                    source_path.relative_to(move_resolved_from)
                except ValueError:
                    pass
                else:
                    target_directory = move_resolved_to

            target_path = target_directory / new_name
            if target_path == source_path:
                continue
            if target_path.exists() and target_path != source_path:
                self.logger.warning("Skipping existing target: %s", target_path)
                continue

            plans.append((current_name, new_name))
            if dry_run:
                continue
            move_tracked_file(sidecar, source_path, target_path)
        return plans

    def rename_tracked_file(
        self,
        state: Sidecar,
        source_path: Path,
        metadata: Mapping[str, Any],
        template: str = DEFAULT_SCHEME,
        dry_run: bool = True,
        author_format: str = "surname_initials",
        max_authors: int = 3,
        authors_when_truncated: int = 1,
        char_policy: str = "keep",
        author_separator: str = "+",
        author_truncation_suffix: str = "+",
        target_directory: Path | None = None,
    ) -> Path:
        """Plan or apply a rename for one tracked resolved PDF."""
        source = Path(source_path).expanduser().resolve()
        if not (is_resolved_record(dict(metadata)) or metadata_ready(metadata)):
            raise ValueError(
                "Record is neither resolved nor metadata-ready for renaming."
            )
        stored_sha = str(
            metadata.get("sha256") or metadata.get(METADATA_SHA256_KEY) or ""
        ).strip()
        if stored_sha and source.is_file():
            actual_sha = sha256_file(source)
            if actual_sha != stored_sha:
                raise ValueError(
                    f"File content hash mismatch for {source}: "
                    f"expected {stored_sha}, got {actual_sha}"
                )

        effective_template = _resolve_rename_template(template, metadata)
        destination_directory = (
            source.parent
            if target_directory is None
            else Path(target_directory).expanduser().resolve()
        )
        target = destination_directory / self.render(
            effective_template,
            metadata,
            author_format=author_format,
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
            char_policy=char_policy,
            author_separator=author_separator,
            author_truncation_suffix=author_truncation_suffix,
        )
        if target == source or dry_run:
            return target
        return move_tracked_file(state, source, target)

    @staticmethod
    def _validate_author_limits(
        max_authors: int,
        authors_when_truncated: int,
    ) -> None:
        if (
            isinstance(max_authors, bool)
            or not isinstance(max_authors, int)
            or not 1 <= max_authors <= _MAX_AUTHORS_LIMIT
        ):
            raise ValueError("max_authors must be an integer from 1 through 99")
        if (
            isinstance(authors_when_truncated, bool)
            or not isinstance(authors_when_truncated, int)
            or not 1 <= authors_when_truncated <= max_authors
        ):
            raise ValueError(
                "authors_when_truncated must be an integer from 1 through "
                f"max_authors ({max_authors})"
            )
