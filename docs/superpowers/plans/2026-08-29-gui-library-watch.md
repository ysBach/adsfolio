# GUI Library Watch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, per-library GUI watching for newly added PDFs, remembered Identify/Rename settings, automatic single-file processing, and non-modal actionable notifications.

**Architecture:** A Qt-free session tracker classifies filesystem identities relative to an activation baseline. A GUI watch controller owns one `QThread` worker and one worker-owned catalog connection per watched root; immutable events cross into `MainWindow`, where a compact notice bar and per-root actions control manual or automatic processing. Existing `PdfWatcher.process_new_pdf()` remains the single-file identification/rename implementation.

**Tech Stack:** Python 3.10+, PySide6, watchdog, SQLite registry, pytest/pytest-qt, Ruff, isort

**Spec:** `docs/superpowers/specs/2026-08-29-gui-library-watch-design.md`

## Global Constraints

- All watch and automatic processing options default to `False`.
- A watch session must not process a PDF identity present before activation.
- Watch recursion uses `RootEntry.recursive`; remembered Identify recursion remains a bulk-operation setting.
- Workers create and close their own registry connections; no SQLite connection crosses threads.
- Automatic processing uses `allow_ads=True` and `apply=True` only for eligible future arrivals.
- No fixed file-size/readiness threshold or retry count is introduced.
- Existing CLI watch behavior and bulk Identify/Rename commands remain compatible.
- Tests use temporary libraries and fake ADS/process objects; they never watch or mutate a live library.
- Preserve unrelated dirty-worktree changes and do not create commits unless requested.

---

### Task 1: Canonical Rename Settings and Persistence

**Files:**
- Create: `src/adsfolio/pdf/rename_settings.py`
- Modify: `src/adsfolio/config.py`
- Modify: `src/adsfolio/pdf/registry.py`
- Create: `tests/test_pdf_rename_settings.py`
- Modify: `tests/test_config_setup.py`
- Modify: `tests/test_pdf_registry.py`

**Interfaces:**
- Produces: `RenameSettings`, `Config.load_rename_settings()`, `Config.save_rename_settings()`, `CentralPdfRegistryState.rename_settings`, and `CentralPdfRegistryState.set_rename_settings()`.
- Consumed by: Tasks 2, 4, and 5.

- [ ] **Step 1: Write failing value-object tests**

```python
def test_rename_settings_defaults_and_roundtrip():
    settings = RenameSettings()
    assert settings.template == DEFAULT_SCHEME
    assert settings.author_format == "surname_initials"
    assert settings.max_authors == 3
    assert settings.char_policy == "keep"
    assert RenameSettings.from_mapping(settings.to_mapping()) == settings


def test_rename_settings_normalizes_each_malformed_field_independently():
    fallback = RenameSettings(template="{bibcode}", max_authors=7)
    loaded = RenameSettings.from_mapping(
        {
            "template": 3,
            "author_format": "invalid",
            "max_authors": True,
            "char_policy": "invalid",
        },
        fallback=fallback,
    )
    assert loaded == fallback
```

- [ ] **Step 2: Verify the tests fail because the model is absent**

Run: `source ~/.venvs/ysbpy/bin/activate && pytest tests/test_pdf_rename_settings.py -q`

Expected: collection fails with `ModuleNotFoundError: adsfolio.pdf.rename_settings`.

- [ ] **Step 3: Implement the immutable settings model**

```python
@dataclass(frozen=True)
class RenameSettings:
    template: str = DEFAULT_SCHEME
    author_format: str = "surname_initials"
    max_authors: int = 3
    char_policy: str = "keep"

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback: RenameSettings | None = None,
    ) -> RenameSettings:
        """Normalize persisted values independently."""

    def to_mapping(self) -> dict[str, object]:
        """Return a canonical JSON-compatible mapping."""
```

Accept non-empty templates, the existing three built-in author formats or
`ads_custom:<format>`, integer author counts from 1 through 99, and `keep` or
`ascii`. Treat `bool` as malformed for the integer field. Log malformed values
and use the corresponding fallback field.

- [ ] **Step 4: Add failing global and library persistence tests**

