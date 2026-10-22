# Changelog

## 0.5.1 (2026-10-22)

### New

- **PyPI package.** Before: the CLI and the Python client needed a source
  checkout and `uv sync`. Now: `pip install adsfolio` installs them, and
  `pip install "adsfolio[full,gui]"` adds the desktop application. PyPI has
  the same wheel and sdist as the GitHub release. Python installations see
  `pip install --upgrade adsfolio` in **About** and **Software Update**.

### Documentation

- **Documentation menu.** Before: the website listed 11 pages in one flat menu,
  and the CLI tutorials were separate from "CLI and Python workflows". Now: the
  menu has four groups: Desktop app, CLI and Python, API reference, and
  Project. "CLI and Python workflows" lists Tutorials 1 to 7 in order. The
  query-to-document example is Tutorial 7. API reference holds the Python API,
  the CLI reference, and the CV settings reference. All page addresses stay
  the same.
- **Stable menu.** Before: opening a page expanded its sections in the menu,
  so the menu changed on "CLI and Python workflows". Now: every page shows the
  same 17 menu items. Pages with several sections start with an "On this page"
  list.

### Fixed

- **Release checksums.** Before: a release could omit `adsfolio-packages.sha256`,
  and `SHA256SUMS.txt` then had no lines for the wheel and the sdist. Version
  0.5.0 had this problem. Now: the release workflow downloads the release
  package files and stops if a checksum file is missing. The 0.5.0 release now
  has the missing file and the complete `SHA256SUMS.txt`.

## 0.5.0 (2026-10-22)

### New

- **CV Generator.** Before: CV lists needed `adsfolio cv` in a terminal. Now:
  **Tools → CV Generator…** fills your query, ORCID iD, and name from your
  profile. It shows a live preview and saves the CV file. **Show as** shows the
  preview as Markdown or HTML that a reader sees, or as source. Each control has
  a tooltip.
- **Export selected papers.** Before: a list of chosen papers needed an ADS
  query. Now: select rows, right-click, and choose **Export Selected
  Papers…**. ADSfolio writes them with a CV template from the stored details,
  with no ADS request. A paper without a bibcode links to its DOI.
- **CV templates for any text format.** Before: CV output was LaTeX or Markdown
  in three fixed styles. Now: `format.output: template` uses your own header,
  section, entry, and footer text. Presets start from LaTeX, Markdown, HTML, or
  plain text. Example: the entry `<li>{authors} ({year}). {title}</li>` with
  `me_fmt: <b>{}</b>` gives `<li><b>Doe, J.</b> and Roe, A. (2025). Title</li>`.
- **Workflows guide.** Before: the BibTeX export, folder watching, and CV
  features were only in the long application guide. Now: "Three everyday
  workflows" shows each one with steps, screenshots, and a video. The README
  and the website link to it.

### Improved

- **Actions on several rows.** Before: with three rows selected, **Copy
  Bibcode** copied only the clicked row. Now: the menu says **Copy 3
  Bibcodes** and copies one bibcode per line. **Open**, **Reveal**, **Copy
  DOI**, **Copy Path**, and **Refresh ADS Records** also act on all selected
  rows. Refresh skips rows without a bibcode.
- **One-list CV templates.** Before: a template always split papers into
  First-authored and Co-authored sections. Now: `template.group: false` makes
  one Publications section in the original order.

### Fixed

- **Watch menu label.** Before: the menu showed "Automatically Identify Rename"
  without the ampersand. Some systems underlined the R. Now: the menu
  shows "Automatically Identify & Rename".

### Documentation

- **README screenshots.** Before: the README showed a version 0.2.1 screenshot
  without the welcome page or the round toolbar buttons. Now: the README shows
  the version 0.4.0 library window and the welcome page, and links to the
  first-run video.
- **Tutorial media.** Before: the guides had no screenshots or video. Now:
  "Start with your PDF library" opens with a 28-second first-run video. The guide
  also shows the welcome page, the subfolder question, the Identify prompt, the
  token window, and the library window. The application guide shows the token,
  profile, and **Software Update** windows.
- **Media script.** `uv run python scripts/capture_docs_media.py` makes all
  images and the video again from fictional sample data. The script uses a
  temporary home folder, so it does not read or change your settings, token,
  or library. The video needs `ffmpeg`. Use `--no-video` without it.

## 0.4.0 (2026-10-21)

### New

- **App Update button.** Before: update results showed only in **About** and in
  a startup notice. Now: a round **App Update** button in the toolbar opens
  **Software Update**.
  A blue dot on the button shows that a new version is available.
