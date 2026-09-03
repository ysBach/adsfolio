# Custom Citation Styles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add globally configured plain-text citation styles that appear as copyable, appearance-aware columns and detail rows.

**Architecture:** A new Qt-free citation domain module validates and renders immutable styles from cached record metadata. The GUI configuration persists those styles, while `MainWindow` supplies runtime column definitions to `FileTableModel` and runtime computed-row definitions to `DetailPane`. A dedicated style editor owns collection editing and commits one validated list atomically.

**Tech Stack:** Python 3.10+, dataclasses, `string.Formatter`, PySide6, pytest, pytest-qt, Ruff, isort.

**Spec:** `docs/superpowers/specs/2026-09-03-custom-citation-styles-ads-library-design.md`

## Global Constraints

- Citation rendering is local and read-only: no ADS request and no filesystem rename.
- Preserve spaces, punctuation, Unicode, and plain text; never append `.pdf` or apply filename sanitation.
- Permit only `{bibcode}`, `{year}`, `{title}`, `{first_author_surname}`, `{first_author_initials}`, `{authors}`, `{authors_without_suffix}`, `{journal}`, `{bibstem}`, `{pub}`, `{volume}`, `{page}`, plus `{{` and `}}`.
- Reject attribute/item access, conversions, format specifications, unknown fields, unmatched braces, duplicate names, empty names, and empty templates.
- Enforce `1 <= authors_when_truncated <= max_authors <= 99` and local author modes only.
- Use stable `citation:<style-id>` preference keys; a rename must not lose layout or typography settings.
- Generated columns and rows are movable/hideable; generated columns are not sortable.
- Keep Bibcode/arXiv links, filename clicks, PDF double-clicks, Rename, watch, and metadata editing unchanged.
- Use `source ~/.venvs/ysbpy/bin/activate` and `uv run --active ...` for Python commands.
- Use test-driven development, run isort and Ruff formatting before each code commit, and do not modify the original checkout.

---

### Task 1: Plain-text citation domain

**Files:**
- Create: `src/adsapi/citations.py`
- Create: `tests/test_citations.py`

**Interfaces:**
- Consumes: `adsapi.formatting.html_to_text(text: str) -> str`.
- Produces: `CitationStyle`, `CitationFormatter`, `CitationStyleError`, `CITATION_FIELD_HELP`, `DEFAULT_CITATION_STYLE_ID`, `initial_citation_styles()`, `citation_preference_key(style_id)`, `new_citation_style(existing_names, existing_ids)`, and `validate_citation_styles(styles)`.

- [ ] **Step 1: Write failing rendering and validation tests**

Add focused tests with this representative record and explicit expected output:

```python
RECORD = {
    "metadata": {
        "bibcode": "2024A&A...123..456B",
        "year": "2024",
        "title": ["Dust <SUP>2</SUP> dynamics"],
        "author": ["Bach, Yoonsoo P.", "Doe, Alice", "Roe, Bob"],
        "bibstem": ["A&A"],
        "pub": "Astronomy &amp; Astrophysics",
        "volume": "123",
        "page": ["456"],
    }
}


def test_formatter_preserves_citation_text_and_expands_every_field() -> None:
    style = CitationStyle(
        id="style-1",
        name="Readable",
        template=(
            "{first_author_surname}|{first_author_initials}|{authors}|"
            "{authors_without_suffix}|{year}|{title}|{journal}|{bibstem}|"
            "{pub}|{volume}|{page}|{bibcode}|{{literal}}"
        ),
        max_authors=2,
        authors_when_truncated=1,
        author_separator=", ",
        author_truncation_suffix=" et al.",
    )
    assert CitationFormatter().render(style, RECORD) == (
        "Bach|BachYP|BachYP et al.|BachYP|2024|Dust 2 dynamics|A&A|A&A|"
        "Astronomy & Astrophysics|123|456|2024A&A...123..456B|{literal}"
    )
```

