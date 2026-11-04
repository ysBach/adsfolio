# Changelog

## Unreleased

### Identifier recheck

- The recheck previously started by itself each time ADSfolio opened, and
  every 24 hours. Now ADSfolio asks first: **Check Now** or **Not Now**.
  **Tools → Check for ADS Updates** starts it at any time. Adding a library no
  longer starts it.
- The recheck previously read again every identified PDF without a stored
  identity proof, even when ADS returned the same bibcode. It also called
  Crossref and DataCite for each of those PDFs. In a 2,912-PDF library, that
  was about 1,370 PDFs. A 25-PDF sample gave about 11 minutes of local work,
  plus the network calls. Now a PDF keeps its stored record when ADS returns
  the same bibcode, and the check does not open it. Without network time, the
  same library took 3.3 s.
- The recheck previously checked again every PDF that failed an identity
  check, and read every unidentified PDF without a metadata DOI. Now it
  remembers these failures for each library. It skips such a PDF until the PDF
  or the inputs of its check change. It stores each failure when it finds it,
  so a stopped check does not repeat that work.
- `adsfolio pdf watch` uses the same recheck rules for unchanged and
  remembered PDFs.
- Skipped PDFs previously had no retry action. Now **Tools → Recheck PDFs That
  Need Review…** shows the count for each library and asks before it starts.
  This check also adds a missing identity proof to PDFs whose bibcode did not
  change. After an ADSfolio update, ADSfolio asks once whether to recheck the
  PDFs that failed the identity check in an earlier version.
- Scrolling the library table during the recheck previously lagged. The
  recheck parsed PDFs in a thread of the window's process, and each table
  paint waited for it. Now the recheck runs in a separate process. In an
  offscreen test, one table paint took 21 ms with no worker. With a busy
  worker thread, it took 76 to 104 ms. With a busy worker process, it took 21
  to 22 ms.
- The log panel previously showed only file-specific changes from the check.
  Now each library gets a start line and a summary line. The summary gives
  the time and counts checked, skipped, updated, renamed, and failed PDFs.

### Busy messages

- Checking **Combine libraries** during a job previously showed "Wait for the
  current operation to finish." The message did not name the operation. Now
  it names it, for example "Wait for the automatic identifier recheck of
  Papers to finish. To stop it, click Cancel in the status bar." Other
  blocked actions use the same named message.
- The recheck previously showed no progress bar and no visible way to stop
  it. Now the status bar shows the running job's name, the progress bar, and a
  **Cancel** button for every job. A cancelled recheck keeps the failures that
  it found before it stopped.

### Library table

- Clicking a cell in a partly hidden column previously scrolled the table
  sideways. A wide column, such as the filename, moved to the left edge. The
  Up and Down arrow keys did the same. Now a click or an arrow key selects the
  row, and the horizontal position stays the same.

### Watching

- A library tooltip previously showed only the folder path. Now it also shows
  **Watching now**, **Autowatch at Start**, and **Automatically Identify &
  Rename** for that library.
- Adding a library previously turned watching off with no question. Now
  ADSfolio asks: **Watch, Identify, and Rename**, **Watch and Notify Only**, or
  **Do Not Watch** (the default). Libraries that you added earlier keep their
  settings.

### Export papers

- **Export Selection As…** and **Export All As…** previously asked only for a
  format. The key styles were in another window, and BibTeX had no preview.
  Now both open **Export Papers**. One window holds the papers, the format,
  every setting of that format, and a preview of the first three papers.
- Export Papers has three formats: BibTeX, citation text with one of your
  custom citation styles, and a publication list. The publication list uses
  the same settings as the earlier list window.
- PDFs without paper details previously dropped out of an export with no
  message. Now the window counts them, and **Show…** lists their paths.
  **Show Needs Identification** opens the view that holds them.
- Papers outside ADS, such as papers from Crossref or DataCite, export with
  ADS papers. Their keys use the **Other papers** key style.
- The preview previously could not show a selection that held a paper whose
  key you must type. Now it shows the other papers, plus a note for that paper.
- Opening **Export Papers** for 2,776 papers previously took about 25 s. The
  list settings rendered every paper in the background, and each journal name
  read the journal file again. Now the window opens in under 1 s.
  A full BibTeX export takes about 2 s instead of 14 s.
- A title with a line break previously stopped the whole BibTeX export with
  "invalid BibTeX syntax". BibTeX reads a run of spaces and line breaks as one
  space, so the export now accepts the title and keeps its text.
- The key template previously listed its fields only in a tooltip. Now the
  window shows every field. A new **{authors}** row sets the author format.
  **Filename Style** makes keys such as `DoeJA+RoeA_2021A&A...650A.123D`, like
  the default PDF filenames.
- Exports previously always wrote every field. Now **Leave out fields** drops
  the named fields, `abstract` and `adsnote` by default. **Export ADS
  Library…** uses the same list for its two BibTeX styles.

### Example names

- Examples in the custom citation styles, the template help, and the guides
  previously used a real name, `Bach, Yoonsoo P.`. Now they use fictional
  names such as `Doe, John A.`.

### Combined view

- The folder tree previously used the full style indentation for each folding
  arrow. PDF rows started one step to the right of their folder icon. Now
  the arrows use half that space, and a PDF row starts under its folder icon.
  Subfolders keep one step of indentation, so the nesting stays visible.
- PDF rows previously had no icon. Now they show the system PDF icon next to
  the folder icons.

## 0.9.0 (2026-11-04)

### Large-library speed

Times below come from a copy of a 2,912-PDF catalog with empty placeholder
files and the offscreen Qt platform. Times in the desktop app can differ.

- Clearing **Combine libraries** with all PDFs selected previously froze the
  window. It did not finish after 180 s. Now it takes about 0.1 s.
- Opening the app previously loaded and parsed the full catalog 34 times,
  mostly for the sidebar and status counts. Each refresh loaded it 12 times.
  Now the app loads it 3 times at opening and 2 times for each refresh. One
  load serves all counts until the catalog changes. **Refresh** went from
  0.53 s to 0.15 s.
