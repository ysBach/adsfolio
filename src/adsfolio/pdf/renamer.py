"""Template-based PDF renaming engine."""

import copy
import logging
import re
import sqlite3
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, Any

from ..formatting import html_to_text
from .catalog import PdfCatalog, _sqlite_file_identity
from .cli_common import is_resolved_record
from .doi_providers import DEFAULT_DOI_PROVIDERS, DoiProvider
from .files import sha256_file
from .identity import (
    IDENTITY_KEY,
    IdentityValidationError,
    UnverifiedIdentityError,
    assert_mutation_allowed,
    target_identity,
)
from .readiness import (
    METADATA_SHA256_KEY,
    METADATA_STALE_KEY,
    PREVIOUS_RESOLUTION_KEY,
    REFERENCE_METADATA_KEY,
    WATCH_PENDING_KEY,
    metadata_ready,
)
from .registry import CentralPdfRegistryState
from .sidecar import Sidecar
from .tracked_move import is_same_file_alias, move_tracked_file

if TYPE_CHECKING:
    from ..client import ADSClient

DEFAULT_SCHEME = "{authors}_{bibcode}-{title}"
# Retain the exported legacy preset for callers that explicitly select it.
# Rename operations never substitute this value for the selected template.
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

__all__ = [
    "DEFAULT_NONBIBCODE_SCHEME",
    "DEFAULT_SCHEME",
    "MissingTemplateFieldsError",
    "PdfRenamer",
    "identity_view",
    "recover_stale_record",
    "verify_record_identity",
]


class MissingTemplateFieldsError(ValueError):
    """Report missing metadata required by a filename template.

    Attributes
    ----------
    missing_fields : tuple of str
        Template field names that have no metadata value.
    """

    def __init__(self, missing_fields: Sequence[str]) -> None:
        self.missing_fields = tuple(sorted(set(missing_fields)))
        names = ", ".join(self.missing_fields)
        super().__init__(
            f"Missing metadata for template fields: {names}. "
            "Add metadata or choose a template without these fields."
        )


def identity_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten a catalog or sidecar record into one metadata mapping.

    Nested ``metadata`` values are overridden by top-level record fields,
    matching how readiness checks read the same record.
    """
    nested = record.get("metadata")
    view: dict[str, Any] = dict(nested) if isinstance(nested, Mapping) else {}
    view.update((key, value) for key, value in record.items() if key != "metadata")
    return view


def cached_provider_document(
    state: Any, record: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Return the cached raw ADS document for a record's primary Bibcode.

    Only the primary Bibcode is looked up, so a secondary identifier that a
    record happens to list cannot select another paper's document. Returns
    None when the state has no registry or the document is not cached.
    """
    registry = getattr(state, "registry", None)
    lookup = getattr(registry, "find_ads_document_by_bibcode", None)
    if lookup is None:
        return None
    bibcode = identity_view(record).get("bibcode")
    if isinstance(bibcode, (list, tuple)):
        bibcode = bibcode[0] if bibcode else ""
    bibcode = str(bibcode or "").strip()
    return lookup(bibcode) if bibcode else None


