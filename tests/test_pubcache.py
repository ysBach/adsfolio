import json

from adsapi import pubcache


def test_publication_cache_round_trip(monkeypatch, tmp_path):
    path = tmp_path / "publications.json"
    monkeypatch.setattr(pubcache, "cache_path", lambda: path)
    profile = {"orcid": "0000", "debut_year": 2010, "end_year": 2026}
    documents = [{"bibcode": "2020ApJ...900..100S", "title": ["Example"]}]

    assert pubcache.save_publications(profile, documents) == path
    loaded = pubcache.load_publications()

    assert loaded is not None
    assert loaded.profile == profile
    assert loaded.documents == documents
    assert loaded.fetched_at.endswith("+00:00")


def test_publication_cache_missing_and_corrupt_are_cache_misses(
    monkeypatch, tmp_path, caplog
):
    path = tmp_path / "publications.json"
    monkeypatch.setattr(pubcache, "cache_path", lambda: path)

    assert pubcache.load_publications() is None
    path.write_text("{invalid", encoding="utf-8")
    assert pubcache.load_publications() is None
    assert "Ignoring unreadable publication cache" in caplog.text


def test_publication_cache_profile_snapshot_detection(monkeypatch, tmp_path):
    path = tmp_path / "publications.json"
    monkeypatch.setattr(pubcache, "cache_path", lambda: path)
    profile = {"orcid": "0000", "query_names": [], "end_year": None}
    pubcache.save_publications(profile, [])
    loaded = pubcache.load_publications()

    assert loaded is not None
    assert pubcache.profile_matches(loaded.profile, profile)
    assert not pubcache.profile_matches(loaded.profile, {"orcid": "1111"})


def test_publication_cache_clear(monkeypatch, tmp_path):
    path = tmp_path / "publications.json"
    monkeypatch.setattr(pubcache, "cache_path", lambda: path)
    path.write_text(json.dumps({}), encoding="utf-8")

    assert pubcache.clear() is True
    assert pubcache.clear() is False