- A search query previously loaded the full catalog 5 times, about 0.6 s for
  each query. Now ADSfolio filters the loaded records with cached search text
  and the same matching rules, about 0.08 s for each query. The results are
  the same.
- Sorting by a column previously read the catalog again, 0.40 s. Now it sorts
  the loaded records, 0.05 s. Rows with equal values keep the same order.
- One click on a PDF previously built the details pane twice. Now it builds
  the pane once, and the app reuses rendered icons. A click went from 0.15 s
  to 0.09 s.
- Turning on **Combine libraries** went from 1.5 s to 0.25 s. **Select All**
  in the combined view went from 0.68 s to 0.21 s.

### Search

- Searching `Bockelée` previously found 0 of 2,912 PDFs in a test catalog,
  because the catalog stored `é` as the escape `\u00e9`. Now search reads the
  decoded metadata values and finds 31 PDFs.
- Search previously ignored letter case only for ASCII letters, so `bockelee`
  found 2 PDFs. Now search ignores accents and letter case by default, and
  `bockelee` finds the same 31 PDFs. Turn on **Exact Match** in the Find bar
  to match accents and letter case exactly.
- `%` and `_` previously acted as wildcards, so `3I_ATLAS` also matched other
  characters in place of `_`. Now they match only themselves by default. Turn
  on **Wildcards** in the Find bar to use them as wildcards.
- A search previously also matched field names in the stored metadata, so
  `title` matched 2,886 of 2,912 PDFs. Now search reads only the values.
- `comet nucleus` previously matched only that exact text. Now spaces
  separate terms, and a PDF must match every term, as on ADS. Use double
  quotes for a phrase: `"comet nucleus"`.
- Search previously had no field limits. Now `author:`, `first_author:`,
  `title:`, `year:`, `journal:`, `abstract:`, `bibcode:`, `doi:`,
  `identifier:`, and `filename:` limit a term to one field. Typing `f`
  suggests `first_author:` and `filename:`. After `author:`, the list
  suggests author names from the library.
- The new **Search Fields** menu sets the fields for terms without a field
  name. The default is all fields.
- Found text was previously not marked. Now the table highlights it in the
  columns that show the searched field. Turn off **Highlight Matches** to
  remove the marks.
- The Find bar options are icon buttons with tooltips, so they need little
  horizontal space. ADSfolio saves them for the next start.

### Combined view

- **Tools → Add to ADS Library…** was previously unavailable while **Combine
  libraries** was checked. Now it opens for the selected PDFs when they belong
  to one library. For a selection from two or more libraries, the tooltip says
  to select PDFs from one library. The combined right-click menu also has this
  item.
- **Export Selection As…** and **Export All As…** were previously unavailable
  in the combined view. Now they export the selected PDFs, or all PDFs in the
  checked libraries.
- Disabled **Tools** items previously showed no reason. Now a tooltip explains
  the reason. Items for one library only say to clear **Combine libraries**.

### CLI, Python API, and agent installation

- Document the base CLI and Python installation without Qt or the native desktop bundle.
  Use `pip install adsfolio` for Python or `uv tool install adsfolio` for an isolated CLI.
- Publish a brief CLI/API agent skill as a checksummed release download and include it in the source archive.
  Save the file as `adsfolio-cli-api/SKILL.md` in your agent's skills directory.
  Use JSON searches for structured results. ADS queries still require a token.

### PDF identification and filenames

- Read an unambiguous DOI from XMP metadata during automatic identification.
  Continue to reject conflicting identifiers and preserve PDF contents.
- Let enabled DOI providers run when the user declines ADS requests. Honor a
  disabled ADS setting for reference hints. Enable ADS or enter a DOI to search.
- Retry a journal-name reference without the word `the` when the first search
  finds no record. For an unverified PDF, select the returned paper to apply it.
- Remove the identity confirmation before **Update by ID…**. A direct ID starts
  the lookup. If a PDF identifier conflicts, review the actual result and confirm
  the correction. Automatic identification still refuses conflicts. If the PDF,
  catalog record, or candidate changes during review, retry the lookup.
- Show reference-search candidates even when the PDF has a different identifier.
  If no candidate matches, enter a DOI or Bibcode. The app keeps the current metadata.
- Replace stale unlocked identifiers during an identifier update. Keep notes
  and locked fields. If a locked identifier conflicts, unlock or correct it and
  retry. The existing record and PDF stay unchanged until you resolve the conflict.
- Show **Identified (no ADS link)** for a verified external record. The status
  marker no longer promises that every Rename template has the required fields.
- Reverify saved PDFs against their cached or current ADS record. Preserve
  existing metadata and history when the PDF proves the identity. Report the
  reason when verification fails.
- Check enabled filename bibcodes during automatic ADS rechecks. A unique exact
  match can associate a PDF without readable identifiers. Mark the filename
  source and reject conflicting PDF identifiers. Keep its filename.
- Recheck saved external records and indexed PDF DOI metadata at GUI startup.
  Crossref and DataCite can verify a DOI without an ADS token. Failed lookups
  leave existing metadata unchanged. Respect the saved Identify scope.
- Prefer a unique first-page DOI that also appears in PDF metadata. This avoids
  selecting an unrelated citation DOI when the page contains several DOIs.
- Corroborate publisher article DOI fields with the first page and provider.
  Report identity updates and filename outcomes in the job log.
- Stop trailing bytes in compressed PDF streams from stalling annotation checks.
- Recheck existing catalog records with ADS at startup and every 24 hours.
  When ADS provides a verified new match or bibcode, update the metadata and
  apply the saved Rename settings. If verification or a rename fails, retain
  the existing files and review the reported issue before retrying.
- Recognize supported display markup in provider titles during PDF identity
  checks. The remaining identity checks still apply.
