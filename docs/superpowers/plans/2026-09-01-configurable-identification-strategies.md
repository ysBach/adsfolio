# Configurable PDF Identification Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace filename-only inference with one configurable ordered identification policy shared by bulk Identify, Rename, and watch.

**Architecture:** `IdentifySettings` owns an ordered tuple of stable strategy IDs. `PdfIdentifier.resolve_many()` executes those stages over the unresolved subset, preserving order while batching and deduplicating ADS lookups; `resolve()` delegates to the same engine. The GUI edits the ordered policy and separates “Save Defaults” from “Run Identify,” while CLI, workflow, and watch pass the same policy end to end.

**Tech Stack:** Python 3.11+, dataclasses, Click, PySide6, pypdf, pytest, pytest-qt, Ruff

**Spec:** `docs/superpowers/specs/2026-09-01-configurable-identification-strategies-design.md`

## Global Constraints

- Breaking compatibility is accepted: remove `infer_from_filenames`, `infer_mode`, and `--infer/--no-infer`.
- The recommended strategy order is exactly the nine IDs listed in the spec.
- A strategy miss continues; ADS authentication, transport, response, and rate-limit failures remain visible failures.
- ADS validates every accepted record; extraction alone never resolves a PDF.
- Bulk, Rename, and watch use the same resolver policy.
- Migration never rescans existing files automatically.
- Use `source ~/.venvs/ysbpy/bin/activate` for tests and `uv run --active` for project tools.
- Preserve unrelated dirty-worktree changes and do not commit unrelated files.

---

### Task 1: Canonical strategy catalog and settings migration

**Files:**
- Modify: `src/adsfolio/pdf/identify_settings.py`
- Modify: `src/adsfolio/config.py`
- Test: `tests/test_pdf_identify_settings.py`
- Test: `tests/test_config_setup.py`
- Test: `tests/test_pdf_sidecar.py`

**Interfaces:**
- Produces: `IdentifyStrategy(str, Enum)` with the nine stable values.
- Produces: `RECOMMENDED_IDENTIFY_STRATEGIES: tuple[IdentifyStrategy, ...]`.
- Produces: `IdentifySettings.strategies: tuple[IdentifyStrategy, ...]`.
- Produces: `IdentifySettings.with_overrides(strategies=...)`.

- [x] **Step 1: Write failing settings and migration tests**

```python
def test_identify_settings_roundtrips_ordered_strategies():
    settings = IdentifySettings.from_mapping(
        {"strategies": ["first_page_doi", "filename_bibcode"]}
    )
    assert settings.strategies == (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )
    assert settings.to_mapping()["strategies"] == [
        "first_page_doi",
        "filename_bibcode",
    ]


def test_legacy_infer_true_migrates_to_recommended_policy():
    settings = IdentifySettings.from_mapping({"infer_from_filenames": True})
    assert settings.strategies == RECOMMENDED_IDENTIFY_STRATEGIES
    assert "infer_from_filenames" not in settings.to_mapping()


def test_identify_settings_rejects_duplicate_explicit_strategies():
    with pytest.raises(ValueError, match="duplicate identification strategy"):
        IdentifySettings(strategies=(
            IdentifyStrategy.FILENAME_DOI,
            IdentifyStrategy.FILENAME_DOI,
        ))
```

- [x] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_identify_settings.py tests/test_config_setup.py tests/test_pdf_sidecar.py -q
```

Expected: failures because the enum and `strategies` field do not exist.

- [x] **Step 3: Implement the catalog and canonical validation**

Add an enum and default tuple to `identify_settings.py`:

```python
class IdentifyStrategy(str, Enum):
    FILENAME_BIBCODE = "filename_bibcode"
    FILENAME_DOI = "filename_doi"
    PDF_METADATA_BIBCODE = "pdf_metadata_bibcode"
    PDF_METADATA_DOI = "pdf_metadata_doi"
    FIRST_PAGE_BIBCODE = "first_page_bibcode"
    FIRST_PAGE_DOI = "first_page_doi"
    STRUCTURED_FILENAME = "structured_filename"
    PDF_METADATA_TITLE = "pdf_metadata_title"
    FIRST_PAGE_TITLE = "first_page_title"


