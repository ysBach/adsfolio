# Identify Settings Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist reusable PDF Identify settings globally or per library through one model shared by the CLI and GUI, while keeping risky actions one-run-only and displaying unset years as blank.

**Architecture:** Add a domain-owned immutable `IdentifySettings` model with strict field-by-field normalization. Store global values in `setup.json`, library values in root registry/sidecar metadata, and only per-root scope selection in `gui.json`. The CLI resolves persisted settings plus tri-state overrides; the GUI edits and saves the same model before invoking the CLI with the selected scope.

**Tech Stack:** Python 3.10+, Click, PySide6, SQLite/JSON persistence, pytest, pytest-qt, uv, Ruff.

## Global Constraints

- Global scope is the default for every library without an explicit GUI scope selection.
- `refresh_cache`, `allow_ads`, and `apply_moves` are never persisted and reopen disabled.
- Unset years are represented as `None`, display as blank, and are never stored as `"Not set"` or `0`.
- Existing `retire_year` input remains readable; new writes use `end_year`.
- Every Identify interactive item receives matching tooltip and accessible help text.
- Existing unrelated worktree changes must remain untouched.

## File Structure

- Create `src/adsapi/pdf/identify_settings.py`: canonical model, normalization, mapping serialization, and override resolution.
- Modify `src/adsapi/config.py`: atomic global settings read/write using the canonical mapping.
- Modify `src/adsapi/pdf/sidecar.py`: legacy-profile migration plus canonical sidecar settings access.
- Modify `src/adsapi/pdf/catalog.py`: import canonical and legacy settings into root metadata.
- Modify `src/adsapi/pdf/registry.py`: root-specific canonical settings access and sidecar-compatible export.
- Modify `src/adsapi/pdf/cli.py`: settings scope and explicit one-run override resolution.
- Modify `src/adsapi/pdf/identify.py`: consume the already-resolved publication profile instead of independently reloading conflicting global values.
- Modify `src/adsapi/gui/config.py`: normalized root-to-scope presentation preference.
- Modify `src/adsapi/gui/dialogs.py`: scope selector, blank years, complete help text, validation, and save-before-accept behavior.
- Modify `src/adsapi/gui/main_window.py`: load/save the selected settings scope and provide persistence callbacks.
- Create `tests/test_pdf_identify_settings.py` and `tests/test_gui_dialogs.py`; extend focused config, sidecar, registry, and CLI tests.
- Modify `docs/gui.md` and `docs/cli-tutorials/04-pdf-workflows.md`: user-visible persistence and CLI override behavior.

---

### Task 1: Canonical Identify settings model

**Files:**
- Create: `src/adsapi/pdf/identify_settings.py`
- Create: `tests/test_pdf_identify_settings.py`

**Interfaces:**
- Produces: `IdentifySettings.from_mapping(payload, *, fallback=None) -> IdentifySettings`
- Produces: `IdentifySettings.to_mapping() -> dict[str, object]`
- Produces: `IdentifySettings.with_overrides(...) -> IdentifySettings`
- Produces: `IdentifySettings.publication_profile() -> PublicationProfile`
- Consumes: existing `PublicationProfile` from `adsapi.pdf.prefetch`

- [ ] **Step 1: Write failing normalization and migration tests**

```python
def test_identify_settings_normalizes_legacy_profile_and_invalid_fields():
    fallback = IdentifySettings(recursive=True, output_unresolved="pending")
    settings = IdentifySettings.from_mapping(
        {
            "orcid": " 0000-0002-1825-0097 ",
            "query_names": [" Bach, Y. ", ""],
            "retire_year": "2030",
            "recursive": "yes",
            "verbose": True,
        },
        fallback=fallback,
    )
    assert settings.orcid == "0000-0002-1825-0097"
    assert settings.query_names == ("Bach, Y.",)
    assert settings.end_year == 2030
    assert settings.recursive is True
    assert settings.output_unresolved == "pending"
    assert settings.verbose is True
    assert "retire_year" not in settings.to_mapping()


def test_identify_settings_rejects_reversed_year_window():
    with pytest.raises(ValueError, match="debut year cannot be greater"):
        IdentifySettings(debut_year=2030, end_year=2020)
```