```python
def test_global_rename_settings_preserve_other_setup_sections(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    Config.save_identify_settings(IdentifySettings(recursive=True))
    expected = RenameSettings(template="{bibcode}", char_policy="ascii")
    Config.save_rename_settings(expected)
    assert Config.load_rename_settings() == expected
    assert Config.load_identify_settings().recursive


def test_registry_roundtrips_root_rename_settings(monkeypatch, tmp_path):
    state = open_registry_state(tmp_path / "library")
    expected = RenameSettings(max_authors=5)
    assert state.set_rename_settings(expected)
    assert state.rename_settings == expected
    state.close()
```

- [ ] **Step 5: Implement persistence by following the Identify settings pattern**

Add lazy imports under `TYPE_CHECKING`, load/save a `rename` object in
`setup.json`, and preserve all unknown setup keys. Extend
`Config._save_setup_sections()` so saving profile, Identify, or Rename settings
rewrites all three canonical sections without losing either of the others.

Use the exact root metadata key:

```python
key = f"root:{self.directory}:rename_settings"
```

Malformed JSON returns `None` for a library override; the caller then uses the
global fallback.

- [ ] **Step 6: Run focused persistence tests**

Run: `source ~/.venvs/ysbpy/bin/activate && pytest tests/test_pdf_rename_settings.py tests/test_config_setup.py tests/test_pdf_registry.py -q`

Expected: all selected tests pass.

---

### Task 2: GUI Watch Policy and Remembered Rename Dialog

**Files:**
- Modify: `src/adsfolio/gui/config.py`
- Modify: `src/adsfolio/gui/dialogs.py`
- Modify: `tests/test_gui_config.py`
- Modify: `tests/test_gui_dialogs.py`

**Interfaces:**
- Consumes: `RenameSettings` from Task 1.
- Produces: `RootEntry.watch_on_start`, `RootEntry.auto_process_new_pdfs`, `GuiConfig.rename_scopes`, and a scope-aware `RenameDialog`.
- Consumed by: Task 5.

- [ ] **Step 1: Write failing GUI configuration migration tests**

```python
def test_version_two_config_loads_safe_watch_defaults(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    config_path().write_text(
        json.dumps({"version": 2, "roots": [{"path": "/papers"}]}),
        encoding="utf-8",
    )
    loaded = load_config()
    assert loaded.version == 3
    assert not loaded.roots[0].watch_on_start
    assert not loaded.roots[0].auto_process_new_pdfs


def test_watch_policy_and_rename_scope_roundtrip(monkeypatch, tmp_path):
    _use_tmp_config(monkeypatch, tmp_path)
    root = str((tmp_path / "papers").resolve())
    expected = GuiConfig(
        roots=[RootEntry(root, watch_on_start=True, auto_process_new_pdfs=True)],
        rename_scopes={root: "library"},
    )
    save_config(expected)
    assert load_config() == expected
```

- [ ] **Step 2: Implement schema version 3 and field-by-field migration**

Set `GUI_CONFIG_VERSION = 3`; parse each root boolean with `_as_bool(value,
False)`; normalize `rename_scopes` exactly like `identify_scopes`; and always
return the current schema version after loading. Unknown and malformed fields
remain ignored independently.

- [ ] **Step 3: Write failing Rename dialog scope tests**

```python
def test_rename_dialog_switches_scope_and_saves_before_apply(application, monkeypatch):
    calls = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(template="{bibcode}"),
        RenameSettings(template="{title}", max_authors=6),
        initial_scope="library",
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    dialog.accept()
    assert calls == [("library", dialog.selected_settings())]


def test_rename_dialog_cancel_does_not_save(application):
    calls = []
    dialog = RenameDialog(
        Path("/papers"),
        RenameSettings(),
        save_settings=lambda scope, settings: calls.append((scope, settings)),
    )
    dialog.reject()
    assert calls == []
```

- [ ] **Step 4: Refactor `RenameDialog` to load and save reusable settings**

Use the constructor signature
`RenameDialog(root, global_settings=None, library_settings=None, *,
initial_scope="global", save_settings=None, parent=None)`. Build the selected
value directly from the controls:

```python
def selected_settings(self) -> RenameSettings:
    return RenameSettings(
        template=self.template.text(),
        author_format=self._format(),
        max_authors=self.max_authors.value(),
        char_policy=str(self.char_policy.currentText()),
    )
```

