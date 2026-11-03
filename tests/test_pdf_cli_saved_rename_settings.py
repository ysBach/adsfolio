"""Apply saved global or library Rename settings in the rename and watch commands, and
let each explicit option override only its own value.
"""

import json
from types import SimpleNamespace

import pytest
import requests
from click.testing import CliRunner
from pypdf import PdfWriter

from adsfolio.config import AuthenticationError, Config
from adsfolio.pdf.cli import pdf
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.files import sha256_file
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.renamer import DEFAULT_SCHEME, PdfRenamer

DOCUMENT = {
    "bibcode": "2024ApJ...900..100S",
    "doi": ["10.1234/example"],
    "title": ["A test paper"],
    "author": ["Hanuš, Ada", "Smith, Bea", "Jones, Cal"],
    "year": "2024",
    "bibstem": ["ApJ"],
    "volume": "900",
    "page": ["100"],
}
GLOBAL_SETTINGS = RenameSettings(
    template="global_{authors}_{year}",
    author_format="surname",
    max_authors=2,
    authors_when_truncated=2,
    char_policy="ascii",
    author_separator="-",
    author_truncation_suffix="++",
)
LIBRARY_SETTINGS = RenameSettings(
    template="library_{authors}_{year}",
    author_format="surname_initials",
    max_authors=2,
    authors_when_truncated=1,
    char_policy="keep",
    author_separator="~",
    author_truncation_suffix="_et_al",
)