- [ ] **Step 2: Run tests and verify the missing model is the failure**

Run: `uv run pytest tests/test_pdf_identify_settings.py -q`

Expected: collection fails because `adsapi.pdf.identify_settings` does not exist.

- [ ] **Step 3: Implement the immutable model and field normalizers**

```python
@dataclass(frozen=True)
class IdentifySettings:
    orcid: str = ""
    query_names: tuple[str, ...] = ()
    debut_year: int | None = None
    end_year: int | None = None
    library_ids: tuple[str, ...] = ()
    recursive: bool = False
    infer_from_filenames: bool = False
    output_unresolved: str | None = None
    verbose: bool = False

    def __post_init__(self) -> None:
        if (
            self.debut_year is not None
            and self.end_year is not None
            and self.debut_year > self.end_year
        ):
            raise ValueError("debut year cannot be greater than end year")
```

`from_mapping` must accept only real booleans, normalize years to `1..9999` or the fallback field, trim paths/identity strings, use `end_year` before legacy `retire_year`, ignore unknown keys, and log malformed values. `with_overrides` must distinguish an omitted value from an explicit `None` by using a private sentinel.

`publication_profile()` must import `PublicationProfile` inside the method and
use a `TYPE_CHECKING` import for its return annotation. A module-level import
would create `Config → IdentifySettings → PublicationProfile → Config`.

- [ ] **Step 4: Verify model tests pass**

Run: `uv run pytest tests/test_pdf_identify_settings.py -q`

Expected: all model tests pass without warnings from valid fixtures.

- [ ] **Step 5: Commit the domain slice**

```bash
git add src/adsapi/pdf/identify_settings.py tests/test_pdf_identify_settings.py
git commit -m "feat: add canonical identify settings"
```

---

### Task 2: Global and per-library persistence

**Files:**
- Modify: `src/adsapi/config.py`
- Modify: `src/adsapi/pdf/sidecar.py`
- Modify: `src/adsapi/pdf/catalog.py`
- Modify: `src/adsapi/pdf/registry.py`
- Modify: `tests/test_config_setup.py`
- Modify: `tests/test_pdf_sidecar.py`
- Modify: `tests/test_pdf_registry.py`

**Interfaces:**
- Consumes: `IdentifySettings.from_mapping()` and `.to_mapping()` from Task 1
- Produces: `Config.load_identify_settings() -> IdentifySettings`
- Produces: `Config.save_identify_settings(settings: IdentifySettings) -> Path`
- Produces: `Sidecar.identify_settings: IdentifySettings | None`
- Produces: `Sidecar.set_identify_settings(settings: IdentifySettings | None) -> bool`
- Produces: `CentralPdfRegistryState.identify_settings: IdentifySettings | None`
- Produces: `CentralPdfRegistryState.set_identify_settings(settings: IdentifySettings | None) -> bool`

- [ ] **Step 1: Write failing persistence and migration tests**

```python
def test_global_identify_settings_roundtrip_preserves_identity_and_options(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: tmp_path))
    expected = IdentifySettings(
        orcid="0000-0002-1825-0097",
        debut_year=2010,
        end_year=2030,
        recursive=True,
        output_unresolved="unresolved",
    )
    Config.save_identify_settings(expected)
    assert Config.load_identify_settings() == expected


def test_sidecar_migrates_identify_profile_to_identify_settings(tmp_path):
    (tmp_path / SIDECAR_FILENAME).write_text(
        json.dumps({"version": 2, "files": {}, "identify_profile": {"retire_year": 2030}})
    )
    assert Sidecar(tmp_path).identify_settings.end_year == 2030
```

Add a central-registry roundtrip assertion that `export_payload()` emits
`identify_settings` and not `identify_profile` after saving canonical settings.

