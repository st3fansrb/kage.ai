"""Teste pentru backup-ul cache_db — _backup_cache_db (arhivare + rotație)."""
import tarfile

import orchestrator


async def test_backup_creates_archive_with_contents(monkeypatch, tmp_path):
    cache = tmp_path / "cache_db"
    cache.mkdir()
    (cache / "chat_history.db").write_text("fake-sqlite", encoding="utf-8")
    (cache / "chroma.sqlite3").write_text("fake-chroma", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    monkeypatch.setattr(orchestrator, "CACHE_DB_PATH", cache)
    monkeypatch.setattr(orchestrator, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(orchestrator, "BACKUP_KEEP", 7)
    monkeypatch.setattr(orchestrator, "_db_conn", None)  # skip SQLite online backup
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)

    path = await orchestrator._backup_cache_db()
    archive = tmp_path / "backups"
    assert path.endswith(".tar.gz")
    members = tarfile.open(path).getnames()
    assert any(n.endswith("cache_db/chat_history.db") for n in members)
    assert any(n.endswith("cache_db/chroma.sqlite3") for n in members)
    assert len(list(archive.glob("cache_db-*.tar.gz"))) == 1


async def test_backup_rotation_keeps_n(monkeypatch, tmp_path):
    cache = tmp_path / "cache_db"
    cache.mkdir()
    (cache / "x.txt").write_text("x", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # pre-creează 3 arhive vechi (nume sortabil cronologic)
    for stamp in ("20200101-000001", "20200102-000001", "20200103-000001"):
        (backup_dir / f"cache_db-{stamp}.tar.gz").write_text("old", encoding="utf-8")

    monkeypatch.setattr(orchestrator, "CACHE_DB_PATH", cache)
    monkeypatch.setattr(orchestrator, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(orchestrator, "BACKUP_KEEP", 2)
    monkeypatch.setattr(orchestrator, "_db_conn", None)
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)

    await orchestrator._backup_cache_db()
    remaining = sorted(p.name for p in backup_dir.glob("cache_db-*.tar.gz"))
    assert len(remaining) == 2  # rotația păstrează doar ultimele 2
