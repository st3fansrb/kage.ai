# Review adversarial WP-SD

Revizuit: 15.07.2026. Diff evaluat: `e14b06a...feat/wp-sd-self-development`.
Între timp branch-ul a fost integrat în `dev`, deci `dev...feat/wp-sd-self-development`
nu mai expune schimbarea originală.

## Constatări

### Critical — eșecul worktree-ului revine explicit la checkout-ul viu

`orchestrator.py:3902`–`3909` face fallback la `original_cwd` (pentru o misiune Kage
acesta este `PROJECT_ROOT`) şi apoi poate chema `_mission_git_ensure_branch`. Scenariu:
`git worktree add` eșuează din cauza unui worktree stale, a unui lock Git sau a lipsei de
spațiu; misiunea continuă totuși, iar agentul şi verificările lui rulează în checkout-ul din
care servesc procesele live. Exact izolarea care justifică WP-SD dispare, iar un `pytest`,
o modificare de cod sau un restart cerut de misiune concurează cu producția. Acest caz trebuie
să se oprească fail-closed (`paused`/`failed` cu alertă), nu să execute comportamentul vechi.

### High — un director existent cu branch greșit este acceptat şi executat

`orchestrator.py:3674`–`3683` detectează că worktree-ul determinist are branch diferit,
dar loghează avertismentul şi „îl refolosește oricum”. Scenariu: o misiune reluată cu același
slug după o intervenție manuală sau un director reutilizat în `~/.kage-worktrees/<slug>` este
pe `dev`/alt `mission/*`; agentul scrie şi `_mission_mark_and_commit()` comite pe acel branch.
Rezultatul nu mai este atribuibil misiunii, iar un branch comun poate fi modificat accidental.
Refuză calea dacă nu este worktree Git valid pe `mission/<slug>` sau reconstruiește-o doar după
o decizie explicită. Testele acoperă reutilizarea corectă, nu acest mismatch.

### High — verificările misiunii nu au o barieră care să protejeze datele live

`orchestrator.py:3988` execută criteriile de acceptare prin `_mission_verify(...,
effective_cwd)`, însă `cwd` nu limitează căile absolute sau `..`; în config, confinement-ul
este implicit dezactivat. Scenariu: un `mission.md` generat de model conține un test cu o cale
absolută către `~/orchestrator-v2/kage_config.json` sau `cache_db/`; verificarea rulează direct
ca subprocess şi poate citi/scrie starea live, deşi agentul lucrează într-un worktree. Nu există
un test care demonstrează că o verificare de acest fel este refuzată. Rulează verificările într-un
mediu cu config/cache temporare ori validează comanda înainte de execuție.

## Acoperirea criteriilor WP-SD

| Criteriu | Acoperire în diff | Verdict |
|---|---|---|
| Misiunea Kage folosește worktree şi lasă branch-ul viu neschimbat | `tests/test_mission_sd.py:223`–`253` | Parțial: cazul normal este acoperit; fallback-ul Critical îl invalidează la eșec. |
| Același slug se reia fără al doilea worktree | `tests/test_mission_sd.py:73`–`80` | Parțial: reuse corect este testat; branch mismatch nu este respins. |
| Worktree şters la succes, păstrat la eșec | `tests/test_mission_sd.py:121`–`131`, `257`–`274` | Acoperit. |
| Restart mid-misiune | `_mission_resume_on_startup()` relansează starea `running`; seed-ul nu suprascrie progresul | Parțial: există teste pentru seed/reuse, nu un test end-to-end cu restart. |
| Pytest/misiune nu atinge `kage_config.json` sau `cache_db/` live | Nu există izolare a subprocess-ului de verificare | Neacoperit: constatarea High de mai sus. |
