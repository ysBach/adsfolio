# CV settings reference (`cv.yaml`)

`adsfolio cv` generates publication-list fragments for CV workflows from ADS query results.

The command is config-driven via YAML (`cv.yaml`) and supports:

- LaTeX output (primary)
- Markdown output
- user-defined templates for any text format: LaTeX, HTML, Markdown, plain
  text, or another format you write
- multiple rendering styles
- marker categories by bibcode
- status overrides for accepted/submitted preprints

The desktop application has the same features in **Tools → CV Generator…**.
See [CV Generator](gui.md#cv-generator). The window and the command share
`cv.yaml` in the ADSfolio configuration folder.

## Command surface

```bash
adsfolio cv init
adsfolio cv generate --config cv.yaml
adsfolio cv show-styles
```

Common generation overrides:

```bash
adsfolio cv generate --config cv.yaml --dry-run
adsfolio cv generate --config cv.yaml --format markdown --style detailed
adsfolio cv generate --config cv.yaml --author-format 'ads_custom:%3.2l'
adsfolio cv generate --config cv.yaml --output out/publications.tex
```

`--dry-run` still queries ADS and renders the result; it prints to stdout instead
of writing a file. `--format` selects a renderer but does not rewrite an existing
configured filename, so supply `--output out/publications.md` when switching a
LaTeX configuration to Markdown. Relative `output.dir` paths are resolved against
the YAML file's directory; an explicit CLI `--output` is relative to the current
working directory.

ORCID behavior:

- `adsfolio cv init` reads defaults from your `adsfolio set` profile.
- If profile ORCID exists, `cv init` enables ORCID matching by default.
- Use `adsfolio cv init --no-orcid` to disable that default at config creation.

## Config schema

Top-level sections:

- `author`
- `format`
- `markers`
- `status`
- `output`

Required field:

- `author.query`

### `author`

| Field | Type | Description |
| --- | --- | --- |
| `query` | string | ADS Solr query used as base query |
| `orcid` | string | ORCID identifier used when `use_orcid` is true |
| `use_orcid` | bool | Whether to add `orcid_pub:"<orcid>"` constraint |
| `debut_year` | int/null | Optional lower bound for publication year |
| `retire_year` | int/null | Optional upper bound for publication year |
| `exclude_bibstems` | list[string] | Bibstems to exclude via appended `bibstem:(- X)` clauses |
| `first_author_prefix` | string | Lowercase prefix used to classify first-authored papers |
| `me_target` | string | Name token used by `format_author_list_cv()` highlighting |
| `me_display` | string | Display name metadata for user context |

### `format`

| Field | Type | Description |
| --- | --- | --- |
| `output` | `latex` \| `markdown` \| `template` | Renderer target |
| `style` | `compact` \| `detailed` \| `bibcode-first` | Render style |
| `n_max_authors` | int | Author list truncation threshold |
| `n_min_authors` | int | Minimum shown before `et al.` |
| `n_me` | int | Ensure highlighted author appears within threshold |
| `author_sep` | string | Author separator |
| `me_fmt` | string | `.format()` template for highlighted author |
| `author_format` | string | `cv_default` or `ads_custom:<format>` |

### `template`

Used when `format.output` is `template`. Each part is text with `{name}`
placeholders. Only the names below are replaced. Other braces stay as written,
so LaTeX needs no doubled braces: `\textit{{title}}` becomes
`\textit{Dust in comet atmospheres}`. A misspelled placeholder, such as
`{titel}`, stays in the output unchanged.

| Field | Description |
| --- | --- |
| `preset` | Optional starting point: `latex`, `markdown`, `html`, or `text` |
| `header` | Once, at the top |
| `section` | Before each section (First-authored, then Co-authored) |
| `entry` | Once per publication |
| `section_end` | After each section, for example `</ol>` |
| `footer` | Once, at the end |
| `escape` | `latex`, `html`, or `none`: how ADS text is escaped |
| `group` | `true` (default) for First-authored and Co-authored sections; `false` for one section, Publications, in the original order |

A `preset` fills every part, `escape`, `format.me_fmt`, `format.author_sep`,
and `output.filename`. Keys that you write replace the preset values.

Placeholders:

| Part | Placeholders |
| --- | --- |
| `header`, `footer` | `{me}`, `{date}`, `{total}`, `{first_count}`, `{co_count}` |
| `section`, `section_end` | `{section}`, `{count}` |
| `entry` | `{index}`, `{authors}`, `{title}`, `{year}`, `{journal}`, `{volume}`, `{page}`, `{venue}`, `{bibcode}`, `{url}`, `{doi}`, `{citations}`, `{n_authors}`, `{status}`, `{markers}` |

`{venue}` joins journal, volume, and page and leaves out empty parts.
`{authors}` applies `format.me_fmt` to your name, for example `<b>{}</b>` in
HTML. ADSfolio escapes text from ADS for the `escape` target. It never changes
text that you write: the template, `me_fmt`, `author_sep`, `status`, and marker
symbols. With `escape: none`, ADS text is plain Unicode without markup.

```yaml
format:
  output: template
template:
  preset: html
  entry: '<li>{authors} ({year}). <a href="{url}">{title}</a>. {venue}.</li>'
```

Template output does not add the journal or marker legends. Write them in the
`footer` if you need them.

### `markers`

`markers` is a map of arbitrary marker categories:

```yaml
markers:
  highlight:
    symbol: "\\textbf{\\color{myblue} \\small \\ddagger}"
    description: "Selected publications"
    bibcodes:
      - "2024A&A...684A..80B"
```

Each category includes:

- `symbol` (string)
- `description` (string)
- `bibcodes` (list[string])

### `status`

`status` maps bibcode to free-text status string:

```yaml
status:
  "2025arXiv251207318L": "ACCEPTED by \\textbf{ApJL}"
```

### `output`

| Field | Type | Description |
| --- | --- | --- |
| `dir` | string | Output directory |
| `filename` | string | Output filename |
| `journal_legend` | bool | Append bibstem-to-journal legend |
| `latex.list_env` | `itemize` \| `enumerate` | LaTeX list environment |
| `latex.left_margin` | string | List left margin option |
| `latex.item_sep` | string | List item separation |

## ADS query build behavior

At runtime, `adsfolio cv` constructs query as:

1. include `orcid_pub:"<orcid>"` when `author.use_orcid` is true
2. include `year:[start TO end]` when `debut_year`/`retire_year` are set
3. include `author.query`
4. append `bibstem:(- <value>)` for each `author.exclude_bibstems` item

## Rendering behavior

### Classification

Fetched ADS docs are split into:

- first-authored: `first_author` starts with `first_author_prefix`
- co-authored: all remaining docs

### Marker and status application

- marker symbols are appended for bibcodes listed under each marker category
- status text is appended from `status[bibcode]` when present
- non-peer-reviewed documents (`NOT REFEREED`, excluding arXiv) are tracked and marked

### Author formatting modes

`adsfolio cv` supports two author formatting modes:

- `cv_default`:
  uses local `format_author_list_cv()` with `n_max_authors`, `n_min_authors`, `n_me`, `me_target`, and `me_fmt`.
- `ads_custom:<format>`:
  requests author formatting from ADS/SciX export custom format API (`/export/custom`), using the supplied format string.

Reference for the custom format syntax:
[SciX Help: Export Results](https://scixplorer.org/scixhelp/actions-scix/export).

Important details from the SciX custom-format reference:

- Syntax is `printf`-like with `%` conversion specifiers.
- Author truncation can use `%[n.m]l` (example: `%3.2l`).
- Useful author directives include `%l`, `%A`, `%G`, `%m`, `%M`.
- In `adsfolio cv`, this custom output replaces only the author segment of each rendered entry.
- Custom author formatting makes an export request per publication. If that
  request raises `ADSError`, generation logs a warning and retains local author
  formatting for that publication.

### Journal handling

- journal display uses `format_journal(..., fmt="full")`
- `arXiv e-prints` is normalized to `arXiv`
- optional bibstem-to-journal legend can be appended

## Full example

See the tutorial page for a full working YAML and end-to-end command sequence:

- [Tutorial 6: CV Workflows](./cli-tutorials/06-cv-workflows.md)
