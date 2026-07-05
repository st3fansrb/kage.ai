# Kage — ce este și ce face

**Kage** este un strat personal de orchestrare AI: primește orice cerere și o rutează automat către modelul potrivit — local sau cloud — o pune în cache semantic, îi injectează context și memorie, și blochează operațiile riscante înainte să ruleze.

> Task rapid → Qwen local · Task complex → Claude/Gemini · Operație periculoasă → aprobare obligatorie

Rulează self-hosted pe Mac (FastAPI pe `:4001`), expune un API compatibil OpenAI și o interfață web proprie (`kage.html`). E gândit ca alternativă personală la un chat cloud generic — cu control pe cost, pe date și pe execuție.

---

## Problema pe care o rezolvă

Uneltele AI obișnuite te forțează la un compromis: ori modele locale rapide-dar-limitate, ori modele cloud capabile-dar-scumpe și cu limite. Kage elimină alegerea manuală:

- **rutează automat** fiecare mesaj pe tier-ul potrivit, în funcție de complexitate;
- **protejează bugetul** cloud (limită zilnică cu fallback pe local);
- **ține operațiile periculoase sub control** (gate de risc cu aprobare);
- **rămâne al tău** — datele, istoricul și memoria stau local.

---

## Cum funcționează (fluxul unei cereri)

```text
mesaj → [prefix?] → clasificare tier → [cache hit?] → [budget ok?]
      → injectare context + memorie → model (local/cloud) → răspuns (SSE stream)
      → salvare istoric + memorie
```

1. **Clasificare tier** — routing semantic (embeddings `nomic-embed-text` + ChromaDB, <50ms), cu fallback pe Qwen și apoi pe euristică.
2. **Cache semantic** — dacă o întrebare identică/aproape identică a mai fost pusă, răspunsul vine din ChromaDB, fără apel LLM.
3. **Budget** — dacă limita zilnică de apeluri cloud e atinsă, cererea e redirecționată automat pe tier local (T2).
4. **Context + memorie** — se injectează context personal (Obsidian) și fapte relevante din memoria pe termen lung.
5. **Execuție** — răspuns real-time prin streaming (SSE), cu badge de tier.
6. **Persistență** — conversația se salvează în SQLite; perechile relevante intră în memoria vectorială.

---

## Cele 6 tier-uri de rutare

| Tier | Model | Când se folosește |
|------|-------|-------------------|
| **T1** | Qwen 3 8B (local) | întrebări scurte, rezumate, definiții |
| **T2** | Qwen 3.6 35B (local) | cod, raționament, context personal |
| **T3** | Claude Haiku | taskuri moderate, context-aware |
| **T4** | Gemini | multimodal, context mare |
| **T5** | Claude Sonnet | analiză complexă, scriere, planificare |
| **T6** | Claude Opus | decizii critice, calitate maximă |

Tier-ul se poate forța din prefixe (vezi mai jos). Clasificarea semantică **învață** din
forțări: un prefix explicit devine exemplu în colecția de routing, iar un mesaj similar
ulterior e rutat la același tier (vot ponderat pe 5 vecini).

---

## Funcționalități

### Rutare & performanță
- **Routing semantic** — clasificare pe similaritate de embeddings, cu fallback în cascadă (semantic → Qwen → euristic).
- **Cache semantic** — ChromaDB, prag cosinus configurabil (0.92), TTL 24h, vacuum zilnic la 04:00.
- **Context compaction** — sliding window pe conversații lungi (tiers 1-2), cu sumarizare opțională, ca să nu depășească limita modelului.

### Memorie & context
- **Long-term memory vector** — colecție ChromaDB `long_term_memory`: fapte și preferințe din conversații anterioare, cu dedup, reinjectate automat în prompt.
- **Context personal (Obsidian)** — injectare din vault-ul `StefanBrain`.
- **Sesiuni persistente** — istoric SQLite per `session_id`, supraviețuiește reload-ului.

### Siguranță & control
- **Risk gate 3-axis** — fiecare tool call (Bash, operații pe fișiere) al agentului e evaluat pe 3 axe; operațiile riscante cer aprobare explicită în UI/telefon.
- **Workspace confinement** — agenții `!run`/`!sysrun`/`!swarm` sunt limitați la folderele permise (`allowed_task_roots`); execuția în afara lor e blocată automat.
- **Budget zilnic** — limită hard de apeluri cloud/zi (default 20), alertă la 80%, fallback pe local la depășire.
- **Auth API** — token în middleware FastAPI.