def verify_record_identity(
    pdf_path: Path,
    record: Mapping[str, Any],
    *,
    sha256: str | None = None,
    manual_confirmation: bool = False,
    manual_conflict_override: bool = False,
    provider_document: Mapping[str, Any] | None = None,
    state: Any = None,
    client: "ADSClient | None" = None,
    providers: Sequence[DoiProvider] = DEFAULT_DOI_PROVIDERS,
) -> dict[str, Any]:
    """Check that a stored record is proven to describe the PDF's bytes.

    Parameters
    ----------
    pdf_path : Path
        PDF that a write or rename would affect.
    record : Mapping[str, Any]
        Catalog or sidecar record, flat or with nested ``metadata``.
    sha256 : str, optional
        Digest the proof must be tied to. Defaults to the record's digest.
    manual_confirmation : bool, optional
        Set only for an explicit, user-reviewed confirmation of an unknown
        identity. A known conflict is never overridden.
    manual_conflict_override : bool, optional
        Allow a specifically reviewed conflict with a fresh provider record.
    provider_document : Mapping[str, Any], optional
        Authoritative raw ADS or provider document for the record. When
        omitted, a valid stored proof is checked first. Otherwise, the cached
        ADS document for the record's primary Bibcode is used when available.
    state : Sidecar or CentralPdfRegistryState, optional
        Catalog state used to look up the cached document.
    client : ADSClient, optional
        Client for automatically fetching an unverifiable stored ADS candidate.
        Valid stored proofs need no request. An omitted client keeps checks local.
    providers : sequence of DoiProvider, optional
        Enabled providers. Automatic ADS requests require ADS in this sequence.

    Returns
    -------
    dict[str, Any]
        Identity proof to store under ``IDENTITY_KEY``.

    Raises
    ------
    IdentityValidationError
        If the identity is conflicting or cannot be independently verified.
    adsfolio.config.ADSError
        An enabled ADS record lookup failed.
    """
    view = identity_view(record)
    expected = sha256 or str(view.get("sha256") or "").strip() or None
    proof = view.get(IDENTITY_KEY)
    if (
        provider_document is None
        and isinstance(proof, Mapping)
        and proof.get("status") in {"verified", "manual_confirmed"}
    ):
        try:
            return assert_mutation_allowed(
                Path(pdf_path),
                view,
                expected_sha256=expected,
                manual_confirmation=manual_confirmation,
                manual_conflict_override=manual_conflict_override,
            )
        except IdentityValidationError:
            # An invalid stored proof grants nothing; use ordinary validation.
            pass
    if provider_document is None and state is not None:
        provider_document = cached_provider_document(state, view)
    try:
        return assert_mutation_allowed(
            Path(pdf_path),
            view,
            expected_sha256=expected,
            manual_confirmation=manual_confirmation,
            manual_conflict_override=manual_conflict_override,
            provider_document=provider_document,
        )
    except UnverifiedIdentityError:
        if client is None or DoiProvider.ADS not in providers:
            raise
        bibcode = view.get("bibcode")
        if isinstance(bibcode, (list, tuple)):
            bibcode = bibcode[0] if bibcode else ""
        bibcode = str(bibcode or "").strip()
        if not bibcode:
            raise
        from .identifier import PdfIdentifier

        registry = getattr(state, "registry", None)
        cache = getattr(registry, "upsert_ads_documents", None)
        documents = PdfIdentifier(client).query_docs_for_bibcodes(
            [bibcode],
            ads_documents_callback=(
                (lambda values: cache(values, source="identity:automatic"))
                if cache is not None
                else None
            ),
        )
        document = documents.get(bibcode)
        if document is None:
            raise
        return assert_mutation_allowed(
            Path(pdf_path),
            view,
            expected_sha256=expected,
            manual_confirmation=manual_confirmation,
            manual_conflict_override=manual_conflict_override,
            provider_document=document,
        )


_STALE_RECOVERY_HISTORY = "_adsfolio_stale_recovery_history"