RECOMMENDED_IDENTIFY_STRATEGIES = tuple(IdentifyStrategy)
```

Normalize persisted lists to enum tuples, remove duplicate/unknown persisted
values with warnings, and fall back to the recommended tuple when nothing valid
remains. Validate direct construction in `__post_init__` so GUI/CLI callers
cannot create an empty, duplicate, or unknown policy. Remove
`infer_from_filenames` from `_IDENTIFY_FLAT_KEYS` and all canonical writes.

- [x] **Step 4: Run focused tests and formatting**

```bash
uv run --active isort src/adsfolio/pdf/identify_settings.py tests/test_pdf_identify_settings.py
uv run --active ruff format src/adsfolio/pdf/identify_settings.py tests/test_pdf_identify_settings.py
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_identify_settings.py tests/test_config_setup.py tests/test_pdf_sidecar.py -q
```

Expected: all focused tests pass.

---

### Task 2: Ordered, batched resolver engine

**Files:**
- Modify: `src/adsfolio/pdf/identifier.py`
- Test: `tests/test_pdf_identifier.py`

**Interfaces:**
- Consumes: `tuple[IdentifyStrategy, ...]`.
- Produces: `PdfIdentifier.resolve_many(pdf_paths, *, strategies, ...)`.
- Produces: `PdfIdentifier.resolve(pdf_path, *, strategies, ...)` delegating to `resolve_many`.
- Produces: `resolution_strategy` in successful records.

- [x] **Step 1: Write priority, disabling, fallthrough, and deduplication tests**

```python
def test_resolve_many_obeys_configured_strategy_order(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier({
        'doi:"10.1/from-file"': SearchResult(
            num_found=1, start=0,
            docs=[{"bibcode": "FILE", "doi": ["10.1/from-file"]}],
        ),
        'doi:"10.1/from-pdf"': SearchResult(
            num_found=1, start=0,
            docs=[{"bibcode": "PDF", "doi": ["10.1/from-pdf"]}],
        ),
    })
    monkeypatch.setattr(identifier, "extract_doi_from_filename", lambda _: "10.1/from-file")
    monkeypatch.setattr(identifier, "extract_doi_from_metadata", lambda _: "10.1/from-pdf")
    result = identifier.resolve_many(
        [path],
        strategies=(IdentifyStrategy.PDF_METADATA_DOI, IdentifyStrategy.FILENAME_DOI),
    )[path]
    assert result["bibcode"] == "PDF"
    assert result["resolution_strategy"] == "pdf_metadata_doi"
    assert len(search.calls) == 1


def test_missing_filename_candidate_falls_through_to_first_page_doi(
    tmp_path, monkeypatch
):
    path = tmp_path / "aa43983-22.pdf"
    path.write_bytes(b"%PDF")
    doi = "10.1051/0004-6361/202243983"
    query = f'doi:"{doi}"'
    identifier, search = _build_identifier(
        {
            query: SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "2022A&A...668A.132B", "doi": [doi]}],
            )
        }
    )
    monkeypatch.setattr(identifier, "extract_doi_from_filename", lambda _: None)
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: doi)
    result = identifier.resolve_many(
        [path],
        strategies=(IdentifyStrategy.FILENAME_DOI, IdentifyStrategy.FIRST_PAGE_DOI),
    )[path]
    assert result["bibcode"] == "2022A&A...668A.132B"
    assert result["resolution_strategy"] == "first_page_doi"
    assert [call["q"] for call in search.calls] == [query]


def test_ambiguous_doi_falls_through_to_next_strategy(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier(
        {
            'doi:"10.1/ambiguous"': SearchResult(
                num_found=2,
                start=0,
                docs=[
                    {"bibcode": "A", "doi": ["10.1/ambiguous"]},
                    {"bibcode": "B", "doi": ["10.1/ambiguous"]},
                ],
            ),
            'doi:"10.1/good"': SearchResult(
                num_found=1,
                start=0,
                docs=[{"bibcode": "GOOD", "doi": ["10.1/good"]}],
            ),
        }
    )
    monkeypatch.setattr(
        identifier, "extract_doi_from_filename", lambda _: "10.1/ambiguous"
    )
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: "10.1/good")
    result = identifier.resolve_many(
        [path],
        strategies=(IdentifyStrategy.FILENAME_DOI, IdentifyStrategy.FIRST_PAGE_DOI),
    )[path]
    assert result["bibcode"] == "GOOD"
    assert [call["q"] for call in search.calls] == [
        'doi:"10.1/ambiguous"',
        'doi:"10.1/good"',
    ]


