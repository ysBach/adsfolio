"""Exact-plan tests for the hidden Rename preview worker."""

from contextlib import closing

import pytest
from pdf_identity_fixtures import confirm_metadata, write_pdf

from adsfolio.pdf.identifier import PdfIdentifier
from adsfolio.pdf.registry import open_registry_state
from adsfolio.pdf.rename_preview import (
    apply_plan,
    build_apply_request,
    build_request,
    run_request,
)


def _register(root, relative_path, bibcode, *, title=None):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    write_pdf(path)
    metadata = confirm_metadata(
        path,
        {
            "bibcode": bibcode,
            "title": [title or f"Paper {bibcode[-1]}"],
            "author": ["Example, A."],
            "year": "2024",
            "status": "resolved",
            "current_filename": relative_path,
        },
    )
    with closing(open_registry_state(root)) as state:
        state.upsert_file(relative_path, metadata)
    return path


def _preview(root, tmp_path, *, template="{bibcode}"):
    request = build_request(
        root,
        {
            "template": template,
            "move_resolved_from": str(root / "unresolved"),
            "move_resolved_to": str(root),
        },
        config_dir=tmp_path / "config",
        result_path=tmp_path / "preview-result.json",
    )
    result = run_request(request)
    assert result["error"] is None
    assert result["invalid"] is None
    return result


def _apply_request(root, actions, tmp_path):
    return build_apply_request(
        root,
        actions,
        config_dir=tmp_path / "config",
        result_path=tmp_path / "apply-result.json",
    )


def test_worker_preview_freezes_exact_nested_source_target_and_catalog_version(
    tmp_path,
):
    root = tmp_path / "library"
    root.mkdir()
    source = _register(root, "unresolved/paper.pdf", "2024ApJ...900..100S")
    source_bytes = source.read_bytes()

    result = _preview(root, tmp_path)

    assert result["plans"] == [["unresolved/paper.pdf", "2024ApJ...900..100S.pdf"]]
    action = result["actions"][0]
    assert action["source"] == "unresolved/paper.pdf"
    assert action["target"] == "2024ApJ...900..100S.pdf"
    assert action["sha256"]
    assert action["catalog"]["current_version_id"] is not None

    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    target = root / action["target"]
    assert applied["error"] is None
    assert applied["applied"] == [[action["source"], action["target"]]]
    assert target.read_bytes() == source_bytes
    assert not source.exists()


