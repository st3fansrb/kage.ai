# RESTORE — recuperarea datelor Kage

Kage face zilnic (05:00) un backup al `cache_db/` (SQLite `chat_history.db` +
ChromaDB) în arhive `cache_db-YYYYMMDD-HHMMSS.tar.gz`. În plus, vault-ul
`StefanBrain` e sub git (commit automat zilnic la 03:00), deci fiecare `!save`
al unui agent e reversibil. Acest document e procedura de restore — **testată**
în `tests/test_restore.py`.

## 1. Unde sunt backup-urile

- **cache_db**: `backup_dir` din `kage_config.json` (implicit
  `<vault>/backups/kage/`). Se păstrează ultimele `backup_keep` arhive (7).
- **vault** (`StefanBrain`): istoricul git din directorul vault-ului însuși.

Listează backup-urile de cache_db:

```bash
ls -lt "$(python -c "import orchestrator as o; print(o.BACKUP_DIR)")"/cache_db-*.tar.gz
```

## 2. Restore cache_db (istoric chat, cache, memorie)

> ⚠️ **Oprește orchestratorul întâi** — SQLite și ChromaDB țin fișiere deschise.
> Restore peste un proces viu corupe datele.

```bash
# 1. oprește serviciile
./stop_all.sh          # sau: pkill -f orchestrator.py

# 2. restaurează din cea mai recentă (sau o arhivă anume)
source .venv/bin/activate
python -c "import orchestrator as o; \
  import glob; arch=sorted(glob.glob(str(o.BACKUP_DIR/'cache_db-*.tar.gz')))[-1]; \
  print(o._restore_cache_db(arch))"

# 3. repornește
./start_all.sh
```

`_restore_cache_db()` mută automat `cache_db/` curent în
`cache_db.pre-restore-<timestamp>/` înainte de a extrage arhiva — dacă restore-ul
a fost o greșeală, îl poți inversa mutând directorul înapoi.

Verificare rapidă după restore:

```bash
curl -s localhost:4001/health
sqlite3 cache_db/chat_history.db 'SELECT COUNT(*) FROM messages;'
```

## 3. Restore vault (un `!save` greșit al unui agent)

Vault-ul e un repo git; orice modificare de fișier e reversibilă:

```bash
cd "$(python -c "import orchestrator as o; print(o.VAULT)")"
git log --oneline -10                 # găsește commit-ul dinainte de greșeală
git revert <hash>                     # inversează un commit anume
# sau, pentru a arunca modificări necomise:
git checkout -- <fișier>
```

## 4. Backup manual on-demand

```bash
curl -s -X POST localhost:4001/admin/backup   # cache_db → arhivă nouă
```
