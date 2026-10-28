# Tutorial 2: Export and BibTeX

This tutorial covers `adsfolio export` output formats and BibTeX workflows.

For references outside ADS, see [step 8](#8-references-outside-ads).

## 0) Start with `--help`

Before you run the examples, check the complete export options:

~~~bash
adsfolio export --help
~~~

Key export options:

| Option | Meaning | When to use |
| --- | --- | --- |
| `--format` | Export format (`bibtex`, `aastex`, `mnras`, `custom`, etc.) | Match your journal or manuscript toolchain |
| `--output` | Write result to file instead of stdout | Build or update `.bib` files directly |
| `--sort` | Sorting rule in export endpoint | Keep deterministic bibliography ordering |
| `--custom` | Custom ADS format string (used with `--format custom`) | Produce compact custom references/labels |
| `--journal` | Journal style (`ads`, `full`, `iso4`) | Select macro/full/ISO4 journal naming policy |

## 1) Export a single bibcode

```bash
adsfolio export '2024A&A...684A..80B'
```

The default format is `bibtex`.

## 2) Export multiple bibcodes

```bash
adsfolio export '2024A&A...684A..80B' '2021A&A...654A.113B'
```

## 3) Save to file

```bash
adsfolio export '2024A&A...684A..80B' '2021A&A...654A.113B' \
  --format bibtex \
  --output references.bib
```

## 4) Choose format and sort

```bash
adsfolio export '2024A&A...684A..80B' \
  --format aastex \
  --sort "first_author asc"
```

## 5) Custom export template

```bash
adsfolio export '2024A&A...684A..80B' \
  --format custom \
  --custom "%R %T"
```

## 6) Journal name style

```bash
# ADS macro style (default, e.g. \apj)
adsfolio export '2024A&A...684A..80B' --journal ads

# Full journal name
adsfolio export '2024A&A...684A..80B' --journal full

# ISO4 abbreviation (requires iso4 from adsfolio[full])
adsfolio export '2024A&A...684A..80B' --journal iso4
```

## 7) Typical flow from search to export

1. Run `adsfolio search` to find bibcodes.
2. Copy selected bibcodes.
3. Export them into a `.bib` file.

```bash
adsfolio search "author:Einstein" --rows 5
adsfolio export <bibcode1> <bibcode2> --output refs.bib
```

## 8) References outside ADS

### One PDF that ADS does not index

Give the PDF its DOI or arXiv ID. When ADS has no match, ADSfolio asks
Crossref and DataCite, then renames the PDF. No ADS token is necessary. The
catalog must already track the PDF. If it does not, run `adsfolio pdf identify`
on the library first.

```bash
adsfolio pdf refresh-ads ~/papers --identifier 10.5555/jex.2019.12.34 \
  --relative-path scan_0042.pdf
adsfolio pdf refresh-ads ~/papers --identifier arXiv:2401.12345 \
  --relative-path preprint.pdf
```

The command refuses an identifier that contradicts the PDF's own arXiv stamp or
metadata DOI. If the PDF states no such identifier, add `--confirm-identity`
after you check the paper yourself. See
[PDF identity checks](../gui.md#pdf-identity-checks).

To identify only some PDFs with the saved strategies, repeat `--file`:

```bash
adsfolio pdf identify ~/papers --file ~/papers/a.pdf --file ~/papers/b.pdf
```

### Citation keys

Each BibTeX export uses the saved key styles. The defaults are the bibcode for
ADS papers and the DOI for other papers.

```bash
# Show the styles.
adsfolio reference keys
# Use Google Scholar style keys, such as roe2019grain, for papers outside ADS.
adsfolio reference keys --other scholar
# Use a template for ADS papers.
adsfolio reference keys --ads template --template '{first_author_initials}{year}'
```

The styles are `bibcode` (ADS papers only), `doi`, `template`, `scholar`, and
`manual`. Export stops when a paper has no key or shares a key. The command
lists those papers. Set a key for each one, then export again:

```bash
adsfolio reference set-key 12 Roe2019Grains
```

A key from `set-key`, or from imported BibTeX, always wins over the style.

### Books, theses, and Google Scholar citations

`adsfolio reference` keeps references that ADS does not index, such as a book,
a thesis, or a Google Scholar citation. It can export them together with
cataloged ADS documents in one bibliography. The commands use the same central
catalog as the desktop app. They do not contact ADS or need a token. For the
desktop steps, see [My paper is not on ADS](../local-references.md).

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

Later commands use the IDs that `reference list` prints. Here `12` and `19` are
example local IDs, not ADS bibcodes or citation keys.

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

- Omit `-o` to write BibTeX to standard output.
- `reference import -` reads BibTeX from standard input.
- `reference edit 12 --remove-field url` removes a field.
- An explicit `--key` changes a citation key. Then update your LaTeX citations
  yourself.

For experiments, use a separate catalog:

```bash
adsfolio reference --catalog ./scratch.sqlite import scholar.bib
adsfolio reference --catalog ./scratch.sqlite export --all -o scratch.bib
```

`adsfolio pdf export` backs up file-oriented state. It does not restore local
reference identities, even when a reference has a PDF, and it does not include
standalone references. For a portable bibliography copy, use
`adsfolio reference export --all -o references.bib`.

## Next

Continue with [Tutorial 3: Library workflows](./03-library-workflows.md).
