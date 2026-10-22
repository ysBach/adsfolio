# ADSfolio

A desktop application and Python toolkit for organizing research PDFs with NASA
ADS / SciXplorer, Crossref, and DataCite metadata. ADSfolio is an independent
project, not an official ADS application.

![ADSfolio logo](https://raw.githubusercontent.com/ysBach/adsfolio/main/docs/_static/adsfolio.png)

## Start with the PDFs you already have

Add your existing PDF folder. Identify papers in a batch, inspect their metadata,
and preview useful filenames before you rename anything. You can keep your
current folder structure. You do not need to import PDFs into managed storage.

Identification checks saved results before online lookups. Lookup time depends on
PDF text quality, cache coverage, provider limits, and your connection. No fixed
speed claim applies to every collection. Start with one folder to check results.

![ADSfolio library window](https://raw.githubusercontent.com/ysBach/adsfolio/main/docs/_static/app-overview.png)

*Application screenshot with illustrative sample data. Version 0.5.0 shown.*

[Website](https://ysbach.github.io/adsfolio/) · [Getting started](https://ysbach.github.io/adsfolio/docs/getting-started.html) · [Application guide](https://ysbach.github.io/adsfolio/docs/gui.html)

## Daily workflows

- **Keep a BibTeX file for your paper.** Add the papers you cite to a NASA ADS
  library, then choose **Tools → Export ADS Library…** to save a `.bib` file for
  LaTeX or Overleaf. `adsfolio library watch` keeps that file current while you
  write.
- **Watch a folder and rename new papers.** Right-click a library and choose
  **Watch Current Library** and **Automatically Identify & Rename**. Each new PDF
  gets publication details and a filename from your Rename template.
- **Make your CV from your publications.** **Tools → CV Generator…** builds your
  publication list from NASA ADS in LaTeX, HTML, Markdown, plain text, or your
  own template. You choose how to highlight your name and how each entry looks.

[See the daily workflows with screenshots and a short video](https://ysbach.github.io/adsfolio/docs/workflows.html).

## Install the desktop app

Download [ADSfolio 0.5.1](https://github.com/ysBach/adsfolio/releases/latest).
macOS downloads include DMG installers and ZIP archives.
Desktop downloads include Python and the application dependencies. You do not
need a Python environment or an ADS token to browse your local library.

| System | Download and open |
| --- | --- |
| macOS 15+, Apple Silicon | Open `adsfolio-macos-arm64.dmg`, drag `ADSfolio.app` to Applications, then open it. |
| macOS 15+, Intel | Open `adsfolio-macos-x86_64.dmg`, drag `ADSfolio.app` to Applications, then open it. |
| Windows x86-64 | Extract `adsfolio-windows-x86_64.zip`, then open `adsfolio.exe` inside the extracted folder. |
| Linux x86-64 | Extract `adsfolio-linux-x86_64.tar.gz`, then run `adsfolio` inside the extracted folder. Requires glibc 2.39+ and system desktop libraries. |

Keep the complete extracted folder together on Windows and Linux.
See [Getting started](https://ysbach.github.io/adsfolio/docs/getting-started.html#install) for details.

### macOS with Homebrew

```sh
brew install --cask ysBach/adsfolio/adsfolio
open -a ADSfolio
```

**Version 0.5.1 is unsigned and not notarized.** macOS can block opening even
when Homebrew installation succeeds. After attempting to open ADSfolio, review
**System Settings → Privacy & Security → Open Anyway** if macOS offers that
option. Approve it only if you trust the release. Installation alone does not
establish that the app can open. Windows can also show publisher warnings.

### Start using your library

![ADSfolio welcome page with three setup steps](https://raw.githubusercontent.com/ysBach/adsfolio/main/docs/_static/welcome.png)

*Welcome page of a new installation. Version 0.5.0 shown.*

[Watch the 28-second first-run video](https://ysbach.github.io/adsfolio/docs/getting-started.html#watch-the-first-run).

1. Open ADSfolio. Choose **Add PDF Folder…** on the welcome page and select your
   PDF folder. If it has subfolders, choose **Include Subfolders** or
   **This Folder Only**.
2. Select a PDF to inspect or edit its metadata. Double-click a row to open it
   in your system PDF viewer.
3. Choose **OK** in the **Identify** prompt to look up publication metadata.
   The prompt also opens after adding a folder. Token setup opens if ADS needs it.
   **Advanced options…** changes the provider order. Crossref and DataCite can
   look up DOIs without an ADS token. Files keep their names and locations.
4. Preview **Rename** before applying filename changes. Papers with sufficient
   metadata can be renamed without an ADS bibcode. Use **File → Local References…**
   for manual entries and BibTeX import/export, including references without PDFs.
5. Press **Refresh** or **F5** after moving or deleting files outside ADSfolio.
   This reconciles local files. Use the separate ADS refresh actions to update
   remote metadata. Permanent cleanup remains a separate operation.

See the [App GUI guide](https://ysbach.github.io/adsfolio/docs/gui.html) for watching folders,
selection, metadata editing, notifications, and refresh options.

### Updates and existing libraries

Quit ADSfolio before replacing the app, or run `brew upgrade --cask adsfolio`.
The catalog, metadata, and history live in your user data directory, outside the
application bundle. Retain that directory when upgrading or uninstalling.

Earlier settings and catalogs are copied into ADSfolio storage on first use.
The source remains intact. Close the earlier application before migration and
keep its data until you check the migrated library. Saved automatic watch and
rename settings remain active, so review them before resuming library work.

## CLI and Python

ADSfolio also provides search, bibliography export, and scripted PDF workflows.
See the [CLI and Python workflows](https://ysbach.github.io/adsfolio/docs/command-line-and-python.html),
[CLI tutorials](https://ysbach.github.io/adsfolio/docs/cli-tutorials/index.html), and [API reference](https://ysbach.github.io/adsfolio/docs/api/index.html).
Version 0.5.1 and later are on PyPI and need Python 3.10 or newer:

```sh
pip install adsfolio                # CLI and Python client
pip install "adsfolio[full,gui]"    # also the desktop application (adsfolio-gui)
```

## License

BSD-3-Clause
