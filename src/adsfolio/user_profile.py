"""Global publication-profile settings for the current user."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .pdf.prefetch import PublicationProfile

logger = logging.getLogger(__name__)


def _text(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    return value.strip()


def _strings(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return fallback
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _library_ids(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize canonical lists and delimiter-separated legacy IDs."""
    if isinstance(value, str):
        raw_values = [value]
    elif isinstance(value, (list, tuple)):
        raw_values = value
    else:
        return fallback

    normalized: list[str] = []
    for item in raw_values:
        if not isinstance(item, str):
            continue
        for raw_id in item.replace(";", ",").split(","):
            library_id = raw_id.strip()
            if library_id and library_id not in normalized:
                normalized.append(library_id)
    return tuple(normalized)


def _year(value: Any, fallback: int | None, field: str) -> int | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, bool):
        logger.warning("Ignoring malformed %s: %r", field, value)
        return fallback
    try:
        year = int(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed %s: %r", field, value)
        return fallback
    if 1 <= year <= 9999:
        return year
    logger.warning("Ignoring out-of-range %s: %r", field, value)
    return fallback


@dataclass(frozen=True)
class UserProfile:
    """Identity used to prefetch the current user's ADS publications."""

    orcid: str = ""
    author_names: tuple[str, ...] = ()
    first_publication_year: int | None = None
    last_publication_year: int | None = None
    ads_library_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate relationships between normalized fields."""
        for field, value in (
            ("first publication year", self.first_publication_year),
            ("last publication year", self.last_publication_year),
        ):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field} must be an integer or `None`")
            if not 1 <= value <= 9999:
                raise ValueError(f"{field} must be between 1 and 9999")
        if (
            self.first_publication_year is not None
            and self.last_publication_year is not None
            and self.first_publication_year > self.last_publication_year
        ):
            raise ValueError(
                "first publication year cannot be greater than last publication year"
            )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback: UserProfile | None = None,
    ) -> UserProfile:
        """Normalize canonical or legacy profile fields from a mapping."""
        base = fallback or cls()
        if not isinstance(payload, Mapping):
            return base

        first_year = payload.get(
            "first_publication_year",
            payload.get("debut_year", base.first_publication_year),
        )
        last_year = payload.get(
            "last_publication_year",
            payload.get(
                "end_year",
                payload.get("retire_year", base.last_publication_year),
            ),
        )
        values = {
            "orcid": _text(payload.get("orcid", base.orcid), base.orcid),
            "author_names": _strings(
                payload.get(
                    "author_names",
                    payload.get("query_names", base.author_names),
                ),
                base.author_names,
            ),
            "first_publication_year": _year(
                first_year,
                base.first_publication_year,
                "first_publication_year",
            ),
            "last_publication_year": _year(
                last_year,
                base.last_publication_year,
                "last_publication_year",
            ),
            "ads_library_ids": _library_ids(
                payload.get(
                    "ads_library_ids",
                    payload.get("library_ids", base.ads_library_ids),
                ),
                base.ads_library_ids,
            ),
        }
        if (
            values["first_publication_year"] is not None
            and values["last_publication_year"] is not None
            and values["first_publication_year"] > values["last_publication_year"]
        ):
            logger.warning("Ignoring reversed publication-profile year window")
            values["first_publication_year"] = base.first_publication_year
            values["last_publication_year"] = base.last_publication_year
        return cls(**values)

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "orcid": self.orcid,
            "author_names": list(self.author_names),
            "first_publication_year": self.first_publication_year,
            "last_publication_year": self.last_publication_year,
            "ads_library_ids": list(self.ads_library_ids),
        }

    def publication_profile(self) -> PublicationProfile:
        """Return the PDF-prefetch representation of this profile."""
        from .pdf.prefetch import PublicationProfile

        return PublicationProfile(
            orcid=self.orcid,
            query_names=list(self.author_names),
            debut_year=self.first_publication_year,
            end_year=self.last_publication_year,
            library_ids=list(self.ads_library_ids),
        )


__all__ = ["UserProfile"]