Also add parametrized tests for scalar/list metadata, missing known fields,
Unicode names, each author mode, author counts below/equal/above the threshold,
empty rendered output, and `journal` fallback to `pub`. Add validation cases for
`{first_author}`, `{first_author.name}`, `{author[0]}`, `{year!r}`,
`{year:04}`, unmatched braces, invalid limits, `ads_custom:`, duplicate
case-folded names, duplicate IDs, and empty collections.

- [ ] **Step 2: Run the domain tests and verify RED**

Run:

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_citations.py
```

Expected: collection fails because `adsapi.citations` does not exist.

- [ ] **Step 3: Implement the immutable model and renderer**

Implement these public definitions with numpydoc docstrings and typed
signatures:

```python
DEFAULT_CITATION_STYLE_ID = "my-bibcode-1"
ALLOWED_CITATION_FIELDS = frozenset(
    {
        "bibcode",
        "year",
        "title",
        "first_author_surname",
        "first_author_initials",
        "authors",
        "authors_without_suffix",
        "journal",
        "bibstem",
        "pub",
        "volume",
        "page",
    }
)


class CitationStyleError(ValueError):
    """Report an invalid citation style or style collection."""


@dataclass(frozen=True)
class CitationStyle:
    id: str
    name: str
    template: str
    author_format: str = "surname_initials"
    max_authors: int = 3
    authors_when_truncated: int = 1
    author_separator: str = ", "
    author_truncation_suffix: str = " et al."

