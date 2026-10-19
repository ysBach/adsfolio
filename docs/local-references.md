# References outside ADS

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
edits, and catalog history remain intact. Extraction strategy order determines
where identifiers come from; provider order determines which service receives
a DOI. These are separate settings.

Metadata readiness and ADS linkage are separate. A non-ADS PDF with current
title, author, year, and external or manual provenance can be renamed. The default
name omits the bibcode when none exists. Custom templates still use the fields
you specify. An external record remains in the **Unresolved** catalog view
because that view means no ADS match; its status tooltip shows metadata readiness.

If a PDF's contents change, stored metadata is retained but marked stale. Stale
metadata cannot authorize automatic renaming. F5 reconciles local file facts;
renaming also checks a known content digest. Editing notes does not revalidate
stale metadata. DOI lookup fills blanks and does not replace retained fields to
clear this safeguard; review a changed PDF before relinking its metadata.

## Purpose and scope

ADSfolio focuses on ADS / SciXplorer research workflows: queries, publication
matching, local PDF identification and renaming, and reproducible bibliography
output. Local references fill gaps in ADS coverage so a bibliography can include
a book, thesis, report, or paper found elsewhere.

[Zotero](https://www.zotero.org/support/quick_start_guide) and
[Mendeley](https://www.elsevier.com/products/mendeley) provide broad workflows for
collecting, organizing, and citing research. Some capabilities overlap: BibTeX
import is useful interoperability. ADSfolio's local-reference feature is scoped to
intake, metadata correction, optional PDF links, and mixed BibTeX export. It does
not add browser capture, word-processor integration, or a cloud library service.

Use another reference manager alongside ADSfolio when those workflows are useful;
exchange bibliography data through `.bib` files.

## Add a Google Scholar citation

1. In Google Scholar, choose **Cite → BibTeX** under the result and copy the text.
2. In ADSfolio, open **File → Local References…** and choose **Import BibTeX…**.
3. Paste the text, choose **Preview**, and inspect the citation key and title.
4. Choose **Add references**. Select the saved reference to edit it or attach a PDF.
5. Select the references you need and choose **Export selected…** or **Copy BibTeX**.

The [Google Scholar help page](https://scholar.google.com/intl/en/scholar/help.html#export)
documents citation export. This workflow uses the exported text; ADSfolio does not
scrape Scholar. Use **Open .bib…** in the import dialog for a saved bibliography.

The local-reference list includes documents already identified through ADS,
including those outside the currently selected PDF root. The existing main
table continues to display PDFs. A reference without a PDF appears in the
local-reference window.

## Use the CLI

Commands use the same central catalog as the desktop application. These commands
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

Use IDs printed by `reference list` in subsequent commands. Here `12` and `19`
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
An explicit `--key` changes a citation key; update your LaTeX citations yourself
when you make that change.

For experiments, use a separate catalog:

```bash
adsfolio reference --catalog ./scratch.sqlite import scholar.bib
adsfolio reference --catalog ./scratch.sqlite export --all -o scratch.bib
```

## Preservation and failures

Each reference has a local ID, a citation key, and an optional ADS bibcode.
`Smith2025Report` is a citation key; it is never sent to ADS as a fabricated
bibcode. A complete external reference remains a local reference even when ADS
cannot identify it.

Import preserves bibliographic fields, including fields not shown in the editor,
and retains the source text as provenance. Export uses the current edited fields.
Use **Edit…** in **Local References…** to change these fields; the PDF detail pane
directs edits there so multiple attachments keep consistent bibliography data.
Whitespace and formatting can change; export is a semantic bibliography export,
not a byte-for-byte copy of the imported file. BibTeX macros are expanded, and
required cross-reference parents are included in export. Missing dependencies
cause an error instead of an incomplete bibliography.

Duplicate citation keys or DOI identities require correction before import or
save. The first version does not automatically merge references. A failed batch
import saves no entries from that batch. Export validates the selected references
before replacing an output file; errors preserve the existing destination.

Local export uses stored metadata. ADS fields that have never been fetched are
not available automatically. **Export ADS Library…** remains a separate remote
operation, and local-only references are excluded from ADS library synchronization.

## Current boundaries

DOI lookup and linking an existing external reference to an ADS match are future
steps. This release provides offline import, manual editing, attachment linking,
and mixed export.

`adsfolio pdf export` backs up file-oriented state; it does not restore local
reference identities, even when a reference has a PDF, and does not include
standalone references. Use `adsfolio reference export --all -o references.bib`
for a portable bibliography copy. BibTeX does not back up attachment locations,
local IDs, or import history; retain a backup of the central catalog for that
state. Close adsfolio before copying the SQLite catalog so pending writes finish.
