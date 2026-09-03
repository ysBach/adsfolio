# GUI Resolution and Detail Appearance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resolution state compact and actionable, consolidate detail-pane settings, disclose complete abstracts, and remove the ambiguous `Source` GUI field.

**Architecture:** Preserve the existing `status`/`bibcode` registry data and use the canonical resolved condition for both SQL ordering and the Qt marker delegate. Keep detail-pane preferences in `GuiConfig`; move the visibility/reorder UI into `Appearance` while retaining the existing in-pane drag path. `source` remains internal storage used by upgrade logic but is removed from all GUI presentation surfaces.

**Tech Stack:** Python 3.10+, SQLite, PySide6, pytest-qt, Ruff, uv.

## Global Constraints

- Do not migrate the SQLite schema or rename/delete persisted `source` metadata.
- The `arXiv only` view and `pdf upgrade` behavior remain unchanged.
- A green marker means a non-empty bibcode and `status != "unresolved"`; all other records need attention.
- Do not use color as the only resolution-state cue: provide tooltip and accessible text.
- Existing `gui.json` files with obsolete `source` preferences must load safely.
- Keep edits local; preserve current editable/lockable ADS-field behavior and drag-to-reorder.
- Run Python checks through `uv run`; use offscreen Qt tests.

---

### Task 1: Resolution marker and resolution-first ordering

**Files:**
- Modify: `src/adsapi/pdf/registry.py:35-55`
- Modify: `src/adsapi/gui/library.py:145-157`
- Modify: `src/adsapi/gui/models.py:1-180`
- Modify: `src/adsapi/gui/main_window.py:70-110`
- Test: `tests/test_pdf_registry.py`
- Test: `tests/test_gui_library.py`
- Test: `tests/test_gui_models.py`

**Interfaces:**
- Consumes: existing `PdfRegistry.query_files(order_by, descending, **filters)`.
- Produces: `COLUMNS` entry `Column("status", "", 22, ...)` and
  `ResolutionDelegate(QStyledItemDelegate)`.
- Produces: `SORTABLE_COLUMNS["status"]` as a constant SQL expression that
  evaluates unresolved/invalid rows to `0` and resolved rows to `1`.

- [ ] **Step 1: Write failing registry ordering tests**

  Add three records: one with a bibcode and `status="resolved"`, one with an
  empty bibcode and `status="resolved"`, and one with a bibcode and
  `status="unresolved"`. Assert ascending `query_files(order_by="status")`
  returns the two needs-attention records before the resolved record.

  ```python
  records = registry.query_files(order_by="status")
  assert [record["relative_path"] for record in records] == [
      "missing-bibcode.pdf", "unresolved.pdf", "resolved.pdf",
  ]
  ```

- [ ] **Step 2: Run the registry test to verify the old lexical sort fails**

  Run: `uv run pytest tests/test_pdf_registry.py -q`

  Expected: the test fails because lexical `"resolved" < "unresolved"` places
  the resolved record first.

- [ ] **Step 3: Replace the status sort expression**

  In `SORTABLE_COLUMNS`, replace `"status": "status"` with the constant SQL
  expression below. It deliberately matches the existing canonical condition
  used by `is_resolved_record` without interpolating caller input.

  ```python
  "status": (
      "CASE WHEN bibcode IS NOT NULL AND TRIM(bibcode) != '' "
      "AND status != 'unresolved' THEN 1 ELSE 0 END"
  ),
  ```

- [ ] **Step 4: Write failing model and table-definition tests**

  Assert the status column has an empty header and narrow width; create model
  indexes for a resolved and unresolved record and assert their `ToolTipRole`
  and `AccessibleTextRole` values are `"Resolved"` and `"Needs attention"`.

  ```python
  status_column = next(i for i, column in enumerate(COLUMNS) if column.key == "status")
  assert COLUMNS[status_column].header == ""
  assert model.index(0, status_column).data(Qt.ToolTipRole) == "Resolved"
  ```

- [ ] **Step 5: Implement the marker delegate and semantic model roles**

  Add `ResolutionDelegate` in `models.py`. It must draw a centered green circle
  only for `is_resolved_record(record)` and otherwise draw no glyph. Keep
  `Qt.DisplayRole` empty for the column; return the resolution text for
  `Qt.ToolTipRole` and `Qt.AccessibleTextRole`. Import and use
  `is_resolved_record` rather than reimplementing a divergent Python predicate.

  ```python
  if role in (Qt.ToolTipRole, Qt.AccessibleTextRole) and column.key == "status":
      return "Resolved" if is_resolved_record(record) else "Needs attention"
  ```

  In `main_window.py`, install `ResolutionDelegate` for the `status` column in
  the existing `for index, column in enumerate(COLUMNS)` loop.

