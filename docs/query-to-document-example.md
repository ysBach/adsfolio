# Query-to-Document Example (Markdown First)

This guide shows how to:

1. query ADS with `adsfolio`,
2. split first-authored vs co-authored papers,
3. optionally merge a talks CSV (Google Sheets export),
4. generate clean Markdown outputs for CV/publication documents.

You can later convert the Markdown files to LaTeX with minimal changes.

## Files

- Example script: `docs/examples/build_cv_markdown.py`
- Outputs (default): `cv_example_output/publications.md`, `cv_example_output/talks.md`, `cv_example_output/cv_bundle.md`

## Prerequisites

1. Set your ADS token:

```bash
export ADS_API_TOKEN="YOUR_TOKEN"
```

2. Install dependencies (repo uses `uv`):

```bash
uv sync
```

This uses the project's `.venv`; never synchronize a shared environment.
`pandas` is needed only for talks from a CSV URL. Add it temporarily to that run
with `uv run --with pandas python docs/examples/build_cv_markdown.py
--talks-csv-url 'https://example.org/talks.csv'`. Use an actual CSV URL in place
of the example. This leaves the package's dependency declarations unchanged.

## Quick Run

```bash
uv run python docs/examples/build_cv_markdown.py \
  --first-author-prefix "bach, y" \
  --me-target "Bach, Y."
```

Set `--query` to your ADS query and `--output-dir` to choose a destination.
The example performs live ADS queries and writes Markdown; it has no dry-run
option. For a config-driven workflow that can print output without writing it,
use [`adsfolio cv generate`](cv.md) (its dry-run still queries ADS).

## What the script does

- Uses `ADSClient.search.query_all(...)` so you do not need to paginate manually.
- Requests CV-relevant ADS fields (`bibcode`, `title`, `author`, `year`, `citation_count`, etc.).
- Splits records by `first_author` prefix.
- Formats publication entries with:
  - ADS abstract links
  - author highlighting
  - citation counts
  - optional status markers/comments
- Optionally reads talks CSV and writes invited/contributed sections.

## Customization points

In `docs/examples/build_cv_markdown.py`, edit:

- `DEFAULT_QUERY`: your ADS search query.
- `HIGHLIGHT_BIBCODES`: selected papers marker.
- `STUDENT_BIBCODES`: student-led marker.
- `STATUS_BY_BIBCODE`: per-paper notes like accepted/submitted status.
- `RenderConfig`: marker symbols and author highlighting format.

## Notes for LaTeX users

The generated files are Markdown so they are easy to inspect and diff.
When you are ready:

1. Convert with Pandoc:

```bash
pandoc cv_example_output/publications.md -o cv_example_output/publications.tex
```

2. Or replace `format_publication_markdown(...)` / `format_talk_markdown(...)`
   with TeX emitters (your existing workflow already maps cleanly to this structure).

`adsfolio` already provides helpers that are LaTeX-friendly (for example `format_title_latex`, `format_author_list_cv`, `format_journal`).
