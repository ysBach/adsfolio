# Tutorial 2: Export and BibTeX

This tutorial covers `adsfolio export` output formats and BibTeX workflows.

For references outside ADS, use `adsfolio reference import` and `adsfolio reference
export`. These local commands can combine imported citations and cataloged ADS
documents in one bibliography. See [Local references](../local-references.md).

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

## Next

Continue with [Tutorial 3: Library workflows](./03-library-workflows.md).
