# My paper is not on ADS

ADS does not index every paper. Choose the case that matches your paper:

- **You have the PDF, and it has a DOI:**
  [Identify it with Crossref or DataCite](#identify-a-pdf-by-its-doi).
- **It has no DOI, or you have no PDF:**
  [Add it as a local reference](#add-a-local-reference). Examples are a book,
  a thesis, and a citation from Google Scholar.

Neither case needs an ADS token. For the command line, see
[Tutorial 2](cli-tutorials/02-export-and-bibtex.md#8-references-outside-ads).

## Identify a PDF by its DOI

1. Select the library, then choose **Identify**.
2. Choose **Advanced options…**.
3. In **Metadata providers**, clear **NASA/ADS**, or move **Crossref** and
   **DataCite** above it with **Up** and **Down**.
4. Choose **OK**.
5. Choose **Rename** to preview new filenames from the found metadata.

DOI lookup fills missing metadata and records its provider and file digest.
Existing metadata, manual edits, and catalog history remain intact.
**Reset recommended** restores the default order: NASA/ADS, Crossref, then
DataCite.

Extraction strategies decide where ADSfolio finds identifiers. Provider order
decides which service receives a DOI. These are separate settings.

### Renaming a paper that is not on ADS

You can rename a PDF that has no ADS record. It needs a current title, authors,
a year, and external or manual provenance. The default filename omits the
bibcode when none exists. A custom template still uses the fields that you
specify.

The paper stays in **Needs ADS lookup**. That view uses the saved ADS lookup
status, so it includes papers with complete non-ADS metadata. The status
tooltip shows whether the metadata is ready for renaming.

### When the PDF file changes

If a PDF's contents change, ADSfolio keeps the stored metadata but marks it
stale. Stale metadata cannot authorize automatic renaming. **F5** reconciles
local file facts. Renaming also checks a known content digest.

Editing notes does not revalidate stale metadata. DOI lookup fills only blank
fields. It does not replace retained fields, so it does not clear this
safeguard. Review a changed PDF before you relink its metadata.

## Add a local reference

Open **File → Local References…**. Use it for a book, a thesis, a report, or a
paper that you found elsewhere. You can save a reference without a PDF, a DOI,
or an ADS token.

### Copy a citation from Google Scholar

1. In Google Scholar, choose **Cite → BibTeX** under the result, then copy the
   BibTeX text.
2. In ADSfolio, open **File → Local References…** and choose **Import BibTeX…**.
3. Paste the text and choose **Preview**.
4. Check the citation key and the title.
5. Choose **Add references**.

To import a saved `.bib` file, use **Open .bib…** in the import window.
ADSfolio uses the text that Google Scholar exports. It does not scrape Scholar.
See the [Google Scholar help page](https://scholar.google.com/intl/en/scholar/help.html#export).

### Add, edit, and export

- **Add manually…** saves a reference that you type in.
- **Edit…** changes the fields of the selected reference.
- **Attach PDF…** links an existing PDF without moving it.
- **Export selected…** and **Copy BibTeX** write the selected references.

The list also shows papers already identified through ADS, including papers
outside the selected PDF folder. You can export ADS and local references
together in one bibliography. The main table still shows PDFs only. A reference
without a PDF appears only in **Local References…**.

## What ADSfolio keeps

Each reference has a local ID, a citation key, and an optional ADS bibcode.
`Smith2025Report` is a citation key. ADSfolio never sends it to ADS as a
fabricated bibcode. A complete external reference remains a local reference
even when ADS cannot identify it.

Import keeps every bibliographic field, including fields that the editor does
not show. Import also keeps the source text as provenance. Export uses the
current edited fields. To change these fields, use **Edit…** in
**Local References…**. The PDF detail pane directs edits there, so that several
attachments keep the same bibliography data.

Export writes the same bibliography, not a byte-for-byte copy of the imported
file. Whitespace and formatting can change.

- ADSfolio expands BibTeX macros.
- Export includes required cross-reference parents.
- A missing dependency causes an error instead of an incomplete bibliography.
- Export checks the selected references before it replaces an output file.
  An error keeps the existing file.

Import and save require unique citation keys and DOI identities. Correct any
duplicates first. ADSfolio does not merge references automatically. A failed
batch import saves no entries from that batch.

## Limits

- **ADS fields:** local export uses stored metadata. ADS fields that ADSfolio
  never fetched are not available automatically.
- **ADS libraries:** **Export ADS Library…** is a separate remote operation.
  ADS library synchronization excludes local-only references.
- **Linking to ADS later:** DOI lookup for a local reference and linking it to
  an ADS match are future steps.
- **Backups:** BibTeX does not keep attachment locations, local IDs, or import
  history. To keep them, back up the central catalog. Close ADSfolio before you
  copy the SQLite catalog, so that pending writes finish.

## Other reference managers

ADSfolio focuses on NASA ADS / SciXplorer research workflows. Its local
references fill gaps in ADS coverage. They cover intake, metadata correction,
optional PDF links, and mixed BibTeX export. ADSfolio does not add browser
capture, word-processor integration, or a cloud library.

[Zotero](https://www.zotero.org/support/quick_start_guide) and
[Mendeley](https://www.elsevier.com/products/mendeley) offer those broader
workflows. You can use one of them alongside ADSfolio, and exchange data through
`.bib` files.
