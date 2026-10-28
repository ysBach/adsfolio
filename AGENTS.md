# ADSfolio contributor instructions

## Communication and documentation

Use both skills for responses, documentation, docstrings, and release notes:

- `i-have-adhd:i-have-adhd`
- `asd-ste100`

Find the skills in the local skill or plugin catalog. Read their `SKILL.md`
instructions before writing. If either required skill
is unavailable, stop and report the missing skill.

Lead with the result or next action. Use short sections and numbered procedures.
Keep each list to five items when possible. Group longer lists without losing
necessary details. Give one instruction per sentence. Keep instructions within
20 words and descriptions within 25 words. Use active voice and consistent terms.
Preserve uncertainty, conditions, units, and warnings. Do not claim certified
ASD-STE100 dictionary compliance.

Update affected user guides and the changelog with behavior changes. Explain
the user action, result, and recovery path. Keep implementation details in the
maintainer or API reference. Run the ASD-STE100 structural linter on edited prose.
Review each finding. Fix ambiguity without deleting necessary facts.

## Scope and data preservation

Fix application defects in application code. Preserve user documents and their
contents. Do not rewrite, replace, convert, or create changed document copies
unless the user requests those changes.

Before editing, state the intended behavior, permitted side effects, and failure
conditions. Preserve intervening edits. Test the unchanged failing input through
the requested workflow. Check content hashes after metadata updates and renames.
Improved error text alone does not complete a behavior fix.

Separate facts, assumptions, and estimates. Do not invent APIs or guarantees.
Ask before changing an ambiguous scientific decision. Do not add data-specific
production rules to improve test or benchmark results. Store secrets in a
secret manager or an untracked `.env` file.

## Development

Use `rg` for searches. Use RTK for commands when available, except for notebooks.
Use `rtk proxy` when exact diagnostics matter. Prefer files, APIs, and command-line
tools over screen captures. Explain why a capture is unavoidable before taking it.

Use `uv` and the project `.venv`. Do not synchronize dependencies into a shared
environment. Use logging in library code. Give public Python functions simple
type hints and NumPy-style docstrings.

At the last implementation stage, run isort and Ruff formatting on changed
Python files. Run Ruff checks and the relevant pytest suite. Preserve the full
release gates in `scripts/release.py` and GitHub Actions.

## Release completion

An explicit release request authorizes the version commit, tag, publication,
documentation deployment, and Homebrew update. Use the existing release tools.
Check the working tree before staging. Exclude credentials, personal files,
catalogs, and build output.

Update the version, lockfile, changelog, and affected docs together. Check the
published release assets and their checksums. Check PyPI, documentation, and
Homebrew publication before reporting those channels as complete. Report any
channel that remains unavailable.
