# Changelog

## 0.1.0 (2026-10-19)

- Closing the library window cancels pending search and watcher refresh timers
  before closing the catalog.

- The Python namespace, commands, application identifiers, and storage directories
  now use `adsfolio`. Earlier data is copied safely and reserved keys are migrated
  without changing catalog IDs, history, or manual locks.
- Repository setup, versioning, release tags, and Homebrew tap publication have
  explicit preview/apply scripts. Stable releases include an architecture-specific
  cask with verified checksums. Optional PyPI publication uses trusted publishing.
- A static landing page and documentation site can deploy through GitHub Pages.

- ADSfolio branding includes a deterministic papers-and-magnifier logo, canonical
  commands, and desktop bundles. Earlier data remains readable through migration.
- Crossref and DataCite provide DOI metadata without an ADS token. Provider order
  is configurable independently of extraction strategy order.
- Complete non-ADS metadata supports renaming without a bibcode. File changes
  invalidate readiness while retaining metadata and history; note edits cannot
  clear that safeguard. External lookup fills blanks instead of replacing edits.
- Release workflows build macOS ARM/Intel, Windows, and Linux bundles, verify
  checksums, and gate publication on package checks. Native platform acceptance
  and code signing remain separate release requirements.

- First-page title identification uses typography candidates and ADS verification,
  including larger journal mastheads and duplicate PDF form text. Conflicting
  matches remain unresolved; only verified metadata enters the catalog cache.
- Local library counts use SQL without decoding every record. Source and
  duplicate filters preserve their existing behavior.
- Nested HTML formatting produces LaTeX without changing CV author separators.
  Literal caret, tilde and backslash characters work inside sub/sup elements.
- HTTP session calls are synchronized while PDF response bodies stream in
  parallel. HTML-to-PDF fallback uses the supported Playwright API.
- Troubleshooting instructions now describe F5's local-file reconciliation.

- `discover_root(root, recursive=False)` previously marked nested PDF locations
  missing; it now limits missing-file checks to the scanned directory.
- `export(..., format="%R %T")` previously omitted the custom format from the
  ADS request; it now sends the format. `format="custom"` without a nonempty
  `custom_format` now raises `ValueError` before making a request.
- `adsfolio-gui` in a base installation previously raised `ModuleNotFoundError`;
  it now explains how to install the existing `gui` extra.
- Reading or editing one local reference previously loaded every cataloged
  document and attachment; it now retrieves only the selected document and
  its locations. Citation-key changes still check other references for crossrefs.
- The package version previously had separate source and build definitions;
  build metadata now uses `adsfolio.__version__`. The equivalent development
  version is spelled `0.1.0.dev0` consistently in installed metadata and CLI output.
- Release checks now cover Python 3.10–3.13, strict documentation, formatting,
  core lint rules, and installation of the built wheel without optional extras.
  License metadata uses SPDX syntax with an explicit license file.
- ADS BibTeX exports could contain unbalanced braces from asymmetric math
  escaping, e.g. `keywords = {..., 2001 KX\$\_\{76},`. BibTeX then swallowed
  the next entry and latexmk left every citation undefined. Now `bibtex` and
  `bibtexabs` exports (CLI, library watch, GUI library export) write
  `KX$_{76}$` and log a warning per repaired field; balanced output is unchanged.
- A citation without an ADS bibcode could not become a standalone local document.
  Now **File → Local References…** and `adsfolio reference` accept BibTeX or manual
  entries, such as `Smith2025Report`, without a DOI, PDF, or ADS token.
- BibTeX export was tied to ADS identifiers. Now `reference export 12 19`
  combines local and cataloged ADS references using their stored metadata;
  errors preserve an existing output file instead of writing a partial result.
- General BibTeX imports need nested braces and arbitrary citation keys. The new
  intake preserves bibliographic fields and original source text, expands macros,
  and includes required cross-reference parents when exporting.
- External metadata previously remained attached to individual PDF records.
  Local references now retain their own identity and citation key, with optional
  PDF links and editable metadata protected from background ADS updates.
- Added an external-reference workflow guide that distinguishes adsfolio's ADS
  research focus from broader reference-management workflows and documents the
  limits of offline export and bibliography backups.