- Enable ADS and allow ADS requests to retry verified external records without
  an ADS bibcode. ADSfolio may also use a cached ADS record without network
  permission. A verified match can add a bibcode. If no match passes the
  identity check, keep the existing metadata. Review the log before retrying.
- Skip renames when a template field lacks metadata. Report the missing fields.
  Add the metadata or choose a template without those fields.
- Retry enabled ADS after an external Watch result when the template needs a
  bibcode. Keep the existing record if ADS cannot verify a match.
- Load saved global Rename settings for `pdf rename` and `pdf watch` by default.
  Use `--settings-scope library` for library settings with global fallback.
  Explicit Rename flags override the selected settings for that run.
- Recognize equivalent Unicode path spellings when they name one directory entry.
  Keep existing attachment history and avoid merging separate entries.

### Saved PDF annotations

- Keep an existing identification when an incremental save changes only
  annotations. Preserve the previous verification level and the PDF contents.
  If the check cannot prove continuity, review the identity notice.

### Settings and sorting

- Open **Settings** with **⌘,** on macOS. Edit desktop, library, citation,
  account, Identify, Rename, and CV settings from one window. Save each section
  separately. Library watching controls apply immediately.
- Keep the selected sort column and direction across searches, library changes,
  refreshes, and restarts. Report a save failure in Notifications.

### Library browsing and notifications

- Recognize a newly added PDF path when the filesystem reuses an earlier inode.
  Keep replacements at existing paths quiet. Preserve PDF contents and watcher identity checks.
- Keep ADS as the primary service. Separate **ADS linked**, **Identified without ADS**, and **Needs identification** views.
  Count verified non-ADS identities separately in the status bar. Exclude them from the Identify badge and retain normal table text.
  Changed files need review again. Unconfirmed metadata alone does not establish identity.
  Use **Update by ID…** for a selected PDF that needs confirmation. Preserve PDF contents, provider order, and rename safeguards.
- Check **Combine libraries** to browse selected libraries together. Expand
  library groups and nested folder groups to find PDFs. The combined view does
  not move PDFs. Existing watchers continue to run.
- Select one PDF in the combined view to edit fields or locks, enter details,
  or update by ID. Each action uses the owning library and keeps the combined
  view open. An ID update may rename the PDF with that library's settings. It
  may also move a resolved PDF from the unresolved folder to the library root.
  An edit may change metadata shared with other attachments.
- Identify selected PDFs from one library without leaving the combined view.
  You can also apply that library's Rename settings to verified matches.
- Preserve shared fields and concurrent edits when saving PDF details. Reopen
  manual entry if another process changed a field you edited.
- During an automatic ADS recheck, choose **Update by ID…** or **Enter Details…**
  for one PDF. The app stops the recheck before starting the edit. If the PDF or
  its catalog entry changes during the wait, refresh the library and retry.
  A watcher that failed to resume no longer blocks a later recheck indefinitely.
- For whole-library Identify or Rename, right-click its folder. If an editing
  control is unavailable, read its tooltip. Select one PDF or wait for the active
  job or ADS recheck to finish. Notifications names unavailable folders.
  Reconnect the folder. Choose **Refresh**.
- Choose **Tools → New PDF Sound…** to preview and save a sound for watched
  arrivals. **Silent** is the default. Check the audio output if playback fails.
- Drag table headers to reorder columns. Right-click a header to set its
  alignment. Choose **Default** to restore the column's default alignment.

### Documentation

- Credit the official NASA ADS API in the README and documentation.
  Link the ADS team's API documentation and distinguish it from ADSfolio's own Python client.
- The App GUI guide has a new "Search the table" section with every search
  field and option. "Getting started" has a short "Find a paper" step.
- The screenshots and videos of the main window show the new Find bar. The
  media script previously stopped before the watch-menu image because it
  opened the menu on **Combine libraries**. Now it opens the menu on a library.

## 0.8.0 (2026-11-02)

### Automatic candidate verification

- Fetch the exact ADS candidate automatically when the watcher or Identify cannot verify a saved association locally.
  Matching PDF identifiers save verified provenance and continue normal processing without a notification approval step.
- Reuse saved proofs and cached provider records when they verify the PDF.
  Preserve notes, locked fields, PDF contents, and the selected Rename template.
- Retain review or retry actions for candidates that remain unverifiable.
  Disabled providers, unavailable records, service failures, changed files, and conflicting identifiers do not grant an association.

### Complete, persistent Notifications

- Keep Notifications open after action icons, review dialogs, and clicks elsewhere.
  Close it with its close icon, **Escape**, or the toolbar toggle.
- Show every queued notification in the scrollable panel with its own actions.
  Remove the collapsed “more” queue display. Preserve per-file action routing.
- Offer **Verify This ID** as a retry when automatic candidate verification cannot finish.
  Fetch its ADS record and check the current PDF before updating or renaming.
  Preserve manual **Use This ID** review and identifier-conflict checks.

### Merge defaults and rename outcomes

- Enable **Move originals to Trash after merge** by default in annotation review.
  After a complete, verified save, keep only the merged PDF in the active library.
  Clear the option before saving to keep inputs in place. Partial merges keep their inputs.
  Cleanup failures retain failed inputs and open the retry dialog.
- Report filenames that already match the selected Rename rule.
  Report missing files, changed contents, unavailable metadata, and existing destinations individually.
- Use the exact selected Rename template in batch, single-file, and identity-repair operations.
  Remove the hidden substitution of `{year}` for `{bibcode}` when no ADS bibcode exists.
  Keep missing fields as empty components, consistent with custom-template rendering.

### Lookup and recovery

- Check reference-search candidates against the selected PDF before applying
  metadata. Recover from a coauthor match through the PDF's own DOI or arXiv ID.
  Keep explicit conflicting identifiers blocked.
- Try later enabled providers after outages or rejected DOI candidates.
  Apply saved provider order to manual updates and automatic identification.
  Report service failures instead of claiming that no record exists.