def test_worker_apply_does_not_add_files_created_after_preview(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    first = _register(root, "first.pdf", "2024ApJ...900..100S")
    result = _preview(root, tmp_path)
    later = _register(root, "later.pdf", "2025ApJ...901..101B")
    later_bytes = later.read_bytes()

    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    assert applied["error"] is None
    assert not first.exists()
    assert later.exists() and later.read_bytes() == later_bytes
    assert (root / "2025ApJ...901..101B.pdf").exists() is False


def test_worker_keeps_non_ads_rename_inside_unresolved_subfolder(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "unresolved" / "local-paper.pdf"
    source.parent.mkdir()
    write_pdf(source)
    metadata = confirm_metadata(
        source,
        {
            "title": ["Local Paper"],
            "author": ["Example, A."],
            "year": "2024",
            "status": "unresolved",
            "current_filename": "unresolved/local-paper.pdf",
            "_adsfolio_locked_fields": ["title"],
        },
    )
    with closing(open_registry_state(root)) as state:
        state.upsert_file("unresolved/local-paper.pdf", metadata)

    result = _preview(root, tmp_path, template="{title}")

    assert result["error"] is None
    assert result["actions"][0]["source"] == "unresolved/local-paper.pdf"
    assert result["actions"][0]["target"] == "unresolved/Local_Paper.pdf"
    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    assert applied["error"] is None
    assert (root / "unresolved/Local_Paper.pdf").exists()
    assert not source.exists()


@pytest.mark.parametrize(
    ("first_title", "second_title"),
    [("Paper", "paper"), ("Café", "Cafe\u0301")],
)
def test_preview_excludes_every_unicode_equivalent_target(
    tmp_path, first_title, second_title
):
    root = tmp_path / "library"
    root.mkdir()
    _register(root, "first.pdf", "2024ApJ...900..100S", title=first_title)
    _register(root, "second.pdf", "2025ApJ...901..101B", title=second_title)

    result = _preview(root, tmp_path, template="{title}")

    assert result["error"] is None
    assert result["plans"] == []
    assert result["actions"] == []
    assert set(result["skipped"]) == {"first.pdf", "second.pdf"}
    assert all(
        "Conflicts with 1 other planned source" in reason
        for reason in result["skipped"].values()
    )


def test_preview_skips_symlinked_action_and_keeps_valid_actions(tmp_path, monkeypatch):
    from adsfolio.pdf.renamer import PdfRenamer

    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    write_pdf(outside / "unsafe.pdf")
    valid = root / "valid.pdf"
    write_pdf(valid)

    def fake_batch(_self, _state, *, dry_run, _action_sink, **_options):
        assert dry_run
        _action_sink.extend(
            [
                {
                    "source": "redirect/unsafe.pdf",
                    "target": "redirect/Unsafe.pdf",
                    "catalog": {},
                    "sha256": "unused",
                    "recovery": False,
                },
                {
                    "source": "valid.pdf",
                    "target": "Valid.pdf",
                    "catalog": {},
                    "sha256": "unused",
                    "recovery": False,
                },
            ]
        )
        return [
            ("redirect/unsafe.pdf", "Unsafe.pdf"),
            ("valid.pdf", "Valid.pdf"),
        ]

    monkeypatch.setattr(PdfRenamer, "batch_rename", fake_batch)

    result = _preview(root, tmp_path, template="{title}")

    assert result["error"] is None
    assert result["plans"] == [["valid.pdf", "Valid.pdf"]]
    assert [action["source"] for action in result["actions"]] == ["valid.pdf"]
    assert "symbolic link" in result["skipped"]["redirect/unsafe.pdf"]
    assert valid.exists()


@pytest.mark.parametrize("conflict", ["source", "target"])
def test_worker_apply_refuses_stale_source_or_new_target_without_moving(
    tmp_path, conflict
):
    root = tmp_path / "library"
    root.mkdir()
    source = _register(root, "paper.pdf", "2024ApJ...900..100S")
    result = _preview(root, tmp_path)
    action = result["actions"][0]
    target = root / action["target"]
    if conflict == "source":
        source.write_bytes(source.read_bytes() + b"changed after preview")
    else:
        target.write_bytes(b"new target")
        target_bytes = target.read_bytes()

    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    assert applied["error"]
    assert applied["conflicts"] == [applied["error"]]
    if conflict == "source":
        assert source.exists() and not target.exists()
    else:
        assert source.exists() and target.read_bytes() == target_bytes


def test_worker_preflights_all_targets_before_first_move(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    first = _register(root, "a.pdf", "2024ApJ...900..100S")
    second = _register(root, "b.pdf", "2025ApJ...901..101B")
    result = _preview(root, tmp_path)
    assert len(result["actions"]) == 2
    blocked = root / result["actions"][1]["target"]
    blocked.write_bytes(b"occupied")

    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    assert applied["error"]
    assert first.exists() and second.exists()
    assert not (root / result["actions"][0]["target"]).exists()


def test_worker_apply_rejects_target_parent_symlink(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    source = _register(root, "paper.pdf", "2024ApJ...900..100S")
    result = _preview(root, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "redirect").symlink_to(outside, target_is_directory=True)
    action = dict(result["actions"][0])
    action["target"] = "redirect/target.pdf"

    applied = apply_plan(_apply_request(root, [action], tmp_path))

    assert applied["error"]
    assert source.exists()
    assert not (outside / "target.pdf").exists()


def test_worker_marks_unscanned_byte_change_for_safe_recovery(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    source = root / "paper.pdf"
    doi = "10.1234/rename-recovery"
    document = {
        "bibcode": "2024ApJ...900..100S",
        "doi": [doi],
        "title": ["Recovered paper"],
        "author": ["Example, A."],
        "year": "2024",
    }
    write_pdf(source, doi=doi)
    with closing(open_registry_state(root)) as state:
        state.registry.upsert_ads_document(document, source="test")
        state.upsert_file(
            source.name, PdfIdentifier(None).build_record(document, source)
        )
    source.write_bytes(source.read_bytes() + b"\n% changed without catalog scan\n")

    result = _preview(root, tmp_path)

    assert result["error"] is None
    assert result["actions"][0]["recovery"] is True
    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    target = root / "2024ApJ...900..100S.pdf"
    assert applied["error"] is None
    assert target.exists() and not source.exists()


def test_worker_reports_completed_move_when_later_move_fails(tmp_path, monkeypatch):
    import adsfolio.pdf.rename_preview as preview_module

    root = tmp_path / "library"
    root.mkdir()
    first = _register(root, "a.pdf", "2024ApJ...900..100S")
    second = _register(root, "b.pdf", "2025ApJ...901..101B")
    result = _preview(root, tmp_path)
    original_move = preview_module.move_tracked_file

    def fail_second(state, source, target):
        if source == second:
            raise OSError("simulated second move failure")
        return original_move(state, source, target)

    monkeypatch.setattr(preview_module, "move_tracked_file", fail_second)
    applied = apply_plan(_apply_request(root, result["actions"], tmp_path))

    assert applied["applied"] == [["a.pdf", "2024ApJ...900..100S.pdf"]]
    assert "simulated second move failure" in applied["error"]
    assert not first.exists()
    assert second.exists()


def test_main_logs_completed_moves_before_batch_error(tmp_path, monkeypatch, caplog):
    import json
    import logging

    import adsfolio.pdf.rename_preview as preview_module

    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "operation": "apply",
                "config_dir": str(tmp_path / "config"),
                "root": str(tmp_path),
                "result_path": str(tmp_path / "result.json"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        preview_module,
        "apply_plan",
        lambda _request: {
            "applied": [["a.pdf", "A.pdf"]],
            "conflicts": ["later move failed"],
            "error": "later move failed",
        },
    )
    monkeypatch.setattr(preview_module, "write_result", lambda *_args: None)

    with caplog.at_level(logging.INFO, logger="adsfolio.pdf.rename_preview"):
        assert preview_module.main(request_path) == 1

    messages = [record.getMessage() for record in caplog.records]
    assert messages.index("Renamed a.pdf to A.pdf") < messages.index(
        "Rename Apply stopped: later move failed"
    )