@pytest.fixture
def cli_case(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(Config, "get_config_dir", classmethod(lambda cls: config_dir))

    def reject_network(*args, **kwargs):
        raise AssertionError("Network access is not allowed in these tests")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    identify = IdentifySettings(providers=(DoiProvider.CROSSREF, DoiProvider.DATACITE))
    Config.save_identify_settings(identify)
    Config.save_rename_settings(GLOBAL_SETTINGS)

    source = root / "paper.pdf"
    writer = PdfWriter()
    writer.add_blank_page(200, 200)
    writer.add_metadata({"/DOI": DOCUMENT["doi"][0]})
    with source.open("wb") as handle:
        writer.write(handle)
    digest = sha256_file(source)
    record = PdfIdentifier(None).build_record(DOCUMENT, source)
    state = open_registry_state(root)
    try:
        state.upsert_file(source.name, record)
    finally:
        state.close()

    observed = []
    client_calls = []
    fake_client = SimpleNamespace(
        export=SimpleNamespace(custom=lambda bibcodes, format_string: "Custom Author")
    )

    def get_client(ctx):
        client_calls.append(ctx)
        return fake_client

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", get_client)
    original_batch_rename = PdfRenamer.batch_rename

    def capture_batch(self, *args, **kwargs):
        observed.append({key: kwargs[key] for key in GLOBAL_SETTINGS.to_mapping()})
        return original_batch_rename(self, *args, **kwargs)

    monkeypatch.setattr(PdfRenamer, "batch_rename", capture_batch)

    class FakeWatcher:
        def __init__(self, identifier, renamer, upgrader):
            self.renamer = renamer

        def process_new_pdf(self, **kwargs):
            options = {
                key: kwargs["scheme" if key == "template" else key]
                for key in GLOBAL_SETTINGS.to_mapping()
            }
            observed.append(options.copy())
            observed.append({"providers": kwargs["providers"]})
            filename = self.renamer.render(options.pop("template"), DOCUMENT, **options)
            return {"bibcode": DOCUMENT["bibcode"], "planned_filename": filename}

        def poll_loop(self, **kwargs):
            kwargs["callback"](source)

        def watchdog_loop(self, **kwargs):
            kwargs["callback"](source)

    monkeypatch.setattr("adsfolio.pdf.cli.PdfWatcher", FakeWatcher)

    def set_library_settings(settings):
        state = open_registry_state(root)
        try:
            state.set_rename_settings(settings)
        finally:
            state.close()

    return SimpleNamespace(
        root=root,
        source=source,
        digest=digest,
        observed=observed,
        client_calls=client_calls,
        identify=identify,
        set_library_settings=set_library_settings,
    )


def invoke(case, command, *options):
    return CliRunner().invoke(pdf, [command, str(case.root), *options])


@pytest.mark.parametrize("command", ["rename", "watch"])
@pytest.mark.parametrize(
    ("scope_args", "save_library", "expected", "filename"),
    [
        ([], True, GLOBAL_SETTINGS, "global_Hanus-Smith++_2024.pdf"),
        (
            ["--settings-scope", "global"],
            True,
            GLOBAL_SETTINGS,
            "global_Hanus-Smith++_2024.pdf",
        ),
        (
            ["--settings-scope", "library"],
            True,
            LIBRARY_SETTINGS,
            "library_HanušA_et_al_2024.pdf",
        ),
        (
            ["--settings-scope", "library"],
            False,
            GLOBAL_SETTINGS,
            "global_Hanus-Smith++_2024.pdf",
        ),
    ],
)
def test_saved_settings_control_both_commands(
    cli_case, command, scope_args, save_library, expected, filename
):
    if save_library:
        cli_case.set_library_settings(LIBRARY_SETTINGS)
    setup_before = Config.get_setup_path().read_bytes()

    result = invoke(cli_case, command, *scope_args)

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == expected.to_mapping()
    assert filename in result.output
    assert cli_case.source.exists()
    assert sha256_file(cli_case.source) == cli_case.digest
    assert Config.get_setup_path().read_bytes() == setup_before
    assert Config.load_identify_settings() == cli_case.identify
    if command == "watch":
        assert cli_case.observed[1]["providers"] == cli_case.identify.providers
        assert not cli_case.client_calls


@pytest.mark.parametrize("command", ["rename", "watch"])
@pytest.mark.parametrize(
    ("flag", "value", "field", "expected"),
    [
        ("--scheme", DEFAULT_SCHEME, "template", DEFAULT_SCHEME),
        ("--author-format", "surname_initials", "author_format", "surname_initials"),
        ("--char-policy", "keep", "char_policy", "keep"),
        ("--max-authors", "3", "max_authors", 3),
        ("--authors-when-truncated", "1", "authors_when_truncated", 1),
        ("--author-separator", "+", "author_separator", "+"),
        ("--author-truncation-suffix", "+", "author_truncation_suffix", "+"),
        ("--scheme", "", "template", ""),
        ("--author-separator", "", "author_separator", ""),
        ("--author-truncation-suffix", "", "author_truncation_suffix", ""),
    ],
)
def test_each_explicit_option_overrides_only_its_saved_value(
    cli_case, command, flag, value, field, expected
):
    settings = GLOBAL_SETTINGS.to_mapping()
    settings[field] = expected

    result = invoke(cli_case, command, flag, value)

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == settings


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_explicit_options_override_library_settings(cli_case, command):
    cli_case.set_library_settings(LIBRARY_SETTINGS)
    result = invoke(
        cli_case,
        command,
        "--settings-scope",
        "library",
        "--scheme",
        DEFAULT_SCHEME,
        "--author-format",
        "surname",
        "--max-authors",
        "3",
        "--authors-when-truncated",
        "2",
        "--char-policy",
        "ascii",
        "--author-separator",
        "+",
        "--author-truncation-suffix",
        "+",
    )

    assert result.exit_code == 0, result.output
    assert (
        cli_case.observed[0]
        == RenameSettings(
            author_format="surname", authors_when_truncated=2, char_policy="ascii"
        ).to_mapping()
    )


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_malformed_global_settings_use_normalized_defaults(cli_case, command):
    path = Config.get_setup_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["rename"] = {
        "template": 42,
        "author_format": "unknown",
        "max_authors": True,
        "authors_when_truncated": -1,
        "char_policy": "unknown",
        "author_separator": 1,
        "author_truncation_suffix": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = invoke(cli_case, command)

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == RenameSettings().to_mapping()


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_invalid_library_json_falls_back_to_global(cli_case, command):
    state = open_registry_state(cli_case.root)
    try:
        state.registry.catalog.set_meta(
            f"root:{state.directory}:rename_settings", "{invalid"
        )
    finally:
        state.close()

    result = invoke(cli_case, command, "--settings-scope", "library")

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == GLOBAL_SETTINGS.to_mapping()


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_malformed_library_fields_use_existing_normalization(cli_case, command):
    payload = {
        "template": "library_{year}",
        "author_format": "unknown",
        "max_authors": 2,
        "authors_when_truncated": 8,
        "char_policy": "unknown",
        "author_separator": "~",
        "author_truncation_suffix": 4,
    }
    state = open_registry_state(cli_case.root)
    try:
        state.registry.catalog.set_meta(
            f"root:{state.directory}:rename_settings", json.dumps(payload)
        )
    finally:
        state.close()

    result = invoke(cli_case, command, "--settings-scope", "library")

    assert result.exit_code == 0, result.output
    assert (
        cli_case.observed[0]
        == RenameSettings(
            template="library_{year}", max_authors=2, author_separator="~"
        ).to_mapping()
    )


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_library_settings_from_another_root_are_not_used(cli_case, command):
    other_root = cli_case.root.parent / "other-library"
    other_root.mkdir()
    state = open_registry_state(other_root)
    try:
        state.set_rename_settings(LIBRARY_SETTINGS)
    finally:
        state.close()

    result = invoke(cli_case, command, "--settings-scope", "library")

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == GLOBAL_SETTINGS.to_mapping()


@pytest.mark.parametrize("command", ["rename", "watch"])
@pytest.mark.parametrize(
    "options",
    [
        ["--max-authors", "1"],
        ["--authors-when-truncated", "3"],
        ["--max-authors", "1", "--authors-when-truncated", "2"],
    ],
)
def test_combined_author_limits_fail_before_processing(cli_case, command, options):
    result = invoke(cli_case, command, *options)

    assert result.exit_code == 2
    assert "authors_when_truncated cannot exceed max_authors" in result.output
    assert not cli_case.observed
    assert not cli_case.client_calls
    assert sha256_file(cli_case.source) == cli_case.digest


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_library_author_limits_are_checked_after_overrides(cli_case, command):
    cli_case.set_library_settings(
        RenameSettings(max_authors=4, authors_when_truncated=3)
    )

    result = invoke(
        cli_case, command, "--settings-scope", "library", "--max-authors", "2"
    )

    assert result.exit_code == 2
    assert "authors_when_truncated cannot exceed max_authors" in result.output
    assert not cli_case.observed
    assert not cli_case.client_calls
    assert sha256_file(cli_case.source) == cli_case.digest


@pytest.mark.parametrize("command", ["rename", "watch"])
@pytest.mark.parametrize(
    "flag,value", [("--max-authors", "0"), ("--authors-when-truncated", "100")]
)
def test_individual_author_limits_keep_cli_range_checks(cli_case, command, flag, value):
    result = invoke(cli_case, command, flag, value)

    assert result.exit_code == 2
    assert not cli_case.observed
    assert not cli_case.client_calls


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_explicit_author_limits_are_validated_together(cli_case, command):
    result = invoke(
        cli_case, command, "--max-authors", "4", "--authors-when-truncated", "3"
    )

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0]["max_authors"] == 4
    assert cli_case.observed[0]["authors_when_truncated"] == 3


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_saved_custom_author_format_uses_fake_client(cli_case, command):
    Config.save_rename_settings(
        RenameSettings(template="{authors}", author_format="ads_custom:%3.1m")
    )

    result = invoke(cli_case, command)

    assert result.exit_code == 0, result.output
    assert "Custom_Author.pdf" in result.output
    assert len(cli_case.client_calls) == 1
    if command == "watch":
        assert cli_case.observed[1]["providers"] == cli_case.identify.providers


def test_watch_custom_author_format_requires_authentication(cli_case, monkeypatch):
    Config.save_rename_settings(RenameSettings(author_format="ads_custom:%3.1m"))

    def fail_auth(ctx):
        raise AuthenticationError("No test token")

    monkeypatch.setattr("adsfolio.pdf.cli._get_client", fail_auth)
    result = invoke(cli_case, "watch")

    assert result.exit_code == 1
    assert "No test token" in result.output
    assert not cli_case.observed


def test_watchdog_uses_library_rename_settings(cli_case):
    cli_case.set_library_settings(LIBRARY_SETTINGS)

    result = invoke(cli_case, "watch", "--settings-scope", "library", "--use-watchdog")

    assert result.exit_code == 0, result.output
    assert cli_case.observed[0] == LIBRARY_SETTINGS.to_mapping()


def test_rename_apply_preserves_pdf_bytes(cli_case):
    result = invoke(cli_case, "rename", "--apply")

    target = cli_case.root / "global_Hanus-Smith++_2024.pdf"
    assert result.exit_code == 0, result.output
    assert not cli_case.source.exists()
    assert sha256_file(target) == cli_case.digest


def test_rename_scope_does_not_change_identify_settings(cli_case, monkeypatch):
    library_identify = IdentifySettings(providers=(DoiProvider.DATACITE,))
    state = open_registry_state(cli_case.root)
    try:
        state.set_identify_settings(library_identify)
    finally:
        state.close()
    identify_calls = []
    monkeypatch.setattr(
        "adsfolio.pdf.cli.run_identify",
        lambda client, directory, **kwargs: identify_calls.append(kwargs),
    )

    result = invoke(cli_case, "rename", "--settings-scope", "global", "--allow-ads")

    assert result.exit_code == 0, result.output
    assert identify_calls[0]["providers"] == library_identify.providers
    assert Config.load_identify_settings() == cli_case.identify


def test_watch_rename_scope_does_not_change_identify_settings(cli_case):
    library_identify = IdentifySettings(providers=(DoiProvider.DATACITE,))
    state = open_registry_state(cli_case.root)
    try:
        state.set_identify_settings(library_identify)
    finally:
        state.close()

    result = invoke(cli_case, "watch", "--settings-scope", "global")

    assert result.exit_code == 0, result.output
    assert cli_case.observed[1]["providers"] == library_identify.providers
    assert Config.load_identify_settings() == cli_case.identify


@pytest.mark.parametrize("command", ["rename", "watch"])
def test_help_describes_saved_defaults(cli_case, command):
    result = CliRunner().invoke(pdf, [command, "--help"])

    assert result.exit_code == 0
    assert "--settings-scope" in result.output
    assert "saved Rename setting" in result.output
