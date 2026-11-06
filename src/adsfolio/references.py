"""Local references with optional ADS identities and PDF attachments.

Paste BibTeX with ``parse_bibtex()`` to preview it, then save it with
``LocalReferences.import_bibtex()``. Exports work without an ADS connection.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import io
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from bibtexparser.bibdatabase import BibDatabase, as_text
from bibtexparser.bibtexexpression import BibtexExpression
from bibtexparser.bparser import BibTexParser
from bibtexparser.bwriter import BibTexWriter
from pyparsing import ParseBaseException

from .bibtex.sanitize import remove_fields, split_entry, with_cite_key
from .citekeys import KeyAssignment, KeyPolicy, assign_keys, citation_key
from .formatting import html_to_latex
from .pdf.catalog import (
    CITE_KEY_METADATA_KEY,
    LOCKED_METADATA_FIELDS_KEY,
    REFERENCE_FIELD_MAP,
    REFERENCE_METADATA_KEY,
    PdfCatalog,
)

# Length of every ADS bibcode.
BIBCODE_LENGTH = 19


def _parse_database(text: str, *, strict: bool = True) -> BibDatabase:
    """Reject malformed declarations instead of accepting them as comments.

    With `strict` false, the separate declaration check is skipped. Use it
    only for text that this module wrote, where a comparison of the parsed
    entries detects any entry that the parser did not read back.
    """
    try:
        if strict:
            grammar = BibtexExpression()
            grammar.implicit_comment.add_condition(
                lambda tokens: not re.search(r"@[A-Za-z]+\s*[{(]", tokens[0]),
                message="Malformed BibTeX declaration",
                fatal=True,
            )
            grammar.parse_file(io.StringIO(text))
        database = BibTexParser(ignore_nonstandard_types=False).parse(text)
        database.preambles = [as_text(value) for value in database.preambles]
        return database
    except (ParseBaseException, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid BibTeX: {exc}") from exc


def _check_round_trip(
    entries: Sequence[Mapping[str, Any]], references: Sequence[Mapping[str, Any]]
) -> None:
    """Check the BibTeX round trip of all entries with one write and one parse.

    Parsing every entry alone repeats the grammar setup for each entry. When
    the combined check finds a difference, each reference is checked alone,
    so the error names the paper and fields as `_validated_entry` does.
    """
    if not entries:
        return
    database = BibDatabase()
    database.entries = [dict(entry) for entry in entries]
    writer = BibTexWriter()
    writer.order_entries_by = None
    try:
        parsed = _parse_database(writer.write(database), strict=False)

        def by_key(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
            return sorted(items, key=lambda item: str(item.get("ID")))

        if _same_bibtex_entries(by_key(parsed.entries), by_key(database.entries)):
            return
    except ValueError:
        pass
    # The check of each entry alone decides, as it did before the combined check.
    for reference in references:
        _validated_entry(
            reference["cite_key"], reference["entry_type"], reference["fields"]
        )


def _same_bibtex_entries(
    parsed: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]
) -> bool:
    """Compare entries as BibTeX reads them.

    BibTeX treats a run of spaces, tabs, and line breaks inside a field value
    as one space, and the parser collapses such runs. Values that differ only
    in this way are the same value.
    """

    def normalized(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value
            for key, value in entry.items()
        }

    return [normalized(entry) for entry in parsed] == [
        normalized(entry) for entry in expected
    ]


def _validated_entry(
    cite_key: str,
    entry_type: str,
    fields: Mapping[str, str],
    *,
    round_trip: bool = True,
) -> dict[str, Any]:
    """Validate editable values by checking a semantic BibTeX round trip.

    With `round_trip` false, only the key, type, and field names are checked.
    The caller then checks the round trip of many entries at once.
    """
    if not cite_key or re.search(r"[\s,{}()=\\#%\"]", cite_key):
        raise ValueError(
            "Citation key must be nonempty and contain no BibTeX delimiters"
        )
    if not re.fullmatch(r"[A-Za-z]+", entry_type):
        raise ValueError("Entry type must contain letters only")
    clean: dict[str, str] = {}
    for name, value in fields.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_().+\-]+", name):
            raise ValueError(f"Invalid BibTeX field name: {name!r}")
        normalized = name.lower()
        if normalized in {"id", "entrytype"} or normalized in clean:
            raise ValueError(f"Reserved or duplicate BibTeX field: {name}")
        if not isinstance(value, str):
            raise ValueError(f"BibTeX field {name} must be text")  # noqa: TRY004 -- user-input API
        clean[normalized] = value
    entry = {"ID": cite_key, "ENTRYTYPE": entry_type.lower(), **clean}
    if clean and round_trip:
        database = BibDatabase()
        database.entries = [entry]
        try:
            parsed = _parse_database(BibTexWriter().write(database))
            if not _same_bibtex_entries(parsed.entries, [entry]):
                raise ValueError("BibTeX changes field values during validation")
        except ValueError as exc:
            invalid_fields = []
            for name, value in clean.items():
                single_entry = {
                    "ID": cite_key,
                    "ENTRYTYPE": entry_type.lower(),
                    name: value,
                }
                database.entries = [single_entry]
                try:
                    parsed = _parse_database(BibTexWriter().write(database))
                    if not _same_bibtex_entries(parsed.entries, [single_entry]):
                        invalid_fields.append(name)
                except ValueError:
                    invalid_fields.append(name)
            field_names = (
                ", ".join(invalid_fields) if invalid_fields else ", ".join(clean)
            )
            title = clean.get("title") or cite_key
            raise ValueError(
                f'Cannot generate BibTeX for "{title}" (citation key: {cite_key}).\n'
                f"Field(s): {field_names}.\n"
                "The field contains invalid BibTeX syntax or cannot be preserved "
                "during export. Check for unbalanced braces in LaTeX or math.\n"
                "Open this paper's details, correct the named field(s), "
                "and export again."
            ) from exc
    return {"cite_key": cite_key, "entry_type": entry_type.lower(), "fields": clean}


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    """Preview BibTeX without changing the catalog.

    Parameters
    ----------
    text
        Complete BibTeX text. String macros are expanded; nested braces,
        LaTeX commands, custom fields, and cross-reference keys are retained.

    Returns
    -------
    list of dict
        Each entry has `cite_key`, `entry_type`, `fields`, and `source`.
        Preambles are retained; import stores the original input once.

    Raises
    ------
    ValueError
        If the text is malformed, has duplicate keys, or contains no entries.
    """
    database = _parse_database(text)
    if not database.entries:
        raise ValueError("No BibTeX entries found")
    result = []
    keys: set[str] = set()
    for entry in database.entries:
        key = entry["ID"]
        if key in keys:
            raise ValueError(f"Duplicate citation key in import: {key}")
        keys.add(key)
        reference = _validated_entry(
            key,
            entry["ENTRYTYPE"],
            {
                name: value
                for name, value in entry.items()
                if name not in {"ID", "ENTRYTYPE"}
            },
        )
        reference.update(
            source="bibtex",
            preambles=list(database.preambles),
        )
        result.append(reference)
    return result


class CitationKeyError(ValueError):
    """Export stopped because papers have no key or share a key.

    Attributes
    ----------
    assignment : `KeyAssignment`
        Keys found, papers without a key, and shared keys.
    references : dict
        The selected references by document ID, for display.
    """

    def __init__(
        self, assignment: KeyAssignment, references: Mapping[int, Mapping[str, Any]]
    ) -> None:
        self.assignment = assignment
        self.references = dict(references)
        parts = []
        if assignment.missing:
            parts.append(f"{len(assignment.missing)} without a citation key")
        if assignment.clashes:
            shared = ", ".join(sorted(assignment.clashes))
            parts.append(f"duplicate citation keys ({shared})")
        super().__init__(
            "Selected references need citation keys: "
            + "; ".join(parts)
            + ". Type a key for each one before export."
        )


def _own_key(metadata: Mapping[str, Any]) -> str:
    """Return the key that the user typed or imported, or ``""``."""
    reference = metadata.get(REFERENCE_METADATA_KEY)
    if isinstance(reference, Mapping) and reference.get("cite_key"):
        return str(reference["cite_key"])
    return str(metadata.get(CITE_KEY_METADATA_KEY) or "")


def _reference_from_document(
    document: Mapping[str, Any], policy: KeyPolicy | None = None
) -> dict[str, Any]:
    """Adapt stored ADS metadata while preserving explicit local BibTeX fields.

    The citation key is the paper's own key, or else the key that `policy`
    makes. It is ``""`` when neither gives one.
    """
    metadata = document["metadata"]
    reference = metadata.get(REFERENCE_METADATA_KEY)
    if reference is not None:
        return {
            **copy.deepcopy(reference),
            "id": document["id"],
            "bibcode": document["bibcode"],
        }
    fields: dict[str, str] = {}
    for target, source in REFERENCE_FIELD_MAP.items():
        value = metadata.get(source)
        if isinstance(value, list):
            value = (
                " and ".join(str(item) for item in value)
                if source == "author"
                else next(iter(value), "")
            )
        if value not in (None, ""):
            fields[target] = (
                str(value)
                if source == "doi"
                else html_to_latex(str(value), bibtex=True)
            )
    return {
        "id": document["id"],
        "bibcode": document["bibcode"],
        "cite_key": citation_key(
            {**metadata, "bibcode": document["bibcode"]},
            policy or KeyPolicy(),
            own_key=_own_key(metadata),
        ),
        "entry_type": "article" if fields.get("journal") else "misc",
        "fields": fields,
        "source": "ads",
        "preambles": [],
    }


def ads_bibtex_fingerprint(metadata: Mapping[str, Any]) -> str:
    """Return a hash of the ADS fields that a saved ADS BibTeX entry reflects.

    A saved entry is used only while this hash is unchanged. After an ADS
    refresh changes these fields, export asks ADS for the entry again.
    """
    names = sorted({"bibcode", *REFERENCE_FIELD_MAP.values()})
    payload = json.dumps(
        {name: metadata.get(name) for name in names}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _uses_ads_bibtex(document: Mapping[str, Any]) -> bool:
    """Return whether export may use ADS's own entry for this document.

    Papers with a local reference or a locked bibliographic field keep the
    local fields, so your edits stay in the export.
    """
    metadata = document["metadata"]
    if not document.get("bibcode") or metadata.get(REFERENCE_METADATA_KEY):
        return False
    locked = set(metadata.get(LOCKED_METADATA_FIELDS_KEY) or ())
    return not locked & set(REFERENCE_FIELD_MAP.values())


def _author_names(value: str) -> list[str]:
    """Split the BibTeX `and` separator only outside protected brace groups."""
    names = []
    depth = 0
    start = 0
    for match in re.finditer(r"[{}]|\band\b", value, flags=re.IGNORECASE):
        token = match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        elif (
            depth == 0
            and value[match.start() - 1 : match.start()].isspace()
            and value[match.end() : match.end() + 1].isspace()
        ):
            names.append(value[start : match.start()].strip())
            start = match.end()
    if value[start:].strip():
        names.append(value[start:].strip())
    return names


def _document_metadata(
    reference: Mapping[str, Any],
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project editable fields into PDF views and lock them against refresh."""
    metadata = copy.deepcopy(dict(existing or {}))
    fields = reference["fields"]
    projected: set[str] = {REFERENCE_METADATA_KEY, "bibcode"}
    for field, target in REFERENCE_FIELD_MAP.items():
        projected.add(target)
        value = fields.get(field, "")
        metadata[target] = (
            [value] if target in {"title", "author", "page", "doi"} and value else value
        )
        if target == "author":
            metadata[target] = _author_names(value)
    metadata[REFERENCE_METADATA_KEY] = {
        key: copy.deepcopy(value)
        for key, value in reference.items()
        if key not in {"id", "bibcode", "attachments"}
    }
    metadata[LOCKED_METADATA_FIELDS_KEY] = sorted(
        projected | set(metadata.get(LOCKED_METADATA_FIELDS_KEY, []))
    )
    metadata["bibcode"] = reference.get("bibcode")
    return metadata


