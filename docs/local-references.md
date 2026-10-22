# Local references

Open **File → Local References…** to add a reference that ADS does not index.
You can save a citation without a PDF, DOI, or ADS token.

## Identify PDFs by DOI

Choose providers in the Identify dialog, or set their order explicitly:

```bash
adsfolio pdf identify /path/to/papers --provider crossref --provider datacite
adsfolio pdf rename /path/to/papers
```

This example does not use ADS or require an ADS token. DOI lookup fills missing
metadata and records its provider and file digest. Existing metadata, manual
edits, and catalog history remain intact.

Extraction strategy order determines where identifiers come from. Provider order
determines which service receives a DOI. These are separate settings.

Metadata readiness and ADS linkage are separate. You can rename a non-ADS PDF
with current title, author, year, and external or manual provenance. The default
name omits the bibcode when none exists. Custom templates still use the fields
you specify. An external record remains in the **Unresolved** catalog view
because that view means no ADS match. The record's status tooltip shows metadata
readiness.

If a PDF's contents change, ADSfolio keeps the stored metadata but marks it
stale. Stale metadata cannot authorize automatic renaming. F5 reconciles local
file facts. Renaming also checks a known content digest.

Editing notes does not revalidate stale metadata. DOI lookup fills only blank
fields. It does not replace retained fields, so it does not clear this
safeguard. Review a changed PDF before you relink its metadata.

## Purpose and scope

ADSfolio focuses on ADS / SciXplorer research workflows: queries, publication
matching, local PDF identification and renaming, and reproducible bibliography
output. Local references fill gaps in ADS coverage so a bibliography can include
a book, thesis, report, or paper found elsewhere.

[Zotero](https://www.zotero.org/support/quick_start_guide) and
[Mendeley](https://www.elsevier.com/products/mendeley) provide broad workflows for
collecting, organizing, and citing research. Some capabilities overlap: BibTeX
import is useful interoperability. ADSfolio limits its local-reference feature to
intake, metadata correction, optional PDF links, and mixed BibTeX export. It does
not add browser capture, word-processor integration, or a cloud library service.

Use another reference manager alongside ADSfolio when those workflows are useful.
Exchange bibliography data through `.bib` files.

## Add a Google Scholar citation

**Copy the citation:**

1. In Google Scholar, choose **Cite → BibTeX** under the result.
2. Copy the BibTeX text.

**Import it:**

1. In ADSfolio, open **File → Local References…**.
2. Choose **Import BibTeX…**.
3. Paste the text and choose **Preview**.
4. Inspect the citation key and title.
5. Choose **Add references**.

**Edit and export:**

1. To edit the saved reference or attach a PDF, select it.
2. Select the references you need.
3. Choose **Export selected…** or **Copy BibTeX**.

To import a saved bibliography, use **Open .bib…** in the import dialog.
The [Google Scholar help page](https://scholar.google.com/intl/en/scholar/help.html#export)
documents citation export. This workflow uses the exported text. ADSfolio does
not scrape Scholar.

The local-reference list includes documents already identified through ADS,
including those outside the currently selected PDF root. The existing main
table continues to show PDFs. A reference without a PDF appears in the
local-reference window.

## Use the CLI

These commands use the same central catalog as the desktop application. They
do not contact ADS or require a token.

```bash
# Inspect parsed entries without saving references.
adsfolio reference import scholar.bib --preview

# Import BibTeX; the command prints each new local ID and citation key.
adsfolio reference import scholar.bib --source google-scholar

# Save a manual reference without a PDF.
adsfolio reference add --key Smith2025Report --type techreport \
  --field 'title=Observing log' --field 'author=Smith, A.' \
  --field 'year=2025' --field 'institution=Example Observatory'

# Inspect local IDs and complete metadata.
adsfolio reference list --json
```

In later commands, use the IDs that `reference list` prints. Here `12` and `19`
are example local IDs, not ADS bibcodes or citation keys.

```bash
# Change one field; other fields remain intact.
adsfolio reference edit 12 --field 'title=Corrected observing log'

# Link an existing PDF without moving it.
adsfolio reference attach 12 /path/to/report.pdf

# Export ADS and external references together, in the requested order.
adsfolio reference export 12 19 -o manuscript.bib

# Export the entire local bibliography.
adsfolio reference export --all -o references.bib
```

Omit `-o` to write BibTeX to standard output. `reference import -` reads BibTeX
from standard input. Use `reference edit 12 --remove-field url` to remove a field.
An explicit `--key` changes a citation key. When you make that change, update
your LaTeX citations yourself.

For experiments, use a separate catalog:

```bash
adsfolio reference --catalog ./scratch.sqlite import scholar.bib
adsfolio reference --catalog ./scratch.sqlite export --all -o scratch.bib
```

## Preservation and failures

Each reference has a local ID, a citation key, and an optional ADS bibcode.
`Smith2025Report` is a citation key. ADSfolio never sends it to ADS as a
fabricated bibcode. A complete external reference remains a local reference even
when ADS cannot identify it.

Import preserves bibliographic fields, including fields that the editor does not
show. Import also retains the source text as provenance. Export uses the current
edited fields.

Use **Edit…** in **Local References…** to change these fields. The PDF detail
pane directs edits there so multiple attachments keep consistent bibliography
data.

Whitespace and formatting can change. Export is a semantic bibliography export,
not a byte-for-byte copy of the imported file. ADSfolio expands BibTeX macros.
Export includes required cross-reference parents. Missing dependencies cause an
error instead of an incomplete bibliography.

Import and save require unique citation keys and DOI identities. Correct any
duplicates first. The
first version does not automatically merge references. A failed batch import
saves no entries from that batch. Export checks the selected references before
it replaces an output file. Errors preserve the existing destination.

Local export uses stored metadata. ADS fields that ADSfolio never fetched are
not available automatically. **Export ADS Library…** remains a separate remote
operation. ADS library synchronization excludes local-only references.

## Current boundaries

DOI lookup and linking an existing external reference to an ADS match are future
steps. This release provides offline import, manual editing, attachment linking,
and mixed export.

`adsfolio pdf export` backs up file-oriented state. It does not restore local
reference identities, even when a reference has a PDF. It also does not include
standalone references. Use `adsfolio reference export --all -o references.bib`
for a portable bibliography copy.

BibTeX does not back up attachment locations, local IDs, or import history. To
keep that state, retain a backup of the central catalog. Close adsfolio before
you copy the SQLite catalog so pending writes finish.
