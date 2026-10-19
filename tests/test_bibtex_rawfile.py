"""Summary-file and text-file helpers for BibTeX workflows."""

from pathlib import Path

from adsfolio.bibtex import make_rawfile
from adsfolio.bibtex.io import write_bibtex_file


def test_make_rawfile_reads_single_line_nested_and_quoted_entries(tmp_path: Path):
    text = (
        '@article{2020ApJ...900..100S, author = "{Smith}, A. and {Lee}, B.", '
        'title = "{A {nested} title}"}\n'
        "@article{2021MNRAS.501..123A,\n"
        "  author = {{Al\\'i}, C. and {Doe}, J. and {Roe}, K. and {Poe}, L.},\n"
        "  title = {Multi\n    line}\n}\n"
        "@article{shortkey, author = {{Skip}, S.}, title = {Skipped}}\n"
    )
    output = tmp_path / "raw.txt"

    make_rawfile(text, output)

    assert output.read_text(encoding="utf-8").split("\n") == [
        "2020ApJ...900..100S  # Smith+Lee || A {nested} title",
        "2021MNRAS.501..123A  # Al\\'i+3 || Multi line",
    ]


def test_make_rawfile_handles_closing_brace_without_entry(tmp_path: Path):
    output = tmp_path / "raw.txt"

    make_rawfile("}\n", output)

    assert output.read_text(encoding="utf-8") == ""


def test_write_bibtex_file_appends_utf8(tmp_path: Path):
    path = tmp_path / "out" / "refs.bib"

    write_bibtex_file("@misc{a, title={é}}\n", path)
    write_bibtex_file("@misc{b, title={ü}}\n", path, append=True)

    assert path.read_text(encoding="utf-8").count("@misc") == 2