Add **Save defaults for** with values `global` and `library`. Scope changes load
the selected values, with global values copied for an unset library scope.
`accept()` saves first; an `OSError` or `ValueError` displays a critical message
and leaves the dialog open. `apply()` calls this saving path before emitting its
job. Preview never saves. Place `keep` before `ascii` and initialize all controls
from `RenameSettings`.

- [ ] **Step 5: Run focused GUI configuration/dialog tests**

Run: `source ~/.venvs/ysbpy/bin/activate && QT_QPA_PLATFORM=offscreen pytest tests/test_gui_config.py tests/test_gui_dialogs.py -q`

Expected: all selected tests pass.

---

### Task 3: Qt-Free Watch Session State Machine

**Files:**
- Create: `src/adsfolio/pdf/watch_session.py`
- Create: `tests/test_pdf_watch_session.py`

**Interfaces:**
- Produces: `FileSnapshot`, `ArrivalIdentity`, `WatchState`, `WatchEvent`, `WatchOutcome`, and `WatchSession`.
- Consumed by: Tasks 4 and 5.

- [ ] **Step 1: Write failing baseline and identity-generation tests**

```python
def test_preexisting_identity_remains_excluded_after_modification(tmp_path):
    before = snapshot(tmp_path / "existing.pdf", device=1, inode=2, mtime_ns=3)
    session = WatchSession(Path(tmp_path), "session-1", [before])
    changed = replace(before, size_bytes=20, mtime_ns=4)
    assert session.observe(changed, WatchSource.MODIFIED) is None


def test_created_event_during_baseline_is_new_even_when_scan_sees_it(tmp_path):
    session = WatchSession.starting(Path(tmp_path), "session-1")
    created = snapshot(tmp_path / "new.pdf", device=1, inode=3)
    event = session.observe(created, WatchSource.CREATED)
    session.finish_baseline([created])
    assert event is not None
    assert event.identity not in session.baseline


def test_creation_generation_distinguishes_recycled_inode(tmp_path):
    existing = snapshot(tmp_path / "old.pdf", device=1, inode=2)
    session = WatchSession(Path(tmp_path), "session-1", [existing])
    recreated = snapshot(tmp_path / "new.pdf", device=1, inode=2)
    event = session.observe(recreated, WatchSource.CREATED)
    assert event is not None
    assert event.identity.generation == 1
```

- [ ] **Step 2: Verify the state-machine tests fail**

Run: `source ~/.venvs/ysbpy/bin/activate && pytest tests/test_pdf_watch_session.py -q`

Expected: collection fails because `watch_session` is absent.

- [ ] **Step 3: Implement immutable event types and session transitions**

```python
class WatchState(str, Enum):
    DETECTED = "detected"
    PROCESSING = "processing"
    DEFERRED = "deferred"
    DISMISSED = "dismissed"
    RENAMED = "renamed"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    STALE = "stale"
    FAILED = "failed"


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    birth_time_ns: int | None = None


@dataclass(frozen=True)
class ArrivalIdentity:
    device: int
    inode: int
    birth_time_ns: int | None
    generation: int


@dataclass(frozen=True)
class WatchEvent:
    root: Path
    session_id: str
    identity: ArrivalIdentity
    path: Path
    state: WatchState


@dataclass(frozen=True)
class WatchOutcome:
    event: WatchEvent
    destination: Path | None = None
    bibcode: str | None = None
    message: str | None = None
```

`WatchSession` maintains baseline physical identities, observed creation
generations, current snapshots, and per-arrival states. `CREATED` and `MOVED_IN`
can create a new generation; `MODIFIED` only updates an already eligible
arrival. Reconciliation discovers a physical identity absent from the baseline.
`dismiss()`, `begin_processing()`, `complete()`, and `retry()` reject stale
session/identity transitions with `ValueError` rather than silently acting on a
replacement path.

- [ ] **Step 4: Add and pass deterministic terminal/retry tests**

Cover these exact rules:

```python
assert session.dismiss(identity).state is WatchState.DISMISSED
assert session.observe(unchanged, WatchSource.RECONCILE) is None
assert session.complete(identity, WatchState.FAILED).state is WatchState.FAILED
assert session.retry(identity).state is WatchState.DETECTED
assert session.complete(identity, WatchState.DEFERRED).state is WatchState.DEFERRED
assert session.observe(changed, WatchSource.MODIFIED).state is WatchState.DETECTED
```

