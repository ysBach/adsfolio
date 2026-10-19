# PDF Identity Merge and Unified Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve arXiv identities to canonical ADS records, consolidate annotations from every same-work PDF into one user-selected canonical PDF, safely archive or defer the remaining files, and make TOML the sole non-secret configuration authority.

**Architecture:** Implement four dependent phases: a layered TOML configuration service; a shared publication-identity and merge-group domain; a strict annotation planner plus recovery-journaled filesystem/catalog transaction; and a focused dual-pane Qt PDF review UI. Automatic behavior uses only authoritative work identifiers and exact unique annotation text; ambiguity is represented explicitly and routed to user review.

**Tech Stack:** Python 3.10+, Click, SQLite, `tomlkit>=0.13`, PyMuPDF 1.24+, PySide6 6.6+ (`QtPdf`), pytest, pytest-qt, Ruff, isort.

## Global Constraints

- Configuration precedence is: explicit CLI arguments > library `.adsfolio.toml` > global `config.toml` > defaults.
- The ADS token remains a separate mode-`0600` secret file.
- Default managed directories are `_PDF-obsolete` and `_needs-merge`; both are user-configurable relative paths inside the library root.
- Identify and Watch exclude both managed directory trees.
- Similar titles never establish same-work identity automatically.
- ADS ambiguity never resolves by result ordering.
- Automatic annotation placement requires one exact normalized occurrence and one unambiguous character-to-quad mapping.
- Non-text annotations are detected but never positioned automatically.
- The canonical PDF remains byte-for-byte unchanged until staged verification succeeds.
- No obsolete or needs-merge file is deleted automatically.
- Existing user worktree changes must be preserved; do not run destructive Git commands or broad rewrites.
- Use `source ~/.venvs/ysbpy/bin/activate` for this workspace because `uv` execution is unavailable in the current sandbox.
- Run isort and Ruff formatting on only the files changed by each task; do not format the whole dirty worktree.
- The `.git` directory may be read-only in the execution environment. Perform each commit step when writable; otherwise record the exact intended commit and continue without modifying Git metadata.

---

## File Structure

### New files

- `src/adsfolio/settings.py`: versioned TOML documents, typed merge settings, layered effective values, scoped updates, legacy import, and atomic writes.
- `src/adsfolio/pdf/work_identity.py`: arXiv/Bibcode/DOI normalization, authoritative identity keys, and published ADS resolution results.
- `src/adsfolio/pdf/merge_groups.py`: derive same-work groups and canonical-PDF recommendations without mutating files.
- `src/adsfolio/pdf/annotation_merge.py`: enumerate all PDF annotation types, build exact text indexes, produce automatic/manual decisions, apply decisions, fingerprint, and verify staged output.
- `src/adsfolio/pdf/merge_transaction.py`: stage, recovery journal, canonical replacement, obsolete/needs-merge moves, rollback, and reports.
- `src/adsfolio/gui/pdf_region_view.py`: render one PDF page, show annotation/candidate overlays, and convert drag selection into `QPdfSelection` page-space geometry.
- `src/adsfolio/gui/merge_dialogs.py`: merge-group preview and focused dual-pane review queue.
- `tests/test_settings.py`: TOML precedence, validation, atomic writes, unknown-key preservation, and legacy import.
- `tests/test_pdf_work_identity.py`: parser, ADS query, canonical resolution, and identity-key tests.
- `tests/test_pdf_merge_groups.py`: multi-PDF grouping and canonical recommendation tests.
- `tests/test_pdf_annotation_merge.py`: exact mapping, all annotation classes, fingerprints, apply, and verification tests.
- `tests/test_pdf_merge_transaction.py`: successful commit, rollback, emergency report, obsolete, and needs-merge tests.
- `tests/test_gui_merge_dialogs.py`: preview, region mapping, review decisions, waiver, and abort tests.

### Modified files

- `pyproject.toml`, `uv.lock`: add TOML writing and ensure the GUI extra installs PyMuPDF.
- `src/adsfolio/config.py`: delegate non-secret setup storage to the TOML service while retaining token behavior.
- `src/adsfolio/gui/config.py`: serialize GUI appearance/applications/state through TOML sections instead of `gui.json`.
- `src/adsfolio/gui/appearance.py`: choose global/library save scope and edit merge directory settings with tooltips.
- `src/adsfolio/gui/dialogs.py`: use effective TOML settings in Identify and expose library/global scope consistently.
- `src/adsfolio/gui/main_window.py`: load effective settings per selected root and add merge actions/dialog integration.
- `src/adsfolio/pdf/cli.py`, `src/adsfolio/cli/commands.py`: use effective TOML settings and expose merge dry-run/apply commands.
- `src/adsfolio/pdf/cli_common.py`, `src/adsfolio/pdf/identify.py`, `src/adsfolio/pdf/watcher.py`: managed-directory exclusion and summary counts.
- `src/adsfolio/pdf/prefetch.py`, `src/adsfolio/pdf/identifier.py`, `src/adsfolio/pdf/upgrade_utils.py`: preserve ADS identity fields and resolve arXiv IDs correctly.
- `src/adsfolio/pdf/catalog.py`, `src/adsfolio/pdf/registry.py`: merge provenance schema and transactional catalog update APIs; remove catalog settings authority after migration.
- `src/adsfolio/pdf/upgrade.py`, `src/adsfolio/pdf/cli_upgrade.py`: route the legacy upgrade entry point through the generalized merge planner rather than maintaining a separate annotation policy.
- `src/adsfolio/pdf/annotations.py`: retain public compatibility imports only for substantive adapters; remove fuzzy matching implementation after callers migrate.
- Existing tests under `tests/test_config_setup.py`, `tests/test_gui_config.py`, `tests/test_pdf_cli.py`, `tests/test_pdf_identifier.py`, `tests/test_pdf_catalog.py`, `tests/test_pdf_registry.py`, `tests/test_pdf_upgrade.py`, `tests/test_pdf_annotations.py`, and `tests/test_pdf_watcher.py`: replace obsolete JSON/legacy behavior assertions with supported TOML and generalized merge behavior.
- `docs/cli-tutorials/04-pdf-workflows.md`, `docs/gui.md`, `README.md`: document configuration files, identity promotion, merge semantics, review behavior, and cleanup reminders.

---

### Task 1: Layered TOML Configuration and Legacy Import

**Files:**
- Create: `src/adsfolio/settings.py`
- Create: `tests/test_settings.py`
- Modify: `pyproject.toml:51-72`
- Modify: `uv.lock`
- Modify: `src/adsfolio/config.py:42-224`
- Modify: `src/adsfolio/cli/commands.py:200-360`
- Modify: `src/adsfolio/cv/cli.py`
- Modify: `src/adsfolio/pdf/prefetch.py:41-95`
- Modify: `tests/test_config_setup.py`
- Modify: `tests/test_cli_set.py`
- Modify: `tests/test_cv_cli.py`
- Modify: `tests/test_pdf_prefetch.py`

**Interfaces:**
- Produces: `MergeSettings`, `SettingsStore`, `SettingsSnapshot`, `migrate_legacy_settings()`.
- `SettingsStore.load(root: Path | None = None) -> SettingsSnapshot` returns layered effective sections and both source documents.
- `SettingsStore.update(section: str, values: Mapping[str, Any], *, root: Path | None = None) -> Path` atomically updates global or library scope while preserving unknown keys.
- `SettingsSnapshot.identify -> IdentifySettings`; `merge -> MergeSettings`; `appearance`, `applications`, and `state` are normalized mappings.
- Consumed by Tasks 2-9.

- [ ] **Step 1: Add failing tests for paths, precedence, and validation**

```python
def test_library_toml_overrides_global_merge_and_identify(tmp_path):
    config_dir = tmp_path / "config"
    root = tmp_path / "papers"
    root.mkdir()
    (config_dir / "config.toml").parent.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        '[identify]\nrecursive = false\n[merge]\nobsolete_directory = "old"\n'
    )
    (root / ".adsfolio.toml").write_text(
        '[identify]\nrecursive = true\n[merge]\nneeds_merge_directory = "review"\n'
    )

    snapshot = SettingsStore(config_dir=config_dir).load(root)

    assert snapshot.identify.recursive is True
    assert snapshot.merge == MergeSettings("old", "review")


@pytest.mark.parametrize("value", ["", ".", "../outside", "/absolute"])
def test_merge_directory_rejects_unsafe_paths(value):
    with pytest.raises(ValueError):
        MergeSettings(obsolete_directory=value)
```

- [ ] **Step 2: Run the focused tests and confirm the missing service failure**

