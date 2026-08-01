# Live Appearance Preview Design

**Date:** 2026-08-02  
**Status:** Approved

## Goal

Make the real main window update immediately while the user edits the
**Appearance** dialog. **Cancel** must restore the complete pre-dialog state,
while **OK** keeps the live settings for the existing close-time persistence.

## Current behavior

`AppearanceDialog` owns temporary widget values and only transfers them to
`MainWindow.config` after `dialog.exec()` returns `Accepted`. The dialog also
contains a small table labeled **Preview**. `MainWindow._apply_table_appearance`
already centralizes most table and detail appearance application, but detail
row ordering and visibility are currently applied separately after acceptance.

## Requirements

- Font family, font size, color, enabled state, table/detail order and
  visibility, timestamp format, abstract expansion, label boldness, and row
  heights update the real main window while being edited.
- Cancel and closing the dialog through its window close button restore the
  exact pre-dialog `GuiConfig` and visible table/detail state.
- Accepting the dialog keeps the live settings in the running application.
- Configuration files are not written for individual edits; existing
  close-time persistence remains responsible for saving the accepted state.
- The separate table Preview widget is removed. The real table and detail pane
  are the preview. The timestamp Example label remains because it demonstrates
  arbitrary `strftime` input without duplicating the main table.
- Existing appearance behavior and configuration formats remain compatible.

## Architecture

`AppearanceDialog` remains responsible for collecting and exposing the current
values of its controls. It adds a parameterless `settings_changed` Qt signal.
The signal is emitted for every user-visible mutation, including changes made
through bulk font controls and row drag operations.

`MainWindow._edit_appearance` takes a deep copy of the current `GuiConfig`,
connects `settings_changed` to a helper that transfers the dialog's current
values into the runtime config, and applies the complete appearance state.
The helper is also called once after acceptance to ensure the final values are
captured. On rejection, the saved config snapshot replaces the runtime config
and the same complete application path reapplies it.

The complete application path coordinates:

1. table and detail font/color conversion;
2. timestamp format and abstract behavior;
3. table row height and column layout;
4. detail row height, label style, order, and visibility; and
5. rerendering the current detail selection.

This keeps live preview and rollback on the same code path and prevents the
table/detail panes from drifting apart.

## Signal coverage

The dialog emits `settings_changed` from:

- font enable toggles, font-family changes, size changes, and color selection;
- table and detail visibility item changes;
- table and detail vertical-header section moves;
- timestamp text or preset changes;
- abstract-expansion and label-boldness toggles; and
- table/detail row-height value changes.

Initialization may emit signals before `MainWindow` connects to the dialog;
the initial runtime state is already applied, so those emissions have no
effect. Applying a candidate config changes `MainWindow.config`, not dialog
controls, so it does not create a signal loop.

## Error handling and rollback

All preview application is in-process and uses existing font, timestamp, and
Qt layout behavior. No new external side effects occur while the dialog is
open. A dialog rejection restores the deep-copied config and reapplies every
appearance component, including detail order and visibility. A missed signal
would leave one control non-live; focused tests cover representative controls
and the complete rollback path.

## Verification

Add Qt tests that verify:

- changing an Appearance control emits `settings_changed`;
- a live edit changes the main window before the dialog is accepted;
- rejecting after multiple live edits restores the original config and pane
  state; and
- accepting retains the edited state for close-time persistence.

Run the affected offscreen Qt tests, the full test suite, Ruff formatting, and
Ruff linting before reporting completion.
