"""
Configuration and token management for NASA ADS.

Token is searched in order:
1. Environment variable ADS_API_TOKEN
2. ~/.ads/token
3. .ads-token (current directory)
4. Interactive prompt (saved to ~/.ads/token)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import platformdirs

from .data_migration import copy_data_directory

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .citekeys import KeyPolicy
    from .pdf.identify_settings import IdentifySettings
    from .pdf.rename_settings import RenameSettings
    from .user_profile import UserProfile

__all__ = [
    "ADSError",
    "AuthenticationError",
    "Config",
    "RateLimitError",
    "RequestTooLongError",
]


def make_private_directory(path: Path) -> None:
    """Create `path` if needed and make it accessible only to its owner.

    POSIX permission bits do not apply on Windows, where the directory is only
    created.
    """
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and path.stat().st_mode & 0o077:
        path.chmod(0o700)


def write_private_text(path: Path, text: str) -> None:
    """Atomically replace `path` with `text` that only its owner can read.

    The temporary file is created with mode ``0o600``, so the text is never
    readable by other users, even before the final rename.
    """
    make_private_directory(path.parent)
    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # A stale temporary file keeps its old mode when it is reopened.
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


class ADSError(Exception):
    """Base exception for NASA ADS errors."""


class RateLimitError(ADSError):
    """Raised when rate limit is exceeded."""


class AuthenticationError(ADSError):
    """Raised when authentication fails."""


class RequestTooLongError(ADSError):
    """Indicate that ADS rejected an oversized request URL."""


class Config:
    """Configuration management for NASA ADS."""

    API_BASE = "https://api.adsabs.harvard.edu/v1"
    ENV_VAR = "ADS_API_TOKEN"
    SETUP_FILENAME = "setup.json"
    SETUP_SCHEMA_VERSION = 2
    _PROFILE_FLAT_KEYS: ClassVar[set[str]] = {
        "orcid",
        "query_names",
        "author_names",
        "debut_year",
        "first_publication_year",
        "end_year",
        "retire_year",
        "last_publication_year",
        "library_ids",
        "ads_library_ids",
    }
    _IDENTIFY_FLAT_KEYS: ClassVar[set[str]] = {
        "recursive",
        "strategies",
        "output_unresolved",
        "skip_unresolved_directory",
        "verbose",
    }
    _RENAME_FLAT_KEYS: ClassVar[set[str]] = {
        "template",
        "author_format",
        "max_authors",
        "authors_when_truncated",
        "char_policy",
        "author_separator",
        "author_truncation_suffix",
    }

    # Default fields to return in searches
    DEFAULT_FIELDS: ClassVar[list[str]] = [
        "bibcode",
        "title",
        "author",
        "year",
        "pub",
        "citation_count",
    ]

    # Rate limit tracking (updated from response headers)
    _rate_limit = None
    _rate_remaining = None
    _rate_reset = None

    @classmethod
    def get_config_dir(cls) -> Path:
        """Get platform-appropriate config directory."""
        destination = Path(platformdirs.user_config_dir("adsfolio", appauthor=False))
        source = Path(platformdirs.user_config_dir("ads", appauthor=False))
        copy_data_directory(source, destination)
        return destination

    @classmethod
    def get_token_paths(cls) -> list[Path]:
        """Return list of paths to check for token, in order."""
        return [
            cls.get_config_dir() / "token",
            Path.home() / ".config" / "ads" / "token",  # Standard config location
            Path.home() / ".ads" / "token",
            Path.home() / ".ads" / "dev_key",  # Official client legacy path
            Path(".ads-token"),
        ]

    @classmethod
    def get_token(cls, *, prompt: bool = True) -> str:
        """
        Get NASA ADS token from various sources.

        Order of precedence:
        1. Environment variable ADS_API_TOKEN
        2. Config file (~/.config/ads/token or ~/.ads/token)
        3. Local .ads-token file
        4. Interactive prompt (saves to config)

        Parameters
        ----------
        prompt : `bool`, optional
            Allow prompting when standard input is an interactive terminal.
            Set to `False` for GUI token lookup.

        Returns
        -------
        str
            The API token.

        Raises
        ------
        AuthenticationError
            If no token can be found or created.
        """
        found = cls._find_token()
        if found is not None:
            return found[0]

        if not prompt or sys.stdin is None or not sys.stdin.isatty():
            raise AuthenticationError(
                "No NASA ADS token found. In the desktop app, use "
                "Tools → NASA ADS Token…; in a terminal, run adsfolio set."
            )
        return cls._prompt_for_token()

    @classmethod
    def _find_token(cls) -> tuple[str, str] | None:
        """Return the first nonempty token and a description of its source."""
        token = os.environ.get(cls.ENV_VAR)
        if token and token.strip():
            return token.strip(), f"the {cls.ENV_VAR} environment variable"
        for path in cls.get_token_paths():
            if path.exists():
                token = path.read_text().strip()
                if token:
                    return token, str(path.absolute())
        return None

    @classmethod
    def token_source(cls) -> str | None:
        """Describe where the active token comes from, or `None` without one.

        Returns
        -------
        str or `None`
            The environment variable name or the absolute token file path.
        """
        found = cls._find_token()
        return None if found is None else found[1]

    @classmethod
    def save_token(cls, token: str) -> Path:
        """
        Save token to config directory.

        Parameters
        ----------
        token : str
            Token string to save.

        Returns
        -------
        Path
            Path where token was saved.
        """
        token_path = cls.get_config_dir() / "token"
        write_private_text(token_path, token.strip())
        return token_path

    @classmethod
    def get_setup_path(cls) -> Path:
        """Path for persisted interactive setup profile."""
        return cls.get_config_dir() / cls.SETUP_FILENAME

    @classmethod
    def save_user_setup(
        cls,
        orcid: str,
        query_names: list[str] | None = None,
        debut_year: int | None = None,
        retire_year: int | None = None,
        library_ids: list[str] | None = None,
        *,
        end_year: int | None = None,
    ) -> Path:
        """
        Save identify-optimization profile used by `adsfolio pdf identify`.

        Parameters
        ----------
        orcid : str
            ORCID identifier (recommended).
        query_names : list[str], optional
            Optional author names for fallback matching.
        debut_year : int, optional
            Earliest publication year to include in prefetch.
        end_year : int, optional
            Latest publication year to include in prefetch.
        library_ids : list[str], optional
            ADS library IDs to include in prefetch.
        """
        from .user_profile import UserProfile

        return cls.save_user_profile(
            UserProfile(
                orcid=(orcid or "").strip(),
                author_names=tuple(
                    name.strip() for name in (query_names or []) if name.strip()
                ),
                first_publication_year=debut_year,
                last_publication_year=(
                    end_year if end_year is not None else retire_year
                ),
                ads_library_ids=tuple(
                    lib_id.strip() for lib_id in (library_ids or []) if lib_id.strip()
                ),
            )
        )

    @classmethod
    def load_user_profile(cls) -> UserProfile:
        """Load the normalized global publication profile."""
        from .user_profile import UserProfile

        payload = cls._load_setup_mapping()
        nested = payload.get("user_profile")
        legacy_profile = UserProfile.from_mapping(payload)
        if not isinstance(nested, Mapping):
            return legacy_profile
        return UserProfile.from_mapping(nested, fallback=legacy_profile)

    @classmethod
    def save_user_profile(cls, profile: UserProfile) -> Path:
        """Atomically persist the global publication profile."""
        return cls._save_setup_sections(
            profile=profile,
            identify=cls.load_identify_settings(),
            rename=cls.load_rename_settings(),
        )

    @classmethod
    def load_identify_settings(cls) -> IdentifySettings:
        """Load normalized global Identify behavior."""
        from .pdf.identify_settings import IdentifySettings

        payload = cls._load_setup_mapping()
        nested = payload.get("identify")
        legacy_settings = IdentifySettings.from_mapping(payload)
        if not isinstance(nested, Mapping):
            return legacy_settings
        return IdentifySettings.from_mapping(nested, fallback=legacy_settings)

    @classmethod
    def save_identify_settings(cls, settings: IdentifySettings) -> Path:
        """Atomically persist global Identify behavior without changing identity."""
        return cls._save_setup_sections(
            profile=cls.load_user_profile(),
            identify=settings,
            rename=cls.load_rename_settings(),
        )

    @classmethod
    def load_rename_settings(cls) -> RenameSettings:
        """Load normalized global Rename behavior."""
        from .pdf.rename_settings import RenameSettings

        payload = cls._load_setup_mapping()
        nested = payload.get("rename")
        legacy_settings = RenameSettings.from_mapping(payload)
        if not isinstance(nested, Mapping):
            return legacy_settings
        return RenameSettings.from_mapping(nested, fallback=legacy_settings)

    @classmethod
    def save_rename_settings(cls, settings: RenameSettings) -> Path:
        """Atomically persist global Rename behavior without changing identity."""
        return cls._save_setup_sections(
            profile=cls.load_user_profile(),
            identify=cls.load_identify_settings(),
            rename=settings,
        )

    @classmethod
    def load_key_policy(cls) -> KeyPolicy:
        """Load the citation key styles, or the defaults when unset or invalid."""
        from .citekeys import KeyPolicy

        payload = cls._load_setup_mapping().get("citation_keys")
        try:
            return KeyPolicy.from_mapping(
                payload if isinstance(payload, Mapping) else None
            )
        except ValueError:
            logger.warning("Ignoring invalid citation key settings; using defaults.")
            return KeyPolicy()

    @classmethod
    def save_key_policy(cls, policy: KeyPolicy) -> Path:
        """Atomically persist the citation key styles."""
        payload = cls._load_setup_mapping()
        payload["citation_keys"] = policy.to_mapping()
        return cls._save_setup_mapping(payload)

    @classmethod
    def _save_setup_sections(
        cls,
        *,
        profile: UserProfile,
        identify: IdentifySettings,
        rename: RenameSettings,
    ) -> Path:
        """Write canonical setup sections while preserving unknown keys."""
        payload = cls._load_setup_mapping()
        for key in (
            cls._PROFILE_FLAT_KEYS | cls._IDENTIFY_FLAT_KEYS | cls._RENAME_FLAT_KEYS
        ):
            payload.pop(key, None)

        existing_profile = payload.get("user_profile")
        profile_payload = (
            dict(existing_profile) if isinstance(existing_profile, Mapping) else {}
        )
        profile_payload.update(profile.to_mapping())

        existing_identify = payload.get("identify")
        identify_payload = (
            dict(existing_identify) if isinstance(existing_identify, Mapping) else {}
        )
        identify_payload.update(identify.to_mapping())

        existing_rename = payload.get("rename")
        rename_payload = (
            dict(existing_rename) if isinstance(existing_rename, Mapping) else {}
        )
        rename_payload.update(rename.to_mapping())

        payload.update(
            {
                "schema_version": cls.SETUP_SCHEMA_VERSION,
                "user_profile": profile_payload,
                "identify": identify_payload,
                "rename": rename_payload,
            }
        )
        return cls._save_setup_mapping(payload)

    @classmethod
    def _save_setup_mapping(cls, payload: dict[str, Any]) -> Path:
        """Atomically persist one setup mapping."""
        path = cls.get_setup_path()
        write_private_text(
            path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        )
        return path

    @classmethod
    def get_user_setup(cls) -> dict:
        """Return the publication profile using legacy flat API field names."""
        payload = cls._load_setup_mapping()
        if not payload:
            return {}
        profile = cls.load_user_profile()
        return {
            "orcid": profile.orcid,
            "query_names": list(profile.author_names),
            "debut_year": profile.first_publication_year,
            "end_year": profile.last_publication_year,
            "library_ids": list(profile.ads_library_ids),
        }

    @classmethod
    def _load_setup_mapping(cls) -> dict[str, Any]:
        """Load the raw setup mapping; return an empty mapping when invalid."""
        path = cls.get_setup_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    @classmethod
    def _prompt_for_token(cls) -> str:
        """Prompt user for token and save it."""
        print("\n" + "=" * 60)
        print("NASA ADS Token Required")
        print("=" * 60)
        print("Get your token at: https://scixplorer.org/user/settings/token")
        print()

        token = input("Paste your token: ").strip()

        if not token:
            raise AuthenticationError("No token provided.")

        token_path = cls.save_token(token)

        print(f"\nToken saved to: {token_path}")
        print("=" * 60 + "\n")

        return token

    @classmethod
    def update_rate_limits(cls, headers: dict) -> None:
        """Update rate limit info from response headers."""
        cls._rate_limit = headers.get("X-RateLimit-Limit")
        cls._rate_remaining = headers.get("X-RateLimit-Remaining")
        cls._rate_reset = headers.get("X-RateLimit-Reset")

    @classmethod
    def get_rate_limits(cls) -> dict:
        """Get current rate limit status."""
        return {
            "limit": cls._rate_limit,
            "remaining": cls._rate_remaining,
            "reset": cls._rate_reset,
        }

    @classmethod
    def check_rate_limit(cls) -> bool:
        """Check if we're close to rate limit. Returns True if OK to proceed."""
        if cls._rate_remaining is None:
            return True
        try:
            remaining = int(cls._rate_remaining)
            return remaining > 10  # Leave buffer
        except (ValueError, TypeError):
            return True
