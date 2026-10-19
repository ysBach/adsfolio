# ADS Library Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users add selected or all locally identified papers to an existing or newly created writable ADS library through an explicit, responsive, recoverable GUI workflow.

**Architecture:** A Qt-free synchronization service collects and deduplicates local Bibcodes, paginates remote library state, and performs API-sized additions with structured partial results. A one-operation Qt worker owns its ADS client and session off the UI thread. A dedicated dialog manages source scope, destination selection/creation, duplicate preflight, confirmation, progress, cancellation boundaries, and retry reconciliation.

**Tech Stack:** Python 3.10+, dataclasses, requests, PySide6 `QObject`/`QThread`, pytest, pytest-qt, Ruff, isort.

**Spec:** `docs/superpowers/specs/2026-09-03-custom-citation-styles-ads-library-design.md`

## Global Constraints

- Local PDF-library roots and remote ADS libraries are different concepts and must be labeled distinctly.
- Source scope is either currently selected loaded rows or every resolved record in the current local root; all-identified ignores search, virtual view, and table pagination.
- Eligibility requires a resolved record and non-empty Bibcode.
- Deduplicate Bibcodes deterministically while preserving first local occurrence.
- Only `owner`, `admin`, and `write` destinations are selectable; display `read` and unknown permissions disabled.
- Creating a library creates it empty, returns to the destination list, and does not implicitly add pending papers.
- Fetch remote documents before confirmation when possible; report already-present count as unknown when preflight fails.
- Require one final confirmation immediately before any document-add request.
- Submit at most 2,000 Bibcodes per request because that is the ADS/SciX contract limit.
- Stop after the first failed request; retain acknowledged batches and distinguish failed, status-unknown, and unattempted Bibcodes.
- A retry first reloads remote state and submits only Bibcodes still absent.
- Network resources belong to the worker thread; no Qt widget, mutable catalog, SQLite connection, or requests session crosses thread boundaries.
- Do not mutate PDFs, paths, local metadata, registry state, Rename settings, or watch state.
- Do not issue live ADS mutations during automated or manual verification.
- Use `source ~/.venvs/ysbpy/bin/activate` and `uv run --active ...` for Python commands.
- Use test-driven development, run isort and Ruff formatting before each code commit, and do not modify the original checkout.

---

### Task 1: Bounded HTTP requests and complete raw library pagination

**Files:**
- Modify: `src/adsfolio/client.py`
- Modify: `src/adsfolio/api/libraries.py`
- Create: `tests/test_client_http.py`
- Create: `tests/test_api_libraries.py`

**Interfaces:**
- Consumes: existing `BaseClient` request/error handling and `Library.from_response()`.
- Produces: `BaseClient(timeout=(3.05, 30.0))`,
  `LibrariesAPI.iter_libraries()`, and
  `LibrariesAPI.iter_documents(library_id, raw=False)`.

- [ ] **Step 1: Write failing timeout and pagination tests**

In `tests/test_client_http.py`, mock the session and prove every HTTP verb passes
the configured timeout without changing headers or payloads:

```python
def test_base_client_passes_configured_timeout_to_get(monkeypatch) -> None:
    client = BaseClient(token="token", timeout=(3.05, 30.0))
    response = Mock(ok=True, status_code=200, headers={}, json=lambda: {})
    get = Mock(return_value=response)
    monkeypatch.setattr(client._session, "get", get)
    client.get("/test", params={"q": "x"})
    assert get.call_args.kwargs["timeout"] == (3.05, 30.0)
```

Repeat for `post`, `post_raw`, `put`, and `delete`. Verify a mocked
`requests.Timeout` propagates unchanged so the GUI can classify it.

In `tests/test_api_libraries.py`, use a fake base client and assert exact `start`
offsets. `iter_libraries(rows=2)` must request starts 0, 2, and 4 for response
lengths 2, 2, and 0. `iter_documents(..., rows=2)` must combine pages without
dropping metadata and stop when `num_documents` is reached or a page is short.
Also test an empty page, a final count smaller than metadata, and a repeated
page; repeated pages must raise `ADSError` instead of looping forever.