- [ ] **Step 2: Run focused persistence tests and verify missing interfaces fail**

Run: `uv run pytest tests/test_config_setup.py tests/test_pdf_sidecar.py tests/test_pdf_registry.py -q`

Expected: failures name the new load/save properties and canonical payload key.

- [ ] **Step 3: Implement atomic global persistence and legacy-compatible library persistence**

`Config.save_identify_settings()` writes the complete canonical mapping to
`setup.json` via a sibling temporary file and `os.replace`, then applies mode
`0o600`. Keep `save_user_setup()` as a compatibility entry point that loads the
current canonical settings and replaces only its identity/year fields.

Sidecar and registry readers check `identify_settings` first and fall back to
`identify_profile`. Writers emit only `identify_settings`. `export_payload()`
must include the canonical mapping, while legacy imports continue to work. Bump
the sidecar schema version because older applications will no longer see the
mirrored `identify_profile`; backward compatibility with older writers is an
explicitly accepted breaking change.

The catalog migration reads `identify_settings` first and falls back to
`identify_profile`. Canonical settings access is root-only and distinguishes a
missing value from an explicit clear; it must not use the registry's generic
global fallback. Add typed root-only get/set/delete operations and malformed-JSON
fallback tests.

- [ ] **Step 4: Run persistence tests and existing setup tests**

Run: `uv run pytest tests/test_config_setup.py tests/test_cli_set.py tests/test_pdf_sidecar.py tests/test_pdf_registry.py -q`

Expected: all pass; existing setup calls retain reusable global options.

- [ ] **Step 5: Commit the persistence slice**

```bash
git add src/adsapi/config.py src/adsapi/pdf/sidecar.py src/adsapi/pdf/catalog.py src/adsapi/pdf/registry.py tests/test_config_setup.py tests/test_pdf_sidecar.py tests/test_pdf_registry.py
git commit -m "feat: persist identify settings by scope"
```

---

### Task 3: Shared CLI resolution and explicit overrides

**Files:**
- Modify: `src/adsapi/pdf/cli.py`
- Modify: `src/adsapi/pdf/identify.py`
- Modify: `tests/test_pdf_cli.py`

**Interfaces:**
- Consumes: global and root settings loaders from Task 2
- Produces CLI: `--settings-scope [global|library]`, default `global`
- Produces CLI: `--recursive/--no-recursive` and `--infer/--no-infer`
- Preserves CLI: repeatable `--verbose/-v` as an additive one-run override of the saved base verbosity
- Produces CLI: `--clear-debut-year`, `--clear-end-year`, and `--clear-output-unresolved`
- Produces: `run_identify(..., profile: PublicationProfile | None = None)`

- [ ] **Step 1: Write failing CLI precedence and safety tests**

```python
def test_identify_cli_inherits_library_settings_and_applies_negative_overrides(
    tmp_path, monkeypatch
):
    saved = IdentifySettings(
        debut_year=2010,
        end_year=2030,
        recursive=True,
        infer_from_filenames=True,
        output_unresolved="pending",
        verbose=True,
    )
    monkeypatch.setattr(
        "adsapi.pdf.cli.load_library_identify_settings", lambda directory: saved
    )
    captured = _capture_run_identify(monkeypatch)
    result = CliRunner().invoke(
        pdf,
        [
            "identify",
            str(tmp_path),
            "--settings-scope",
            "library",
            "--no-recursive",
            "--no-infer",
            "--clear-output-unresolved",
        ],
    )
    assert result.exit_code == 0
    assert captured["recursive"] is False
    assert captured["infer_mode"] is False
    assert captured["output_unresolved"] is None
    assert captured["refresh_cache"] is False
    assert captured["allow_ads"] is False
    assert captured["apply"] is False
```

Add tests for global default scope, explicit year clearing, mutually exclusive
path/year options, relative output resolution against the scan root, and the
effective `PublicationProfile` passed into `run_identify`.

- [ ] **Step 2: Run focused CLI tests and verify current flags cannot satisfy them**