```

Add `CitationStyle.from_mapping(payload: Mapping[str, Any]) -> CitationStyle`
and `CitationStyle.to_mapping() -> dict[str, object]`; the former trims required
strings, normalizes malformed optional author controls to the dataclass
defaults, and validates the finished value, while the latter emits every field
shown above. Add
`CitationFormatter.render(style: CitationStyle, record: Mapping[str, Any]) -> str`
with the parsing and rendering behavior below.

Use `string.Formatter.parse()` for syntax inspection. Reject any non-empty
`format_spec` or `conversion`, any field outside the exact allowlist, and any
field containing `.` or `[`. Render with a plain dictionary only after
validation. Reuse the author split/initial concepts, not `PdfRenamer.render` or
its normalization methods. Treat a no-comma author as last-token surname and
preceding-token given names, matching current Rename behavior.

`initial_citation_styles()` returns one style with ID
`my-bibcode-1`, name `My bibcode 1`, and template
`{first_author_initials} ({year}) {bibstem}, {volume}, {page}`.
`new_citation_style()` uses the lowest unused positive `My bibcode N` name and
`uuid.uuid4().hex` for a new ID. `citation_preference_key()` rejects an empty ID
and returns `citation:<id>`. `validate_citation_styles()` permits an empty tuple
but enforces per-style validity plus unique trimmed/case-folded names and IDs.

- [ ] **Step 4: Run focused tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/citations.py tests/test_citations.py
uv run --active ruff format src/adsapi/citations.py tests/test_citations.py
uv run --active ruff check src/adsapi/citations.py tests/test_citations.py
uv run --active pytest -q tests/test_citations.py
```

Expected: all citation-domain tests pass and quality checks report no errors.

- [ ] **Step 5: Commit the domain unit**

```bash
git add src/adsapi/citations.py tests/test_citations.py
git commit -m "feat: add plain-text citation styles"
```

---

### Task 2: Citation-style persistence and migration

**Files:**
- Modify: `src/adsapi/gui/config.py`
- Modify: `tests/test_gui_config.py`

**Interfaces:**
- Consumes: `CitationStyle.from_mapping()`, `CitationStyle.to_mapping()`, `initial_citation_styles()`, and `validate_citation_styles()` from Task 1.
- Produces: `GuiConfig.citation_styles: list[CitationStyle]` and GUI config schema version 4.

- [ ] **Step 1: Write failing config tests**

Add tests proving:

```python
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
```

Add malformed-entry coverage: skip non-mappings and empty structural fields;
normalize malformed optional author settings to defaults; retain the first
duplicate ID/name; log one warning per skipped duplicate; ignore obsolete
`citation:<deleted-id>` preferences only when the style manager explicitly
deletes the style, not during generic config loading.

- [ ] **Step 2: Run config tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_config.py
```

Expected: failures show missing `citation_styles` and version 4 behavior.

- [ ] **Step 3: Implement schema version 4 persistence**

Set `GUI_CONFIG_VERSION = 4`. Add
`citation_styles: list[CitationStyle]` to `GuiConfig`; because saved explicit
emptiness must survive, use `default_factory=lambda: list(initial_citation_styles())`
only for a newly constructed config. In `_from_mapping`, distinguish
`"citation_styles" not in payload` from an explicit empty list. Parse entries
in order, warn and skip malformed structures, normalize individual settings,
and retain the first valid duplicate name/ID. `asdict()` already serializes
frozen dataclasses, so keep atomic save behavior unchanged.

- [ ] **Step 4: Run focused and regression tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/gui/config.py tests/test_gui_config.py
uv run --active ruff format src/adsapi/gui/config.py tests/test_gui_config.py
uv run --active ruff check src/adsapi/gui/config.py tests/test_gui_config.py
uv run --active pytest -q tests/test_citations.py tests/test_gui_config.py
```

Expected: all tests pass, including version-2/3 safe-default tests.

- [ ] **Step 5: Commit persistence**

```bash
git add src/adsapi/gui/config.py tests/test_gui_config.py
git commit -m "feat: persist global citation styles"
```

---

### Task 3: Runtime table columns

**Files:**
- Modify: `src/adsapi/gui/library.py`
- Modify: `src/adsapi/gui/models.py`
- Modify: `tests/test_gui_library.py`
- Modify: `tests/test_gui_models.py`

**Interfaces:**
- Consumes: `CitationStyle`, `CitationFormatter.render()`, and `citation_preference_key()`.
- Produces: `BASE_COLUMNS`, `build_columns(styles) -> tuple[Column, ...]`, `Column.sortable`, `FileTableModel.columns`, and `FileTableModel.set_columns(columns)`.

- [ ] **Step 1: Write failing runtime-column tests**

Test that `build_columns([style_a, style_b])` inserts generated columns directly
after Bibcode, retains style order, uses stable keys, renders record metadata,
and marks only generated columns non-sortable. Test model reset behavior and
that sorting a generated column leaves `model.query` and registry calls
unchanged:

```python
def test_generated_citation_column_is_displayed_but_not_sortable(qtbot) -> None:
    style = CitationStyle(id="alpha", name="Compact", template="{year}: {title}")
    columns = build_columns([style])
    model = FileTableModel(_StaticRegistry([RECORD]), FileQuery(), columns=columns)
    citation = next(i for i, item in enumerate(columns) if item.key == "citation:alpha")
    assert model.index(0, citation).data(Qt.DisplayRole) == "2024: A title"
    before = model.query
    model.sort(citation, Qt.DescendingOrder)
    assert model.query == before
```

Also verify generated cells expose their full text through tooltip and
accessible roles, and base Bibcode/arXiv roles remain unchanged.

- [ ] **Step 2: Run table tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_library.py tests/test_gui_models.py
```

Expected: failures show missing dynamic column APIs.

- [ ] **Step 3: Implement dynamic column definitions**

Rename the declaration to `BASE_COLUMNS` and retain `COLUMNS = BASE_COLUMNS`
temporarily as a compatibility alias for unaffected callers/tests. Extend
`Column` with `sortable: bool = True`. `build_columns()` creates closures with a
bound style default argument to avoid late binding and inserts generated
columns after the base Bibcode definition.

Change `FileTableModel.__init__` to accept
`columns: Sequence[Column] = BASE_COLUMNS`, store an immutable tuple, expose a
`columns` property, and add:

```python
def set_columns(self, columns: Sequence[Column]) -> None:
    normalized = tuple(columns)
    self.beginResetModel()
    self._columns = normalized
    self.endResetModel()
```

Use `self._columns` in `columnCount`, `data`, `headerData`, and `sort`. For a
non-sortable column, return from `sort` without calling `set_query`. Generated
cells return their complete display text for tooltip and accessibility roles.

- [ ] **Step 4: Run focused quality and behavior tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/gui/library.py src/adsapi/gui/models.py tests/test_gui_library.py tests/test_gui_models.py
uv run --active ruff format src/adsapi/gui/library.py src/adsapi/gui/models.py tests/test_gui_library.py tests/test_gui_models.py
uv run --active ruff check src/adsapi/gui/library.py src/adsapi/gui/models.py tests/test_gui_library.py tests/test_gui_models.py
uv run --active pytest -q tests/test_citations.py tests/test_gui_library.py tests/test_gui_models.py
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit runtime columns**

```bash
git add src/adsapi/gui/library.py src/adsapi/gui/models.py tests/test_gui_library.py tests/test_gui_models.py
git commit -m "feat: add runtime citation columns"
```

---

### Task 4: Runtime citation rows in the detail pane

**Files:**
- Modify: `src/adsapi/gui/detail_pane.py`
- Modify: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Consumes: immutable `CitationStyle` values and `CitationFormatter.render()`.
- Produces: `DetailPane.set_citation_styles(styles)` and generated computed rows keyed by `citation:<id>`.

- [ ] **Step 1: Write failing detail-row tests**

Add tests that configure two styles, set a record, and assert generated rows:

```python
pane.set_citation_styles(
    [
        CitationStyle(id="a", name="Short", template="{bibcode}"),
        CitationStyle(id="b", name="Readable", template="{year}: {title}"),
    ]
)
pane.set_record(RECORD, Path("/papers"))
assert pane.row_keys().index("citation:a") == pane.row_keys().index("bibcode") + 1
assert pane.rows["citation:a"].value._copy_text == "2024TEST"
assert not pane.rows["citation:a"].edit_button.isVisible()
assert not pane.rows["citation:a"].lock_button.isVisible()
```

Verify style order, `available_rows()`, hidden/order preferences, per-row
typography, recomputation after a changed record, and no copy for an empty
rendered value.

- [ ] **Step 2: Run detail tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_detail_pane.py
```

Expected: failure because `set_citation_styles` and computed rows do not exist.

- [ ] **Step 3: Implement computed detail rows**

Store `self._citation_styles: tuple[CitationStyle, ...]`. Add
`set_citation_styles()` that replaces styles and rerenders the current record.
During `set_record`, add generated fields immediately after Bibcode in style
order. Create `MetadataRow` with `editable=False`, `lockable=False`, and the
renderer output. Keep generated values outside metadata and outside
`ADS_FIELDS`, so edit/lock persistence cannot affect them. Ensure an empty
value leaves the `CopyableLabel` inert under its existing `self.text()` guard.

- [ ] **Step 4: Run quality and detail regressions**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py
uv run --active ruff format src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py
uv run --active ruff check src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py
uv run --active pytest -q tests/test_citations.py tests/test_gui_detail_pane.py
```

Expected: all focused tests pass, including existing copy/edit/lock behavior.

- [ ] **Step 5: Commit detail rows**

```bash
git add src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py
git commit -m "feat: show citation styles in details"
```

---

### Task 5: Citation-style editor

**Files:**
- Create: `src/adsapi/gui/citation_styles.py`
- Create: `tests/test_gui_citation_styles.py`

**Interfaces:**
- Consumes: Task 1's domain API; preview renders directly with
  `CitationFormatter` into a normal read-only label.
- Produces: `CitationStylesDialog(styles, preview_record=None, parent=None)`, `CitationStylesDialog.styles`, and `CitationStylesDialog.saved = Signal(object)`.

- [ ] **Step 1: Write failing editor tests**

Test Add, Duplicate, rename, move, delete-to-empty, preview, Cancel, and atomic
Save. Assert the Template help/accessible description contains every allowed
placeholder, literal-brace syntax, missing-field behavior, HTML conversion,
Unicode/punctuation preservation, no `.pdf`, and every save-blocking error.

Use this save contract:

```python
dialog = CitationStylesDialog([style], preview_record=RECORD)
dialog.name_edit.setText("Readable")
dialog.template_edit.setText("{first_author_initials} ({year})")
dialog._save()
assert dialog.result() == QDialog.Accepted
assert dialog.styles[0].name == "Readable"
```

Verify Preview never changes `dialog.styles`, and a validation error calls
`QMessageBox.critical`, leaves the dialog open, and selects the affected list
item/control.

- [ ] **Step 2: Run editor tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_citation_styles.py
```

Expected: collection fails because the GUI module does not exist.

- [ ] **Step 3: Implement the dedicated editor**

Use a `QListWidget` plus explicit Add, Duplicate, Move Up, Move Down, and Delete
buttons. Use `QLineEdit` controls for name, template, separator, and suffix;
`QComboBox` for the three local author formats; and `QSpinBox` controls for the
two author counts. Synchronize `authors_when_truncated.maximum()` whenever the
threshold or selected style changes.

Keep a private mutable list of immutable style replacements. Before selection
changes or list operations, validate and store the current editor fields. Add
uses `new_citation_style`; Duplicate uses `dataclasses.replace` with a fresh
UUID and a unique `Copy of <name>` suffix. Preview validates only the current
draft and renders against `preview_record` or a labeled built-in example. Save
stores the current draft, validates the full tuple, emits it, and accepts.
Cancel rejects without emission.

Put `CITATION_FIELD_HELP` in a word-wrapped, selectable `QTextBrowser` or
`QLabel` that is always visible, and use the exact same complete text for the
Template help button and accessible description.

- [ ] **Step 4: Run GUI quality and behavior tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/gui/citation_styles.py tests/test_gui_citation_styles.py
uv run --active ruff format src/adsapi/gui/citation_styles.py tests/test_gui_citation_styles.py
uv run --active ruff check src/adsapi/gui/citation_styles.py tests/test_gui_citation_styles.py
uv run --active pytest -q tests/test_citations.py tests/test_gui_citation_styles.py
```

Expected: all tests pass in offscreen Qt mode.

- [ ] **Step 5: Commit the editor**

```bash
git add src/adsapi/gui/citation_styles.py tests/test_gui_citation_styles.py
git commit -m "feat: add citation style editor"
```

---

### Task 6: Main-window and Appearance integration

**Files:**
- Modify: `src/adsapi/gui/main_window.py`
- Modify: `src/adsapi/gui/appearance.py`
- Modify: `tests/test_gui_detail_pane.py`
- Modify: `tests/test_gui_appearance.py`
- Modify: `tests/test_gui_dialogs.py`

**Interfaces:**
- Consumes: `build_columns()`, `CitationStylesDialog`, `FileTableModel.set_columns()`, and `DetailPane.set_citation_styles()`.
- Produces: live style replacement, click-to-copy citation cells, and full appearance persistence for generated keys.

- [ ] **Step 1: Write failing integration tests**

Add tests proving:

- `Tools -> Custom Citation Styles...` opens with global config styles and the
  selected record as preview data;
- accepting replaces the runtime columns and detail rows exactly once;
- deleting a style removes only that stable key from width/order/hidden/font
  and detail preference collections;
- new generated columns appear after Bibcode and are visible by default;
- clicking a non-empty generated cell copies text and shows status feedback;
- clicking an empty generated cell does not replace clipboard content;
- existing Bibcode/arXiv/filename click tests remain unchanged;
- Appearance receives runtime table definitions, lists generated columns/rows,
  and returns their order, visibility, font, and color settings.

- [ ] **Step 2: Run GUI integration tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
```

Expected: failures identify missing actions, runtime definitions, and copy path.

- [ ] **Step 3: Replace static GUI column assumptions**

In `MainWindow`, set `self.columns = build_columns(self.config.citation_styles)`
before constructing the model. Pass `columns=self.columns` to the model. Replace
all GUI uses of `COLUMNS` for index lookup, width persistence, header menus,
layout, delegates, and close-state capture with `self.columns` or
`self.model.columns`. Keep sort-priority choices limited to
`column.sortable is True`.

Change `AppearanceDialog.__init__` to accept
`table_columns: Sequence[Column]` explicitly alongside detail rows; stop
importing static `COLUMNS` there. Preserve live preview/Cancel rollback.

- [ ] **Step 4: Add style action and atomic live replacement**

Add `self.citation_styles_action = QAction("Custom Citation Styles…", self)`
under Tools. `_edit_citation_styles()` opens the dedicated dialog. On accept:

1. compare old/new stable IDs;
2. remove deleted `citation:<id>` keys from all table/detail preference maps
   and lists;
3. insert new keys after Bibcode in table order and after Bibcode in detail
   order, preserving all existing relative placements;
4. assign `config.citation_styles`;
5. rebuild columns and call model/detail setters;
6. recreate per-column delegates for the new model shape;
7. apply current widths/layout/appearance and refresh the selected record.

In `_table_clicked`, detect `citation:` keys before link/file branches. Copy a
non-empty `Qt.DisplayRole` value, show `Copied` in the status bar, and do
nothing for empty text.

- [ ] **Step 5: Run focused GUI regressions**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsapi/gui/main_window.py src/adsapi/gui/appearance.py tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
uv run --active ruff format src/adsapi/gui/main_window.py src/adsapi/gui/appearance.py tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
uv run --active ruff check src/adsapi/gui/main_window.py src/adsapi/gui/appearance.py tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
uv run --active pytest -q tests/test_gui_config.py tests/test_gui_library.py tests/test_gui_models.py tests/test_gui_citation_styles.py tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
```

Expected: all citation and existing GUI interaction tests pass.

- [ ] **Step 6: Commit integration**

```bash
git add src/adsapi/gui/main_window.py src/adsapi/gui/appearance.py tests/test_gui_appearance.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py
git commit -m "feat: integrate copyable citation styles"
```

---

### Task 7: Citation documentation and complete verification

**Files:**
- Modify: `docs/gui.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed citation behavior from Tasks 1–6.
- Produces: user instructions and verified subsystem release candidate.

- [ ] **Step 1: Add exact user documentation**

Document `Tools -> Custom Citation Styles...`, Add/Duplicate/Rename/Reorder/
Delete, preview/save/cancel behavior, generated table/detail placement,
click-to-copy, empty values, local-only rendering, and the complete field-help
reference from the spec. Include this exact example:

```text
{first_author_initials} ({year}) {bibstem}, {volume}, {page}
→ BachYP (2024) A&A, 123, 456
```

State that custom citation text is not a filename and does not rename PDFs.

- [ ] **Step 2: Run documentation and full quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort --check-only src tests
uv run --active ruff format --check src tests
uv run --active ruff check src tests
uv run --active pytest -q
git diff --check
```

Expected: 0 failures, 0 lint errors, no formatting changes required, and no
whitespace errors.

- [ ] **Step 3: Manually smoke-test the Qt flow**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active adsapi-gui
```

Verify one bounded flow: open the style editor, create a second style, preview,
save, copy both a table cell and detail row, hide/reorder one in Appearance,
restart the app, and confirm style plus appearance persistence. Do not rename a
PDF and do not run an ADS operation.

- [ ] **Step 4: Commit documentation and verification-ready state**

```bash
git add README.md docs/gui.md
git commit -m "docs: explain custom citation styles"
```
