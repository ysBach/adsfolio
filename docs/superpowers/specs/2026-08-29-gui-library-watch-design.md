# GUI Library Watch and Remembered Rename Design

## Goal

Extend the ADSFOLIO desktop application so each configured PDF library can be
watched on demand. A watch session baselines the PDFs already present and
reports only files added afterward. Depending on the selected policy, the GUI
either offers a one-click **Identify & Rename** action or automatically
identifies and renames the new file with the last accepted settings.

Identify and Rename remember reusable answers globally or per library, so a
new-file action does not require reopening both dialogs. All watch and
automatic-mutation options default to off.

This implementation changes repository code, tests, and documentation only.
It does not enable a watcher, contact ADS, rename PDFs, or mutate a live catalog
during development or verification.

## User-visible controls

The Tools menu and the active library's context menu expose three independent,
checkable controls:

- **Watch Current Library** starts or stops the current session's watcher.
- **Autowatch at Start** persists per library and starts its watcher when the
  application starts.
- **Automatically Identify & Rename** persists per library and controls how
  future arrivals are handled while that library is watched.

`Autowatch at Start` does not mean that the library is processed immediately
when the option is selected. It changes the next application-start behavior.
Turning **Watch Current Library** on starts a fresh baseline; turning it off
stops the worker. Turning it on again creates another fresh baseline.

Enabling automatic processing is an explicit authorization for future PDF
arrivals in that library to make ADS requests and perform filesystem renames.
It never authorizes processing a file that existed when the current watch
session began.

## Persistent settings

### Watch policy

Add the following per-root fields to the GUI configuration, using domain names
that remain separate from the UI labels:

- `watch_on_start: bool`, default `False`;
- `auto_process_new_pdfs: bool`, default `False`.

Runtime watch state is not persisted. Root keys use the same normalized paths
as the existing GUI root configuration. Missing, malformed, or legacy values
fall back independently to `False`.

### Identify settings

Continue using the existing global/per-library `IdentifySettings` persistence
and its effective-resolution rule: library settings when present and selected,
otherwise global settings, then built-in defaults.

Automatic processing may enable the operational permissions needed for the
explicitly authorized action, but it does not persist or silently reuse the
Identify dialog's one-run controls. In particular, `refresh_cache`, `allow_ads`,
and `apply_moves` remain excluded from remembered Identify settings.

### Rename settings

Introduce a domain-level immutable `RenameSettings` value object containing
only reusable inputs:

- filename template;
- author format;
- maximum displayed authors;
- Unicode/ASCII character policy.

It provides field-by-field normalization and mapping serialization. Malformed
fields fall back independently and log a warning. The built-in values match the
effective defaults of the rename backend; the character policy defaults to
Unicode-preserving `keep`.

Global Rename settings live under the `rename` section of the normal setup
configuration and are accessed through `Config.load_rename_settings()` and
`Config.save_rename_settings()`. Library-specific Rename settings use
`CentralPdfRegistryState.rename_settings` and
`CentralPdfRegistryState.set_rename_settings()` with the catalog metadata key
`root:<normalized-root>:rename_settings`. The GUI stores
`rename_scopes: dict[str, str]` in `gui.json`, mapping normalized root paths to
`global` or `library`, parallel to `identify_scopes`.

The Rename dialog adds the same **Remember settings** scope selector as
Identify. Switching scope loads that scope. Selecting a new library scope
initializes it from current global settings. Cancel writes nothing; accepting
Rename validates and immediately saves the reusable fields before running the
rename operation. The GUI configuration schema advances from version 2 to 3;
version-2 files load with both watch flags off and empty Rename scopes.

For a watched file, effective Identify and Rename settings are resolved
separately. Each uses the library-specific settings when configured, with a
global fallback. Therefore one library may override only Rename while still
using global Identify settings.

## Runtime architecture

### Controller and worker ownership

Add one GUI watch controller owned by `MainWindow`. It owns at most one
background worker per watched root and exposes typed start, stop, detection,
outcome, and failure signals.

Each worker owns and closes its own ADS client and catalog/registry connection.
No SQLite connection, Qt widget, or mutable catalog object crosses thread
boundaries. The GUI thread sends immutable commands/settings to workers and
receives immutable event payloads through queued Qt signals.

The controller API is root- and session-specific:

- `start_root(root, recursive)` returns a new opaque session identifier after
  event intake and baseline construction are ready;
- `stop_root(root)` stops event intake, finishes an atomic operation if one is
  running, closes worker-owned resources, and acknowledges termination;
- `pause_writes(root)` and `resume_writes(root)` quiesce worker catalog writes
  around a foreground subprocess without discarding filesystem events;
- `process(session_id, identity, identify_settings, rename_settings)` snapshots
  immutable effective settings and submits one still-current arrival.

