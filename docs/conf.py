"""Sphinx configuration for adsapi documentation."""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

project = "adsapi"
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
    "python": ("https://docs.python.org/3", None),
    "click": ("https://click.palletsprojects.com/en/stable/", None),
}

# Napoleon type fields ("str, optional", "bool, default=True") and shorthand
# type names are prose, not importable classes; keep -n strict for real refs.
nitpick_ignore_regex = [
    ("py:class", r"optional"),
    ("py:class", r"default=.*"),
    ("py:class", r"\{.*"),
    ("py:class", r"'.*"),
    ("py:class", r".*'\}?$"),
    ("py:class", r"sequence"),
    ("py:class", r"mapping"),
    ("py:class", r"iterable"),
    ("py:class", r"Path"),
    ("py:class", r"callable"),
    ("py:class", r"dataclass"),
]
nitpick_ignore = [
    ("py:class", "User's permission level"),
    ("py:class", "BibEntry"),
    ("py:obj", "adsapi.bibtex.io.write_bibtex_file"),
    ("py:obj", "adsapi.bibtex.parser.BibEntry"),
]

# adsapi re-exports (e.g. ADSClient) make module-level refs ambiguous.
suppress_warnings = ["ref.python"]

autosummary_generate = True
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
}

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
    "examples/cv_example_output/*.md",
]

myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
html_static_path = []