- **GitHub button.** Before: the main window had no link to the project page.
  Now: a round GitHub button next to **App Update** opens
  `https://github.com/ysBach/adsfolio` in your browser.
- **Software Update window.** Before: **View Update** opened the GitHub release
  page. Now: the window shows the installed version, the available version, and
  the changelog between them.
  - **Install** downloads the file for your system, checks its SHA-256 checksum,
    and opens it. ADSfolio does not replace the application.
  - **Skip This Version** hides the dot and the startup notice for that version.
- **Welcome page.** Before: a new installation showed empty panes and one
  button. Now: **Welcome to ADSfolio** shows three steps: add a PDF folder, add
  an ADS token (optional), and set a profile (optional). The page also lists the
  services that ADSfolio contacts.
- **Subfolder question.** Before: adding a folder with subfolders added only the
  top folder, with no message. Now: ADSfolio asks **Include Subfolders** or
  **This Folder Only**.
- **Test Token.** Before: **Save** stored the token with no check. Now:
  **Test Token** sends one search to ADS and shows "ADS accepted the token." or
  "ADS rejected the token." The dialog also shows the token source, for example
  `ADS_API_TOKEN` or a file path.

### Safer

- **Private token and profile files.** Before: other users on the computer could
  read `token` and `setup.json` for a short time during each save (mode `0644`
  with the usual umask). Now: ADSfolio creates both files with mode `0600`.
  When ADSfolio saves settings, it sets the settings folder to mode `0700`.
- **ORCID check.** Before: the profile saved `0000-0002-1825-0098`, which has a
  wrong check digit. Now: **Save** shows "invalid check digit" and keeps the
  dialog open. If you paste `https://orcid.org/0000-0002-1825-0097`, ADSfolio
  saves `0000-0002-1825-0097`.

### Fixed

- **Actions without a library.** Before: **Rename** and **Remove Root** were
  enabled when no library existed, and a click did nothing. Now: both stay
  disabled until you add a library.

## 0.3.0 (2026-10-21)

- The macOS application menu uses ADSfolio when launched from source.
  About displays the GitHub copyright handle.
- Shift-scroll uses smaller horizontal steps for mouse wheels and trackpads,
  including macOS wheel events with estimated pixel deltas. Header and scrollbar
  scrolling use the same speed. Small trackpad movements accumulate correctly.
- Identify offers a short explanation with OK to start. It checks stored bibcodes
  first and identifies the remaining papers. Adding a folder offers this prompt,
  and Needs ADS lookup has a direct action. Advanced options remain available.
  Lookup retains notes, locked fields, history, filenames, and file locations.
- ADS groups distinguish stored links from metadata readiness and PDF checks.
  Pending bibcodes can be linked from cache or batched ADS queries without
  changing existing metadata, filenames, or history.
- Equivalent Unicode filename spellings retain catalog identity when they refer
  to one directory entry. Distinct files and hardlinks remain separate.
- The AI CSV prompt requires exact identifiers and forbids invented bibcodes.
- The window stays idle after a job finishes. On macOS, the native job progress
  bar kept redrawing while hidden. Watched-folder checks examine each PDF with
  fewer filesystem calls.

- About ADSfolio shows the version, copyright, license links, and installation type.
  App details can be copied for bug reports.
- Help offers manual update checks and an optional check at startup.
  New releases appear without interrupting library work. Checks use public GitHub
  release information and never install updates or change library data.

## 0.2.1 (2026-10-19)

- Successful rename and identification notices use **OK** to acknowledge completion.

- Compact Details rows use field context menus and a resizable label column.
  Inline button preferences remain available outside compact mode.
- Libraries, Details, the job log, Find, Identify, and Rename have menu shortcuts.
  Column menus link directly to Appearance.
- Shorter ADS group labels include explanations of saved match status.

- A macOS source launcher can replace the installed app while retaining a backup.
  Reopening the app runs the checkout with its local environment.
- First-library guidance, vector icons, and gesture hints clarify common actions.
- Changed PDFs use a distinct warning marker until verification succeeds.
- Annotation review shows source and target PDFs side by side and saves a new copy.
  Originals remain unchanged. Unsupported annotations require an explicit decision.

- macOS builds include verified drag-to-install DMGs alongside ZIP archives.
- The README and website explain desktop setup and batch identification first.
- Verification views explain why a stored bibcode can need a new PDF check.
- Bibcode, arXiv, and filename cells open on double-click by default, so a click
  only selects the row. Appearance > Applications offers a persisted single-click
  option.

## 0.1.0 (2026-10-19)

- Windows file identifiers retain their full precision in the catalog.
  Invalid library paths are rejected before platform-specific path resolution.

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