def test_equal_candidate_is_queried_once_across_strategies(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier, search = _build_identifier({})
    monkeypatch.setattr(
        identifier, "extract_doi_from_metadata", lambda _: "10.1/same"
    )
    monkeypatch.setattr(identifier, "extract_doi_from_text", lambda _: "10.1/same")
    result = identifier.resolve_many(
        [path],
        strategies=(
            IdentifyStrategy.PDF_METADATA_DOI,
            IdentifyStrategy.FIRST_PAGE_DOI,
        ),
    )[path]
    assert result is None
    assert [call["q"] for call in search.calls] == ['doi:"10.1/same"']
```

- [x] **Step 2: Run the resolver tests and verify they fail**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_identifier.py -q
```

Expected: signature/behavior failures around `strategies` and stage order.

- [x] **Step 3: Implement stage descriptors and the unresolved-subset loop**

Use one private dispatch table keyed by `IdentifyStrategy`; do not add nine
public wrapper classes. Each stage returns candidates for unresolved paths and
one resolver kind (`bibcode`, `doi`, `structured`, or `title`). Execute:

```python
pending = list(ordered_paths)
candidate_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
for strategy in strategies:
    candidates = self._extract_strategy_candidates(strategy, pending, journal_map)
    stage_records, pending = self._resolve_strategy_candidates(
        strategy,
        pending,
        candidates,
        candidate_cache=candidate_cache,
        sha256_by_path=sha256_by_path,
        ads_documents_callback=ads_documents_callback,
    )
    for path, record in stage_records.items():
        record["resolution_strategy"] = strategy.value
        results[path] = record
```

Use `query_docs_for_bibcodes`, `query_docs_for_dois`, and
`_query_docs_for_filename_hints` for batch-compatible stages. Cache lookup
results by normalized candidate kind/value, including misses. Query title
candidates once per normalized title. Require normalized title equality after
ADS returns one document. Never catch `ADSError`/`RateLimitError` as a miss.

- [x] **Step 4: Make `resolve()` delegate to the canonical engine**

```python
def resolve(self, pdf_path: Path, *, strategies=RECOMMENDED_IDENTIFY_STRATEGIES, **kwargs):
    path = Path(pdf_path)
    return self.resolve_many([path], strategies=strategies, **kwargs)[path]
```

Remove `infer_only`, `include_title_fallback`, preferred-candidate arguments,
and the old hard-coded resolution loops after callers are migrated.

- [x] **Step 5: Run resolver tests and quality tools**

```bash
uv run --active isort src/adsfolio/pdf/identifier.py tests/test_pdf_identifier.py
uv run --active ruff format src/adsfolio/pdf/identifier.py tests/test_pdf_identifier.py
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_identifier.py -q
```

Expected: all resolver tests pass.

---

### Task 3: Integrate cache, query estimation, and Identify workflow

**Files:**
- Modify: `src/adsfolio/pdf/identify.py`
- Modify: `src/adsfolio/pdf/cli.py`
- Test: `tests/test_pdf_cli.py`

**Interfaces:**
- Consumes: `IdentifySettings.strategies`.
- Produces: `run_identify(..., strategies: tuple[IdentifyStrategy, ...], ...)`.
- Produces: strategy-aware ADS estimate and cache-document callback.

- [x] **Step 1: Write workflow tests proving cache does not override priority**

```python
def test_identify_command_passes_saved_strategy_order(tmp_path, monkeypatch):
    captured = {}
    settings = IdentifySettings(
        strategies=(
            IdentifyStrategy.FIRST_PAGE_DOI,
            IdentifyStrategy.FILENAME_BIBCODE,
        )
    )
    monkeypatch.setattr(Config, "load_identify_settings", lambda: settings)
    monkeypatch.setattr(
        "adsfolio.pdf.cli.run_identify",
        lambda client, directory, *, helpers, **options: captured.update(options),
    )
    result = CliRunner().invoke(pdf, ["identify", str(tmp_path), "--allow-ads"])
    assert result.exit_code == 0
    assert captured["strategies"] == settings.strategies


def test_estimator_does_not_extract_disabled_strategies(tmp_path, monkeypatch):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    identifier = PdfIdentifier(SimpleNamespace())
    monkeypatch.setattr(
        identifier,
        "extract_doi_from_metadata",
        lambda _: (_ for _ in ()).throw(AssertionError("disabled strategy ran")),
    )
    monkeypatch.setattr(identifier, "extract_doi_from_filename", lambda _: "10.1/x")
    analysis = _estimate_ads_queries_for_targets(
        identifier,
        [path],
        strategies=(IdentifyStrategy.FILENAME_DOI,),
    )
    assert analysis["filename_doi_files"] == 1
    assert analysis["metadata_doi_files"] == 0
```

Build the second test with a cache document for one DOI and two enabled DOI
strategies returning different candidates. Assert the earlier configured
candidate wins even when only the later candidate exists in the profile cache.

- [x] **Step 2: Run focused tests and verify they fail**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_cli.py -q
```

- [x] **Step 3: Replace prefetch-first matching with cache-backed candidate lookup**

Load/refresh profile documents as before, but index/cache them for candidate
resolution rather than resolving pending PDFs before the strategy engine.
Remove `_prefetch_match_records()` as a separate first phase. Preserve registry
ADS-document reuse and callback writes. Pass `strategies` directly to
`resolve_many` and update progress text to name the configured stages.

Refactor `_estimate_ads_queries_for_targets` to accept `strategies`; extract and
count only enabled stages in their order. The estimator may conservatively count
one title query per distinct enabled title candidate. Remove `infer_mode`.

- [x] **Step 4: Preserve explicit failures and unresolved behavior**

Keep rate-limit handling at the operation boundary. Do not transform auth,
transport, or malformed-response exceptions into per-stage misses. Continue to
write unresolved records only when the resolver completed normally with no
match.

- [x] **Step 5: Run workflow tests and quality tools**

```bash
uv run --active isort src/adsfolio/pdf/identify.py src/adsfolio/pdf/cli.py tests/test_pdf_cli.py
uv run --active ruff format src/adsfolio/pdf/identify.py src/adsfolio/pdf/cli.py tests/test_pdf_cli.py
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_cli.py -q
```

---

### Task 4: CLI, Rename, and watch policy parity

**Files:**
- Modify: `src/adsfolio/pdf/cli.py`
- Modify: `src/adsfolio/pdf/watcher.py`
- Test: `tests/test_pdf_cli.py`
- Test: `tests/test_pdf_watcher.py`
- Test: `tests/test_pdf_watch_session.py`

**Interfaces:**
- Produces: repeatable CLI `--strategy ID` preserving occurrence order.
- Consumes: the same `IdentifySettings` in Rename and watch.

- [x] **Step 1: Write failing CLI and watcher parity tests**

```python
def test_identify_cli_preserves_repeated_strategy_order(runner, monkeypatch, tmp_path):
    result = runner.invoke(
        pdf,
        ["identify", str(tmp_path), "--strategy", "first_page_doi",
         "--strategy", "filename_bibcode", "--allow-ads"],
    )
    assert result.exit_code == 0
    assert captured["strategies"] == (
        IdentifyStrategy.FIRST_PAGE_DOI,
        IdentifyStrategy.FILENAME_BIBCODE,
    )


def test_watcher_passes_identify_strategy_policy(monkeypatch, tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF")
    settings = IdentifySettings(strategies=(IdentifyStrategy.FIRST_PAGE_DOI,))
    state = _state(monkeypatch, tmp_path)
    identifier = _FilenameInferenceIdentifier({"bibcode": "B1"})
    watcher = PdfWatcher(identifier, _Renamer())
    watcher.process_new_pdf(path, state, strategies=settings.strategies)
    assert identifier.resolve_many_calls[0][2]["strategies"] == settings.strategies
    state.close()
```

- [x] **Step 2: Run focused tests and verify they fail**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_cli.py tests/test_pdf_watcher.py tests/test_pdf_watch_session.py -q
```

- [x] **Step 3: Replace CLI infer option with ordered strategies**

```python
@click.option(
    "--strategy",
    "strategy_ids",
    multiple=True,
    type=click.Choice([strategy.value for strategy in IdentifyStrategy]),
    help="Enabled identification strategy; repeat in priority order.",
)
```

When no option occurs, use saved settings. When present, convert every value to
`IdentifyStrategy` and use the tuple as the complete one-run override. Remove
all `infer_mode` forwarding. Rename’s pre-identify call loads the effective
Identify settings and passes their strategies.

- [x] **Step 4: Route watch through `resolve_many([path])`**

Remove `infer_from_filenames` from `process_new_pdf`. Add `strategies` and call
the canonical resolver for every unresolved SHA. Keep the existing guard that
does not retry an unchanged resolved record and does retry an unchanged
unresolved record. Do not add startup scans to watch.

- [x] **Step 5: Run focused tests and quality tools**

```bash
uv run --active isort src/adsfolio/pdf/cli.py src/adsfolio/pdf/watcher.py tests/test_pdf_cli.py tests/test_pdf_watcher.py
uv run --active ruff format src/adsfolio/pdf/cli.py src/adsfolio/pdf/watcher.py tests/test_pdf_cli.py tests/test_pdf_watcher.py
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_cli.py tests/test_pdf_watcher.py tests/test_pdf_watch_session.py -q
```

---

### Task 5: Ordered strategy editor and explicit dialog actions

**Files:**
- Modify: `src/adsfolio/gui/dialogs.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Test: `tests/test_gui_dialogs.py`
- Test: `tests/test_gui_main_window_watch.py`

**Interfaces:**
- Produces: `IdentifyDialog.selected_settings()` with ordered enabled strategies.
- Produces: `IdentifyDialog.save_defaults()` without job execution.
- Produces: `IdentifyDialog.run_identify()` without persistence.

- [x] **Step 1: Write failing GUI tests**

```python
def test_identify_dialog_reorders_and_disables_strategies(application):
    dialog = IdentifyDialog(Path("/library"), IdentifySettings())
    dialog.strategy_list.setCurrentRow(5)
    dialog.move_strategy_up()
    dialog.strategy_list.item(0).setCheckState(Qt.Unchecked)
    settings = dialog.selected_settings()
    assert settings.strategies[4] is IdentifyStrategy.FIRST_PAGE_DOI
    assert IdentifyStrategy.FILENAME_BIBCODE not in settings.strategies


def test_identify_save_defaults_does_not_start_job(application, monkeypatch):
    saved = []
    started = []
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(),
        save_settings=lambda scope, settings: saved.append((scope, settings)),
    )
    monkeypatch.setattr(dialog, "start_job", lambda: started.append(True))
    dialog.save_defaults()
    assert saved == [("global", dialog.selected_settings())]
    assert started == []


def test_identify_run_does_not_save_defaults(application, monkeypatch):
    saved = []
    started = []
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(),
        save_settings=lambda scope, settings: saved.append((scope, settings)),
    )
    monkeypatch.setattr(dialog, "start_job", lambda: started.append(True))
    dialog.run_identify()
    assert saved == []
    assert started == [True]


