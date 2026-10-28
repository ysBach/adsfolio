"""Verify that a PDF is the paper that metadata claims it is.

Automatic identification may write metadata for a PDF only after the PDF's
own identifiers (arXiv stamp, DOI in document metadata) agree with the
identifiers of the target record. File names, content hashes, search-result
order, and title or author similarity never establish identity.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.parsers import expat

from .arxiv import normalize_arxiv_id

__all__ = [
    "IDENTITY_KEY",
    "IdentityConflictError",
    "IdentityValidationError",
    "PdfOwnIdentity",
    "TargetIdentity",
    "UnverifiedIdentityError",
    "assert_mutation_allowed",
    "extract_pdf_own_identity",
    "target_identity",
]

IDENTITY_KEY = "_adsfolio_identity"
_PROVENANCE_VERSION = 1
_LOGGER = logging.getLogger(__name__)

_NEW_ARXIV = r"\d{4}\.\d{4,5}(?:v\d+)?"
_OLD_ARXIV = r"[A-Za-z][A-Za-z-]*(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?"
_ARXIV_ID = rf"(?:{_NEW_ARXIV}|{_OLD_ARXIV})"
_STAMP_RE = re.compile(rf"^\s*arXiv\s*:\s*({_ARXIV_ID})(?![A-Za-z0-9])", re.IGNORECASE)
_BARE_ARXIV_RE = re.compile(rf"^(?:arxiv\s*:\s*)?({_ARXIV_ID})$", re.IGNORECASE)
_ARXIV_BIBCODE_RE = re.compile(
    r"^\d{4}arXiv(\d{4}\.\d{4,5}|\d{9})[A-Z]$", re.IGNORECASE
)
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIX_RE = re.compile(
    r"^(?:doi\s*:\s*|https?://(?:dx\.)?doi\.org/)", re.IGNORECASE
)
_BIBCODE_RE = re.compile(r"^\d{4}\S{14}[A-Za-z.]$")
_PRISM_NS = (
    "http://prismstandard.org/namespaces/basic/2.0/",
    "http://prismstandard.org/namespaces/basic/2.1/",
    "http://prismstandard.org/namespaces/basic/3.0/",
)
_PDFX_NS = ("http://ns.adobe.com/pdfx/1.3/",)
_DC_NS = "http://purl.org/dc/elements/1.1/"
_XMP_DOI_TAGS = frozenset(f"{{{ns}}}doi" for ns in (*_PRISM_NS, *_PDFX_NS))
_XMP_IDENTIFIER_TAG = f"{{{_DC_NS}}}identifier"
# DocInfo keys that state the PDF's own identifier (other keys, such as a list
# of reference DOIs, are not the PDF naming itself).
_DOCINFO_SINGLE_KEYS = frozenset({"doi", "prism:doi", "arxiv", "arxiv_id"})
_DOCINFO_MULTI_KEYS = frozenset({"dc:identifier"})

# MuPDF is not safe to call from several threads at once.
_FITZ_LOCK = threading.Lock()


class IdentityValidationError(ValueError):
    """The PDF may not be associated with the proposed metadata."""


class IdentityConflictError(IdentityValidationError):
    """The PDF's own identifiers contradict the proposed metadata."""


