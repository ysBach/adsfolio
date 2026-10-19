"""Shared arXiv identifier derivation for the GUI table and registry sorting."""

from pathlib import Path

import pytest

from adsfolio.config import Config
from adsfolio.gui.library import arxiv_id as gui_arxiv_id
from adsfolio.pdf.arxiv import arxiv_id
from adsfolio.pdf.registry import PdfRegistry


@pytest.fixture(autouse=True)
def _use_test_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"doi": "10.1006/icar.1998.6100"}, ""),
        ({"metadata": {"doi": ["10.1016/j.pss.2018.1234"]}}, ""),
        ({"metadata": {"identifier": ["10.1016/j.pss.2020.1234"]}}, ""),
        ({"identifier": ["doi:10.1016/j.pss.2020.1234"]}, ""),
        ({"doi": "110.48550/arXiv.2401.12345"}, ""),
        ({"doi": "https://doi.org/10.48550/arXiv.2401.12345v2"}, "2401.12345v2"),
        ({"identifier": "arXiv:astro-ph/0601001"}, "astro-ph/0601001"),
        ({"doi": "10.48550/arXiv.2401.12345v2"}, "2401.12345v2"),
        (
            {"metadata": {"identifier": ["doi:10.1/x", "arXiv:astro-ph/0601001"]}},
            "astro-ph/0601001",
        ),
        ({"bibcode": "2024arXiv240112345B"}, "2401.12345"),
        (
            {"metadata": {"arxiv_id": ["2501.00001"]}, "doi": "10.1006/icar.1998.6100"},
            "2501.00001",
        ),
    ],
)
def test_arxiv_id_accepts_only_arxiv_sources(record: dict, expected: str) -> None:
    assert arxiv_id(record) == expected


def test_gui_reexports_shared_arxiv_id() -> None:
    assert gui_arxiv_id is arxiv_id


def _add(registry: PdfRegistry, root: Path, name: str, **fields) -> None:
    registry.upsert_file(
        root=root,
        relative_path=name,
        current_path=name,
        sha256=name,
        status="resolved",
        **{"size_bytes": 1, "mtime_ns": 1, **fields},
    )


def test_registry_arxiv_sort_ignores_ordinary_dois_and_reads_identifiers(
    tmp_path: Path,
) -> None:
    registry = PdfRegistry(tmp_path)
    _add(registry, tmp_path, "icarus.pdf", metadata={"doi": "10.1006/icar.1998.6100"})
    _add(
        registry,
        tmp_path,
        "old.pdf",
        metadata={"identifier": ["arXiv:astro-ph/0601001"]},
    )
    _add(registry, tmp_path, "new.pdf", metadata={"doi": "10.48550/arXiv.2501.00001"})

    ordered = [r["current_path"] for r in registry.query_files(order_by="arxiv")]

    assert ordered == ["new.pdf", "old.pdf", "icarus.pdf"]
    registry.close()


def test_registry_sorts_size_and_mtime_numerically_with_zero(tmp_path: Path) -> None:
    registry = PdfRegistry(tmp_path)
    for name, size, mtime in [("a", 9, 100), ("b", 10, 20), ("c", 0, 3)]:
        _add(registry, tmp_path, f"{name}.pdf", size_bytes=size, mtime_ns=mtime)

    def order(key: str, descending: bool = False) -> list[str]:
        records = registry.query_files(order_by=key, descending=descending)
        return [r["current_path"] for r in records]

    assert order("size_bytes") == ["c.pdf", "a.pdf", "b.pdf"]
    assert order("size_bytes", True) == ["b.pdf", "a.pdf", "c.pdf"]
    assert order("mtime_ns") == ["c.pdf", "b.pdf", "a.pdf"]
    registry.close()


@pytest.mark.parametrize("descending", [False, True])
def test_numeric_sort_places_missing_values_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descending: bool
) -> None:
    registry = PdfRegistry(tmp_path)
    rows = [
        {"current_path": "none.pdf", "size_bytes": None},
        {"current_path": "zero.pdf", "size_bytes": 0},
        {"current_path": "text.pdf", "size_bytes": ""},
        {"current_path": "big.pdf", "size_bytes": 100},
    ]
    monkeypatch.setattr(registry, "_query_records", lambda **_: list(rows))

    ordered = [
        r["current_path"]
        for r in registry.query_files(order_by="size_bytes", descending=descending)
    ]

    expected = ["big.pdf", "zero.pdf"] if descending else ["zero.pdf", "big.pdf"]
    assert ordered == [*expected, "none.pdf", "text.pdf"]
    registry.close()
