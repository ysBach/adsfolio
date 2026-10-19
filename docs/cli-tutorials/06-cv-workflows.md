# Tutorial 6: CV Workflows

This tutorial covers `adsfolio cv`: initialize a config, generate publication lists, and select output styles.

## 0) Start with `--help`

~~~bash
adsfolio cv --help
adsfolio cv init --help
adsfolio cv generate --help
adsfolio cv show-styles --help
~~~

Core commands:

| Command | Purpose |
| --- | --- |
| `adsfolio cv init` | Create a starter `cv.yaml` config interactively |
| `adsfolio cv generate` | Query ADS and render output from `cv.yaml` |
| `adsfolio cv show-styles` | Print compact examples for supported styles |

Key `init` options:

| Option | Meaning |
| --- | --- |
| `--output FILE` | Path for starter config (default: `cv.yaml`) |
| `--force` | Overwrite output file if it already exists |
| `--no-orcid` | Do not auto-use ORCID from `adsfolio set` profile |

Key `generate` options:

| Option | Meaning |
| --- | --- |
| `--config FILE` | Path to YAML config (default: `cv.yaml`) |
| `--output FILE` | Override output path |
| `--format latex\|markdown` | Override configured output format |
| `--style compact\|detailed\|bibcode-first` | Override configured style |
| `--author-format TEXT` | Override author formatting (`cv_default` or `ads_custom:<format>`) |
| `--dry-run` | Print output to stdout instead of writing file |

## 1) Create starter config

Run interactive setup:

~~~bash
adsfolio cv init
~~~

`cv init` uses your saved `adsfolio set` profile as defaults:

- ORCID (`orcid`) and ORCID usage (`use_orcid`)
- optional publication window (`debut_year` / `retire_year`)
- saved query names for default query/prefix hints

If an ORCID exists in your `adsfolio set` profile, `cv init` enables ORCID matching by default.
To disable this behavior:

~~~bash
adsfolio cv init --no-orcid
~~~

`cv init` writes `cv.yaml` with:

- ADS query + author identity settings
- output format and style
- marker categories (for highlighted bibcodes)
- status overrides (for accepted/submitted preprints)
- output path + LaTeX list settings

Write to a different path:

~~~bash
adsfolio cv init --output configs/my-cv.yaml
~~~

Overwrite an existing config:

~~~bash
adsfolio cv init --force
~~~

## 2) Minimal `cv.yaml` example

```yaml
author:
  query: 'author:"doe, jane" year:2010-'
  orcid: "0000-0002-1825-0097"
  use_orcid: true
  debut_year: 2010
  retire_year: null
  exclude_bibstems:
    - zndo
    - yCat
    - EPSC
    - cosp
    - AGUFM
    - BKAS
    - LPICo
    - IAUFM
    - AAS
  first_author_prefix: "doe, j"
  me_target: "Doe, J."
  me_display: "Jane Doe"

format:
  output: latex
  style: compact
  n_max_authors: 5
  n_min_authors: 3
  n_me: 6
  author_sep: "; "
  me_fmt: "\\textbf{{\\color{{black}}{{{}}}}}"
  author_format: "cv_default"

markers:
  highlight:
    symbol: "\\textbf{\\color{myblue} \\small \\ddagger}"
    description: "Selected publications"
    bibcodes:
      - "2024A&A...684A..80B"
      - "2024A&A...684A..81B"
  student:
    symbol: "*"
    description: "Student-led publications"
    bibcodes: []

status:
  "2025arXiv251207318L": "ACCEPTED by \\textbf{ApJL}"

output:
  dir: "."
  filename: "publ.tex"
  journal_legend: true
  latex:
    list_env: "itemize"
    left_margin: "-14em"
    item_sep: "6pt"
```

Notes:

- `author.query` is required.
- `author.use_orcid: true` plus `author.orcid` makes query execution ORCID-first.
- `output.latex.list_env` supports `itemize` or `enumerate`.
- `format.style` supports `compact`, `detailed`, and `bibcode-first`.
- `format.author_format` supports `cv_default` and `ads_custom:<format>`.
- If a publication has `NOT REFEREED` and is not arXiv, `adsfolio cv` marks it as non-peer-reviewed.
- `me_fmt` uses Python `.format()`: keep doubled braces for literal LaTeX braces.

## 3) Generate output

From config defaults:

~~~bash
adsfolio cv generate --config cv.yaml
~~~

Dry-run to inspect without writing:

~~~bash
adsfolio cv generate --config cv.yaml --dry-run
~~~

Override output path:

~~~bash
adsfolio cv generate --config cv.yaml --output out/publications.tex
~~~

Switch renderer at runtime:

~~~bash
adsfolio cv generate --config cv.yaml --format markdown --style detailed --output out/publications.md
~~~

Override author format at runtime:

~~~bash
adsfolio cv generate --config cv.yaml --author-format 'ads_custom:%3.2l'
~~~

## 4) ADS Custom Author Format

For `format.author_format` (or `--author-format`), use:

- `cv_default`: built-in local author formatter (`format_author_list_cv`)
- `ads_custom:<FORMAT>`: SciX/ADS custom formatter (`/export/custom`) for authors

Reference syntax is documented in SciX Help:
[Export Results](https://scixplorer.org/scixhelp/actions-scix/export).

The custom format string follows a `printf`-like directive style. Useful author directives include:

- `%l`: `lastname, f. m.` with `&` separator
- `%A`: `lastname, firstname m.` style
- `%G`: `lastname, f. m.` without ampersand styling
- `%m` / `%M`: lastname-centric compact variants

Truncation is supported for author-list directives:

- `%[n.m]l`
- Example: `%3.2l` means if author count > 3, keep through author 2 then `et al.`

Examples:

```yaml
format:
  author_format: "ads_custom:%l"
```

```yaml
format:
  author_format: "ads_custom:%3.2l"
```

Important:

- In `adsfolio cv`, `ads_custom` output replaces only the author string field.
- Use author-focused directives for best results. Full-reference formats (with title/journal/year directives) will be inserted as the author segment.

## 5) Compare styles

Preview style signatures:

~~~bash
adsfolio cv show-styles
~~~

Style intent:

- `compact`: two-line compact item optimized for dense CV lists.
- `detailed`: fuller line layout with explicit ADS link line.
- `bibcode-first`: bibcode-centered item label format.

## 6) Output behavior

`adsfolio cv generate`:

- runs a single ADS query using your configured `author.query` plus `exclude_bibstems`
- classifies documents into first-authored vs co-authored groups
- renders either LaTeX or Markdown
- writes one output file (unless `--dry-run` is set)

For LaTeX output with `journal_legend: true`, a bibstem-to-journal legend is appended at the end.

## Next

Return to [Operations and Troubleshooting](./05-operations-and-troubleshooting.md) for general CLI debugging patterns.
