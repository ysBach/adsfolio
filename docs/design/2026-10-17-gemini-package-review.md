# Package Review and Visual/Correctness Audit (2026-10-17)

The review checked the existing working tree, reproduced defects, and inspected
GUI renders. The results below describe the changes and their validation limits.

This is the October 17 review record. The subsequent
[prepublication review](../release-readiness.md#prepublication-review-2026-10-19)
documents additional fixes, including DOI punctuation handling and catalog
performance and identity checks.

## Scope and Coverage

This review audited the `adsfolio` package across API/client layers, CLI commands, bibliography/reference/CV tools, the PDF catalog/identification/renaming/watching subsystem, the PySide6 desktop GUI, tests, documentation, and desktop packaging.

- **Reviewed in full:**
  - Core client, configuration, profile, cache, and library sync: `src/adsfolio/client.py`, `src/adsfolio/config.py`, `src/adsfolio/user_profile.py`, `src/adsfolio/pubcache.py`, `src/adsfolio/library_sync.py`
  - API wrappers: `src/adsfolio/api/search.py`, `src/adsfolio/api/export.py`, `src/adsfolio/api/libraries.py`, `src/adsfolio/api/metrics.py`, `src/adsfolio/api/journals.py`
  - Citations, local references, formatting, and BibTeX: `src/adsfolio/citations.py`, `src/adsfolio/references.py`, `src/adsfolio/reference_cli.py`, `src/adsfolio/formatting.py`, `src/adsfolio/journal_utils.py`, `src/adsfolio/bibtex/parser.py`, `src/adsfolio/bibtex/formatter.py`, `src/adsfolio/bibtex/io.py`, `src/adsfolio/bibtex/sanitize.py`
  - CV subsystem: `src/adsfolio/cv/config.py`, `src/adsfolio/cv/config_starter.py`, `src/adsfolio/cv/query.py`, `src/adsfolio/cv/render_factory.py`, `src/adsfolio/cv/render_legend.py`, `src/adsfolio/cv/renderer.py`, `src/adsfolio/cv/cli.py`
  - PDF & catalog subsystem: `src/adsfolio/pdf/catalog.py`, `src/adsfolio/pdf/registry.py`, `src/adsfolio/pdf/identifier.py`, `src/adsfolio/pdf/identify.py`, `src/adsfolio/pdf/identify_settings.py`, `src/adsfolio/pdf/renamer.py`, `src/adsfolio/pdf/rename_settings.py`, `src/adsfolio/pdf/tracked_move.py`, `src/adsfolio/pdf/watcher.py`, `src/adsfolio/pdf/watch_session.py`, `src/adsfolio/pdf/upgrade.py`, `src/adsfolio/pdf/upgrade_utils.py`, `src/adsfolio/pdf/prefetch.py`, `src/adsfolio/pdf/annotations.py`, `src/adsfolio/pdf/obsidian.py`, `src/adsfolio/pdf/sidecar.py`, `src/adsfolio/pdf/cli.py`, `src/adsfolio/pdf/cli_common.py`, `src/adsfolio/pdf/cli_upgrade.py`
  - Desktop GUI & packaging: `src/adsfolio/gui/main_window.py`, `src/adsfolio/gui/detail_pane.py`, `src/adsfolio/gui/notifications.py`, `src/adsfolio/gui/models.py`, `src/adsfolio/gui/library.py`, `src/adsfolio/gui/citation_styles.py`, `src/adsfolio/gui/appearance.py`, `src/adsfolio/gui/dialogs.py`, `src/adsfolio/gui/references_dialog.py`, `src/adsfolio/gui/ads_library_dialog.py`, `src/adsfolio/gui/ads_library_export_dialog.py`, `src/adsfolio/gui/ads_library_jobs.py`, `src/adsfolio/gui/token_dialog.py`, `src/adsfolio/gui/refresh_job.py`, `src/adsfolio/gui/jobs.py`, `src/adsfolio/gui/watch.py`, `src/adsfolio/gui/config.py`, `src/adsfolio/gui/osint.py`, `packaging/adsfolio.spec`, `packaging/desktop_entry.py`, `packaging/smoke_entry.py`, `packaging/check_macos_bundle.py`, `packaging/hooks/hook-nltk.py`

## Findings and Implemented Fixes

1. **GUI DetailPane Layout Clipping, Row Reorder Cache Bug, and Default Splitter Proportions (`src/adsfolio/gui/detail_pane.py`, `src/adsfolio/gui/main_window.py`)**
   - *Finding:* On first launch without saved splitter sizes, `QSplitter` allocated ~38% of the window width to the left sidebar and compressed the table and detail pane. Inside `DetailPane`, `MetadataRowsContainer` did not report its `heightForWidth` in `minimumSizeHint()`, causing `QScrollArea` to squish wrapped multi-line fields (`Title`, `Authors`, `Journal`, `DOI`, `Full path`, `Abstract`) to a single line and clip the right-hand `✎`/`🔒` buttons under the vertical scrollbar. In addition, `DetailPane.move_row()` removed the layout item with `takeAt(source)` before re-inserting the widget, leaving a stale layout-item height-for-width cache so subsequent abstract expand/collapse toggles kept the collapsed 79 px row height. Clearing rows with `deleteLater()` alone also left uncollected widgets visible at `(0, 0)` during synchronous re-renders.
   - *Fix:* Propagated `heightForWidth` through `MetadataRowsContainer` and `MetadataRow` size hints with `minimumWidth = 0` so rows always fit the scroll viewport width and expand vertically to their wrapped height; replaced `takeAt(source)` with `removeWidget(widget)` in `move_row()`; hid and unparented removed row widgets before `deleteLater()`; and configured stretch factors `(0, 1, 0)` plus default first-launch splitter sizes `[220, 700, 360]` only when `config.splitter_sizes` is empty.

2. **Stale DetailPane State After Model Reset / File Deletion / Empty View (`src/adsfolio/gui/main_window.py`)**
   - *Finding:* When the selected file was externally deleted and `Refresh` (`F5`) completed, or when a search/view filter or root switch emptied the table, `FileTableModel` reset without `QTreeView` emitting `currentChanged`, leaving stale metadata and active buttons in `DetailPane` and leaving single-record ADS refresh actions enabled.
   - *Fix:* Connected `model.modelReset` and `selectionModel().selectionChanged` to `_sync_detail_and_selection_actions()`, ensuring stale details and selection-dependent actions clear whenever the current index becomes invalid while preserving multi-selection semantics and unrelated-root refreshes. Added single-row ADS refresh and identifier update actions to the table context menu.

3. **Mnemonic Ampersand Rendering in `NoticeBar` (`src/adsfolio/gui/notifications.py`)**
   - *Finding:* `QPushButton` interpreted the single `&` in `"Identify & Rename"` and `"Identify & Rename All"` as a keyboard mnemonic shortcut, rendering `_Rename`.
   - *Fix:* Escaped `&` as `&&` when setting button labels in `NoticeBar` while keeping `NoticeAction.IDENTIFY_RENAME.value == "Identify & Rename"` intact.

4. **Dialog Visual Polish (`src/adsfolio/gui/appearance.py`, `src/adsfolio/gui/citation_styles.py`)**
   - *Finding:* `AppearanceDialog` compressed the `Font` combo column while leaving empty horizontal space to the right, and `CitationStylesDialog` scrolled long template strings to the end of the `QLineEdit` on load, hiding the opening `{`.
   - *Fix:* Set `QHeaderView.ResizeMode.Stretch` on the `Font` column in `AppearanceDialog` tables and called `home(False)` after populating `QLineEdit` fields in `CitationStylesDialog`.

5. **DOI Resolver URLs in Update from ADS / `--identifier` (`src/adsfolio/pdf/upgrade_utils.py`, `src/adsfolio/pdf/cli.py`)**
   - *Finding:* `_parse_ads_identifier()` and `_normalize_doi()` did not recognize `https://doi.org/...` or `http://dx.doi.org/...` resolver URLs, misclassifying them as Bibcodes.
   - *Fix:* Parsed HTTP(S) `doi.org` and `dx.doi.org` URLs via `urllib.parse.urlsplit` (guarding malformed URLs) and single `unquote` on the URL path, preserving valid DOI path punctuation such as closing parentheses on resolver URLs while keeping existing bare-DOI normalization unchanged.

6. **`CitationStyle` Constructor Validation (`src/adsfolio/citations.py`)**
   - *Finding:* `CitationStyle.__post_init__()` accepted `bool` values for `max_authors` and `authors_when_truncated`, accepted non-strings for `author_separator` and `author_truncation_suffix`, and raised `TypeError` on unhashable `author_format` inputs.
   - *Fix:* Added explicit type checks raising `CitationStyleError` in `__post_init__()` and guarded `bool` inputs in `from_mapping()`.

7. **`SearchAPI.query_all()` Empty-Page Completeness Check (`src/adsfolio/api/search.py`)**
   - *Finding:* `query_all()` exited silently with partial results if ADS returned an empty page while `start < target`.
   - *Fix:* Raised `ADSError` when `page.docs` is empty and `start < target`, while allowing zero-match queries, `max_records` limits, and updated `page.num_found` counts.

8. **Legacy BibTeX Parser (`src/adsfolio/bibtex/parser.py`)**
   - *Finding:* `parse_bibtex_entries()` used a line-anchored non-greedy regex that failed on single-line entries, nested braces, and multiline fields, and extracted author tokens naively.
   - *Fix:* Replaced the regex parser with `BibTexParser` and `pyparsing.Located(parser._expr.entry)` inside `main_expression` (with `parse_with_tabs()` enabled) to preserve exact `BibEntry.raw` slices (including tabs), resolve chained `@string` macros with cycle detection, ignore `@comment`/implicit comments, and extract surnames (including braced ADS names and full braced corporate authors containing commas).

## Validation and Render Evidence

- **Final independent verification:** 927 tests passed and one was skipped on
  the local Python 3.13 environment. Another 23 acceptance checks outside the
  repository passed. A separate real `refresh-ads` command check verified the
  complete DOI query and catalog update with a mocked ADS response and an
  isolated temporary library.
- **Reviewer corrections:** malformed DOI URL fallback, citation mapping type
  guards, tab-preserving raw BibTeX, literal corporate names, chained/cyclic
  macros, narrow-pane geometry, and single-DOI refresh without a second
  normalization were verified after revision.
- **Distribution verification:** source distribution and wheel builds passed;
  the final wheel installed into an isolated environment without GUI extras.
  CLI help/version, the missing-GUI installation message, offline catalog
  operations, and parser/identifier checks passed outside the checkout.
- **Broader lint status:** the blocking lint gate passes. Full Ruff findings
  decreased from 120 at this review's baseline to 119, with no additional
  findings; existing lint debt remains.
- **Automated test suite:** `UV_CACHE_DIR=/tmp/adsfolio-uv-cache QT_QPA_PLATFORM=offscreen uv run --no-sync pytest -q` passed across all test modules.
- **Lint & formatting gates:**
  - `uv run --no-sync ruff check --select E4,E7,E9,F src tests packaging`
  - `uv run --no-sync ruff format --check src tests packaging`
  - `uv run --no-sync isort --check-only src tests packaging`
- **Documentation build:** `QT_QPA_PLATFORM=offscreen uv run --no-sync sphinx-build -n -W --keep-going -b html docs /tmp/adsfolio-docs-build`
- **Offscreen GUI renders (`/tmp/adsfolio-audit-renders/`):**
  - Baseline renders: `01_main_empty_1280x800.png` through `09_citation_styles_dialog.png`
  - Post-patch renders: `after_01_main_1280x800.png`, `after_02_main_expanded_abstract_1280x800.png`, `after_03_main_compact_960x620.png`, `after_04_main_no_action_btns_960x620.png`, `after_05_main_notice_1280x800.png`, `after_06_appearance_dialog.png`, `after_07_citation_styles_dialog.png`
- **Validation gaps:** GUI verification used `QT_QPA_PLATFORM=offscreen` with isolated temporary catalogs/configs; interactive window-manager behavior on native macOS/Windows/Linux desktops and live network mutations against ADS were not exercised.

## Maintainability

The patches retain the existing CLI, domain-service, catalog, and GUI boundaries
and add no dependencies. Saved appearance settings and local history remain
covered by regression tests. Raw BibTeX capture uses `BibTexParser._expr`, a
private interface within the existing `bibtexparser<2` dependency range;
dependency upgrades must rerun the raw-preservation and macro tests.

## Residual Findings Out of Scope for This Patch

- **First-page title heuristic on historical scans (`src/adsfolio/pdf/identifier.py`):** Typography/layout-based title extraction remains unimplemented pending an explicit user decision on title-extraction heuristics; oversized title queries are safely caught via `RequestTooLongError`.
- **Concurrent `requests.Session` usage in `ADSClient.download_pdfs()` (`src/adsfolio/client.py`):** `download_pdfs()` shares `self._base._session` across `ThreadPoolExecutor` workers when `max_workers > 1`, which is a potential thread-safety concern for `requests.Session` state.
