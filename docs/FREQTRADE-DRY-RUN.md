# Freqtrade dry-run (T1-exec)

Acest daemon este strict paper-only: configurația are `dry_run: true`, nu conține
chei de exchange, iar supervisorul refuză să pornească dacă garda `assert_paper_only`
nu trece. Nu există autostart și nu există cale de promovare live.

```bash
bash scripts/setup_trading.sh
bash scripts/start_freqtrade_dryrun.sh
bash scripts/stop_freqtrade_dryrun.sh
```

La prima pornire, strategia tracked `trading/SampleStrategy_example.py` este copiată
în `trading/ft_userdata/strategies/SampleStrategy.py` (gitignored). Strategia aplică
`bias_allows("long")` înainte de orice intrare.

Logul este `.logs/freqtrade-dryrun.log`, iar PID-ul `.logs/freqtrade-dryrun.pid`.
Heartbeat-ul, equity curve şi tranzacțiile oglindite din SQLite-ul dry-run Freqtrade se văd
în `cache_db/trading.db`:

```sql
SELECT * FROM agent_status WHERE agent = 'crypto-freqtrade';
SELECT * FROM equity_snapshots WHERE source = 'freqtrade' ORDER BY id DESC LIMIT 20;
SELECT * FROM paper_trades ORDER BY id DESC LIMIT 20;
```

Înainte de a considera paper period-ul început, verifică explicit că `agent_status`
primește heartbeat-uri; procesul nu este persistent prin launchd în această felie.
