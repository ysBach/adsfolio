import json
import re
import warnings
from typing import Literal

try:
    import requests
except ImportError:
    requests = None

from .config import Config

__all__ = ["format_journal", "JOURNAL_MACRO"]

JOURNAL_ABBREV_URL = "https://adsabs.harvard.edu/abs_doc/journals1.html"
CACHE_FILENAME = "journal_abbrevs.json"


# LaTeX macros for common astronomy journals
JOURNAL_MACRO = {
    "aas": "American Astronomical Society Meeting Abstracts",
    "aj": "Astronomical Journal",
    "actaa": "Acta Astronomica",
    "araa": "Annual Review of Astron and Astrophysis",
    "apjl": "Astrophysical Journal, Letters",
    "apjs": "Astrophysical Journal, Supplement",
    "apj": "Astrophysical Journal",
    "ao": "Applied Optics",
    "apss": "Astrophysics and Space Science",
    "aapr": "Astronomy and Astrophysics Reviews",
    "aaps": "Astronomy and Astrophysics, Supplement",
    "aap": "Astronomy and Astrophysics",
    "aplett": "Astrophysics Letters",
    "apspr": "Astrophysics Space Physics Research",
    "azh": "Astronomicheskii Zhurnal",
    "baas": "Bulletin of the American Astronomical Society",
    "bac": "Bulletin of the Astronomical Institutes of Czechoslovakia",
    "bain": "Bulletin Astronomical Institute of the Netherlands",
    "caa": "Chinese Astronomy and Astrophysics",
    "cjaa": "Chinese Journal of Astronomy and Astrophysics",
    "dps": "American Astronomical Society/Division for Planetary Sciences Meeting Abstracts",
    "fcp": "Fundamental Cosmic Physics",
    "gca": "Geochimica Cosmochimica Acta",
    "grl": "Geophysics Research Letters",
    "iaucirc": "International Astronomical Union Cirulars",
    "icarus": "Icarus",
    "jaavso": "Journal of the American Association of Variable Star Observers",
    "jcap": "Journal of Cosmology and Astroparticle Physics",
    "jcp": "Journal of Chemical Physics",
    "jgr": "Journal of Geophysics Research",
    "jqsrt": "Journal of Quantitiative Spectroscopy and Radiative Transfer",
    "jrasc": "Journal of the Royal Astronomical Society of Canada",
    "maps": "Meteoritics and Planetary Science",
    "memras": "Memoirs of the Royal Astronomical Society",
    "memsai": "Mem. Societa Astronomica Italiana",
    "mnras": "Monthly Notices of the Royal Astronomical Society",
    "nat": "Nature",
    "nar": "New Astronomy Review",
    "na": "New Astronomy",
    "nphysa": "Nuclear Physics A",
    "pasa": "Publications of the Astronomical Society of Australia",
    "pasp": "Publications of the Astronomical Society of the Pacific",
    "pasj": "Publications of the Astronomical Society of Japan",
    "physrep": "Physics Reports",
    "physscr": "Physica Scripta",
    "planss": "Planetary Space Science",
    "pra": "Physical Review A",
    "prb": "Physical Review B",
    "prc": "Physical Review C",
    "prd": "Physical Review D",
    "pre": "Physical Review E",
    "prl": "Physical Review Letters",
    "procspie": "Proceedings of the Society of Photo-Optical Instrumentation Engineers",
    "psj": "Planetary Science Journal",
    "qjras": "Quarterly Journal of the Royal Astronomical Society",
    "rmxaa": "Revista Mexicana de Astronomia y Astrofisica",
    "skytel": "Sky and Telescope",
    "solphys": "Solar Physics",
    "sovast": "Soviet Astronomy",
    "ssr": "Space Science Reviews",
    "zap": "Zeitschrift fuer Astrophysik",
}

# Create reverse mapping for ADS formatting (Title -> Macro)
TITLE_TO_MACRO = {v: f"\\{k}" for k, v in JOURNAL_MACRO.items()}

MANUAL_OVERRIDES = {
    "ApJL": {
        "full": "The Astrophysical Journal Letters",
        "iso4": "Astrophys. J. Lett.",
    },
}


