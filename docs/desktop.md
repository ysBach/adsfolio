# Building the desktop app

This page is for maintainers. To install the app, see
[Getting started](getting-started.md#install).

The desktop bundle includes Python, PySide6/Qt, the CLI, and the `full`
optional dependencies. Recipients do not need Python, pip, or uv.
ADS queries still need an internet connection and the recipient's own API token.
The app opens PDFs with a system PDF viewer. The bundle does not include OCR
software.

## Build on each target operating system

Use a separate project `.venv`. Never synchronize a shared scientific
environment to build this application.

```sh
uv sync --locked --python 3.13 --extra gui --extra full --group desktop
uv run --no-sync python -m nltk.downloader -d build/nltk_data wordnet
uv run --no-sync pyinstaller --noconfirm --clean packaging/adsfolio.spec
```

| Native build host | Result | Build-check target |
| --- | --- | --- |
| Apple Silicon macOS | `dist/ADSfolio.app` | macOS 15, ARM64 |
| Intel macOS | `dist/ADSfolio.app` | macOS 15, x86-64 |
| Windows | `dist/adsfolio/adsfolio.exe` | Windows Server 2022 runner, x86-64 |
| Linux | `dist/adsfolio/adsfolio` | Ubuntu 24.04, x86-64 |

These are build targets. They are not a claim that testing covered every
corresponding desktop. Windows desktop acceptance and minimum OS versions
require validation on those systems. Qt and Python can set a higher minimum OS
version than the application itself.

The Linux build compiles against Ubuntu 24.04. It requires glibc 2.39 or newer,
and it also requires system desktop libraries. It will not run on older
distributions with older glibc versions or musl-based distributions such as
Alpine.

WordNet is a build-time download for ISO4 abbreviation. The spec bundles this
explicit corpus and the ISO4 lookup tables. It does not copy arbitrary NLTK
data from the builder's home directory. The app needs no corpus download at
runtime.

The checked-in GitHub Actions workflow builds each target independently. It
uploads archives for pull requests that affect the app, for version tags, and
for manual runs. For version tags (`v*`), the workflow does these steps:

1. It checks that the tag and the package version match exactly.
2. It generates SHA-256 checksums.
3. It publishes a GitHub Release with the desktop bundles, Python packages, and
   checksums.

The workflow marks the release as a prerelease when the version tag is a
prerelease or dev release. A green macOS run does not validate Windows or
Linux. PyInstaller is not a cross-compiler. See its
[platform notes](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-platforms).

On Linux, install any missing desktop libraries that the workflow lists. To see
errors from the Qt platform loader, run the app from a terminal.

## Bundle architecture and verification

The `adsfolio` executable starts the GUI without a console on Windows.
`adsfolio-cli` (or `adsfolio-cli.exe`) runs the same Click commands that the
Python package uses. Both executables share the analyzed Python modules and
dynamic libraries.

The GUI uses the console helper for Identify, Rename, and refresh jobs. This
design preserves streamed output, cancellation, working directories, and
argument boundaries. It does not rely on an installed Python interpreter.

Identify normalizes the scan root before it constructs catalog paths. Relative
roots and directory symlink aliases therefore use the same root as the catalog,
including macOS's `/var` and `/private/var` aliases.

```sh
# Source tests, including real Qt subprocess output
QT_QPA_PLATFORM=offscreen uv run --no-sync pytest -q

# macOS frozen CLI; use dist/adsfolio/adsfolio-cli[.exe] on other systems
dist/ADSfolio.app/Contents/MacOS/adsfolio-cli --version
dist/ADSfolio.app/Contents/MacOS/adsfolio-cli pdf identify --help
uv run --no-sync python packaging/check_macos_bundle.py dist/ADSfolio.app

# Build a separate frozen test executable, excluded from the app archive
uv run --no-sync pyinstaller --noconfirm packaging/adsfolio.spec -- --with-smoke-test
QT_QPA_PLATFORM=offscreen dist/adsfolio-smoke/adsfolio-smoke
```

On Windows, run `dist/adsfolio-smoke/adsfolio-smoke.exe`. The smoke executable
uses temporary settings. It checks these items:

- first-launch cancellation
- a visible Qt window
- PDF engines
- offline ISO4 data
- actual output from the bundled CLI helper

The smoke executable makes no ADS requests. This checks the bundled modules and
libraries. Manual tests on native desktops still cover OS integration and
security prompts.

The macOS metadata check validates the actual `.app` executable. It also makes
sure that the metadata does not mark the GUI as background-only. This check is
separate from the frozen Qt test. A working test executable cannot validate how
Finder interprets the app.

Before you distribute a build, do these steps:

1. Launch the build from the desktop on a clean account.
2. Enter a token through the GUI.
3. Open a PDF.
4. Reveal the PDF in the file manager.
5. In a disposable library, run Identify plus a Rename preview.

Test paths with spaces and non-ASCII characters. Do not use the presence of an
executable or a successful `--help` as evidence that those desktop operations
work.

## Archives and release status

The workflow produces macOS DMG and ZIP files, a Windows ZIP, and a Linux tar.gz.
DMGs contain the application and an Applications shortcut. During the build, the
workflow verifies each image and mounts it read-only. DMGs do not provide code
signing or notarization.

Version 0.1.0 has ZIP downloads only. Version 0.2.0 adds DMGs. macOS archives
use `ditto`. Linux archives use `tar` to preserve required symbolic links.
Ordinary ZIP creation can lose these links. See
[PyInstaller's distribution notes](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#requirements-imposed-by-symbolic-links-in-frozen-application).

Version 0.6.0 ships unsigned bundles. macOS Developer ID signing/notarization and
Windows publisher signing require release credentials and are not configured.
Downloaded builds may trigger operating-system security prompts.

There is no automatic updater that replaces the app, and there is no system
installer. The App Update button downloads a release, verifies its SHA-256
checksum, and opens it. It replaces nothing automatically. Native signing and
broader installation and upgrade acceptance remain future work.

### Status and manual acceptance

The macOS bundle sets `LSBackgroundOnly` to `false` in `packaging/adsfolio.spec`.
Without this setting, the console helper would make the app background-only.
`packaging/check_macos_bundle.py` checks the built app's metadata and executable
paths. The smoke executable cannot catch this problem.

Release workflows require native tests and frozen runtime checks on all four
build targets. These automated checks do not establish compatibility with every
desktop. Real ADS requests, PDF-viewer integration, Finder launch, and
clean-machine installation require separate manual acceptance.

The project license remains BSD-3-Clause. Bundled third-party components retain
their own licenses. The `full` extra includes PyMuPDF. PyMuPDF uses AGPL terms.
Dependency metadata and license notices accompany the bundles. The project
license does not replace bundled component licenses.

## Homebrew on macOS

After a stable release, publish the cask to the tap with
`scripts/publish_tap.py`. The release workflow generates the cask. See
[Publishing releases](maintaining.md#publishing-releases).

The cask selects the ARM64 or Intel archive, checks its SHA-256 digest, installs
`ADSfolio.app`, and adds the `adsfolio` CLI. It requires macOS 15 or later.
Uninstalling the cask keeps configuration and catalog data.

## Run the latest local source on macOS

A packaged app contains a fixed copy of the code. Editing a checkout does not
update that copy. A source launcher runs your checkout when you open the app.

1. Quit ADSfolio before switching launchers.
2. In the checkout, run `uv sync --all-extras` to prepare its local `.venv`.
3. Run `uv run --no-sync python scripts/install_source_app.py`.
4. Open `/Applications/ADSfolio.app`. Its window title shows **ADSfolio (Development)**.

The installer backs up the previous app under
`~/Library/Application Support/ADSfolio Backups/`. It prints the exact path.
The launcher uses the existing ADSfolio configuration, metadata, and history.
It does not copy or delete library data. Do not run both apps against the same
watched folders at once.

After you edit source files, quit the app completely. Then reopen it. Closing a
window alone may not exit the application.

After you change dependencies, run `uv sync --all-extras` again. Startup does
not synchronize dependencies. Keep the checkout and its `.venv` at the same
paths.

Startup errors appear in `~/Library/Logs/ADSfolio/source-launch.log`.

To restore the packaged app:

1. Quit ADSfolio.
2. Move the source launcher from Applications to another folder.
3. Move the backed-up `ADSfolio.app` into Applications.

A Homebrew upgrade can replace the source launcher with a packaged release.
Run the installer again if you want to return to the source launcher.
