"""Canonical settings shared by PDF Identify interfaces."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

_UNSET = object()


class IdentifyStrategy(str, Enum):
    """Supported evidence extraction strategies for PDF identification."""

    FILENAME_BIBCODE = "filename_bibcode"
    FILENAME_DOI = "filename_doi"
    PDF_METADATA_BIBCODE = "pdf_metadata_bibcode"
    PDF_METADATA_DOI = "pdf_metadata_doi"
    FIRST_PAGE_BIBCODE = "first_page_bibcode"
    FIRST_PAGE_DOI = "first_page_doi"
    STRUCTURED_FILENAME = "structured_filename"
    PDF_METADATA_TITLE = "pdf_metadata_title"
    FIRST_PAGE_TITLE = "first_page_title"


RECOMMENDED_IDENTIFY_STRATEGIES: tuple[IdentifyStrategy, ...] = tuple(IdentifyStrategy)

IDENTIFY_STRATEGY_LABELS: Mapping[IdentifyStrategy, str] = {
    IdentifyStrategy.FILENAME_BIBCODE: "Bibcode in filename",
    IdentifyStrategy.FILENAME_DOI: "DOI in filename",
    IdentifyStrategy.PDF_METADATA_BIBCODE: "Bibcode in PDF metadata",
    IdentifyStrategy.PDF_METADATA_DOI: "DOI in PDF metadata",
    IdentifyStrategy.FIRST_PAGE_BIBCODE: "Bibcode on first page",
    IdentifyStrategy.FIRST_PAGE_DOI: "DOI on first page",
    IdentifyStrategy.STRUCTURED_FILENAME: "Structured bibliographic filename",
    IdentifyStrategy.PDF_METADATA_TITLE: "Title in PDF metadata",
    IdentifyStrategy.FIRST_PAGE_TITLE: "Title on first page",
}


def _optional_text(value: Any, fallback: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return fallback
    return value.strip() or None


def _boolean(value: Any, fallback: bool, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is not None:
        logger.warning("Ignoring malformed %s: %r", field, value)
    return fallback


def _normalize_strategies(
    value: Any,
) -> tuple[IdentifyStrategy, ...]:
    if value is None:
        return RECOMMENDED_IDENTIFY_STRATEGIES
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        logger.warning("Ignoring malformed strategies: %r", value)
        return RECOMMENDED_IDENTIFY_STRATEGIES
    normalized: list[IdentifyStrategy] = []
    seen: set[IdentifyStrategy] = set()
    for item in value:
        try:
            strategy = (
                item
                if isinstance(item, IdentifyStrategy)
                else IdentifyStrategy(str(item))
            )
        except (ValueError, TypeError):
            logger.warning("Ignoring unknown identification strategy: %r", item)
            continue
        if strategy in seen:
            logger.warning(
                "Ignoring duplicate identification strategy: %r", strategy.value
            )
            continue
        seen.add(strategy)
        normalized.append(strategy)
    if not normalized:
        logger.warning(
            "No valid strategies found in %r; using recommended order", value
        )
        return RECOMMENDED_IDENTIFY_STRATEGIES
    return tuple(normalized)


@dataclass(frozen=True)
class IdentifySettings:
    """Reusable PDF Identify behavior, separate from the user's identity.

    Notes
    -----
    `recursive` and `verbose` configure foreground/bulk Identify runs. Watch
    processing handles one typed arrival at a time and reports its outcome via
    GUI notices, so those two fields do not alter single-file watch behavior.
    """

    strategies: tuple[IdentifyStrategy, ...] = RECOMMENDED_IDENTIFY_STRATEGIES
    recursive: bool = False
    output_unresolved: str | None = None
    skip_unresolved_directory: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.strategies, Iterable) or isinstance(
            self.strategies, (str, bytes, Mapping)
        ):
            raise ValueError(
                f"strategies must be an iterable of IdentifyStrategy, got {self.strategies!r}"
            )
        validated: list[IdentifyStrategy] = []
        seen: set[IdentifyStrategy] = set()
        for item in self.strategies:
            try:
                strategy = (
                    item
                    if isinstance(item, IdentifyStrategy)
                    else IdentifyStrategy(str(item))
                )
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Unknown identification strategy: {item!r}") from exc
            if strategy in seen:
                raise ValueError(
                    f"duplicate identification strategy: {strategy.value!r}"
                )
            seen.add(strategy)
            validated.append(strategy)
        if not validated:
            raise ValueError("At least one identification strategy is required")
        object.__setattr__(self, "strategies", tuple(validated))

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback: IdentifySettings | None = None,
    ) -> IdentifySettings:
        """Normalize persisted values independently.

        Parameters
        ----------
        payload
            Persisted settings. Unknown fields are ignored.
        fallback
            Values used for missing or malformed fields.

        Returns
        -------
        IdentifySettings
            Normalized settings.
        """
        base = fallback or cls()
        if not isinstance(payload, Mapping):
            return base
        if "strategies" in payload:
            strategies = _normalize_strategies(payload["strategies"])
        elif "infer_from_filenames" in payload:
            strategies = RECOMMENDED_IDENTIFY_STRATEGIES
        else:
            strategies = base.strategies
        return cls(
            strategies=strategies,
            recursive=_boolean(
                payload.get("recursive", base.recursive),
                base.recursive,
                "recursive",
            ),
            output_unresolved=_optional_text(
                payload.get("output_unresolved", base.output_unresolved),
                base.output_unresolved,
            ),
            skip_unresolved_directory=_boolean(
                payload.get(
                    "skip_unresolved_directory",
                    base.skip_unresolved_directory,
                ),
                base.skip_unresolved_directory,
                "skip_unresolved_directory",
            ),
            verbose=_boolean(
                payload.get("verbose", base.verbose),
                base.verbose,
                "verbose",
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "strategies": [strategy.value for strategy in self.strategies],
            "recursive": self.recursive,
            "output_unresolved": self.output_unresolved,
            "skip_unresolved_directory": self.skip_unresolved_directory,
            "verbose": self.verbose,
        }

    def with_overrides(
        self,
        *,
        strategies: Sequence[IdentifyStrategy | str] | object = _UNSET,
        recursive: bool | object = _UNSET,
        output_unresolved: str | None | object = _UNSET,
        skip_unresolved_directory: bool | object = _UNSET,
        verbose: bool | object = _UNSET,
    ) -> IdentifySettings:
        """Return a copy with supplied fields, including explicit clears."""
        changes: dict[str, Any] = {}
        if strategies is not _UNSET:
            changes["strategies"] = (
                tuple(strategies)
                if strategies is not None
                else RECOMMENDED_IDENTIFY_STRATEGIES
            )
        if recursive is not _UNSET:
            changes["recursive"] = recursive
        if output_unresolved is not _UNSET:
            changes["output_unresolved"] = output_unresolved
        if skip_unresolved_directory is not _UNSET:
            changes["skip_unresolved_directory"] = skip_unresolved_directory
        if verbose is not _UNSET:
            changes["verbose"] = verbose
        return replace(self, **changes)


__all__ = [
    "IDENTIFY_STRATEGY_LABELS",
    "IdentifySettings",
    "IdentifyStrategy",
    "RECOMMENDED_IDENTIFY_STRATEGIES",
]
