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
uv add pandas
```

`pandas` is needed only if you also generate talks from a CSV URL.

## Quick Run

```bash
uv run python docs/examples/build_cv_markdown.py \
  --first-author-prefix "bach, y" \
  --me-target "Bach, Y."
```

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
