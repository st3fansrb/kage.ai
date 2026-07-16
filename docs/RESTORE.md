# RESTORE — recuperarea datelor Kage

Kage face zilnic (05:00) un backup al `cache_db/` (SQLite `chat_history.db` +
ChromaDB) în arhive `cache_db-YYYYMMDD-HHMMSS.tar.gz`. Arhiva include și
`kage_config.json` (restore complet dintr-un singur fișier). În plus, vault-ul
`StefanBrain` e sub git (commit automat zilnic la 03:00), deci fiecare `!save`
al unui agent e reversibil. Acest document e procedura de restore — **testată**
în `tests/test_restore.py`.

## 0. Backup off-machine (WP-B)

Un singur laptop = un singur punct de eșec. Kage duce datele în două locuri, pe
naturi diferite (config: `kage_config.json`):

- **Vault → GitHub privat:** setează `vault_git_remote` (URL SSH sau HTTPS cu
  credential helper — **nu** sincroniza `.git` prin iCloud, corupe repo-ul).
  Jobul de 03:00 face `git push` după commit. `.git/config` (cu eventualul token
  din URL) rămâne local — nu ajunge niciodată în arborele împins.
- **Arhive tar.gz → iCloud Drive:** `icloud_backup_dir` (default
  `~/Library/Mobile Documents/com~apple~CloudDocs/KageBackups`). După fiecare
  backup, arhiva se copiază acolo cu aceeași rotație `backup_keep`; macOS
  sincronizează singur. Pe o mașină fără iCloud activ, pasul se sare grațios.
  Token-urile (în `kage_config.json` din arhivă) ajung astfel DOAR în iCloud.

## 1. Unde sunt backup-urile

- **cache_db**: `backup_dir` din `kage_config.json` (implicit
  `<vault>/backups/kage/`) + copie în `icloud_backup_dir`. Se păstrează ultimele
  `backup_keep` arhive (7) în ambele locuri.
- **vault** (`StefanBrain`): istoricul git local + remote-ul `vault_git_remote`.

Listează backup-urile de cache_db:

```bash
ls -lt "$(python -c "import orchestrator as o; print(o.BACKUP_DIR)")"/cache_db-*.tar.gz
```

## 2. Restore cache_db (istoric chat, cache, memorie)

> ⚠️ **Oprește orchestratorul întâi** — SQLite și ChromaDB țin fișiere deschise.
> Restore peste un proces viu corupe datele.

```bash
# 1. oprește serviciile
./scripts/stop_all.sh  # sau: pkill -f orchestrator.py

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

## 2a. Restore Postgres (telemetrie + stare: usage, runs, missions, scheduled_tasks)

Din WP-PG, arhiva conține și `kage.pgdump` (format custom `pg_dump`). Restore:

```bash
# 1. extrage dump-ul din arhivă
tar -xzf cache_db-YYYYMMDD-HHMMSS.tar.gz -C /tmp kage.pgdump

# 2. restaurează peste baza kage (recreează obiectele)
/opt/homebrew/opt/postgresql@16/bin/pg_restore \
  --clean --if-exists --no-owner -d kage /tmp/kage.pgdump
```

Pe o mașină nouă: `brew install postgresql@16`, pornește-l (`start_all.sh` o face),
`createdb kage`, apoi comanda de mai sus. Fără dump, orchestratorul pornește oricum —
schema se recreează goală la startup (`pg_store.ensure_schema`), pierzi doar istoricul.

## 2b. Restore config din arhivă (dezastru complet)

Arhiva conține `kage_config.json` la rădăcină (token-uri, remote, chei). Restore-ul
de cache_db **nu** îl suprascrie automat — extrage-l manual:

```bash
tar -xzf cache_db-YYYYMMDD-HHMMSS.tar.gz -C /destinatie kage_config.json
```

Pe o mașină nouă: instalează Kage (vezi INSTALL.md), extrage `kage_config.json`
din cea mai recentă arhivă din iCloud, apoi restaurează `cache_db/` (pasul 2).

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
