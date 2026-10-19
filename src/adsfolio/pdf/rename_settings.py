"""Canonical settings shared by PDF Rename interfaces."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .renamer import DEFAULT_NONBIBCODE_SCHEME, DEFAULT_SCHEME

logger = logging.getLogger(__name__)

_ALLOWED_AUTHOR_FORMATS = {"surname", "surname_initials", "last_comma_first"}
_ALLOWED_CHAR_POLICIES = {"ascii", "keep"}
_MAX_AUTHORS = 99


def _normalize_template(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value is not None:
        logger.warning("Ignoring malformed template: %r", value)
    return fallback


def _normalize_author_format(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned in _ALLOWED_AUTHOR_FORMATS or (
            cleaned.startswith("ads_custom:") and bool(cleaned.split(":", 1)[1].strip())
        ):
            return cleaned
    if value is not None:
        logger.warning("Ignoring malformed author_format: %r", value)
    return fallback


def _normalize_max_authors(value: Any, fallback: int) -> int:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= _MAX_AUTHORS
    ):
        return value
    if value is not None:
        logger.warning("Ignoring malformed max_authors: %r", value)
    return fallback


def _normalize_authors_when_truncated(
    payload: Mapping[str, Any],
    fallback: int,
    max_authors: int,
) -> int:
    if "authors_when_truncated" not in payload:
        return min(fallback, max_authors)
    value = payload.get("authors_when_truncated")
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 1 <= value <= max_authors
    ):
        return value
    if value is not None:
        logger.warning("Ignoring malformed authors_when_truncated: %r", value)
    return min(fallback, max_authors)


def _normalize_char_policy(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value in _ALLOWED_CHAR_POLICIES:
        return value
    if value is not None:
        logger.warning("Ignoring malformed char_policy: %r", value)
    return fallback


def _normalize_author_join_text(value: Any, fallback: str, field: str) -> str:
    if isinstance(value, str):
        return value
    if value is not None:
        logger.warning("Ignoring malformed %s: %r", field, value)
    return fallback


@dataclass(frozen=True)
class RenameSettings:
    """Reusable PDF Rename behavior."""

    template: str = DEFAULT_SCHEME
    author_format: str = "surname_initials"
    max_authors: int = 3
    authors_when_truncated: int = 1
    char_policy: str = "keep"
    author_separator: str = "+"
    author_truncation_suffix: str = "+"

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_authors, bool)
            or not isinstance(self.max_authors, int)
            or not 1 <= self.max_authors <= _MAX_AUTHORS
        ):
            raise ValueError("max_authors must be an integer from 1 through 99")
        if (
            isinstance(self.authors_when_truncated, bool)
            or not isinstance(self.authors_when_truncated, int)
            or not 1 <= self.authors_when_truncated <= self.max_authors
        ):
            raise ValueError(
                "authors_when_truncated must be an integer from 1 through "
                f"max_authors ({self.max_authors})"
            )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback: RenameSettings | None = None,
    ) -> RenameSettings:
        """Normalize persisted values independently.

        Parameters
        ----------
        payload
            Persisted settings. Unknown fields are ignored.
        fallback
            Values used for missing or malformed fields.

        Returns
        -------
        RenameSettings
            Normalized settings.
        """
        base = fallback or cls()
        if not isinstance(payload, Mapping):
            return base
        max_authors = _normalize_max_authors(
            payload.get("max_authors", base.max_authors),
            base.max_authors,
        )
        return cls(
            template=_normalize_template(
                payload.get("template", base.template),
                base.template,
            ),
            author_format=_normalize_author_format(
                payload.get("author_format", base.author_format),
                base.author_format,
            ),
            max_authors=max_authors,
            authors_when_truncated=_normalize_authors_when_truncated(
                payload,
                base.authors_when_truncated,
                max_authors,
            ),
            char_policy=_normalize_char_policy(
                payload.get("char_policy", base.char_policy),
                base.char_policy,
            ),
            author_separator=_normalize_author_join_text(
                payload.get("author_separator", base.author_separator),
                base.author_separator,
                "author_separator",
            ),
            author_truncation_suffix=_normalize_author_join_text(
                payload.get("author_truncation_suffix", base.author_truncation_suffix),
                base.author_truncation_suffix,
                "author_truncation_suffix",
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "template": self.template,
            "author_format": self.author_format,
            "max_authors": self.max_authors,
            "authors_when_truncated": self.authors_when_truncated,
            "char_policy": self.char_policy,
            "author_separator": self.author_separator,
            "author_truncation_suffix": self.author_truncation_suffix,
        }


__all__ = ["DEFAULT_NONBIBCODE_SCHEME", "RenameSettings"]