class UnverifiedIdentityError(IdentityValidationError):
    """The PDF's own identifiers do not confirm the proposed metadata.

    Parameters
    ----------
    message : str
        Failed identity check and recovery instructions.
    manually_confirmable : bool
        Whether explicit review can establish the association.
    candidate_bibcode : str or None
        Proposed ADS record for review. This does not establish PDF identity.
    candidate_sha256 : str or None
        Content hash of the PDF inspected for this candidate.
    candidate_fingerprint : str or None
        Identifier fingerprint of the exact proposed record.

    Notes
    -----
    The 'manually_confirmable' attribute is True only when a person can settle
    the question: the PDF states no identifier of its own, or the metadata
    claims no stable identifier. It is False when the PDF names itself and the
    provider does not corroborate that identifier. Manual confirmation cannot
    override that.
    """

    def __init__(
        self,
        message: str,
        *,
        manually_confirmable: bool = False,
        candidate_bibcode: str | None = None,
        candidate_sha256: str | None = None,
        candidate_fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.manually_confirmable = manually_confirmable
        self.candidate_bibcode = candidate_bibcode
        self.candidate_sha256 = candidate_sha256
        self.candidate_fingerprint = candidate_fingerprint


@dataclass(frozen=True)
class PdfOwnIdentity:
    """Identifiers a PDF states about itself, with the file state they came from.

    Notes
    -----
    'sha256' is the hash of the bytes that were parsed, and 'size' and
    'mtime_ns' are the file state observed before and after reading (a change
    between the two raises instead of returning). 'arxiv_ids' holds
    version-free, lower-case arXiv identifiers, including arXiv DOIs. 'dois'
    holds lower-case DOIs from document metadata, never from body text.
    'errors' lists all parser failures. 'warnings' identifies unreadable
    optional metadata sources. These do not invalidate readable identifiers
    from other sources. 'blocking_errors' excludes those warnings.
    """

    sha256: str
    size: int
    mtime_ns: int
    arxiv_ids: frozenset[str]
    dois: frozenset[str]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def blocking_errors(self) -> tuple[str, ...]:
        """Return failures that prevent automatic identity verification."""
        return tuple(error for error in self.errors if error not in self.warnings)

    @property
    def identifiers(self) -> frozenset[tuple[str, str]]:
        """Own identifiers as ('arxiv' | 'doi', value) pairs."""
        return frozenset(
            [("arxiv", value) for value in self.arxiv_ids]
            + [("doi", value) for value in self.dois]
        )

    @property
    def has_identifier(self) -> bool:
        """Whether the PDF states at least one identifier."""
        return bool(self.arxiv_ids or self.dois)


@dataclass(frozen=True)
class TargetIdentity:
    """Canonical aliases and primary identity of a metadata record.

    'primary' is 'bibcode:<bibcode>' for ADS records, 'ids:<sorted DOIs and
    arXiv ids>' for other records with stable identifiers, and
    'bib:<title>|<authors>|<year>' for hand-entered records with none. Only
    the first two are stable identities; the last identifies manual input.
    """

    primary: str
    bibcodes: frozenset[str]
    dois: frozenset[str]
    arxiv_ids: frozenset[str]

    @property
    def has_stable_identity(self) -> bool:
        """Whether the record names a bibcode, DOI or arXiv identifier."""
        return self.primary.startswith(("bibcode:", "ids:"))

    @property
    def fingerprint(self) -> str:
        """Digest of the primary identity and every claimed identifier.

        Any change to a claimed bibcode, DOI or arXiv id changes the digest, so
        a stored proof cannot be reused for an edited record. '' if the record
        has no identity at all.
        """
        if not self.primary:
            return ""
        claims = json.dumps(
            [
                self.primary,
                sorted(self.bibcodes),
                sorted(self.dois),
                sorted(self.arxiv_ids),
            ]
        )
        return hashlib.sha256(claims.encode("utf-8")).hexdigest()

    def claims_outside(self, authority: TargetIdentity) -> list[str]:
        """List claimed identifiers (and the primary) that 'authority' does not list."""
        missing = [f"bibcode {v}" for v in sorted(self.bibcodes - authority.bibcodes)]
        missing += [f"DOI {v}" for v in sorted(self.dois - authority.dois)]
        missing += [f"arXiv {v}" for v in sorted(self.arxiv_ids - authority.arxiv_ids)]
        return missing


def _classify(token: Any) -> tuple[str, str] | None:
    """Return ('arxiv' | 'doi' | 'bibcode', canonical value) for an alias string."""
    text = " ".join(str(token or "").split())
    if not text:
        return None
    bare = _DOI_PREFIX_RE.sub("", text)
    if _DOI_RE.fullmatch(bare):
        match = _ARXIV_DOI_RE.fullmatch(bare)
        if match:
            arxiv = normalize_arxiv_id(match.group(1))
            return ("arxiv", arxiv) if arxiv else None
        return "doi", bare.lower()
    match = _BARE_ARXIV_RE.fullmatch(text)
    if match:
        return "arxiv", normalize_arxiv_id(match.group(1))
    match = _ARXIV_BIBCODE_RE.fullmatch(text)
    if match:
        value = match.group(1)
        if "." not in value:
            value = f"{value[:4]}.{value[4:]}"
        return "arxiv", value.lower()
    if _BIBCODE_RE.fullmatch(text):
        return "bibcode", text.lower()
    return None


def _values(value: Any) -> Iterable[Any]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set, frozenset)):
        return value
    return (value,)


