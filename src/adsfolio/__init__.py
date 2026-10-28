"""ADSfolio research library tools and NASA ADS client."""

from .client import ADSClient
from .config import Config
from .formatting import format_author_list_cv, format_title_latex
from .journal_utils import format_journal

__version__ = "0.7.1"
__all__ = [
    "ADSClient",
    "Config",
    "__version__",
    "format_author_list_cv",
    "format_journal",
    "format_title_latex",
]
