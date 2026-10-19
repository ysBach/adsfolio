# Desktop GUI

ADSfolio provides a graphical desktop application for browsing, cataloging,
and organizing scientific PDF libraries.

For a development checkout, run `uv sync --all-extras` and then
`uv run adsfolio-gui`. After package-index publication, install the Qt extra:

```bash
uv pip install 'adsfolio[gui]'
```

Use `'adsfolio[full,gui]'` to include GUI watching and PDF annotation tools.

Launch the desktop application with ``adsfolio-gui``, ``adsfolio gui``, or ``adsfolio-gui``.
Startup does not require an ADS token; local PDF roots can be added, inspected,
and organized offline. If an operation requires NASA ADS network services (such
as ADS Refresh, Add to ADS Library, or ADS Identify fallback), an explicit token
dialog is presented. Bibliographic identification supports ADS, Crossref, and DataCite, in that default
order. Reorder or disable providers in the Identify dialog. Crossref and DataCite
look up exact DOIs without an ADS token; title matching uses ADS. OpenAlex and
PMID/PubMed lookup are not supported.  The window has
three panes: registered library roots and virtual views on the left, a paged
sortable/searchable file table in the middle, and editable metadata for the
selected file on the right.

## Library roots and missing PDFs

For citations without ADS records or PDFs, use **File → Local References…**.
That window imports BibTeX, accepts manual entries, links optional PDFs, and
exports ADS and external references together. It is independent of the selected
PDF root. See [References outside ADS](local-references.md) for the complete flow.

Add a root from **File → Add Library Root…**.  Registration discovers PDFs
immediately.  New roots use a non-recursive scan by default, so only PDFs
directly in the selected directory are discovered.  To include nested
directories, right-click the root under **LIBRARIES** and enable **Include
Subdirectories**.  The checkbox is disabled while that root is watched; stop
the watcher before changing it.  Changing the checkbox saves the scan scope
but does not rescan the current catalog immediately; press **Refresh** (**F5**)
to scan the root with its updated **Include Subdirectories** setting.

The views show all, resolved, unresolved, arXiv-only, and duplicate records.
**All** and **Unresolved** are catalog-status views, rather than directory
views: **All** includes current catalog locations regardless of resolution, and
**Unresolved** filters those locations by unresolved status.  Normal queries
exclude locations marked missing, so a PDF that was never discovered or whose
catalog location is marked missing appears in neither view.  Double-click a
row to open the PDF in the operating system's default viewer; the context menu
can reveal a file, copy its path, or copy its Bibcode/DOI.

Non-recursive discovery updates only top-level locations. Previously cataloged
nested files remain unchanged, including their missing status. Enable
**Include Subdirectories** and press **F5** to restore existing
nested files and discover new ones.

## Browsing files and editing metadata

The table loads records in pages to keep large libraries responsive. Bibcode and
arXiv columns are clickable links to ADS and arXiv, respectively. If a link
column is too narrow for its full value, it displays a compact branded link
badge while retaining the full value in its tooltip and accessible text. The
Filename column follows the configured click action: open the PDF or reveal it
in the file manager. Right-click the table header to show or hide columns,
show all columns, resize columns to their contents, or restore the default
column order. **Added** appears immediately before **Modified**, and the default
sort places the most recently added files first.

The status
bar reports the loaded and total counts; scroll or use **File → Load Next Page**
to load another page. Its narrow, unlabeled resolution column shows a green dot
for a successful ADS match or complete, current external metadata. Its tooltip
distinguishes ADS linkage from metadata readiness. Click that header to group records
needing attention first. Use **View → Appearance…** to choose a fixed-width
font and point size independently for each table column, configure right-pane
metadata text, and select ISO 8601 or any Python ``strftime`` timestamp format
(for example ``%Y-%m-%d``; month is lowercase ``%m``).