def _scalar_text(value: Any) -> str:
    items = list(_values(value))
    return " ".join(str(items[0]).split()).casefold() if items else ""


def _bibliographic_primary(metadata: Mapping[str, Any]) -> str:
    title = _scalar_text(metadata.get("title"))
    authors = ";".join(
        " ".join(str(a).split()).casefold() for a in _values(metadata.get("author"))
    )
    year = _scalar_text(metadata.get("year"))
    return f"bib:{title}|{authors}|{year}" if title else ""


def target_identity(metadata: Mapping[str, Any]) -> TargetIdentity:
    """Collect canonical aliases from the identifier fields of a metadata record.

    Reads ``bibcode``, ``alternate_bibcode``, ``identifier``, ``doi``, ``arxiv``
    and ``arxiv_id``. Nothing else (title, authors, file name) is consulted.
    """
    found: dict[str, set[str]] = {"arxiv": set(), "doi": set(), "bibcode": set()}
    primary_bibcode = ""
    for key in (
        "bibcode",
        "alternate_bibcode",
        "identifier",
        "doi",
        "arxiv",
        "arxiv_id",
    ):
        for item in _values(metadata.get(key)):
            if key in {"bibcode", "alternate_bibcode"}:
                text = str(item or "").strip()
                if text:
                    found["bibcode"].add(text.lower())
                    if key == "bibcode" and not primary_bibcode:
                        primary_bibcode = text.lower()
            classified = _classify(item)
            if classified is not None:
                found[classified[0]].add(classified[1])
    if primary_bibcode:
        primary = f"bibcode:{primary_bibcode}"
    elif found["doi"] or found["arxiv"]:
        ids = [f"doi:{v}" for v in found["doi"]] + [
            f"arxiv:{v}" for v in found["arxiv"]
        ]
        primary = "ids:" + ",".join(sorted(ids))
    else:
        primary = _bibliographic_primary(metadata)
    return TargetIdentity(
        primary=primary,
        bibcodes=frozenset(found["bibcode"]),
        dois=frozenset(found["doi"]),
        arxiv_ids=frozenset(found["arxiv"]),
    )


def _display_primary(metadata: Mapping[str, Any], target: TargetIdentity) -> str:
    """Return the primary identity with the supplied bibcode capitalization."""
    if target.primary.startswith("bibcode:"):
        for value in _values(metadata.get("bibcode")):
            original = str(value or "").strip()
            if original:
                return f"bibcode:{original}"
    return target.primary


