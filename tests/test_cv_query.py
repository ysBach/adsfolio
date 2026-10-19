from dataclasses import replace
from pathlib import Path

from adsfolio.cv.config import CVConfig
from adsfolio.cv.query import classify_publications, fetch_publications


def _cfg() -> CVConfig:
    return CVConfig(
        query='author:"doe, jane"',
        orcid="",
        use_orcid=False,
        debut_year=None,
        retire_year=None,
        exclude_bibstems=("AAS",),
        first_author_prefix="doe, j",
        me_target="Doe, J.",
        me_display="Jane Doe",
        output_format="latex",
        style="compact",
        n_max_authors=5,
        n_min_authors=3,
        n_me=6,
        author_sep="; ",
        me_fmt=r"\textbf{{{}}}",
        author_format="cv_default",
        markers={},
        status={},
        output_dir=Path("."),
        output_filename="publ.tex",
        journal_legend=True,
        latex_list_env="itemize",
        latex_left_margin="-14em",
        latex_item_sep="6pt",
    )


def test_fetch_publications_uses_query_all() -> None:
    docs = [{"bibcode": "A"}, {"bibcode": "B"}]
    called: dict[str, object] = {}

    class StubSearch:
        def query_all(self, q, fl, sort):  # noqa: ANN001
            called["q"] = q
            called["fl"] = fl
            called["sort"] = sort
            return type("Result", (), {"docs": docs})()

    class StubClient:
        search = StubSearch()

    result = fetch_publications(StubClient(), _cfg())
    assert result == docs
    assert 'author:"doe, jane"' in str(called["q"])
    assert "bibstem:(- AAS)" in str(called["q"])
    assert called["sort"] == "date desc"
    assert isinstance(called["fl"], list)


def test_apply_author_format_overrides_uses_ads_custom() -> None:
    cfg = replace(_cfg(), author_format="ads_custom:%l")
    docs = [{"bibcode": "2024ApJ...900..100S", "author": ["Doe, Jane"]}]

    class StubExport:
        def custom(self, bibcodes, format_string):  # noqa: ANN001
            assert bibcodes == ["2024ApJ...900..100S"]
            assert format_string == "%l"
            return "Doe, J.; Smith, A."

    class StubClient:
        export = StubExport()

    from adsfolio.cv.query import apply_author_format_overrides

    overridden = apply_author_format_overrides(docs, StubClient(), cfg)
    assert overridden[0]["_adsapi_author_text"] == "Doe, J.; Smith, A."


def test_classify_publications_splits_first_vs_co_author() -> None:
    docs = [
        {"first_author": ["Doe, Jane"], "bibcode": "A"},
        {"first_author": ["Smith, John"], "bibcode": "B"},
        {"first_author": "Doe, J", "bibcode": "C"},
    ]
    first_docs, co_docs = classify_publications(docs, "doe, j")

    assert [doc["bibcode"] for doc in first_docs] == ["A", "C"]
    assert [doc["bibcode"] for doc in co_docs] == ["B"]
