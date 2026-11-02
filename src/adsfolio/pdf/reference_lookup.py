"""Match bibliographic reference hints against the current PDF's identity."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..api.search import escape_query_phrase
from ..config import ADSError
from .doi_providers import DoiProvider, ProviderError
from .identifier import AdsQueryResult, PdfIdentifier, fetch_external_identifier
from .identity import IdentityValidationError, extract_pdf_own_identity, target_identity

__all__ = ["ReferenceLookupResult", "lookup_exact_identifier", "match_reference_to_pdf"]


@dataclass(frozen=True)
class ReferenceLookupResult:
    """Keep verified reference candidates separate from rejected search hints."""

    matches: AdsQueryResult
    rejected: tuple[str, ...] = ()
    used_own_identifiers: bool = False


def lookup_exact_identifier(
    identifier: PdfIdentifier,
    kind: str,
    value: str,
    providers: Sequence[DoiProvider],
    cache_documents: Callable[[list[dict[str, Any]]], None] | None = None,
    path: Path | None = None,
) -> AdsQueryResult:
    """Find exact DOI or arXiv metadata in the selected provider order.

    Parameters
    ----------
    identifier : PdfIdentifier
        Resolver with an optional ADS client.
    kind : str
        Identifier type, either ``doi`` or ``arxiv``.
    value : str
        Exact DOI or version-free arXiv identifier.
    providers : sequence of DoiProvider
        Enabled providers in priority order.
    cache_documents : callable, optional
        Callback for independently cached ADS documents.
    path : Path, optional
        Current PDF whose identifiers a successful provider must prove.

    Returns
    -------
    AdsQueryResult
        Candidates from the first suitable provider. Multiple verified
        candidates retain their ambiguity. If all candidates fail the PDF
        check, return one for the caller's explicit identity refusal.

    Raises
    ------
    ADSError or ProviderError
        A lookup failed and no later provider supplied metadata.
    ValueError
        The identifier type or value is invalid.
    """
    if kind not in {"doi", "arxiv"}:
        raise ValueError(f"Unsupported identifier type: {kind}")
    evidence = extract_pdf_own_identity(path) if path is not None else None
    first_rejected = None
    errors = []
    for provider in providers:
        try:
            if provider is DoiProvider.ADS:
                if identifier.client is None:
                    continue
                query = (
                    f'doi:"{escape_query_phrase(value)}"'
                    if kind == "doi"
                    else f'identifier:"arXiv:{escape_query_phrase(value)}"'
                )
                result = identifier.query_documents(query, cache_documents)
                if result.num_found != len(result.documents):
                    raise ADSError(
                        "ADS returned incomplete identifier results. Retry the lookup."
                    )
                documents = tuple(
                    document
                    for document in result.documents
                    if value.casefold()
                    in (
                        target_identity(document).dois
                        if kind == "doi"
                        else target_identity(document).arxiv_ids
                    )
                )
                if result.num_found and not documents:
                    raise ADSError(
                        "ADS results do not contain the requested identifier."
                    )
            else:
                document = fetch_external_identifier(kind, value, (provider,))
                documents = (document,) if document is not None else ()
            if documents:
                if evidence is not None and evidence.has_identifier:
                    verified = []
                    for document in documents:
                        try:
                            identifier.build_record(
                                document, path, sha256=evidence.sha256
                            )
                        except IdentityValidationError as exc:
                            if first_rejected is None:
                                first_rejected = document
                            identifier.logger.warning(
                                "Rejected %s candidate: %s", provider.value, exc
                            )
                        else:
                            verified.append(document)
                    if not verified:
                        continue
                    documents = tuple(verified)
                return AdsQueryResult(len(documents), documents)
        except (ADSError, ProviderError, ValueError) as exc:
            errors.append(exc)
            identifier.logger.warning(
                "%s lookup failed; trying the next provider: %s", provider.value, exc
            )
    if first_rejected is not None:
        return AdsQueryResult(1, (first_rejected,))
    if errors:
        raise errors[-1]
    return AdsQueryResult(0, ())


def match_reference_to_pdf(
    identifier: PdfIdentifier,
    path: Path,
    result: AdsQueryResult,
    providers: Sequence[DoiProvider],
    cache_documents: Callable[[list[dict[str, Any]]], None] | None = None,
) -> ReferenceLookupResult:
    """Verify reference candidates and recover through the PDF's own identifiers.

    Parameters
    ----------
    identifier : PdfIdentifier
        Resolver with an optional ADS client.
    path : Path
        Current PDF to inspect without changing its contents.
    result : AdsQueryResult
        Initial ADS result for a bibliographic reference hint.
    providers : sequence of DoiProvider
        Enabled providers in priority order for recovery.
    cache_documents : callable, optional
        Callback for independently cached ADS documents.

    Returns
    -------
    ReferenceLookupResult
        Matching candidates, rejected hints, and the recovery state.

    Notes
    -----
    A reference is a search hint, so a coauthor match cannot establish identity.
    Explicit DOI and bibcode updates must not call this recovery operation.
    Without trusted PDF identifiers, retain the original candidates for review.
    A partial result cannot establish a unique automatic match.
    """
    evidence = extract_pdf_own_identity(path)
    if not evidence.has_identifier:
        return ReferenceLookupResult(result)
    rejected: list[str] = []

    def verified(documents: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
        matches = {}
        for document in documents:
            try:
                identifier.build_record(document, path, sha256=evidence.sha256)
            except IdentityValidationError as exc:
                rejected.append(str(exc))
                continue
            matches[target_identity(document).fingerprint] = document
        return tuple(matches.values())

    candidates = verified(result.documents)
    if candidates and result.num_found == len(result.documents):
        return ReferenceLookupResult(
            AdsQueryResult(len(candidates), candidates), tuple(rejected)
        )

    lookup_errors = []
    for provider in providers:
        documents = []
        for kind, value in (
            *[("doi", value) for value in sorted(evidence.dois)],
            *[("arxiv", value) for value in sorted(evidence.arxiv_ids)],
        ):
            try:
                if provider is DoiProvider.ADS:
                    if identifier.client is None:
                        continue
                    lookup = (
                        identifier.query_docs_for_dois
                        if kind == "doi"
                        else identifier.query_docs_for_arxiv_ids
                    )
                    document = lookup(
                        [value], ads_documents_callback=cache_documents
                    ).get(value)
                else:
                    document = fetch_external_identifier(kind, value, (provider,))
            except (ADSError, ProviderError, ValueError) as exc:
                lookup_errors.append(exc)
                continue
            if document is not None:
                documents.append(document)
        matches = verified(documents)
        if matches:
            return ReferenceLookupResult(
                AdsQueryResult(len(matches), matches), tuple(rejected), True
            )
    if lookup_errors:
        raise lookup_errors[-1]
    return ReferenceLookupResult(AdsQueryResult(0, ()), tuple(rejected), True)