- Read complete ADS identifier results before deciding uniqueness. Stop an
  incomplete or changing response from producing an automatic association.
- Honor the GUI's Identify scope during Update by ID. Count external metadata
  separately from ADS bibcodes in the job log.

### Preserve files and inputs

- Preserve existing direct and printed PDF downloads. Give new downloads
  numeric suffixes. Reject HTML error pages and try the next download source.
- Prevent unresolved-file moves from replacing a destination created during
  processing. Retain missing-location history for files moved outside the
  library. Roll the move back if the catalog update fails.
- Create upgrade backups exclusively so another backup cannot be overwritten.
- Stage complete Obsidian notes before replacing an unchanged existing note.
  Preserve manual text after a write failure. Skip notes that changed during export.
- Preserve existing unresolved CSV reports and selected AI inputs. Number new
  reports and print their actual paths in the job log.

### Settings and selected actions

- Use independent private temporary files for simultaneous settings saves.
  Serialize final replacements within one process to prevent concurrent Windows move failures.
  A failed replacement keeps the existing file. Retry the save after resolving the error.
- Add **Identify & Rename Selected PDF** to the table's right-click menu.
  Process one or several selected PDFs with saved Identify and Rename settings.
  Leave unverified files, existing destinations, and unselected PDFs unchanged.
  Use the job log and **Update by ID…** to review skipped files.
- Add repeatable `pdf rename --file` selection. Keep the same selection through
  optional identification and renaming, including files in subfolders.
- Allow newly verified external metadata to clear stale status after a scan.
  Recheck the current PDF before clearing it. Missing or conflicting identity
  proof still prevents renaming.

### Identify results

- Report verified saved metadata separately in Identify results. This includes
  PDFs with metadata from other providers and no ADS bibcode.
- Show file-move advice only when Identify previews moves. Report when no
  moves are planned. For a preview, enable **Apply file moves** in Identify
  or rerun the CLI command with `--apply`.
- Count unreadable PDFs separately instead of omitting them from the summary.

## 0.7.4 (2026-10-29)

### Notifications without layout movement

- Move warnings and sidebar-view explanations into **Notifications** in the
  toolbar. Scroll long messages without moving the main panes. Keep pending
  messages visible through a count and severity indicator.
- Use compact icon buttons for notice actions. Display tooltips immediately
  on hover or keyboard focus. Keep action names available to screen readers.

### Review and identify PDFs

- Add **Use This ID** to confirm a displayed candidate without entering its
  bibcode again. Apply saved Rename settings while preserving PDF contents.
  Changed PDFs, changed candidates, and conflicting identifiers still stop the update.
- Add **View ADS Entry** to unverified ADS candidate notices. Open the proposed
  record beside **Open PDF** to compare metadata before confirming a match.
  The link follows the notice and does not update metadata or rename the PDF.
- Accept matching document DOIs when PDF and provider titles differ only in
  quote typography or subtitle separators. Metadata, page 1, and the provider
  must still agree on the exact DOI. PDF contents remain unchanged.
- Explain why a candidate needs review. Notices name failed DOI or title
  checks and offer a recovery action without repeating the filename.

### Safe filename conflicts

- Treat equivalent filename spellings for the same file as an unchanged rename.
  This prevents false destination conflicts on macOS with accented author names.
- Review watched rename conflicts with **Show Both PDFs**. Compare the selected
  files before choosing which copy to keep or reviewing an annotation merge.
  Clear search filters and select only the two exact paths.

## 0.7.3 (2026-10-28)

- **Keep ADS fallback enabled.** Save the choice for all libraries or one
  library with **Save Defaults**. Identify restores it after reopening or
  restarting the app. Query-count limits and PDF identity checks still apply.
- **Override one CLI run.** Identify inherits the saved ADS preference.
  `--allow-ads` permits fallback without confirmation. `--no-allow-ads` restores
  confirmation. Neither option changes saved defaults.
- **Read current documentation.** Updated screenshots and walkthrough videos
  show the current controls. Documentation and website version labels now
  use the package version automatically. Historical migration versions stay fixed.

## 0.7.2 (2026-10-28)

- Keep unchanged catalog paths free of write transactions. A refused move
  also releases its transaction. Recursive identification can continue after
  Windows normalizes two path spellings to the same path.
- Start the diagonal-scroll test inside the available scrollbar range.
  The test still requires horizontal and vertical movement.
- Include all changes below. Versions 0.7.0 and 0.7.1 did not publish because
  Windows release checks failed.

## 0.7.1 (2026-10-28)

- Fix Windows imports and catalog recovery locking. Recovery now uses the
  operating system's file lock without truncating the lock file.
- Flush journal data before atomic replacement on all supported systems.
  POSIX also flushes the parent directory.
- Include the 0.7.0 changes below. Version 0.7.0 did not publish because
  Windows release checks failed.

## 0.7.0 (2026-10-28)

### Identify papers without changing PDF contents

- **Check the actual PDF.** Identify, Rename, ADS refresh, and the watcher now
  check readable identifiers against the provider record. File names and title
  similarity alone cannot establish a match. Readable identifier conflicts
  block changes. Existing records receive the same check before updates.
- **Read more publisher metadata.** A complete labeled DOI in the PDF metadata
  title now identifies the PDF. A DOI in Subject can also identify the PDF when
  the first page repeats it and the metadata title matches the provider title.
  A DOI mentioned in ordinary prose does not establish identity.
- **Tolerate malformed optional XMP.** An unreadable XMP block no longer
  invalidates a matching DOI from another source. ADSfolio records the parser
  warning. It also reads declared DOI properties past unrelated undeclared
  prefixes. Readable conflicting identifiers still block the match.
- **Recover from a failed lookup.** Failed and unresolved notices now offer
  **Open PDF** and **Update by ID…**. Identifier lookup fills publication fields
  and applies saved Rename settings. It also works for an unregistered PDF.
  **Enter Details…** remains an optional local fallback.