Run: `uv run pytest tests/test_pdf_cli.py -k 'identify_command or identify_cli' -q`

Expected: Click rejects the new options or assertions show hard-coded defaults.

- [ ] **Step 3: Implement settings loading, tri-state options, and explicit clear options**

Resolve settings once in `identify_command`, convert the selected settings to a
publication profile, resolve relative output paths against `directory`, and pass
concrete values to `run_identify`. Keep the three one-run actions as ordinary
default-false flags. Reject `--clear-*` combined with its positive value using
`click.UsageError`.

Change `run_identify` to use its supplied profile and fall back to
`load_profile()` only for internal callers that do not supply one. Do not save
CLI run overrides back to either scope. Preserve its existing
`set_identify_profile()` synchronization as identity/year provenance; never
replace that call with `set_identify_settings()`, because doing so would persist
run overrides and overwrite reusable preferences.

- [ ] **Step 4: Run focused and complete PDF CLI tests**

Run: `uv run pytest tests/test_pdf_cli.py tests/test_pdf_prefetch.py -q`

Expected: all tests pass with updated assertions for the intentional CLI change.

- [ ] **Step 5: Commit the CLI slice**

```bash
git add src/adsapi/pdf/cli.py src/adsapi/pdf/identify.py tests/test_pdf_cli.py
git commit -m "feat: resolve persisted identify settings in cli"
```

---

### Task 4: GUI scope, blank years, help text, and save-before-run

**Files:**
- Modify: `src/adsapi/gui/config.py`
- Modify: `src/adsapi/gui/dialogs.py`
- Modify: `src/adsapi/gui/main_window.py`
- Modify: `tests/test_gui_config.py`
- Create: `tests/test_gui_dialogs.py`

**Interfaces:**
- Produces: `GuiConfig.identify_scopes: dict[str, str]`
- Consumes: normalized root path and values `global | library`
- Produces: `IdentifyDialog(root, global_settings, library_settings=None, initial_scope="global", save_settings=None, parent=None)`
- Produces: `IdentifyDialog.selected_settings() -> IdentifySettings`
- Consumes callback: `save_settings(scope: str, settings: IdentifySettings) -> None`

- [ ] **Step 1: Write failing GUI configuration and dialog behavior tests**

```python
def test_identify_dialog_defaults_global_and_displays_unset_years_as_blank(
    application
):
    dialog = IdentifyDialog(Path("/papers"), IdentifySettings())
    assert dialog.scope.currentData() == "global"
    assert dialog.debut.text() == ""
    assert dialog.end.text() == ""


def test_identify_dialog_saves_selected_scope_before_accept(application):
    calls = []
    dialog = IdentifyDialog(
        Path("/papers"),
        IdentifySettings(recursive=True),
        IdentifySettings(recursive=False),
        initial_scope="library",
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    dialog.accept()
    assert calls == [("library", dialog.selected_settings())]
    assert dialog.result() == QDialog.Accepted
```

Add tests for scope switching/copy-on-first-library-use, Cancel writing nothing,
one-run actions reopening false, reversed-year rejection, save errors keeping the
dialog open, generated argv containing only scope plus one-run actions, and all
eleven interactive controls having non-empty identical tooltip/accessibility text.
The scope-switching test must assert that changing the combo immediately
repopulates every reusable field without closing the dialog.