def _recovery_file_facts(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _check_recovery_locations(
    locations: list[dict[str, Any]], location_id: int
) -> None:
    """Refuse recovery if another location is live or its state is unknown."""
    active = next(
        (item for item in locations if item["location_id"] == location_id), None
    )
    if active is None:
        raise IdentityValidationError("The PDF location is no longer tracked.")
    for item in locations:
        if item["location_id"] == location_id:
            continue
        if item["missing_at"] is None:
            raise IdentityValidationError("The attachment has another active location.")
        root = Path(item["root"])
        if root == Path(active["root"]) and PdfCatalog._normalize_relative_path(
            str(item["current_path"])
        ) == PdfCatalog._normalize_relative_path(str(active["current_path"])):
            continue
        if not root.is_dir():
            raise IdentityValidationError(
                f"A previously tracked library is unavailable: {root}."
            )
        relative = PdfCatalog._normalize_relative_path(str(item["current_path"]))
        try:
            (root / relative).lstat()
        except FileNotFoundError:
            continue
        raise IdentityValidationError(
            f"A previously tracked PDF location exists: {root / relative}."
        )


def recover_stale_record(
    state: Sidecar | CentralPdfRegistryState,
    relative_path: str,
    *,
    apply: bool,
) -> dict[str, Any] | None:
    """Reverify a changed PDF against its cached ADS record.

    Parameters
    ----------
    state
        Library state containing the tracked PDF and cached ADS record.
    relative_path
        Current PDF path relative to the library root.
    apply
        Persist a verified recovery when True. False only checks and plans.

    Returns
    -------
    dict or None
        Flat, rename-ready metadata, or None if recovery does not apply.

    Raises
    ------
    IdentityValidationError
        The cached record, PDF, catalog, or shared locations cannot be verified.

    Notes
    -----
    Recovery also handles a changed file before the catalog scans it. It never
    fetches a provider record or changes the shared document metadata. Recovery
    retains prior identity proof and content versions as history.
    """
    return _recover_stale_record(state, relative_path, apply=apply)


def _first_text(value: Any) -> str:
    """Return the first value of a scalar or list field as stripped text."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return str(value or "").strip()


def _recover_stale_record(
    state: Sidecar | CentralPdfRegistryState,
    relative_path: str,
    *,
    apply: bool,
    expected_record: Mapping[str, Any] | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any] | None:
    """Bind an optional Rename preview to the same record and PDF bytes."""
    registry = getattr(state, "registry", None)
    catalog = getattr(registry, "catalog", None)
    if catalog is None:
        return None
    root = Path(state.directory).expanduser().resolve()
    record = catalog.get_file(root, relative_path)
    if expected_record is not None and record != expected_record:
        raise IdentityValidationError(
            "The catalog changed before Rename. Retry Rename."
        )
    if record is None:
        return None
    metadata = copy.deepcopy(record["metadata"])
    stale = bool(record.get(METADATA_STALE_KEY) or metadata.get(METADATA_STALE_KEY))
    if record["missing_at"] is not None:
        if stale:
            raise IdentityValidationError("The tracked PDF location is missing.")
        return None
    relative = PdfCatalog._normalize_relative_path(str(record["current_path"]))
    path = root / relative
    if path.is_symlink() or path.resolve() != path:
        raise IdentityValidationError("The PDF path contains a symbolic link.")
    facts = _recovery_file_facts(path)
    digest = sha256_file(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise IdentityValidationError("The PDF changed before Rename. Retry Rename.")
    if _recovery_file_facts(path) != facts:
        raise IdentityValidationError("The PDF changed during identity verification.")
    if not stale and digest == record["sha256"]:
        return None
    if not record["sha256"] or record["version_id"] is None:
        raise IdentityValidationError("The catalog has no current PDF digest.")
    if REFERENCE_METADATA_KEY in metadata:
        raise IdentityValidationError(
            "Edit this managed reference in Local References."
        )

    bibcode = metadata.get("bibcode")
    if isinstance(bibcode, (list, tuple)):
        bibcode = bibcode[0] if bibcode else ""
    bibcode = str(bibcode or "").strip()
    if not bibcode:
        raise IdentityValidationError("The saved metadata has no ADS bibcode.")
    document = cached_provider_document(state, metadata)
    if not isinstance(document, Mapping):
        raise IdentityValidationError("The saved ADS record is not cached.")
    if str(document.get("bibcode") or "").strip().casefold() != bibcode.casefold():
        raise IdentityValidationError("The cached ADS record has a different bibcode.")
    authority = target_identity(document)
    rows = catalog._connection.execute(
        "SELECT id FROM documents WHERE lower(bibcode) = lower(?) ORDER BY id",
        (bibcode,),
    ).fetchall()
    if len(rows) != 1:
        raise IdentityValidationError("The saved ADS document is missing or ambiguous.")
    document_id = int(rows[0]["id"])
    shared_document = catalog.get_document(document_id)
    if shared_document is None or not isinstance(shared_document["metadata"], dict):
        raise IdentityValidationError("The saved ADS document is invalid.")
    if REFERENCE_METADATA_KEY in shared_document["metadata"]:
        raise IdentityValidationError("The saved ADS document is a managed reference.")
    if target_identity(shared_document["metadata"]).claims_outside(authority):
        raise IdentityValidationError(
            "The saved ADS document has conflicting identifiers."
        )
    if record["document_id"] not in (None, document_id):
        raise IdentityValidationError("The PDF is linked to a different document.")
    previous = metadata.get(PREVIOUS_RESOLUTION_KEY)
    if previous is not None and (
        not isinstance(previous, Mapping)
        or previous.get("document_id") != document_id
        or previous.get("sha256")
        != (metadata.get(METADATA_SHA256_KEY) or metadata.get("sha256"))
        or previous.get("identity") != target_identity(metadata).fingerprint
    ):
        raise IdentityValidationError("The previous PDF resolution is inconsistent.")

    locations = catalog.query_files(
        attachment_id=record["attachment_id"], include_missing=True
    )
    _check_recovery_locations(locations, record["location_id"])
    proof = assert_mutation_allowed(
        path, metadata, expected_sha256=digest, provider_document=document
    )
    if proof.get("status") != "verified":
        raise IdentityValidationError("The PDF identity is not verified.")
    history = metadata.get(_STALE_RECOVERY_HISTORY, [])
    if not isinstance(history, list):
        raise IdentityValidationError("The saved recovery history is invalid.")
    old_proof = metadata.get(IDENTITY_KEY)
    old_sha = metadata.get(METADATA_SHA256_KEY) or metadata.get("sha256")
    if old_proof is not None or previous is not None or old_sha:
        metadata[_STALE_RECOVERY_HISTORY] = [
            *history,
            {
                "sha256": old_sha,
                "identity_proof": copy.deepcopy(old_proof),
                "previous_resolution": copy.deepcopy(previous),
            },
        ]
    metadata.pop(METADATA_STALE_KEY, None)
    metadata.pop(PREVIOUS_RESOLUTION_KEY, None)
    metadata[WATCH_PENDING_KEY] = False
    metadata["sha256"] = digest
    metadata[METADATA_SHA256_KEY] = digest
    metadata[IDENTITY_KEY] = proof

    def check_snapshot() -> None:
        if (
            catalog.get_file(root, relative) != record
            or catalog.query_files(
                attachment_id=record["attachment_id"], include_missing=True
            )
            != locations
            or cached_provider_document(state, metadata) != document
            or catalog.get_document(document_id) != shared_document
            or catalog._connection.execute(
                "SELECT id FROM documents WHERE lower(bibcode) = lower(?) ORDER BY id",
                (bibcode,),
            ).fetchall()
            != rows
        ):
            raise IdentityValidationError("The catalog changed during verification.")
        _check_recovery_locations(locations, record["location_id"])
        if path.is_symlink() or path.resolve() != path:
            raise IdentityValidationError("The PDF path changed during verification.")
        if (
            _recovery_file_facts(path) != facts
            or sha256_file(path) != digest
            or _recovery_file_facts(path) != facts
        ):
            raise IdentityValidationError(
                "The PDF changed during identity verification."
            )

    if not apply:
        check_snapshot()
        return CentralPdfRegistryState._record_view(
            {
                **record,
                "sha256": digest,
                "status": "resolved",
                "document_id": document_id,
                "metadata": metadata,
            }
        )
    connection = catalog._connection
    if connection.in_transaction:
        raise IdentityValidationError("The catalog already has an active transaction.")
    connection.execute("BEGIN IMMEDIATE")
    try:
        check_snapshot()
        location = connection.execute(
            "SELECT * FROM file_locations WHERE id = ? AND missing_at IS NULL",
            (record["location_id"],),
        ).fetchone()
        if location is None:
            raise IdentityValidationError("The PDF location is no longer tracked.")
        version_id = catalog._select_or_create_version(
            record["attachment_id"], location, digest, facts[2]
        )
        catalog._update_attachment(
            record["attachment_id"],
            document_id=document_id,
            status="resolved",
            metadata=metadata,
        )
        connection.execute(
            "UPDATE file_locations SET current_version_id = ?, size_bytes = ?, "
            "mtime_ns = ?, device = ?, inode = ?, last_seen_at = ? WHERE id = ?",
            (
                version_id,
                facts[2],
                facts[3],
                _sqlite_file_identity(facts[0]),
                _sqlite_file_identity(facts[1]),
                catalog._utc_timestamp(),
                record["location_id"],
            ),
        )
        _check_recovery_locations(locations, record["location_id"])
        if (
            path.is_symlink()
            or path.resolve() != path
            or _recovery_file_facts(path) != facts
            or sha256_file(path) != digest
            or _recovery_file_facts(path) != facts
        ):
            raise IdentityValidationError(
                "The PDF changed during identity verification."
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    saved = catalog.get_file(root, relative)
    if saved is None:
        raise IdentityValidationError("The PDF location is no longer tracked.")
    return CentralPdfRegistryState._record_view(saved)


class PdfRenamer:
    """Render rename templates and optionally apply them to disk."""

    def __init__(self, client: "ADSClient | None" = None):
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.identity_refused: dict[str, str] = {}
        self.unchanged: dict[str, str] = {}
        self.skipped: dict[str, str] = {}

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

    def _validate_template_fields(
        self, template: str, metadata: Mapping[str, Any]
    ) -> None:
        """Reject referenced fields that have no corresponding metadata."""

        def has_first_value(value: Any) -> bool:
            if isinstance(value, list):
                value = value[0] if value else None
            return value is not None and bool(str(value).strip())

        authors = metadata.get("author")
        if isinstance(authors, (list, tuple)):
            first_author = authors[0] if authors else None
            has_authors = any(has_first_value(author) for author in authors)
        else:
            first_author = authors[0] if isinstance(authors, str) and authors else None
            has_authors = has_first_value(authors)

        # Match the first-value and journal fallback rules used by render.
        bibstem = metadata.get("bibstem")
        journal = bibstem if self._first_list_value(bibstem) else metadata.get("pub")
        available = {
            "bibcode": has_first_value(metadata.get("bibcode")),
            "year": has_first_value(metadata.get("year")),
            "title": has_first_value(metadata.get("title")),
            "first_author": has_first_value(first_author),
            "first_author_full": has_first_value(first_author),
            "authors": has_authors,
            "authors_N": has_authors,
            "journal": has_first_value(journal),
            "volume": has_first_value(metadata.get("volume")),
            "page": has_first_value(metadata.get("page")),
        }
        missing: set[str] = set()
        pending = [template]
        formatter = Formatter()
        # Format specifications can contain additional replacement fields.
        while pending:
            for _, field, format_spec, _ in formatter.parse(pending.pop()):
                if field is None:
                    continue
                name = field.split(".", 1)[0].split("[", 1)[0]
                if name in available and not available[name]:
                    missing.add(name)
                if format_spec:
                    pending.append(format_spec)
        if missing:
            raise MissingTemplateFieldsError(sorted(missing))

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
        current_filenames: Sequence[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Rename tracked PDFs with verified ADS or ready local metadata.

        If both move directories are supplied, resolved files below
        ``move_resolved_from`` are placed in ``move_resolved_to`` after the
        new filename is rendered. Non-ADS records are renamed in place.

        Records whose identity is not proven by the PDF itself are skipped,
        including in a dry run. Their tracked names and reasons are kept in
        ``identity_refused`` until the next call.

        `current_filenames`, when supplied, limits processing to these current
        paths relative to the catalog root. An empty sequence selects no files.
        A Unicode alias selects a current path only when its directory entry
        proves the match.

        Use the selected template without substitution. Skip records when a
        referenced field has no value. Record unchanged targets in `unchanged`
        and other skipped reasons in `skipped`. Clear these mappings for each call.
        """
        self._validate_author_limits(max_authors, authors_when_truncated)
        self.identity_refused = {}
        self.unchanged = {}
        self.skipped = {}
        plans: list[tuple[str, str]] = []
        files = sidecar.files
        registry = getattr(sidecar, "registry", None)
        catalog = getattr(registry, "catalog", None)
        selected = set(current_filenames) if current_filenames is not None else None
        if selected:
            current_names = [
                metadata.get("current_filename", name)
                for name, metadata in files.items()
            ]
            for name in selected.difference(current_names):
                try:
                    PdfCatalog._normalize_relative_path(name)
                except ValueError:
                    continue
                if catalog is not None:
                    record = catalog.get_file(sidecar.directory, name)
                    if record is not None and record["missing_at"] is None:
                        selected.add(record["current_path"])
                else:
                    aliases = [
                        current
                        for current in current_names
                        if PdfCatalog._same_unicode_entry(
                            sidecar.directory, current, name
                        )
                    ]
                    if len(aliases) == 1:
                        selected.add(aliases[0])

        def target_for(source_path: Path, metadata: Mapping[str, Any]) -> Path:
            """Render the target path from stored metadata without reading the PDF."""
            self._validate_template_fields(template, metadata)
            new_name = self.render(
                template,
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
            return target_directory / new_name

        for original_name, metadata in files.items():
            current_name = metadata.get("current_filename", original_name)
            if selected is not None and current_name not in selected:
                continue
            stale = bool(metadata.get(METADATA_STALE_KEY))
            if not stale and not (
                is_resolved_record(dict(metadata)) or metadata_ready(metadata)
            ):
                self.skipped[str(current_name)] = (
                    "Metadata is not ready for renaming. Run Identify or Update by ID."
                )
                continue
            source_path = sidecar.directory / current_name
            if not source_path.exists():
                self.logger.warning("Missing file for rename: %s", source_path)
                self.skipped[str(current_name)] = "PDF is missing. Refresh the library."
                continue
            if stale and not _first_text(metadata.get("bibcode")):
                # Recovery needs a saved bibcode. Refuse before hashing the PDF.
                self.identity_refused[str(current_name)] = (
                    "Metadata is not ready for renaming. The saved metadata has no "
                    "ADS bibcode. Review the PDF, then use Update by ID."
                )
                continue
            if not stale:
                # A file that already has its target name changes nothing, so
                # it needs no hash or identity check. Only renamed files do.
                try:
                    planned = target_for(source_path, metadata)
                except MissingTemplateFieldsError as exc:
                    self.skipped[str(current_name)] = str(exc)
                    continue
                if planned == source_path or is_same_file_alias(source_path, planned):
                    self.unchanged[str(current_name)] = planned.name
                    continue
                # An existing target blocks the rename whatever the check says.
                if planned.exists():
                    self.skipped[str(current_name)] = (
                        f"Destination exists: {planned}. Review both PDFs before "
                        "choosing a filename."
                    )
                    continue

            stored_sha = str(
                metadata.get("sha256") or metadata.get(METADATA_SHA256_KEY) or ""
            ).strip()
            actual_sha = None
            if stored_sha:
                try:
                    actual_sha = sha256_file(source_path)
                except OSError as exc:
                    self.skipped[str(current_name)] = f"Cannot read PDF: {exc}"
                    continue
            changed = bool(stored_sha and actual_sha != stored_sha)
            needs_recovery = stale or bool(
                changed and catalog is not None and metadata.get("bibcode")
            )
            recovery_snapshot = None
            if needs_recovery:
                try:
                    if catalog is not None:
                        recovery_snapshot = catalog.get_file(
                            sidecar.directory, str(current_name)
                        )
                    if recovery_snapshot is None:
                        raise IdentityValidationError(
                            "This record cannot be recovered in this library."
                        )
                    recovered = _recover_stale_record(
                        sidecar,
                        str(current_name),
                        apply=False,
                        expected_record=recovery_snapshot,
                        expected_sha256=actual_sha,
                    )
                    if recovered is None:
                        raise IdentityValidationError(
                            "This record no longer needs recovery. Retry Rename."
                        )
                    metadata = recovered
                    stored_sha = str(metadata.get("sha256") or "").strip()
                except (OSError, ValueError, sqlite3.Error) as exc:
                    reason = (
                        "Metadata is not ready for renaming."
                        if stale
                        else "PDF content changed."
                    )
                    self.identity_refused[str(current_name)] = (
                        f"{reason} {exc} Review the PDF, then use Update by ID."
                    )
                    continue
            elif changed:
                self.logger.warning(
                    "Skipping rename due to hash mismatch: %s", source_path
                )
                self.skipped[str(current_name)] = (
                    "PDF content changed. Run Identify to check the current file."
                )
                continue

            try:
                verify_record_identity(
                    source_path, metadata, sha256=stored_sha or None, state=sidecar
                )
            except (IdentityValidationError, OSError) as exc:
                self.logger.warning(
                    "Skipping rename; identity not verified: %s: %s", source_path, exc
                )
                self.identity_refused[str(current_name)] = str(exc)
                continue

            try:
                target_path = target_for(source_path, metadata)
            except MissingTemplateFieldsError as exc:
                self.skipped[str(current_name)] = str(exc)
                continue
            new_name = target_path.name
            unchanged = target_path == source_path or is_same_file_alias(
                source_path, target_path
            )
            if not unchanged and target_path.exists():
                self.logger.warning("Skipping existing target: %s", target_path)
                self.skipped[str(current_name)] = (
                    f"Destination exists: {target_path}. Review both PDFs before choosing a filename."
                )
                continue

            if needs_recovery and not dry_run:
                try:
                    committed = _recover_stale_record(
                        sidecar,
                        str(current_name),
                        apply=True,
                        expected_record=recovery_snapshot,
                        expected_sha256=str(metadata["sha256"]),
                    )
                    if committed is None or {
                        key: value
                        for key, value in committed.items()
                        if key != IDENTITY_KEY
                    } != {
                        key: value
                        for key, value in metadata.items()
                        if key != IDENTITY_KEY
                    }:
                        raise IdentityValidationError(
                            "The PDF or catalog changed before Rename. Retry Rename."
                        )
                    metadata = committed
                except (OSError, ValueError, sqlite3.Error) as exc:
                    self.identity_refused[str(current_name)] = (
                        f"{exc} Review the PDF, then use Update by ID."
                    )
                    continue

            if unchanged:
                self.unchanged[str(current_name)] = new_name
                continue
            if needs_recovery and not dry_run:
                try:
                    current = catalog.get_file(sidecar.directory, str(current_name))
                    if (
                        current is None
                        or CentralPdfRegistryState._record_view(current) != metadata
                    ):
                        raise IdentityValidationError(
                            "The catalog changed before Rename. Retry Rename."
                        )
                    if sha256_file(source_path) != metadata["sha256"]:
                        raise IdentityValidationError(
                            "The PDF changed before Rename. Retry Rename."
                        )
                except (OSError, ValueError, sqlite3.Error) as exc:
                    self.identity_refused[str(current_name)] = str(exc)
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
        """Plan or apply a rename for one tracked resolved PDF.

        Raises
        ------
        MissingTemplateFieldsError
            The template references fields without metadata. No move occurs.
        """
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

        verify_record_identity(source, metadata, sha256=stored_sha or None, state=state)
        self._validate_template_fields(template, metadata)

        destination_directory = (
            source.parent
            if target_directory is None
            else Path(target_directory).expanduser().resolve()
        )
        target = destination_directory / self.render(
            template,
            metadata,
            author_format=author_format,
            max_authors=max_authors,
            authors_when_truncated=authors_when_truncated,
            char_policy=char_policy,
            author_separator=author_separator,
            author_truncation_suffix=author_truncation_suffix,
        )
        if target == source or is_same_file_alias(source, target):
            return source
        if dry_run:
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