- [ ] **Step 2: Run API tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_client_http.py tests/test_api_libraries.py
```

Expected: failures identify missing timeout arguments and iterator methods.

- [ ] **Step 3: Add configurable request timeouts**

Add a typed `timeout` parameter to `BaseClient.__init__`, defaulting to
`(3.05, 30.0)`, store it, and pass `timeout=self._timeout` to every requests
session call. Add the same optional parameter to `ADSClient.__init__` and pass
it to `BaseClient`. Do not add retries: automatic retry of a document-add POST
could duplicate an operation with unknown server outcome.

- [ ] **Step 4: Add lazy pagination methods**

Implement
`iter_libraries(*, rows: int = 100, sort: Literal["date_created",
"date_last_modified"] = "date_created", order: Literal["asc", "desc"] =
"asc") -> Iterator[Library]` and
`iter_documents(library_id: str, *, raw: bool = False, rows: int = 2000) ->
Iterator[str]`.

Validate `rows > 0`; reject `rows > 2000` for documents. Advance by the number
actually returned. Track the previous page's tuple and raise `ADSError` if a
non-empty page repeats at a later offset. For documents, use the first page's
`num_documents` as a completion target when positive, but also stop on an
empty or short page. Preserve existing one-page `list_libraries` and
`get_library` APIs for current callers.

- [ ] **Step 5: Run focused quality and API tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsfolio/client.py src/adsfolio/api/libraries.py tests/test_client_http.py tests/test_api_libraries.py
uv run --active ruff format src/adsfolio/client.py src/adsfolio/api/libraries.py tests/test_client_http.py tests/test_api_libraries.py
uv run --active ruff check src/adsfolio/client.py src/adsfolio/api/libraries.py tests/test_client_http.py tests/test_api_libraries.py
uv run --active pytest -q tests/test_client_http.py tests/test_api_libraries.py tests/test_client_pdf.py tests/test_search_query_all.py
```

Expected: all focused client/API tests pass.

- [ ] **Step 6: Commit HTTP and pagination behavior**

```bash
git add src/adsfolio/client.py src/adsfolio/api/libraries.py tests/test_client_http.py tests/test_api_libraries.py
git commit -m "feat: paginate ADS libraries safely"
```

---

### Task 2: Qt-free local selection and ADS synchronization service

**Files:**
- Create: `src/adsfolio/library_sync.py`
- Create: `tests/test_library_sync.py`

**Interfaces:**
- Consumes: `ADSClient.libraries.iter_libraries()`, `iter_documents()`, `create_library()`, and `add_documents()`.
- Produces: `BibcodeSelection`, `LibraryDestination`, `AddDocumentsResult`, `collect_identified_bibcodes(records)`, and `AdsLibraryService`.

- [ ] **Step 1: Write failing local-selection tests**

Use mixed records and assert exact stable-order results:

```python
def test_collect_identified_bibcodes_deduplicates_and_counts_exclusions() -> None:
    result = collect_identified_bibcodes(
        [
            {"status": "resolved", "document_id": 1, "metadata": {"bibcode": "B2"}},
            {"status": "unresolved", "metadata": {"bibcode": "BAD"}},
            {"status": "resolved", "document_id": 2, "metadata": {"bibcode": "B1"}},
            {"status": "resolved", "document_id": 3, "metadata": {"bibcode": "B2"}},
            {"status": "resolved", "document_id": 4, "metadata": {}},
        ]
    )
    assert result.bibcodes == ("B2", "B1")
    assert result.source_files == 5
    assert result.ineligible_files == 2
    assert result.local_duplicate_files == 1
```

Test Bibcodes available at top level or nested metadata, whitespace trimming,
and records that claim `resolved` without a `document_id`.

- [ ] **Step 2: Write failing service tests**

With a fake `ADSClient`, test:

- list destinations retains API order and marks only owner/admin/write writable;
- create trims and validates a non-empty name, passes description/public, and
  always uses `bibcodes=None`;
- preflight returns stable missing/already-present tuples;
- a 4,001-item input calls `add_documents` with lengths 2,000, 2,000, and 1;
- an `ADSError` on batch two returns batch one as acknowledged, batch two as
  failed, and batch three as unattempted;
- `requests.Timeout` or `requests.ConnectionError` marks the active batch
  status-unknown and later batches unattempted;
- a cancellation predicate that becomes true after batch one stops before the
  next request and returns the remainder as unattempted;
- retry reconciliation fetches remote documents and submits only absent
  Bibcodes.

