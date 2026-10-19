import json

import pytest

from adsfolio.pdf.identify_settings import IdentifySettings
from adsfolio.pdf.sidecar import SIDECAR_FILENAME, Sidecar, SidecarError


def test_sidecar_initializes_empty_payload(tmp_path):
    sidecar = Sidecar(tmp_path)
    assert sidecar.data == {"version": 2, "files": {}}


def test_sidecar_upsert_and_reload_roundtrip(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "paper.pdf",
        {
            "bibcode": "2024ApJ...900..100S",
            "sha256": "abc123",
            "title": ["Paper Title"],
        },
    )
    sidecar.save()

    reloaded = Sidecar(tmp_path)
    record = reloaded.get_file("paper.pdf")
    assert record is not None
    assert record["bibcode"] == "2024ApJ...900..100S"
    assert record["current_filename"] == "paper.pdf"


def test_sidecar_lookup_by_sha_and_current_filename(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "orig.pdf",
        {"bibcode": "2020ApJ...900..001A", "sha256": "sha-a"},
    )
    sidecar.set_current_filename("orig.pdf", "renamed.pdf")

    found_sha = sidecar.find_by_sha256("sha-a")
    assert found_sha is not None
    assert found_sha[0] == "orig.pdf"

    found_name = sidecar.find_by_current_filename("renamed.pdf")
    assert found_name is not None
    assert found_name[0] == "orig.pdf"


def test_sidecar_indexes_track_upsert_and_remove_updates(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "orig.pdf",
        {"bibcode": "2020ApJ...900..001A", "sha256": "sha-a"},
    )
    sidecar.set_current_filename("orig.pdf", "first-name.pdf")

    sidecar.upsert_file(
        "orig.pdf",
        {"bibcode": "2021ApJ...901..002B", "sha256": "sha-b"},
    )
    sidecar.set_current_filename("orig.pdf", "second-name.pdf")

    assert sidecar.find_by_sha256("sha-a") is None
    found_sha = sidecar.find_by_sha256("sha-b")
    assert found_sha is not None
    assert found_sha[0] == "orig.pdf"

    assert sidecar.find_by_current_filename("first-name.pdf") is None
    found_name = sidecar.find_by_current_filename("second-name.pdf")
    assert found_name is not None
    assert found_name[0] == "orig.pdf"

    assert sidecar.remove_file("orig.pdf") is True
    assert sidecar.find_by_sha256("sha-b") is None
    assert sidecar.find_by_current_filename("second-name.pdf") is None


def test_sidecar_migrates_legacy_payload(tmp_path):
    legacy_payload = {
        "legacy.pdf": {
            "bibcode": "2019MNRAS.123..456B",
            "sha256": "legacy-sha",
        }
    }
    sidecar_path = tmp_path / SIDECAR_FILENAME
    sidecar_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    sidecar = Sidecar(tmp_path)
    data = sidecar.data
    assert data["version"] == 2
    assert "legacy.pdf" in data["files"]
    assert data["files"]["legacy.pdf"]["current_filename"] == "legacy.pdf"
    assert data["files"]["legacy.pdf"]["source"] == "published"
    assert data["files"]["legacy.pdf"]["upgrade_status"] == "published"


def test_sidecar_migrates_v1_payload_to_v2_defaults(tmp_path):
    payload_v1 = {
        "version": 1,
        "files": {
            "arxiv.pdf": {
                "bibcode": "2024arXiv240112345B",
                "doi": "10.48550/arXiv.2401.12345",
                "sha256": "legacy-sha",
            }
        },
    }
    sidecar_path = tmp_path / SIDECAR_FILENAME
    sidecar_path.write_text(json.dumps(payload_v1), encoding="utf-8")

    sidecar = Sidecar(tmp_path)
    data = sidecar.data
    assert data["version"] == 2
    record = data["files"]["arxiv.pdf"]
    assert record["source"] == "arxiv"
    assert record["upgrade_status"] == "arxiv_only"
    assert record["original_bibcode"] == "2024arXiv240112345B"
    assert record["annotations_ported"] == 0
    assert record["annotations_failed"] == 0