Run: `source ~/.venvs/ysbpy/bin/activate && pytest tests/test_pdf_watch_session.py -q`

Expected: all tests pass without Qt, watchdog, sleeps, or network.

---

### Task 4: Thread-Owned GUI Watch Controller

**Files:**
- Create: `src/adsfolio/gui/watch.py`
- Create: `tests/test_gui_watch_controller.py`
- Modify: `src/adsfolio/client.py`
- Modify: `tests/test_client_pdf.py`
- Modify: `src/adsfolio/pdf/watcher.py`
- Modify: `tests/test_pdf_watcher.py`

**Interfaces:**
- Consumes: Task 1 settings and Task 3 event types/state machine.
- Produces: `LibraryWatchController` with `start_root()`, `stop_root()`, `stop_all()`, `is_watching()`, `process()`, `dismiss()`, `retry()`, `pause_writes()`, and `resume_writes()`.
- Consumed by: Task 5.

- [ ] **Step 1: Add a typed adapter test around existing single-file processing**

Add `PdfWatcher.process_event()` without changing `process_new_pdf()` or CLI
behavior:

```python
def test_process_event_reports_renamed_destination(monkeypatch, tmp_path):
    outcome = watcher.process_event(
        event,
        state,
        RenameSettings(template="{bibcode}"),
        IdentifySettings(),
    )
    assert outcome.event.state is WatchState.RENAMED
    assert outcome.destination == tmp_path / "2025RNAAS...9..338Z.pdf"
```

Map `PdfNotReadyError` to `DEFERRED`, `None` to `UNRESOLVED`, an unchanged path
to `RESOLVED`, a moved path to `RENAMED`, and expected ADS/SQLite/filesystem
exceptions to `FAILED`. Resolve relative `output_unresolved` against the root.
Do not swallow unexpected programming exceptions.

Add focused cases proving a configured unresolved destination is applied,
destination collisions report the occupied target without overwrite, and a
missing/invalid ADS credential reports `FAILED` without moving the source.

- [ ] **Step 2: Write controller tests using an injected worker factory**

```python
def test_controller_manages_roots_independently(qtbot, tmp_path):
    workers = {}
    controller = LibraryWatchController(
        worker_factory=lambda root, recursive, session_id: workers.setdefault(
            root, FakeWorker(root, session_id)
        )
    )
    controller.start_root(tmp_path / "one", recursive=False)
    controller.start_root(tmp_path / "two", recursive=True)
    assert controller.is_watching(tmp_path / "one")
    controller.stop_root(tmp_path / "one")
    assert not controller.is_watching(tmp_path / "one")
    assert controller.is_watching(tmp_path / "two")


def test_obsolete_session_event_is_discarded(qtbot, tmp_path):
    controller.start_root(root, recursive=False)
    old_session = controller.session_id(root)
    controller.stop_root(root)
    controller.start_root(root, recursive=False)
    worker.detected.emit(event_for(session_id=old_session))
    assert received == []
```

- [ ] **Step 3: Implement controller ownership and injected boundaries**

```python
class LibraryWatchController(QObject):
    started = Signal(object)
    stopped = Signal(object)
    detected = Signal(object)
    outcome = Signal(object)
    failed = Signal(object)
    paused = Signal(object)
```

Implement the exact public methods `start_root(root: Path, *, recursive: bool)
-> str`, `stop_root(root: Path) -> bool`, `stop_all() -> bool`,
`is_watching(root: Path) -> bool`, `session_id(root: Path) -> str | None`,
`process(event: WatchEvent, identify_settings: IdentifySettings,
rename_settings: RenameSettings) -> None`, `dismiss(event: WatchEvent) -> None`,
`retry(event: WatchEvent) -> None`, `pause_writes(root: Path) -> None`, and
`resume_writes(root: Path) -> None`.

Normalize root keys. Create one `QThread` and worker per root. Connect worker
signals before starting. On stop, request worker shutdown and wait for its
`stopped` acknowledgment/thread finish. Return `False` if the worker cannot stop
safely; callers then keep registries/window open. Discard every event whose
session identifier differs from the current root session.

- [ ] **Step 4: Implement the production worker with thread-local resources**

On its thread, `_LibraryWatchWorker`:

1. creates `open_registry_state(root)`, `ADSClient()`, `PdfIdentifier`,
   `PdfRenamer`, and `PdfWatcher`;
2. starts watchdog event intake before calling `WatchSession.finish_baseline()`;
3. converts created, modified, moved-in, and moved-within events into snapshots;
4. reconciles with the existing watcher interval and root recursion setting;
5. emits immutable `WatchEvent` objects only;
6. executes `PdfWatcher.process_event()` synchronously on its own thread;
7. pauses processing and closes/idles its registry around foreground jobs while
   still coalescing incoming filesystem events;
8. stops the observer, closes its registry and ADS HTTP session, and then emits
   `stopped`.

Add `BaseClient.close() -> None`, implemented as `self._session.close()`, so the
worker does not reach into a private requests-session attribute. Test it with a
mock session whose `close()` call is observable.

Use Qt queued signals/slots; observer callbacks only enqueue paths and never use
ADS or SQLite. Coalesce queue keys by normalized path and event kind. If stable
device/inode data and reliable creation events are unavailable, emit one typed
failure and refuse to mark the root started.

- [ ] **Step 5: Run worker/controller regressions**

Run: `source ~/.venvs/ysbpy/bin/activate && QT_QPA_PLATFORM=offscreen pytest tests/test_pdf_watcher.py tests/test_pdf_watch_session.py tests/test_gui_watch_controller.py -q`

Expected: typed processing and controller lifecycle tests pass; existing CLI
watcher tests remain green.

---

### Task 5: Notice Bar and MainWindow Integration

**Files:**
- Create: `src/adsfolio/gui/notifications.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Create: `tests/test_gui_notifications.py`
- Create: `tests/test_gui_main_window_watch.py`
- Modify: `tests/test_gui_dialogs.py`
- Modify: `docs/gui.md`

**Interfaces:**
- Consumes: `RenameSettings`, `WatchEvent`, `WatchOutcome`, and `LibraryWatchController`.
- Produces: user-facing Watch controls, startup behavior, compact notices, manual/automatic processing, and orderly lifecycle integration.

- [ ] **Step 1: Write deterministic notice-bar queue tests**

```python
def test_notice_bar_shows_one_item_and_pending_count(qtbot):
    bar = NoticeBar()
    bar.enqueue(Notice("one", "One", action_label="Identify & Rename"))
    bar.enqueue(Notice("two", "Two", action_label="Identify & Rename"))
    assert bar.message.text() == "One"
    assert bar.pending_count.text() == "1 more"


def test_notice_replacement_does_not_duplicate_identity(qtbot):
    bar.enqueue(Notice("one", "Detected"))
    bar.enqueue(Notice("one", "Resolved"))
    assert bar.queued_keys() == ("one",)
    assert bar.message.text() == "Resolved"
```

`Notice` contains immutable display data and an opaque payload. `NoticeBar`
emits `action_requested`, `dismiss_requested`, `action_all_requested`, and
`dismiss_all_requested`; it does not store Python callbacks.

- [ ] **Step 2: Add failing MainWindow action/persistence tests with a fake controller**

```python
def test_watch_actions_are_per_root_and_persist_immediately(window, fake_controller):
    window.watch_current_action.setChecked(True)
    window.watch_current_action.trigger()
    assert fake_controller.started == [(window.current_root, False)]
    window.watch_on_start_action.setChecked(True)
    window.watch_on_start_action.trigger()
    assert window.root_entries[str(window.current_root)].watch_on_start


def test_startup_only_watches_opted_in_roots(window_factory, fake_controller):
    window = window_factory(
        roots=[
            RootEntry(str(root_one), watch_on_start=True),
            RootEntry(str(root_two), watch_on_start=False),
        ]
    )
    assert [call.root for call in fake_controller.started] == [root_one.resolve()]
```

Also test that malformed/missing startup roots produce a notice, root removal
stops the worker before closing its GUI registry, and window shutdown refuses to
close registries when `stop_all()` returns `False`. Verify changing a root's
recursive setting is rejected while that root is actively watched.

- [ ] **Step 3: Add the compact non-modal notice bar**

```python
@dataclass(frozen=True)
class Notice:
    key: str
    message: str
    severity: str = "info"
    action_label: str | None = None
    payload: object | None = None


