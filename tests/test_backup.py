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


# ── WP-B: config în arhivă + copie off-machine (iCloud) ───────────────────────

def _common_backup_patches(monkeypatch, tmp_path):
    cache = tmp_path / "cache_db"
    cache.mkdir()
    (cache / "chroma.sqlite3").write_text("fake", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "CACHE_DB_PATH", cache)
    monkeypatch.setattr(orchestrator, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(orchestrator, "BACKUP_KEEP", 7)
    monkeypatch.setattr(orchestrator, "_db_conn", None)
    monkeypatch.setattr(orchestrator, "_notify", lambda *a, **k: None)


async def test_backup_includes_config(monkeypatch, tmp_path):
    _common_backup_patches(monkeypatch, tmp_path)
    cfg = tmp_path / "kage_config.json"
    cfg.write_text('{"api_token": "SECRET"}', encoding="utf-8")
    monkeypatch.setattr(orchestrator, "KAGE_CONFIG_PATH", cfg)
    monkeypatch.setattr(orchestrator, "BACKUP_INCLUDE_CONFIG", True)
    monkeypatch.setattr(orchestrator, "ICLOUD_BACKUP_DIR", None)

    path = await orchestrator._backup_cache_db()
    members = tarfile.open(path).getnames()
    assert any(n.endswith("kage_config.json") for n in members)
    assert any(n.endswith("cache_db/chroma.sqlite3") for n in members)


async def test_backup_copies_to_icloud(monkeypatch, tmp_path):
    _common_backup_patches(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "KAGE_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(orchestrator, "BACKUP_INCLUDE_CONFIG", False)
    icloud = tmp_path / "iCloud" / "KageBackups"
    monkeypatch.setattr(orchestrator, "ICLOUD_BACKUP_DIR", icloud)

    path = await orchestrator._backup_cache_db()
    name = orchestrator.Path(path).name
    assert (icloud / name).exists()  # arhiva apare off-machine


async def test_backup_icloud_disabled(monkeypatch, tmp_path):
    _common_backup_patches(monkeypatch, tmp_path)
    monkeypatch.setattr(orchestrator, "KAGE_CONFIG_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(orchestrator, "BACKUP_INCLUDE_CONFIG", False)
    monkeypatch.setattr(orchestrator, "ICLOUD_BACKUP_DIR", None)

    path = await orchestrator._backup_cache_db()  # nu crapă fără iCloud
    assert path.endswith(".tar.gz")