Every command and event carries the normalized root and session identifier.
Events from an obsolete session are discarded, so a stale banner cannot act on
a restarted watcher. Before processing a manual banner action, the worker
resolves the identity to its current path and verifies that the path still has
the same identity. A disappeared or replaced identity produces `stale` rather
than processing a different file.

Continuous watch work is separate from the existing single `CommandJob`, which
remains for short-lived CLI subprocess actions. Consequently a running watcher
does not disable Identify, Rename, or other normal GUI commands for the entire
session.

### Baseline and new-file identity

Watching and baseline enumeration use `RootEntry.recursive`; the remembered
`IdentifySettings.recursive` controls explicit bulk Identify only. Changing the
root recursion flag is blocked while that root is watched, so the scope cannot
change silently during a session.

Activation enters a `starting` state and establishes filesystem-event intake
before baseline enumeration. It records every PDF identity already present at
that point, while separately retaining creation/move-in events observed during
enumeration. An identity delivered by a post-intake creation event is eligible
even if it appears in the scan; a partial file that existed before event intake
remains preexisting even when later writes complete it. This closes the normal
scan/event race without interpreting a file modification as a new arrival.

The stable identity is `(device, inode, birth_time_ns)` when the platform
reports birth time, otherwise `(device, inode)` plus the session's observed
creation generation. Baseline identities remain excluded for that session,
even if an existing file is modified or moved within the root. A later explicit
creation/move-in event creates a new generation, preventing ordinary inode
reuse from being mistaken for the baseline file. If the platform supplies
neither stable identity nor reliable creation events, watching is unavailable
for that root rather than silently weakening the preexisting-file guarantee.

A PDF is eligible only when its filesystem identity was absent from the
activation baseline and has not already reached a terminal outcome in the
session. Consequences:

- existing untracked PDFs are not automatically processed;
- modifying or renaming an existing PDF does not make it new;
- a genuinely new file is eligible regardless of whether its filename already
  contains a valid Bibcode;
- deleting a file and later creating a different PDF at the same path is a new
  arrival because its birth identity or observed creation generation differs,
  even if an inode is recycled.

The implementation must not rely only on path or modification time for this
classification. If a platform cannot provide a stable device/inode pair, the
worker reports that watching is unavailable for that root instead of weakening
the preexisting-file guarantee silently.

### Detection and processing

The controller reuses the existing reliable PDF watcher readiness and
single-file processing behavior. Filesystem events enqueue candidates, and a
periodic facts-only reconciliation catches missed events. Duplicate events for
one identity are coalesced. No ADS or SQLite work runs in an observer callback.

A detected file first enters a pending state. Readiness requires a parseable,
non-empty PDF whose filesystem facts remain unchanged across hashing. Partial
downloads are deferred without being catalogued as unresolved and are retried
only after a later filesystem change or reconciliation observes new facts.
There is no fixed sleep, size threshold, or retry-count heuristic.

In manual policy, the worker emits a detection event and waits. Clicking the
banner action submits that specific filesystem identity for processing with a
snapshot of the current effective remembered settings. Dismissing the banner
marks that identity `dismissed` for the watch session, so reconciliation does
not display it repeatedly. Dismissal does not reinterpret the file as
preexisting; it remains available through the normal manual Identify/Rename
commands.

In automatic policy, a ready detection is submitted immediately. Processing
uses ADS validation, applies the resolved rename, updates the catalog, and
preserves the existing move rollback guarantee. Automatic mode does not route
unresolved files elsewhere unless that behavior is part of the effective saved
Identify settings.

The automatic policy explicitly supplies `allow_ads=True` and `apply=True` for
the newly detected file. `apply=True` covers the resolved rename and a configured
unresolved destination; it does not alter the persisted one-run dialog flags.
A missing ADS credential or exhausted/request-failed ADS service produces a
failure notice without moving the PDF.

Switching automatic policy affects newly detected work that has not started.
An operation already running is allowed to finish to avoid leaving a file and
catalog in an indeterminate state.

Outcome retry rules are deterministic:

- `deferred` is retried only after filesystem facts change;
- `dismissed`, `renamed`, `resolved`, `unresolved`, and `stale` are terminal for
  that watch session;
- `failed` is terminal for unattended processing in that session, but its
  banner offers an explicit **Retry** action while the identity is still valid.

Periodic reconciliation therefore cannot repeatedly trigger ADS requests or
reopen an acknowledged banner for an unchanged file.

## Typed outcomes and notifications

Use immutable event payloads rather than parsing CLI output. A watch event
identifies the normalized root, source path, filesystem identity, and event
time. A processing outcome has one of these states:

- `renamed`: resolved and moved to the rendered filename;
- `resolved`: resolved but the final path was unchanged;
- `unresolved`: stable PDF, but no ADS record was identified;
- `deferred`: not ready because it was still changing or unreadable;
- `stale`: the detected identity disappeared or was replaced before processing;
- `failed`: an unexpected ADS, catalog, configuration, or filesystem error.

