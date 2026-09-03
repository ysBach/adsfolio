# Identify Settings Persistence Design

## Goal

Make Tools → Identify remember its reusable settings across dialog openings and
application restarts. Users can choose global settings, which are the default,
or settings attached to the current PDF library. The CLI and GUI use the same
settings model, normalization, persistence, and resolution rules.

Unset year bounds display as blank fields. Every Identify form control has
concise tooltip and accessible help text.

## Canonical settings model

Introduce one `IdentifySettings` value object owned by the PDF identify domain,
not by the GUI. It contains the reusable settings that have the same meaning in
both interfaces:

- `recursive: bool`
- `debut_year: int | None`
- `end_year: int | None`
- `infer_from_filenames: bool`
- `output_unresolved: str | None`
- `verbose: bool`

The existing identity/prefetch fields remain part of the canonical identify
configuration and continue to be shared with the CLI:

- `orcid: str`
- `query_names: list[str]`
- `library_ids: list[str]`

`end_year` is canonical. Existing `retire_year` data remains readable during
migration but is written back as `end_year`. Missing years normalize to `None`;
the string `"Not set"` and integer `0` are not persisted values.

The following are deliberately one-run actions and are excluded from persisted
settings:

- `refresh_cache`
- `allow_ads`
- `apply_moves`

They start disabled whenever the dialog opens. This prevents a previous run
from silently repeating network-expensive or filesystem-changing behavior.

## Storage and migration

Global Identify settings live in the normal user setup configuration alongside
the existing global identify profile. They do not live in `gui.json`.

Library-specific Identify settings live in the existing per-root registry
metadata and are included in sidecar-compatible import/export. Replace the
narrow `identify_profile` payload with the canonical identify configuration.
Readers migrate existing profile-only data by filling the new reusable fields
with built-in defaults. Unknown fields are ignored and malformed fields fall
back independently rather than invalidating the whole configuration.

The GUI's selected scope is presentation state, so only that choice belongs in
`gui.json`. Store it per normalized library-root path. A root without a saved
choice uses `global`; selecting `library` for a root with no saved library
configuration initializes it from the current global configuration.

Paths in global settings are stored as entered. Relative paths resolve against
the active scan root at execution time. Library-specific paths should normally
be relative to the library root so they remain portable; absolute paths remain
valid but are inherently machine-specific.

## Resolution and CLI behavior

Both interfaces resolve an effective configuration in this order:

1. Built-in defaults.
2. The selected persisted scope (`global` or `library`).
3. Explicit values supplied for the current run.

Tools → Identify exposes a `Remember settings` selector with `All libraries`
first and selected by default, plus `This library only`. Switching the selector
loads the chosen scope. Pressing OK validates and saves the reusable fields to
that scope immediately, then starts Identify with the three one-run actions.
Cancel writes nothing.

The CLI gains an explicit settings-scope option and defaults to global settings,
matching the GUI. Reusable boolean flags become tri-state positive/negative
overrides: omitted inherits the selected scope, while the positive or negative
form overrides it for one invocation. A saved unresolved destination can be
overridden by another path or explicitly cleared for one invocation. This is an
intentional breaking cleanup where current one-way flags cannot represent an
explicit false value after persistence is introduced.

CLI run overrides do not mutate saved reusable settings. Persistent changes are
made by the setup/configuration workflow or by accepting the GUI dialog. The
effective identity/prefetch configuration may still be recorded with library
metadata as provenance after an Identify run.

## Dialog behavior and help text

The year controls use an empty special-value display while retaining a bounded
numeric editor. Validation rejects a lower year greater than the upper year.
Serialization omits CLI year overrides for blank fields.

Add descriptive tooltips and matching accessible descriptions to:

- Remember-settings scope
- Recursive scan
- Debut year
- End year
- Refresh cache
- Allow ADS fallback
- Infer from filenames
- Unresolved destination
- Destination chooser
- Apply unresolved moves
- Verbose output

Help text states operational consequences, especially ADS requests and file
moves. Labels retain their visible short names; tooltips supplement rather than
replace labels.

## Error handling

Settings are normalized before reaching widgets or command construction.
Malformed persisted booleans, years, collections, and paths fall back field by
field and emit a warning through `logging`.

If saving selected settings fails, the dialog remains open, no Identify process
starts, and a concise error message identifies the destination that could not be
saved. A corrupt or missing library configuration falls back to global settings
without rewriting storage merely by opening the dialog.

## Tests and documentation

Focused tests cover:

- canonical normalization and legacy `retire_year` migration;
- global and per-library persistence round trips;
- resolution precedence and explicit CLI positive/negative/clear overrides;
- global default scope and per-root remembered scope;
- switching to a new library scope copies global values;
- Cancel writes nothing and OK saves before process launch;
- one-run actions always reopen disabled;
- blank year display and omitted year arguments;
- invalid year ordering;
- non-empty tooltip and accessible help text for every interactive form item;
- save failures preventing process launch.

Update GUI and CLI documentation to describe the two scopes, the reusable
settings, the three one-run actions, and the intentional CLI flag changes.

## Deliberate exclusions

This change does not persist ADS API tokens in library metadata, automatically
delete settings for removed or moved library roots, or add new identity fields
to the Identify dialog. It does not remember `refresh_cache`, `allow_ads`, or
`apply_moves` under any scope.
