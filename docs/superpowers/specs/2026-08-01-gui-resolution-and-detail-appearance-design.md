# GUI resolution and detail appearance design

## Scope

Refine the existing PySide6 PDF browser in four focused ways:

1. Replace the main-table `Status` column with a compact resolution marker.
2. Consolidate detail-pane appearance, row visibility, and row ordering into the
   existing appearance dialog.
3. Make long abstracts usable without consuming the entire detail pane.
4. Remove `Source` from the GUI.

The implementation must remain additive to the existing registry schema except
for the GUI presentation changes described here.

## Decisions

### Resolution marker

The `status` table column remains a sortable model column but has an empty
header and a narrow fixed default width. A delegate draws a green dot only when
the canonical resolved predicate passes: a record has a non-empty bibcode and
its status is not `unresolved`. Other rows draw no dot.

The column retains semantic text through a cell tooltip and accessible text:
`Resolved` or `Needs attention`. Sorting this column orders needs-attention
records before resolved records in ascending order, matching its manual-review
purpose. The SQL ordering expression must use the same resolved condition as
the display predicate; it must not depend solely on the stored status string.

### Appearance dialog

Rename **Table Appearance** to **Appearance** and remove the separate
**Detail Rows** command. The dialog has three tabs:

- **Table:** existing per-column typography controls and table preview.
- **Details:** one shared detail-pane font and color control, plus a reorderable
  checked list of metadata rows. Check state controls visibility; list order is
  the displayed row order.
- **Time Format:** existing timestamp controls.

The existing in-pane drag handle remains a quick reorder mechanism. Both the
dialog and drag action write the same `detail_row_order` preference. Details
typography remains a single shared setting rather than per-field styling, which
avoids a large preference surface without a demonstrated use case.

### Abstract disclosure

The detail pane presents an abstract in a collapsed, multi-line preview. An
inline disclosure control expands it to its complete wrapped text and collapses
it again. This replaces truncation while preserving normal pane scrolling.
The abstract remains an ADS metadata field: its existing editing and locking
semantics are unchanged.

### Source removal

Remove `Source` from every GUI presentation surface: the main table, detail
pane, appearance dialog, and GUI documentation. Existing GUI configuration
values for the obsolete key are ignored safely.

The persisted `source` metadata, the `arXiv only` view, and the upgrade
workflow are intentionally unchanged. They rely on this internal classification
to determine preprint records and whether a published upgrade has completed.
Replacing it with a persisted boolean `preprint` would be a separate migration
with its own compatibility plan.

## Data flow and compatibility

`PdfRegistry` continues to return the `status`, `bibcode`, and `source` values
it stores. The table model uses `status` and `bibcode` to render and sort the
resolution marker. The UI never renders `source`; internal filters and upgrade
code still do.

Existing `gui.json` files require no migration. Unknown/stale `source` entries
in `column_fonts`, `detail_row_order`, or `detail_hidden_rows` are ignored when
the available GUI fields are constructed. Other saved preferences retain their
current behavior.

## Error handling and accessibility

The marker must not convey its only meaning through color. Its tooltip and
accessible text provide the resolved state. Missing or malformed record fields
are treated as needs-attention, not as resolved.

The abstract disclosure must be safe for empty, scalar, and malformed values:
it renders a blank value without a disclosure control when there is no
meaningful text. It must not change the copied text or stored metadata.

## Verification

Add focused tests for:

- resolution marker display, tooltip/accessibility semantics, and SQL sort
  ordering;
- the appearance dialog's persisted detail visibility/order and shared detail
  font/color settings;
- abstract collapsed/expanded rendering with long, empty, and scalar values;
- absence of `source` from `COLUMNS`, the available detail rows, and GUI docs;
- loading legacy GUI configuration that contains obsolete `source` preferences.

Run the affected Qt tests offscreen, registry tests, formatting/lint checks, and
the full test suite before completion.
