# Reliable Single-PDF Watch Design

## Scope

Repair the existing `adsapi pdf watch` workflow so a newly arrived PDF is
identified, renamed, and catalogued without re-identifying unchanged PDFs.
The same work also fixes the observed Unicode filename loss, first-page title
selection, and catalog divergence after file moves.

This implementation changes repository code and documentation only. It does
not install or start a daemon, move library PDFs, or mutate the live PDF
catalog. The documented `launchd` configuration remains an explicit user
installation step.

## Constraints

- Preserve the existing bulk `identify` and `rename` commands.
- Keep ADS validation authoritative; a syntactically plausible filename
  Bibcode is evidence, not proof.
- Do not use fixed retry counts or file-size thresholds.
- Do not mark an unreadable or concurrently changing PDF as unresolved.
- Every successful filesystem move must update the catalog path, or roll the
  filesystem move back if the catalog update fails.
- Default filename output preserves Unicode. Explicit ASCII output uses
  transliteration instead of silently deleting unsupported characters.
- Watch processing is single-writer per process. Duplicate filesystem events
  for one path are coalesced.
- Periodic reconciliation performs filesystem facts checks only; unchanged
  PDFs are not re-hashed or re-identified.
- Existing unrelated workspace changes are preserved. No commits are created
  unless requested.

## Identification

### Shared evidence order

Single-file and bulk identification use the same precedence:

1. filename Bibcode;
2. PDF metadata Bibcode;
3. first-page Bibcode;
4. PDF metadata DOI;
5. first-page DOI;
6. title extracted from the first page;
7. PDF metadata title.

Every Bibcode and DOI candidate is validated by an ADS response before the
record becomes resolved. Filename Bibcode detection must not suppress DOI or
title extraction, because the current positional regex admits false positives.

### Title extraction

Title extraction treats publisher/access/version lines as boilerplate and
joins adjacent title lines before citation, author, affiliation, or abstract
boundaries. The behavior is based on document roles rather than a filename or
paper-specific string. For the reported RNAAS PDF, the resulting title is:

`Aperture-averaged Polarimetry of the Second Interstellar Comet 3I/ATLAS within the Snow Line`

### Unicode filename policy

`char_policy="keep"` becomes the default for rendering, batch renaming, and
watching. `char_policy="ascii"` uses standard Unicode decomposition plus an
explicit mapping for Latin letters that Unicode does not decompose, so
`Wesołowski` becomes `Wesolowski`, never `Wesoowski`.

This is a deliberate breaking default for callers that relied on ASCII-only
filenames. They retain that behavior by passing `char_policy="ascii"`.

## Filesystem and Catalog Consistency

A tracked move has four stages:

1. compute and validate source and destination paths;
2. reject an existing destination before mutation;
3. rename the file;
4. update the catalog location.

If stage 4 fails, the implementation renames the file back to its source and
re-raises the catalog error. If rollback also fails, both failures are exposed
in one exception so the operator knows manual reconciliation is required.

Unresolved routing and resolved filename rendering use this operation. Watch
destinations must remain inside the watched root so one root-scoped catalog
can represent the move. The bulk identify command retains its existing
external unresolved destination support; catalog path updates are performed
when the destination is inside the scan root.

## Watcher

### Readiness

Before ADS work, the watcher:

1. captures size, modification time, device, and inode;
2. verifies that `pypdf` can read the document and that at least one page
   exists;
3. hashes the file;
4. captures the same facts again.

If parsing fails or facts differ, processing is deferred. No unresolved record
or file move occurs. A later filesystem event or periodic reconciliation
retries the path. This uses observable file state and parser validity rather
than a fixed delay or retry limit.

### Backends

Polling remains available. It compares catalog facts and an in-memory last
processed snapshot, and invokes identification only for new or changed paths.

The watchdog backend becomes hybrid:

- created, moved, and modified events enqueue paths;
- duplicate queued paths are coalesced;
- a single worker in the watch loop performs catalog and filesystem mutation;
- the existing `--interval` value controls a periodic facts-only
  reconciliation scan;
- deferred paths re-enter through later events or reconciliation;
- moved-out/deleted files are marked missing during reconciliation.

The event callback never performs ADS or SQLite work on the watchdog observer
thread.

### Result behavior

- Unchanged tracked files are skipped.
- Resolved files are renamed with the configured scheme and catalogued at the
  new path when `--apply` is set.
- Dry-run mode reports the proposed resolved rename without changing disk or
  catalog paths.
- Unresolved files are left in place or moved to the configured in-root
  unresolved directory, with the catalog path updated after a successful
  move.
- `Ctrl+C` stops after the current synchronous file operation and closes the
  catalog connection.

## macOS `launchd`

Documentation provides a user-specific `LaunchAgent` plist that runs the
installed `adsapi pdf watch` executable with explicit absolute paths,
`--use-watchdog`, `--apply`, and a reconciliation interval. Standard output
and error paths are explicit. Installation uses `launchctl bootstrap`; removal
uses `launchctl bootout`.

The repository does not auto-install the plist because executable paths,
library paths, naming policy, and permission scope are user-specific, and
starting it would immediately authorize mutations to a live library.

## Verification

- Regression tests cover non-decomposable Unicode transliteration and the new
  Unicode-preserving default.
- Identifier tests cover publisher boilerplate, multiline titles, filename
  Bibcodes in single-file resolution, and false-positive filename evidence
  falling through to other evidence.
- Watcher tests cover deferred partial PDFs, resolved rename application,
  unresolved path updates, rollback after catalog failure, duplicate event
  coalescing, modified events, and periodic reconciliation.
- CLI tests cover default character policy, in-root unresolved validation,
  interval forwarding, and catalog closure.
- Full pytest, `isort`, `ruff format`, and `ruff check` run before completion.