- [ ] **Step 3: Run service tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_library_sync.py
```

Expected: collection fails because `adsfolio.library_sync` does not exist.

- [ ] **Step 4: Implement immutable result types and selection**

Implement frozen dataclasses with tuple fields:

```python
@dataclass(frozen=True)
class BibcodeSelection:
    bibcodes: tuple[str, ...]
    source_files: int
    ineligible_files: int
    local_duplicate_files: int


@dataclass(frozen=True)
class LibraryDestination:
    id: str
    name: str
    permission: str
    public: bool
    num_documents: int

    @property
    def writable(self) -> bool:
        return self.permission in {"owner", "admin", "write"}


@dataclass(frozen=True)
class AddDocumentsResult:
    acknowledged: tuple[str, ...] = ()
    already_present: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    status_unknown: tuple[str, ...] = ()
    unattempted: tuple[str, ...] = ()
    cancelled: bool = False
    error: Exception | None = None
```

Use the existing `is_resolved_record()` contract plus a non-empty Bibcode for
eligibility. Deduplicate with an insertion-ordered dictionary/set pattern.

- [ ] **Step 5: Implement `AdsLibraryService`**

The service receives `ADSClient` in its constructor and does not close it.
Implement `AdsLibraryService.MAX_DOCUMENTS_PER_REQUEST = 2000` and these exact
methods:

- `list_destinations() -> tuple[LibraryDestination, ...]`;
- `create_destination(name: str, description: str, public: bool) ->
  LibraryDestination`;
- `preflight(library_id: str, bibcodes: Sequence[str]) -> AddDocumentsResult`;
- `add_documents(library_id: str, bibcodes: Sequence[str], *,
  already_present: Sequence[str] = (), cancelled: Callable[[], bool] = lambda:
  False) -> AddDocumentsResult`;
- `reconcile_and_retry(library_id: str, bibcodes: Sequence[str], *, cancelled:
  Callable[[], bool] = lambda: False) -> AddDocumentsResult`.

Normalize/deduplicate input once. Reject a non-writable destination before the
first POST when its permission is known. Catch only `requests.Timeout` and
`requests.ConnectionError` as uncertain transport outcomes; catch `ADSError`
as a failed response; allow programming errors to propagate. Check cancellation
before each request, never retry a POST automatically, and concatenate result
tuples in source order.

- [ ] **Step 6: Run service quality and behavior tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsfolio/library_sync.py tests/test_library_sync.py
uv run --active ruff format src/adsfolio/library_sync.py tests/test_library_sync.py
uv run --active ruff check src/adsfolio/library_sync.py tests/test_library_sync.py
uv run --active pytest -q tests/test_api_libraries.py tests/test_library_sync.py
```

Expected: all tests pass with exact batch boundaries and result categories.

- [ ] **Step 7: Commit the synchronization service**

```bash
git add src/adsfolio/library_sync.py tests/test_library_sync.py
git commit -m "feat: add ADS library synchronization service"
```

---

### Task 3: Thread-owned ADS library jobs

**Files:**
- Create: `src/adsfolio/gui/ads_library_jobs.py`
- Create: `tests/test_gui_ads_library_jobs.py`

**Interfaces:**
- Consumes: `ADSClient`, `AdsLibraryService`, `LibraryDestination`, and `AddDocumentsResult`.
- Produces: `AdsLibraryOperation`, `AdsLibraryRequest`, `AdsLibraryFailure`, and `AdsLibraryJob` with `succeeded`, `failed`, and `finished` signals.

- [ ] **Step 1: Write failing worker lifecycle tests**

Create a fake client factory that records creation, thread identity, calls, and
close. Test LIST, LOAD_DOCUMENTS, CREATE, ADD, and RETRY requests. Assert:

- the client is created and closed in the worker thread for success and error;
- only immutable request/result values cross signals;
- cancellation sets an event observed before the next add request;
- one terminal success/failure and one finished signal are emitted;
- authentication, rate-limit, timeout, connection, and general ADS exceptions
  map to distinct `AdsLibraryFailure.kind` values;
- no `PdfRegistry` or widget is accepted by the request type.

- [ ] **Step 2: Run job tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_ads_library_jobs.py
```

Expected: collection fails because the jobs module does not exist.

- [ ] **Step 3: Implement one-shot request and failure types**

Use:

```python
class AdsLibraryOperation(str, Enum):
    LIST = "list"
    LOAD_DOCUMENTS = "load_documents"
    CREATE = "create"
    ADD = "add"
    RETRY = "retry"