Run:

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_settings.py -q
```

Expected: collection fails because `adsfolio.settings` does not exist.

- [ ] **Step 3: Add TOML dependencies**

Add `tomlkit>=0.13` to core dependencies and `pymupdf>=1.24` to the `gui` extra:

```toml
dependencies = [
    # existing dependencies
    "tomlkit>=0.13",
]

[project.optional-dependencies]
gui = ["PySide6>=6.6", "pymupdf>=1.24"]
```

Refresh `uv.lock` with the repository's normal lock command if available. If the
sandboxed `uv` executable still crashes, update only after dependency installation
is available and record that lock verification remains blocked.

- [ ] **Step 4: Implement typed settings and safe relative paths**

Use these public models and signatures:

```python
GLOBAL_CONFIG_FILENAME = "config.toml"
LIBRARY_CONFIG_FILENAME = ".adsfolio.toml"
SETTINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MergeSettings:
    obsolete_directory: str = "_PDF-obsolete"
    needs_merge_directory: str = "_needs-merge"

    def __post_init__(self) -> None:
        obsolete = _safe_library_relative_path(self.obsolete_directory)
        needs_merge = _safe_library_relative_path(self.needs_merge_directory)
        if obsolete == needs_merge:
            raise ValueError("merge directories must be distinct")
        object.__setattr__(self, "obsolete_directory", obsolete)
        object.__setattr__(self, "needs_merge_directory", needs_merge)


@dataclass(frozen=True)
class SettingsSnapshot:
    identify: IdentifySettings
    merge: MergeSettings
    appearance: dict[str, Any]
    applications: dict[str, Any]
    state: dict[str, Any]
    global_document: Mapping[str, Any]
    library_document: Mapping[str, Any] | None
```

Implement `SettingsStore.__init__(config_dir: Path | None = None)`,
`global_path() -> Path`, `library_path(root: Path) -> Path`, `load(root: Path |
None = None) -> SettingsSnapshot`, and `update(section: str, values:
Mapping[str, Any], *, root: Path | None = None) -> Path` with those exact
signatures.

`_safe_library_relative_path()` must use `PurePosixPath`, reject absolute paths,
`.`/empty values, and any `..` component, and return one normalized POSIX string.
`update()` parses with `tomlkit`, modifies only named known keys, sets
`schema_version = 1`, writes `<name>.tmp`, flushes and `os.fsync()`s it, calls
`os.replace()`, then fsyncs the parent directory where supported.

- [ ] **Step 5: Add failing tests for unknown-key preservation and atomic writes**

```python
def test_update_preserves_unknown_sections_and_uses_atomic_replace(
    tmp_path, monkeypatch
):
    store = SettingsStore(config_dir=tmp_path)
    store.global_path().write_text('[future]\nenabled = true\n')
    replacements = []
    monkeypatch.setattr(
        "adsfolio.settings.os.replace",
        lambda source, target: replacements.append((Path(source), Path(target))),
    )

    store.update("identify", {"recursive": True})

    staged = store.global_path().with_name("config.toml.tmp")
    assert replacements == [(staged, store.global_path())]
    assert "[future]" in staged.read_text()
```

Use a real-replace companion test to verify the final file is readable and the
temporary file disappears.

- [ ] **Step 6: Implement deterministic legacy import**

```python
@dataclass(frozen=True)
class LegacyMigrationResult:
    global_path: Path | None
    library_paths: tuple[Path, ...]
    imported_sources: tuple[Path | str, ...]
```

Implement `migrate_legacy_settings(store: SettingsStore, *, roots:
Iterable[Path], catalog: PdfCatalog | None = None) -> LegacyMigrationResult`.

Rules:

- Run only when the target TOML file is absent.
- Import `setup.json` into `[identify]` and `gui.json` into
  `[appearance]`, `[applications]`, and `[state]`.
- Import each catalog `root:<canonical-path>:identify_settings` or legacy
  profile into that root's `.adsfolio.toml` only when its normalized values differ
  from global Identify values.
- Import legacy sidecar settings without changing the sidecar.
- Leave every legacy source untouched.
- Re-running returns no new destinations and does not rewrite TOML.

- [ ] **Step 7: Make `Config` use TOML for non-secret settings**

Retain token lookup/writes exactly. Change setup APIs to use `[identify]`:

```python
@classmethod
def get_setup_path(cls) -> Path:
    return SettingsStore(cls.get_config_dir()).global_path()

@classmethod
def get_user_setup(cls) -> dict[str, Any]:
    return SettingsStore(cls.get_config_dir()).load().identify.to_mapping()

@classmethod
def save_identify_settings(cls, settings: IdentifySettings) -> Path:
    return SettingsStore(cls.get_config_dir()).update(
        "identify", settings.to_mapping()
    )
```

`save_user_setup()` merges supplied identity/year fields with current
`IdentifySettings` and writes through `save_identify_settings()`.

Update `adsfolio set`, CV defaults, and `prefetch.load_profile()` to read the same
TOML-backed `Config` methods. Remove compatibility signature inspection once old
JSON has been imported; keep legacy `retire_year` parsing only inside migration.

- [ ] **Step 8: Run configuration tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_settings.py tests/test_config_setup.py tests/test_cli_set.py tests/test_cv_cli.py tests/test_pdf_prefetch.py -q
isort src/adsfolio/settings.py src/adsfolio/config.py src/adsfolio/cli/commands.py src/adsfolio/cv/cli.py src/adsfolio/pdf/prefetch.py tests/test_settings.py tests/test_config_setup.py
ruff format src/adsfolio/settings.py src/adsfolio/config.py src/adsfolio/cli/commands.py src/adsfolio/cv/cli.py src/adsfolio/pdf/prefetch.py tests/test_settings.py tests/test_config_setup.py
ruff check src/adsfolio/settings.py src/adsfolio/config.py src/adsfolio/cli/commands.py src/adsfolio/cv/cli.py src/adsfolio/pdf/prefetch.py tests/test_settings.py tests/test_config_setup.py
```

Expected: all focused tests pass and Ruff reports no errors.

- [ ] **Step 9: Commit the configuration service**

```bash
git add pyproject.toml uv.lock src/adsfolio/settings.py src/adsfolio/config.py src/adsfolio/cli/commands.py src/adsfolio/cv/cli.py src/adsfolio/pdf/prefetch.py tests/test_settings.py tests/test_config_setup.py tests/test_cli_set.py tests/test_cv_cli.py tests/test_pdf_prefetch.py
git commit -m "feat: unify adsfolio settings in TOML"
```

---

### Task 2: GUI/CLI Configuration Consumers and Managed Scan Exclusions

