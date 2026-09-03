# Editable Detail Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users edit, lock, reorder, and refresh selected PDF metadata from ADS without overwriting locked fields.

**Architecture:** Store the per-file lock set in the existing `metadata_json` payload under a private `'_adsapi_locked_fields'` key. Extend the existing CLI ADS refresh command with a single-record target, so it updates the cached ADS document and the selected file record atomically at the registry layer. Replace the static form layout with internally draggable metadata row widgets; persist their order in GUI configuration.

**Tech Stack:** Python 3.10+, SQLite registry, Click, PySide6, pytest, Ruff.

## Global Constraints

- Keep the existing `files` schema and update flat index columns through `PdfRegistry.upsert_file`.
- File identity, filename, full path, size, modified timestamp, and SHA-256 remain read-only.
- ADS refresh overwrites unlocked ADS fields and preserves locked fields.
- Persist metadata-row order in `GuiConfig`; persist locks in each file's local registry metadata.
- Use `QT_QPA_PLATFORM=offscreen` for Qt tests.

---

### Task 1: Registry metadata replacement and locked ADS merge

**Files:**
- Modify: `src/adsapi/pdf/registry.py`
- Test: `tests/test_pdf_registry.py`

**Interfaces:**
- Produces: `PdfRegistry.replace_file_metadata(root: Path, relative_path: str, metadata: Mapping[str, Any]) -> bool`
- Produces: `merge_ads_metadata(existing: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]`

- [ ] Write tests showing that replacement preserves file identity fields and that locked `title` remains unchanged while an unlocked `doi` is overwritten.
- [ ] Run the targeted tests and confirm the missing APIs fail.
- [ ] Implement replacement by reading the existing record and passing preserved file identity values plus complete metadata to `upsert_file`.
- [ ] Implement the merge policy for ADS document fields; retain `'_adsapi_locked_fields'` unchanged.
- [ ] Run `pytest -q tests/test_pdf_registry.py`.

### Task 2: Single-file ADS refresh command

**Files:**
- Modify: `src/adsapi/pdf/cli.py`
- Test: `tests/test_pdf_metrics_refresh.py`

**Interfaces:**
- Consumes: `PdfRegistry.replace_file_metadata` and `merge_ads_metadata`
- Produces: `adsapi pdf refresh-ads DIRECTORY --bibcode BIBCODE --relative-path PATH`

- [ ] Write a test for the helper that applies an ADS document to one selected record, including a locked field.
- [ ] Run the targeted test and confirm it fails before the helper exists.
- [ ] Add `--relative-path`; require exactly one requested bibcode for this mode and leave the file untouched if ADS has no matching document.
- [ ] Cache the ADS document and metrics as before, then replace only the targeted file metadata when a document exists.
- [ ] Run the targeted CLI tests.

### Task 3: Persisted row order and draggable detail rows

**Files:**
- Modify: `src/adsapi/gui/config.py`
- Modify: `src/adsapi/gui/detail_pane.py`
- Test: `tests/test_gui_config.py`
- Test: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Produces: `GuiConfig.detail_row_order: list[str]`
- Produces: `DetailPane.metadata_changed: Signal(dict)` and `DetailPane.ads_update_requested: Signal(str)`

- [ ] Write configuration round-trip and offscreen detail-pane tests for the default position of `Modified`, `Full path`, and order persistence.
- [ ] Run the targeted tests and confirm the new settings/signals are absent.
- [ ] Replace the static `QFormLayout` rows with draggable custom metadata rows, each containing a label, wrapped/copyable value, edit control where applicable, and a lock control for ADS fields.
- [ ] Remove the copy-time stylesheet mutation; retain tooltip/status feedback so copying cannot alter row geometry.
- [ ] Persist the ordered metadata field keys and reapply the order after every record selection.
- [ ] Run the targeted GUI tests offscreen.

### Task 4: Main-window editing and manual bibcode refresh

**Files:**
- Modify: `src/adsapi/gui/main_window.py`
- Test: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Consumes: `DetailPane.metadata_changed`, `DetailPane.ads_update_requested`, and `PdfRegistry.replace_file_metadata`

- [ ] Write an offscreen test that edits metadata through the detail signal and verifies a registry reload sees it.
- [ ] Run the test and confirm it fails before the connection exists.
- [ ] Connect edits to registry replacement and refresh the selected root.
- [ ] Prompt for a bibcode using the selected record's current bibcode as the initial value; dispatch `refresh-ads` with `--relative-path`.
- [ ] Keep existing job enable/disable and cancellation behavior for the new action.
- [ ] Run targeted GUI tests offscreen.

### Task 5: Documentation and final verification

**Files:**
- Modify: `.handoff/20260801-pyside6-gui-plan.md`
- Modify: `docs/gui.md`

- [ ] Document editable metadata, locks, reordering, and single-file ADS refresh.
- [ ] Add a Terra review note naming the lock persistence and ADS merge policy.
- [ ] Run `ruff format --check`, `ruff check`, full `pytest -q`, and an offscreen GUI smoke test.
