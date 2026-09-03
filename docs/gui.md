Desktop GUI
==========

Install the optional Qt dependency with::

   pip install 'adsapi[gui]'

Launch the browser with either ``adsapi gui`` or ``adsapi-gui``.  The window has
three panes: registered library roots and virtual views on the left, a paged
sortable/searchable file table in the middle, and editable metadata for the
selected file on the right.

Add a root from **File → Add Library Root**.  The views show all, resolved,
unresolved, arXiv-only, and duplicate records.  Double-click a row to open the
PDF in the operating system's default viewer; the context menu can reveal a
file, copy its path, or copy its Bibcode/DOI.

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
for a record with a successful ADS match. Click that header to group records
needing attention first. Use **View → Appearance…** to choose a fixed-width
font and point size independently for each table column, configure right-pane
metadata text, and select ISO 8601 or any Python ``strftime`` timestamp format
(for example ``%Y-%m-%d``; month is lowercase ``%m``).

Registry entries remain after a PDF is moved or deleted outside adsapi so their
metadata is not silently discarded. Select **File → Remove Missing Registry
Records…** to review and remove those stale entries; this never deletes files.
Use **File → Reconcile File Facts…** to refresh stored file sizes and modified
timestamps from paths that still exist. It reports missing paths without
removing them, so review and delete stale records separately.
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
file's Bibcode, DOI, or journal-volume-page coordinates. It refreshes that
file's unlocked ADS metadata, abstract, and citation counts, and marks the file
resolved after a unique matching ADS record is found. If a Bibcode has no ADS
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

**Tools → Identify** and **Tools → Rename** use the same CLI commands as the
terminal application, run as subprocesses.  Output is streamed to the log dock,
and the registry is reopened after completion so counts and metadata are fresh.
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
it. **Refresh publication cache**, **Allow ADS fallback**, and **Apply unresolved
moves** are intentionally one-run actions and always reopen disabled. Every
non-obvious Identify and Rename row has a visible **?** button as well as a
tooltip and accessible description. Rename's filename-template help lists all
supported fields and explains truncation, sanitization, and automatic `.pdf`.

Watching library roots
----------------------

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

Do not run a separate ``adsapi pdf watch`` process or ``launchd`` watcher on a
root while the GUI watcher is active. Concurrent watchers can issue duplicate
ADS work and compete to rename or update the same PDF and catalog records.

While an identify, rename, or ADS refresh job runs, an indeterminate progress
indicator appears in the status bar. Common shortcuts are **Return**, **Enter**,
or **Space** to open the selected PDF; **F5** to refresh; **Ctrl/Cmd+F** to focus search;
**Ctrl/Cmd+Shift+D** to toggle details; and **Escape** to clear search.