**Files:**
- Modify: `src/adsfolio/gui/config.py`
- Modify: `src/adsfolio/gui/appearance.py`
- Modify: `src/adsfolio/gui/dialogs.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Modify: `src/adsfolio/pdf/cli.py`
- Modify: `src/adsfolio/pdf/cli_common.py`
- Modify: `src/adsfolio/pdf/identify.py`
- Modify: `src/adsfolio/pdf/watcher.py`
- Modify: `tests/test_gui_config.py`
- Modify: `tests/test_gui_appearance.py`
- Modify: `tests/test_gui_dialogs.py`
- Modify: `tests/test_pdf_cli.py`
- Modify: `tests/test_pdf_watcher.py`

**Interfaces:**
- Consumes: `SettingsStore`, `SettingsSnapshot`, `MergeSettings` from Task 1.
- Produces: `iter_pdf_paths(directory, recursive, excluded_directories=())`, effective root-aware GUI configuration, and global/library scoped saves.
- Consumed by Tasks 7-9 for merge destinations and GUI settings.

- [ ] **Step 1: Replace GUI JSON round-trip tests with TOML section tests**

```python
def test_gui_config_uses_global_toml_sections(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    expected = GuiConfig(timestamp_format="%Y-%m-%d", table_row_height=31)

    path = save_config(expected)

    assert path.name == "config.toml"
    text = path.read_text()
    assert "[appearance]" in text
    assert "[applications]" in text
    assert "[state]" in text
    assert load_config() == expected


def test_library_appearance_overrides_global(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    root = tmp_path / "papers"
    root.mkdir()
    save_config(GuiConfig(table_row_height=24))
    save_config(GuiConfig(table_row_height=40), root=root)
    assert load_config(root=root).table_row_height == 40
```

- [ ] **Step 2: Run focused tests and verify they fail against `gui.json`**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_gui_config.py tests/test_gui_appearance.py tests/test_gui_dialogs.py -q
```

Expected: filename/section/scope assertions fail.

- [ ] **Step 3: Route `GuiConfig` through the TOML service**

Keep `GuiConfig` as the GUI's typed runtime model, but map fields as follows:

- `[appearance]`: fonts, colors, row/column order and visibility, timestamp,
  spacing, labels, sort and click behavior;
- `[applications]`: PDF viewer and browser commands;
- `[state]`: roots, last root/view, window geometry/state, splitters, and each
  root's last settings scope.

`config_path() -> Path` returns `SettingsStore().global_path()`.
`load_config(*, root: Path | None = None) -> GuiConfig` maps the effective
snapshot. `save_config(config: GuiConfig, *, root: Path | None = None, sections:
Collection[str] = ("appearance", "applications", "state")) -> Path` writes only
the requested sections.

Reject `state` in library scope. Do not keep a JSON fallback after the one-time
migration service exists.

- [ ] **Step 4: Add global/library scope and merge directories to Appearance**

Add a scope combo defaulting to Global and two line edits in an Organization tab:

```python
self.settings_scope.addItem("Global", "global")
self.settings_scope.addItem("This library", "library")
self.obsolete_directory.setToolTip(
    "Relative directory where verified noncanonical PDFs are retained."
)
self.needs_merge_directory.setToolTip(
    "Relative directory for groups you explicitly defer after a failed merge."
)
```

Expose exact properties `selected_scope -> Literal["global", "library"]` and
`merge_settings -> MergeSettings`.

Validation errors keep the dialog open and show the exact unsafe value.

- [ ] **Step 5: Make MainWindow reload effective settings on root changes**

When selecting a root, call `load_config(root=root)` and apply appearance and
applications. Appearance saves write either global `config.toml` or that root's
`.adsfolio.toml`; Identify saves `[identify]` via `SettingsStore.update()` instead
of catalog metadata. Keep only the selected scope in global `[state]`.

- [ ] **Step 6: Add failing managed-directory scan tests**

```python
def test_iter_pdf_paths_excludes_configured_directory_trees(tmp_path):
    keep = tmp_path / "paper.pdf"
    obsolete = tmp_path / "archive" / "old.pdf"
    needs = tmp_path / "review" / "group" / "bad.pdf"
    for path in (keep, obsolete, needs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF")

    found = list(
        iter_pdf_paths(
            tmp_path,
            recursive=True,
            excluded_directories=("archive", "review"),
        )
    )

    assert found == [keep]
```

Also test Watch does not process create events or polling paths under either
configured tree.

- [ ] **Step 7: Implement exclusion and managed-directory summaries**

Change the shared iterator signature:

```python
def iter_pdf_paths(
    directory: Path,
    recursive: bool,
    excluded_directories: Iterable[str] = (),
) -> Iterable[Path]:
    excluded = tuple(PurePosixPath(value).parts for value in excluded_directories)
    for path in sorted(directory.rglob("*.pdf") if recursive else directory.glob("*.pdf")):
        relative_parts = path.relative_to(directory).parts
        if any(relative_parts[: len(parts)] == parts for parts in excluded):
            continue
        yield path
```

Use effective `MergeSettings` in Identify and Watch. At Identify completion count
PDFs under each managed directory without traversing any other tree and print:

```text
Managed PDFs: 3 obsolete; 1 needs merge. Review and remove obsolete files when no longer needed.
```

- [ ] **Step 8: Make CLI Identify use effective TOML settings**

Remove catalog-backed `load_library_identify_settings()`. Load once with
`SettingsStore().load(directory)` and apply explicit Click options as overrides.
`--settings-scope` controls where the resulting reusable settings are persisted,
not which source is loaded: the effective global+library values are always the
base.

- [ ] **Step 9: Run focused tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_gui_config.py tests/test_gui_appearance.py tests/test_gui_dialogs.py tests/test_pdf_cli.py tests/test_pdf_watcher.py -q
isort src/adsfolio/gui/config.py src/adsfolio/gui/appearance.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/cli_common.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/watcher.py
ruff format src/adsfolio/gui/config.py src/adsfolio/gui/appearance.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/cli_common.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/watcher.py
ruff check src/adsfolio/gui/config.py src/adsfolio/gui/appearance.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/main_window.py src/adsfolio/pdf/cli.py src/adsfolio/pdf/cli_common.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/watcher.py
```

- [ ] **Step 10: Commit TOML consumers and scan exclusions**

```bash
git add src/adsfolio/gui src/adsfolio/pdf/cli.py src/adsfolio/pdf/cli_common.py src/adsfolio/pdf/identify.py src/adsfolio/pdf/watcher.py tests/test_gui_config.py tests/test_gui_appearance.py tests/test_gui_dialogs.py tests/test_pdf_cli.py tests/test_pdf_watcher.py
git commit -m "feat: apply scoped TOML settings"
```

---

### Task 3: Shared Work Identity and ADS Published Resolution

**Files:**
- Create: `src/adsfolio/pdf/work_identity.py`
- Create: `tests/test_pdf_work_identity.py`
- Modify: `src/adsfolio/pdf/identifier.py`
- Modify: `src/adsfolio/pdf/prefetch.py`
- Modify: `src/adsfolio/pdf/upgrade_utils.py`
- Modify: `src/adsfolio/pdf/cli.py`
- Modify: `tests/test_pdf_identifier.py`
- Modify: `tests/test_pdf_prefetch.py`
- Modify: `tests/test_pdf_upgrade.py`

**Interfaces:**
- Produces: `ArxivIdentifier`, `WorkIdentity`, `ResolutionResult`, `parse_arxiv_identifier()`, `identity_from_metadata()`, `PublicationResolver.resolve_arxiv_ids()`.
- `PdfIdentifier.build_record()` produces `identifier`, `alternate_bibcode`, all DOI values, and normalized `arxiv_id`.
- Consumed by Task 4 grouping and Task 9 Identify integration.

- [ ] **Step 1: Add failing parser and normalization tests**

```python
@pytest.mark.parametrize(
    ("raw", "base", "version"),
    [
        ("1712.07496", "1712.07496", None),
        ("arXiv:1712.07496v2", "1712.07496", 2),
        ("10.48550/arXiv.1712.07496", "1712.07496", None),
        ("astro-ph/9901234v3", "astro-ph/9901234", 3),
        ("2017arXiv171207496A", "1712.07496", None),
    ],
)
def test_parse_arxiv_identifier(raw, base, version):
    parsed = parse_arxiv_identifier(raw)
    assert parsed == ArxivIdentifier(base=base, version=version)
```

- [ ] **Step 2: Add failing ADS query and ambiguity tests**

```python
def test_resolver_queries_arxiv_identifier_and_prefers_unique_published_record():
    published = {
        "bibcode": "2018A&A...612A..85A",
        "identifier": ["arXiv:1712.07496"],
        "doi": ["10.1051/0004-6361/201731806", "10.48550/arXiv.1712.07496"],
        "bibstem": ["A&A"],
    }
    resolver, search = _resolver({
        'arxiv:"1712.07496"': SearchResult(1, 0, [published])
    })

    result = resolver.resolve_arxiv_ids(["1712.07496"])["1712.07496"]

    assert result.status == "resolved"
    assert result.document["bibcode"] == "2018A&A...612A..85A"
    assert search.calls == ['arxiv:"1712.07496"']


def test_resolver_never_selects_first_ambiguous_result():
    resolver, _ = _resolver({
        'arxiv:"1712.07496"': SearchResult(2, 0, [PUBLISHED_A, PUBLISHED_B])
    })
    result = resolver.resolve_arxiv_ids(["1712.07496"])["1712.07496"]
    assert result.status == "ambiguous"
    assert result.document is None
    assert result.candidates == (PUBLISHED_A, PUBLISHED_B)
```

- [ ] **Step 3: Implement one shared identity model**

```python
@dataclass(frozen=True)
class ArxivIdentifier:
    base: str
    version: int | None = None


@dataclass(frozen=True)
class WorkIdentity:
    bibcode: str | None
    alternate_bibcodes: tuple[str, ...]
    arxiv_id: str | None
    dois: tuple[str, ...]

    def authoritative_keys(self) -> frozenset[str]:
        keys = set()
        if self.bibcode:
            keys.add(f"bibcode:{self.bibcode.lower()}")
        keys.update(f"bibcode:{value.lower()}" for value in self.alternate_bibcodes)
        if self.arxiv_id:
            keys.add(f"arxiv:{self.arxiv_id.lower()}")
        keys.update(
            f"doi:{value.lower()}"
            for value in self.dois
            if not value.lower().startswith("10.48550/arxiv.")
        )
        return frozenset(keys)


@dataclass(frozen=True)
class ResolutionResult:
    status: Literal["resolved", "ambiguous", "missing"]
    document: dict[str, Any] | None
    candidates: tuple[dict[str, Any], ...]
    evidence: str
```

Use one parser from GUI, registry sorting, upgrade, and Identify. Normalize DOI
case and punctuation. Primary and alternate Bibcodes deliberately emit the same
`bibcode:` namespace so one record's primary value matches another record's
alternate value. The method also emits `arxiv:` and non-arXiv `doi:` keys; it
never emits title/author keys.

- [ ] **Step 4: Implement batched arXiv resolution**

Implement `PublicationResolver.__init__(client: ADSClient)` and
`resolve_arxiv_ids(arxiv_ids: Sequence[str]) -> dict[str, ResolutionResult]`.
Set `MAX_BATCH_TERMS = 25`.

Build OR queries in chunks with `arxiv:"<base>"`. For each returned document,
derive its normalized arXiv IDs from `identifier` and arXiv DOI values. A result is
resolved only when exactly one non-arXiv primary Bibcode candidate remains. If
ADS returns only one arXiv record, return it as resolved with evidence
`arxiv-only`; if multiple authoritative candidates remain, return ambiguous.

- [ ] **Step 5: Preserve identity evidence in all ADS fetches and records**

Add `alternate_bibcode` and `identifier` to `PdfIdentifier.SEARCH_FIELDS` and
`PREFETCH_FIELDS`. Change `build_record()` to retain all DOI values:

```python
identity = identity_from_metadata(doc)
record = {
    "bibcode": identity.bibcode or "",
    "alternate_bibcode": list(identity.alternate_bibcodes),
    "identifier": self._as_list(doc.get("identifier")),
    "doi": list(identity.dois),
    "arxiv_id": identity.arxiv_id or "",
    # existing fields
}
```

This is an approved breaking change: `doi` becomes consistently list-valued in
new PDF records. Existing scalar DOI readers must continue accepting scalars.

- [ ] **Step 6: Route “Update from ADS” arXiv input correctly**

Extend `_parse_ads_identifier()` with `"arxiv"` and call
`PublicationResolver.resolve_arxiv_ids()`. Inputs `1712.07496`,
`arXiv:1712.07496v2`, an arXiv DOI, and legacy IDs must not enter
`query_docs_for_bibcodes()`.

- [ ] **Step 7: Replace upgrade resolver internals with PublicationResolver**

Keep the public `check_published_version()` temporarily, but make it a substantive
adapter that accepts an arXiv Bibcode or normalized ID, calls
`PublicationResolver`, and maps a unique published result to `PublishedMatch`.
Delete `_find_non_arxiv_doc()` first-result behavior and its tests.

- [ ] **Step 8: Run focused tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_work_identity.py tests/test_pdf_identifier.py tests/test_pdf_prefetch.py tests/test_pdf_upgrade.py tests/test_pdf_cli.py -q
isort src/adsfolio/pdf/work_identity.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/prefetch.py src/adsfolio/pdf/upgrade_utils.py tests/test_pdf_work_identity.py
ruff format src/adsfolio/pdf/work_identity.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/prefetch.py src/adsfolio/pdf/upgrade_utils.py tests/test_pdf_work_identity.py
ruff check src/adsfolio/pdf/work_identity.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/prefetch.py src/adsfolio/pdf/upgrade_utils.py tests/test_pdf_work_identity.py
```

- [ ] **Step 9: Commit shared identity resolution**

```bash
git add src/adsfolio/pdf/work_identity.py src/adsfolio/pdf/identifier.py src/adsfolio/pdf/prefetch.py src/adsfolio/pdf/upgrade_utils.py src/adsfolio/pdf/cli.py tests/test_pdf_work_identity.py tests/test_pdf_identifier.py tests/test_pdf_prefetch.py tests/test_pdf_upgrade.py tests/test_pdf_cli.py
git commit -m "feat: resolve arXiv identities through ADS"
```

---

### Task 4: Authoritative Merge Groups and Catalog Provenance

**Files:**
- Create: `src/adsfolio/pdf/merge_groups.py`
- Create: `tests/test_pdf_merge_groups.py`
- Modify: `src/adsfolio/pdf/catalog.py`
- Modify: `src/adsfolio/pdf/registry.py`
- Modify: `tests/test_pdf_catalog.py`
- Modify: `tests/test_pdf_registry.py`

**Interfaces:**
- Consumes: `WorkIdentity` and `identity_from_metadata()` from Task 3.
- Produces: `MergeMember`, `MergeGroup`, `find_merge_groups()`, catalog schema v3 merge tables, `PdfCatalog.record_merge()`.
- Consumed by Tasks 7-9.

- [ ] **Step 1: Add failing grouping tests**

```python
def test_same_published_bibcode_groups_legacy_records_without_arxiv_id():
    records = [
        _record(1, "preprint.pdf", bibcode="2018A&A...612A..85A"),
        _record(2, "publisher.pdf", bibcode="2018A&A...612A..85A"),
    ]
    groups = find_merge_groups(records)
    assert [member.relative_path for member in groups[0].members] == [
        "preprint.pdf", "publisher.pdf"
    ]


def test_multiple_arxiv_copies_group_by_normalized_arxiv_id():
    records = [
        _record(1, "a.pdf", arxiv_id="1712.07496v1"),
        _record(2, "b.pdf", doi=["10.48550/arXiv.1712.07496"]),
    ]
    assert len(find_merge_groups(records)[0].members) == 2


def test_title_similarity_does_not_group_records():
    records = [_record(1, "a.pdf", title=["Same"]), _record(2, "b.pdf", title=["Same"])]
    assert find_merge_groups(records) == []
```

- [ ] **Step 2: Implement union-by-authoritative-identity grouping**

```python
@dataclass(frozen=True)
class MergeMember:
    attachment_id: int
    location_id: int
    relative_path: str
    sha256: str
    identity: WorkIdentity
    role: Literal["published", "arxiv", "unknown"]


@dataclass(frozen=True)
class MergeGroup:
    group_key: str
    members: tuple[MergeMember, ...]
    recommended_canonical_attachment_id: int | None
    recommendation_evidence: str | None
```

Implement `find_merge_groups(records: Iterable[Mapping[str, Any]]) ->
list[MergeGroup]`.

Build an internal disjoint-set keyed by attachment ID. Union records sharing any
authoritative key. Keep only components with at least two active locations.
Recommend a canonical member only when exactly one member has a non-arXiv primary
Bibcode and non-arXiv DOI/provider evidence; otherwise return `None` and require
user choice. Sort members and groups deterministically.

- [ ] **Step 3: Add failing catalog migration and provenance tests**

```python
def test_catalog_records_merge_and_annotation_outcomes(tmp_path):
    catalog = PdfCatalog(tmp_path / "catalog.sqlite")
    merge_id = catalog.record_merge(
        root=tmp_path,
        group_key="2018A&A...612A..85A",
        canonical_attachment_id=2,
        status="completed",
        report={"canonical": "publisher.pdf"},
        annotation_outcomes=[
            {"fingerprint": "fp1", "source_attachment_id": 1,
             "target_attachment_id": 2, "outcome": "automatic",
             "annotation": {"type": "Highlight"}, "target": {"page": 2}}
        ],
    )
    assert catalog.get_merge(merge_id)["annotation_outcomes"][0]["fingerprint"] == "fp1"
```

- [ ] **Step 4: Add schema version 3 merge provenance tables**

```sql
CREATE TABLE IF NOT EXISTS pdf_merges (
    id INTEGER PRIMARY KEY,
    root_id INTEGER NOT NULL REFERENCES library_roots(id),
    group_key TEXT NOT NULL,
    canonical_attachment_id INTEGER REFERENCES attachments(id),
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS annotation_transfers (
    id INTEGER PRIMARY KEY,
    merge_id INTEGER NOT NULL REFERENCES pdf_merges(id) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL UNIQUE,
    source_attachment_id INTEGER REFERENCES attachments(id),
    target_attachment_id INTEGER REFERENCES attachments(id),
    outcome TEXT NOT NULL,
    annotation_json TEXT NOT NULL,
    target_json TEXT NOT NULL
);
```

Implement `record_merge()`, `get_merge()`, and
`has_annotation_fingerprint(fingerprint: str) -> bool`. `record_merge()` uses one
SQLite transaction and validates outcomes against
`{"automatic", "manual", "waived", "failed"}`.

- [ ] **Step 5: Expose merge groups through Registry**

```python
def find_merge_groups(self) -> list[MergeGroup]:
    return find_merge_groups(self.catalog.query_files(root=self.directory))
```

Remove root Identify settings as an authority after Task 1 migration. Retain
catalog meta read access only to import legacy values; new saves never write
`root:*:identify_settings`.

- [ ] **Step 6: Run focused tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_merge_groups.py tests/test_pdf_catalog.py tests/test_pdf_registry.py -q
isort src/adsfolio/pdf/merge_groups.py src/adsfolio/pdf/catalog.py src/adsfolio/pdf/registry.py tests/test_pdf_merge_groups.py
ruff format src/adsfolio/pdf/merge_groups.py src/adsfolio/pdf/catalog.py src/adsfolio/pdf/registry.py tests/test_pdf_merge_groups.py
ruff check src/adsfolio/pdf/merge_groups.py src/adsfolio/pdf/catalog.py src/adsfolio/pdf/registry.py tests/test_pdf_merge_groups.py
```

- [ ] **Step 7: Commit grouping and provenance**

```bash
git add src/adsfolio/pdf/merge_groups.py src/adsfolio/pdf/catalog.py src/adsfolio/pdf/registry.py tests/test_pdf_merge_groups.py tests/test_pdf_catalog.py tests/test_pdf_registry.py
git commit -m "feat: model same-work PDF merge groups"
```

---

### Task 5: Strict Annotation Enumeration and Exact Mapping

**Files:**
- Create: `src/adsfolio/pdf/annotation_merge.py`
- Create: `tests/test_pdf_annotation_merge.py`
- Modify: `src/adsfolio/pdf/annotations.py`
- Modify: `tests/test_pdf_annotations.py`

**Interfaces:**
- Produces: `PdfAnnotation`, `TargetPlacement`, `AnnotationPlanItem`, `AnnotationPlan`, `extract_pdf_annotations()`, `build_annotation_plan()`, `annotation_fingerprint()`.
- Consumed by Tasks 6-8.

- [ ] **Step 1: Add failing enumeration tests for text and non-text annotations**

```python
def test_extract_enumerates_every_user_annotation_type(real_annotation_pdf):
    records = extract_pdf_annotations(real_annotation_pdf)
    assert {record.subtype for record in records} >= {
        "Highlight", "Underline", "Squiggly", "StrikeOut",
        "FreeText", "Text", "Ink", "Line", "Square", "Circle", "Stamp"
    }
    assert all(record.source_xref > 0 for record in records)


def test_generated_navigation_links_are_not_user_annotations(link_only_pdf):
    assert extract_pdf_annotations(link_only_pdf) == []
```

Create fixtures programmatically with PyMuPDF under `tmp_path`; do not commit
publisher PDFs or special-case production code for fixture text.

- [ ] **Step 2: Add failing exact/ambiguous mapping tests**

```python
def test_one_exact_multiline_occurrence_maps_to_target_quads(source_pdf, target_pdf):
    plan = build_annotation_plan(source_pdf, target_pdf)
    assert plan.items[0].status == "automatic"
    assert plan.items[0].placement.normalized_text == "exact highlighted phrase"
    assert len(plan.items[0].placement.quads) == 2


def test_repeated_exact_text_requires_manual_review(source_pdf, repeated_target_pdf):
    plan = build_annotation_plan(source_pdf, repeated_target_pdf)
    assert plan.items[0].status == "manual"
    assert plan.items[0].reason == "multiple_exact_occurrences"


def test_changed_wording_never_uses_fuzzy_automatic_match(source_pdf, changed_target_pdf):
    plan = build_annotation_plan(source_pdf, changed_target_pdf)
    assert plan.items[0].status == "manual"
    assert plan.items[0].placement is None
```

- [ ] **Step 3: Implement complete annotation records**

```python
TEXT_MARKUP_SUBTYPES = frozenset({"Highlight", "Underline", "Squiggly", "StrikeOut"})


@dataclass(frozen=True)
class PdfAnnotation:
    source_xref: int
    page_index: int
    subtype: str
    rect: tuple[float, float, float, float]
    vertices: tuple[tuple[float, float], ...]
    text: str
    normalized_text: str
    context_before: str
    context_after: str
    colors: dict[str, Any]
    opacity: float | None
    content: str
    properties: dict[str, Any]


@dataclass(frozen=True)
class TargetPlacement:
    page_index: int
    quads: tuple[tuple[float, ...], ...]
    normalized_text: str


@dataclass(frozen=True)
class AnnotationPlanItem:
    source: PdfAnnotation
    status: Literal["automatic", "manual"]
    reason: str
    placement: TargetPlacement | None
```

Enumerate `page.annots()` and retain every subtype. Exclude `/Link` only when it
has no user content, author, modified date, custom color, or non-default border.
Unknown subtypes remain manual records with raw properties.

- [ ] **Step 4: Implement character-bound normalized indexes**

Read `page.get_text("rawdict")`, flatten spans to characters with bounding boxes,
and build:

```python
@dataclass(frozen=True)
class IndexedCharacter:
    normalized: str
    bbox: tuple[float, float, float, float]
    block: int
    line: int


@dataclass(frozen=True)
class PageTextIndex:
    normalized_text: str
    source_character_indexes: tuple[int, ...]
    characters: tuple[IndexedCharacter, ...]
```

Implement `PageTextIndex.quads_for_range(start: int, end: int) ->
tuple[tuple[float, ...], ...]` by slicing the retained source-character indexes,
grouping consecutive boxes by `(block, line)`, and returning one finite bounding
quad per line in reading order. Raise `ValueError` for an empty range, an index
outside `normalized_text`, non-finite coordinates, or overlapping line groups.

Normalization performs Unicode NFC, removes soft hyphens, joins a hyphen followed
by a line break, and collapses whitespace. Every emitted normalized character
retains the original character index. `quads_for_range()` groups contiguous
characters by page line and returns one quad per line. It rejects empty,
non-finite, or overlapping line geometry.

For source markup, use annotation vertices to form source quads and include raw
characters whose bbox center lies inside one source quad. Do not use only
`annot.rect` clipping because it includes neighboring text.

- [ ] **Step 5: Implement exact global occurrence counting**

For each markup record, search its exact `normalized_text` in every target page
index. Automatic status requires one total occurrence across the whole document
and successful `quads_for_range()`. Context may rank candidate pages shown in the
manual UI but must not change `manual` to `automatic`.

All non-text records immediately become manual with reason
`geometry_requires_review`. Textless markup becomes manual with reason
`source_text_unavailable`.

- [ ] **Step 6: Implement stable fingerprints**

```python
def annotation_fingerprint(
    *,
    source_sha256: str,
    annotation: PdfAnnotation,
    target_lineage_sha256: str,
    placement: TargetPlacement,
) -> str:
    payload = {
        "source_sha256": source_sha256,
        "source_xref": annotation.source_xref,
        "subtype": annotation.subtype,
        "text": annotation.normalized_text,
        "target_lineage_sha256": target_lineage_sha256,
        "page": placement.page_index,
        "quads": placement.quads,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
```

Round coordinates to six decimal places in canonical JSON so serialization noise
does not change identity while geometry differences remain distinguishable.

- [ ] **Step 7: Remove fuzzy compatibility behavior**

Migrate callers away from `match_annotations()` sentence/context fallback. Keep
`port_annotations()` only if it adds value as a strict plan/apply adapter used by
legacy API consumers; otherwise remove it as an approved breaking change. Do not
leave two matching policies.

- [ ] **Step 8: Run focused tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_annotation_merge.py tests/test_pdf_annotations.py -q
isort src/adsfolio/pdf/annotation_merge.py src/adsfolio/pdf/annotations.py tests/test_pdf_annotation_merge.py tests/test_pdf_annotations.py
ruff format src/adsfolio/pdf/annotation_merge.py src/adsfolio/pdf/annotations.py tests/test_pdf_annotation_merge.py tests/test_pdf_annotations.py
ruff check src/adsfolio/pdf/annotation_merge.py src/adsfolio/pdf/annotations.py tests/test_pdf_annotation_merge.py tests/test_pdf_annotations.py
```

- [ ] **Step 9: Commit strict annotation planning**

```bash
git add src/adsfolio/pdf/annotation_merge.py src/adsfolio/pdf/annotations.py tests/test_pdf_annotation_merge.py tests/test_pdf_annotations.py
git commit -m "feat: plan exact PDF annotation transfers"
```

---

### Task 6: Annotation Application and Reopened Verification

**Files:**
- Modify: `src/adsfolio/pdf/annotation_merge.py`
- Modify: `tests/test_pdf_annotation_merge.py`

**Interfaces:**
- Consumes: annotation models and fingerprints from Task 5.
- Produces: `ResolvedAnnotationDecision`, `apply_annotation_decisions()`, `verify_staged_pdf()`, `AnnotationAudit`.
- Consumed by Task 7 transaction and Task 8 GUI.

- [ ] **Step 1: Add failing apply and idempotency tests**

```python
def test_apply_preserves_markup_type_style_and_comment(tmp_path):
    output, audits = apply_annotation_decisions(
        canonical_pdf=TARGET_PDF,
        output_pdf=tmp_path / "staged.pdf",
        decisions=[UNDERLINE_DECISION],
        existing_fingerprints=frozenset(),
    )
    written = _annotation_by_content(output, "review note")
    assert written.type[1] == "Underline"
    assert written.colors["stroke"] == pytest.approx((0.1, 0.2, 0.3))
    assert written.opacity == pytest.approx(0.6)
    assert audits[0].outcome == "automatic"


def test_existing_fingerprint_is_not_written_twice(tmp_path):
    result = apply_annotation_decisions(
        canonical_pdf=TARGET_PDF,
        output_pdf=tmp_path / "staged.pdf",
        decisions=[UNDERLINE_DECISION],
        existing_fingerprints=frozenset({UNDERLINE_DECISION.fingerprint}),
    )
    assert result.audits == ()
    assert result.skipped_existing_fingerprints == (UNDERLINE_DECISION.fingerprint,)
```

- [ ] **Step 2: Define decisions and audits**

```python
@dataclass(frozen=True)
class ResolvedAnnotationDecision:
    source_attachment_id: int
    target_attachment_id: int
    source: PdfAnnotation
    outcome: Literal["automatic", "manual", "waived"]
    placement: TargetPlacement | None
    fingerprint: str | None


@dataclass(frozen=True)
class AnnotationAudit:
    fingerprint: str | None
    outcome: Literal["automatic", "manual", "waived", "failed"]
    source: dict[str, Any]
    target: dict[str, Any]
    message: str


@dataclass(frozen=True)
class AnnotationApplyResult:
    output_pdf: Path
    audits: tuple[AnnotationAudit, ...]
    skipped_existing_fingerprints: tuple[str, ...]
```

Waived decisions have no placement and are recorded but not written. Existing
fingerprints are reported separately and do not create a second audit row. Manual
geometry records include subtype-specific properties supplied by the GUI.

- [ ] **Step 3: Implement subtype-specific writing**

Dispatch text markup to PyMuPDF's highlight/underline/squiggly/strikeout methods.
Dispatch reviewed non-text items only for explicitly supported manual decisions:
FreeText, Text, Ink, Line, Square, Circle, Polygon, PolyLine, Stamp, and
FileAttachment. If the source subtype cannot be recreated by installed PyMuPDF,
return failed verification and require waiver; never silently downgrade it.

Copy style properties only when valid for that subtype. Preserve content and
author where PyMuPDF supports them. Save with garbage collection and deflation to
the staged path; never save over the canonical input.

- [ ] **Step 4: Add failing reopened verification tests**

```python
def test_verifier_reopens_and_checks_selected_text(tmp_path):
    staged = _write_real_highlighted_target(tmp_path)
    result = verify_staged_pdf(staged, EXPECTED_AUDITS)
    assert result.ok
    assert result.verified_fingerprints == (EXPECTED_FINGERPRINT,)


def test_verifier_rejects_wrong_subtype_and_empty_geometry(tmp_path):
    staged = _write_corrupt_annotation_target(tmp_path)
    result = verify_staged_pdf(staged, EXPECTED_AUDITS)
    assert result.ok is False
    assert {failure.code for failure in result.failures} >= {
        "subtype_mismatch", "invalid_geometry"
    }
```

- [ ] **Step 5: Implement verification and rendering checks**

```python
@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    verified_fingerprints: tuple[str, ...]
    failures: tuple[VerificationFailure, ...]
```

Implement `verify_staged_pdf(staged_pdf: Path, expected:
Sequence[AnnotationAudit]) -> VerificationResult`.

Reopen the file, enumerate output annotations, and match expected annotations by
subtype/page/geometry/text and preserved comment/style. Fingerprints remain in
catalog/report provenance rather than being injected into user-visible PDF
comments. For text markup, extract characters intersecting the written quads and
compare normalized text. Verify finite nonempty geometry, style, comment, and
render every modified page to a pixmap. Any expected non-waived item missing or
mismatched makes `ok=False`.

- [ ] **Step 6: Run focused tests and commit**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_annotation_merge.py -q
isort src/adsfolio/pdf/annotation_merge.py tests/test_pdf_annotation_merge.py
ruff format src/adsfolio/pdf/annotation_merge.py tests/test_pdf_annotation_merge.py
ruff check src/adsfolio/pdf/annotation_merge.py tests/test_pdf_annotation_merge.py
git add src/adsfolio/pdf/annotation_merge.py tests/test_pdf_annotation_merge.py
git commit -m "feat: apply and verify PDF annotations"
```

---

### Task 7: Recovery-Journaled Merge Transaction and CLI

**Files:**
- Create: `src/adsfolio/pdf/merge_transaction.py`
- Create: `tests/test_pdf_merge_transaction.py`
- Modify: `src/adsfolio/pdf/catalog.py`
- Modify: `src/adsfolio/pdf/registry.py`
- Modify: `src/adsfolio/pdf/upgrade.py`
- Modify: `src/adsfolio/pdf/cli_upgrade.py`
- Modify: `src/adsfolio/pdf/cli.py`
- Modify: `tests/test_pdf_upgrade.py`
- Modify: `tests/test_pdf_cli.py`

**Interfaces:**
- Consumes: Merge groups from Task 4, annotation decisions/verifier from Task 6, `MergeSettings` from Task 1.
- Produces: `PdfMergePlan`, `PdfMergeTransaction.stage()`, `.commit()`, `.move_to_needs_merge()`, and CLI `adsfolio pdf merge`.
- Consumed by Task 8/9 GUI integration.

- [ ] **Step 1: Add failing success transaction test**

```python
def test_commit_replaces_canonical_and_archives_every_source(tmp_path):
    plan = _merge_plan(tmp_path, canonical="publisher.pdf", sources=("a.pdf", "b.pdf"))
    tx = PdfMergeTransaction(plan, registry=_registry(tmp_path))
    staged = tx.stage(ALL_VERIFIED_DECISIONS)

    result = tx.commit(staged)

    assert result.status == "completed"
    assert (tmp_path / "publisher.pdf").read_bytes() == VERIFIED_BYTES
    assert not (tmp_path / "a.pdf").exists()
    assert (tmp_path / "_PDF-obsolete" / plan.group_key / "a.pdf").exists()
    assert (tmp_path / "_PDF-obsolete" / plan.group_key / "b.pdf").exists()
    assert json.loads(result.report_path.read_text())["status"] == "completed"
```

- [ ] **Step 2: Add failing rollback and emergency tests**

```python
def test_commit_failure_restores_all_original_bytes_and_paths(tmp_path, monkeypatch):
    before = _snapshot_group(tmp_path)
    tx = _prepared_transaction(tmp_path)
    monkeypatch.setattr(tx, "_record_catalog_merge", Mock(side_effect=OSError("db")))

    with pytest.raises(MergeCommitError):
        tx.commit(tx.staged_result)

    assert _snapshot_group(tmp_path) == before


def test_rollback_failure_writes_emergency_report_without_more_moves(
    tmp_path, monkeypatch
):
    tx = _prepared_transaction(tmp_path)
    monkeypatch.setattr(tx, "_restore_paths", Mock(side_effect=OSError("restore")))
    with pytest.raises(MergeRecoveryError) as caught:
        tx.commit(tx.staged_result)
    report = json.loads(caught.value.report_path.read_text())
    assert report["status"] == "manual_recovery_required"
    assert all(item["actual_path"] and item["sha256"] for item in report["files"])
```

- [ ] **Step 3: Define the transaction plan and result**

```python
@dataclass(frozen=True)
class PdfMergePlan:
    root: Path
    group_key: str
    canonical: MergeMember
    sources: tuple[MergeMember, ...]
    merge_settings: MergeSettings


@dataclass(frozen=True)
class StagedMerge:
    plan: PdfMergePlan
    staged_pdf: Path
    verification: VerificationResult
    audits: tuple[AnnotationAudit, ...]
    canonical_before_sha256: str


@dataclass(frozen=True)
class MergeTransactionResult:
    status: Literal["completed", "needs_review"]
    canonical_path: Path
    moved_paths: tuple[Path, ...]
    report_path: Path
```

Validate that every member path is inside `root`, exists, and is not already
under a managed directory. Sanitize `group_key` with the existing safe Bibcode
filename policy and a stable digest fallback.

- [ ] **Step 4: Implement staging and recovery journal**

`stage()` creates `TemporaryDirectory(prefix=".adsfolio-merge-", dir=root)`, copies
the canonical PDF, applies decisions, and calls `verify_staged_pdf()`. Failed
verification deletes the temporary directory and leaves active files unchanged.

Before commit, write and fsync a JSON journal in the temporary directory:

```json
{
  "phase": "prepared",
  "canonical": {"original": "publisher.pdf", "backup": "canonical.backup"},
  "sources": [{"original": "a.pdf", "destination": "_PDF-obsolete/.../a.pdf"}]
}
```

Update and fsync phase after canonical replacement, each source move, and catalog
commit. Use `os.replace()` only within the same library filesystem.

- [ ] **Step 5: Implement commit and rollback ordering**

Commit sequence:

1. Copy canonical to backup in transaction directory and fsync it.
2. `os.replace(staged_pdf, canonical_path)`.
3. Move sources with collision-safe filenames into the obsolete group.
4. Write `merge-report.json.tmp`, fsync, and replace to final report.
5. Call `PdfCatalog.record_merge()` and catalog path updates in one SQLite
   transaction.
6. Fsync affected directories and delete the transaction directory.

On any failure, reverse completed source moves, replace the canonical backup,
restore catalog state if catalog commit was not atomic, and verify every restored
digest. If recovery fails, stop all further moves and write
`adsfolio-merge-recovery-<group>-<timestamp>.json` at the library root.

- [ ] **Step 6: Add and implement explicit needs-merge deferral**

Implement `PdfMergeTransaction.move_to_needs_merge(*, failure_report:
Mapping[str, Any]) -> MergeTransactionResult`.

This method is called only after user confirmation or explicit CLI `--defer`.
Move every active group file together into the needs-merge group through the same
journal/rollback mechanism. Do not replace or merge the canonical PDF. Put
`merge-report.json` in the destination group. Discovery alone never calls it.

- [ ] **Step 7: Add generalized merge CLI**

Register `adsfolio pdf merge DIRECTORY` with:

- `--group <group-key>`;
- `--canonical <relative-path>`;
- `--apply` (default dry-run);
- `--defer` (move an explicitly selected group to needs-merge);
- no noninteractive waiver flag.

Dry-run prints all members, recommendation, annotation counts, automatic count,
manual-review count, and destinations. If manual review is required, `--apply`
exits nonzero with `Manual annotation review required; use adsfolio-gui.` It never
guesses or waives.

- [ ] **Step 8: Route legacy Upgrade through merge planning**

Keep `adsfolio pdf upgrade` as a deprecated command for one release. It resolves and
downloads the published PDF, registers it, creates a merge group, and prints the
equivalent merge dry-run. Remove separate overwrite/backup annotation behavior;
accepted breaking change messaging directs users to `pdf merge`.

- [ ] **Step 9: Run transaction/CLI tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
pytest tests/test_pdf_merge_transaction.py tests/test_pdf_upgrade.py tests/test_pdf_cli.py tests/test_pdf_catalog.py tests/test_pdf_registry.py -q
isort src/adsfolio/pdf/merge_transaction.py src/adsfolio/pdf/upgrade.py src/adsfolio/pdf/cli_upgrade.py src/adsfolio/pdf/cli.py tests/test_pdf_merge_transaction.py
ruff format src/adsfolio/pdf/merge_transaction.py src/adsfolio/pdf/upgrade.py src/adsfolio/pdf/cli_upgrade.py src/adsfolio/pdf/cli.py tests/test_pdf_merge_transaction.py
ruff check src/adsfolio/pdf/merge_transaction.py src/adsfolio/pdf/upgrade.py src/adsfolio/pdf/cli_upgrade.py src/adsfolio/pdf/cli.py tests/test_pdf_merge_transaction.py
```

- [ ] **Step 10: Commit transaction and CLI**

```bash
git add src/adsfolio/pdf/merge_transaction.py src/adsfolio/pdf/catalog.py src/adsfolio/pdf/registry.py src/adsfolio/pdf/upgrade.py src/adsfolio/pdf/cli_upgrade.py src/adsfolio/pdf/cli.py tests/test_pdf_merge_transaction.py tests/test_pdf_upgrade.py tests/test_pdf_cli.py tests/test_pdf_catalog.py tests/test_pdf_registry.py
git commit -m "feat: transact multi-PDF annotation merges"
```

---

### Task 8: Focused Dual-Pane Manual Review UI

**Files:**
- Create: `src/adsfolio/gui/pdf_region_view.py`
- Create: `src/adsfolio/gui/merge_dialogs.py`
- Create: `tests/test_gui_merge_dialogs.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Modify: `tests/test_gui_detail_pane.py`

**Interfaces:**
- Consumes: `MergeGroup`, `AnnotationPlanItem`, `ResolvedAnnotationDecision`, `PdfMergeTransaction`.
- Produces: `PdfRegionView.selection_created`, `MergePreviewDialog.selected_canonical`, `AnnotationReviewDialog.decisions`.
- Task 9 completes end-to-end wiring.

- [ ] **Step 1: Add failing page-coordinate selection tests**

```python
def test_region_view_maps_drag_to_pdf_selection(qtbot, monkeypatch):
    view = PdfRegionView()
    qtbot.addWidget(view)
    view.resize(600, 800)
    view.load_pdf(PDF_PATH)
    view.show_page(2, focus_rect=QRectF(72, 144, 200, 40))
    spy = QSignalSpy(view.selection_created)

    qtbot.mousePress(view, Qt.LeftButton, pos=QPoint(120, 220))
    qtbot.mouseMove(view, QPoint(360, 260))
    qtbot.mouseRelease(view, Qt.LeftButton, pos=QPoint(360, 260))

    assert len(spy) == 1
    selection = spy[0][0]
    assert selection.page_index == 2
    assert selection.text
    assert selection.bounds
```

- [ ] **Step 2: Implement `PdfRegionView` using public Qt PDF APIs**

Do not depend on a nonexistent `QPdfView.selectionChanged` signal. Implement a
custom `QWidget` that:

- owns a `QPdfDocument`;
- renders the active page with `QPdfDocument.render()` into a cached `QImage`;
- computes one aspect-preserving page-to-widget `QTransform` and its inverse;
- draws source/candidate overlays in `paintEvent()`;
- maps mouse press/release through the inverse transform;
- calls `QPdfDocument.getSelection(page_index, start_page_point,
  end_page_point)`;
- emits a serializable selection containing `text`, `bounds`, and page index;
- supports zoom and centering on a supplied page-space rectangle.

```python
@dataclass(frozen=True)
class PdfTextSelection:
    page_index: int
    text: str
    bounds: tuple[tuple[float, float, float, float], ...]


class PdfRegionView(QWidget):
    selection_created = Signal(object)
    geometry_created = Signal(object)
```

Implement exact methods `load_pdf(path: Path) -> None`, `show_page(page_index:
int, focus_rect: QRectF | None = None) -> None`, `set_overlays(rects:
Sequence[QRectF]) -> None`, and `set_interaction_mode(mode:
Literal["select_text", "draw_geometry", "none"]) -> None`.

- [ ] **Step 3: Add failing preview and decision queue tests**

```python
def test_preview_requires_canonical_selection_when_no_unique_recommendation(qtbot):
    dialog = MergePreviewDialog(AMBIGUOUS_GROUP, ANNOTATION_COUNTS)
    qtbot.addWidget(dialog)
    assert dialog.confirm_button.isEnabled() is False
    dialog.member_table.selectRow(1)
    assert dialog.confirm_button.isEnabled() is True


def test_review_cannot_finish_with_unresolved_items(qtbot):
    dialog = AnnotationReviewDialog(SOURCE, TARGET, MANUAL_ITEMS)
    qtbot.addWidget(dialog)
    assert dialog.finish_button.isEnabled() is False
    dialog.waive_current()
    dialog.accept_current_selection(TARGET_SELECTION)
    assert dialog.finish_button.isEnabled() is True


def test_abort_returns_no_decisions_and_does_not_start_transaction(qtbot):
    dialog = AnnotationReviewDialog(SOURCE, TARGET, MANUAL_ITEMS)
    qtbot.addWidget(dialog)
    dialog.reject()
    assert dialog.decisions is None
```

- [ ] **Step 4: Implement the merge preview**

Show member filename, role/evidence, Bibcode, arXiv ID, DOI, digest prefix, and
annotation counts grouped into text markup/non-text/unknown. Explain explicitly:
"Merge consolidates annotations; it does not concatenate pages." Require one
canonical row. Show configured obsolete and needs-merge destinations. The
published recommendation is preselected only when the group model provides one;
the user must still press Continue.

- [ ] **Step 5: Implement the focused dual-pane queue**

Layout:

- source `PdfRegionView` left, target `PdfRegionView` right;
- one current item, synchronized page/focus setup, type/style/comment summary;
- progress `N of M`;
- `Accept Candidate` only when a candidate placement exists;
- `Select Exact Text` enables target text selection;
- `Recreate Geometry` enables target rectangle/ink/shape capture according to
  subtype;
- `Waive` opens a confirmation naming the exact item;
- `Abort Merge` rejects without filesystem writes;
- `Finish` enables only when every manual item has manual or waived outcome.

Translate Qt selection bounds directly into `TargetPlacement`; do not infer
geometry from screen pixels after the selection is returned. For non-text items,
store explicitly drawn page-space geometry and editable subtype properties.

- [ ] **Step 6: Integrate a MainWindow Merge action**

Add **Tools -> Merge PDFs...**. It operates on the selected record's derived
group or opens a group chooser when none is selected. Sequence:

1. Preview and canonical selection.
2. Build automatic/manual annotation plans for every source.
3. Review all manual items.
4. Stage and verify.
5. Show verification summary and require final Commit confirmation.
6. Commit and refresh selection to the canonical attachment.

On stage failure show **Keep files in place** (default) and **Move group to
needs-merge...**. The latter requires another explicit confirmation.

- [ ] **Step 7: Run GUI tests and quality checks**

```bash
source ~/.venvs/ysbpy/bin/activate
QT_QPA_PLATFORM=offscreen pytest tests/test_gui_merge_dialogs.py tests/test_gui_detail_pane.py tests/test_gui_dialogs.py tests/test_gui_appearance.py -q
isort src/adsfolio/gui/pdf_region_view.py src/adsfolio/gui/merge_dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_merge_dialogs.py
ruff format src/adsfolio/gui/pdf_region_view.py src/adsfolio/gui/merge_dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_merge_dialogs.py
ruff check src/adsfolio/gui/pdf_region_view.py src/adsfolio/gui/merge_dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_merge_dialogs.py
```

- [ ] **Step 8: Commit focused review UI**

```bash
git add src/adsfolio/gui/pdf_region_view.py src/adsfolio/gui/merge_dialogs.py src/adsfolio/gui/main_window.py tests/test_gui_merge_dialogs.py tests/test_gui_detail_pane.py
git commit -m "feat: add focused PDF annotation review"
```

---

### Task 9: Identify Reconciliation, Documentation, and End-to-End Verification

**Files:**
- Modify: `src/adsfolio/pdf/identify.py`
- Modify: `src/adsfolio/pdf/catalog.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Modify: `tests/test_pdf_cli.py`
- Modify: `tests/test_pdf_catalog.py`
- Modify: `tests/test_gui_merge_dialogs.py`
- Modify: `docs/cli-tutorials/04-pdf-workflows.md`
- Modify: `docs/gui.md`
- Modify: `README.md`

**Interfaces:**
- Consumes all previous tasks.
- Produces the complete Identify -> canonical ADS identity -> merge group -> review -> verified merge workflow.

- [ ] **Step 1: Add the Ali-Lagoa identity regression test**

```python
def test_identify_promotes_arxiv_pair_to_same_published_document(tmp_path, monkeypatch):
    arxiv = tmp_path / "AliLagoa_2017arXiv171207496A.pdf"
    published = tmp_path / "aa31806-17.pdf"
    arxiv.write_bytes(b"%PDF-arxiv")
    published.write_bytes(b"%PDF-published")
    monkeypatch.setattr(
        PublicationResolver,
        "resolve_arxiv_ids",
        lambda self, ids: {
            "1712.07496": ResolutionResult(
                status="resolved",
                document=ALI_LAGOA_PUBLISHED_DOC,
                candidates=(ALI_LAGOA_PUBLISHED_DOC,),
                evidence="arxiv",
            )
        },
    )

    _run_identify_with_records(tmp_path, arxiv, published)

    state = open_registry_state(tmp_path)
    groups = state.registry.find_merge_groups()
    assert len(groups) == 1
    assert {member.relative_path for member in groups[0].members} == {
        arxiv.name, published.name
    }
    assert groups[0].group_key == "2018A&A...612A..85A"
```

- [ ] **Step 2: Reconcile newly identified records by canonical identity**

After each Identify batch:

1. Collect normalized arXiv IDs from newly resolved and existing records.
2. Resolve them in batched ADS queries, using cache first.
3. Replace attachment metadata with canonical ADS documents while retaining
   source-specific `source_arxiv_id`, `source_bibcode`, and `source_sha256`.
4. Let `_ensure_document()` link every matching attachment to the same published
   document.
5. Derive and report merge groups; do not move or modify PDFs during Identify.

Print:

```text
Merge candidates: 1 same-work group containing 2 PDFs. Use Tools -> Merge PDFs or `adsfolio pdf merge`.
```

- [ ] **Step 3: Add a real end-to-end different-layout PDF fixture**

Generate two small PDFs at test runtime with PyMuPDF:

- source: one-column text with Highlight, Underline, repeated phrase, FreeText,
  Circle, and Stamp;
- target: two-column/reflowed text where one highlighted phrase is unique and
  another repeats.

The test must:

1. form one group;
2. auto-place only the unique exact annotation;
3. supply explicit manual decisions for repeated text and non-text geometry;
4. stage and reopen verification;
5. commit;
6. assert canonical output annotations and normalized selected text;
7. assert all sources and `merge-report.json` under configured obsolete path;
8. rerun planning and prove no duplicate fingerprints are written.

- [ ] **Step 4: Document supported behavior and failure modes**

Update docs with:

- global `config.toml`, library `.adsfolio.toml`, precedence, and example sections;
- manual one-time deletion commands for legacy `gui.json`, `setup.json`, and
  migrated sidecars after checking TOML;
- arXiv ID to published Bibcode promotion;
- same-work evidence and why title matching is not automatic;
- "Merge consolidates annotations; it does not concatenate pages";
- exact automatic matching and focused review behavior;
- all detected annotation types and manual requirement for geometry;
- `_PDF-obsolete` and `_needs-merge` defaults/overrides;
- rollback/emergency reports and no automatic deletion.

- [ ] **Step 5: Run the full test suite**

```bash
source ~/.venvs/ysbpy/bin/activate
QT_QPA_PLATFORM=offscreen pytest -q
```

Expected: every test passes; GUI tests are not skipped when PySide6/PyMuPDF are
installed. Record the exact pass/skip count.

- [ ] **Step 6: Run repository quality checks**

Run only after inspecting the dirty worktree so unrelated user files are not
rewritten:

```bash
source ~/.venvs/ysbpy/bin/activate
isort <all Python files changed by Tasks 1-9>
ruff format <all Python files changed by Tasks 1-9>
ruff check <all Python files changed by Tasks 1-9>
```

If project-wide Ruff is requested, first verify it will not modify unrelated
files; `ruff check .` is read-only, but `ruff format .` is not allowed in this
dirty worktree.

- [ ] **Step 7: Perform visual and PDF verification**

1. Launch GUI with the generated end-to-end fixture.
2. Inspect preview and dual-pane review at normal and narrow window widths.
3. Verify source/candidate overlays, zoom, long filenames, annotation properties,
   waiver confirmation, and disabled Finish state.
4. Render every modified page from the final canonical PDF with `pdftoppm`.
5. Inspect PNGs for misplaced markup, clipped text, black boxes, or missing shapes.
6. Reopen the final PDF with PyMuPDF and compare annotations to the audit report.

- [ ] **Step 8: Final merged review lenses**

Request three read-only reviews over the final diff:

- Architect: configuration authority, identity boundaries, extensibility,
  naming, and long-term catalog model.
- Code Reviewer: correctness, rollback, idempotency, concurrency, and breaking
  behavior.
- Tester/User Advocate: ambiguous identity, annotation edge cases, UX, migration,
  docs, and recovery clarity.

Resolve material findings with a failing test before implementation changes.

- [ ] **Step 9: Final commit**

```bash
git add src tests docs README.md pyproject.toml uv.lock
git commit -m "feat: reconcile and merge same-work PDFs"
```

Do not stage unrelated dirty-worktree files. If Git remains read-only, report the
exact files that would be staged and do not claim a commit.

---

## Execution Order and Review Gates

1. Tasks 1-2: TOML configuration is a deployable checkpoint. Run all existing
   configuration, GUI settings, Identify, and Watch tests before continuing.
2. Tasks 3-4: identity/grouping is a deployable read-only checkpoint. Identify may
   report groups but cannot move files yet.
3. Tasks 5-7: merge engine checkpoint. CLI dry-run and fully automatic verified
   groups work; manual groups explicitly require GUI.
4. Task 8: GUI manual-review checkpoint.
5. Task 9: end-to-end integration and completion evidence.

At every gate, preserve the existing central catalog and run migration tests
against copies, never the user's live `pdf-library.sqlite`.
