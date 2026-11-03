"""Test watch rechecks with isolated clocks, providers, and files."""

import hashlib
import queue
import sys
from types import ModuleType, SimpleNamespace

import pytest
import requests
from click.testing import CliRunner
from pypdf import PdfWriter

from adsfolio.config import AuthenticationError, Config
from adsfolio.pdf import cli as pdf_cli
from adsfolio.pdf import watcher as watcher_module
from adsfolio.pdf.doi_providers import DoiProvider
from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_settings import RenameSettings
from adsfolio.pdf.upgrade import PdfUpgrader
from adsfolio.pdf.watcher import PdfWatcher


def _result(**overrides):
    values = {
        "checked": 1,
        "updated": 0,
        "renamed": 0,
        "failures": [],
        "cancelled": False,
        "planned_updates": 1,
        "planned_renames": 1,
    }
    return SimpleNamespace(**(values | overrides))


class _Clock:
    def __init__(self):
        self.times = [0]
        self.index = 0
        self.finished = False
        self.sleeps = []

    def monotonic(self):
        return self.times[self.index]

    def advance(self):
        if self.index + 1 == len(self.times):
            self.finished = True
        else:
            self.index += 1

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance()


class _StopEvent:
    def __init__(self, clock):
        self.clock = clock
        self.stopped = False

    def is_set(self):
        return self.stopped or self.clock.finished

    def set(self):
        self.stopped = True


class _State:
    def __init__(self, root):
        self.directory = root
        self.identify_settings = IdentifySettings(
            providers=(DoiProvider.ADS,), allow_ads=False
        )
        self.rename_settings = RenameSettings(template="library_{bibcode}")
        self.missing_checks = []
        self.registry = SimpleNamespace(
            catalog=SimpleNamespace(mark_missing_locations=self.mark_missing)
        )
        self.records = {"paper.pdf": {"bibcode": "temporary"}}

    def mark_missing(self, *args, **kwargs):
        self.missing_checks.append((args, kwargs))

    def close(self):
        pass