class LocalReferences:
    """Manage local references using an existing `PdfCatalog` connection.

    The caller owns and closes `catalog`. Edits and imports preserve document
    IDs; no operation merges records or contacts ADS automatically.
    """

    def __init__(self, catalog: PdfCatalog, policy: KeyPolicy | None = None) -> None:
        self.catalog = catalog
        self.policy = policy or KeyPolicy()
        self._export_memo: tuple[Any, list[dict[str, Any]]] | None = None

    def _export_references(self) -> list[dict[str, Any]]:
        """Return every reference without PDF locations, for BibTeX export.

        The list is kept until the catalog changes or the key policy changes,
        so repeated previews read the catalog once. Do not change it.
        """
        token = getattr(self.catalog, "change_token", None)
        key = (token(), self.policy) if callable(token) else None
        if key is not None and self._export_memo is not None:
            if self._export_memo[0] == key:
                return self._export_memo[1]
        saved_entries = getattr(self.catalog, "ads_bibtex_entries", dict)()
        references = []
        for document in self.catalog.list_documents():
            references.append(
                self._ads_reference(document, saved_entries)
                or _reference_from_document(document, self.policy)
            )
        if key is not None:
            self._export_memo = (key, references)
        return references

    def _ads_reference(
        self,
        document: Mapping[str, Any],
        saved_entries: Mapping[str, tuple[str | None, str]],
    ) -> dict[str, Any] | None:
        """Return the reference from the saved ADS entry, or `None`."""
        saved = saved_entries.get(document.get("bibcode") or "")
        if (
            saved is None
            or not saved[0]
            or not _uses_ads_bibtex(document)
            or saved[1] != ads_bibtex_fingerprint(document["metadata"])
        ):
            return None
        parts = split_entry(saved[0])
        if parts is None or not parts[2]:
            return None
        metadata = document["metadata"]
        return {
            "id": document["id"],
            "bibcode": document["bibcode"],
            "cite_key": citation_key(
                {**metadata, "bibcode": document["bibcode"]},
                self.policy,
                own_key=_own_key(metadata),
            ),
            "entry_type": parts[0].lower(),
            "fields": parts[2],
            "ads_entry": saved[0],
            "source": "ads",
            "preambles": [],
        }

    def ads_bibtex_requests(self, document_ids: Sequence[int]) -> dict[str, str]:
        """Return ``{bibcode: fingerprint}`` for papers without saved ADS BibTeX.

        A paper is listed when no ADS result is saved for its current ADS
        metadata. A saved "ADS has no entry" result is not listed again.
        """
        wanted = set(document_ids)
        saved_entries = getattr(self.catalog, "ads_bibtex_entries", dict)()
        requests: dict[str, str] = {}
        for document in self.catalog.list_documents():
            if document["id"] not in wanted or not _uses_ads_bibtex(document):
                continue
            fingerprint = ads_bibtex_fingerprint(document["metadata"])
            saved = saved_entries.get(document["bibcode"])
            if saved is None or saved[1] != fingerprint:
                requests[document["bibcode"]] = fingerprint
        return requests

    def store_ads_bibtex(
        self, entries: Mapping[str, str], requests: Mapping[str, str]
    ) -> int:
        """Save fetched ADS BibTeX for the requested papers.

        Parameters
        ----------
        entries
            Entry text by Bibcode, from ``ExportAPI.export_entries()``.
        requests
            The ``{bibcode: fingerprint}`` map from `ads_bibtex_requests`. A
            requested Bibcode without an entry is saved as "ADS has no entry".

        Returns
        -------
        int
            The number of saved entries.
        """
        self.catalog.store_ads_bibtex(
            {
                bibcode: (entries.get(bibcode), fingerprint)
                for bibcode, fingerprint in requests.items()
            }
        )
        return sum(1 for bibcode in requests if entries.get(bibcode))

    def list(self) -> list[dict[str, Any]]:
        """Return references and their known PDF locations, including missing files."""
        attachments: dict[int, list[dict[str, Any]]] = {}
        for record in self.catalog.query_files(include_missing=True):
            attachments.setdefault(record["document_id"], []).append(record)
        return [
            {
                **_reference_from_document(document, self.policy),
                "attachments": attachments.get(document["id"], []),
            }
            for document in self.catalog.list_documents()
        ]

    def get(self, document_id: int) -> dict[str, Any]:
        """Return one reference or raise `ValueError` for an unknown ID."""
        document = self.catalog.get_document(document_id)
        if document is None:
            raise ValueError(f"Unknown reference ID: {document_id}")
        return {
            **_reference_from_document(document, self.policy),
            "attachments": self.catalog.query_files(
                document_id=document_id, include_missing=True
            ),
        }

    def import_bibtex(
        self, text: str, *, source: str = "bibtex"
    ) -> list[dict[str, Any]]:
        """Import all entries atomically; duplicate keys or DOIs leave no changes."""
        references = parse_bibtex(text)
        for reference in references:
            reference["source"] = source
        ids = self.catalog.save_reference_documents(
            [{"metadata": _document_metadata(reference)} for reference in references],
            source_text=text,
        )
        return [self.get(document_id) for document_id in ids]

    def original_bibtex(self, document_id: int) -> str | None:
        """Read the shared import; manually added references return `None`."""
        source_id = self.get(document_id).get("source_id")
        if source_id is None:
            return None
        text = self.catalog.get_meta(f"reference_source:{source_id}")
        if text is None:
            raise ValueError(
                f"Original BibTeX source is missing for reference {document_id}"
            )
        return text

    def add(
        self,
        *,
        cite_key: str,
        fields: Mapping[str, str],
        entry_type: str = "misc",
        source: str = "manual",
        bibcode: str | None = None,
    ) -> dict[str, Any]:
        """Add a reference without requiring a PDF or ADS bibcode.

        Empty `fields` save a draft. Export requires at least one field.
        """
        reference = _validated_entry(cite_key, entry_type, fields)
        reference.update(source=source, bibcode=bibcode or None, preambles=[])
        ids = self.catalog.save_reference_documents(
            [{"bibcode": bibcode or None, "metadata": _document_metadata(reference)}]
        )
        return self.get(ids[0])

    def update(
        self,
        document_id: int,
        *,
        cite_key: str | None = None,
        entry_type: str | None = None,
        fields: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Edit one reference; `fields` replaces its complete editable field mapping.

        Changing a key used by another reference's `crossref` is rejected.
        Update those links first to avoid creating dangling references.
        """
        document = self.catalog.get_document(document_id)
        if document is None:
            raise ValueError(f"Unknown reference ID: {document_id}")
        reference = _reference_from_document(document, self.policy)
        validated = _validated_entry(
            reference["cite_key"] if cite_key is None else cite_key,
            reference["entry_type"] if entry_type is None else entry_type,
            reference["fields"] if fields is None else fields,
        )
        if validated["cite_key"] != reference["cite_key"]:
            for other in self.list():
                if other["fields"].get("crossref") == reference["cite_key"]:
                    raise ValueError(
                        f"Citation key is used by crossref in {other['cite_key']}"
                    )
        reference.update(validated)
        self.catalog.save_reference_documents(
            [
                {
                    "id": document_id,
                    "bibcode": reference["bibcode"],
                    "metadata": _document_metadata(reference, document["metadata"]),
                }
            ]
        )
        return self.get(document_id)

    def set_cite_key(self, document_id: int, key: str) -> None:
        """Save the citation key that the user typed for one reference.

        A local reference stores the key in its BibTeX entry. Another paper
        stores it as its own key, which wins over the key style. An empty key
        removes the own key of a paper that is not a local reference.

        Raises
        ------
        ValueError
            If the reference is unknown, or the key holds characters that
            BibTeX rejects.
        """
        document = self.catalog.get_document(document_id)
        if document is None:
            raise ValueError(f"Unknown reference ID: {document_id}")
        key = key.strip()
        if REFERENCE_METADATA_KEY in document["metadata"]:
            self.update(document_id, cite_key=key)
            return
        if key:
            _validated_entry(key, "misc", {})
        self.catalog.set_document_cite_key(document_id, key)

    def export_bibtex(
        self, document_ids: Sequence[int], *, exclude_fields: Collection[str] = ()
    ) -> str:
        """Export selected records offline, including required crossref parents.

        `exclude_fields` names fields to leave out of every entry, ignoring
        letter case, for example ``("abstract",)``.

        Unknown IDs, empty drafts, absent parents, and cycles fail before any
        text is returned. Imports retain original text; output preserves field
        semantics, not original whitespace or macro spelling.
        """
        references = self._export_references()
        by_id = {item["id"]: item for item in references}
        by_key: dict[str, list[dict[str, Any]]] = {}
        for item in references:
            by_key.setdefault(item["cite_key"], []).append(item)
        selected: dict[int, dict[str, Any]] = {}
        visiting: set[int] = set()

        def include(document_id: int) -> None:
            if document_id in visiting:
                raise ValueError(f"Cyclic crossref involving reference {document_id}")
            if document_id in selected:
                return
            if document_id not in by_id:
                raise ValueError(f"Unknown reference ID: {document_id}")
            reference = by_id[document_id]
            selected[document_id] = reference
            visiting.add(document_id)
            parent = reference["fields"].get("crossref")
            if parent:
                if parent not in by_key:
                    raise ValueError(
                        f"Missing crossref {parent!r} for {reference['cite_key']}"
                    )
                if len(by_key[parent]) != 1:
                    raise ValueError(f"Ambiguous crossref key: {parent}")
                include(by_key[parent][0]["id"])
            visiting.remove(document_id)

        for document_id in document_ids:
            include(document_id)
        ordered = list(selected.values())
        assignment = assign_keys(
            ((item["id"], {}, item["cite_key"]) for item in ordered), self.policy
        )
        if not assignment.complete:
            raise CitationKeyError(assignment, selected)
        positions = {item["cite_key"]: index for index, item in enumerate(ordered)}
        pending_children = [0] * len(ordered)
        for item in ordered:
            parent = item["fields"].get("crossref")
            if parent:
                pending_children[positions[parent]] += 1
        ready = [index for index, count in enumerate(pending_children) if count == 0]
        heapq.heapify(ready)
        database = BibDatabase()
        excluded = {name.lower() for name in exclude_fields}
        validated: list[dict[str, Any]] = []
        # Output in order: an index into `database.entries`, or ADS's own text.
        chunks: list[int | str] = []
        # Stable topological ordering keeps unrelated selections in input order.
        while ready:
            reference = ordered[heapq.heappop(ready)]
            parent = reference["fields"].get("crossref")
            if parent:
                parent_index = positions[parent]
                pending_children[parent_index] -= 1
                if pending_children[parent_index] == 0:
                    heapq.heappush(ready, parent_index)
            fields = {
                name: value
                for name, value in reference["fields"].items()
                if name.lower() not in excluded
            }
            if not fields:
                raise ValueError(
                    f"Reference {reference['cite_key']} has no bibliography fields; edit it before export"
                )
            if reference.get("ads_entry"):
                # ADS's own text, with this export's key and field choices.
                chunks.append(
                    remove_fields(
                        with_cite_key(reference["ads_entry"], reference["cite_key"]),
                        excluded,
                    ).strip()
                    + "\n"
                )
                continue
            entry = _validated_entry(
                reference["cite_key"],
                reference["entry_type"],
                fields,
                round_trip=False,
            )
            validated.append({**reference, "fields": fields})
            database.entries.append(
                {
                    "ID": entry["cite_key"],
                    "ENTRYTYPE": entry["entry_type"],
                    **entry["fields"],
                }
            )
            chunks.append(len(database.entries) - 1)
            for preamble in reference.get("preambles", []):
                if preamble not in database.preambles:
                    database.preambles.append(preamble)
        _check_round_trip(database.entries, validated)
        writer = BibTexWriter()
        writer.order_entries_by = None
        if all(isinstance(chunk, int) for chunk in chunks):
            return writer.write(database)
        # Mixed output keeps the selection order and the writer's layout:
        # preambles first, then entries separated by one blank line.
        preambles = BibDatabase()
        preambles.preambles = list(database.preambles)
        texts = []
        for chunk in chunks:
            if isinstance(chunk, int):
                single = BibDatabase()
                single.entries = [database.entries[chunk]]
                texts.append(writer.write(single))
            else:
                texts.append(chunk)
        return writer.write(preambles) + writer.entry_separator.join(texts)

    def check_citations(self, cited_keys: Iterable[str]) -> dict[str, list[str]]:
        """Find cited keys that are not current keys, and their current keys.

        A key that equals the key of a reference is current. For another key,
        each 19-character part is compared with the bibcode and the alternate
        bibcodes of each reference. An ADS bibcode has 19 characters. A
        preprint bibcode becomes an alternate bibcode after publication.

        Parameters
        ----------
        cited_keys
            Keys that a paper cites, for example from its ``.aux`` file.

        Returns
        -------
        dict
            One item for each cited key that is not current, in input order.
            The value lists the current keys of the matched references. An
            empty list means that no reference matched.
        """
        references = self._export_references()
        current = {item["cite_key"] for item in references if item["cite_key"]}
        alternates = {
            document["id"]: document["metadata"].get("alternate_bibcode") or []
            for document in self.catalog.list_documents()
        }
        by_bibcode: dict[str, set[str]] = {}
        for item in references:
            if not item["cite_key"]:
                continue
            codes = alternates.get(item["id"], [])
            codes = [codes] if isinstance(codes, str) else list(codes)
            for code in [item.get("bibcode") or "", *codes]:
                if len(code) == BIBCODE_LENGTH:
                    by_bibcode.setdefault(code, set()).add(item["cite_key"])
        result: dict[str, list[str]] = {}
        for key in dict.fromkeys(cited_keys):
            if key in current:
                continue
            matches: set[str] = set()
            for start in range(len(key) - BIBCODE_LENGTH + 1):
                matches |= by_bibcode.get(key[start : start + BIBCODE_LENGTH], set())
            result[key] = sorted(matches)
        return result

    def attach_pdf(self, document_id: int, path: Path) -> dict[str, Any]:
        """Link a PDF at its current path without copying or renaming it."""
        self.get(document_id)
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError(f"Choose an existing PDF file: {path}")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise ValueError(f"File does not start with a PDF header: {path}")
            stream.seek(0)
            for chunk in iter(lambda: stream.read(io.DEFAULT_BUFFER_SIZE), b""):
                digest.update(chunk)
        stat = path.stat()
        return self.catalog.attach_document_file(
            document_id,
            path,
            sha256=digest.hexdigest(),
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            device=stat.st_dev,
            inode=stat.st_ino,
        )