@dataclass(frozen=True)
class AdsLibraryRequest:
    operation: AdsLibraryOperation
    library_id: str = ""
    bibcodes: tuple[str, ...] = ()
    name: str = ""
    description: str = ""
    public: bool = False
    already_present: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdsLibraryFailure:
    kind: Literal["authentication", "rate_limit", "timeout", "connection", "ads", "unexpected"]
    message: str
```

`LOAD_DOCUMENTS` returns a tuple of Bibcodes from
`client.libraries.iter_documents`; LIST returns destinations; CREATE returns a
destination; ADD/RETRY return `AddDocumentsResult`.

- [ ] **Step 4: Implement `AdsLibraryJob` and private worker**

The GUI-thread `AdsLibraryJob` owns `QThread`, a private worker, and a
`threading.Event`. The worker receives only `AdsLibraryRequest`, a client
factory, and the event. In its `run` slot it creates `ADSClient()`, constructs
`AdsLibraryService`, dispatches exactly one operation, emits the typed result,
closes the client in `finally`, and quits the thread. `cancel()` only sets the
event; it does not call `QThread.terminate()` or interrupt an in-flight request.
Delete worker/thread objects with `deleteLater` after completion.

- [ ] **Step 5: Run worker quality and lifecycle tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsfolio/gui/ads_library_jobs.py tests/test_gui_ads_library_jobs.py
uv run --active ruff format src/adsfolio/gui/ads_library_jobs.py tests/test_gui_ads_library_jobs.py
uv run --active ruff check src/adsfolio/gui/ads_library_jobs.py tests/test_gui_ads_library_jobs.py
uv run --active pytest -q tests/test_library_sync.py tests/test_gui_ads_library_jobs.py
```

Expected: all jobs close their client and thread cleanly.

- [ ] **Step 6: Commit the worker unit**

```bash
git add src/adsfolio/gui/ads_library_jobs.py tests/test_gui_ads_library_jobs.py
git commit -m "feat: run ADS library work off the UI thread"
```

---

### Task 4: ADS library destination and confirmation dialog

**Files:**
- Create: `src/adsfolio/gui/ads_library_dialog.py`
- Create: `tests/test_gui_ads_library_dialog.py`

**Interfaces:**
- Consumes: `BibcodeSelection`, `collect_identified_bibcodes()`, `AdsLibraryJob`, `AdsLibraryRequest`, `LibraryDestination`, and `AddDocumentsResult`.
- Produces: `CreateAdsLibraryDialog` and
  `AdsLibraryDialog(root, selected_records, all_records, job_factory=AdsLibraryJob,
  parent=None)`.

- [ ] **Step 1: Write failing source-scope and destination tests**

Use fake one-shot jobs whose results are delivered synchronously by tests.
Assert:

- Selected rows is enabled/default when selected records exist; otherwise it is
  disabled and All identified is selected;
- changing scope recomputes exact source, eligible, ineligible, and local
  duplicate counts without an ADS request;
- destination rows display name/count/public/permission and disable read or
  unknown permissions;
- Refresh starts LIST and disables conflicting controls until completion;
- selecting a writable destination starts LOAD_DOCUMENTS and computes an exact
  already-present count;
- preflight failure displays `Unknown` and keeps confirmation available with an
  explicit warning;
- Create validates name, launches CREATE with no Bibcodes, inserts/selects the
  result, and still makes no ADD request.

- [ ] **Step 2: Write failing confirmation, result, and retry tests**

Intercept `QMessageBox.question` and assert ADD cannot start without an
affirmative confirmation. Verify the confirmation text contains destination,
permission, eligible unique count, already-present or Unknown, ineligible,
local duplicates, and submit count.

Test complete success, partial failure, status-unknown transport outcome,
cancelled remainder, and Retry Remaining. Retry must issue RETRY with the union
of failed/status-unknown/unattempted Bibcodes in original order. If retry
reconciliation fails, show a second explicit warning before allowing raw
resubmission.