- [ ] **Step 6: Run focused tests**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py -q`

  Expected: PASS.

- [ ] **Step 7: Format, lint, and commit the task**

  Run: `uv run isort src/adsapi/gui/library.py src/adsapi/gui/models.py src/adsapi/gui/main_window.py src/adsapi/pdf/registry.py tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py`

  Run: `uv run ruff format src/adsapi/gui/library.py src/adsapi/gui/models.py src/adsapi/gui/main_window.py src/adsapi/pdf/registry.py tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py && uv run ruff check src/adsapi/gui/library.py src/adsapi/gui/models.py src/adsapi/gui/main_window.py src/adsapi/pdf/registry.py tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py`

  ```bash
  git add src/adsapi/pdf/registry.py src/adsapi/gui/library.py src/adsapi/gui/models.py src/adsapi/gui/main_window.py tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py
  git commit -m "feat: compact GUI resolution status"
  ```

### Task 2: Remove Source from the GUI safely

**Files:**
- Modify: `src/adsapi/gui/library.py:145-157`
- Modify: `src/adsapi/gui/detail_pane.py:32-93, 545-575`
- Modify: `src/adsapi/gui/config.py:120-197`
- Modify: `docs/gui.md:20-42`
- Test: `tests/test_gui_library.py`
- Test: `tests/test_gui_detail_pane.py`
- Test: `tests/test_gui_config.py`

**Interfaces:**
- Consumes: stored `source` field and `LibraryView.ARXIV_ONLY` unchanged.
- Produces: GUI `COLUMNS`, `DEFAULT_ROW_ORDER`, `FIELD_LABELS`, and
  `DetailPane.available_rows()` with no `source` entry.

- [ ] **Step 1: Write failing presentation and legacy-preference tests**

  Assert `"source"` is absent from `COLUMNS`, `pane.available_rows()`, and the
  rendered row keys. Load a config payload containing `source` in all three
  affected preference collections and assert no `source` key survives.

  ```python
  assert "source" not in {column.key for column in COLUMNS}
  assert "source" not in dict(pane.available_rows())
  assert "source" not in loaded.column_fonts
  assert "source" not in loaded.detail_row_order
  assert "source" not in loaded.detail_hidden_rows
  ```

- [ ] **Step 2: Run the focused tests to verify the old GUI exposes Source**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py -q`

  Expected: the new absence assertions fail.

- [ ] **Step 3: Remove GUI presentation entries without changing data behavior**

  Remove the `source` column, its detail default-order/label/known-metadata
  entries, and documentation wording that calls source a displayed field. Do
  not remove `FileQuery`'s `ARXIV_ONLY` filter or `PdfRegistry` support for
  `source`.

- [ ] **Step 4: Filter obsolete configuration keys while loading**

  Define private GUI-only valid-key sets from `COLUMNS` and from the detail row
  keys. In `_from_mapping`, discard `source` only where it is a GUI preference;
  retain unknown future values where `DetailPane` already supports them. This
  prevents obsolete saved fonts/row order from being written back indefinitely.

  ```python
  if key == "source":
      continue
  ```

  Keep this compatibility filter local to GUI preference parsing; do not alter
  registry metadata.