- **Review an uncertain match explicitly.** A PDF without readable identity
  evidence needs your review. The CLI accepts `--confirm-identity` with
  `--relative-path` for one PDF. Stored confirmation applies only to the same
  file contents and record. No confirmation overrides a readable conflict.

### Replace PDFs and keep their history

- Deleted PDFs no longer reserve active paths or block replacement filenames.
  Watching updates the visible library after a deletion.
- A verified replacement can rejoin one uniquely matching absent attachment.
  It preserves notes, locked fields, and content history. Existing editions
  remain separate. Ambiguous missing editions remain separate too.
- Same-path updates, atomic replacements, and changes made while the app was
  closed receive identity checks. A rename by ADSfolio does not repeat a lookup
  for unchanged contents.
- Automatic edition-merge prompts now require a shared identifier. You can
  still select two PDFs and start a merge explicitly.

### Read the library state

- The status column distinguishes an ADS link, usable local metadata, and
  a PDF that needs identification. A changed PDF shows a warning marker.
  Clicking the marker selects the PDF and opens its detail pane.
- **N_author** and **Citations** show saved counts. A blank means unknown.
  A known citation count of zero appears as **0**.
- Compact toolbar icons show their action and scope in tooltips. The Identify
  badge counts catalogued PDFs that need an ADS lookup across the library.
- Discrete horizontal mouse-wheel scrolling now uses the same bounded step
  as Shift scrolling. Ordinary vertical scrolling and smooth trackpad input
  retain their native behavior.

### Export PDF annotations to Obsidian

- Export now includes highlights, underlines, strikeouts, squiggles, and text
  comments in a managed **PDF annotations** section.
- Repeated export updates that section while preserving other note text.
  Unchanged notes keep their modification time. `--force` regenerates the
  entire note and replaces manual edits.
- Export skips unreadable PDFs, invalid section markers, and notes that change
  during export. PDF contents remain unchanged.

### Documentation

- The guides now explain identity evidence, automatic lookup settings, and
  recovery through **Open PDF** and **Update by ID…**.
- `AGENTS.md` requires the ADHD and ASD-STE100 skills for documentation,
  docstrings, responses, and release notes.

## 0.6.3 (2026-10-23)

### Merging PDF editions

- **Find the other edition.** Before: a newly identified preprint or published
  PDF had no merge prompt, and selected rows had no merge action. Now: the
  watcher offers **Review Merge…** when stored identifiers link the editions.
  If ADS has no shared identifier, you can choose the source explicitly.
  Select two rows and choose **Merge Selected PDFs…** to start directly.
- **Review annotations in one window.** Before: you had to navigate both PDFs
  and place most annotations manually. Now: selecting an annotation opens the
  relevant pages and shows matching passages side by side. Local text search
  finds exact passages and the closest wording. Changed words, numbers, and
  symbols remain visible for review. **Accept & Next**, target text search,
  and **Undo decision** reduce navigation. Every placement still needs approval.
  Matching needs no account, API key, network connection, or downloaded model.
- **Keep the final merged PDF.** Before: the merged copy stayed outside the
  catalog, and originals needed separate cleanup. Now: for library PDFs,
  saving the verified copy inside an open library folder registers it with the
  canonical PDF's metadata. After a complete save,
  **Remove original PDFs…** lets you choose which inputs to move to Trash.
  **Keep originals** is the default. Cleanup checks that the result and inputs
  are unchanged, and retains catalog history. Sequential complete merges can
  collect three or more originals and intermediate copies for one cleanup.
- **Handle source links separately.** Before: hundreds of source hyperlinks
  could obscure reader annotations. Now: reader marks appear first. You can
  show source links or explicitly exclude them together. The target keeps its
  own links. Before cleanup, the dialog reports waived annotations and explains
  that other editions contribute annotations, not their pages or attachments.

### Library workflows

- **Choose an export format.** Before: separate menu actions hardcoded BibTeX
  or a publication list. Now: **Export Selection As…** and **Export All As…**
  let you choose either format. Export All includes every current PDF in the
  library, including rows hidden by filters or not yet loaded in the table.
- **Export ADS abstracts safely.** Before: literal braces in an abstract could
  stop BibTeX export with a parser error. Now: export escapes those braces
  safely. Other invalid fields produce a message that names the paper,
  citation key, and fields to correct. Stored metadata stays unchanged.
- **Reveal files from notices.** Before: an unresolved or failed file notice
  gave no way to locate the PDF. Now: **Reveal in Finder** on macOS, or
  **Reveal in File Manager** elsewhere, opens its current location. The notice
  stays available after a rename, move, or change of selection.
- **Enter details without a lookup.** Before: unresolved and failed PDFs
  depended on another provider lookup. Now: **Enter Details…** saves local
  title, author, year, journal, and DOI fields without changing the PDF's name
  or location. Changed fields are locked against provider updates. Both warning
  states offer this action.
- **Identify from a bibliographic reference.** Before: manual lookup help did
  not define the reference fields. Now: the app and CLI explain
  `{authorLastName}-{year}-{bibstem}` and `{bibstem}-{volume}-{page}`, including
  full journal names. These reference formats query ADS only. DOI and arXiv
  lookups retain their existing provider choices.

### Fixed

- **Obsidian export for large libraries.** Before: fetching missing abstracts
  put up to 200 bibcodes in a GET URL, which ADS could reject as too long.
  Now: the request uses the existing bulk POST API, with up to 2,000 bibcodes
  in each request body. Dry runs and existing-note protection stay the same.
- **Changed PDFs during manual entry.** Before: a same-size rewrite could pass
  the file check when the filesystem retained the same modification time.
  Now: saving manual metadata also checks the PDF's content digest. A changed
  PDF keeps the dialog and warning open for another review.
- **Detail pane on Windows.** Before: the extra metadata action could force
  the detail pane wider than the publication table. Now: the actions use two
  columns so the default pane sizes remain usable.