def test_reset_strategies_restores_recommended_policy(application):
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(strategies=(IdentifyStrategy.FIRST_PAGE_DOI,)),
    )
    dialog.reset_strategies()
    assert dialog.selected_settings().strategies == RECOMMENDED_IDENTIFY_STRATEGIES
```

Use `qtbot.mouseClick` and spies/call lists for the save callback and
`job_started` signal. Assert exactly one side effect for each button.

- [x] **Step 2: Run GUI tests and verify they fail**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_gui_dialogs.py tests/test_gui_main_window_watch.py -q
```

- [x] **Step 3: Implement the ordered checklist**

Use a `QListWidget` with one checkable item per catalog strategy, stable ID in
`Qt.UserRole`, internal move enabled, and explicit Up/Down buttons. Add “Reset to
Recommended.” Reconstruct the enabled tuple from row order. Reject an empty
selection with a visible message rather than running a no-op operation.

- [x] **Step 4: Separate reusable and one-run controls**

Replace the OK button with `Save Defaults`, `Run Identify`, and `Cancel`.
`Save Defaults` invokes only `_save_settings(scope, selected_settings())` and
keeps the dialog open. `Run Identify` builds CLI arguments from all current
controls, including repeatable `--strategy`, and starts the job without calling
the save callback.

Add one-run “Show PDF parser warnings” and AI CSV path/chooser controls. Keep
refresh cache, allow ADS, and apply moves one-run-only. Ensure scope changes load
only reusable controls and never reset one-run choices.

