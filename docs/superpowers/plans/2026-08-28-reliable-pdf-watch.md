# Reliable Single-PDF Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reliably identify, rename, and catalogue newly arrived PDFs without re-identifying unchanged library files.

**Architecture:** Unify single-file evidence with bulk identification, make file moves catalog-transactional, and turn the watchdog backend into an event queue backed by periodic facts-only reconciliation. Preserve Unicode by default and use explicit transliteration for ASCII output.

**Tech Stack:** Python 3.10+, Click, pypdf, watchdog (optional), SQLite catalog, pytest, macOS launchd documentation.

**Spec:** `docs/superpowers/specs/2026-08-28-reliable-pdf-watch-design.md`

## Global Constraints

- Do not mutate `/Users/ysbach/Dropbox/Studocs/PublOCR` or the live central catalog.
- Do not use fixed retry counts, file-size thresholds, or paper-specific production branches.
- Validate every Bibcode/DOI candidate against ADS before resolution.
- Do not create commits in the dirty workspace unless the user explicitly asks.
- Run Python commands after `source ~/.venvs/ysbpy/bin/activate`.
- Add tests before production changes and observe each regression test fail for the intended reason.

---

### Task 1: Unicode and identification evidence

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/adsapi/pdf/renamer.py`
- Modify: `src/adsapi/pdf/identifier.py`
- Modify: `src/adsapi/pdf/cli.py`
- Test: `tests/test_pdf_renamer.py`
- Test: `tests/test_pdf_identifier.py`

**Interfaces:**
- Produces: `PdfIdentifier.resolve()` with filename/metadata/first-page Bibcode precedence.
- Produces: Unicode-preserving defaults and `Unidecode`-backed ASCII rendering.
- Consumes: existing ADS `query_docs_for_bibcodes()` and `_search_by_title()` methods.

- [ ] Add tests asserting the default rendering keeps `Wesołowski`, explicit ASCII renders `Wesolowski`, publisher boilerplate is skipped, multiline titles are joined, and `resolve()` validates a filename Bibcode before DOI/title fallback.
- [ ] Run the new tests and confirm failures show the old ASCII default, dropped `ł`, wrong banner title, and missing Bibcode query.
- [ ] Add standard-library transliteration for non-decomposable Latin letters and update normalization/defaults.
- [ ] Refactor first-page title candidate extraction and add single-file Bibcode resolution without constructing or trusting unvalidated Bibcodes.
- [ ] Remove the bulk optimization that skips content evidence whenever a filename merely looks like a Bibcode.
- [ ] Run identifier and renamer tests until green.

### Task 2: Transactional tracked moves and single-file rename

**Files:**
- Create: `src/adsapi/pdf/tracked_move.py`
- Modify: `src/adsapi/pdf/identify.py`
- Modify: `src/adsapi/pdf/renamer.py`
- Modify: `src/adsapi/pdf/watcher.py`
- Test: `tests/test_pdf_renamer.py`
- Test: `tests/test_pdf_watcher.py`
- Test: `tests/test_pdf_cli.py`

**Interfaces:**
- Produces: `move_tracked_file(state, source_path, destination_path) -> Path`.
- Produces: `PdfRenamer.rename_tracked_file(...) -> Path` for one resolved PDF.
- Consumes: root-scoped state `directory` and `set_current_filename()`.

- [ ] Add tests proving a successful move updates the catalog path, an update failure rolls the filesystem back, an unresolved move records its destination, and a resolved watched PDF is renamed without batch-renaming the library.
- [ ] Run the tests and confirm failures are caused by missing catalog updates and missing watcher rename behavior.
- [ ] Implement `move_tracked_file` with destination preflight, in-root key derivation, catalog update, and rollback.
- [ ] Use it from unresolved routing when the destination is within the scan root and from watcher unresolved moves.
- [ ] Add single-file rename planning/application to `PdfRenamer` and invoke it from `PdfWatcher.process_new_pdf`.
- [ ] Run move, renamer, watcher, and CLI tests until green.

### Task 3: Readiness and hybrid watchdog reconciliation

**Files:**
- Modify: `src/adsapi/pdf/identifier.py`
- Modify: `src/adsapi/pdf/watcher.py`
- Modify: `src/adsapi/pdf/cli.py`
- Test: `tests/test_pdf_identifier.py`
- Test: `tests/test_pdf_watcher.py`
- Test: `tests/test_pdf_cli.py`

**Interfaces:**
- Produces: `PdfIdentifier.pdf_is_readable(path: Path) -> bool`.
- Produces: `PdfNotReadyError` for parse failure or changing file facts.
- Produces: `watchdog_loop(..., reconciliation_interval: float)`.
- Consumes: existing polling facts `(size, mtime_ns, device, inode)`.

- [ ] Add tests proving unreadable/zero-page PDFs are deferred without catalog mutation, concurrent fact changes defer processing, modified events enqueue work, duplicate events coalesce, and periodic reconciliation finds a missed file.
- [ ] Run the tests and confirm failures identify immediate partial-file processing and synchronous observer callbacks.
- [ ] Implement parser/facts readiness checks and defer without marking unresolved.
- [ ] Extract a facts-only changed-path reconciliation helper shared by polling and watchdog backends.
- [ ] Implement a thread-safe event queue; observer callbacks enqueue only, while the main loop processes and periodically reconciles.
- [ ] Forward `--interval` to watchdog reconciliation and close the state in `finally`.
- [ ] Run watcher and CLI tests until green.

### Task 4: CLI safety and macOS operation documentation

**Files:**
- Modify: `src/adsapi/pdf/cli.py`
- Modify: `README.md`
- Modify: `docs/cli-tutorials/04-pdf-workflows.md`
- Test: `tests/test_pdf_cli.py`

**Interfaces:**
- Consumes: Task 3 watcher reconciliation interval and Task 2 in-root tracked moves.
- Produces: validated in-root `--output-unresolved` behavior for watch mode.

- [ ] Add CLI tests proving relative unresolved paths resolve below the watch root and external destinations are rejected before the loop starts.
- [ ] Run the tests and confirm current path handling fails them.
- [ ] Normalize and validate the watcher unresolved destination and update help text.
- [ ] Document event-driven processing, periodic reconciliation, dry-run/apply behavior, Unicode/ASCII policy, and a concrete user-edited LaunchAgent plist with bootstrap/bootout commands.
- [ ] Run CLI tests until green.

### Task 5: Quality, regression, and review

**Files:**
- Modify only files required by failures introduced by Tasks 1-4.

**Interfaces:**
- Consumes: all prior task outputs.
- Produces: verified implementation and review findings resolved or explicitly reported.

- [ ] Run `source ~/.venvs/ysbpy/bin/activate && isort src/adsapi/pdf tests`.
- [ ] Run `source ~/.venvs/ysbpy/bin/activate && ruff format . && ruff check .`.
- [ ] Run targeted PDF tests.
- [ ] Run the complete pytest suite.
- [ ] Re-read the spec and verify every requirement against code/tests/docs.
- [ ] Request architecture, code, and tester/user-advocate reviews using the persistent review lenses.
- [ ] Fix every Critical/Important finding with a new failing regression test when behavior changes.
- [ ] Re-run formatting, lint, targeted tests, and full pytest before reporting completion.