Use the toolbar **Refresh** button or **F5** after adding, editing, moving, or
deleting PDFs outside adsfolio. Refresh scans the selected root using its
**Include Subdirectories** setting. It runs locally in a background job, updates
changed file facts, discovers new PDFs, and hides missing paths while retaining
their catalog history. Existing metadata and local edits are preserved; moved
files retain their metadata when their filesystem identity matches. The status
bar and job log report added, modified, moved, restored, and missing counts.
Selection, search, and sort settings are retained when the originating view is
still active. Watcher writes pause during the scan. **File → Reconcile File
Facts…** runs the same scan.

The toolbar **ADS Refresh ▾** menu provides refresh and rename actions for the
selected record, a manually entered identifier, all ADS records, or all citation
splits. These actions contact ADS and apply the saved rename settings. A local
Refresh does neither.

Permanent cleanup remains separate: **File → Remove Missing Registry Records…**
asks before removing missing catalog entries and never deletes files.
Metadata rows in the right pane can be dragged to choose their display order; the
order is saved for the next launch. **View → Appearance… → Details** also lets
you reorder rows and choose which ones appear. Click a value to copy it. The
tooltip and status-bar message confirm the copy without changing the row layout.
Appearance changes apply immediately to the main table and detail pane while
the dialog is open; **Cancel** restores the previous appearance, and **OK**
keeps the live settings for normal configuration saving. **View → Appearance… →
Applications** controls whether filename clicks open or reveal files and accepts
optional PDF-viewer and web-browser commands. Empty commands use the operating
system defaults.
Use the pencil control to edit a bibliographic metadata field. The lock control
preserves that field through later ADS updates, including **Identify** and
explicit refresh; unlocked fields are replaced by ADS. File identity fields
(filename, full path, modified date, size, and SHA-256) and workflow-state fields
(status and identification time) remain read-only. Long author lists show five
names first and expand on demand; long abstracts start as a preview and expand
inline with **Show more**.

Upgrade and annotation-port audit fields are hidden by default. Bibstem, volume,
and page are placed at the end of the default order because that information is
encoded in the bibcode. ADS HTML display markup is converted to plain text in the
table, detail pane, and generated filenames.

Use **Update from ADS…** in the detail pane to enter or correct the selected
file's Bibcode, DOI, or journal-volume-page coordinates. A DOI may be bare,
`DOI:`-prefixed, or an HTTP(S) `doi.org` / `dx.doi.org` link; other publisher
URLs are not recognized. A DOI is used exactly as typed (apart from
lower-casing), so trailing punctuation or parentheses that belong to the DOI are
kept; remove any sentence punctuation you pasted along with it. It refreshes that
file's unlocked ADS metadata, abstract, and citation counts, and marks the file
resolved after a unique matching ADS record is found. It then renames the PDF
using the selected global or library Rename settings. If a Bibcode has no ADS
match, it is cleared and the local file remains tracked as unresolved.
**Tools → Refresh Selected ADS Record** performs the same update using the
current Bibcode, including detailed citation splits. **Tools → Refresh All ADS
Records…** refreshes metadata and abstracts for every tracked file with each
returned Bibcode, while preserving locked fields. **Tools → Refresh All Citation
Splits…** is deliberately
separate because ADS's Metrics endpoint returns refereed/non-refereed counts only
per paper; it confirms the number of individual requests before starting. No ADS
request occurs simply from selecting a row.

**Tools → My Publication Profile…** stores your ORCID, ADS author-name forms,
first/last publication years, and optional ADS library IDs. These global fields
define the publications fetched for profile matching; the years do not reject
PDFs by their publication year. Saving the profile does not make a network
request. Use **Tools → Identify → Refresh publication cache** when you want to
rebuild the cache.

## Custom citation styles

Custom citations are edited from the exact menu command
``Tools -> Custom Citation Styles...`` (displayed by Qt as **Tools → Custom
Citation Styles…**). The editor manages the ordered list of styles:

1. Select **Add** to create a new style with the next available ``My bibcode N``
   name, or select a style and choose **Duplicate** to insert a copy with a
   fresh ID and a unique ``Copy of <name>`` name.