- [ ] **Step 5: Run focused tests**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py -q`

  Expected: PASS, including the existing arXiv-only query test.

- [ ] **Step 6: Format, lint, and commit the task**

  Run: `uv run isort src/adsapi/gui/library.py src/adsapi/gui/detail_pane.py src/adsapi/gui/config.py tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py`

  Run: `uv run ruff format src/adsapi/gui/library.py src/adsapi/gui/detail_pane.py src/adsapi/gui/config.py tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py && uv run ruff check src/adsapi/gui/library.py src/adsapi/gui/detail_pane.py src/adsapi/gui/config.py tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py`

  ```bash
  git add src/adsapi/gui/library.py src/adsapi/gui/detail_pane.py src/adsapi/gui/config.py docs/gui.md tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_config.py
  git commit -m "feat: remove source from GUI"
  ```

### Task 3: Consolidate table and detail appearance controls

**Files:**
- Modify: `src/adsapi/gui/appearance.py:34-260`
- Modify: `src/adsapi/gui/main_window.py:20-25, 205-220, 480-525`
- Modify: `src/adsapi/gui/config.py:35-55, 120-197`
- Modify: `src/adsapi/gui/detail_pane.py:395-455`
- Test: `tests/test_gui_config.py`
- Test: `tests/test_gui_detail_pane.py`
- Create: `tests/test_gui_appearance.py`

**Interfaces:**
- Replaces: `DetailRowsDialog` and `MainWindow._edit_detail_rows()`.
- Produces: `AppearanceDialog` with properties `column_fonts`, `timestamp_format_value`,
  `detail_row_order`, and `detail_hidden_rows`.
- Consumes: existing `GuiConfig.column_fonts`, `detail_row_order`, and
  `detail_hidden_rows` fields.

- [ ] **Step 1: Write failing dialog tests**

  Create an offscreen test that instantiates the dialog with an explicit detail
  row order and hidden field, moves a row through the Details tab, unchecks a
  different row, accepts the dialog, and checks the returned lists. Also assert
  the dialog title is `"Appearance"` and its tab labels are `Table`, `Details`,
  and `Time Format`.

  ```python
  dialog = AppearanceDialog(config, pane.available_rows())
  assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
      "Table", "Details", "Time Format",
  ]
  ```

- [ ] **Step 2: Run the dialog test to verify the current split UI fails**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py -q`

  Expected: FAIL because `AppearanceDialog` and its details properties do not
  exist.

- [ ] **Step 3: Replace the two dialogs with one Appearance dialog**

  Rename `TableAppearanceDialog` to `AppearanceDialog`. Keep the current fonts
  table as the `Table` tab, preserving its single `detail` typography row. Add a
  `Details` tab containing a `QListWidget` with each row's key in
  `Qt.UserRole`, a checked/unchecked visibility state, and internal
  drag-and-drop reorder. The tab owns no duplicate source of truth: its
  properties read the list's current order and check state.

  ```python
  item.setData(Qt.UserRole, key)
  item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
  item.setCheckState(Qt.Unchecked if key in hidden else Qt.Checked)
  ```

  Configure the list with `InternalMove`, then expose ordered visible/hidden
  fields through `detail_row_order` and `detail_hidden_rows` properties. Do not
  use a separate Detail Rows dialog.

- [ ] **Step 4: Connect MainWindow and preserve row-order behavior**

  Remove the `Detail Rows…` action, import, and handler. Rename the View action
  to `Appearance…`. When accepted, update column fonts/timestamp plus detail
  order/visibility, call `DetailPane.set_row_order()` and
  `DetailPane.set_hidden_rows()`, then rerender the current detail selection.
  Retain `DetailPane.row_order_changed` so drag handles continue updating the
  same `GuiConfig.detail_row_order` field.

- [ ] **Step 5: Add configuration round-trip coverage**

  Extend configuration tests to round-trip the returned details settings and to
  retain the shared `detail` font/color record. Confirm a reloaded `MainWindow`
  applies both the detail style and saved row visibility/order.

- [ ] **Step 6: Run focused tests**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py tests/test_gui_config.py tests/test_gui_detail_pane.py -q`

  Expected: PASS.

- [ ] **Step 7: Format, lint, and commit the task**

  Run: `uv run isort src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py src/adsapi/gui/config.py src/adsapi/gui/detail_pane.py tests/test_gui_appearance.py tests/test_gui_config.py tests/test_gui_detail_pane.py`

  Run: `uv run ruff format src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py src/adsapi/gui/config.py src/adsapi/gui/detail_pane.py tests/test_gui_appearance.py tests/test_gui_config.py tests/test_gui_detail_pane.py && uv run ruff check src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py src/adsapi/gui/config.py src/adsapi/gui/detail_pane.py tests/test_gui_appearance.py tests/test_gui_config.py tests/test_gui_detail_pane.py`

  ```bash
  git add src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py src/adsapi/gui/config.py src/adsapi/gui/detail_pane.py tests/test_gui_appearance.py tests/test_gui_config.py tests/test_gui_detail_pane.py
  git commit -m "feat: consolidate GUI appearance settings"
  ```

### Task 4: Expand and collapse long abstracts inline

**Files:**
- Modify: `src/adsapi/gui/detail_pane.py:220-355`
- Test: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Consumes: `MetadataRow(key="abstract", ...)` and existing `CopyableLabel`.
- Produces: `MetadataRow.abstract_toggle: QToolButton` and a bounded collapsed
  abstract preview that retains the original copy text.

- [ ] **Step 1: Write failing abstract disclosure tests**

  Render a long scalar abstract and assert the abstract row begins collapsed,
  has a visible expand control, and has a smaller value height than after
  triggering the control. Assert collapse restores the original bounded height.
  Add empty and list/scalar safety cases that do not raise and hide the control
  when no text exists.

  ```python
  abstract = pane._row_widgets["abstract"]
  assert abstract.abstract_toggle.isVisible()
  collapsed_height = abstract.value.height()
  abstract.abstract_toggle.click()
  assert abstract.value.height() > collapsed_height
  ```

- [ ] **Step 2: Run the test to verify the unbounded current label fails**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_detail_pane.py -q`

  Expected: FAIL because `MetadataRow` has no abstract disclosure control and
  always shows the complete text.