def test_sidecar_save_creates_backup(tmp_path):
    sidecar_path = tmp_path / SIDECAR_FILENAME
    original = {"version": 1, "files": {}}
    sidecar_path.write_text(json.dumps(original), encoding="utf-8")

    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file("paper.pdf", {"bibcode": "2024ApJ...900..100S"})
    sidecar.save()

    backup_path = tmp_path / f"{SIDECAR_FILENAME}.bak"
    assert backup_path.exists()
    backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup_data == original


def test_sidecar_raises_on_invalid_json(tmp_path):
    sidecar_path = tmp_path / SIDECAR_FILENAME
    sidecar_path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(SidecarError):
        Sidecar(tmp_path)


def test_sidecar_duplicate_detection(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file("a.pdf", {"sha256": "same"})
    sidecar.upsert_file("b.pdf", {"sha256": "same"})
    sidecar.upsert_file("c.pdf", {"sha256": "other"})

    duplicates = sidecar.duplicates_by_sha256()
    assert duplicates == {"same": ["a.pdf", "b.pdf"]}


def test_sidecar_normalizes_unicode_and_writes_unescaped_utf8(tmp_path):
    sidecar = Sidecar(tmp_path)
    sidecar.upsert_file(
        "paper.pdf",
        {
            "author": ["Hanus\u030c, Jan"],
            "title": ["Český název"],
        },
    )
    sidecar.save()

    raw = (tmp_path / SIDECAR_FILENAME).read_text(encoding="utf-8")
    assert "\\u030c" not in raw
    assert "\\u010c" not in raw
    assert "Hanuš, Jan" in raw

    reloaded = Sidecar(tmp_path)
    record = reloaded.get_file("paper.pdf")
    assert record is not None
    assert record["author"][0] == "Hanuš, Jan"


def test_sidecar_set_identify_profile_roundtrip(tmp_path):
    sidecar = Sidecar(tmp_path)
    changed = sidecar.set_identify_profile(
        {
            "orcid": "0000-0002-1825-0097",
            "query_names": ["Bach, Y. P.", ""],
            "debut_year": "2010",
            "retire_year": 2030,
            "library_ids": ["LIB123", "  "],
        }
    )
    assert changed is True
    sidecar.save()

    reloaded = Sidecar(tmp_path)
    assert reloaded.identify_profile == {
        "orcid": "0000-0002-1825-0097",
        "query_names": ["Bach, Y. P."],
        "debut_year": 2010,
        "retire_year": 2030,
        "library_ids": ["LIB123"],
    }


def test_sidecar_set_identify_profile_no_change_and_clear(tmp_path):
    sidecar = Sidecar(tmp_path)
    profile = {
        "orcid": "0000-0002-1825-0097",
        "query_names": ["Bach, Y. P."],
        "debut_year": 2010,
        "retire_year": 2030,
        "library_ids": ["LIB123"],
    }
    assert sidecar.set_identify_profile(profile) is True
    assert sidecar.set_identify_profile(profile) is False
    assert sidecar.set_identify_profile(None) is True
    assert sidecar.identify_profile is None


def test_sidecar_keeps_profile_separate_and_roundtrips_identify_settings(tmp_path):
    (tmp_path / SIDECAR_FILENAME).write_text(
        json.dumps(
            {
                "version": 2,
                "files": {},
                "identify_profile": {"orcid": "0000", "retire_year": 2030},
            }
        ),
        encoding="utf-8",
    )

    sidecar = Sidecar(tmp_path)
    assert sidecar.identify_settings is None
    expected = IdentifySettings(recursive=True, output_unresolved="pending")
    assert sidecar.set_identify_settings(expected)
    sidecar.save()

    assert Sidecar(tmp_path).identify_settings == expected
