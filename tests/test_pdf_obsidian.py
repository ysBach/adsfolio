from unittest.mock import Mock

import pytest

from adsfolio.api.search import SearchResult
from adsfolio.client import ADSClient
from adsfolio.pdf.obsidian import NotePlan, ObsidianNoteWriter
from adsfolio.pdf.sidecar import Sidecar


class _SearchStub:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def bigquery(self, bibcodes, fl=None, rows=None):
        self.calls.append({"bibcodes": bibcodes, "fl": fl, "rows": rows})
        return self.results.get(
            tuple(bibcodes), SearchResult(num_found=0, start=0, docs=[])
        )


class _ClientStub:
    def __init__(self, results):
        self.search = _SearchStub(results)


def _sample_metadata(**overrides):
    base = {
        "bibcode": "2024ApJ...900..100S",
        "doi": "10.1234/example",
        "title": ["Paper Title"],
        "author": ["Smith, John", "Doe, Alice"],
        "year": "2024",
        "bibstem": ["ApJ"],
        "volume": "900",
        "page": ["100"],
        "pub": "The Astrophysical Journal",
    }
    base.update(overrides)
    return base


def test_render_note_includes_frontmatter_and_pdf_link(tmp_path):
    writer = ObsidianNoteWriter()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    metadata = _sample_metadata(abstract="An interesting result.")

    note = writer.render_note(metadata, pdf_path)

    assert "title: Paper Title" in note
    assert "bibcode: 2024ApJ...900..100S" in note
    assert (
        "[2024ApJ...900..100S](https://ui.adsabs.harvard.edu/abs/2024ApJ...900..100S/abstract)"
        in note
    )
    assert f"[Open PDF]({pdf_path.resolve().as_uri()})" in note
    assert "An interesting result." in note


def test_render_note_without_abstract_uses_placeholder(tmp_path):
    writer = ObsidianNoteWriter()
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")

    note = writer.render_note(_sample_metadata(), pdf_path)

    assert "_No abstract cached yet._" in note


def test_fetch_missing_abstracts_posts_bibcodes(tmp_path):
    bibcodes = ("2024ApJ...900..100S",)
    result = SearchResult(
        num_found=1,
        start=0,
        docs=[{"bibcode": ["2024ApJ...900..100S"], "abstract": "Fetched abstract."}],
    )
    client = _ClientStub({bibcodes: result})
    writer = ObsidianNoteWriter(client=client)

    abstracts = writer.fetch_missing_abstracts(["2024ApJ...900..100S"])

    assert abstracts == {"2024ApJ...900..100S": "Fetched abstract."}
    assert client.search.calls[0]["fl"] == ["bibcode", "abstract"]


def test_fetch_missing_abstracts_requires_client():
    writer = ObsidianNoteWriter()
    try:
        writer.fetch_missing_abstracts(["2024ApJ...900..100S"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_generate_dry_run_plans_without_writing(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="paper.pdf"),
    )

    writer = ObsidianNoteWriter()
    plans = writer.generate(tmp_path, sidecar, apply=False)

    assert plans == [
        NotePlan(
            "original.pdf",
            "paper.md",
            tmp_path / "paper.md",
            pdf_path,
            "create",
        )
    ]
    assert not (tmp_path / "paper.md").exists()


def test_generate_apply_writes_note_and_caches_abstract(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="paper.pdf"),
    )

    bibcodes = ("2024ApJ...900..100S",)
    result = SearchResult(
        num_found=1,
        start=0,
        docs=[{"bibcode": ["2024ApJ...900..100S"], "abstract": "Cached abstract."}],
    )
    client = _ClientStub({bibcodes: result})
    writer = ObsidianNoteWriter(client=client)

    plans = writer.generate(tmp_path, sidecar, apply=True)

    assert plans[0].action == "create"
    note_path = tmp_path / "paper.md"
    assert note_path.exists()
    assert "Cached abstract." in note_path.read_text(encoding="utf-8")
    assert sidecar.get_file("original.pdf")["abstract"] == "Cached abstract."

    reloaded = Sidecar(tmp_path)
    assert reloaded.get_file("original.pdf")["abstract"] == "Cached abstract."


def test_generate_skips_existing_note_unless_forced(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF")
    note_path = tmp_path / "paper.md"
    note_path.write_text("manual notes", encoding="utf-8")
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "original.pdf",
        _sample_metadata(current_filename="paper.pdf", abstract="Already cached."),
    )

    writer = ObsidianNoteWriter()
    plans = writer.generate(tmp_path, sidecar, apply=True)
    assert plans[0].action == "skip-exists"
    assert note_path.read_text(encoding="utf-8") == "manual notes"

    forced_plans = writer.generate(tmp_path, sidecar, apply=True, force=True)
    assert forced_plans[0].action == "update"
    assert "Already cached." in note_path.read_text(encoding="utf-8")


def test_generate_skips_unresolved_and_missing_file(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "unresolved.pdf",
        {"current_filename": "unresolved.pdf"},
    )
    sidecar.upsert_file(
        "missing.pdf",
        _sample_metadata(current_filename="missing.pdf"),
    )

    writer = ObsidianNoteWriter()
    plans = writer.generate(tmp_path, sidecar, apply=False)

    actions = {plan.original_filename: plan.action for plan in plans}
    assert actions["unresolved.pdf"] == "skip-unresolved"
    assert actions["missing.pdf"] == "skip-missing-file"


@pytest.mark.parametrize("count", [0, 2000, 2001, 4001])
def test_fetch_abstracts_posts_bounded_batches_without_long_urls(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    bibcodes = [f"2024Test..{index:09d}A" for index in range(count)]
    client = ADSClient(token="test-token")
    requests = []

    def post(
        url: str,
        *,
        data: str,
        params: dict[str, str | int],
        headers: dict[str, str],
        timeout: tuple[float, float],
    ) -> Mock:
        assert url.endswith("/search/bigquery")
        assert headers["Content-Type"] == "text/plain"
        assert params["q"] == "*:*"
        assert params["fl"] == "bibcode,abstract"
        lines = data.splitlines()
        assert lines[0] == "bibcode"
        batch = lines[1:]
        assert 1 <= len(batch) <= 2000
        assert params["rows"] == len(batch)
        requests.append(batch)
        return Mock(
            status_code=200,
            ok=True,
            headers={},
            json=lambda: {
                "response": {
                    "numFound": len(batch),
                    "start": 0,
                    "docs": [
                        {"bibcode": code, "abstract": f"Abstract for {code}"}
                        for code in batch
                    ],
                }
            },
        )

    monkeypatch.setattr(client._base._session, "post", post)
    monkeypatch.setattr(
        client._base._session,
        "get",
        Mock(side_effect=AssertionError("Use a POST body")),
    )
    try:
        writer = ObsidianNoteWriter(client=client)
        supplied = ["", *bibcodes, *(f" {code.lower()} " for code in bibcodes)]
        abstracts = writer.fetch_missing_abstracts(supplied)
        assert abstracts == {code: f"Abstract for {code}" for code in bibcodes}
        assert [code for batch in requests for code in batch] == bibcodes
        assert len(requests) == (count + 1999) // 2000
    finally:
        client.close()