class NoticeBar(QFrame):
    action_requested = Signal(object)
    dismiss_requested = Signal(object)
    action_all_requested = Signal(tuple)
    dismiss_all_requested = Signal(tuple)
```

Implement `enqueue(notice: Notice) -> None`, `dismiss(key: str) -> None`,
`clear_root(root: Path) -> None`, and `queued_keys() -> tuple[str, ...]`. Maintain
one ordered mapping keyed by `Notice.key`; assignment replaces an existing item
without changing queue cardinality.

Wrap the current central splitter in a zero-margin vertical container with the
notice bar above it. Only one notice is visible; the count reports remaining
items. Severity changes styling without modal dialogs.

- [ ] **Step 4: Add and synchronize the three per-root actions**

Create checkable Tools and sidebar-context actions:

- **Watch Current Library** — runtime controller state;
- **Autowatch at Start** — `RootEntry.watch_on_start`;
- **Automatically Identify & Rename** — `RootEntry.auto_process_new_pdfs`.

On root selection, block action signals while updating checked/enabled state.
Persist the latter two immediately with `save_config()`, restoring the old value
and displaying an error notice if saving fails. Display `●` beside active root
labels without changing the stored label.

- [ ] **Step 5: Resolve effective settings and route manual/automatic arrivals**

Add helpers parallel to `_identify()`: `_effective_identify_settings(root: Path)
-> IdentifySettings`, `_effective_rename_settings(root: Path) ->
RenameSettings`, `_handle_watch_detection(event: WatchEvent) -> None`,
`_process_watch_event(event: WatchEvent) -> None`, and
`_handle_watch_outcome(outcome: WatchOutcome) -> None`.

For automatic roots, call controller `process()` immediately. For manual roots,
enqueue **New PDF found: NAME** with **Identify & Rename** and **Dismiss**.
Failure notices expose **Retry** only for the same current session/identity.
Resolve Identify and Rename scope independently, using library metadata first
when that scope is selected and global settings otherwise.

- [ ] **Step 6: Preserve selection and coordinate foreground jobs**

Use a zero-interval, single-shot `QTimer` to coalesce refresh requests arriving
in one event-loop turn. Capture the current location ID before refresh and call
`_select_file_id()` afterward. If the detail pane reports unsaved edits, defer
the refresh and show a notice rather than discarding them.

Before opening Identify/Rename for an actively watched root, request
`pause_writes(root)` and continue opening only after acknowledgment. Cancelled
dialogs and completed/failed jobs call `resume_writes(root)`. Watch workers never
occupy `MainWindow.job`.

- [ ] **Step 7: Complete root/startup/shutdown lifecycle and docs**

After `_restore_config()`, schedule startup watches with `QTimer.singleShot(0,
self._start_configured_watchers)`. `_remove_root()` must stop successfully before
closing/removing that root's GUI registry. `closeEvent()` stops all watch workers
before saving state and closing any registry; on failure it ignores the close
event and displays a non-modal error.

Document all three controls, global/library Rename defaults, future-arrivals-only
semantics, automatic ADS/filesystem authorization, and the unsupported case of
running a separate CLI/`launchd` watcher on the same root.

- [ ] **Step 8: Run focused GUI integration tests**

Run: `source ~/.venvs/ysbpy/bin/activate && QT_QPA_PLATFORM=offscreen pytest tests/test_gui_notifications.py tests/test_gui_main_window_watch.py tests/test_gui_dialogs.py tests/test_gui_config.py -q`

Expected: all tests pass without native filesystem observers or ADS requests.

---

### Task 6: Added-Date Column and Newest-First Default

**Files:**
- Modify: `src/adsfolio/pdf/catalog.py`
- Modify: `src/adsfolio/pdf/registry.py`
- Modify: `src/adsfolio/gui/library.py`
- Modify: `src/adsfolio/gui/models.py`
- Modify: `src/adsfolio/gui/config.py`
- Modify: `src/adsfolio/gui/main_window.py`
- Modify: `tests/test_pdf_catalog.py`
- Modify: `tests/test_pdf_registry.py`
- Modify: `tests/test_gui_library.py`
- Modify: `tests/test_gui_models.py`
- Modify: `tests/test_gui_config.py`

**Interfaces:**
- Consumes: existing `file_locations.first_seen_at` catalog data.
- Produces: sortable/displayable `first_seen_at`, an **Added** table column before **Modified**, and default `first_seen_at` descending GUI queries.

- [ ] **Step 1: Write failing catalog and sorting tests**

Prove catalog records expose the location's original `first_seen_at`, that a
tracked rename preserves it, and `PdfRegistry.query_files(order_by="first_seen_at",
descending=True)` orders later timestamps first. Use literal ISO timestamps in
controlled temporary catalogs.

- [ ] **Step 2: Implement catalog projection and sortable registry support**

Select `l.first_seen_at` in file-record queries, expose it from
`PdfCatalog._record_from_row()`, and add `first_seen_at` to `SORTABLE_COLUMNS`.
Do not alter the value during observation updates or tracked moves.

- [ ] **Step 3: Write failing table display and position tests**

Assert `COLUMNS` places `Column("first_seen_at", "Added", ...)` immediately
before `mtime_ns`, the model formats its ISO timestamp using the configured
timestamp format, and a missing value displays blank.

- [ ] **Step 4: Implement Added formatting and model display**

Add a dedicated ISO timestamp formatter in `gui/library.py`; use it for the
Added column and keep Modified's nanosecond formatter unchanged. Apply the
model's existing timestamp-format preference to both columns.

- [ ] **Step 5: Write failing default-sort and compatibility tests**

Assert default `FileQuery` and default/missing-key `GuiConfig` use
`order_by="first_seen_at"` and `descending=True`; assert an explicitly persisted
legacy sort such as `title` ascending still round-trips unchanged.

- [ ] **Step 6: Implement newest-first defaults**

Update `FileQuery`, `GuiConfig`, config loading fallbacks, and MainWindow's
invalid/missing sort fallback. Do not overwrite valid saved sort preferences.

- [ ] **Step 7: Run focused tests**

Run: `source ~/.venvs/ysbpy/bin/activate && QT_QPA_PLATFORM=offscreen pytest tests/test_pdf_catalog.py tests/test_pdf_registry.py tests/test_gui_library.py tests/test_gui_models.py tests/test_gui_config.py -q`

Expected: all selected tests pass.

---

### Task 7: Full Verification and Review

**Files:**
- Modify only files identified by failures attributable to this feature.

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces: a formatted, regression-tested implementation and evidence-backed handoff.

- [ ] **Step 1: Run import sorting and formatting on changed Python files**

Run: `source ~/.venvs/ysbpy/bin/activate && isort src/adsfolio/client.py src/adsfolio/config.py src/adsfolio/pdf/rename_settings.py src/adsfolio/pdf/registry.py src/adsfolio/pdf/watch_session.py src/adsfolio/pdf/watcher.py src/adsfolio/gui/config.py src/adsfolio/gui/dialogs.py src/adsfolio/gui/watch.py src/adsfolio/gui/notifications.py src/adsfolio/gui/main_window.py tests/test_client_pdf.py tests/test_pdf_rename_settings.py tests/test_pdf_watch_session.py tests/test_gui_watch_controller.py tests/test_gui_notifications.py tests/test_gui_main_window_watch.py`

Run: `source ~/.venvs/ysbpy/bin/activate && ruff format src/adsfolio tests`

- [ ] **Step 2: Run static quality checks**

Run: `source ~/.venvs/ysbpy/bin/activate && ruff check src/adsfolio tests`

If a type checker is configured in `pyproject.toml`, run its configured command;
otherwise record that no configured type-check target exists.

- [ ] **Step 3: Run the complete test suite**

Run: `source ~/.venvs/ysbpy/bin/activate && QT_QPA_PLATFORM=offscreen pytest -q`

Expected: all tests pass, with only already-known documented skips.

- [ ] **Step 4: Review requirement coverage and dirty-worktree scope**

Check that every spec behavior has a focused test, inspect `git diff --check`, and
list only files changed for this feature. Confirm no real library path, ADS token,
or runtime watcher activation entered tests or source defaults.

- [ ] **Step 5: Perform architecture, correctness, and user-advocate review**

Review for thread ownership, stale sessions, shutdown ordering, settings
migration, UI ergonomics, and regression compatibility. Apply only findings
supported by code/tests, rerunning the smallest affected test set after each
change and the complete suite after the final change.