- [x] **Step 5: Run GUI tests and quality tools**

```bash
uv run --active isort src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_dialogs.py
uv run --active ruff format src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_dialogs.py
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_gui_dialogs.py tests/test_gui_main_window_watch.py -q
```

---

### Task 6: Real-file regression, documentation, and full verification

**Files:**
- Modify: `tests/test_pdf_identifier.py`
- Modify: `tests/test_pdf_cli.py`
- Modify: `docs/gui.md`
- Modify: `docs/cli-tutorials/04-pdf-workflows.md`
- Modify: `docs/cli-tutorials/05-operations-and-troubleshooting.md`

**Interfaces:**
- Verifies: `aa43983-22.pdf` class of filenames reaches PDF DOI strategies.
- Documents: strategy meanings, order, migration, one-run repair, and watch scope.

- [x] **Step 1: Add deterministic aa43983 regression coverage**

Create a small pypdf fixture in `tmp_path` whose metadata and first-page text
contain `10.1051/0004-6361/202243983`, name it `aa43983-22.pdf`, stub ADS to
return Bibcode `2022A&A...668A.132B`, and assert the recommended policy resolves
through `pdf_metadata_doi`. Add a second test with metadata DOI disabled to
prove `first_page_doi` resolves it.

- [x] **Step 2: Document the replacement behavior**