The main window hosts a non-modal notification banner:

- manual detection: **New PDF found** with **Identify & Rename** and
  **Dismiss** actions;
- automatic success: resolved/renamed filename;
- unresolved or deferred: concise state and file name;
- destination collision: identifies both the source and occupied destination;
- failure or unavailable startup-watch root: concise error plus a route to the
  existing job/details log and **Retry** when safe.

The banner presents one compact FIFO item and a pending-count indicator rather
than stacking widgets into the content area. Repeated status updates for the
same filesystem identity replace its queued item. A manual queue also exposes
**Identify & Rename All** and **Dismiss All**, each applying only to identities
that are still valid for the same session.

Library model/catalog refresh requests are coalesced so a burst of files does
not repeatedly rebuild the view. Before a refresh, the GUI captures the active
location identity and any pending detail edit, then restores the same selection
afterward. A background refresh must not silently discard an unrelated
selection or an unsaved detail edit. The root list displays a lightweight watch
status indicator for every active worker, including roots that are not selected.

## Startup, root changes, and shutdown

After the main window and configured roots are initialized, start workers only
for roots whose `watch_on_start` value is true. A missing or unreadable root produces
a non-modal warning and remains unwatched; startup continues.

Removing a root first stops and joins its worker. Changing the active root only
changes which controls are displayed; watchers for other configured roots keep
running. Persist checkable policy changes immediately so a crash does not lose
the user's choice.

On application shutdown, the controller stops event intake, lets any current
atomic move/catalog operation finish, stops and joins every worker, and only
then allows `MainWindow` to close its GUI-thread registry connections. A bounded
shutdown wait may report a failure and keep the window open; it must not close a
SQLite connection still owned by running work.

Before launching a foreground Identify or Rename subprocess for a watched root,
`MainWindow` obtains a `pause_writes(root)` acknowledgment. The worker keeps
collecting/coalescing filesystem events but closes or idles its write
transaction until the subprocess exits, then resumes. This prevents the two
application-owned writers from racing. Documentation states that running an
independent `adsfolio pdf watch`/`launchd` watcher on the same root concurrently is
unsupported because the GUI cannot coordinate that external writer.

## Error handling and safety

- Settings-save failures leave the control or dialog in its prior state and
  show a concise error; no operation starts with unsaved assumptions.
- ADS failures never turn a stable PDF into a falsely resolved entry.
- Readiness failures remain deferred rather than unresolved.
- Catalog-update failures retain the existing filesystem rollback behavior.
- A destination collision is reported and never overwritten.
- Worker exceptions are converted to typed failures and do not terminate the
  GUI event loop.
- Logs include root and source path but never ADS credentials.
- No watcher is enabled merely by upgrading from an older configuration.

## Verification

Focused tests cover:

- `RenameSettings` defaults, normalization, serialization, global persistence,
  library persistence, and global fallback;
- Rename dialog scope switching, accept-to-save behavior, and Cancel writing
  nothing;
- watch-policy configuration migration and immediate round trips;
- explicit start/stop, independent per-root state, and autowatch startup;
- observer-before-scan activation races, modified/moved preexisting files, new
  files, inode reuse, and delete/recreate identities;
- manual banner action/dismiss behavior and automatic processing;
- stale banner actions, session replacement, deterministic failure retry, and
  compact multi-arrival notification behavior;
- partial-download deferral, duplicate-event coalescing, and reconciliation;
- typed resolved, renamed, unresolved, deferred, stale, and failure outcomes;
- missing autowatch roots, settings-save failures, root removal, and orderly
  shutdown before GUI registry closure;
- paused writes around foreground jobs and coalesced, selection-preserving model
  refresh under multiple arrivals.

Run focused tests first, then the complete pytest suite. Run `isort`,
`ruff format`, `ruff check`, and the configured type checker, if present,
before completion. GUI tests use temporary libraries and fake ADS responses;
they never activate a watcher on the user's real PDF directories.

## Deliberate exclusions

This feature is an in-application watcher, not a system daemon or macOS
notification-center integration. It works only while ADSFOLIO is running. It does
not bulk-process a library on startup, remember one-run Identify permissions,
overwrite destination files, or automatically install/enable background
services. Existing PDFs remain available to the explicit manual Identify and
Rename commands.

## Approved table amendment

The main library table adds an **Added** column immediately before
**Modified**. **Added** is the catalog location's `file_locations.first_seen_at`
value: the time ADSFOLIO first registered that PDF location. It is not filesystem
birth time, attachment creation time, or the most recent rescan time. Moves and
renames of the same tracked location retain the original value.

The column uses the same ISO-8601/custom timestamp display preference as
**Modified**. New or default GUI configurations sort by **Added** descending so
the most recently registered PDF appears first. An existing explicitly saved
sort column and direction remain unchanged.