def _stamp_arxiv_ids(page: Any) -> set[str]:
    """Return arXiv identifiers from text that starts 'arXiv:' and is not horizontal.

    The arXiv watermark is printed along the page margin. Horizontal text that
    starts with 'arXiv:' (a citation, a footnote) is not the PDF naming itself.
    Orientation is read from the text matrix composed with the current
    transformation matrix.
    """
    fragments: list[tuple[bool, tuple[float, float], str]] = []

    def visit(text: str, cm: Any, tm: Any, _font: Any, _size: Any) -> None:
        if not text.strip():
            return
        a1, b1, _c1, _d1 = (float(v) for v in tm[:4])
        a2, b2, c2, d2 = (float(v) for v in cm[:4])
        a = a1 * a2 + b1 * c2
        b = a1 * b2 + b1 * d2
        fragments.append((b != 0.0 or a <= 0.0, (a, b), text))

    page.extract_text(visitor_text=visit)
    found: set[str] = set()
    for index, (rotated, direction, text) in enumerate(fragments):
        if not rotated or not text.lstrip().lower().startswith("arxiv"):
            continue
        run = text
        for later_rotated, later_direction, later in fragments[index + 1 :]:
            if not later_rotated or later_direction != direction:
                break
            run += later
        match = _STAMP_RE.match(run)
        if match:
            found.add(normalize_arxiv_id(match.group(1)))
    return found


def _fitz_stamp_arxiv_ids(data: bytes) -> set[str]:
    """Optional PyMuPDF fallback for the margin stamp; empty if unavailable."""
    try:
        import fitz
    except ImportError:
        return set()
    found: set[str] = set()
    with _FITZ_LOCK:
        document = fitz.open(stream=data, filetype="pdf")
        try:
            if document.page_count == 0:
                return found
            blocks = document[0].get_text("dict")["blocks"]
            for block in blocks:
                for line in block.get("lines", ()):
                    dx, dy = line["dir"]
                    if dy == 0.0 and dx > 0.0:
                        continue
                    text = "".join(span["text"] for span in line["spans"])
                    match = _STAMP_RE.match(text)
                    if match:
                        found.add(normalize_arxiv_id(match.group(1)))
        finally:
            document.close()
    return found


def _add_metadata_token(
    token: str, arxiv_ids: set[str], dois: set[str], errors: list[str], where: str
) -> None:
    classified = _classify(token)
    if classified is None or classified[0] == "bibcode":
        errors.append(f"unparseable identifier in {where}")
    elif classified[0] == "arxiv":
        arxiv_ids.add(classified[1])
    else:
        dois.add(classified[1])


def _add_known_tokens(
    tokens: Iterable[str], arxiv_ids: set[str], dois: set[str]
) -> None:
    """Keep tokens that are DOIs or arXiv ids; other identifiers are ignored."""
    for token in tokens:
        classified = _classify(token)
        if classified is not None and classified[0] == "arxiv":
            arxiv_ids.add(classified[1])
        elif classified is not None and classified[0] == "doi":
            dois.add(classified[1])


def _xmp_tree_with_unbound_prefixes(packet: bytes) -> ET.Element | None:
    """Read declared namespaces without guessing undeclared prefix meanings.

    An undeclared prefix on an unrelated property does not prevent reading
    properties with explicit namespace declarations. Invalid XML structure
    returns no tree. Parsing changes no PDF bytes.
    """
    builder = ET.TreeBuilder()
    scopes: list[dict[str, str]] = [{}]

    def expand(name: str, scope: dict[str, str], *, attribute: bool = False) -> str:
        if ":" in name:
            prefix, local = name.split(":", 1)
            namespace = scope.get(prefix)
        else:
            local = name
            namespace = None if attribute else scope.get("")
        return f"{{{namespace}}}{local}" if namespace else name

    def start(name: str, attributes: dict[str, str]) -> None:
        scope = dict(scopes[-1])
        for key, value in attributes.items():
            if key == "xmlns":
                scope[""] = value
            elif key.startswith("xmlns:"):
                scope[key[6:]] = value
        scopes.append(scope)
        builder.start(
            expand(name, scope),
            {
                expand(key, scope, attribute=True): value
                for key, value in attributes.items()
                if key != "xmlns" and not key.startswith("xmlns:")
            },
        )

    def end(name: str) -> None:
        builder.end(expand(name, scopes.pop()))

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(packet, True)
    except expat.ExpatError:
        return None
    return builder.close()