2. Select a style and use **Rename** by editing its **Name** field, then edit
   its template and author controls. **Reorder** styles with **Move Up** and
   **Move Down**; **Delete** removes the selected style, including the valid
   case where the list becomes empty.
3. Select **Update Preview** to validate the current draft and render it. When
   the editor was opened with exactly one selected table record, that record is
   used; otherwise the preview uses a labeled built-in example. Preview changes
   only the preview and does not modify the saved style list or the running
   library view.
4. Select **Save** to validate the current draft and the complete style list.
   A valid save immediately replaces the generated table columns and detail
   rows and persists the styles in the GUI configuration. Invalid syntax,
   fields, names, IDs, duplicate names/IDs, or author settings show an error
   and keep the editor open. The affected style is identified; a control is
   focused only when one exists (IDs have no editor field). Select **Cancel**
   to close without applying or emitting the draft changes.

For each saved style, adsfolio creates a computed table column and detail row
after **Bibcode** by default, in the order shown in the editor. Generated
table columns are display-only and are not sortable. Generated detail rows are
computed, read-only, and not lockable; they do not become ADS metadata fields.
Use **View → Appearance…** to reorder or hide generated columns and detail rows
and to set their optional table/detail font, size, and color. Use **OK** in the
Appearance dialog to keep those live settings for configuration saving; **Cancel**
restores the prior appearance. New generated items are visible by default.
These appearance settings, the style definitions, and their order persist
across the next application launch.

Click a non-empty generated table cell or detail value to copy its complete
rendered text. The table status bar or detail tooltip reports **Copied**. An
empty rendered value is inert and leaves the clipboard unchanged. Missing
metadata supplies an empty string for its placeholder, while the template's
literal spaces and punctuation remain; Unicode is preserved. Rendering is
local-only from the record already available in the catalog or selected row:
editing, previewing, selecting, and copying a citation do not issue an ADS
request. Custom citation text is not a filename and does not rename PDFs.

Use Python-style replacement fields from this complete reference. Fields must
be written exactly as shown; format specifiers, conversions, attribute/item
access, unknown fields, and unmatched braces are rejected when saving.

| Field | Rendered value |
| --- | --- |
| ``{bibcode}`` | ADS Bibcode, unchanged. |
| ``{year}`` | Publication year. |
| ``{title}`` | The first title value, with ADS HTML display markup converted to plain text. |
| ``{first_author_surname}`` | Surname of the first author only. For ``Bach, Yoonsoo P.`` this is ``Bach``. |
| ``{first_author_initials}`` | First-author surname followed by given-name initials. For ``Bach, Yoonsoo P.`` this is ``BachYP``. |
| ``{authors}`` | The author list in the selected author format and separator. If the total exceeds **Max authors**, retain **Authors when truncated** leading authors and append the configured truncation suffix. |
| ``{authors_without_suffix}`` | The same rendered author list, but without the truncation suffix. |
| ``{journal}`` | The first ADS bibstem, falling back to ``pub`` only when bibstem is absent. |
| ``{bibstem}`` | The first ADS bibstem only; it has no fallback. |
| ``{pub}`` | ADS publication or journal text, rendered as plain text when ADS markup is present. |
| ``{volume}`` | Publication volume. |
| ``{page}`` | First page or article identifier. |
| ``{{`` and ``}}`` | Literal opening and closing braces. |

Missing values render as empty strings. Spaces, punctuation, and Unicode are
preserved. No ``.pdf`` extension is appended. Empty names, templates, and IDs,
invalid author settings, duplicate names/IDs, and invalid template syntax keep
the editor open and identify the affected style. A control is focused only when
one exists; IDs have no editor field. For example:

```text
{first_author_initials} ({year}) {bibstem}, {volume}, {page}
→ BachYP (2024) A&A, 123, 456
```

