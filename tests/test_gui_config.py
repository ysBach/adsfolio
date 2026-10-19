import json
import logging

from adsfolio.citations import CitationStyle, initial_citation_styles
from adsfolio.config import Config
from adsfolio.gui.config import (
    GUI_CONFIG_VERSION,
    GuiConfig,
    RootEntry,
    config_path,
    load_config,
    save_config,
)


def _use_tmp_config(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))


def test_config_roundtrip(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    expected = GuiConfig(
        roots=[RootEntry("/papers", "Papers", recursive=True)],
        last_root="/papers",
        last_view="resolved",
        window_geometry="Z2VvbWV0cnk=",
        window_state="c3RhdGU=",
        column_widths={"title": 400},
        column_fonts={"bibcode": {"family": "Menlo", "point_size": 12}},
        table_column_order=["bibcode", "title"],
        hidden_table_columns=["filename"],
        detail_fonts={"abstract": {"family": "Times New Roman", "point_size": 12}},
        detail_label_bold=False,
        show_edit_buttons=False,
        show_lock_buttons=False,
        filename_click_action="reveal",
        pdf_viewer_command="pdf-viewer",
        web_browser_command="web-browser",
        timestamp_format="%Y-%m-%d",
        sort_column="title",
        sort_descending=True,
        splitter_sizes=[200, 800, 300],
        detail_row_order=["modified", "title", "full_path"],
        detail_hidden_rows=["upgrade_status", "annotations_failed"],
    )

    assert save_config(expected) == config_path()
    assert load_config() == expected
    assert expected.version == GUI_CONFIG_VERSION
    assert not config_path().with_name("gui.json.tmp").exists()


def test_load_config_returns_defaults_for_missing_file(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)

    loaded = load_config()
    assert loaded == GuiConfig()
    assert loaded.detail_label_bold


def test_gui_config_defaults_to_added_newest_first(monkeypatch, tmp_path) -> None:
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().write_text(json.dumps({"version": GUI_CONFIG_VERSION}))

    loaded = load_config()

    assert GuiConfig().sort_column == "first_seen_at"
    assert GuiConfig().sort_descending
    assert loaded.sort_column == "first_seen_at"
    assert loaded.sort_descending


def test_config_preserves_explicit_valid_legacy_sort(monkeypatch, tmp_path) -> None:
    _use_tmp_config(monkeypatch, tmp_path)
    expected = GuiConfig(sort_column="title", sort_descending=False)

    save_config(expected)

    loaded = load_config()
    assert loaded.sort_column == "title"
    assert not loaded.sort_descending


def test_main_window_invalid_sort_falls_back_to_added_newest_first(
    monkeypatch, qtbot, tmp_path
) -> None:
    import adsfolio.gui.main_window as main_window_module

    _use_tmp_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        main_window_module,
        "load_config",
        lambda: GuiConfig(sort_column="not-a-column", sort_descending=False),
    )
    monkeypatch.setattr(main_window_module, "save_config", lambda config: None)
    window = main_window_module.MainWindow()
    qtbot.addWidget(window)

    assert window.current_query.order_by == "first_seen_at"
    assert window.current_query.descending


