# Missions — modul handoff (WP11)

„Îi dau planul și lucrează singur." O misiune e un `missions/<slug>/mission.md` cu
pachete de lucru (WP-uri) în același format ca `docs/KAGE-HANDOFF.md`. Runner-ul ia
următorul WP nemarcat, rulează o sesiune de agent (Claude Agent SDK / WP9), verifică
criteriile de acceptare, marchează pachetul ✅ + commit, și trece la următorul.

## Format

```markdown
# Mission: <titlu>

## <titlu WP>
- pas 1
- pas 2

### Acceptare
- `pytest -q`            ← criteriu VERIFICABIL (comandă shell între backtick-uri) — runner-ul o rulează, exit 0 = trecut
- criteriu în text liber ← informativ (agentul se auto-verifică)
```

- Un `✅` în antetul unui `## WP` = pachet deja terminat (sărit la reluare) — checklist viu.
- Criteriile între backtick-uri sunt rulate în `cwd`-ul misiunii; toate trebuie să dea exit 0.

## Comenzi (chat / Telegram)

- `!mission start <slug>` — pornește o misiune (o singură misiune activă la un moment dat)
- `!mission status` — checklist-ul live al misiunii curente
- `!mission pause` / `!mission resume` — pauză/reluare
- `!mission stop` — oprește misiunea
- `!mission list` — misiunile recente

## Comportament

- **Stare persistentă:** poziția + statusul WP-urilor trăiesc în SQLite (`missions`,
  `mission_wps`) → un restart reia misiunea din pachetul corect.
- **Puntea de decizii:** dacă un pachet nu trece verificarea, runner-ul întreabă pe
  Telegram (retry / skip / abort). Fără răspuns → misiunea intră în `paused` (nu eșuează).
- **Auto-resume la limită:** la rate-limit Claude, runner-ul parsează ora de reset și
  programează reluarea; fallback retry la 15 min.
- **Anti-sleep:** ține un `caffeinate -s` cât timp misiunea rulează.
- **Kill switch:** `!stop` oprește și misiunea activă.

Vezi `missions/exemplu/mission.md` pentru un smoke test rulabil.