def _read_xmp(
    packet: bytes,
    arxiv_ids: set[str],
    dois: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Read DOI and identifier properties by namespace URI, whatever the prefix."""
    try:
        root = ET.fromstring(packet.strip())
    except ET.ParseError:
        errors.append("XMP metadata is not well-formed")
        warnings.append("XMP metadata is not well-formed")
        root = _xmp_tree_with_unbound_prefixes(packet.strip())
        if root is None:
            return
    for element in root.iter():
        for name, value in element.attrib.items():
            if name in _XMP_DOI_TAGS and value:
                _add_metadata_token(value, arxiv_ids, dois, errors, "XMP")
        if element.tag in _XMP_DOI_TAGS:
            text = "".join(element.itertext()).strip()
            if text:
                _add_metadata_token(text, arxiv_ids, dois, errors, "XMP")
        elif element.tag == _XMP_IDENTIFIER_TAG:
            _add_known_tokens("".join(element.itertext()).split(), arxiv_ids, dois)
        if _XMP_IDENTIFIER_TAG in element.attrib:
            _add_known_tokens(
                element.attrib[_XMP_IDENTIFIER_TAG].split(), arxiv_ids, dois
            )


def _read_pdf_identifiers(
    data: bytes,
    arxiv_ids: set[str],
    dois: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Collect own identifiers from DocInfo, XMP and the first-page margin stamp."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            errors.append("encrypted PDF")
            return
        page_count = len(reader.pages)
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        errors.append(f"cannot open PDF: {type(exc).__name__}")
        return
    if page_count == 0:
        errors.append("PDF has no pages")
        return

    try:
        for key, value in dict(reader.metadata or {}).items():
            name = str(key).lstrip("/").lower()
            if value is None:
                continue
            if name in _DOCINFO_SINGLE_KEYS:
                _add_metadata_token(str(value), arxiv_ids, dois, errors, "DocInfo")
            elif name in _DOCINFO_MULTI_KEYS:
                _add_known_tokens(str(value).split(), arxiv_ids, dois)
            elif name == "title" and _DOI_PREFIX_RE.match(str(value).strip()):
                # Some publisher PDFs put a DOI label in the entire title field.
                # Keep the whole value so prose and reference lists do not match.
                _add_known_tokens([str(value)], arxiv_ids, dois)
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        errors.append(f"cannot read DocInfo: {type(exc).__name__}")

    try:
        stream = reader.trailer["/Root"].get("/Metadata")
        if stream:
            _read_xmp(stream.get_object().get_data(), arxiv_ids, dois, errors, warnings)
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        message = f"cannot read XMP: {type(exc).__name__}"
        errors.append(message)
        warnings.append(message)

    try:
        arxiv_ids |= _stamp_arxiv_ids(reader.pages[0])
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        errors.append(f"cannot read first page: {type(exc).__name__}")


def extract_pdf_own_identity(path: Path) -> PdfOwnIdentity:
    """Read the identifiers a PDF states about itself.

    The file is read once; the hash and every parsed identifier come from the
    same bytes. Sources are the arXiv margin stamp on page 1, DocInfo DOI keys,
    a complete labeled DOI in DocInfo Title, and XMP
    ``prism:doi`` / ``dc:identifier``. DOIs printed in the body (often
    references) are never used. Parser failures remain in ``errors``.
    Unreadable optional XMP is also listed in ``warnings``. It does not
    invalidate readable identifiers or corroborated document DOI evidence.

    Raises
    ------
    IdentityValidationError
        If the file cannot be read or changed while it was read.
    """
    path = Path(path)
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise IdentityValidationError(f"Cannot read {path.name}: {exc}") from exc
    signature = (before.st_size, before.st_mtime_ns, before.st_ino)
    if signature != (after.st_size, after.st_mtime_ns, after.st_ino) or len(data) != (
        after.st_size
    ):
        raise IdentityValidationError(f"{path.name} changed while it was being read")

    arxiv_ids: set[str] = set()
    dois: set[str] = set()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        _read_pdf_identifiers(data, arxiv_ids, dois, errors, warnings)
    except ImportError:
        errors.append("pypdf is not installed")
    except Exception as exc:  # noqa: BLE001 - parsers raise many types
        errors.append(f"parser failure: {type(exc).__name__}")
    if not arxiv_ids:
        try:
            arxiv_ids |= _fitz_stamp_arxiv_ids(data)
        except Exception as exc:  # noqa: BLE001 - parsers raise many types
            _LOGGER.debug("PyMuPDF fallback failed for %s: %s", path.name, exc)
    return PdfOwnIdentity(
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        mtime_ns=after.st_mtime_ns,
        arxiv_ids=frozenset(arxiv_ids),
        dois=frozenset(dois),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def _stamp(
    metadata: Mapping[str, Any], status: str, sha256: str, target: TargetIdentity
):
    """Return the stored proof if it has 'status' and is bound to this file and record."""
    stamp = metadata.get(IDENTITY_KEY)
    if (
        isinstance(stamp, Mapping)
        and stamp.get("status") == status
        and stamp.get("sha256") == sha256
        and bool(target.fingerprint)
        and stamp.get("identity") == target.fingerprint
    ):
        return stamp
    return None


def _primary_arxiv_ids(target: TargetIdentity) -> frozenset[str]:
    """Return the eprint ids of the primary when the primary is an arXiv object.

    An arXiv bibcode (2012arXiv1208.4606R) or a record identified only by
    arXiv ids / arXiv DOIs names one preprint. A journal primary returns an
    empty set, because it may list several preprint and DOI aliases.
    """
    if target.primary.startswith("bibcode:"):
        match = _ARXIV_BIBCODE_RE.fullmatch(target.primary.removeprefix("bibcode:"))
        if match is None:
            return frozenset()
        value = match.group(1)
        return frozenset({value if "." in value else f"{value[:4]}.{value[4:]}"})
    if target.primary.startswith("ids:") and not target.dois:
        return target.arxiv_ids
    return frozenset()


def _compare(
    own: PdfOwnIdentity, authority: TargetIdentity
) -> tuple[list[str], list[str]]:
    """Split the PDF's identifiers into contradicted and uncorroborated ones.

    An identifier is contradicted when the authority lists others of the same
    kind but not this one, and uncorroborated when the authority lists none of
    that kind.
    """
    contradicted: list[str] = []
    uncorroborated: list[str] = []
    for label, mine, theirs in (
        ("arXiv", own.arxiv_ids, authority.arxiv_ids),
        ("DOI", own.dois, authority.dois),
    ):
        for value in sorted(mine - theirs):
            (contradicted if theirs else uncorroborated).append(f"{label} {value}")
    return contradicted, uncorroborated


def _provenance(
    status: str, authority: str, evidence: PdfOwnIdentity, target: TargetIdentity
) -> dict[str, Any]:
    proof = {
        "version": _PROVENANCE_VERSION,
        "status": status,
        "authority": authority,
        "sha256": evidence.sha256,
        "identity": target.fingerprint,
        "target": target.primary,
        "own_arxiv": sorted(evidence.arxiv_ids),
        "own_doi": sorted(evidence.dois),
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if evidence.warnings:
        proof["parser_warnings"] = list(evidence.warnings)
    return proof


def assert_mutation_allowed(
    pdf_path: Path,
    metadata: Mapping[str, Any],
    *,
    expected_sha256: str | None = None,
    manual_confirmation: bool = False,
    provider_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Allow associating a PDF with 'metadata' only if the PDF vouches for it.

    The current file is always hashed and parsed. A new 'verified' result needs
    an authoritative 'provider_document' (the ADS or external-provider record)
    that explicitly lists every identifier 'metadata' claims (its primary bibcode
    may be an alternate bibcode of the provider record) and whose aliases
    include every identifier the PDF states. Aliases that appear only in
    'metadata' (for example secondary fields merged into a legacy record)
    never verify anything. Without a provider document, a stored 'verified'
    proof bound to this file hash and every identifier this record claims is
    accepted; otherwise the result is unverified.

    Unreadable optional XMP produces a recorded warning. Readable matching
    identifiers or corroborated document DOI evidence can still verify the
    record. Every readable own identifier must agree, including identifiers
    recovered from XMP.

    Manual confirmation (the argument, or a stored 'manual_confirmed' proof
    bound to this file hash and record) admits only two cases: the PDF states
    no identifier of its own, or the metadata claims no stable identifier
    (hand-entered title, authors, year). It never overrides a PDF identifier
    that the provider contradicts or fails to corroborate.

    Parameters
    ----------
    pdf_path
        PDF to check.
    metadata
        Proposed record (ADS document, external record, or library entry).
    expected_sha256
        Hash the caller believes the file has; a different current hash raises.
    manual_confirmation
        The user explicitly confirmed an otherwise unverified association.
    provider_document
        Fresh record from the authoritative provider for 'metadata'.

    Returns
    -------
    dict
        Provenance for 'IDENTITY_KEY': status ('verified' or
        'manual_confirmed'), authority, file hash, record fingerprint, and the
        PDF's own identifiers. Manual provenance never means "ADS resolved".

    Raises
    ------
    IdentityConflictError
        A PDF identifier contradicts the provider (or, without a provider,
        the metadata).
    UnverifiedIdentityError
        The PDF does not confirm the record; see 'manually_confirmable'.
    IdentityValidationError
        The file is unreadable, changed, differs from 'expected_sha256', or
        'provider_document' is not the record named by 'metadata'.
    """
    evidence = extract_pdf_own_identity(pdf_path)
    name = Path(pdf_path).name
    if expected_sha256 is not None and expected_sha256 != evidence.sha256:
        raise IdentityValidationError(
            f"{name} no longer matches the expected content hash"
        )
    target = target_identity(metadata)
    target_label = _display_primary(metadata, target)
    stable = target.has_stable_identity

    authority: TargetIdentity | None = None
    if provider_document is not None:
        authority = target_identity(provider_document)
        # Every identifier the record claims, including its primary bibcode,
        # must be explicitly listed by the provider document.
        outside = target.claims_outside(authority)
        if not stable or outside:
            authority_label = _display_primary(provider_document, authority)
            raise IdentityValidationError(
                f"The provider document ({authority_label or 'no identifier'}) does "
                f"not list {', '.join(outside) or 'a stable identifier'} claimed by "
                f"the proposed record ({target_label or 'no identifier'})"
            )

    contradicted, uncorroborated = _compare(evidence, authority or target)
    # An arXiv primary is one specific eprint: an alias cannot turn eprint B
    # into eprint A.
    primary_arxiv = _primary_arxiv_ids(target)
    if primary_arxiv:
        contradicted += [
            f"arXiv {value}"
            for value in sorted(evidence.arxiv_ids - primary_arxiv)
            if f"arXiv {value}" not in contradicted
        ]
    if contradicted:
        raise IdentityConflictError(
            f"{name} identifies itself as {', '.join(contradicted)}, which "
            f"contradicts the proposed record ({target_label or 'no identifier'})"
        )

    if evidence.has_identifier and not evidence.blocking_errors:
        if authority is not None and not uncorroborated:
            return _provenance("verified", "provider_document", evidence, target)
        proof = _stamp(metadata, "verified", evidence.sha256, target)
        if (
            authority is None
            and stable
            and proof is not None
            and list(proof.get("own_arxiv", ())) == sorted(evidence.arxiv_ids)
            and list(proof.get("own_doi", ())) == sorted(evidence.dois)
        ):
            return _provenance("verified", "stored_proof", evidence, target)

    if (
        authority is None
        and stable
        and not evidence.blocking_errors
        and not evidence.dois
        and not contradicted
    ):
        proof = _stamp(metadata, "verified", evidence.sha256, target)
        if (
            proof is not None
            and proof.get("authority")
            in ("corroborated_document_doi", "stored_proof", "provider_document")
            and list(proof.get("own_arxiv", ())) == sorted(evidence.arxiv_ids)
        ):
            proof_dois = list(proof.get("own_doi", ()))
            if bool(proof_dois) and set(proof_dois).issubset(target.dois):
                corroborated_evidence = PdfOwnIdentity(
                    sha256=evidence.sha256,
                    size=evidence.size,
                    mtime_ns=evidence.mtime_ns,
                    arxiv_ids=evidence.arxiv_ids,
                    dois=frozenset(proof_dois),
                    errors=evidence.errors,
                    warnings=evidence.warnings,
                )
                return _provenance(
                    "verified", "stored_proof", corroborated_evidence, target
                )

    document_evidence_reason = ""
    if (
        authority is not None
        and provider_document is not None
        and not evidence.blocking_errors
        and not contradicted
        and not uncorroborated
    ):
        try:
            pdf_bytes = Path(pdf_path).read_bytes()
        except OSError as exc:
            raise IdentityValidationError(f"Cannot read {name}: {exc}") from exc
        if hashlib.sha256(pdf_bytes).hexdigest() != evidence.sha256:
            raise IdentityValidationError(
                f"{name} changed while checking document evidence"
            )
        from .document_identity_evidence import check_document_doi_evidence

        document_evidence = check_document_doi_evidence(pdf_bytes, provider_document)
        corroborated_doi = document_evidence.doi
        document_evidence_reason = document_evidence.reason
        if corroborated_doi is not None and (
            not target.dois or corroborated_doi in target.dois
        ):
            corroborated_evidence = PdfOwnIdentity(
                sha256=evidence.sha256,
                size=evidence.size,
                mtime_ns=evidence.mtime_ns,
                arxiv_ids=evidence.arxiv_ids,
                dois=frozenset({corroborated_doi}),
                errors=evidence.errors,
                warnings=evidence.warnings,
            )
            return _provenance(
                "verified",
                "corroborated_document_doi",
                corroborated_evidence,
                target,
            )

    confirmable = not evidence.has_identifier or not stable
    wanted = manual_confirmation or (
        not evidence.blocking_errors
        and _stamp(metadata, "manual_confirmed", evidence.sha256, target) is not None
    )
    if wanted and confirmable:
        return _provenance("manual_confirmed", "manual", evidence, target)
    if evidence.blocking_errors:
        reason = "; ".join(evidence.blocking_errors)
    elif uncorroborated:
        reason = f"the provider record does not corroborate {', '.join(uncorroborated)}"
    elif not evidence.has_identifier:
        reason = (
            document_evidence_reason
            or "No document identifier could be verified from the PDF."
        )
    else:
        reason = "no authoritative provider record"
    candidate = target_label.removeprefix("bibcode:") or "the proposed record"
    candidate_bibcode = (
        target_label.removeprefix("bibcode:")
        if target.primary.startswith("bibcode:")
        else None
    )
    message = f"{name}: Candidate {candidate} needs review. {reason}"
    if not message.endswith((".", "!", "?")):
        message += "."
    if (
        evidence.warnings
        and not evidence.has_identifier
        and not evidence.blocking_errors
    ):
        message += " Some embedded metadata could not be read."
    if confirmable:
        message += " Open PDF to check the paper."
        if candidate_bibcode:
            message += " Use This ID to confirm this candidate, or Update by ID to choose another record."
        else:
            message += " Use Update by ID to review and confirm this candidate."
    else:
        message += " Run Update by ID with an identifier printed in the PDF."
    message += " The PDF contents and filename are unchanged."
    raise UnverifiedIdentityError(
        message,
        manually_confirmable=confirmable,
        candidate_bibcode=candidate_bibcode,
        candidate_sha256=evidence.sha256,
        candidate_fingerprint=target.fingerprint,
    )
