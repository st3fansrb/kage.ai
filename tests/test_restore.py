"""WP-G1 — restore backup cache_db: round-trip backup → modificare → restore."""
import tarfile
from pathlib import Path

import pytest

import orchestrator as o


def _make_archive(archive_path: Path, files: dict):
    """Construiește o arhivă în formatul lui _backup_cache_db (cache_db/ ca root)."""
    import tempfile
    with tempfile.TemporaryDirectory() as staging:
        cdb = Path(staging) / "cache_db"
        cdb.mkdir()
        for name, content in files.items():
            (cdb / name).write_text(content, encoding="utf-8")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(cdb, arcname="cache_db")


def test_restore_roundtrip(tmp_path):
    archive = tmp_path / "cache_db-20260704-050000.tar.gz"
    _make_archive(archive, {"chat_history.db": "ORIGINAL", "meta.txt": "v1"})

    dest = tmp_path / "cache_db"
    dest.mkdir()
    (dest / "chat_history.db").write_text("CORUPT", encoding="utf-8")

    msg = o._restore_cache_db(archive, dest=dest)
    assert "restaurat" in msg
    assert (dest / "chat_history.db").read_text(encoding="utf-8") == "ORIGINAL"
    assert (dest / "meta.txt").read_text(encoding="utf-8") == "v1"


def test_restore_keeps_safety_copy(tmp_path):
    archive = tmp_path / "cache_db-20260704-050000.tar.gz"
    _make_archive(archive, {"chat_history.db": "NEW"})

    dest = tmp_path / "cache_db"
    dest.mkdir()
    (dest / "chat_history.db").write_text("OLD", encoding="utf-8")

    o._restore_cache_db(archive, dest=dest)
    # plasa de siguranță cu datele vechi trebuie să existe
    safety = list(tmp_path.glob("cache_db.pre-restore-*"))
    assert len(safety) == 1
    assert (safety[0] / "chat_history.db").read_text(encoding="utf-8") == "OLD"


def test_restore_ignores_config_member(tmp_path):
    """WP-B: o arhivă cu kage_config.json la rădăcină restaurează cache_db normal,
    iar config-ul rămâne extractabil separat (nu suprascris automat)."""
    import tempfile
    archive = tmp_path / "cache_db-20260705-050000.tar.gz"
    with tempfile.TemporaryDirectory() as staging:
        cdb = Path(staging) / "cache_db"
        cdb.mkdir()
        (cdb / "chat_history.db").write_text("DATA", encoding="utf-8")
        cfg = Path(staging) / "kage_config.json"
        cfg.write_text('{"api_token": "SECRET"}', encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(cdb, arcname="cache_db")
            tar.add(cfg, arcname="kage_config.json")

    dest = tmp_path / "cache_db"
    msg = o._restore_cache_db(archive, dest=dest)
    assert "restaurat" in msg
    assert (dest / "chat_history.db").read_text(encoding="utf-8") == "DATA"
    # config-ul NU e restaurat automat peste dest (rămâne pas manual)
    assert not (dest / "kage_config.json").exists()
    # ...dar e prezent în arhivă pentru extragere manuală
    assert "kage_config.json" in tarfile.open(archive).getnames()


def test_restore_missing_archive(tmp_path):
    with pytest.raises(FileNotFoundError):
        o._restore_cache_db(tmp_path / "nope.tar.gz", dest=tmp_path / "cache_db")


def test_restore_invalid_archive(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        pass  # arhivă goală, fără cache_db/
    with pytest.raises(ValueError):
        o._restore_cache_db(bad, dest=tmp_path / "cache_db")
