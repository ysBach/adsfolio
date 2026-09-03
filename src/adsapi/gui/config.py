"""Persistent configuration for the adsapi desktop application."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ..config import Config

logger = logging.getLogger(__name__)

GUI_CONFIG_FILENAME = "gui.json"
GUI_CONFIG_VERSION = 3
DEFAULT_TIMESTAMP_FORMAT = "iso8601"
DEFAULT_HIDDEN_DETAIL_ROWS = [
    "annotations_failed",
    "annotations_ported",
    "original_bibcode",
    "unported_annotations_file",
    "upgraded_at",
    "upgrade_status",
]
MAX_ROW_HEIGHT = 200
MAX_ROW_SPACING = 64
_OBSOLETE_GUI_PREFERENCE_KEYS = {"source"}


@dataclass
class RootEntry:
    """A PDF library root registered in the GUI."""

    path: str
    label: str = ""
    recursive: bool = False
    watch_on_start: bool = False
    auto_process_new_pdfs: bool = False


@dataclass
class GuiConfig:
    """Serializable GUI preferences and registered library roots."""

    version: int = GUI_CONFIG_VERSION
    roots: list[RootEntry] = field(default_factory=list)
    last_root: str | None = None
    last_view: str = "all"
    window_geometry: str | None = None
    window_state: str | None = None
    column_widths: dict[str, int] = field(default_factory=dict)
    column_fonts: dict[str, dict[str, Any]] = field(default_factory=dict)
    table_column_order: list[str] = field(default_factory=list)
    hidden_table_columns: list[str] = field(default_factory=list)
    detail_fonts: dict[str, dict[str, Any]] = field(default_factory=dict)
    timestamp_format: str = DEFAULT_TIMESTAMP_FORMAT
    sort_column: str = "first_seen_at"
    sort_descending: bool = True
    secondary_sort_column: str | None = None
    tertiary_sort_column: str | None = None
    expand_abstracts: bool = False
    table_row_height: int | None = None
    detail_row_spacing: int | None = None
    detail_label_bold: bool = True
    show_edit_buttons: bool = True
    show_lock_buttons: bool = True
    filename_click_action: str = "open"
    pdf_viewer_command: str | None = None
    web_browser_command: str | None = None
    splitter_sizes: list[int] = field(default_factory=list)
    detail_row_order: list[str] = field(default_factory=list)
    detail_hidden_rows: list[str] = field(
        default_factory=lambda: list(DEFAULT_HIDDEN_DETAIL_ROWS)
    )
    identify_scopes: dict[str, str] = field(default_factory=dict)
    rename_scopes: dict[str, str] = field(default_factory=dict)


def config_path() -> Path:
    """Return the path of the GUI configuration file."""
    return Config.get_config_dir() / GUI_CONFIG_FILENAME


def _as_string(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _as_optional_command(value: Any) -> str | None:
    """Return a non-empty configured application command, if valid."""
    if not isinstance(value, str):
        return None
    command = value.strip()
    return command or None


def _as_optional_row_height(value: Any) -> int | None:
    """Return a persisted row height accepted by the appearance control."""
    height = _as_int(value, 0)
    return height if 0 < height <= MAX_ROW_HEIGHT else None


def _as_optional_row_spacing(value: Any) -> int | None:
    """Return persisted detail-row separation accepted by the control."""
    spacing = _as_int(value, 0)
    return spacing if 0 < spacing <= MAX_ROW_SPACING else None


def _from_mapping(payload: Mapping[str, Any]) -> GuiConfig:
    """Build a config from a mapping, ignoring unknown or malformed fields."""
    roots: list[RootEntry] = []
    raw_roots = payload.get("roots", [])
    if isinstance(raw_roots, list):
        for raw_root in raw_roots:
            if not isinstance(raw_root, Mapping):
                continue
            path = raw_root.get("path")
            if not isinstance(path, str) or not path:
                continue
            roots.append(
                RootEntry(
                    path=path,
                    label=_as_string(raw_root.get("label"), "") or "",
                    recursive=_as_bool(raw_root.get("recursive"), False),
                    watch_on_start=_as_bool(raw_root.get("watch_on_start"), False),
                    auto_process_new_pdfs=_as_bool(
                        raw_root.get("auto_process_new_pdfs"), False
                    ),
                )
            )

    raw_widths = payload.get("column_widths", {})
    column_widths: dict[str, int] = {}
    if isinstance(raw_widths, Mapping):
        for key, value in raw_widths.items():
            if not isinstance(key, str) or key in _OBSOLETE_GUI_PREFERENCE_KEYS:
                continue
            try:
                width = int(value)
            except (TypeError, ValueError):
                continue
            if isinstance(value, bool) or width < 0:
                continue
            column_widths[key] = width

    raw_splitter_sizes = payload.get("splitter_sizes", [])
    splitter_sizes: list[int] = []
    if isinstance(raw_splitter_sizes, list):
        for value in raw_splitter_sizes:
            if isinstance(value, bool):
                continue
            try:
                size = int(value)
            except (TypeError, ValueError):
                continue
            if size >= 0:
                splitter_sizes.append(size)

    detail_row_order: list[str] = []
    raw_detail_row_order = payload.get("detail_row_order", [])
    if isinstance(raw_detail_row_order, list):
        for value in raw_detail_row_order:
            if (
                not isinstance(value, str)
                or not value
                or value in _OBSOLETE_GUI_PREFERENCE_KEYS
                or value in detail_row_order
            ):
                continue
            detail_row_order.append(value)

    detail_hidden_rows: list[str] = []
    raw_detail_hidden_rows = payload.get(
        "detail_hidden_rows", DEFAULT_HIDDEN_DETAIL_ROWS
    )
    if isinstance(raw_detail_hidden_rows, list):
        for value in raw_detail_hidden_rows:
            if (
                not isinstance(value, str)
                or not value
                or value in _OBSOLETE_GUI_PREFERENCE_KEYS
                or value in detail_hidden_rows
            ):
                continue
            detail_hidden_rows.append(value)

    filename_click_action = _as_string(payload.get("filename_click_action"), "open")
    if filename_click_action not in {"open", "reveal"}:
        filename_click_action = "open"

    raw_fonts = payload.get("column_fonts", {})
    column_fonts: dict[str, dict[str, Any]] = {}
    if isinstance(raw_fonts, Mapping):
        for key, value in raw_fonts.items():
            if (
                not isinstance(key, str)
                or key in _OBSOLETE_GUI_PREFERENCE_KEYS
                or not isinstance(value, Mapping)
            ):
                continue
            family = value.get("family")
            point_size = value.get("point_size")
            color = value.get("color", "")
            if not isinstance(family, str) or not family:
                continue
            if isinstance(point_size, bool):
                continue
            try:
                normalized_size = int(point_size)
            except (TypeError, ValueError):
                continue
            if normalized_size > 0:
                normalized_font: dict[str, Any] = {
                    "family": family,
                    "point_size": normalized_size,
                }
                if isinstance(color, str) and color:
                    normalized_font["color"] = color
                column_fonts[key] = normalized_font

    table_column_order: list[str] = []
    raw_table_column_order = payload.get("table_column_order", [])
    if isinstance(raw_table_column_order, list):
        for value in raw_table_column_order:
            if (
                not isinstance(value, str)
                or not value
                or value in _OBSOLETE_GUI_PREFERENCE_KEYS
                or value in table_column_order
            ):
                continue
            table_column_order.append(value)

    hidden_table_columns: list[str] = []
    raw_hidden_table_columns = payload.get("hidden_table_columns", [])
    if isinstance(raw_hidden_table_columns, list):
        for value in raw_hidden_table_columns:
            if (
                not isinstance(value, str)
                or not value
                or value in _OBSOLETE_GUI_PREFERENCE_KEYS
                or value in hidden_table_columns
            ):
                continue
            hidden_table_columns.append(value)

    raw_detail_fonts = payload.get("detail_fonts", {})
    detail_fonts: dict[str, dict[str, Any]] = {}
    if isinstance(raw_detail_fonts, Mapping):
        for key, value in raw_detail_fonts.items():
            if (
                not isinstance(key, str)
                or key in _OBSOLETE_GUI_PREFERENCE_KEYS
                or not isinstance(value, Mapping)
            ):
                continue
            family = value.get("family")
            point_size = value.get("point_size")
            color = value.get("color", "")
            if not isinstance(family, str) or not family:
                continue
            if isinstance(point_size, bool):
                continue
            try:
                normalized_size = int(point_size)
            except (TypeError, ValueError):
                continue
            if normalized_size > 0:
                normalized_font = {
                    "family": family,
                    "point_size": normalized_size,
                }
                if isinstance(color, str) and color:
                    normalized_font["color"] = color
                detail_fonts[key] = normalized_font

    identify_scopes: dict[str, str] = {}
    raw_identify_scopes = payload.get("identify_scopes", {})
    if isinstance(raw_identify_scopes, Mapping):
        for path, scope in raw_identify_scopes.items():
            if isinstance(path, str) and scope in {"global", "library"}:
                identify_scopes[str(Path(path).expanduser().resolve())] = str(scope)

    rename_scopes: dict[str, str] = {}
    raw_rename_scopes = payload.get("rename_scopes", {})
    if isinstance(raw_rename_scopes, Mapping):
        for path, scope in raw_rename_scopes.items():
            if isinstance(path, str) and scope in {"global", "library"}:
                rename_scopes[str(Path(path).expanduser().resolve())] = str(scope)

    return GuiConfig(
        version=GUI_CONFIG_VERSION,
        roots=roots,
        last_root=_as_string(payload.get("last_root")),
        last_view=_as_string(payload.get("last_view"), "all") or "all",
        window_geometry=_as_string(payload.get("window_geometry")),
        window_state=_as_string(payload.get("window_state")),
        column_widths=column_widths,
        column_fonts=column_fonts,
        table_column_order=table_column_order,
        hidden_table_columns=hidden_table_columns,
        detail_fonts=detail_fonts,
        timestamp_format=_as_string(
            payload.get("timestamp_format"), DEFAULT_TIMESTAMP_FORMAT
        )
        or DEFAULT_TIMESTAMP_FORMAT,
        sort_column=_as_string(payload.get("sort_column"), "first_seen_at")
        or "first_seen_at",
        sort_descending=_as_bool(payload.get("sort_descending"), True),
        secondary_sort_column=_as_string(payload.get("secondary_sort_column")),
        tertiary_sort_column=_as_string(payload.get("tertiary_sort_column")),
        expand_abstracts=_as_bool(payload.get("expand_abstracts"), False),
        table_row_height=_as_optional_row_height(payload.get("table_row_height")),
        detail_row_spacing=_as_optional_row_spacing(payload.get("detail_row_spacing")),
        detail_label_bold=_as_bool(payload.get("detail_label_bold"), True),
        show_edit_buttons=_as_bool(payload.get("show_edit_buttons"), True),
        show_lock_buttons=_as_bool(payload.get("show_lock_buttons"), True),
        filename_click_action=filename_click_action or "open",
        pdf_viewer_command=_as_optional_command(payload.get("pdf_viewer_command")),
        web_browser_command=_as_optional_command(payload.get("web_browser_command")),
        splitter_sizes=splitter_sizes,
        detail_row_order=detail_row_order,
        detail_hidden_rows=detail_hidden_rows,
        identify_scopes=identify_scopes,
        rename_scopes=rename_scopes,
    )


def load_config() -> GuiConfig:
    """Load GUI preferences, returning defaults when unavailable or invalid.

    Returns
    -------
    GuiConfig
        Loaded preferences, or a default configuration when the file is
        missing, malformed, or unreadable.
    """
    path = config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return GuiConfig()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load GUI configuration from %s: %s", path, exc)
        return GuiConfig()
    if not isinstance(payload, Mapping):
        logger.warning("Ignoring non-object GUI configuration at %s", path)
        return GuiConfig()
    return _from_mapping(payload)


def save_config(config: GuiConfig) -> Path:
    """Atomically save GUI preferences and return the destination path.

    Parameters
    ----------
    config : GuiConfig
        Preferences to serialize.

    Returns
    -------
    pathlib.Path
        The path of the saved configuration.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    payload = asdict(config)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError:
        # Do not hide the original write/replace error, but avoid leaving a
        # stale temporary file after a failed save where possible.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


__all__ = [
    "GUI_CONFIG_FILENAME",
    "GUI_CONFIG_VERSION",
    "DEFAULT_HIDDEN_DETAIL_ROWS",
    "GuiConfig",
    "RootEntry",
    "config_path",
    "load_config",
    "save_config",
]
