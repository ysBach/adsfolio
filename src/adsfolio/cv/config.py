"""Configuration loading for ``adsfolio cv``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SUPPORTED_OUTPUT_FORMATS = ("latex", "markdown")
SUPPORTED_STYLES = ("compact", "detailed", "bibcode-first")
SUPPORTED_LATEX_LIST_ENVS = ("itemize", "enumerate")

DEFAULT_EXCLUDE_BIBSTEMS = (
    "zndo",
    "yCat",
    "EPSC",
    "cosp",
    "AGUFM",
    "BKAS",
    "LPICo",
    "IAUFM",
    "AAS",
)

DEFAULT_QUERY = 'author:"doe, jane" year:2010-'
DEFAULT_ME_FMT = r"\textbf{{\color{{black}}{{{}}}}}"


@dataclass(frozen=True)
class MarkerConfig:
    """Marker configuration for a bibcode category."""

    symbol: str
    description: str
    bibcodes: frozenset[str]


@dataclass(frozen=True)
class CVConfig:
    """Immutable runtime config for CV generation."""

    query: str
    orcid: str
    use_orcid: bool
    debut_year: int | None
    retire_year: int | None
    exclude_bibstems: tuple[str, ...]
    first_author_prefix: str
    me_target: str
    me_display: str
    output_format: Literal["latex", "markdown"]
    style: str
    n_max_authors: int
    n_min_authors: int
    n_me: int
    author_sep: str
    me_fmt: str
    author_format: str
    markers: dict[str, MarkerConfig]
    status: dict[str, str]
    output_dir: Path
    output_filename: str
    journal_legend: bool
    latex_list_env: str
    latex_left_margin: str
    latex_item_sep: str

    @property
    def output_path(self) -> Path:
        """Path where generated output is written."""
        return self.output_dir / self.output_filename


def _as_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a mapping.")
    return {str(key): item for key, item in value.items()}


def _as_string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list.")
    items = [str(item).strip() for item in value]
    return tuple(item for item in items if item)


def _as_int(value: Any, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc


def _as_optional_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer or null.") from exc


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError("Boolean field must be true/false.")


def _as_choice(value: Any, default: str, options: tuple[str, ...], field: str) -> str:
    text = str(value if value is not None else default).strip()
    if text not in options:
        choices = ", ".join(options)
        raise ValueError(f"{field} must be one of: {choices}.")
    return text


def _resolve_output_dir(raw: Any, config_path: Path) -> Path:
    if raw is None:
        return config_path.parent
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def normalize_author_format(author_format: str) -> str:
    """Normalize and validate CV author format option."""
    text = author_format.strip()
    if not text:
        return "cv_default"
    if text == "cv_default" or text.startswith("ads_custom:"):
        return text
    raise ValueError("format.author_format must be cv_default or ads_custom:<format>.")


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PyYAML is required for adsfolio cv config loading."
        ) from exc

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Failed reading config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML in {path}: {exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Top-level config must be a mapping.")
    return {str(key): item for key, item in raw.items()}


def _load_markers(raw_markers: dict[str, Any]) -> dict[str, MarkerConfig]:
    markers: dict[str, MarkerConfig] = {}
    for marker_name, marker_raw in raw_markers.items():
        marker_map = _as_mapping(marker_raw, f"markers.{marker_name}")
        marker = MarkerConfig(
            symbol=str(marker_map.get("symbol", "")).strip(),
            description=str(marker_map.get("description", "")).strip(),
            bibcodes=frozenset(
                _as_string_tuple(marker_map.get("bibcodes"), f"markers.{marker_name}")
            ),
        )
        markers[marker_name] = marker
    return markers


def _section_map(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    author_cfg = _as_mapping(raw.get("author"), "author")
    format_cfg = _as_mapping(raw.get("format"), "format")
    marker_cfg = _as_mapping(raw.get("markers"), "markers")
    status_cfg = _as_mapping(raw.get("status"), "status")
    output_cfg = _as_mapping(raw.get("output"), "output")
    latex_cfg = _as_mapping(output_cfg.get("latex"), "output.latex")
    return {
        "author": author_cfg,
        "format": format_cfg,
        "markers": marker_cfg,
        "status": status_cfg,
        "output": output_cfg,
        "latex": latex_cfg,
    }


def _validate_query(author_cfg: dict[str, Any]) -> str:
    query = str(author_cfg.get("query", "")).strip()
    if not query:
        raise ValueError("author.query is required.")
    return query


def _default_output_filename(
    format_cfg: dict[str, Any], output_cfg: dict[str, Any]
) -> str:
    default_name = (
        "publ.tex" if str(format_cfg.get("output", "latex")) == "latex" else "publ.md"
    )
    return str(output_cfg.get("filename", default_name))


def _author_fields(author_cfg: dict[str, Any], query: str) -> dict[str, Any]:
    exclude_bibstems = _as_string_tuple(
        author_cfg.get("exclude_bibstems"),
        "author.exclude_bibstems",
    )
    first_author_prefix = str(author_cfg.get("first_author_prefix", "")).strip()
    orcid = str(author_cfg.get("orcid", "")).strip()
    use_orcid = _as_bool(author_cfg.get("use_orcid"), bool(orcid))
    return {
        "query": query,
        "orcid": orcid,
        "use_orcid": use_orcid,
        "debut_year": _as_optional_int(
            author_cfg.get("debut_year"), "author.debut_year"
        ),
        "retire_year": _as_optional_int(
            author_cfg.get("retire_year"), "author.retire_year"
        ),
        "exclude_bibstems": exclude_bibstems or DEFAULT_EXCLUDE_BIBSTEMS,
        "first_author_prefix": first_author_prefix or "doe, j",
        "me_target": str(author_cfg.get("me_target", "Doe, J.")).strip(),
        "me_display": str(author_cfg.get("me_display", "Jane Doe")).strip(),
    }


def _format_fields(format_cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_format": _as_choice(
            format_cfg.get("output"),
            "latex",
            SUPPORTED_OUTPUT_FORMATS,
            "format.output",
        ),
        "style": _as_choice(
            format_cfg.get("style"),
            "compact",
            SUPPORTED_STYLES,
            "format.style",
        ),
        "n_max_authors": _as_int(
            format_cfg.get("n_max_authors"),
            5,
            "format.n_max_authors",
        ),
        "n_min_authors": _as_int(
            format_cfg.get("n_min_authors"),
            3,
            "format.n_min_authors",
        ),
        "n_me": _as_int(format_cfg.get("n_me"), 6, "format.n_me"),
        "author_sep": str(format_cfg.get("author_sep", "; ")),
        "me_fmt": str(format_cfg.get("me_fmt", DEFAULT_ME_FMT)),
        "author_format": normalize_author_format(
            str(format_cfg.get("author_format", "cv_default"))
        ),
    }


def _output_fields(
    format_cfg: dict[str, Any],
    output_cfg: dict[str, Any],
    latex_cfg: dict[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    return {
        "output_dir": _resolve_output_dir(output_cfg.get("dir"), config_path),
        "output_filename": _default_output_filename(format_cfg, output_cfg),
        "journal_legend": _as_bool(output_cfg.get("journal_legend"), True),
        "latex_list_env": _as_choice(
            latex_cfg.get("list_env"),
            "itemize",
            SUPPORTED_LATEX_LIST_ENVS,
            "output.latex.list_env",
        ),
        "latex_left_margin": str(latex_cfg.get("left_margin", "-14em")),
        "latex_item_sep": str(latex_cfg.get("item_sep", "6pt")),
    }


def _build_cv_config(
    sections: dict[str, dict[str, Any]],
    query: str,
    config_path: Path,
) -> CVConfig:
    author_cfg = sections["author"]
    format_cfg = sections["format"]
    marker_cfg = sections["markers"]
    status_cfg = sections["status"]
    output_cfg = sections["output"]
    latex_cfg = sections["latex"]
    author_fields = _author_fields(author_cfg, query)
    format_fields = _format_fields(format_cfg)
    output_fields = _output_fields(format_cfg, output_cfg, latex_cfg, config_path)
    return CVConfig(
        **author_fields,
        **format_fields,
        markers=_load_markers(marker_cfg),
        status={str(key): str(value) for key, value in status_cfg.items()},
        **output_fields,
    )


def load_config(path: Path) -> CVConfig:
    """Load ``cv.yaml`` from disk."""
    config_path = path.expanduser().resolve()
    raw = _load_yaml(config_path)
    sections = _section_map(raw)
    query = _validate_query(sections["author"])
    return _build_cv_config(sections, query, config_path)


def _year_clause(start: int | None, end: int | None) -> str:
    left = "*" if start is None else str(start)
    right = "*" if end is None else str(end)
    if left == "*" and right == "*":
        return ""
    return f"year:[{left} TO {right}]"


def build_query(cfg: CVConfig) -> str:
    """Combine base query and bibstem exclusions into one Solr query."""
    clauses: list[str] = []
    if cfg.use_orcid and cfg.orcid:
        clauses.append(f'orcid_pub:"{cfg.orcid}"')

    year_clause = _year_clause(cfg.debut_year, cfg.retire_year)
    if year_clause:
        clauses.append(year_clause)

    base_query = cfg.query.strip()
    if base_query:
        clauses.append(base_query)
    elif not clauses:
        clauses.append("*:*")

    for bibstem in cfg.exclude_bibstems:
        text = bibstem.strip()
        if text:
            clauses.append(f"bibstem:(- {text})")
    return " AND ".join(f"({part})" for part in clauses if part).strip()