def _normalize_name(name: str) -> str:
    """Normalize journal name for fuzzy matching (lowercase, no punctuation/spaces)."""
    if name.lower().startswith("the "):
        name = name[4:]
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _get_journal_map() -> dict[str, dict[str, str]]:
    """
    Load journal abbreviation map from cache or fetch from ADS.
    Returns dict mapping {bibstem: {"full": full_name, "iso4": iso4_name}}.
    """
    cache_path = Config.get_config_dir() / CACHE_FILENAME

    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            # Check format: simple dict (old) vs nested dict (new)
            if data and isinstance(next(iter(data.values())), str):
                warnings.warn("Old journal cache format detected. Re-fetching.")
            else:
                data.update(MANUAL_OVERRIDES)
                return data
        except (OSError, json.JSONDecodeError) as e:
            warnings.warn(f"Failed to read journal cache: {e}. Re-fetching.")

    if requests is None:
        warnings.warn(
            "Requests library not installed. Cannot fetch journal abbreviations."
        )
        return {}

    try:
        response = requests.get(JOURNAL_ABBREV_URL, timeout=10)
        response.raise_for_status()
        content = response.text
    except requests.RequestException as e:
        warnings.warn(f"Failed to fetch journal abbreviations: {e}")
        return {}

    # Parse HTML
    # Pattern: <a href="#" onClick="setfields('ApJ..');">    ApJ      </a>    Astrophysical Journal
    pattern = r"onClick=\"setfields\('([^']+)'\);\">.*?</a>\s*(.+?)(?:\r|\n|<)"
    matches = re.findall(pattern, content)

    # Prepare ISO4 function (not used for eager computation anymore, but kept if we want to enable it later)
    # Actually, we skip eager computation to avoid 2-minute startup delay.

    journal_map = {}
    for stem, name in matches:
        stem = stem.strip()
        full_name = name.strip()

        # Initialize with None for iso4 to compute on demand
        entry = {"full": full_name, "iso4": None}
        journal_map[stem] = entry

    journal_map.update(MANUAL_OVERRIDES)

    # Save to cache
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(journal_map, indent=2), encoding="utf-8")
    except OSError as e:
        warnings.warn(f"Failed to save journal cache: {e}")

    return journal_map


def format_journal(
    journal: str, fmt: Literal["ads", "full", "iso4"] = "ads", latex: bool = True
) -> str:
    """
    Format a journal name.

    Escapes special characters, but preserves AASTeX macros (e.g., \\apj)
    if the string starts with a backslash and contains no spaces.

    Parameters
    ----------
    journal : str
        Journal name, bibstem (e.g. 'ApJ'), or macro (e.g. '\\apj').
    fmt : {'ads', 'full', 'iso4'}, default='ads'
        - 'ads': Use AASTeX macros if available (e.g. \\apj), otherwise full name.
        - 'full': Full journal name (e.g. Astrophysical Journal).
        - 'iso4': ISO 4 abbreviation (e.g. Astrophys. J.).
    latex : bool, default=True
        Escape special characters for LaTeX output.
    """
    if not journal:
        return ""

    # 1. Resolve to Full Name
    full_name = journal.strip()

    # Clean up input if it's a macro
    # Remove backslash for lookup
    if full_name.startswith("\\") and full_name[1:] in JOURNAL_MACRO:
        full_name = JOURNAL_MACRO[full_name[1:]]

    # If input is a bibstem (e.g. ApJ, ApJ..), resolve using scraper map
    journal_map = _get_journal_map()

    # Check if we have an entry for this journal (assuming input is bibstem)
    journal_entry = None

    # Try exact match
    if full_name in journal_map:
        journal_entry = journal_map[full_name]
    else:
        # 1. Try padded stem (e.g. AJ -> AJ...)
        # ADS bibstems are typically 5 characters (padded with dots)
        if len(full_name) < 10:
            padded = full_name.ljust(5, ".")
            if padded in journal_map:
                journal_entry = journal_map[padded]

            if not journal_entry:
                stripped = full_name.rstrip(".")
                if stripped in journal_map:
                    journal_entry = journal_map[stripped]

        # 2. Try Reverse Lookup (Full Name -> Entry)
        # If input was a macro (e.g. \aj), full_name is "Astronomical Journal"
        # But cache key is "AJ..." and value has "The Astronomical Journal"
        if not journal_entry:
            for entry in journal_map.values():
                # Check matches with normalization
                normalized_entry = _normalize_name(entry.get("full", ""))
                normalized_input = _normalize_name(full_name)

                if normalized_entry == normalized_input:
                    journal_entry = entry
                    break

    # Update full_name if entry found (canonical name)
    if journal_entry:
        full_name = journal_entry["full"]

    # 2. Apply Format
    result = full_name

    if fmt == "full":
        result = full_name

    elif fmt == "iso4":
        # Use cached ISO4 if available
        if journal_entry and journal_entry.get("iso4"):
            result = journal_entry["iso4"]
        else:
            # Fallback to runtime calculation
            try:
                from iso4 import abbreviate

                result = abbreviate(full_name, periods=True)
            except ImportError:
                warnings.warn(
                    "iso4 package not installed. Install adsfolio[full]. Returning full name."
                )
                result = full_name

    elif fmt == "ads":
        # Check if we have a macro for this full name
        if full_name in TITLE_TO_MACRO:
            result = TITLE_TO_MACRO[full_name]
        elif full_name.startswith("The ") and full_name[4:] in TITLE_TO_MACRO:
            result = TITLE_TO_MACRO[full_name[4:]]

    # 3. Latex Escaping
    # If result is a macro (starts with \ and no space), don't escape
    if latex:
        is_macro = result.startswith("\\") and " " not in result
        if not is_macro:
            from .formatting import html_to_latex

            result = html_to_latex(result)

    return result
