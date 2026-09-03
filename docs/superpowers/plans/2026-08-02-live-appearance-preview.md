# Live Appearance Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply Appearance changes to the real GUI immediately, restore the prior state on Cancel, and remove the duplicate table preview.

**Architecture:** `AppearanceDialog` emits a parameterless `settings_changed` signal for every user-visible setting mutation. `MainWindow` snapshots `GuiConfig`, transfers dialog values into a runtime candidate config, and applies all table/detail settings through one coordinated helper; rejection restores the snapshot through that same helper.

**Tech Stack:** Python 3.10+, PySide6, pytest-qt, pytest, uv, Ruff.

## Global Constraints

- Live preview is temporary; no configuration file write occurs per edit.
- Cancel and dialog-window close restore the complete pre-dialog `GuiConfig` and visible UI state.
- Accepted settings remain active and use existing close-time persistence.
- Remove only the duplicate table Preview widget; retain the timestamp Example label.
- Do not change the GUI configuration schema or add dependencies.
- Run Python commands through `uv run` and Qt tests with `QT_QPA_PLATFORM=offscreen`.

---

### Task 1: Emit live changes from `AppearanceDialog`

**Files:**
- Modify: `src/adsapi/gui/appearance.py:8-180,246-488`
- Test: `tests/test_gui_appearance.py`

**Interfaces:**
- Produces `AppearanceDialog.settings_changed: Signal`.
- Produces `_emit_settings_changed(*args: Any) -> None` as the slot-safe signal emitter.
- Removes the `preview` table and `_update_preview()` implementation.
- Preserves the existing dialog properties consumed by `MainWindow`.

- [ ] **Step 1: Write the failing signal tests**

  Add tests that change a row-height control and a visibility control. Each
  test must wait for the dialog signal, proving that user edits—not only
  property reads—notify the owner.

  ```python
  def test_appearance_dialog_emits_for_row_height(application, qtbot) -> None:
      dialog = AppearanceDialog(GuiConfig(), [("title", "Title")])

      with qtbot.waitSignal(dialog.settings_changed, timeout=1000):
          dialog.table_row_height_spin.setValue(24)

      dialog.close()


  def test_appearance_dialog_emits_for_visibility(application, qtbot) -> None:
      dialog = AppearanceDialog(GuiConfig(), [("title", "Title")])

      with qtbot.waitSignal(dialog.settings_changed, timeout=1000):
          dialog.font_table.item(0, 0).setCheckState(Qt.Unchecked)

      dialog.close()
  ```

- [ ] **Step 2: Run the focused tests and confirm the intended failure**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py::test_appearance_dialog_emits_for_row_height tests/test_gui_appearance.py::test_appearance_dialog_emits_for_visibility -q
  ```

  Expected: FAIL because `AppearanceDialog` does not yet define
  `settings_changed`.

- [ ] **Step 3: Add the signal and connect every mutable control**

  Import `Signal`, declare `settings_changed = Signal()` on
  `AppearanceDialog`, and implement:

  ```python
  def _emit_settings_changed(self, *_args: Any) -> None:
      self.settings_changed.emit()
  ```

  Connect the following signals to that emitter, preserving the existing
  update slots where needed:

  - font enable toggles, font-family changes, size changes, and valid color
    selections;
  - `font_table.itemChanged` and `detail_table.itemChanged` for visibility;
  - both vertical-header `sectionMoved` signals for row reorder;
  - timestamp text and preset changes;
  - abstract expansion and label-boldness toggles; and
  - table/detail row-height `valueChanged` signals.

  Ensure bulk font application emits once after it changes its target rows, so
  changing only a bulk color still updates the main window.

- [ ] **Step 4: Remove the duplicate table preview**

  Delete the `Preview` label, `self.preview` table construction, the initial
  `_update_preview()` call, all preview-only callback calls, and the
  `_update_preview()` method. Keep the timestamp Example label and all dialog
  properties unchanged.

- [ ] **Step 5: Run the signal and existing Appearance tests**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py -q
  ```

  Expected: PASS.

### Task 2: Apply live settings and restore on Cancel

**Files:**
- Modify: `src/adsapi/gui/main_window.py:3-15,545-565,610-647`
- Test: `tests/test_gui_appearance.py`

**Interfaces:**
- Produces `_apply_appearance_dialog(self, dialog: AppearanceDialog) -> None`.
- Produces `_apply_appearance_config(self) -> None`, which applies detail order/
  visibility together with the existing table/detail appearance application.
- Keeps `_apply_table_appearance()` available for existing callers and tests.