Document:

- filename evidence is optional and falls through;
- strategy rows are independently enabled and reordered;
- recommended exact-to-fuzzy order;
- Save Defaults versus Run Identify;
- how to include the unresolved directory for one manual repair run;
- watch applies only to files added after activation;
- `--strategy` replaces removed `--infer`.

- [x] **Step 3: Scan for obsolete compatibility surface**

```bash
rg -n "infer_from_filenames|infer_mode|--infer|Infer from filenames" src tests docs
```

Expected: no runtime, test, or user-documentation references. Historical design
documents may retain the old term as historical context.

- [x] **Step 4: Run changed-file formatting and lint**

```bash
uv run --active isort src/adsfolio/pdf/identify_settings.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/watcher.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py tests/test_pdf_identify_settings.py tests/test_pdf_identifier.py tests/test_pdf_cli.py tests/test_pdf_watcher.py tests/test_gui_dialogs.py
uv run --active ruff format src/adsfolio/pdf/identify_settings.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/watcher.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py tests/test_pdf_identify_settings.py tests/test_pdf_identifier.py tests/test_pdf_cli.py tests/test_pdf_watcher.py tests/test_gui_dialogs.py
uv run --active ruff check src/adsfolio/pdf/identify_settings.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/watcher.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py tests/test_pdf_identify_settings.py tests/test_pdf_identifier.py tests/test_pdf_cli.py tests/test_pdf_watcher.py tests/test_gui_dialogs.py
```

Expected: all changed files pass.

- [x] **Step 5: Run the complete suite**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest -q
```

Expected: all tests pass.

- [x] **Step 6: Manually verify the supplied PDF read-only**

Run the resolver against
`/Users/ysbach/Dropbox/Studocs/PublOCR/unresolved/aa43983-22.pdf` with no rename or
move application and assert the returned Bibcode is `2022A&A...668A.132B` and
the recorded strategy is `pdf_metadata_doi` or `first_page_doi`. Do not alter the
source PDF during this verification.
