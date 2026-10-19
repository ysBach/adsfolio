"""ADS BibTeX exports with unbalanced braces are repaired before saving."""

from __future__ import annotations

from typing import Any

import pytest

from adsfolio.api.export import ExportAPI
from adsfolio.bibtex.parser import parse_bibtex_entries
from adsfolio.bibtex.sanitize import brace_balanced, repair_bibtex, repair_field_text

IXION = r"""@ARTICLE{2004A&A...415L..21B,
       author = {{Boehnhardt}, H. and {Bagnulo}, S.},
        title = "{Surface characterization of 28978 Ixion (2001 KX$_{76}$)}",
      journal = {\aap},
     keywords = {transneptunian object, 28978 Ixion: 2001 KX\$\_\{76},
         year = 2004,
       adsnote = {Provided by the SAO/NASA Astrophysics Data System}
}

@ARTICLE{2004A&A...422L..39K,
       author = {{Kaasalainen}, M.},
         year = 2004
}
"""


def test_ixion_keyword_is_repaired_and_rest_untouched() -> None:
    fixed = repair_bibtex(IXION)
    assert "keywords = {transneptunian object, 28978 Ixion: 2001 KX$_{76}$}," in fixed
    assert brace_balanced(fixed)
    assert fixed.replace("KX$_{76}$},", r"KX\$\_\{76},") == IXION


def test_balanced_input_is_returned_unchanged() -> None:
    text = IXION.replace(r"KX\$\_\{76}", "KX$_{76}$")
    assert repair_bibtex(text) is text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"KX\$\_\{76", "KX$_{76}$"),
        (r"a \{b", "a {b}"),
        ("a } b", "a  b"),
        ("x^{2", "x^{2}"),
        ("balanced {x}", "balanced {x}"),
    ],
)
def test_repair_field_text(raw: str, expected: str) -> None:
    assert repair_field_text(raw) == expected
    assert brace_balanced(repair_field_text(raw))


def test_last_field_without_comma_and_crlf() -> None:
    text = "@MISC{k,\r\n  keywords = {a \\{b}\r\n}\r\n"
    assert repair_bibtex(text) == "@MISC{k,\r\n  keywords = {a {b}}\r\n}\r\n"


class Client:
    def __init__(self, reference: str) -> None:
        self.reference = reference

    def post(self, endpoint: str, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "export": self.reference,
            "num_docs": 1,
            "docs": [{"bibcode": data["bibcode"][0], "reference": self.reference}],
        }


@pytest.mark.parametrize("fmt", ["bibtex", "bibtexabs"])
def test_export_paths_repair_bibtex(fmt: str) -> None:
    api = ExportAPI(Client(IXION))
    assert "KX$_{76}$}" in api.export(["B"], format=fmt)
    assert "KX$_{76}$}" in api.export_complete(["B"], format=fmt)


def test_non_bibtex_formats_are_untouched() -> None:
    api = ExportAPI(Client(IXION))
    assert api.export(["B"], format="ris") == IXION
    assert api.export_complete(["B"], format="ris") == IXION


def test_parse_bibtex_entries_handles_nested_braces_macros_comments_and_raw_slices() -> (
    None
):
    single_line = "@article{k,\ttitle={A {nested} title}}\r\n"
    parsed_single = parse_bibtex_entries(single_line)
    assert len(parsed_single) == 1
    assert parsed_single[0].bibcode == "k"
    assert parsed_single[0].entry_type == "article"
    assert parsed_single[0].title == "A {nested} title"
    assert parsed_single[0].raw == "@article{k,\ttitle={A {nested} title}}"

    corporate_bib = (
        "@article{k,author={{Research, Development and Testing}},title={A title}}"
    )
    corporate_entries = parse_bibtex_entries(corporate_bib)
    assert len(corporate_entries) == 1
    assert corporate_entries[0].authors == ["Research, Development and Testing"]

    nested_corp_bib = "@article{k,author={{{NASA}, Planetary Group}},title={A title}}"
    nested_corp_entries = parse_bibtex_entries(nested_corp_bib)
    assert len(nested_corp_entries) == 1
    assert nested_corp_entries[0].authors == ["{NASA}, Planetary Group"]

    chained_macros = (
        "@string{a = missing}\n"
        '@string{b = a # "-suffix"}\n'
        "@string{cyc1 = cyc2}\n"
        "@string{cyc2 = cyc1}\n"
        "@article{k, title = a, journal = b, volume = cyc1}\n"
    )
    macro_entries = parse_bibtex_entries(chained_macros)
    assert len(macro_entries) == 1
    assert macro_entries[0].title == "missing"
    assert macro_entries[0].journal == "missing-suffix"
    assert macro_entries[0].volume == "cyc1"

    complex_bib = (
        "Implicit comment mentioning @article{fake, title={Ignored}}\n"
        "@comment{@article{commented, title={Also ignored}}}\n"
        '@string{josa = "Journal of the Optical Society of America"}\n'
        "@ARTICLE{1934JOSA...24..206W,\n"
        "  author = {{Wright}, F. E. and {Einstein}, A. and {Hanu{\\v s}}, J. and {SPHEREx Collaboration} and Jane {van der Waals}},\n"
        "  title = {An {Eyepiece} for\n"
        "    Measuring {Plane-Polarization}},\n"
        "  journal = josa,\n"
        "  year = 1934,\n"
        '  volume = "24",\n'
        "  pages = {206--215}\n"
        "}\n"
    )
    entries = parse_bibtex_entries(complex_bib)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.bibcode == "1934JOSA...24..206W"
    assert entry.entry_type == "article"
    assert entry.authors == [
        "Wright",
        "Einstein",
        "Hanu{\\v s}",
        "SPHEREx Collaboration",
        "van der Waals",
    ]
    assert "An {Eyepiece} for" in entry.title
    assert "Measuring {Plane-Polarization}" in entry.title
    assert entry.journal == "Journal of the Optical Society of America"
    assert entry.year == "1934"
    assert entry.volume == "24"
    assert entry.pages == "206--215"
    assert entry.raw.startswith("@ARTICLE{1934JOSA...24..206W,")
    assert entry.raw.endswith("}")