- [ ] **Step 3: Run dialog tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_ads_library_dialog.py
```

Expected: collection fails because the dialog module does not exist.

- [ ] **Step 4: Implement create and main dialogs**

`CreateAdsLibraryDialog` contains name, description, and public controls and
returns trimmed values only after non-empty-name validation.

`AdsLibraryDialog` receives record snapshots, not a registry. Compute two
`BibcodeSelection` objects at construction. Build source radio buttons, count
labels, destination table/list, Refresh/Create controls, status/progress label,
Cancel, and `Add to ADS Library` button. Start one `AdsLibraryJob` at a time and
retain it until `finished`. Disable controls that could invalidate the active
request. Store destination documents only after a complete LOAD_DOCUMENTS
result; otherwise store `None` to mean unknown.

Before ADD, derive `to_submit` by subtracting known destination documents while
preserving source order. Empty `to_submit` reports that everything is already
present without opening a mutation confirmation. On results, display all
categories from `AddDocumentsResult` and enable Retry Remaining only when a
non-empty remainder exists.

- [ ] **Step 5: Implement close and cancellation boundaries**

While a job runs, dialog Cancel/close calls `job.cancel()`, disables itself, and
waits for the job's normal `finished` signal before rejecting. Do not spin a
nested unbounded event loop and do not terminate the thread. For ADD, the
cancel message states that an in-flight request may finish and cancellation
takes effect before the next batch.

- [ ] **Step 6: Run dialog quality and behavior tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsfolio/gui/ads_library_dialog.py tests/test_gui_ads_library_dialog.py
uv run --active ruff format src/adsfolio/gui/ads_library_dialog.py tests/test_gui_ads_library_dialog.py
uv run --active ruff check src/adsfolio/gui/ads_library_dialog.py tests/test_gui_ads_library_dialog.py
uv run --active pytest -q tests/test_library_sync.py tests/test_gui_ads_library_jobs.py tests/test_gui_ads_library_dialog.py
```

Expected: all dialog behavior tests pass without live network access.

- [ ] **Step 7: Commit the dialog unit**

```bash
git add src/adsfolio/gui/ads_library_dialog.py tests/test_gui_ads_library_dialog.py
git commit -m "feat: add ADS library destination dialog"
```

---

### Task 5: Main-window selection and action integration

**Files:**
- Modify: `src/adsfolio/gui/main_window.py`
- Modify: `tests/test_gui_detail_pane.py`
- Create: `tests/test_gui_ads_library_main_window.py`

**Interfaces:**
- Consumes: `AdsLibraryDialog` and the existing `PdfRegistry.query_files(resolved=True)` API.
- Produces: Tools/context-menu `Add to ADS Library…` actions and immutable
  selected/all record snapshots.

- [ ] **Step 1: Write failing main-window tests**

Test that the Tools action is disabled without a current root and enabled with
one. For table context behavior, select rows 0 and 1, right-click row 1, and
assert both remain selected; right-click unselected row 2 and assert row 2
becomes the sole selection.

Monkeypatch `AdsLibraryDialog` and assert `_add_to_ads_library()` passes:

- records corresponding to `selectionModel().selectedRows()` in visual row
  order;
- all records from `registry.query_files(resolved=True)` independent of
  `current_query.search`, `current_view`, and whether the model has fetched all
  pages;
- the normalized current root;
- no registry object, ADS client, or SQLite connection.

Test `sqlite3.OperationalError("database is locked")` shows a busy message and
does not open the dialog; other SQLite errors propagate.

- [ ] **Step 2: Run integration tests and verify RED**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active pytest -q tests/test_gui_ads_library_main_window.py tests/test_gui_detail_pane.py
```

Expected: failures identify missing action and dialog-launch method.

- [ ] **Step 3: Add Tools and context-menu actions**

Create `self.add_to_ads_library_action = QAction("Add to ADS Library…", self)`,
connect it to `_add_to_ads_library`, and add it near ADS refresh actions. Add
the same label to `_table_menu`. Before building a context menu, if the clicked
valid row is not selected, clear selection and select that entire row.

- [ ] **Step 4: Snapshot records and open the dialog**

Implement `_selected_records()` returning deep-copied records for unique
selected row indexes sorted by row. `_add_to_ads_library()` requires a current
root, snapshots selected records, and queries
`self.registries[str(self.current_root)].query_files(resolved=True)` for the
all-identified candidate snapshot. Catch only SQLite busy/locked operational
errors as user-facing busy state. Pass immutable/deep-copied record sequences
to `AdsLibraryDialog`; do not pass the registry.

Update action enabled state when roots/selections/jobs change. The ADS library
dialog is independent of `CommandJob`, but only one ADS library dialog may be
open per main window.

- [ ] **Step 5: Run main-window quality and regression tests**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort src/adsfolio/gui/main_window.py tests/test_gui_ads_library_main_window.py tests/test_gui_detail_pane.py
uv run --active ruff format src/adsfolio/gui/main_window.py tests/test_gui_ads_library_main_window.py tests/test_gui_detail_pane.py
uv run --active ruff check src/adsfolio/gui/main_window.py tests/test_gui_ads_library_main_window.py tests/test_gui_detail_pane.py
uv run --active pytest -q tests/test_gui_ads_library_main_window.py tests/test_gui_ads_library_dialog.py tests/test_gui_detail_pane.py tests/test_gui_main_window_watch.py
```