The author controls apply only when ``{authors}`` or
``{authors_without_suffix}`` is used. **Author format** is **Surname + initials**
(for example ``BachYP``), **Surname only** (``Bach``), or **Last, First**
(``Bach, Yoonsoo P.``). **Max authors** is the threshold: a list at or below
that count is not truncated. **Authors when truncated** selects how many
leading authors remain above the threshold. **Author separator** joins retained
authors, and **Truncation suffix** is appended only to ``{authors}`` when
truncation occurs. A no-comma author name uses its last token as the surname
and preceding tokens as given names.

## Identify and Rename

**Tools → Identify** and **Tools → Rename** use the same CLI commands as the
terminal application, run as subprocesses.  Output is streamed to the log dock,
and catalog records are reloaded after completion so counts and metadata are fresh.
Rename shows a dry-run preview before applying changes. The GUI does not render
PDFs itself.

The Identify dialog remembers reusable options across launches. **All
libraries** stores global defaults and is selected by default; **This library
only** stores portable overrides in that library's catalog metadata. Switching
the scope immediately loads its values. The **Identification priority** list
contains every supported evidence source. Check the sources to enable and drag
them, or use **Up** and **Down**, to set their order. Identification stops at the
first unique validated ADS match; a missing, invalid, or ambiguous candidate
falls through to the next enabled source. The recommended order checks filename
and PDF metadata Bibcodes/DOIs before first-page evidence, structured filenames,
and title matching. Thus an opaque filename such as `aa43983-22.pdf` naturally
continues to DOI extraction from the PDF itself.

**Save Defaults** persists the reusable controls for the selected scope without
running Identify or closing the dialog. **Run Identify** uses the controls as
currently displayed without saving them. Parser warnings and the optional AI
CSV are one-run inputs. **Skip unresolved directory** excludes
the configured unresolved destination before hashing, metadata extraction, or
ADS queries; without a destination it means `<library>/unresolved`. It is off by
default, so existing unresolved files continue to be retried unless you enable
it. **Refresh publication cache**, **Allow ADS fallback**, and **Apply file
moves** are intentionally one-run actions and always reopen disabled. Every
non-obvious Identify and Rename row has a visible **?** button as well as a
tooltip and accessible description. Rename's filename-template help lists all
supported fields and explains truncation, sanitization, and automatic `.pdf`.

Enable **Apply file moves** to return successfully identified files from the
unresolved subtree to the library root. Identify keeps their filenames; Rename
and **Identify & Rename** watcher actions also apply the configured filename
template. With Apply disabled, Identify reports a preview. The unresolved
directory is the configured Identify destination, or `<library>/unresolved` by
default. Recursive scanning must include those files, and **Skip unresolved
directory** must be off. Previously identified files in that scanned subtree
are returned too, without repeating their ADS lookup. AI CSV matches are
handled in the same run.

**Update from ADS** and ADS refresh rename successfully updated PDFs using the
effective Rename settings. Files in the unresolved subtree also return to the
root in the same move; other PDFs are renamed within their current directory.
Filenames use the refreshed metadata, including any locally locked fields.
These actions apply immediately and pause the library watcher while running.
Optional detailed citation metrics are fetched after renaming, so a metrics
request failure does not leave successfully refreshed PDFs waiting for a rename.
Plain metadata edits do not move files. If a filename collision or rename error
occurs, the refreshed metadata is retained and the job log explains why the
rename did not complete.

Rename preview and Apply use the same effective Identify destination for the
selected library. Existing destination files are never overwritten: the log
reports collisions and leaves the identified source in place. File moves and
catalog locations are updated together, with rollback on catalog failure.
Automatic return is limited to an unresolved subtree inside the library root;
no parent library is inferred when an external folder is selected as a root.

## Adding identified PDFs to a remote ADS library

The GUI distinguishes two different kinds of library:

* A **local PDF library** is a registered filesystem root, added with
  **File → Add Library Root**, and backed by the local adsfolio catalog. Its
  records, paths, metadata, and workflow state remain local.
* A **remote ADS library** is an ADS/SciXplorer destination. It is shown in
  this dialog's destination table and is not a filesystem root or a second
  local catalog.

