# Standalone desktop builds

The desktop bundle includes Python, PySide6/Qt, the CLI, and the `full`
optional dependencies. Recipients do not need Python, pip, or uv.
ADS queries still need an internet connection and the recipient's own API token.
PDF opening uses a system PDF viewer. OCR software is not bundled.

## Launch and setup

Version 0.3.0 provides DMG and ZIP downloads for macOS.

1. Download the package for your operating system and processor.
2. On macOS, open the DMG and drag `ADSfolio.app` onto Applications.
   Eject the image, then open ADSfolio from Applications. For a ZIP download, extract it and move `ADSfolio.app` to Applications. On Windows, extract the archive and open
   `adsfolio.exe` inside the extracted `adsfolio` folder. On Linux, launch the
   `adsfolio` executable inside the extracted `adsfolio` folder.
3. Start browsing without a token. For ADS services, configure your token using
   **Tools → NASA ADS Token…**. A token already supplied
   through `ADS_API_TOKEN` or an existing supported token file is reused.
4. Use **File → Add Library Root…** to choose a folder of PDFs. The existing
   **Tools → My Publication Profile…** dialog configures publication matching.

Keep the complete extracted folder together on Windows and Linux. Its helper
executable and `_internal` directory are required. On macOS those files are
inside the app. Replacing the app does not delete your configuration, cache,
or central catalog. These use the ADSfolio per-user directories. Earlier data is
copied on first use without deleting the source. Close the earlier application
before migrating. The bundled GUI and Python installation share the new data.
Do not run overlapping watchers against the same roots.

The token is stored in the existing per-user token file, not in the application
bundle or an operating-system keychain. `ADS_API_TOKEN` takes precedence over
the saved token. A saved token is not verified by making a network request.

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

These are build targets, not a claim that every corresponding desktop has
been tested. Windows desktop acceptance and minimum OS versions require
validation on those systems. Qt and Python can set a higher minimum OS
version than the application itself. The Linux build is compiled against Ubuntu 24.04 and requires glibc 2.39 or
newer alongside system desktop libraries. It will not run on older distributions
with older glibc versions or musl-based distributions such as Alpine.

WordNet is a build-time download for ISO4 abbreviation. The spec bundles this
explicit corpus and the ISO4 lookup tables. It does not copy arbitrary NLTK
data from the builder's home directory. No corpus download is needed at runtime.

The checked-in GitHub Actions workflow builds each target independently and
uploads archives on pull requests affecting the app, version tags, and manual
runs. For version tags (`v*`), the workflow validates exact tag-to-version
consistency, generates SHA-256 checksums, and publishes a GitHub Release
containing the desktop bundles, Python packages, and checksums (marked as
prerelease when the version tag is a prerelease or dev release). A green macOS run does
not validate Windows or Linux. PyInstaller is not a cross-compiler. See its
[platform notes](https://pyinstaller.org/en/stable/usage.html#supporting-multiple-platforms).

On Linux, install the desktop libraries listed in the workflow when missing.
Run from a terminal to see errors from the Qt platform loader.

## Bundle architecture and verification

The `adsfolio` executable launches the GUI without a console on Windows.
`adsfolio-cli` (or `adsfolio-cli.exe`) runs the same Click commands used by the
Python package. Both executables share the analyzed Python modules and dynamic
libraries. The GUI uses the console helper for Identify, Rename, and refresh
jobs, preserving streamed output, cancellation, working directories, and
argument boundaries without relying on an installed Python interpreter.

Identify normalizes the scan root before constructing catalog paths. Relative
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

On Windows run `dist/adsfolio-smoke/adsfolio-smoke.exe`. The smoke executable uses
temporary settings and checks first-launch cancellation, a visible Qt window,
PDF engines, offline ISO4 data, and actual output from the bundled CLI helper.
It makes no ADS requests. This checks the bundled modules and libraries.
Manual native desktop testing still covers OS integration and security prompts.
The macOS metadata check validates the actual `.app` executable and ensures the
GUI is not marked as background-only. This is separate from the frozen Qt test:
a working test executable cannot validate Finder's interpretation of the app.

Before distributing a build, launch it from the desktop on a clean account,
enter a token through the GUI, open a PDF, reveal it in the file manager, and
run Identify plus a Rename preview in a disposable library. Test paths with
spaces and non-ASCII characters. Do not use the presence of an executable or
a successful `--help` as evidence that those desktop operations work.

## Download and update

After a release is published, download the archive for your platform from
[GitHub Releases](https://github.com/ysBach/adsfolio/releases/latest). Compare its
SHA-256 digest with the release checksums. Quit ADSfolio before replacing the
application. Keep your catalog and configuration directories. Replacing the
application does not migrate or delete them. Prereleases are listed on the
[releases page](https://github.com/ysBach/adsfolio/releases).

## Archives and release status

The workflow produces macOS DMG and ZIP files, a Windows ZIP, and a Linux tar.gz.
DMGs contain the application and an Applications shortcut. Each image is verified
and mounted read-only during the build. DMGs do not provide code signing or notarization.
Version 0.1.0 has ZIP downloads only. Version 0.2.1 adds DMGs. macOS
archives use `ditto`. Linux archives use `tar` to preserve required symbolic
links. Ordinary ZIP creation can lose these links. See
[PyInstaller's distribution notes](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#requirements-imposed-by-symbolic-links-in-frozen-application).

Version 0.3.0 ships unsigned bundles. macOS Developer ID signing/notarization and
Windows publisher signing require release credentials and are not configured.
Downloaded builds may trigger operating-system security prompts. There is no
automatic updater or system installer. Native signing and broader installation and upgrade acceptance remain future work.

### Status and manual acceptance

The macOS bundle sets `LSBackgroundOnly` to `false` in `packaging/adsfolio.spec`
(the console helper would otherwise make the app background-only), and
`packaging/check_macos_bundle.py` checks the built app's metadata and
executable paths. The smoke executable cannot catch this problem.

Release workflows require native tests and frozen runtime checks on all four
build targets. These automated checks do not establish compatibility with every
desktop. Real ADS requests, PDF-viewer integration, Finder launch, and
clean-machine installation require separate manual acceptance.

The project license remains BSD-3-Clause. Bundled third-party components retain
their own licenses. The `full` extra includes PyMuPDF. Dependency metadata and license notices accompany the bundles. PyMuPDF uses
AGPL terms. The project license does not replace bundled component licenses.


## Homebrew on macOS

After a stable release and tap publication:

```sh
brew install --cask ysBach/adsfolio/adsfolio
brew upgrade --cask adsfolio
```

The cask selects the ARM64 or Intel archive, checks its SHA-256 digest, installs
`ADSfolio.app`, and adds the `adsfolio` CLI. It requires macOS 15 or later.
Uninstalling the cask retains configuration and catalog data. Homebrew does not
remove the need for code signing or OS acceptance testing. See
[Publishing releases](maintaining.md#publishing-releases) for tap setup and updates.

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

After editing source files, quit the app completely and reopen it.
Closing a window alone may not exit the application. Changes to dependencies
require another `uv sync --all-extras`. Startup does not synchronize dependencies.
Keep the checkout and its `.venv` at the same paths.

Startup errors appear in `~/Library/Logs/ADSfolio/source-launch.log`.
To restore the packaged app, quit ADSfolio. Move the source launcher out of
Applications, then move the backed-up `ADSfolio.app` into Applications.
A Homebrew upgrade can replace the source launcher with a packaged release.
Run the installer again if you want to return to the source launcher.
