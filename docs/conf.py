"""Sphinx configuration for adsfolio documentation."""

import sys
from pathlib import Path

from docutils import nodes
from sphinx.addnodes import pending_xref
from sphinx.application import Sphinx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

project = "ADSfolio"
author = "Yoonsoo P. Bach"
copyright = "2026, Yoonsoo P. Bach"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_click",
]

intersphinx_mapping = {
    "packaging": ("https://packaging.pypa.io/en/stable/", None),
    "python": ("https://docs.python.org/3", None),
    "click": ("https://click.palletsprojects.com/en/stable/", None),
    "qt": ("https://doc.qt.io/qtforpython-6/", None),
}

# Napoleon type fields ("str, optional", "bool, default=True") and shorthand
# type names are prose, not importable classes; keep -n strict for real refs.
nitpick_ignore_regex = [
    ("py:class", r"optional"),
    ("py:class", r"default=.*"),
    ("py:class", r"\{.*"),
    ("py:class", r"'.*"),
    ("py:class", r".*'\}?$"),
    ("py:class", r'"'),
    ("py:class", r"sequence"),
    ("py:class", r"mapping"),
    ("py:class", r"iterable"),
    ("py:class", r"iterator"),
    ("py:class", r"Path"),
    ("py:class", r"callable"),
    ("py:class", r"dataclass"),
]
nitpick_ignore = [
    ("py:class", "User's permission level"),
    ("py:class", "BibEntry"),
    ("py:obj", "adsfolio.bibtex.io.write_bibtex_file"),
    ("py:obj", "adsfolio.bibtex.parser.BibEntry"),
]

# adsfolio re-exports (e.g. ADSClient) make module-level refs ambiguous.
suppress_warnings = ["ref.python"]

autosummary_generate = True
autosummary_ignore_module_all = False
templates_path = ["_templates"]
autodoc_typehints = "description"
autodoc_typehints_format = "fully-qualified"
# Imported Qt classes have no Python source page for viewcode to generate.
viewcode_follow_imported_members = False
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}

autodoc_type_aliases = {
    "PdfCatalog": "adsfolio.pdf.catalog.PdfCatalog",
    "CentralPdfRegistry": "adsfolio.pdf.registry.CentralPdfRegistry",
    "CentralPdfRegistryState": "adsfolio.pdf.registry.CentralPdfRegistryState",
    "QWidget": "PySide6.QtWidgets.QWidget",
    "QObject": "PySide6.QtCore.QObject",
    "QAction": "PySide6.QtGui.QAction",
}
napoleon_type_aliases = autodoc_type_aliases

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
# Show the same menu on every page: top-level page titles only, so nothing
# expands or collapses on navigation. Section headings move to the "On this
# page" list that add_page_contents() inserts; API module pages are listed on
# the Python API page.
html_theme_options = {"navigation_depth": 1, "titles_only": True}
html_static_path = ["_static"]
# Fold the menu groups; "Desktop app" and the current page's group stay open.
html_js_files = ["nav-groups.js"]
html_css_files = ["nav-groups.css"]
html_logo = "_static/adsfolio.svg"
html_favicon = "_static/adsfolio.svg"


def expand_type_aliases(app: Sphinx, doctree: nodes.document) -> None:
    """Qualify shorthand type references before local/intersphinx resolution.

    Parameters
    ----------
    app : `Sphinx`
        Build application containing the configured type aliases.
    doctree : `nodes.document`
        Parsed page, including constructor annotations and docstring roles.
    """
    for reference in doctree.findall(pending_xref):
        if reference.get("refdomain") != "py":
            continue
        target = app.config.autodoc_type_aliases.get(reference.get("reftarget"))
        if target is not None:
            reference["reftarget"] = target


PAGE_CONTENTS = "```{contents} On this page\n:local:\n:depth: 1\n```\n"


def add_page_contents(app: Sphinx, docname: str, source: list[str]) -> None:
    """Insert an "On this page" list after the title of multi-section pages.

    The menu lists page titles only, so pages with two or more ``##``
    sections get their own section list. Generated API pages are skipped.
    """
    if docname.startswith("api/"):
        return
    text = source[0]
    lines = text.splitlines(keepends=True)
    fence = False
    sections = 0
    title = None
    for index, line in enumerate(lines):
        if line.startswith(("```", "~~~")):
            fence = not fence
        elif not fence and line.startswith("# ") and title is None:
            title = index
        elif not fence and line.startswith("## "):
            sections += 1
    if title is None or sections < 2:
        return
    lines.insert(title + 1, "\n" + PAGE_CONTENTS)
    source[0] = "".join(lines)


def setup(app: Sphinx) -> None:
    """Register reference normalization after autodoc has generated each page."""
    app.connect("doctree-read", expand_type_aliases)
    app.connect("source-read", add_page_contents)