This workflow never renames or moves PDFs and never edits the local catalog,
local metadata, Rename settings, or watch state. The main window snapshots
local records before opening the dialog; network work runs in a worker outside
the GUI thread.

### Entry points and source scope

Use **Tools → Add to ADS Library…** or the table context menu's **Add to ADS
Library…** action. The action requires a current local PDF root and is disabled
while another command or ADS-library dialog is active. From the context menu,
if the clicked row is not part of the current selection, that row becomes the
sole selection before the dialog opens.

The source controls are:

* **Selected rows** snapshots the selected rows currently loaded in the table,
  in visual table order, and is disabled when no row is selected.
* **All identified files in this local library** queries every resolved record
  in the current local catalog. It ignores the active search, virtual view,
  and table pagination; it is not limited to currently visible rows.

A source record is eligible only when it is resolved and has a non-empty ADS
Bibcode. Unresolved, missing-Bibcode, and otherwise ineligible records are
reported but not submitted. Eligible Bibcodes are deduplicated deterministically
in first-seen order, so duplicate local files do not produce duplicate
submissions. The source summary reports **Source files**, **Eligible unique
Bibcodes**, **Ineligible files**, and **Local duplicate Bibcodes**.

### Remote destinations and request safety

The destination table shows the ADS library's name, document count, public or
private visibility, and permission. Only `owner`, `admin`, and `write`
permissions are writable and selectable. Destinations with `read` or an
unknown permission remain visible for explanation but are disabled.

**Create Library…** requires a non-empty name and accepts an optional
description and public/private choice. It creates an empty remote ADS library,
returns it to the destination table, and selects it. Creation never includes
pending Bibcodes and cannot silently add documents.

The request sequence is:

| User action | ADS operation | Mutation? |
| --- | --- | --- |
| Open the dialog or choose **Refresh** | List the user's ADS libraries | Read-only |
| Select a writable destination | Fetch all destination Bibcodes for duplicate preflight | Read-only |
| Accept **Create Library…** | Create the named empty ADS library | Creates an empty library; no documents |
| Confirm **Add to ADS Library** | Add missing Bibcodes in document-management requests | Document-add POST, only after confirmation |
| Choose **Retry Remaining** | Reconcile remote documents, then add only Bibcodes still absent | Read-only reconciliation first; any add is an explicit retry action |

Selecting a destination normally completes a remote duplicate preflight. A
complete fetch shows the exact number already present and excludes those
Bibcodes from the add request. If the fetch fails, **Already present** is
**Unknown**; the warning and final confirmation explain that all eligible
Bibcodes may be submitted. No document-add POST occurs merely from opening the
dialog, changing scope, selecting a destination, or loading remote documents.

Before the first document-add request, one final confirmation shows the
destination name and permission, eligible unique count, exact already-present
count or **Unknown**, ineligible-file count, local-duplicate count, and number
to submit. The POST begins only after an affirmative answer. The ADS/SciXplorer
document-management contract permits at most 2,000 Bibcodes per request, so
larger submissions use stable-order batches of no more than 2,000.

### Progress, cancellation, partial results, and retry

The add operation runs asynchronously and reports progress without blocking
the GUI. **Cancel** takes effect before the next add batch; a request already
in flight may finish. Closing the dialog requests the same cancellation and
waits for clean worker shutdown before closing.

If processing stops part-way through, acknowledged batches remain acknowledged
and the result distinguishes:

* **Acknowledged** Bibcodes accepted by completed add requests;
* **Already present** Bibcodes excluded by preflight or learned from the
  terminal result;
* **Failed** Bibcodes rejected with an ADS-declared error;
* **Status unknown** Bibcodes from a timeout or connection failure after a
  request was sent, because ADS may have accepted them;
* **Unattempted** Bibcodes whose later batch was never started; and
* **Cancelled**, plus the local-duplicate and ineligible source counts.

