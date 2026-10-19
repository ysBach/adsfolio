from pathlib import Path

from click.testing import CliRunner

from adsfolio.cli.commands import main
from adsfolio.cv.cli import _query_default, cv_group
from adsfolio.cv.config import CVConfig
from adsfolio.user_profile import UserProfile


def _cfg(tmp_path: Path) -> CVConfig:
    return CVConfig(
        query='author:"doe, jane"',
        orcid="",
        use_orcid=False,
        debut_year=None,
        retire_year=None,
        exclude_bibstems=(),
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
        output_dir=tmp_path,
        output_filename="publ.tex",
        journal_legend=True,
        latex_list_env="itemize",
        latex_left_margin="-14em",
        latex_item_sep="6pt",
    )


def test_profile_default_query_escapes_names_and_requires_identity_for_years():
    assert (
        _query_default(
            ['Doe, A. "Quoted"'],
            2010,
            2030,
            fallback="fallback",
        )
        == 'author:"Doe, A. \\"Quoted\\"" AND year:[2010 TO 2030]'
    )
    assert _query_default([], 2010, 2030, fallback="fallback") == "fallback"


def test_main_registers_cv_group() -> None:
    assert "cv" in main.commands


def test_generate_dry_run_prints_rendered_output(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("adsfolio.cv.cli.load_config", lambda path: cfg)
    monkeypatch.setattr("adsfolio.cv.cli.ADSClient", lambda token=None: object())
    monkeypatch.setattr(
        "adsfolio.cv.cli.generate_cv",
        lambda client, cfg, **kwargs: "rendered output",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--token",
            "abc",
            "cv",
            "generate",
            "--config",
            str(tmp_path / "cv.yaml"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "rendered output" in result.output


def test_generate_non_dry_run_reports_output_path(tmp_path: Path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("adsfolio.cv.cli.load_config", lambda path: cfg)
    monkeypatch.setattr("adsfolio.cv.cli.ADSClient", lambda token=None: object())

    def fake_generate(client, config, **kwargs):  # noqa: ANN001
        output_path = kwargs.get("output_path") or config.output_path
        output_path.write_text("x", encoding="utf-8")
        return "x"

    monkeypatch.setattr("adsfolio.cv.cli.generate_cv", fake_generate)

    runner = CliRunner()
    result = runner.invoke(
        cv_group,
        ["generate", "--config", str(tmp_path / "cv.yaml")],
    )
    assert result.exit_code == 0
    assert f"Wrote CV output to: {cfg.output_path}" in result.output


def test_init_creates_yaml_file(tmp_path: Path) -> None:
    output_path = tmp_path / "cv.yaml"
    runner = CliRunner()
    result = runner.invoke(
        cv_group,
        ["init", "--output", str(output_path)],
        input="\n\n\n\n\n",
    )
    assert result.exit_code == 0
    assert output_path.exists()
    text = output_path.read_text(encoding="utf-8")
    assert "author:" in text
    assert "format:" in text
    assert "markers:" in text


def test_init_uses_adsapi_set_orcid_defaults(tmp_path: Path, monkeypatch) -> None:
    output_path = tmp_path / "cv.yaml"
    monkeypatch.setattr(
        "adsfolio.cv.cli.Config.load_user_profile",
        lambda: UserProfile(
            orcid="0000-0002-1825-0097",
            author_names=("Bach, Y. P.", "Bach, Yoonsoo"),
            first_publication_year=2010,
            last_publication_year=2030,
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        cv_group,
        ["init", "--output", str(output_path)],
        input="\n\n\n\n\n",
    )
    assert result.exit_code == 0
    text = output_path.read_text(encoding="utf-8")
    assert "orcid: 0000-0002-1825-0097" in text
    assert "use_orcid: true" in text
    assert "debut_year: 2010" in text
    assert "retire_year: 2030" in text


def test_init_no_orcid_disables_orcid_default(tmp_path: Path, monkeypatch) -> None:
    output_path = tmp_path / "cv.yaml"
    monkeypatch.setattr(
        "adsfolio.cv.cli.Config.load_user_profile",
        lambda: UserProfile(orcid="0000-0002-1825-0097"),
    )
    runner = CliRunner()
    result = runner.invoke(
        cv_group,
        ["init", "--output", str(output_path), "--no-orcid"],
        input="\n\n\n\n\n",
    )
    assert result.exit_code == 0
    text = output_path.read_text(encoding="utf-8")
    assert "use_orcid: false" in text


def test_show_styles_prints_style_examples() -> None:
    runner = CliRunner()
    result = runner.invoke(cv_group, ["show-styles"])
    assert result.exit_code == 0
    assert "compact" in result.output
    assert "detailed" in result.output
    assert "bibcode-first" in result.output
