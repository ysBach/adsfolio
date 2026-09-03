from pathlib import Path

import pytest

from adsapi.cv.config import build_query, load_config


def test_load_config_reads_nested_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text(
        """
author:
  query: 'author:"doe, jane" year:2020-'
  orcid: "0000-0002-1825-0097"
  use_orcid: true
  debut_year: 2011
  retire_year: 2025
  exclude_bibstems: ["AAS", "zndo"]
  first_author_prefix: "doe, j"
  me_target: "Doe, J."
  me_display: "Jane Doe"
format:
  output: markdown
  style: detailed
  n_max_authors: 7
  n_min_authors: 2
  n_me: 4
  author_sep: " | "
  me_fmt: "**{}**"
  author_format: "ads_custom:%3.2l"
markers:
  highlight:
    symbol: "[H]"
    description: "Selected"
    bibcodes: ["2024ApJ...900..100S"]
status:
  "2024ApJ...900..100S": "ACCEPTED"
output:
  dir: out
  filename: pubs.md
  journal_legend: false
  latex:
    list_env: enumerate
    left_margin: "-8em"
    item_sep: "3pt"
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.query == 'author:"doe, jane" year:2020-'
    assert cfg.orcid == "0000-0002-1825-0097"
    assert cfg.use_orcid is True
    assert cfg.debut_year == 2011
    assert cfg.retire_year == 2025
    assert cfg.exclude_bibstems == ("AAS", "zndo")
    assert cfg.first_author_prefix == "doe, j"
    assert cfg.output_format == "markdown"
    assert cfg.style == "detailed"
    assert cfg.n_max_authors == 7
    assert cfg.n_min_authors == 2
    assert cfg.n_me == 4
    assert cfg.author_sep == " | "
    assert cfg.author_format == "ads_custom:%3.2l"
    assert cfg.markers["highlight"].symbol == "[H]"
    assert cfg.status["2024ApJ...900..100S"] == "ACCEPTED"
    assert cfg.output_dir == (tmp_path / "out").resolve()
    assert cfg.output_filename == "pubs.md"
    assert cfg.journal_legend is False
    assert cfg.latex_list_env == "enumerate"
    assert cfg.latex_left_margin == "-8em"
    assert cfg.latex_item_sep == "3pt"


def test_load_config_defaults_optional_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text(
        """
author:
  query: 'author:"doe, jane" year:2020-'
""",
        encoding="utf-8",
    )

    cfg = load_config(config_path)

    assert cfg.output_format == "latex"
    assert cfg.style == "compact"
    assert cfg.author_format == "cv_default"
    assert cfg.use_orcid is False
    assert cfg.orcid == ""
    assert cfg.output_filename == "publ.tex"
    assert cfg.output_dir == tmp_path
    assert "AAS" in cfg.exclude_bibstems


def test_build_query_adds_exclude_bibstems(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text(
        """
author:
  query: 'author:"doe, jane"'
  exclude_bibstems: ["AAS", "zndo"]
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path)

    query = build_query(cfg)
    assert '(author:"doe, jane")' in query
    assert "(bibstem:(- AAS))" in query
    assert "(bibstem:(- zndo))" in query


def test_build_query_uses_orcid_and_year_range(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text(
        """
author:
  query: "*:*"
  orcid: "0000-0002-1825-0097"
  use_orcid: true
  debut_year: 2010
  retire_year: 2020
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    query = build_query(cfg)
    assert '(orcid_pub:"0000-0002-1825-0097")' in query
    assert "(year:[2010 TO 2020])" in query


def test_load_config_defaults_use_orcid_true_when_orcid_exists(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text(
        """
author:
  query: "*:*"
  orcid: "0000-0002-1825-0097"
""",
        encoding="utf-8",
    )
    cfg = load_config(config_path)
    assert cfg.use_orcid is True


def test_load_config_raises_on_malformed_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "cv.yaml"
    config_path.write_text("author: [", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed YAML"):
        load_config(config_path)