- [ ] **Step 3: Add a dedicated abstract rendering path to MetadataRow**

  Add `abstract_toggle` alongside `author_toggle`. For `key == "abstract"`,
  retain a complete plain-text copy value, render a word-wrapped preview, set a
  maximum collapsed height based on three line heights, and toggle that maximum
  height between collapsed and `QWIDGETSIZE_MAX`. Use concise button labels
  `Show more` / `Show less` and update the accessible name/tool tip as state
  changes.

  ```python
  self.abstract_toggle.clicked.connect(self._toggle_abstract)
  self.value.setMaximumHeight(
      QWIDGETSIZE_MAX if self._show_full_abstract else 3 * self.value.fontMetrics().lineSpacing()
  )
  ```

  Do not alter `DetailPane._metadata`, the edit button, lock button, or
  `CopyableLabel.copy_text`; the disclosure is view state only.

- [ ] **Step 4: Run focused tests**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_detail_pane.py -q`

  Expected: PASS.

- [ ] **Step 5: Format, lint, and commit the task**

  Run: `uv run isort src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py`

  Run: `uv run ruff format src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py && uv run ruff check src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py`

  ```bash
  git add src/adsapi/gui/detail_pane.py tests/test_gui_detail_pane.py
  git commit -m "feat: disclose complete abstract text"
  ```

### Task 5: Documentation and end-to-end verification

**Files:**
- Modify: `docs/gui.md:18-42`
- Test: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Documents: blank-header resolution marker, `Appearance…` dialog, inline
  abstract disclosure, and retained internal preprint workflow.

- [ ] **Step 1: Update GUI documentation**

  Replace references to **Table Appearance…** and **Detail Rows…** with
  **Appearance…**. Explain the green resolution marker and how clicking its
  blank header groups records needing attention. State that Source is no longer
  shown; do not claim source metadata was deleted. Document that abstracts
  expand inline.

- [ ] **Step 2: Run document and targeted GUI checks**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_models.py tests/test_gui_library.py tests/test_gui_detail_pane.py tests/test_gui_appearance.py tests/test_gui_config.py -q`

  Run: `uv run ruff format --check . && uv run ruff check .`

  Run: `scripts/docs_check.sh`

  Expected: all commands exit successfully.

- [ ] **Step 3: Run the complete suite**

  Run: `QT_QPA_PLATFORM=offscreen uv run pytest -q`

  Expected: PASS.

- [ ] **Step 4: Manually smoke-test the GUI**

  Run: `QT_QPA_PLATFORM=offscreen uv run adsapi gui`

  Verify: the window opens; the table has a narrow blank marker header; View
  contains `Appearance…` only; the Details tab reorders/hides rows; a long
  abstract can expand and collapse; Source does not appear in the table or
  detail pane. Close the window normally.

- [ ] **Step 5: Commit documentation and verification changes**

  ```bash
  git add docs/gui.md
  git commit -m "docs: describe refined GUI appearance"
  ```

## Plan self-review

**Spec coverage:** Task 1 covers the marker, canonical sorting, and accessible
semantics. Task 2 removes Source only from GUI presentation while retaining its
internal uses. Task 3 unifies the settings dialog and persists details
preferences. Task 4 gives abstracts a non-destructive inline disclosure.
Task 5 covers documentation and full verification.

**Placeholder scan:** No implementation placeholders remain; every code task
names its files, public interfaces, test command, and expected behavior.

**Type consistency:** `COLUMNS`, `GuiConfig.detail_row_order`,
`GuiConfig.detail_hidden_rows`, `DetailPane.set_row_order`, and
`DetailPane.set_hidden_rows` use their existing list-based interfaces. The only
new widget-facing type is `ResolutionDelegate`, installed directly on the
existing `QTreeView` column.