- **Cancellation tests on slower machines.** Before: three tests created a
  signal waiter without starting it, then relied on a fixed 20 ms delay.
  Now: they wait for the worker's completion signal before checking results.

### Compatibility

- **Retire earlier application formats.** Before: readers also searched for
  sidecars, catalogs, and reserved metadata keys under the earlier application
  name. Now: those compatibility readers are removed. Existing ADSfolio-named
  files remain supported. Before upgrading from the earlier application, open
  ADSfolio **0.6.0** once to convert its catalog and import its per-library files.
  Version 0.6.3 still copies earlier data directories, but it no longer converts
  their reserved keys. An unconverted catalog's manual locks, author text, and
  local references are not honoured. Keep the original data until you verify
  the converted library.

## 0.6.0 (2026-10-22)

### Papers that are not on ADS

- **Type a DOI or an arXiv ID.** Before: **Update ADS…** asked ADS only. A DOI
  that ADS did not know left the PDF unchanged, and arXiv IDs were not
  accepted. Now: **Update by ID…** asks Crossref and DataCite when ADS
  has no match. `10.5555/jex.2019.12.34` renamed `scan_0042.pdf` to
  `RoeA+KimS_2019-Grain_Growth_in_Cold_Clouds.pdf`. An arXiv ID such as
  `2401.12345` uses its DataCite record. No ADS token is necessary.
- **BibTeX from the main table.** Before: a PDF outside ADS had no BibTeX
  export. Only **Local References…** exported BibTeX for chosen papers. Now:
  right-click the selected rows and choose **Copy BibTeX** or **Export
  BibTeX…**. ADS papers and other papers export together.
- **Citation key styles.** Before: ADSfolio had no key choice. A paper without
  a bibcode or a typed key got a key such as `reference12`. Now: **Tools →
  Citation Keys…** sets one style for ADS papers and one for other papers. The styles are the bibcode, the DOI, your template,
  Google Scholar style (`roe2019grain`), or a key that you type. The defaults
  are the bibcode and the DOI. The CLI command is `adsfolio reference keys`.
- **Missing or shared keys.** Before: two papers with the same key stopped the
  export with an error message only. Now: ADSfolio lists the papers and asks
  for a key for each one. It saves the keys for later exports. The CLI command
  is `adsfolio reference set-key ID KEY`.
- **Identify selected PDFs.** Before: Identify always scanned the whole
  library. Now: right-click rows and choose **Identify Selected PDFs…**. The
  CLI option is `adsfolio pdf identify DIRECTORY --file PATH`.
- **One record per paper.** Before: a PDF with only Crossref metadata had no
  catalog record, so it could not be exported. Now: it gets a record. Two PDFs
  with the same DOI, in any spelling, share one record. The record gets the
  bibcode when ADS adds the paper later.

### Documentation

- **"My paper is not on ADS".** Before: the page started with a library-wide
  Identify. Now: it starts with one PDF and a typed DOI or arXiv ID. It then
  explains BibTeX export and the citation key styles.

- **Page names.** Before: the first pages were "Start with your PDF library",
  "Three everyday workflows", "Application guide", and "References outside
  ADS". Now: they are "Getting started", "Daily workflows", "App GUI guide",
  and "My paper is not on ADS". That page starts with the app menu item
  **File → Local References…**.
- **App pages without terminal commands.** Before: "My paper is not on ADS",
  "Daily workflows", and the App GUI guide mixed app steps with CLI commands.
  Now: these pages describe the app. "My paper is not on ADS" starts with two
  cases: a PDF with a DOI, and a paper without a DOI or a PDF. The CLI commands
  are in Tutorial 2 (step 8), Tutorial 3, Tutorial 4, and "CLI and Python".
- **Short README.** Before: the README had 122 lines and repeated the install,
  first-use, and update steps from the docs. Now: it has 47 lines with the
  three install commands, the daily workflows, and links to the docs. It has no
  version numbers, so a release needs no README edits.
- **Foldable menu.** Before: the documentation menu showed all four groups
  open. Now: only "Desktop app" and the group of the current page start open.
  Click a group name to fold or unfold it. The browser remembers your choice.
- **Install steps.** Before: "Installation and desktop builds" mixed app
  installation with 150 lines of build details for maintainers. Now: "Getting
  started" opens with a short Install section. The build details are in
  "Building the desktop app" under Project.
- **Plain language.** Before: the older pages had 104 hard findings from the
  ASD-STE100 linter, mostly passive voice and long sentences. Now: they have
  11. Each one is a word pair that names two different actions in context. No
  list has more than five items, and procedures
  use numbered steps.

## 0.5.1 (2026-10-22)

### New

- **PyPI package.** Before: the CLI and the Python client needed a source
  checkout and `uv sync`. Now: `pip install adsfolio` installs them, and
  `pip install "adsfolio[full,gui]"` adds the desktop application. PyPI has
  the same wheel and sdist as the GitHub release. Python installations see
  `pip install --upgrade adsfolio` in **About** and **Software Update**.

### Documentation

- **Documentation menu.** Before: the website listed 11 pages in one flat menu,
  and the CLI tutorials were separate from "CLI and Python workflows". Now: the
  menu has four groups: Desktop app, CLI and Python, API reference, and
  Project. "CLI and Python workflows" lists Tutorials 1 to 7 in order. The
  query-to-document example is Tutorial 7. API reference holds the Python API,
  the CLI reference, and the CV settings reference. All page addresses stay
  the same.
- **Stable menu.** Before: opening a page expanded its sections in the menu,
  so the menu changed on "CLI and Python workflows". Now: every page shows the
  same 17 menu items. Pages with several sections start with an "On this page"
  list.

### Fixed

- **Release checksums.** Before: a release could omit `adsfolio-packages.sha256`,
  and `SHA256SUMS.txt` then had no lines for the wheel and the sdist. Version
  0.5.0 had this problem. Now: the release workflow downloads the release
  package files and stops if a checksum file is missing. The 0.5.0 release now
  has the missing file and the complete `SHA256SUMS.txt`.