@pytest.fixture
def watch_case(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture contents\n")
    state = _State(root)
    clock = _Clock()
    stop = _StopEvent(clock)
    calls = []
    upgrades = []
    observers = []
    client = object()

    def reject_network(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.setattr(pdf_cli, "open_registry_state", lambda _root: state)
    monkeypatch.setattr(
        pdf_cli, "load_library_identify_settings", lambda _root: state.identify_settings
    )
    monkeypatch.setattr(
        Config,
        "load_rename_settings",
        classmethod(lambda cls: RenameSettings(template="global_{bibcode}")),
    )
    monkeypatch.setattr(pdf_cli, "_get_client", lambda _ctx: client)
    monkeypatch.setattr(pdf_cli, "threading", SimpleNamespace(Event=lambda: stop))
    monkeypatch.setattr(watcher_module, "time", clock)
    monkeypatch.setattr(
        PdfWatcher, "_scan_or_none", lambda *args, **kwargs: ([], set())
    )

    def upgrade(_self, **kwargs):
        upgrades.append((clock.monotonic(), kwargs))
        return []

    monkeypatch.setattr(PdfUpgrader, "batch_upgrade", upgrade)

    events = ModuleType("watchdog.events")
    events.FileSystemEventHandler = type("FileSystemEventHandler", (), {})
    observer_module = ModuleType("watchdog.observers")

    class Observer:
        def __init__(self):
            self.stopped = False
            self.joined = False
            observers.append(self)

        def schedule(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            self.stopped = True

        def join(self):
            self.joined = True

    class EmptyQueue:
        def __init__(self):
            self.first_get = True

        def get(self, timeout):
            if self.first_get:
                self.first_get = False
            else:
                clock.advance()
            raise queue.Empty

        def put(self, value):
            raise AssertionError(f"Unexpected queued path: {value}")

    observer_module.Observer = Observer
    monkeypatch.setitem(sys.modules, "watchdog", ModuleType("watchdog"))
    monkeypatch.setitem(sys.modules, "watchdog.events", events)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observer_module)
    monkeypatch.setattr(
        watcher_module, "queue", SimpleNamespace(Queue=EmptyQueue, Empty=queue.Empty)
    )

    recheck_module = ModuleType("adsfolio.pdf.ads_recheck")

    def recheck(state_arg, client_arg, settings_arg, **kwargs):
        calls.append((clock.monotonic(), state_arg, client_arg, settings_arg, kwargs))
        return _result(updated=1, renamed=1)

    recheck_module.recheck_existing = recheck
    monkeypatch.setitem(sys.modules, "adsfolio.pdf.ads_recheck", recheck_module)

    def invoke(backend, *options, token=True):
        backend_options = ["--use-watchdog"] if backend == "watchdog" else []
        obj = {"token": "test-token"} if token else {}
        return CliRunner().invoke(
            pdf_cli.pdf, ["watch", str(root), *backend_options, *options], obj=obj
        )

    return SimpleNamespace(
        root=root,
        source=source,
        state=state,
        clock=clock,
        stop=stop,
        calls=calls,
        upgrades=upgrades,
        observers=observers,
        client=client,
        recheck_module=recheck_module,
        invoke=invoke,
    )


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize("hours", [24.0, 3.0])
def test_rechecks_at_startup_and_interval_without_auto_upgrade(
    watch_case, backend, hours
):
    period = int(hours * 3600)
    watch_case.clock.times = [0, period - 1, period, period * 2 - 1, period * 2]
    options = [] if hours == 24 else ["--upgrade-interval", str(hours)]

    result = watch_case.invoke(backend, *options)

    assert result.exit_code == 0, result.output
    assert [call[0] for call in watch_case.calls] == [0, period, period * 2]
    assert not watch_case.upgrades
    assert all(call[4]["ads_enabled"] for call in watch_case.calls)
    assert all(
        call[4]["identify_settings"] == watch_case.state.identify_settings
        for call in watch_case.calls
    )
    assert all(call[4]["recursive"] is False for call in watch_case.calls)
    if backend == "watchdog":
        assert watch_case.observers[0].stopped
        assert watch_case.observers[0].joined


def test_poll_sleep_does_not_delay_recheck_past_its_interval(watch_case):
    watch_case.clock.times = [0, 3600]

    result = watch_case.invoke(
        "poll", "--interval", "100000", "--upgrade-interval", "1"
    )

    assert result.exit_code == 0, result.output
    assert [call[0] for call in watch_case.calls] == [0, 3600]
    assert watch_case.clock.sleeps == [3600, 3600]


def test_poll_sleep_stops_when_event_is_set(watch_case, monkeypatch):
    class StopWhileWaiting:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, seconds):
            self.waits.append(seconds)
            return True

        def set(self):
            pass

    stop = StopWhileWaiting()
    monkeypatch.setattr(pdf_cli, "threading", SimpleNamespace(Event=lambda: stop))

    result = watch_case.invoke("poll", "--interval", "100000")

    assert result.exit_code == 0, result.output
    assert stop.waits == [24 * 3600]
    assert len(watch_case.calls) == 1
    assert not watch_case.clock.sleeps


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize("allow_ads", [False, True])
def test_disabled_ads_provider_skips_rechecks(watch_case, backend, allow_ads):
    watch_case.state.identify_settings = IdentifySettings(
        providers=(DoiProvider.CROSSREF,), allow_ads=allow_ads
    )
    watch_case.clock.times = [0, 24 * 3600]

    result = watch_case.invoke(backend, token=False)

    assert result.exit_code == 0, result.output
    assert not watch_case.calls
    assert "ADS recheck" not in result.output


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize("allow_ads", [False, True])
def test_foreground_permission_does_not_block_background_rechecks(
    watch_case, backend, allow_ads
):
    watch_case.state.identify_settings = IdentifySettings(
        providers=(DoiProvider.ADS,), allow_ads=allow_ads
    )

    result = watch_case.invoke(backend)

    assert result.exit_code == 0, result.output
    assert len(watch_case.calls) == 1


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize("use_override", [False, True])
def test_recheck_receives_identify_path_policy_and_recursion(
    watch_case, backend, use_override
):
    watch_case.state.identify_settings = IdentifySettings(
        providers=(DoiProvider.ADS,),
        allow_ads=False,
        output_unresolved="saved-folder",
        skip_unresolved_directory=True,
    )
    options = ["--recursive"]
    if use_override:
        options.extend(["--output-unresolved", "override-folder"])

    result = watch_case.invoke(backend, *options)

    assert result.exit_code == 0, result.output
    assert len(watch_case.calls) == 1
    kwargs = watch_case.calls[0][4]
    assert kwargs["recursive"] is True
    settings = kwargs["identify_settings"]
    assert settings.skip_unresolved_directory is True
    assert settings.allow_ads is False
    if use_override:
        assert settings.output_unresolved == str(watch_case.root / "override-folder")
    else:
        assert settings.output_unresolved == "saved-folder"


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_missing_token_skips_rechecks_without_prompt(watch_case, monkeypatch, backend):
    token_checks = []

    def missing_token(cls, *, prompt=True):
        token_checks.append(prompt)
        raise AuthenticationError("No token")

    monkeypatch.setattr(Config, "get_token", classmethod(missing_token))
    watch_case.clock.times = [0, 24 * 3600]

    result = watch_case.invoke(backend, token=False)

    assert result.exit_code == 0, result.output
    assert token_checks == [False, False]
    assert not watch_case.calls
    assert "ADS recheck skipped: no token" in result.output


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_recheck_resumes_after_token_becomes_available(
    watch_case, monkeypatch, backend
):
    watch_case.clock.times = [0, 24 * 3600]
    token_checks = []

    def token_for_next_check(cls, *, prompt=True):
        token_checks.append(prompt)
        if len(token_checks) == 1:
            raise AuthenticationError("No token")
        return "test-token"

    monkeypatch.setattr(Config, "get_token", classmethod(token_for_next_check))
    result = watch_case.invoke(backend, token=False)

    assert result.exit_code == 0, result.output
    assert token_checks == [False, False]
    assert [call[0] for call in watch_case.calls] == [24 * 3600]


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize("apply", [False, True])
def test_recheck_uses_saved_settings_and_respects_apply(watch_case, backend, apply):
    original_bytes = watch_case.source.read_bytes()
    original_hash = hashlib.sha256(original_bytes).hexdigest()
    original_records = dict(watch_case.state.records)
    destination = watch_case.root / "custom_published.pdf"
    watch_case.clock.times = [0, 11]

    def recheck(state, client, settings, **kwargs):
        watch_case.calls.append((state, client, settings, kwargs))
        if kwargs["apply"]:
            watch_case.source.rename(destination)
            state.records = {destination.name: {"bibcode": "published"}}
        return _result(updated=int(kwargs["apply"]), renamed=int(kwargs["apply"]))

    watch_case.recheck_module.recheck_existing = recheck
    options = ["--settings-scope", "library", "--scheme", "custom_{bibcode}"]
    if apply:
        options.append("--apply")

    result = watch_case.invoke(backend, *options)

    assert result.exit_code == 0, result.output
    assert len(watch_case.calls) == 1
    state, client, settings, kwargs = watch_case.calls[0]
    assert state is watch_case.state
    assert client is watch_case.client
    assert settings.template == "custom_{bibcode}"
    assert settings.author_format == watch_case.state.rename_settings.author_format
    assert kwargs["apply"] is apply
    assert kwargs["ads_enabled"] is True
    assert callable(kwargs["cancelled"])
    if apply:
        assert not watch_case.source.exists()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == original_hash
        assert watch_case.state.records[destination.name]["bibcode"] == "published"
        assert watch_case.state.missing_checks
    else:
        assert watch_case.source.read_bytes() == original_bytes
        assert not destination.exists()
        assert watch_case.state.records == original_records
        assert not watch_case.state.missing_checks
        assert "planned updates 1, planned renames 1" in result.output


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_recheck_failure_is_reported_and_retried_on_schedule(
    watch_case, backend, caplog
):
    period = 24 * 3600
    watch_case.clock.times = [0, period - 1, period]

    def recheck(*args, **kwargs):
        watch_case.calls.append(watch_case.clock.monotonic())
        if len(watch_case.calls) == 1:
            raise AuthenticationError("ADS authorization failed")
        return _result()

    watch_case.recheck_module.recheck_existing = recheck
    result = watch_case.invoke(backend)

    assert result.exit_code == 0, result.output
    assert watch_case.calls == [0, period]
    assert "ADS authorization failed" in caplog.text
    assert "checked 1" in result.output


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_recheck_reports_backend_failure_details(watch_case, backend):
    watch_case.recheck_module.recheck_existing = lambda *args, **kwargs: _result(
        failures=["paper.pdf: ADS quota is low"]
    )

    result = watch_case.invoke(backend)

    assert result.exit_code == 0, result.output
    assert "failures 1" in result.output
    assert "paper.pdf: ADS quota is low" in result.output


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_recheck_cancellation_stops_watch(watch_case, backend):
    def recheck(*args, **kwargs):
        watch_case.stop.set()
        watch_case.calls.append(kwargs["cancelled"]())
        return _result(checked=0, cancelled=True)

    watch_case.recheck_module.recheck_existing = recheck
    result = watch_case.invoke(backend, "--auto-upgrade")

    assert result.exit_code == 0, result.output
    assert watch_case.calls == [True]
    assert len(watch_case.upgrades) == 1
    assert not watch_case.clock.sleeps
    if backend == "watchdog":
        assert watch_case.observers[0].stopped
        assert watch_case.observers[0].joined


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_cancellation_before_start_skips_all_jobs(watch_case, backend):
    watch_case.stop.set()

    result = watch_case.invoke(backend, "--auto-upgrade", "--apply")

    assert result.exit_code == 0, result.output
    assert not watch_case.calls
    assert not watch_case.upgrades
    assert not watch_case.state.missing_checks
    if backend == "watchdog":
        assert watch_case.observers[0].stopped
        assert watch_case.observers[0].joined


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_auto_upgrade_keeps_independent_startup_and_interval(watch_case, backend):
    period = 12 * 3600
    watch_case.clock.times = [0, period - 1, period]

    result = watch_case.invoke(backend, "--auto-upgrade", "--upgrade-interval", "12")

    assert result.exit_code == 0, result.output
    assert [call[0] for call in watch_case.calls] == [0, period]
    assert [call[0] for call in watch_case.upgrades] == [0, period]
    assert all(not call[1]["apply"] for call in watch_case.upgrades)


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_auto_upgrade_still_runs_when_rechecks_are_disabled(watch_case, backend):
    period = 24 * 3600
    watch_case.state.identify_settings = IdentifySettings(
        providers=(DoiProvider.CROSSREF,)
    )
    watch_case.clock.times = [0, period]

    result = watch_case.invoke(backend, "--auto-upgrade")

    assert result.exit_code == 0, result.output
    assert not watch_case.calls
    assert [call[0] for call in watch_case.upgrades] == [0, period]


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
def test_upgrade_runs_before_recheck_changes_a_bibcode(
    watch_case, monkeypatch, backend
):
    order = []

    def upgrade(_self, **kwargs):
        assert watch_case.state.records["paper.pdf"]["bibcode"] == "temporary"
        order.append("upgrade")
        return []

    def recheck(*args, **kwargs):
        watch_case.state.records["paper.pdf"]["bibcode"] = "published"
        order.append("recheck")
        return _result(updated=1)

    monkeypatch.setattr(PdfUpgrader, "batch_upgrade", upgrade)
    watch_case.recheck_module.recheck_existing = recheck
    result = watch_case.invoke(backend, "--auto-upgrade", "--apply")

    assert result.exit_code == 0, result.output
    assert order == ["upgrade", "recheck"]


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("record_type", ["external", "temporary_bibcode"])
def test_cli_rechecks_a_real_isolated_record(tmp_path, monkeypatch, apply, record_type):
    doi = "10.5555/watch-recheck"
    bibcode = "2024ApJ...900..100E"
    document = {
        "bibcode": bibcode,
        "doi": [doi],
        "title": ["Synthetic paper"],
        "author": ["Example, A."],
        "year": "2024",
    }
    temporary_bibcode = "2024ApJ...tmp..10E"
    if record_type == "temporary_bibcode":
        document["alternate_bibcode"] = [temporary_bibcode]
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    destination = root / f"saved_{bibcode}.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=400)
    writer.add_metadata({"/DOI": doi})
    writer.write(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: True))
    state = open_registry_state(root)
    try:
        state.set_identify_settings(
            IdentifySettings(providers=(DoiProvider.ADS,), allow_ads=False)
        )
        state.set_rename_settings(RenameSettings(template="saved_{bibcode}"))
        if record_type == "external":
            original_document = {
                key: value for key, value in document.items() if key != "bibcode"
            }
            original_document["provider"] = "crossref"
        else:
            original_document = {**document, "bibcode": temporary_bibcode}
            original_document.pop("alternate_bibcode")
        metadata = PdfIdentifier(None).build_record(original_document, source)
        metadata["notes"] = "Keep these notes"
        metadata["_adsfolio_locked_fields"] = ["title"]
        state.upsert_file(source.name, metadata)
        original = state.registry.catalog.get_file(root, source.name)
    finally:
        state.close()

    queries = []

    def query(*, q, fl, rows, start=0):
        queries.append(q)
        return SimpleNamespace(num_found=1, docs=[document], start=start)

    def reject_network(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.setattr(
        pdf_cli,
        "_get_client",
        lambda _ctx: SimpleNamespace(search=SimpleNamespace(query=query)),
    )
    monkeypatch.setattr(
        PdfWatcher, "poll_loop", lambda self, **kwargs: kwargs["recheck_callback"]()
    )
    options = ["--settings-scope", "library"]
    if apply:
        options.append("--apply")

    result = CliRunner().invoke(
        pdf_cli.pdf, ["watch", str(root), *options], obj={"token": "test-token"}
    )

    assert result.exit_code == 0, result.output
    assert queries
    state = open_registry_state(root)
    try:
        saved = state.registry.catalog.get_file(
            root, destination.name if apply else source.name
        )
        assert saved["attachment_id"] == original["attachment_id"]
        assert saved["metadata"]["notes"] == "Keep these notes"
        assert saved["metadata"]["_adsfolio_locked_fields"] == ["title"]
        if apply:
            assert saved["bibcode"] == bibcode
            assert not source.exists()
            assert hashlib.sha256(destination.read_bytes()).hexdigest() == digest
        else:
            assert saved == original
            assert not destination.exists()
            assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    finally:
        state.close()


@pytest.mark.parametrize("backend", ["poll", "watchdog"])
@pytest.mark.parametrize(
    ("skip", "options", "included"),
    [
        (True, [], {"top.pdf"}),
        (True, ["--recursive"], {"top.pdf", "nested/deep.pdf", "other/other.pdf"}),
        (
            True,
            ["--recursive", "--output-unresolved", "other"],
            {"top.pdf", "nested/deep.pdf", "unresolved/skipped.pdf"},
        ),
        (
            False,
            ["--recursive"],
            {"top.pdf", "nested/deep.pdf", "other/other.pdf", "unresolved/skipped.pdf"},
        ),
        (True, ["--recursive", "--output-unresolved", "."], set()),
    ],
)
def test_cli_rechecks_only_allowed_locations(
    tmp_path, monkeypatch, backend, skip, options, included
):
    root = tmp_path / "library"
    root.mkdir()
    monkeypatch.setattr(
        Config, "get_config_dir", classmethod(lambda cls: tmp_path / "config")
    )
    monkeypatch.setattr(Config, "check_rate_limit", classmethod(lambda cls: True))
    state = open_registry_state(root)
    documents = {}
    digests = {}
    try:
        state.set_identify_settings(
            IdentifySettings(
                providers=(DoiProvider.ADS,),
                allow_ads=False,
                output_unresolved="unresolved",
                skip_unresolved_directory=skip,
            )
        )
        state.set_rename_settings(RenameSettings(template="{bibcode}"))
        for index, relative in enumerate(
            ("top.pdf", "nested/deep.pdf", "unresolved/skipped.pdf", "other/other.pdf")
        ):
            source = root / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            doi = f"10.5555/scope-{index}"
            document = {
                "bibcode": f"2024ApJ...900..10{index}E",
                "doi": [doi],
                "title": [f"Synthetic paper {index}"],
                "author": ["Example, A."],
                "year": "2024",
            }
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=400)
            writer.add_metadata({"/DOI": doi})
            writer.write(source)
            digests[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
            external = {
                key: value for key, value in document.items() if key != "bibcode"
            }
            metadata = PdfIdentifier(None).build_record(
                {**external, "provider": "crossref"}, source
            )
            metadata["current_filename"] = relative
            metadata["notes"] = f"Keep note {index}"
            state.upsert_file(relative, metadata)
            documents[relative] = document
    finally:
        state.close()

    requested = set()

    def query(*, q, fl, rows, start=0):
        matches = []
        for relative, document in documents.items():
            if document["doi"][0] in q:
                requested.add(relative)
                matches.append(document)
        return SimpleNamespace(num_found=len(matches), docs=matches, start=start)

    def reject_network(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.setattr(
        pdf_cli,
        "_get_client",
        lambda _ctx: SimpleNamespace(search=SimpleNamespace(query=query)),
    )
    loop = "watchdog_loop" if backend == "watchdog" else "poll_loop"
    monkeypatch.setattr(
        PdfWatcher, loop, lambda self, **kwargs: kwargs["recheck_callback"]()
    )
    backend_options = ["--use-watchdog"] if backend == "watchdog" else []
    result = CliRunner().invoke(
        pdf_cli.pdf,
        [
            "watch",
            str(root),
            *backend_options,
            "--settings-scope",
            "library",
            "--apply",
            *options,
        ],
        obj={"token": "test-token"},
    )

    assert result.exit_code == 0, result.output
    assert requested == included
    if options[-1:] == ["."]:
        assert "Invalid Identify scope" in result.output
    state = open_registry_state(root)
    try:
        for index, (relative, document) in enumerate(documents.items()):
            source = root / relative
            target = source.with_name(f"{document['bibcode']}.pdf")
            if relative in included:
                assert not source.exists()
                assert (
                    hashlib.sha256(target.read_bytes()).hexdigest() == digests[relative]
                )
                saved = state.registry.catalog.get_file(
                    root, target.relative_to(root).as_posix()
                )
                assert saved["bibcode"] == document["bibcode"]
            else:
                assert not target.exists()
                assert (
                    hashlib.sha256(source.read_bytes()).hexdigest() == digests[relative]
                )
                saved = state.registry.catalog.get_file(root, relative)
                assert not saved["bibcode"]
            assert saved["metadata"]["notes"] == f"Keep note {index}"
    finally:
        state.close()
