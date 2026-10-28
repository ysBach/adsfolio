# ADSfolio

A desktop app and Python toolkit that organizes your research PDFs with NASA
ADS / SciXplorer, Crossref, and DataCite metadata. ADSfolio is an independent
project, not an official ADS application.

![ADSfolio library window with illustrative sample data](https://raw.githubusercontent.com/ysBach/adsfolio/main/docs/_static/app-overview.png)

## Install

- **Desktop app:** download the file for your system from the
  [latest release](https://github.com/ysBach/adsfolio/releases/latest).
- **Homebrew on macOS:** `brew install --cask ysBach/adsfolio/adsfolio`
- **CLI and Python:** `pip install adsfolio`, or
  `pip install "adsfolio[full,gui]"` for the desktop app too.

The app is not signed. Your system can ask you to approve it the first time.
See [Getting started](https://ysbach.github.io/adsfolio/docs/getting-started.html#install)
for the steps.

## Daily workflows

- **Keep a BibTeX file for your paper:** export an ADS library as `.bib` for
  LaTeX or Overleaf.
- **Watch a folder:** ADSfolio identifies each new PDF and renames it when the
  PDF's own arXiv ID or DOI confirms the match.
- **Make your CV:** write your publication list in LaTeX, HTML, Markdown, or
  your own format.

See the [daily workflows](https://ysbach.github.io/adsfolio/docs/workflows.html)
with screenshots and a video.

## Documentation

- [Website](https://ysbach.github.io/adsfolio/)
- [Getting started](https://ysbach.github.io/adsfolio/docs/getting-started.html)
- [App GUI guide](https://ysbach.github.io/adsfolio/docs/gui.html)
- [CLI and Python](https://ysbach.github.io/adsfolio/docs/command-line-and-python.html)
  and the [API reference](https://ysbach.github.io/adsfolio/docs/api/index.html)
- [Changelog](https://github.com/ysBach/adsfolio/blob/main/CHANGELOG.md) and
  [issues](https://github.com/ysBach/adsfolio/issues)

## License

BSD-3-Clause. The desktop bundles include third-party components under their
own licenses. PyMuPDF uses AGPL terms. See
[Building the desktop app](https://ysbach.github.io/adsfolio/docs/desktop.html)
for details.