The operation stops after the first failed or status-unknown batch. A failed
batch is not silently retried and acknowledged remote additions are not
undone. Use **Retry Remaining** to reconcile first: adsfolio reloads every
remote destination Bibcode and submits only the remainder still absent. If
that read-only reconciliation fails, the dialog warns that a raw retry may
resubmit Bibcodes with an uncertain server outcome and requires a second
explicit confirmation before allowing it.

## Exporting a remote ADS library

Use **Tools → Export ADS Library…**, select a remote library and bibliography
style, and click **Export**. Choose the save location after ADS finishes.
This works without adding a local PDF folder and includes libraries where you
have read permission. It exports the entire selected remote library, independent
of local table selections.

BibTeX (`.bib`) is the default. Other choices are BibTeX with abstracts, RIS
(`.ris`), EndNote, AASTeX, MNRAS, Icarus, Solar Physics, ADS tagged, ProCite,
RefWorks, and MEDLARS. Journal bibliography styles use `.tex`; the remaining
tagged formats use `.txt`. Formatting comes from ADS, including its default
journal macros and author limits; local PDF metadata edits and custom table
citation styles are not applied to this export.

Network work runs in the background. All library pages are loaded, then ADS
exports are batched using its documented 2,000-record limit. Each response is
checked against the requested Bibcodes before a file can be saved. Records are
ordered by Bibcode across the complete file. Count mismatches and missing export
records produce an error instead of a partial bibliography. Library pagination
is not a server-side snapshot; avoid editing its membership during export. See the
[ADS export service](https://github.com/adsabs/export_service) for format details.

An empty library produces no file. Cancelling the operation or Save dialog leaves
existing files intact. Saving uses an atomic replacement after any native
overwrite confirmation. This action does not change ADS library membership,
local PDFs, or the local catalog.

## Watching library roots

Each registered root has three independent controls in the **Tools** menu and
its sidebar context menu:

* **Watch Current Library** starts or stops a watcher for the current session.
  Starting creates a fresh baseline, so only PDFs created or moved into the
  root afterward are offered for processing. Existing PDFs, including files
  modified or renamed within the root, are not reinterpreted as new arrivals.
* **Autowatch at Start** saves whether that root should begin a fresh watch
  session the next time the GUI starts. It does not process anything when the
  preference itself is selected.
* **Automatically Identify & Rename** authorizes future arrivals in an active
  watch session to make ADS requests, update the catalog, rename resolved PDFs,
  and apply a configured unresolved-file move. It is off by default. When it is
  off, the non-modal notice bar offers **Identify & Rename** and **Dismiss** for
  each new arrival.

The watcher recognizes the ``.pdf`` suffix in any letter case, such as
``PAPER.PDF``. A symbolic link to a PDF outside the root is ignored. If a
periodic scan cannot read a folder or file, it reports a watch failure and
leaves the tracked files unchanged; they are not marked missing.

The active watcher is shown with a ``●`` beside the root label. Recursive root
scope cannot be changed until that root's watcher is stopped. Foreground
**Identify** and **Rename** operations temporarily pause the root's worker
catalog writes while filesystem arrivals continue to queue, then resume the
worker after cancellation, failure, or completion.

Identify and Rename defaults are resolved independently. Each dialog's **All
libraries** choice supplies global defaults; **This library only** stores and
uses that root's catalog override, with global settings as the fallback when no
override exists. A root can therefore use library-specific Rename defaults and
global Identify defaults, or the reverse.

Do not run a separate ``adsfolio pdf watch`` process or ``launchd`` watcher on a
root while the GUI watcher is active. Concurrent watchers can issue duplicate
ADS work and compete to rename or update the same PDF and catalog records.

While an identify, rename, or ADS refresh job runs, an indeterminate progress
indicator appears in the status bar. Common shortcuts are **Return**, **Enter**,
or **Space** to open the selected PDF; **F5** to refresh; **Ctrl/Cmd+F** to focus search;
**Ctrl/Cmd+Shift+D** to toggle details; and **Escape** to clear search.