Expected: new actions and all existing table/watch behavior pass.

- [ ] **Step 6: Commit main-window integration**

```bash
git add src/adsfolio/gui/main_window.py tests/test_gui_ads_library_main_window.py tests/test_gui_detail_pane.py
git commit -m "feat: add papers to ADS libraries from the GUI"
```

---

### Task 6: ADS library documentation and complete verification

**Files:**
- Modify: `docs/gui.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: completed ADS library behavior from Tasks 1–5.
- Produces: user instructions and verified feature release candidate.

- [ ] **Step 1: Document the exact workflow and safety model**

Document:

- the distinction between local PDF roots and remote ADS libraries;
- Tools and context-menu entry points;
- Selected rows and All identified scope semantics;
- resolved/Bibcode eligibility and local deduplication;
- writable permission levels and disabled read/unknown destinations;
- empty library creation;
- remote duplicate preflight and Unknown fallback;
- final confirmation, 2,000-Bibcode API batches, cancellation boundary, partial
  results, status-unknown transport failures, and reconciliation-first retry.

State explicitly that this feature never renames/moves PDFs or edits the local
catalog, and that opening the dialog makes read-only ADS requests while the
document-add POST occurs only after confirmation.

- [ ] **Step 2: Run complete automated verification**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort --check-only src tests
uv run --active ruff format --check src tests
uv run --active ruff check src tests
uv run --active pytest -q
git diff --check
```

Expected: 0 failures, 0 lint errors, no formatting changes required, and no
whitespace errors.

- [ ] **Step 3: Run an offline GUI smoke test**

Run the ADS library dialog through the pytest fake job/client harness in an
interactive Qt session or a focused `pytest-qt` test. Verify destination load,
scope switch, create-empty result, confirmation, progress, partial result, and
retry. Do not use a real token and do not mutate a live ADS library.

- [ ] **Step 4: Commit documentation and verification-ready state**

```bash
git add README.md docs/gui.md
git commit -m "docs: explain ADS library integration"
```

---

### Task 7: Combined feature verification

**Files:**
- Test only: all changed source, tests, and documentation from both plans.

**Interfaces:**
- Consumes: completed custom-citation and ADS-library plans.
- Produces: one verified branch ready for review/integration.

- [ ] **Step 1: Verify combined formatting, lint, and tests from a clean index**

```bash
source ~/.venvs/ysbpy/bin/activate
uv run --active isort --check-only src tests
uv run --active ruff format --check src tests
uv run --active ruff check src tests
uv run --active pytest -q
git status --short
```

Expected: all checks pass and `git status --short` is empty.

- [ ] **Step 2: Audit no unintended mutations or API calls**

Use `rg` to confirm citation rendering imports no ADS client and library sync
imports no PDF move/rename functions:

```bash
rg -n "ADSClient|BaseClient|requests" src/adsfolio/citations.py
rg -n "move_tracked_file|PdfRenamer|replace_file_metadata|set_rename_settings" src/adsfolio/library_sync.py src/adsfolio/gui/ads_library_*.py
```

Expected: both commands return no matches.

- [ ] **Step 3: Record final evidence without a live remote mutation**

Report the exact test count, formatting/lint results, commit list, branch name,
worktree path, and any remaining uncertainty about live ADS response shapes.
Do not claim a live ADS addition was verified unless the user separately
authorizes one against a disposable library.