### Agenți autonomi
- **`!run`** — lansează un agent de fundal (Claude sau Gemini CLI) care poate citi/scrie fișiere și rula comenzi.
- **`!swarm`** — Claude + Gemini în paralel pe același task.
- **`!sysrun`** — agent care lucrează pe codul sursă al orchestratorului însuși.

### Canale & notificări
- **Push ntfy.sh + Tailscale** — aprobări de risc și alerte pe telefon.
- **Telegram Bot Gateway** — canal bidirecțional, aprobare risc inline din chat.
- **Widget macOS** — buget și status în menubar (rumps).

### Interfață
- **`kage.html`** — UI proprie: streaming SSE, badge de tier animat, chips de prefix, panou de aprobări pending, URL relativ (pregătit pentru acces remote).
- **Dashboard** — statistici live (polling), tab de taskuri programate.

### Automatizare & operare
- **Scheduled tasks** — `!schedule "0 9 * * 1" <task>` rulează taskuri recurente prin cron (APScheduler).
- **Backup zilnic** — arhivă `tar.gz` a `cache_db/` (SQLite + ChromaDB) la 05:00, cu rotație; trigger manual prin `POST /admin/backup`.
- **Teste** — suită pytest pe logica de rutare, buget, compaction, confinement, memorie și backup.

---

## Prefixe de chat

| Prefix | Efect |
|--------|-------|
| `!fast` | forțează T1 (local, rapid) |
| `!best` | forțează T5 (Sonnet) |
| `!opus` | forțează T6 (Opus) |
| `!gemini` | forțează T4 (Gemini) |
| `escaladează` | forțează T5 |
| `!plan` | minim T2, mod planificare |
| `!retry` | reîncearcă pe tier +1 (până la T6) |
| `!save <path> <msg>` | scrie output-ul în Obsidian |
| `!nocache` | ignoră cache-ul semantic |
| `!schedule "CRON" <msg>` | programează task recurent |
| `!run` / `!swarm` / `!sysrun` | agent de fundal (vezi mai sus) |
| `!status` | snapshot instant (buget, cache, servicii) — fără LLM |
| `!help` | lista prefixelor |

---

## Arhitectură & componente

| Serviciu | Port | Rol |
|----------|------|-----|
| Orchestrator (FastAPI) | 4001 | API OpenAI-compatible, rutare, UI |
| LiteLLM proxy | 4000 | acces unificat la modelele cloud/local |
| Ollama | 11434 | modele locale (Qwen, embeddings) |

**Fișiere cheie:**
- `orchestrator.py` — aplicația principală FastAPI (rutare, cache, memorie, agenți, backup).
- `kage.html` — interfața web.
- `risk_hook.py` — PreToolUse hook cu matricea de risc 3-axis.
- `telegram_gateway.py` — gateway-ul Telegram.
- `status_widget.py` — widget-ul macOS.
- `kage_config.json` — sursa unică de configurare (modele, chei, keywords, `allowed_task_roots`, backup).
- `cache_db/` — ChromaDB (cache semantic, tier routing, memorie) + `chat_history.db` (SQLite).

---

## Stare curentă

Kage e **funcțional și complet operațional pentru uz personal** (Faza 19). Toate componentele core rulează: rutare pe 6 tier-uri, cache, memorie, compaction, gate de risc, confinement, backup, notificări, agenți autonomi și UI proprie.

Planul detaliat (implementat / în lucru / decis conștient că nu se face) e în [ROADMAP.md](ROADMAP.md). Instalarea și configurarea sunt în [INSTALL.md](INSTALL.md) și [README.md](README.md).

---

## Filozofie

- **Self-hosted, GDPR-ready** — datele rămân local, diferențiator față de soluții SaaS.
- **Delegare > execuție manuală** — orchestratorul rulează comenzi și modifică fișiere, cu risc gated.
- **Cost sub control** — bugetul cloud e protejat, iar majoritatea taskurilor merg pe local.
- **Personal acum, extensibil apoi** — direcție de viitor: model „Hub + Agent distribuit” pentru echipe, cu chei cloud proprii per utilizator.
