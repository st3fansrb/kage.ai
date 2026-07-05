# Prompt pentru Claude Design — „Kage Mission Control"

> Pregătit 04.07.2026. Copy-paste integral în Claude Design (sau orice tool de design AI).
> E input-ul de design pentru WP10 (#15B) din docs/KAGE-HANDOFF.md — design-ul se face ÎNAINTEA
> implementării, dar implementarea depinde de WP1 + WP8 (run ledger).
>
> **Stare 05.07.2026 — v1 livrat, direcția = implementare corectă.** Exportul din Claude
> Design (`design/kage-mission-control-handoff.zip`) e v1: design system (paletă dark caldă
> `#0b0907`/`#14110d`, text `#ece9e4`→`#948d82`, accent cyan `#57c4bb` + orange `#ff6a30`;
> Space Grotesk + JetBrains Mono) + **panourile 1–6 de mai jos**. **Neacoperite** (apărute în
> plan după design): panoul de **trading** (WP-T), vederea de **misiuni** cu checklist +
> puntea de decizii `type: question` (WP11), și **inbox-ul interactiv de joburi** multi-profil
> (WP-J — acum doar card de briefing read-only). Nu se redesenează acum: se extind ca pasă
> separată când backend-urile lor există (`trading.db`, formatul misiunilor, run ledger), ca
> să nu proiectăm împotriva unor date inexistente. Extensia = prompt scurt DOAR pentru cele 3
> suprafețe, refolosind explicit tokenii de mai sus.

---

Proiectez interfața unui sistem personal de orchestrare AI numit **Kage** („umbră" în
japoneză). Vreau un **mission control**, nu un chat.

**Context — ce e sistemul:** Kage e un orchestrator self-hosted care rutează fiecare cerere
către modelul potrivit pe 6 tier-uri (T1–T2 = Qwen local, T3 = Claude Haiku, T4 = Gemini,
T5 = Claude Sonnet, T6 = Claude Opus), cu cache semantic, buget zilnic de apeluri cloud,
memorie pe termen lung, agenți autonomi de fundal (rulați cu Claude/Gemini CLI, cu tool
calls vizibile) și un gate de aprobare pentru operațiile riscante (aprob de pe telefon).
Interfața actuală e un chat dark cu accente roșu→cyan și un badge animat de tier — elemente
de identitate care pot fi păstrate/evoluate.

**Obiectiv dual, în ordinea asta:** (1) funcțional pentru uz zilnic — supervizez agenți,
aprob operații, urmăresc costuri; (2) impresionant la prezentări și interviuri — să
transmită „sistem serios de orchestrare", nu „încă un wrapper de chat".

**Panourile necesare (6):**

1. **Command view (home):** activity stream live al tuturor rulărilor — chat, taskuri,
   agenți programați — cu evenimentele fiecărei rulări în timp real (decizie de rutare cu
   confidence, cache hit/miss, tool calls, text streaming, cost), filtrabil pe
   tip/canal/tier/status.
2. **Agent cards:** fiecare agent activ = un card live: nume task, status
   (running / pending approval / done / failed), ultimul eveniment, durată, cost parțial,
   buton de stop. Starea „pending approval" trebuie să fie imposibil de ratat vizual.
3. **Approvals inbox:** operațiile care așteaptă aprobare — comanda exactă, motivul
   clasificării de risc, timpul rămas — cu approve/deny dintr-un singur tap, utilizabil
   pe telefon.
4. **Cost & budget:** bugetul cloud zilnic (apeluri și $), consum defalcat pe tier-uri,
   cache hit rate, trend pe 7/30 de zile.
5. **Briefings:** carduri cu rapoartele agenților programați (știri zilnice, scanări de
   joburi, date) — Markdown randat, timestamp, sursă (care agent le-a produs).
6. **Chat:** rămâne, dar ca panou/mod secundar, nu centrul aplicației; păstrează badge-ul
   de tier (T1–T6) ca element de identitate vizuală.

**Datele disponibile din backend** (ca design-ul să fie onest cu realitatea): un stream SSE
de evenimente tipate per rulare (routing decision + confidence, cache lookup, memorie
injectată, tool_call/tool_result, deltas de text, cost, status final), snapshot de
statistici la 10s, listă de aprobări pending. Nu inventa date care nu există (ex: nu am
metrici de GPU per agent).

**Direcția estetică:** dark-first, „ops room" — dens dar lizibil, ierarhie tipografică
clară; accentele roșu→cyan din identitatea existentă pot evolua; animațiile sunt
funcționale (pulse pe agenți activi, indicatori de streaming, tranziții de status), nu
decorative. Desktop-first (layout pe 2–3 coloane), dar approvals + status trebuie să
funcționeze impecabil pe telefon. Motivul „umbră/kage" poate apărea subtil în identitate.

**De evitat:** dashboard generic de admin (stil Bootstrap/AdminLTE), clonă de ChatGPT,
sci-fi HUD ilizibil. Wow-ul trebuie să vină din senzația de **sistem viu** — date care
curg, agenți care lucrează — nu din ornamente.

**Livrabile:** (1) direcția vizuală — paletă, tipografie, spacing tokens; (2) layout-urile
celor 6 panouri pe desktop + varianta mobilă pentru approvals și agent status; (3) design
detaliat pentru componentele cheie: agent card (toate stările), un rând de eveniment din
activity stream, approval card, tier badge redesenat (T1–T6 + starea CACHE).

**Stack-ul de implementare țintă** (ca design-ul să fie realizabil): React/Next.js +
Tailwind; componentele se mapează pe un stream de evenimente tipate (protocolul AG-UI).
