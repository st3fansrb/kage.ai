# Mission: Nocturnal Trading Improvement Loop

Această misiune WP11 automatizează iterațiile nocturne de îmbunătățire a strategiilor. Agentul (Qwen) va genera variante, le va backtesta, și va stoca rezultatele în `trading.db`.

## WP1 — Rulează bucla nocturnă (3 iterații)
- Verifică mediul și rulează scriptul de iterații nocturne.
- Scriptul va genera 3 mutații ale strategiei de bază (SampleStrategy) și le va rula prin `FreqtradeRunner`.
- Toate rezultatele vor fi persistate.
- Un raport Telegram va fi trimis la final.

### Acceptare
- `python -m trading.nocturnal --iterations 3 --mission-id "nocturnal-test"`
- `sqlite3 cache_db/trading.db "SELECT count(*) FROM experiments WHERE mission_id='nocturnal-test';" | grep -q "[1-9]"`