def test_load_config_returns_defaults_for_corrupt_json(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("{not-json", encoding="utf-8")

    assert load_config() == GuiConfig()


def test_load_config_ignores_unknown_and_malformed_keys(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        json.dumps(
            {
                "version": 1,
                "future_setting": {"enabled": True},
                "roots": [
                    {"path": "/papers", "label": "Papers", "recursive": True},
                    "not-an-entry",
                    {"label": "missing path"},
                ],
                "column_widths": {"title": 400, "bad": "not-an-int"},
                "column_fonts": {
                    "bibcode": {"family": "Menlo", "point_size": 12},
                    "source": {"family": "Menlo", "point_size": 12},
                    "bad": {"family": "", "point_size": 12},
                },
                "detail_row_order": ["modified", "source", 3, "title", "title"],
                "detail_hidden_rows": [
                    "upgrade_status",
                    "source",
                    3,
                    "upgrade_status",
                ],
                "table_row_height": -1,
                "detail_row_spacing": 201,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config()
    assert loaded.roots == [RootEntry("/papers", "Papers", recursive=True)]
    assert loaded.column_widths == {"title": 400}
    assert loaded.column_fonts == {"bibcode": {"family": "Menlo", "point_size": 12}}
    assert loaded.table_column_order == []
    assert loaded.hidden_table_columns == []
    assert loaded.detail_fonts == {}
    assert loaded.detail_row_order == ["modified", "title"]
    assert loaded.detail_hidden_rows == ["upgrade_status"]
    assert loaded.table_row_height is None
    assert loaded.detail_row_spacing is None


def test_load_config_defaults_action_button_visibility_for_malformed_values(
    monkeypatch, tmp_path
):
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        json.dumps(
            {
                "show_edit_buttons": "yes",
                "show_lock_buttons": 1,
                "filename_click_action": "invalid",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_config()

    assert loaded.show_edit_buttons
    assert loaded.show_lock_buttons
    assert loaded.filename_click_action == "open"


def test_config_roundtrips_normalized_identify_scopes(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    root = tmp_path / "papers"
    save_config(GuiConfig(identify_scopes={str(root): "library"}))

    loaded = load_config()

    assert loaded.identify_scopes == {str(root.resolve()): "library"}


def test_version_two_config_loads_safe_watch_defaults(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().write_text(
        json.dumps({"version": 2, "roots": [{"path": "/papers"}]}),
        encoding="utf-8",
    )
    loaded = load_config()
    assert loaded.version == GUI_CONFIG_VERSION
    assert not loaded.roots[0].watch_on_start
    assert not loaded.roots[0].auto_process_new_pdfs


def test_watch_policy_and_rename_scope_roundtrip(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    root = str((tmp_path / "papers").resolve())
    expected = GuiConfig(
        roots=[RootEntry(root, watch_on_start=True, auto_process_new_pdfs=True)],
        rename_scopes={root: "library"},
    )
    save_config(expected)
    assert load_config() == expected


def test_citation_styles_roundtrip(monkeypatch, tmp_path) -> None:
    _use_tmp_config(monkeypatch, tmp_path)
    styles = [CitationStyle(id="alpha", name="Compact", template="{bibcode}")]
    save_config(GuiConfig(citation_styles=styles))
    assert load_config().citation_styles == styles


def test_missing_citation_key_gets_initial_style_but_saved_empty_stays_empty(
    monkeypatch, tmp_path
) -> None:
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().write_text('{"version": 3}', encoding="utf-8")
    assert load_config().citation_styles == list(initial_citation_styles())
    save_config(GuiConfig(citation_styles=[]))
    assert load_config().citation_styles == []


def test_malformed_citation_styles_skipped_normalized_and_deduplicated(
    monkeypatch, tmp_path, caplog
) -> None:
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        json.dumps(
            {
                "version": 4,
                "citation_styles": [
                    "not-a-dict",
                    {"id": "", "name": "No ID", "template": "{bibcode}"},
                    {"id": "valid-1", "name": "Valid 1", "template": "{bibcode}"},
                    {
                        "id": "valid-1",
                        "name": "Different Name",
                        "template": "{title}",
                    },
                    {
                        "id": "valid-2",
                        "name": "VALID 1",
                        "template": "{title}",
                    },
                    {
                        "id": "valid-3",
                        "name": "Normalized",
                        "template": "{bibcode}",
                        "author_format": "invalid_format",
                        "max_authors": "invalid_int",
                        "authors_when_truncated": -5,
                    },
                    {"id": "valid-4", "name": "No Template", "template": ""},
                    {
                        "id": "valid-5",
                        "name": "Bad Syntax",
                        "template": "{invalid_field}",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        loaded = load_config()

    assert len(loaded.citation_styles) == 2
    assert loaded.citation_styles[0] == CitationStyle(
        id="valid-1", name="Valid 1", template="{bibcode}"
    )
    assert loaded.citation_styles[1] == CitationStyle(
        id="valid-3",
        name="Normalized",
        template="{bibcode}",
        author_format="surname_initials",
        max_authors=3,
        authors_when_truncated=1,
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "Duplicate" in r.message or "duplicate" in r.message for r in warning_records
    )
    assert any(
        "Skipping malformed" in r.message or "malformed" in r.message.lower()
        for r in warning_records
    )