## 0.5.0 (2026-10-22)

### New

- **CV Generator.** Before: CV lists needed `adsfolio cv` in a terminal. Now:
  **Tools → CV Generator…** fills your query, ORCID iD, and name from your
  profile. It shows a live preview and saves the CV file. **Show as** shows the
  preview as Markdown or HTML that a reader sees, or as source. Each control has
  a tooltip.
- **Export selected papers.** Before: a list of chosen papers needed an ADS
  query. Now: select rows, right-click, and choose **Export Selected
  Papers…**. ADSfolio writes them with a CV template from the stored details,
  with no ADS request. A paper without a bibcode links to its DOI.
- **CV templates for any text format.** Before: CV output was LaTeX or Markdown
  in three fixed styles. Now: `format.output: template` uses your own header,
  section, entry, and footer text. Presets start from LaTeX, Markdown, HTML, or
  plain text. Example: the entry `<li>{authors} ({year}). {title}</li>` with
  `me_fmt: <b>{}</b>` gives `<li><b>Doe, J.</b> and Roe, A. (2025). Title</li>`.
- **Workflows guide.** Before: the BibTeX export, folder watching, and CV
  features were only in the long application guide. Now: "Three everyday
  workflows" shows each one with steps, screenshots, and a video. The README
  and the website link to it.

### Improved

- **Actions on several rows.** Before: with three rows selected, **Copy
  Bibcode** copied only the clicked row. Now: the menu says **Copy 3
  Bibcodes** and copies one bibcode per line. **Open**, **Reveal**, **Copy
  DOI**, **Copy Path**, and **Refresh ADS Records** also act on all selected
  rows. Refresh skips rows without a bibcode.
- **One-list CV templates.** Before: a template always split papers into
  First-authored and Co-authored sections. Now: `template.group: false` makes
  one Publications section in the original order.

### Fixed

- **Watch menu label.** Before: the menu showed "Automatically Identify Rename"
  without the ampersand. Some systems underlined the R. Now: the menu
  shows "Automatically Identify & Rename".

### Documentation

- **README screenshots.** Before: the README showed a version 0.2.1 screenshot
  without the welcome page or the round toolbar buttons. Now: the README shows
  the version 0.4.0 library window and the welcome page, and links to the
  first-run video.
- **Tutorial media.** Before: the guides had no screenshots or video. Now:
  "Start with your PDF library" opens with a 28-second first-run video. The guide
  also shows the welcome page, the subfolder question, the Identify prompt, the
  token window, and the library window. The application guide shows the token,
  profile, and **Software Update** windows.
- **Media script.** `uv run python scripts/capture_docs_media.py` makes all
  images and the video again from fictional sample data. The script uses a
  temporary home folder, so it does not read or change your settings, token,
  or library. The video needs `ffmpeg`. Use `--no-video` without it.

## 0.4.0 (2026-10-21)

### New

- **App Update button.** Before: update results showed only in **About** and in
  a startup notice. Now: a round **App Update** button in the toolbar opens
  **Software Update**.
  A blue dot on the button shows that a new version is available.
- **GitHub button.** Before: the main window had no link to the project page.
  Now: a round GitHub button next to **App Update** opens
  `https://github.com/ysBach/adsfolio` in your browser.
- **Software Update window.** Before: **View Update** opened the GitHub release
  page. Now: the window shows the installed version, the available version, and
  the changelog between them.
  - **Install** downloads the file for your system, checks its SHA-256 checksum,
    and opens it. ADSfolio does not replace the application.
  - **Skip This Version** hides the dot and the startup notice for that version.
- **Welcome page.** Before: a new installation showed empty panes and one
  button. Now: **Welcome to ADSfolio** shows three steps: add a PDF folder, add
  an ADS token (optional), and set a profile (optional). The page also lists the
  services that ADSfolio contacts.
- **Subfolder question.** Before: adding a folder with subfolders added only the
  top folder, with no message. Now: ADSfolio asks **Include Subfolders** or
  **This Folder Only**.
- **Test Token.** Before: **Save** stored the token with no check. Now:
  **Test Token** sends one search to ADS and shows "ADS accepted the token." or
  "ADS rejected the token." The dialog also shows the token source, for example
  `ADS_API_TOKEN` or a file path.

### Safer

- **Private token and profile files.** Before: other users on the computer could
  read `token` and `setup.json` for a short time during each save (mode `0644`
  with the usual umask). Now: ADSfolio creates both files with mode `0600`.
  When ADSfolio saves settings, it sets the settings folder to mode `0700`.
- **ORCID check.** Before: the profile saved `0000-0002-1825-0098`, which has a
  wrong check digit. Now: **Save** shows "invalid check digit" and keeps the
  dialog open. If you paste `https://orcid.org/0000-0002-1825-0097`, ADSfolio
  saves `0000-0002-1825-0097`.

### Fixed

- **Actions without a library.** Before: **Rename** and **Remove Root** were
  enabled when no library existed, and a click did nothing. Now: both stay
  disabled until you add a library.

## 0.3.0 (2026-10-21)

- The macOS application menu uses ADSfolio when launched from source.
  About displays the GitHub copyright handle.
- Shift-scroll uses smaller horizontal steps for mouse wheels and trackpads,
  including macOS wheel events with estimated pixel deltas. Header and scrollbar
  scrolling use the same speed. Small trackpad movements accumulate correctly.
- Identify offers a short explanation with OK to start. It checks stored bibcodes
  first and identifies the remaining papers. Adding a folder offers this prompt,
  and Needs ADS lookup has a direct action. Advanced options remain available.
  Lookup retains notes, locked fields, history, filenames, and file locations.
- ADS groups distinguish stored links from metadata readiness and PDF checks.
  Pending bibcodes can be linked from cache or batched ADS queries without
  changing existing metadata, filenames, or history.
