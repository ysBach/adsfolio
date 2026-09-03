"""
adsapi - Comprehensive Python client for NASA ADS API

A modular package for querying NASA ADS (Astrophysics Data System) with
full API coverage and CLI tools.
"""

from .client import ADSClient
from .config import Config
from .formatting import format_author_list_cv, format_title_latex
from .journal_utils import format_journal

__version__ = "0.1.0.dev"
__all__ = [
    "ADSClient",
    "Config",
    "format_author_list_cv",
    "format_title_latex",
    "format_journal",
    "__version__",
]