- [ ] **Step 1: Write failing live-preview and rollback tests**

  Add modal-dialog tests that use `QTimer.singleShot` to edit the active
  dialog while `MainWindow._edit_appearance()` is inside `exec()`. The first
  callback records the live value before rejecting; the second accepts the same
  kind of edit.

  ```python
  def test_appearance_cancel_restores_after_live_edit(
      application, monkeypatch, tmp_path
  ) -> None:
      monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
      window = MainWindow()
      original = deepcopy(window.config)
      observed: dict[str, object] = {}

      def edit_and_cancel() -> None:
          dialog = QApplication.activeModalWidget()
          assert isinstance(dialog, AppearanceDialog)
          dialog.table_row_height_spin.setValue(80)
          observed["live_height"] = window.config.table_row_height
          dialog.reject()

      QTimer.singleShot(0, edit_and_cancel)
      window._edit_appearance()

      assert observed["live_height"] == 80
      assert window.config == original
      window.close()


  def test_appearance_accept_keeps_live_edit(
      application, monkeypatch, tmp_path
  ) -> None:
      monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
      window = MainWindow()

      def edit_and_accept() -> None:
          dialog = QApplication.activeModalWidget()
          assert isinstance(dialog, AppearanceDialog)
          dialog.table_row_height_spin.setValue(80)
          dialog.accept()

      QTimer.singleShot(0, edit_and_accept)
      window._edit_appearance()

      assert window.config.table_row_height == 80
      window.close()
  ```

  Add the required `deepcopy` and `QTimer` imports in the test module. These
  tests must fail before the MainWindow signal wiring exists because the live
  value remains unchanged.

- [ ] **Step 2: Run the new tests and confirm the intended failure**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py::test_appearance_cancel_restores_after_live_edit tests/test_gui_appearance.py::test_appearance_accept_keeps_live_edit -q
  ```

  Expected: FAIL because `_edit_appearance()` currently applies settings only
  after an accepted dialog.

- [ ] **Step 3: Add the complete appearance-transfer helper**

  Import `deepcopy` in `main_window.py`. Implement
  `_apply_appearance_dialog()` by copying every dialog property into
  `self.config`:

  ```python
  self.config.column_fonts = {
      key: dict(value) for key, value in dialog.column_fonts.items()
  }
  self.config.detail_fonts = {
      key: dict(value) for key, value in dialog.detail_fonts.items()
  }
  self.config.timestamp_format = dialog.timestamp_format_value
  self.config.table_column_order = list(dialog.table_column_order)
  self.config.hidden_table_columns = list(dialog.hidden_table_columns)
  self.config.expand_abstracts = dialog.expand_abstracts
  self.config.table_row_height = dialog.table_row_height
  self.config.detail_row_height = dialog.detail_row_height
  self.config.detail_label_bold = dialog.detail_label_bold
  self.config.detail_row_order = list(dialog.detail_row_order)
  self.config.detail_hidden_rows = list(dialog.detail_hidden_rows)
  self._apply_appearance_config()
  ```

  Implement `_apply_appearance_config()` to call
  `detail.set_row_order()`, `detail.set_hidden_rows()`,
  `_apply_table_appearance()`, and `_current_changed()` in that order. This
  makes the existing table/detail typography path and the detail row-layout
  path one rollback-safe operation.

- [ ] **Step 4: Wire live updates and snapshot rollback**

  Replace the current post-`exec()` assignments in `_edit_appearance()` with:

  ```python
  original_config = deepcopy(self.config)
  dialog = AppearanceDialog(self.config, self.detail.available_rows(), self)
  dialog.settings_changed.connect(lambda: self._apply_appearance_dialog(dialog))
  result = dialog.exec()
  if result == QDialog.DialogCode.Accepted:
      self._apply_appearance_dialog(dialog)
  else:
      self.config = original_config
      self._apply_appearance_config()
  ```

  The accepted call is intentional: it captures any final control state even
  if a widget changed without a distinct signal emission. The rejected path
  must restore the copied object before applying, so dialog-owned dictionaries
  cannot mutate the snapshot.

- [ ] **Step 5: Run the focused Qt tests**

  Run:

  ```bash
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py -q
  ```

  Expected: PASS, including live-before-accept, accepted-state retention, and
  complete Cancel rollback.

### Task 3: Document and verify the finished behavior

**Files:**
- Modify: `docs/gui.md:20-45`
- Modify: `tests/test_gui_appearance.py` only if focused verification reveals a
  missing user-visible assertion

- [ ] **Step 1: Document live editing and Cancel rollback**

  Extend the Appearance documentation with a direct statement that changes
  apply immediately to the main table and detail pane, that Cancel restores
  the previous appearance, and that accepted settings are saved with the
  normal application configuration.

- [ ] **Step 2: Run import sorting and formatting**

  Run:

  ```bash
  uv run isort src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py tests/test_gui_appearance.py
  uv run ruff format src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py tests/test_gui_appearance.py
  ```

- [ ] **Step 3: Run linting and the complete test suite**

  Run:

  ```bash
  uv run ruff check src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py tests/test_gui_appearance.py
  QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_appearance.py tests/test_gui_config.py -q
  uv run pytest
  ```

  Expected: all commands exit with status 0 and no new warnings.

- [ ] **Step 4: Review the final diff and commit implementation**

  Run `git diff --check` and inspect only the intended files. Then commit:

  ```bash
  git add src/adsapi/gui/appearance.py src/adsapi/gui/main_window.py tests/test_gui_appearance.py docs/gui.md docs/superpowers/plans/2026-08-02-live-appearance-preview.md
  git commit -m "feat: preview appearance changes in main window"
  ```