- Equivalent Unicode filename spellings retain catalog identity when they refer
  to one directory entry. Distinct files and hardlinks remain separate.
- The AI CSV prompt requires exact identifiers and forbids invented bibcodes.
- The window stays idle after a job finishes. On macOS, the native job progress
  bar kept redrawing while hidden. Watched-folder checks examine each PDF with
  fewer filesystem calls.

- About ADSfolio shows the version, copyright, license links, and installation type.
  App details can be copied for bug reports.
- Help offers manual update checks and an optional check at startup.
  New releases appear without interrupting library work. Checks use public GitHub
  release information and never install updates or change library data.

## 0.2.1 (2026-10-19)

- Successful rename and identification notices use **OK** to acknowledge completion.

- Compact Details rows use field context menus and a resizable label column.
  Inline button preferences remain available outside compact mode.
- Libraries, Details, the job log, Find, Identify, and Rename have menu shortcuts.
  Column menus link directly to Appearance.
- Shorter ADS group labels include explanations of saved match status.

- A macOS source launcher can replace the installed app while retaining a backup.
  Reopening the app runs the checkout with its local environment.
- First-library guidance, vector icons, and gesture hints clarify common actions.
- Changed PDFs use a distinct warning marker until verification succeeds.
- Annotation review shows source and target PDFs side by side and saves a new copy.
  Originals remain unchanged. Unsupported annotations require an explicit decision.

- macOS builds include verified drag-to-install DMGs alongside ZIP archives.
- The README and website explain desktop setup and batch identification first.
- Verification views explain why a stored bibcode can need a new PDF check.
- Bibcode, arXiv, and filename cells open on double-click by default, so a click
  only selects the row. Appearance > Applications offers a persisted single-click
  option.

## 0.1.0 (2026-10-19)

- Windows file identifiers retain their full precision in the catalog.
  Invalid library paths are rejected before platform-specific path resolution.

- Closing the library window cancels pending search and watcher refresh timers
  before closing the catalog.

- The Python namespace, commands, application identifiers, and storage directories
  now use `adsfolio`. Earlier data is copied safely and reserved keys are migrated
  without changing catalog IDs, history, or manual locks.
- Repository setup, versioning, release tags, and Homebrew tap publication have
  explicit preview/apply scripts. Stable releases include an architecture-specific
  cask with verified checksums. Optional PyPI publication uses trusted publishing.
- A static landing page and documentation site can deploy through GitHub Pages.

- ADSfolio branding includes a deterministic papers-and-magnifier logo, canonical
  commands, and desktop bundles. Earlier data remains readable through migration.
- Crossref and DataCite provide DOI metadata without an ADS token. Provider order
  is configurable independently of extraction strategy order.
- Complete non-ADS metadata supports renaming without a bibcode. File changes
  invalidate readiness while retaining metadata and history; note edits cannot
  clear that safeguard. External lookup fills blanks instead of replacing edits.
- Release workflows build macOS ARM/Intel, Windows, and Linux bundles, verify
  checksums, and gate publication on package checks. Native platform acceptance
  and code signing remain separate release requirements.

- First-page title identification uses typography candidates and ADS verification,
  including larger journal mastheads and duplicate PDF form text. Conflicting
  matches remain unresolved; only verified metadata enters the catalog cache.
- Local library counts use SQL without decoding every record. Source and
  duplicate filters preserve their existing behavior.
- Nested HTML formatting produces LaTeX without changing CV author separators.
  Literal caret, tilde and backslash characters work inside sub/sup elements.
- HTTP session calls are synchronized while PDF response bodies stream in
  parallel. HTML-to-PDF fallback uses the supported Playwright API.
- Troubleshooting instructions now describe F5's local-file reconciliation.

- `discover_root(root, recursive=False)` previously marked nested PDF locations
  missing; it now limits missing-file checks to the scanned directory.
- `export(..., format="%R %T")` previously omitted the custom format from the
  ADS request; it now sends the format. `format="custom"` without a nonempty
  `custom_format` now raises `ValueError` before making a request.
- `adsfolio-gui` in a base installation previously raised `ModuleNotFoundError`;
  it now explains how to install the existing `gui` extra.
- Reading or editing one local reference previously loaded every cataloged
  document and attachment; it now retrieves only the selected document and
  its locations. Citation-key changes still check other references for crossrefs.
- The package version previously had separate source and build definitions;
  build metadata now uses `adsfolio.__version__`. The equivalent development
  version is spelled `0.1.0.dev0` consistently in installed metadata and CLI output.
- Release checks now cover Python 3.10–3.13, strict documentation, formatting,
  core lint rules, and installation of the built wheel without optional extras.
  License metadata uses SPDX syntax with an explicit license file.
- ADS BibTeX exports could contain unbalanced braces from asymmetric math
  escaping, e.g. `keywords = {..., 2001 KX\$\_\{76},`. BibTeX then swallowed
  the next entry and latexmk left every citation undefined. Now `bibtex` and
  `bibtexabs` exports (CLI, library watch, GUI library export) write
  `KX$_{76}$` and log a warning per repaired field; balanced output is unchanged.
- A citation without an ADS bibcode could not become a standalone local document.
  Now **File → Local References…** and `adsfolio reference` accept BibTeX or manual
  entries, such as `Smith2025Report`, without a DOI, PDF, or ADS token.
- BibTeX export was tied to ADS identifiers. Now `reference export 12 19`
  combines local and cataloged ADS references using their stored metadata;
  errors preserve an existing output file instead of writing a partial result.
- General BibTeX imports need nested braces and arbitrary citation keys. The new
  intake preserves bibliographic fields and original source text, expands macros,
  and includes required cross-reference parents when exporting.
- External metadata previously remained attached to individual PDF records.
  Local references now retain their own identity and citation key, with optional
  PDF links and editable metadata protected from background ADS updates.
- Added an external-reference workflow guide that distinguishes adsfolio's ADS
  research focus from broader reference-management workflows and documents the
  limits of offline export and bibliography backups.