- [ ] **Step 2: Run GUI tests and verify the current dialog fails each behavior**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_config.py tests/test_gui_dialogs.py -q`

Expected: failures identify the missing scope field, constructor inputs, blank display, help text, and persistence callback.

- [ ] **Step 3: Implement normalized scope persistence and the dialog behavior**

Normalize each root key with `str(Path(path).expanduser().resolve())`; ignore
invalid scope values on config load. Add the scope combo first in the form.
Apply each help string with both `setToolTip(text)` and
`setAccessibleDescription(text)`. Set both year controls' special value text to
`""`, range to `0..9999`, and convert `0` to `None` in `selected_settings()`.
Connect `scope.currentIndexChanged` to repopulate reusable controls from the
selected scope. When no library configuration exists, initialize an in-memory
library value by copying the global value; do not persist that copy until OK.

Override `accept()` to validate years, call `save_settings`, and only then call
`super().accept()`. Catch `OSError`/`ValueError`, show a concise `QMessageBox`,
and leave the dialog open. `build_argv()` includes `--settings-scope` plus only
checked one-run actions because reusable settings have already been saved.

- [ ] **Step 4: Connect MainWindow to canonical persistence and verify GUI tests**

`MainWindow._identify()` loads global settings and the current root's optional
library settings, reads `config.identify_scopes[root]`, and supplies one save
callback. The callback writes the chosen canonical destination, updates the
scope map, and saves `gui.json`; if any write fails it raises so the dialog does
not launch the job. Before per-library persistence, convert an unresolved output
inside the root to a root-relative string; retain external absolute paths. Add a
test proving `subfolder/unresolved` stays relative in stored metadata and resolves
to `root / "subfolder/unresolved"` at execution. The authoritative central
registry is stored in the user config directory rather than the library root, so
test a registry write failure—not a read-only library-sidecar fallback—and assert
that the process does not launch.

Run: `QT_QPA_PLATFORM=offscreen uv run pytest tests/test_gui_config.py tests/test_gui_dialogs.py tests/test_gui_library.py -q`

Expected: all pass and no Qt warnings are emitted.

- [ ] **Step 5: Commit the GUI slice**

```bash
git add src/adsapi/gui/config.py src/adsapi/gui/dialogs.py src/adsapi/gui/main_window.py tests/test_gui_config.py tests/test_gui_dialogs.py
git commit -m "feat: persist identify dialog settings"
```

---

### Task 5: Documentation, formatting, and regression verification

**Files:**
- Modify: `docs/gui.md`
- Modify: `docs/cli-tutorials/04-pdf-workflows.md`
- Verify: all files changed in Tasks 1–4

**Interfaces:**
- Documents the exact scope names, persisted fields, one-run fields, and CLI positive/negative/clear overrides.

- [ ] **Step 1: Update user documentation**

Document that `All libraries` is the default, `This library only` travels with
library metadata, blank years mean no bound, and `Refresh cache`, `Allow ADS
fallback`, and `Apply unresolved moves` must be chosen per run. Include one CLI
example using `--settings-scope library --no-recursive` and one using
`--clear-output-unresolved`. Document the sidecar schema bump and that older
adsapi releases do not read the new `identify_settings` payload.

- [ ] **Step 2: Run import sorting and Ruff formatting**

Run: `uv run isort src/adsapi/pdf/identify_settings.py src/adsapi/config.py src/adsapi/pdf/sidecar.py src/adsapi/pdf/registry.py src/adsapi/pdf/cli.py src/adsapi/pdf/identify.py src/adsapi/gui/config.py src/adsapi/gui/dialogs.py src/adsapi/gui/main_window.py tests/test_pdf_identify_settings.py tests/test_config_setup.py tests/test_pdf_sidecar.py tests/test_pdf_registry.py tests/test_pdf_cli.py tests/test_gui_config.py tests/test_gui_dialogs.py`

Run: `uv run ruff format . && uv run ruff check .`

Expected: exit code 0. If Ruff or isort is unavailable, stop and request dependency installation instead of substituting another formatter.

- [ ] **Step 3: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen uv run pytest`

Expected: all tests pass with no failures, errors, or unexpected warnings.

- [ ] **Step 4: Inspect the final diff and run documentation checks**

Run: `git diff --check`

Run: `uv run python -m sphinx -W -b html docs docs/_build/html`

Expected: no whitespace errors and documentation builds without warnings. If Sphinx is not installed in the selected environment, report that verification as unavailable rather than installing without approval.

- [ ] **Step 5: Commit documentation and verification updates**

```bash
git add docs/gui.md docs/cli-tutorials/04-pdf-workflows.md
git commit -m "docs: explain identify settings scopes"
```
