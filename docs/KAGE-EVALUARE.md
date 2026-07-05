# Kage — Evaluare tehnică (3 iulie 2026)

## Rezumat executiv

Kage este un **dispatcher de chat multi-model cu guardrails**, nu încă un orchestrator agentic: clasificare pe tier (embeddings 1-NN → Qwen → euristică), cache semantic, buget cloud, memorie vectorială — iar tot ce e „agentic" e delegat unor subprocese `claude -p`/`gemini -p` one-shot, fără buclă proprie de planificare, tool-use sau evaluare. Partea de infrastructură defensivă (fallback-uri în cascadă, circuit breaker, budget downgrade) e genuin bună. Problema centrală: **fluxul principal de chat e stricat în producție din 8 iunie** (NameError pe `re`, confirmat în log), gateway-ul Telegram nu a livrat niciodată un mesaj (incompatibilitate SSE/JSON cu propriul server), iar mai multe feature-uri promise în documentație nu corespund codului (risk gate „3-axis" cu o axă moartă, confinement care blochează implicit `!run` din UI, T4/T6 practic inaccesibile). Cele 28 de teste trec pentru că testează funcții izolate, nu calea HTTP — exact golul prin care a trecut bug-ul critic. Recomandarea pe scurt: un weekend de reparat fundația + siguranța (itemii 1–2 din secțiunea 4), apoi migrarea agenților pe Claude Agent SDK și un strat de observabilitate — astea transformă proiectul din „router cu memorie" în orchestrator demonstrabil la interviu.

---

## 1. Diagnostic

### Critice

#### D1. Chat-ul principal returnează 500 din 8 iunie — `NameError: name 're' is not defined`
- **Unde:** `orchestrator.py:929`, în `chat_completions()` — `re.search(r"!save\s+(\S+)", ...)`. Modulul `re` nu e importat la nivel de modul; există doar importuri locale `import re as _re` (liniile 372, 960), care leagă numele `_re`, nu `re`.
- **Dovadă:** `.logs/orchestrator.log` — trei `POST /v1/chat/completions → 500` cu traceback `NameError: name 're' is not defined` pe 8 iunie (15:15, 15:47); zero cereri de chat reușite după acea dată, deși serverul rulează și azi (polling Telegram activ). Bug introdus la refactorul `!save <path>` (log-urile din 7 iunie arată formatul vechi `save=False`).
- **Simptom:** orice mesaj care nu e comandă instant (`!status`/`!help`/…) și nu nimerește cache-ul → „Connection error" în UI, „HTTP 500" pe Telegram.
- **Fix (15 minute):**
  1. Adaugă `import re` în blocul de importuri de sus.
  2. Șterge cele două `import re as _re` locale și înlocuiește `_re.` cu `re.`.
  3. Vezi D3 — fără un smoke test HTTP, următorul refactor reintroduce clasa asta de bug.

#### D2. `stream: false` e ignorat — API-ul „OpenAI-compatible" nu e, și propriul gateway Telegram pică pe asta
- **Unde:** `chat_completions()` (`orchestrator.py:876`) returnează **întotdeauna** `StreamingResponse` SSE, indiferent de `body["stream"]`. `telegram_gateway.py:224` (`_forward_to_orchestrator`) trimite `"stream": False` și face `resp.json()` → `JSONDecodeError` pe corp SSE.
- **Dovadă:** în tot log-ul: ~22.000 de `getUpdates`, **zero** `sendMessage` — botul nu a trimis niciodată nimic (nici notificări, nici răspunsuri).
- **Simptom:** chiar și cu D1 reparat, orice mesaj Telegram s-ar termina în „Eroare la comunicare cu orchestratorul".
- **Fix:**
  1. În `chat_completions`, la final: dacă `body.get("stream") is False`, consumă generatorul intern și returnează un obiect JSON standard (`{"choices":[{"message":{"role":"assistant","content":...}}], ...}`).
  2. Test: apel non-stream prin `TestClient` care verifică schema JSON.

#### D3. Suita de teste și alerting-ul nu acoperă exact stratul care s-a rupt
- **Unde:** `tests/` (28 de teste, toate pe funcții pure: `_heuristic_classify`, `_budget_check`, `_compact_messages`…); `ROADMAP.md` recunoaște onest golul („Integration test end-to-end… Mai rămâne"). În paralel, sistemul notifică pe ntfy/Telegram bugetul, backup-urile, escalările — dar **nu și excepțiile neprinse** din endpoint-uri: 500-urile din D1 au fost complet silențioase.
- **Simptom:** o lună de producție moartă cu 28 de teste verzi și niciun alert.
- **Fix:**
  1. `tests/test_e2e.py`: `fastapi.testclient.TestClient` + `respx` pentru mock pe LiteLLM/Ollama; un test care POST-ează un mesaj normal și verifică 200 + SSE parsabil; unul pentru `stream: false`.
  2. Exception handler global FastAPI (`@app.exception_handler(Exception)`) care apelează `_notify("💥 Eroare internă", ...)` — sistemul are deja toată infrastructura de notificare, lipsește doar acest fir.

### Medii

#### D4. Confinement-ul (Faza 19, necomis) blochează implicit orice `!run` din UI
- **Unde:** `task_run()` (`orchestrator.py:796`) — `cwd = body.get("cwd", str(Path.home()))`; `kage.html:703` (`handleTaskRun`) trimite doar `{task}`, fără `cwd`. Config-ul real are `allowed_task_roots = [~/orchestrator-v2, ~/Documents/StefanBrain]`, iar home-ul nu e în listă și nu e părinte acceptat (`_validate_task_cwd` cere ca `cwd` să fie *în interiorul* unui root).
- **Simptom:** orice `!run`/`!swarm` din UI → `[BLOCKED] cwd ... în afara workspace-ului permis`. Doar `!sysrun` mai merge (cwd forțat la `PROJECT_ROOT`).
- **În plus,** documentația supralicitează: `DESPRE_KAGE.md` zice „agenții sunt limitați la folderele permise", dar validarea e doar pe cwd-ul de pornire — agentul poate opera oriunde după start (limitat doar de pattern-urile din risk hook, care nu știu de `allowed_task_roots`).
- **Fix:**
  1. Server: default `cwd` = primul element din `ALLOWED_TASK_ROOTS` (dacă lista e nevidă), nu `Path.home()`.
  2. Endpoint `GET /api/config` care expune `allowed_task_roots`; în `kage.html`, un selector de folder lângă câmpul de task.
  3. Aliniere doc↔cod: fie documentezi onest „confinement pe directorul de pornire", fie propagi rădăcinile în `risk_hook.py` (verifică `file_path`/`cd` față de rooturi) ca să fie adevărată propoziția din doc.

#### D5. „Streaming-ul" pe tiers 3–6 e simulat — și timeout-ul de 120s ucide task-urile legitime
- **Unde:** `_route_claude_autonomous()` (`orchestrator.py:2533`) și `_generate_cli_chunks()` așteaptă evenimente `content_block_delta`, pe care `claude -p --output-format stream-json` **nu** le emite fără flag-ul `--include-partial-messages` (verificat pe CLI-ul instalat, 2.1.173). Rezultatul vine doar în evenimentul final `result` → `has_streamed` rămâne `False` → textul e redat „chunked" abia la final.
- **Simptom:** „connecting…" mort 30–120 de secunde, apoi tot răspunsul dintr-o dată; orice task care depășește 120s (deadline fix la linia 2596) e terminat cu `[Timeout 120s — task oprit]` chiar dacă progresa normal.
- **Fix:**
  1. Adaugă `--include-partial-messages` în `cmd` și tratează evenimentele `type: "stream_event"` (care împachetează `content_block_delta`).
  2. Fă deadline-ul *rulant*: resetează-l la fiecare eveniment primit (inactivity timeout), nu de la start.

#### D6. Risk gate-ul nu face ce spune documentația: o axă moartă, High = refuz automat, și un pattern care blochează comenzi banale
- **Unde:** `risk_hook.py`:
  - `evaluate_risk()` (linia 174) primește `user_message` și există `EXPLICIT_KEYWORDS` (linia 103) — **niciuna nu e folosită nicăieri**. „Axa 2: instrucție explicită" din docstring și README e fictivă.
  - `main()` (linia 284): `Never` **și** `High` → `deny` direct, fără aprobare. README: „High-risk calls are blocked until you approve them in the Kage UI" — fals; doar `Medium` + `autonomous_mode` intră în fluxul de aprobare.
  - `HIGH_CMD_PATTERNS` conține `>\s*/dev/null` (linia 91) → orice comandă cu `2>/dev/null` (idiom banal) e clasificată High și **refuzată automat**. `git rebase` și `git commit --amend` sunt `Never` — excesiv pentru un tool de dezvoltare.
- **Simptom:** agenții eșuează pe comenzi inofensive cu mesaje `[BLOCAT]`, iar tu nu primești niciodată cererea de aprobare promisă pentru operațiile High.
- **Fix:**
  1. Mută `High` în fluxul de aprobare (același mecanism ca `Medium`: register + ntfy/Telegram + poll, timeout → deny). `Never` rămâne refuz direct.
  2. Implementează axa 2: dacă un `EXPLICIT_KEYWORD` relevant apare în `ORCHESTRATOR_USER_MSG`, downgrade High→Medium (userul a cerut explicit operația).
  3. Scoate `>\s*/dev/null` din HIGH; mută `git rebase`/`--amend` din Never în High.
  4. Testele există deja ca pattern (`tests/`) — adaugă `tests/test_risk.py` pe `evaluate_risk` cu cazurile de mai sus.

#### D7. Chat și agent sunt conflate: orice mesaj T3+ devine agent autonom cu Bash pe mașină
- **Unde:** `_route_claude_autonomous()` — fiecare mesaj de chat rutat la T3/T5/T6 pornește `claude` cu `--allowedTools Bash,Read,Write,Edit,...` și `--permission-mode auto`; system prompt-ul (`_build_system_prompt`, tier ≥3) chiar anunță „Mod: Autonom".
- **De ce e problemă (de design, nu de securitate):** un „scrie un email formal" clasificat T3 primește capabilitatea de a rula comenzi — latență mai mare (agentul poate decide să exploreze), comportament surprinzător, și imposibilitatea de a raționa despre ce e „doar chat" în observabilitate. Costul unei greșeli de rutare devine execuție de cod, nu doar un model mai scump.
- **Fix:** separă modurile: chat implicit fără tools (`--allowedTools ""` sau omite flag-ul), tools doar la cerere (`!agent <msg>` sau detecție de intenție), păstrând `!run`/`!sysrun` ca poartă explicită pentru autonomie.

#### D8. Cache-ul semantic ignoră contextul conversației
- **Unde:** `chat_completions()` — `cache_query = last_user.lower()...` (linia 900): cheia de cache e **doar ultimul mesaj**, fără istoricul conversației; `_cache_lookup`/`_cache_store_async` nu știu de `session_id` sau de numărul de ture.
- **Simptom:** follow-up-uri de tip „continuă", „explică mai mult", „și punctul 2?" — aproape identice ca embedding între conversații diferite — pot primi răspunsul altei conversații, servit cu badge de CACHE. Răspunsuri dependente de timp („ce am azi pe agendă") sunt cachate 24h.
- **Fix:**
  1. Sari peste cache (lookup **și** store) dacă conversația are mai mult de o tură de user.
  2. Nu stoca răspunsuri pentru mesaje cu referenți temporali (regex simplu: `azi|acum|mâine|ieri|astăzi`).
  3. Curăță prefixele (`!best`, `!plan`) din `cache_query` înainte de embedding — acum intră în cheie.

#### D9. Istoricul persistent pierde exact cazurile pentru care există
- **Unde:**
  - Cache hit: `chat_completions` returnează la linia 909, **înainte** de INSERT-ul mesajului user (linia 946) — schimburile din cache nu ajung niciodată în SQLite, deci dispar la reload.
  - Disconnect: `history_caching_gen()` (linia 963) salvează doar după ce clientul consumă tot stream-ul; dacă telefonul blochează ecranul mid-stream, generatorul e anulat și răspunsul se pierde — fix scenariul remote care e rațiunea de a fi a proiectului. (`/task/run` rezolvă asta corect, cu task de fundal + coadă.)
- **Fix:**
  1. La cache hit, inserează perechea user/assistant înainte de return.
  2. Aplică pattern-ul din `/task/run` și la chat: consumă LLM-ul într-un `asyncio.create_task` care scrie în coadă + salvează la final, iar handlerul HTTP doar drenează coada.

#### D10. Tier 4 și Tier 6 sunt de facto inaccesibile — tabelul cu 6 tiere e marketing
- **Unde:** `TIER_EXAMPLES` (linia 478) are chei doar pentru {1, 2, 3, 5} → `_semantic_classify` (1-NN pe aceste exemple) nu poate returna niciodată 4 sau 6; `!retry` face `min(last_tier + 1, 5)` (linia 1031); `!best` = 5. Singura cale spre T4/T6: fallback-ul Qwen (rar, doar când semantic pică) sau `tier_override` la taskuri programate.
- **Simptom:** Opus și Gemini nu sunt folosite practic niciodată, deși apar în README, dashboard și config.
- **Fix:** ori adaugi exemple seed pentru 4 și 6 + prefixe explicite (`!opus`, `!gemini`) și ridici capul lui `!retry` la 6, ori tai onest la 4 tiere — ambele sunt mai bune decât starea curentă.

#### D11. Bugetul are găuri exact unde e scump
- **Unde:** `_budget_check` e apelat doar în `chat_completions` și doar pentru `not forced` (linia 919). Ocolesc gate-ul: (a) `!best`/`escaladează` (forced), (b) **`/task/run` în întregime** — agenții `!run`/`!swarm`, cele mai lungi și scumpe rulări, nu sunt limitați, (c) fallback-ul LiteLLM→T3 (`_route_litellm`, linia 2336): dacă Ollama/LiteLLM cade, fiecare cerere escaladează silențios pe cloud, ocolind bugetul. Contorizarea e pe *număr de apeluri*, nu pe cost — un `!run` de 10 minute = 1 apel, cât un „salut" pe Haiku.
- **Fix:**
  1. Apelează `_budget_check` și în `task_run()` (refuz politicos cu buton de override) și în ramura de fallback din `_route_litellm`.
  2. Evenimentul `result` din `claude -p` conține `total_cost_usd` — parsează-l în `_background_task_exec`/`_route_claude_autonomous`, loghează-l în `usage_log`, și treci bugetul pe $/zi. (Vezi și itemul 7 din secțiunea 4.)

#### D12. „Long-term memory" stochează snippets brute, nu fapte
- **Unde:** `_memory_store()` (linia 1319): documentul = `user_msg[:300] + assistant_msg[:300]` — perechi Q/A trunchiate arbitrar, nu fapte extrase; dedup doar `where={"session_id": ...}` → același conținut din sesiuni diferite se duplică; `_memory_retrieve()` caută global (corect pentru „long-term", dar amplifică duplicatele). Rezultatul injectat în system prompt ca „Memorie relevantă" e zgomot: fragmente de răspunsuri vechi tăiate la 300 de caractere.
- **Simptom:** modelele primesc context confuz; memoria crește liniar cu conversațiile fără consolidare.
- **Fix:** vezi itemul 6 din secțiunea 4 (extracție de fapte + consolidare nocturnă) — reparația punctuală minimă: dedup global (scoate `where`) și stochează doar mesajul userului + un rezumat de 1 propoziție, nu răspunsul brut.

#### D13. Logica de comenzi e ruptă între client și server — Telegram promite ce nu poate livra
- **Unde:** `!run`/`!sysrun`/`!swarm` sunt interceptate **doar client-side** în `kage.html:608` și trimise la `/task/run`; `chat_completions` nu are handler pentru ele. `telegram_gateway.py:160` le listează în `/help`, dar le forwardează la `/v1/chat/completions`, unde devin text obișnuit clasificat pe tiere.
- **Simptom:** `!run fă X` de pe telefon nu pornește niciun agent — primești un răspuns conversațional de la un model care a citit literal string-ul „!run fă X".
- **Fix:** mută detecția prefixelor agentice în server (în `chat_completions`, înainte de cache: dacă mesajul începe cu `!run|!swarm|!sysrun`, deleagă la logica din `task_run()`). Orice canal viitor (Cloudflare, MCP) moștenește atunci automat toate capabilitățile.

#### D14. Date personale hardcodate în cod, într-un repo poziționat ca portofoliu
- **Unde:** `_STEFAN_BASE` (linia 2174) — nume, facultate, angajator, proiecte în system prompt hardcodat; `_get_obsidian_context()` (linia 2155) — `project_map` cu „frigo/aumovio/itecify" în cod; `TIER_EXAMPLES` conține „ce am lucrat la aumovio".
- **De ce contează:** pentru CV, repo-ul public își expune autorul la nivel de program şi e neconfigurabil pentru oricine altcineva — anti-semnalul exact al unui proiect „extensibil".
- **Fix:** `persona.md` + `projects_map` în `kage_config.json` / vault; `TIER_EXAMPLES` în config cu default generic. Efort: o seară.

### Cosmetice / igienă

#### D15. Ops & întreținere (grupate)
- `usage_log.jsonl` nu e rotit niciodată, iar `_aggregate_usage()` (linia 1386) citește **tot** fișierul la fiecare poll de 10s al dashboard-ului; `/health` la fel. Fix: mută usage în SQLite (există deja conexiunea) sau rotește lunar.
- Logger-ul `httpx` la INFO scrie URL-uri complete cu **token-ul de bot Telegram** în `.logs/orchestrator.log` la fiecare `getUpdates` (30s). Fix: `logging.getLogger("httpx").setLevel(logging.WARNING)` — un rând.
- `_sleep_response()` (linia 1954) folosește `os.system()` blocant în event loop.
- Cod mort: `_build_chat_html()` (~215 linii, UI vechi nefolosit — `/chat` servește `kage.html`), `UNCERTAINTY_PHRASES` (linia 140, vestigiu al unei auto-escaladări abandonate), `MULTI_TENANT_ARCH.md` (ROADMAP zice explicit că poate fi șters). Fix: șterge tot.
- Dublă implementare pentru „add scheduled task" (`_handle_schedule_command` + `POST /schedule action=add`) — unifică într-o funcție.
- Contradicție de design în auth: există `api_token` pe middleware, dar `/chat` e exempt și **servește token-ul injectat în HTML oricui accesează pagina** (`orchestrator.py:763-770`). Pe LAN e irelevant, dar invalidează premisa „prerequisit Cloudflare" din ROADMAP — de rezolvat înainte de expunerea prin tunel (login-ul Zero Trust ar acoperi, dar merită conștientizat că token-ul e azi decorativ).
- `!retry` citește ultimul tier din `status.json` global (linia 1027) — stare partajată între sesiuni: un `!retry` pe telefon escaladează față de ultimul mesaj de pe desktop.

---

## 2. Gap-ul până la un orchestrator agentic serios

Reperul: un orchestrator agentic matur în 2026 înseamnă planificare multi-pas, tool use structurat, retry/recovery la nivel de task, observabilitatea deciziilor și evaluarea rezultatelor. Pe fiecare dimensiune:

### 2.1 Planificare multi-pas — **inexistentă** (salt: mare)
- **Acum:** `decide_tier()` alege un singur model pentru un singur schimb; `!plan` înseamnă doar `max(tier, 2)` (linia 1035) — nu produce un plan. Agenții primesc task-ul brut într-un singur `-p`. `!swarm` (linia 249) nu descompune nimic: rulează *același* task pe doi agenți în paralel și concatenează output-urile în aceeași coadă, fără agregare sau comparație.
- **Ținta rezonabilă (nu enterprise):** un mod plan-then-execute: T5 produce un plan JSON de 2–5 pași, fiecare pas rutat independent (pași ieftini pe T2 local, pași grei pe agent), cu starea planului persistată. Nu îți trebuie graf generic — o listă liniară de pași cu status e 80% din valoare.
- **Dovadă a distanței:** nu există nicăieri o structură de date „task cu pași" — singura stare multi-pas din sistem e coada de chunks `_active_task_queues`.

### 2.2 Tool use structurat — **zero tools proprii** (salt: mediu)
- **Acum:** capabilitățile orchestratorului (salvare în Obsidian, schedule, status) sunt *prefix parsing pe string-uri* în `chat_completions`, nu tools pe care modelul le poate invoca. Tot tool-use-ul real trăiește în interiorul `claude` CLI, opac pentru Kage. Modelele locale T1/T2 nu au acces la niciun tool — deși Ollama/LiteLLM suportă function calling, iar Qwen 3.x e antrenat pentru asta. ROADMAP-ul recunoaște golul („Code-nav Tools pentru Agent").
- **Ținta:** un registru minimal de tools (`save_to_vault`, `read_vault_note`, `schedule_task`, `system_status`) expus prin function calling către T2 — brusc modelul local *face* lucruri în loc să explice cum se fac; același registru expus extern prin MCP mai târziu.

### 2.3 Retry / recovery — **bun la nivel de infrastructură, absent la nivel de task** (salt: mediu)
- **Acum, partea bună (de păstrat și de povestit la interviu):** circuit breaker pe Ollama cu prag de 3 eșecuri (`_qwen_classify`, linia 1117), fallback în cascadă semantic→Qwen→euristic (`_classify`), LiteLLM→CLI cu escaladare de tier (`_route_litellm`, linia 2335), budget downgrade cu avertisment. Asta e inginerie reală.
- **Acum, partea absentă:** la nivel de *task*, eșecul e doar text: `[task error: {e}]` pus în coadă (linia 237), `proc.wait()` fără să se uite la exit code, răspuns gol tratat cu un mesaj de eroare și atât. `!retry` e manual, plafonat la T5 și folosește stare globală.
- **Ținta:** detectarea eșecului (exit code ≠ 0, răspuns gol, timeout) → o reîncercare automată cu escaladare de tier sau, pentru agenți, resume de sesiune (`--resume` există în CLI 2.1.173) cu instrucțiunea „continuă de unde ai rămas".

### 2.4 Observabilitatea deciziilor — **parțială, la nivel de request; zero la nivel de agent** (salt: mediu, cel mai bun ROI)
- **Acum:** `usage_log.jsonl` are tier/model/latență/preview(40 chars); badge-ul UI arată metoda și confidence — onest și peste media proiectelor personale. Dar: *de ce* s-a ales tier-ul (care exemplu seed a câștigat, la ce distanță), ce a ratat cache-ul la 0.91, ce memorie s-a injectat — nu se înregistrează nicăieri. Agentul e o gaură neagră: stdout text concatenat; tool call-urile lui (vizibile în evenimentele `stream-json`!) sunt aruncate — codul păstrează doar `text_delta`. Task-urile de fundal trăiesc în `_active_task_queues` (memorie) — la restart dispar fără urmă.
- **Ținta:** un tabel `runs` + `events` în SQLite: fiecare cerere/task cu id, decizia de rutare completă (metodă, similaritate, vecinul câștigător, starea bugetului), evenimentele agentului (tool calls cu input/output trunchiat), cost, durată, status final. Un tab „Runs" în dashboard. Este diferența vizibilă dintre „am făcut un router" și „am făcut un sistem pe care îl pot depana".

### 2.5 Evaluarea rezultatelor — **zero** (salt: mediu)
- **Acum:** nimic nu verifică output-ul. `UNCERTAINTY_PHRASES` (linia 140) — o listă de fraze de incertitudine pentru auto-escaladare — există dar nu e folosită nicăieri: vestigiul unei idei corecte, abandonată. Nici măcar „răspunsul e gol / procesul a picat" nu declanșează vreo acțiune în afară de afișarea erorii.
- **Ținta minimă:** verificări mecanice (exit code, lungime, prezența pattern-urilor de refuz — lista există deja!) → retry/escaladare; opțional, un judge ieftin pe T2 local („răspunsul acoperă cererea? DA/NU") care alimentează atât retry-ul cât și feedback-ul de rutare. Local = gratis, deci îți permiți să evaluezi tot.

### 2.6 Stare și sesiuni de agent — **fiecare apel cloud e amnezic** (salt: mic ca efort, mare ca valoare)
- **Acum:** fiecare mesaj T3+ = proces `claude -p` proaspăt; contextul e reconstruit manual din **ultimele 6 mesaje × 600 caractere** (`_build_conversation_context`, linia 2267). Inversiune absurdă: T1/T2 locale primesc 20 de mesaje cu compaction inteligent (`_compact_messages`), iar modelele *scumpe și capabile* primesc cel mai puțin context.
- **Ținta:** sesiuni CLI cu `--resume` per `session_id` Kage (map session→claude_session_id în SQLite), sau direct Claude Agent SDK (secțiunea 3).

**Concluzia secțiunii:** Kage e azi un *model router cu guardrails și memorie* — partea de „orchestrare" (decizia cine execută) există și e decentă; partea de „agentic" (cum se execută, se verifică și se reia) e delegată integral și opac. Vestea bună: golurile 2.3–2.6 se închid incremental, fără rescriere.

---

## 3. Cercetare: ce fac framework-urile actuale și ce merită furat

Toate verificate ca active în iulie 2026. Criteriul de selecție: relevanța pentru un sistem single-user, self-hosted, cu 48GB RAM din care ~25GB ținuți de Qwen 35B — deci nu „adoptă framework-ul", ci „fură mecanismul".

### 3.1 Claude Agent SDK (Anthropic) — [docs](https://platform.claude.com/docs/en/agent-sdk/overview) · [GitHub](https://github.com/anthropics/claude-agent-sdk-python)
- **Ce face bine:** e exact harness-ul din Claude Code, expus ca bibliotecă Python: sesiuni persistente cu resume, subagenți cu context propriu, hooks în-proces (PreToolUse/PostToolUse/Stop…), streaming de evenimente structurate, MCP client. Se facturează pe abonamentul Claude existent.
- **De integrat în Kage:** **înlocuirea spawn-ului `claude -p` cu SDK-ul** este cel mai mare salt disponibil pe efort mediu: (a) risk gate-ul devine un hook Python în-proces — dispare lanțul fragil subprocess → hook script → HTTP polling către orchestrator; (b) sesiuni cu resume rezolvă 2.6; (c) evenimentele de tool use alimentează direct observabilitatea din 2.4. Kage rămâne orchestratorul; SDK-ul e doar executorul.
- **Nu se aplică:** subagenții multi-model și execuția hosted — pentru un user, un agent e de ajuns; hosted contrazice self-hosted-ul.

### 3.2 LangGraph (LangChain) — [pagina de framework-uri](https://www.langchain.com/resources/ai-agent-frameworks) · [comparativ 2026](https://tensoria.fr/en/blog/multi-agent-orchestration-comparison)
- **Ce face bine:** stare explicită ca graf cu checkpointing persistent (poți opri/relua/derula înapoi un run), `interrupt()` pentru human-in-the-loop — standardul de facto pentru workflow-uri agentice stateful în producție.
- **De integrat:** **conceptul de checkpoint, nu biblioteca**: rulările de agent Kage ar trebui să fie rânduri în SQLite cu stare reluabilă (pending_approval → approved → running → done/failed), astfel încât un restart de orchestrator sau un telefon deconectat să nu piardă nimic. Aprobarea de risc devine un „interrupt" persistat, nu un `asyncio.Event` în memorie (`pending_risk`, linia 153) care moare la restart.
- **Nu se aplică:** grafuri condiționale generice, LangGraph Platform, ecosistemul LangChain — pentru fluxurile liniare ale unui singur user, graful e overhead; ai lua dependența întregului ecosistem pentru 10% din el.

### 3.3 smolagents (Hugging Face) — [GitHub](https://github.com/huggingface/smolagents) (v1.26.0, mai 2026, ~28k stele) · [blog](https://huggingface.co/blog/smolagents)
- **Ce face bine:** CodeAgent — modelul își scrie acțiunile ca și cod Python executat de un executor controlat, ceea ce dă compozabilitate naturală (bucle, condiții) cu un core de ~1.000 de linii; model-agnostic, inclusiv Ollama.
- **De integrat:** **agent local ieftin pe T2**: Qwen 35B cu un set mic de tools (fișiere din vault, status, schedule) printr-o buclă smolagents-style ar acoperi taskurile mărunte care azi ori ard cloud, ori nu se pot face deloc local — rezolvă 2.2 aproape gratis (smolagents e suficient de mic încât îl poți folosi ca dependență *sau* citi și reimplementa bucla în ~200 de linii). Filosofia „barebones" e și validarea deciziei tale de a nu lua un framework mare.
- **Nu se aplică:** sandbox-urile cloud (E2B/Modal/Docker — ROADMAP-ul tău exclude explicit Docker); hub-ul de partajare. Execuția locală de cod generat rămâne gated de risk gate-ul existent.

### 3.4 Letta (ex-MemGPT) — [GitHub](https://github.com/letta-ai/letta) · [sleep-time compute](https://www.letta.com/blog/sleep-time-compute/) · [memory blocks](https://www.letta.com/blog/memory-blocks/)
- **Ce face bine:** memorie ierarhică gestionată de agent (core/recall/archival) și **sleep-time compute** — agentul primește ture fără input de user în care își reorganizează memoria: consolidează, rescrie, rezumă.
- **De integrat:** **sleep-time compute e făcut pentru setup-ul tău**: Mac-ul stă pornit noaptea și ai deja infrastructura exactă (APScheduler cu vacuum la 04:00 și backup la 05:00). Un job la 03:00 în care Qwen local citește conversațiile zilei din SQLite, extrage fapte/preferințe persistente, deduplichează global și rescrie colecția `long_term_memory` — transformă D12 din defect în feature, cu **zero cost cloud**. Și „memory blocks": un bloc de profil („cine e userul") rescris periodic, injectat mereu, separat de faptele căutabile.
- **Nu se aplică:** Letta ca server separat (încă un serviciu + RAM pe o mașină care ține 25GB de Qwen); memoria gestionată de agent la fiecare tură (latență pe fiecare mesaj — consolidarea nocturnă e tradeoff-ul corect aici).

### 3.5 goose (Block / AAIF, Linux Foundation) — [GitHub](https://github.com/aaif-goose/goose) (~29k stele) · [docs](https://goose-docs.ai/)
- **Ce face bine:** agent local-first (CLI + desktop) cu extensibilitate exclusiv prin MCP (70+ extensii, registry de 3.000+ servere) și **recipes** — YAML-uri versionabile care împachetează un workflow: instrucțiuni, extensii, parametri, subrecipes.
- **De integrat:** **formatul recipe pentru taskurile Kage**: azi `!schedule` stochează un string de mesaj brut în `scheduled_tasks.json`; un `recipes/*.yaml` (instrucțiuni, tier, cwd, tools permise, parametri) referit de `!schedule` și `!run` ar face automatizările repetabile, parametrizabile și diff-uibile în git. Ca dovadă externă de validitate: Block a scalat pattern-ul ăsta la 60% din companie.
- **Nu se aplică:** aplicația desktop și multi-provider switching (LiteLLM face deja); goose ca agent-de-zi-cu-zi ar *înlocui* Kage, nu-l servește — de furat doar formatul.

### 3.6 OpenAI Agents SDK — [docs](https://openai.github.io/openai-agents-python/) · [guardrails](https://openai.github.io/openai-agents-python/guardrails/) · [tracing](https://openai.github.io/openai-agents-python/tracing/)
- **Ce face bine:** primitive puține și clare (Agents/Tools/Handoffs/Guardrails) și **tracing built-in**: fiecare run produce spans pentru generări, tool calls, guardrails — observabilitate ca parte a modelului de programare, nu adaos.
- **De integrat:** două idei de design, nu codul: (a) **guardrails ca funcții tipate pe input și output** — risk gate-ul Kage acționează doar pe tool calls; un output-guardrail (verificarea rezultatului înainte de a ajunge la user — gol? refuz? a divulgat ceva din vault?) e jumătatea lipsă și se leagă direct de 2.5; (b) **schema de trace** (run → spans cu tipuri) e exact ce ar trebui să fie tabelul `events` din itemul de observabilitate.
- **Nu se aplică:** handoffs (n-ai între cine), ecosistemul OpenAI-centric — modelele tale sunt Claude/Gemini/Qwen.

**Sinteza cercetării:** niciun framework nu merită adoptat integral — dar patru mecanisme da: executor pe Claude Agent SDK (3.1), consolidare nocturnă de memorie (3.4), run ledger cu checkpoint/trace (3.2 + 3.6), și tools locale pentru T2 (3.3). Toate patru sunt compatibile cu FastAPI-ul existent și cu constrângerea de RAM.

### Addendum: Hermes Agent, OpenClaw și auto-skill creation

#### 3.7 Hermes Agent (Nous Research) — [GitHub](https://github.com/nousresearch/hermes-agent) · [docs](https://hermes-agent.nousresearch.com/docs/)
- **Ce face bine:** „closed learning loop" — după un task cu 5+ tool calls, un proces de fundal distilează traiectoria într-un fișier skill Markdown (YAML frontmatter), regăsit ulterior prin SQLite FTS5 + sumarizare LLM; skill-urile se rafinează prin folosire; user modeling (Honcho). Claim-ul lor: agenții cu 20+ skill-uri auto-create termină taskuri similare cu ~40% mai repede — dar strict domain-specific.
- **De integrat în Kage:** decizia din ROADMAP („skill creation loop — complexitate nejustificată") a fost corectă pentru bucla *completă*, dar mecanismul de bază e mai ieftin decât pare: „la finalul unui run reușit, un model ieftin rezumă ce a mers într-un fișier Markdown; la task nou, caută skill-uri relevante și injectează-le". Kage are deja regăsirea (ChromaDB), fundalul (APScheduler) și — după itemul #5 — traiectorii structurate din care merită distilat. **Precondiție reală:** fără run ledger + tool events, ai distila dintr-un blob de stdout; de aceea auto-skill vine *după* #4/#5, nu înainte. Și o corecție de design față de Hermes: în filosofia Kage (aprobare pe risc), skill-urile auto-create ar trebui să fie **draft-uri care cer aprobare** (buton pe Telegram), nu învățare complet autonomă — „approval-gated learning" e o poveste mai bună și decât a lor.
- **Nu se aplică:** rafinarea autonomă a skill-urilor în timpul folosirii și user modeling-ul Honcho — pentru un singur user, profilul din memoria v2 (itemul #6) acoperă același rol la o fracțiune din complexitate.

#### 3.8 OpenClaw — [GitHub](https://github.com/openclaw/openclaw) · [docs](https://docs.openclaw.ai/)
- **Ce face bine:** e concurentul direct al lui Kage, și mai matur: gateway self-hosted cu arhitectură pe trei straturi (channel / brain / body — 20+ adaptoare de mesagerie, runtime de agent, tools), skills în format **Agent Skills (SKILL.md)** cu potrivire automată pe descriere (nu invocare explicită), marketplace ClawHub cu Skill Cards și scanare SkillSpector, aplicații mobile native.
- **De spus onest:** dacă scopul ar fi *doar* uzul personal, instalarea OpenClaw ar fi mai rațională decât construirea Kage. Calculul se schimbă din cauza scopului de CV — valoarea Kage e că e al tău — și pentru că Kage are diferențiatori reali pe care OpenClaw nu-i are: tier routing pe modele locale, budget enforcement, risk gate. Merită studiat ca referință de design, nu adoptat.
- **De integrat:** (a) separația channel/brain/body confirmă fixul D13 — toată logica de comenzi în server, canalele doar transportă; (b) **formatul de skills** — vezi 3.9, care înlocuiește formatul de „recipes" propus la itemul #10.
- **Nu se aplică:** cele 20+ canale (folosești Telegram), ClawHub/marketplace, voice și canvas (ROADMAP le exclude deja, corect).

#### 3.9 Standardul Agent Skills (Anthropic, deschis din dec. 2025) — [spec + overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [engineering blog](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- **Ce e:** un skill = un folder cu `SKILL.md` (frontmatter YAML `name` + `description`, instrucțiuni Markdown, opțional scripturi/referințe), încărcat prin **progressive disclosure**: doar name+description la pornire (~30–50 tokeni/skill), instrucțiunile complete la activare, fișierele referite la execuție. Suportat în 2026 de Claude Code, Codex CLI, Gemini CLI, Copilot, Cursor.
- **De ce contează special pentru Kage:** executorul lui Kage *este* Claude Code CLI — deci un folder `skills/` în format standard e preluat de agent aproape gratis (Claude Code încarcă skills din `.claude/skills` în cwd-ul rulării). Partea de construit în Kage e mică: potrivirea skill-urilor pentru tier-ele locale (embedding match pe descriptions — infrastructura există) și un flux `!skill new`. **Asta înlocuiește itemul #10 (recipes YAML custom):** nu inventa un format — adoptă standardul care a câștigat; skill-urile scrise pentru Kage merg în Claude Code și invers, iar interoperabilitatea e un semnal de CV mai bun decât un format propriu.

#### 3.10 Scan OSS Insight (trending AI, iulie 2026) — idei punctuale din repo-uri emergente

Sursă: [ossinsight.io/trending/ai](https://ossinsight.io/trending/ai) (extras prin API-ul public, top 100 pe săptămână + top 100 pe lună). **Avertisment metodologic:** trending-ul OSS Insight favorizează *velocitatea* repo-urilor noi (100–1.700 stele), nu maturitatea — de tratat ca surse de idei și validare de direcție, nu ca dependențe. Ce e relevant pentru Kage, pe teme:

**Eficiență de context/tokens** (direct pe compaction-ul și modelele locale ale lui Kage):
- [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) (★1.4k/lună) — comprimă output-uri de tools, loguri și chunk-uri RAG înainte să ajungă la LLM (60–95% mai puțini tokeni). Idee de furat pentru Kage: comprimă stdout-ul agenților înainte de salvarea în istoric și memoria vectorială, și contextul Obsidian înainte de injecție — T1/T2 au context mic, asta e levier direct.
- [rtk-ai/rtk](https://github.com/rtk-ai/rtk) — proxy CLI care reduce tokenii comenzilor de dev cu 60–90%; interesant ca strat între agentul Claude și shell.

**Plan-then-execute și economia tier-elor** (validează exact teza de la 2.1):
- [shadcn/improve](https://github.com/shadcn/improve) — „modelul cel mai capabil auditează și scrie planuri; modelele ieftine execută" — exact pattern-ul plan-pe-T5 / execută-pe-T2 recomandat pentru Kage, validat public.
- [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files) — skill Claude Code cu planificare persistentă Manus-style în fișiere Markdown. Cel mai ieftin mod de a da agenților Kage planificare multi-pas: instalezi/adaptezi un skill, zero cod în orchestrator.

**Memorie** (alternative de studiat pentru itemul #6):
- [EverMind-AI/EverOS](https://github.com/EverMind-AI/EverOS) — strat de memorie portabil, local-first, Markdown-nativ, user-owned — filozofic cel mai apropiat de Kage (memoria ca fișiere în vault, nu doar embeddings opace).
- [topoteretes/cognee](https://github.com/topoteretes/cognee) și [rohitg00/agentmemory](https://github.com/rohitg00/agentmemory) — platforme de memorie pentru agenți (graf + vector); de citit pentru design, prea grele ca dependență.

**Ecosistemul de skills** (întărește itemul #12):
- [vercel-labs/skills](https://github.com/vercel-labs/skills) — `npx skills`, installer pentru standardul Agent Skills; [google/skills](https://github.com/google/skills) și [anthropics/knowledge-work-plugins](https://github.com/anthropics/knowledge-work-plugins) — biblioteci oficiale: standardul chiar a câștigat, nu e doar marketing Anthropic.
- [NVIDIA/SkillSpector](https://github.com/NVIDIA/SkillSpector) — scanner pentru skill-uri third-party (instrucțiuni ascunse); relevant în ziua în care instalezi skill-uri din ClawHub/GitHub, nu doar ale tale.
- [microsoft/SkillOpt](https://github.com/microsoft/SkillOpt) — optimizare de skill-uri în spațiul textului pentru agenți „frozen" — versiunea riguroasă a buclei Hermes; de citit înainte de a implementa auto-distilarea.
- [virgiliojr94/book-to-skill](https://github.com/virgiliojr94/book-to-skill) — PDF tehnic → skill; aplicabil direct pe StefanBrain.

**Validare de piață pentru nucleul Kage:**
- [workweave/router](https://github.com/workweave/router) — „rutează fiecare prompt către modelul potrivit în <50ms, taie costurile 40–70%" — pitch-ul e literalmente tier routing-ul lui Kage. Confirmă că nucleul proiectului e o categorie reală în 2026; de citit euristicile lor de rutare pentru comparație.
- [stablyai/orca](https://github.com/stablyai/orca), [ogulcancelik/herdr](https://github.com/ogulcancelik/herdr), [omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent), [multica-ai/multica](https://github.com/multica-ai/multica) — val întreg de „orchestrează flote de coding agents cu abonamentul tău": categoria în care intră Kage cu itemii #4+#5; nimic de importat direct, dar run-ledger-ul cu status/assign din multica e aceeași idee ca itemul #5.

**Mac-specific:**
- [apple/container](https://github.com/apple/container) — containere Linux pe VM-uri ușoare, nativ pe macOS — calea de izolare reală a agenților `!run` fără Docker (pe care ROADMAP-ul îl exclude); candidat pentru „confinement v2" ca feature de produs.

**Nu lua:** gateway-urile de „AI gratis nelimitat" ([freellmapi](https://github.com/tashfeenahmed/freellmapi), 9router, sub2api…) — stacking de free-tiers cu ToS dubios; Kage are deja LiteLLM și un buget onest.

#### 3.11 Voice + interfață desktop — stack-ul matur (decizie 03.07.2026: voice intră în scope, răstoarnă „Ce NU facem" din ROADMAP)

Spre deosebire de 3.10, aici criteriul e **maturitatea**: proiecte cu ani de viață, comunități mari, feedback pozitiv susținut.

**STT local (transcriere) — fundația pentru tot ce e voice:**
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) — alegerea recomandată: workhorse-ul fără dependențe, Metal/Core ML pe Apple Silicon, `large-v3-turbo` rulează ~2–3× realtime și ocupă ~1,6GB (încape lejer lângă cei 25GB de Qwen). **Motivul decisiv pentru tine: româna.** Whisper acoperă 99 de limbi robust; alternativele mai rapide pe Mac ([parakeet-mlx](https://github.com/senstella/parakeet-mlx), fluidaudio-coreml — de 2–6× mai rapide în [benchmark-ul mac-whisper-speedtest](https://github.com/anvanvan/mac-whisper-speedtest)) acoperă ~25 de limbi cu accent pe engleză. Pentru voice memos în română, Whisper e pariul sigur; Parakeet e optimizarea de mai târziu dacă dictezi în engleză.
- [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) — biblioteca matură de „lipici": VAD (detectează când vorbești), wake word (Porcupine/openWakeWord), transcriere streaming — exact stratul dintre microfon și orchestrator, ca să nu-l scrii tu.

**Wake word („Hey Jarvis" la propriu):**
- [openWakeWord](https://github.com/dscripka/openWakeWord) — open-source, rulează pe CPU, folosit de ecosistemul Home Assistant; are **model pre-antrenat `hey_jarvis`** și suportă antrenarea unui model custom („Kage") din sample-uri sintetice.

**TTS local (răspunsul vorbit) — atenție la română:**
- [Piper](https://github.com/OHF-Voice/piper1-gpl) — matur, ușor, CPU-only, și **are voci ro_RO** — alegerea corectă pentru răspunsuri în română. Baseline de zero dependențe: `say -v Ioana` (vocea românească nativă din macOS).
- [Kokoro-82M](https://github.com/hexgrad/kokoro) — cel mai lăudat TTS local pe calitate/greutate în 2026 (Apache, 327MB, rulează pe CPU), **dar nu are română** (8 limbi) — folosește-l doar dacă răspunsurile vin în engleză.
- Ce NU îți trebuie acum: [pipecat](https://github.com/pipecat-ai/pipecat) (matur, dar orientat pe telefonie/web transports și conversație full-duplex cu barge-in) — over-engineering pentru un asistent personal push-to-talk; de revizitat doar dacă vrei conversație continuă cu întreruperi.

**Interfața desktop:** Open WebUI a fost evaluat ca opțiune matură „conectezi și gata" și **respins (03.07.2026)** — prea generic pentru obiectivul de prezentare; direcția aleasă e un mission control propriu — vezi 3.12 și itemul #15 revizuit.

**Arhitectura voice recomandată (channel-agnostic, în spiritul fixului D13):** transcrierea trăiește în orchestrator, nu în clienți — un endpoint `POST /v1/audio/transcriptions` (OpenAI-compatible, servit de whisper.cpp) folosit și de gateway-ul Telegram (voice memos), și de widget-ul Mac (push-to-talk), și de frontend-ul din 3.12. Un singur Whisper încărcat, trei canale.

#### 3.12 „Agentic OS" / mission control UIs — direcția pentru frontend-ul Kage (decizie 03.07.2026)

Categoria „mission control peste agenți" a explodat în 2026 — un val întreg de dashboard-uri de comandă, majoritatea construite peste OpenClaw Gateway. Ce există și ce e de furat:

**Referințe de design (de studiat, nu de adoptat — toate tinere, multe cuplate la OpenClaw):**
- [SapienXai/AgentOS](https://github.com/SapienXai/AgentOS) — control plane peste OpenClaw: **canvas de topologie live** (workspace → agent → runtime), dispatch de misiuni, inspecție de runtime cu transcript + token usage. Cel mai apropiat de „wow-ul" cerut.
- [builderz-labs/mission-control](https://github.com/builderz-labs/mission-control) — **activity stream în timp real** peste toți agenții (filtrabil pe tip/agent/timp), kanban cu 6 coloane, monitorizare de spend.
- [MeisnerDan/mission-control](https://github.com/MeisnerDan/mission-control) — task management „agent-first": agenții citesc/scriu taskuri prin API și raportează într-un **inbox** pentru om; matrice de priorități drag-and-drop.
- [Octogent](https://magicshot.ai/news/octogent-claude-code-multi-agent-dashboard) și [Vibe Kanban](https://github.com/BloopAI/vibe-kanban) (acum open source comunitar, după închiderea Bloop) — carduri de sesiuni de agenți în coloane (planning → implementing → validating → done). [opcode](https://github.com/winfunc/opcode) (ex-Claudia, 21k stele, GUI pentru Claude Code cu usage dashboards) — raportat ca nemenținut, doar sursă de idei.

**Stack-ul matur pe care să construiești propriul mission control:**
- [Protocolul AG-UI](https://docs.copilotkit.ai/agentic-protocols/ag-ui) + [CopilotKit](https://github.com/copilotkit/copilotkit) — standardul deschis pentru interacțiunea agent↔UI: stream de evenimente tipate (mesaje, tool calls, **state updates**, generative UI), adoptat de Google, LangChain, AWS (Bedrock AgentCore, mar. 2026), Microsoft, PydanticAI. Exact ce-i trebuie lui Kage: run ledger-ul (#5) emite evenimente AG-UI, frontend-ul React le randează cu componente gata făcute. Trio-ul de protocoale MCP (agent↔tools) / A2A (agent↔agent) / AG-UI (agent↔user) e vocabularul de interviu al anului.
- [Arize Phoenix](https://github.com/arize-ai/phoenix) — observabilitate self-hosted **fără Docker** (`pip install arize-phoenix`, un singur proces, OpenTelemetry): trace-uri waterfall pentru fiecare rulare de agent, dashboards, evals. Bonus decisiv: [LiteLLM are integrare nativă Phoenix](https://docs.litellm.ai/docs/observability/phoenix_integration) — tier-ele 1–2 devin trasabile aproape fără cod, iar Claude Agent SDK e suportat out-of-the-box. Phoenix = „viziunea de inginer" gratis; mission control-ul tău = „viziunea de produs".

**Arhitectura recomandată pe 3 straturi:** (1) coloana vertebrală de date — run ledger + streaming real (#4/#5): *UI-ul e doar cât de bun e stream-ul de evenimente din spate; fără asta orice dashboard e carcasă goală*; (2) viziunea de inginer — Phoenix peste LiteLLM/SDK, câștig imediat; (3) viziunea de produs — „Kage Mission Control" (Next.js + CopilotKit/AG-UI): carduri live de agenți cu stream, activity feed, buget/cost, cache/memorie, inbox de aprobări de risc, panou de briefing-uri de la agenții programați (știri/date zilnice). Detalii de implementare la #15.

#### 3.13 Verificări punctuale (03.07.2026): superpowers, chrome-devtools-mcp, career-ops, Ornith-1.0

**[obra/superpowers](https://github.com/obra/superpowers)** (245k stele, v6.1.1 din 2 iul. 2026, Jesse Vincent) — metodologie completă de dezvoltare software ca **skill-uri compozabile**: brainstorm → plan → execuție pe subagenți cu review în două etape (conformitate cu specul + calitate), TDD. Plugin pentru Claude Code, Codex, Cursor etc.
- *Pentru Kage:* cel mai ieftin mod de a închide gap-ul de planificare multi-pas (2.1) **pentru taskuri de cod**: îl instalezi în Claude Code și agenții `!run`/`!sysrun` îl moștenesc gratis — zero cod în orchestrator. E și implementarea de referință pentru itemul #12 (cum se structurează skill-uri serioase).
- *Atenție:* fluxurile lui multi-subagent ard tokeni — încă un motiv pentru budget v2 (#7) care să acopere agenții; și e specific software development, nu taskuri generale de asistent.

**[ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)** (45k stele, echipa oficială Chrome, v1.5.0 din 3 iul. 2026) — server MCP care dă agenților control complet pe un Chrome real: automatizare de input, navigare, network inspection, console, screenshots, performance traces, 50+ tools.
- *Pentru Kage:* piesa care face agenții zilnici din mission control (#15) capabili de lucruri reale pe web — nu doar WebFetch, ci browser adevărat: colectat date din dashboards, verificat pagini, screenshots pentru briefing-uri. Se atașează la rulările CLI cu `--mcp-config`. Și poate testa vizual chiar frontend-ul Kage.
- *Observație importantă (ține de D6):* matcher-ul din `risk_settings.json` e `Bash|Write|Edit` — **tool-urile MCP ocolesc complet risk gate-ul**. Înainte de a da agenților un browser, matcher-ul trebuie extins (`mcp__.*` sau `*`) și matricea de risc învățată cu categoriile MCP. De adăugat la pașii itemului #2.

**[santifer/career-ops](https://github.com/santifer/career-ops)** (58k stele, v1.16.0 iul. 2026) — pipeline agentic de job hunting: lipești un URL de job → clasificare pe arhetip → scor A–F pe 10 dimensiuni → CV ATS-optimizat pe PDF → tracker; 15 moduri de skill, scanare preconfigurată pe 45+ companii și 19 job boards; rulează prin Claude Code/Codex/Gemini.
- *Pentru Stefan, direct:* ăsta nu e pentru Kage, e pentru **scopul lui Kage** — CV-ul și jobul. Merită folosit ca atare. Integrarea naturală: scanarea de portaluri ca task `!schedule` zilnic în Kage, cu notificare pe Telegram când apare ceva peste un prag de scor — exact use case-ul „agenți zilnici care caută date" din #15, cu ROI personal imediat.

**[Ornith-1.0](https://deep-reinforce.com/ornith_1_0.html)** (DeepReinforce, 25 iun. 2026, MIT) — familie open-source de modele de **agentic coding** (9B dense / 35B MoE / 397B MoE, context 262K) cu un unghi de research real: modelul își învață singur scaffold-urile de RL (generează și harness-ul, nu doar soluția). Scoruri raportate: 35B MoE — 64,2 pe Terminal-Bench 2.1 (peste Qwen 3.5-397B la 53,5); 9B — 69,4 pe SWE-Bench Verified; [greutăți pe Hugging Face](https://huggingface.co/deepreinforce-ai/Ornith-1.0-35B).
- *Comparație corectată (03.07.2026) — Qwen 3.6 35B e tot MoE A3B, nu dense:* [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) (apr. 2026, Apache 2.0) are 35B total / **3B activi**, context 262K (1M YaRN), **multimodal** (text+imagine+video), multilingv solid — și scorează 73,4 pe SWE-bench Verified și 51,5 pe Terminal-Bench 2.0. Ornith-35B ([HF](https://huggingface.co/deepreinforce-ai/Ornith-1.0-35B)) raportează 75,6 SWE-bench Verified și 64,2 Terminal-Bench 2.1 — dar comparațiile lui oficiale sunt cu **Qwen 3.5**, generația anterioară; față de 3.6, avantajul pe SWE-bench e de doar ~2 puncte, iar cel real e pe taskuri de terminal/agentic (~64 vs ~51, versiuni de benchmark ușor diferite). Ornith e construit *peste* Gemma 4 / Qwen 3.5, GGUF-ul **nu e încă publicat**, iar cifrele sunt self-reported.
- *Verdict:* **T2 rămâne Qwen3.6-35B-A3B** — mai rapid ca certitudine (3B activi), multimodal, multilingv (româna contează pentru chat), matur, deja instalat. Ornith devine interesant când apare GGUF-ul, ca **model specializat** pentru rolul de agent local de cod (#9) sau un „T2-code" separat, după benchmark pe taskurile tale reale — nu ca înlocuitor al T2 general.

#### Meniul realist de „self-improvement" pentru Kage, în ordinea corectă
1. **Router feedback loop (#3)** — cea mai ieftină auto-îmbunătățire, plătește imediat; aceeași poveste („sistemul învață din corecțiile mele"), 10% din efortul unui skill loop.
2. **Consolidare nocturnă de memorie (#6)** — al doilea strat: sistemul își rescrie singur memoria.
3. **Skills scrise de mână (nou, #12)** — 3–5 skill-uri pentru taskurile tale recurente, în format standard; valoare imediată, zero magie.
4. **Auto-distilare de skill-uri din traiectorii (Hermes-style, tot #12)** — abia după #4/#5, ca generare de draft + aprobare, în pipeline-ul nocturn de la 03:00 alături de memoria v2.
5. **Rafinare autonomă a skill-urilor / user modeling** — nu; complexitate de research nejustificată pentru un user.

---

## 4. Îmbunătățiri, prioritizate după valoare/efort

### #1 — Reparația fundației: chat funcțional, `stream:false`, smoke tests, alerting pe erori — **quick win**
- **Ce e:** fixul D1 (import `re`), D2 (răspuns JSON non-stream), D3 (test e2e + exception handler cu notificare) și D13 (prefixele agentice mutate în server) într-un singur PR.
- **De ce merită:** *Personal:* Kage redevine utilizabil, inclusiv de pe Telegram — azi e mort pentru orice conversație nouă. *CV:* povestea de interviu cea mai valoroasă din tot proiectul: „aveam 28 de teste verzi și producția moartă o lună; am învățat că testele care nu ating stratul HTTP nu testează sistemul — iată ce am schimbat". Onestitatea asta + fixul valorează mai mult decât orice feature.
- **Efort:** mic (o seară–un weekend).
- **Pași:** (1) `import re` global, șterge `_re` — `orchestrator.py`; (2) ramură non-stream în `chat_completions`; (3) handler pentru `!run|!swarm|!sysrun` în `chat_completions` care deleagă la logica `task_run`; (4) `tests/test_e2e.py` cu `TestClient` + `respx`; (5) `@app.exception_handler(Exception)` → `_notify`; (6) `logging.getLogger("httpx").setLevel(WARNING)` (scoate token-ul din log).

### #2 — Siguranța să spună adevărul: risk gate v2 + confinement funcțional — **quick win**
- **Ce e:** D4 + D6: High intră în fluxul de aprobare, axa 2 implementată, pattern-urile absurde scoase; `!run` din UI primește cwd valid, cu selector.
- **De ce merită:** *Personal:* agenții nu mai pică pe `2>/dev/null` și primești pe telefon aprobările promise; `!run` remerge. *CV:* „risk gate cu 3 axe" devine o afirmație demonstrabilă, nu una contrazisă de propriul cod — iar la interviu poți arăta matricea + testele ei.
- **Efort:** mic.
- **Pași:** (1) `risk_hook.py`: mută High în ramura de aprobare din `main()`, folosește `EXPLICIT_KEYWORDS` × `ORCHESTRATOR_USER_MSG` pentru downgrade, curăță HIGH/NEVER patterns; (2) `tests/test_risk.py`; (3) `orchestrator.py`: `GET /api/config` + default cwd = primul `ALLOWED_TASK_ROOTS`; (4) `kage.html`: dropdown cwd la task runner.

### #3 — Router cu feedback loop: învață din corecțiile tale — **quick win**
- **Ce e:** fiecare `!retry` și prefix forțat e un semnal de rutare greșită; stochează mesajul cu tier-ul corectat în colecția `tier_routing` și treci clasificarea de la 1-NN la vot ponderat k=5. Rezolvă și D10 (exemple pentru T4/T6 apar organic + prefixe `!opus`/`!gemini`).
- **De ce merită:** *Personal:* routerul se calibrează pe *tine* — exact avantajul unui sistem single-user. *CV:* povestea perfectă de 30 de secunde: „routerul meu semantic învață din override-urile utilizatorului, fără training — doar exemple noi în spațiul de embeddings". Diferențiator real față de „am pus un if pe lungimea mesajului".
- **Efort:** mic (o seară–un weekend).
- **Pași:** (1) în `decide_tier`, la `!retry`/forced, `_routing_collection.add(msg, tier_corectat, metadata={"source":"feedback"})`; (2) `_semantic_classify`: `n_results=5`, vot ponderat cu similaritatea; (3) plafonează colecția (vacuum la N exemple/tier); (4) extinde `tests/test_routing.py`.

### #4 — Executor pe Claude Agent SDK (sau minim: streaming real + resume) — **proiect mare, cel mai mare salt**
- **Ce e:** înlocuiește spawn-ul `claude -p` din `_route_claude_autonomous` și `_background_task_exec` cu `claude-agent-sdk` (secțiunea 3.1): sesiuni cu resume per `session_id`, hook PreToolUse în-proces (risk gate fără HTTP polling), evenimente de tool use streamate în UI/Telegram. Fallback minimal dacă nu vrei dependența: `--include-partial-messages` + `--resume` + parsarea evenimentelor de tool use din stream-json (rezolvă D5 și 2.6 cu efort mic).
- **De ce merită:** *Personal:* vezi live pe telefon ce face agentul („rulează: pytest…"), conversațiile T5 au continuitate reală, timeout-urile false dispar. *CV:* transformarea arhitecturală care mută proiectul din categoria „router" în „orchestrator agentic" — hooks, sesiuni, event streams sunt exact vocabularul interviurilor de agent engineering în 2026.
- **Efort:** mediu spre mare (varianta minimală: mediu; SDK complet: 2–4 săptămâni de seri).
- **Pași:** (1) `agent_runner.py` nou cu clasa `AgentRunner` (SDK client, hooks, event mapping → SSE chunks); (2) rescrie `_route_claude_autonomous` și `_background_task_exec` peste el; (3) map `session_id → sdk_session` în SQLite; (4) portează risk gate-ul ca hook Python (păstrează `risk_hook.py` pentru compatibilitate CLI); (5) e2e cu SDK mock.

### #5 — Run ledger + decision trace (observabilitate) — **proiect mediu**
- **Ce e:** tabelele `runs` și `run_events` în SQLite (2.4 + schema de trace din 3.6 + checkpointing-ul din 3.2): fiecare cerere și task cu decizia de rutare completă, evenimente, cost, status persistent; tab „Runs" în dashboard; aprobările de risc persistate (supraviețuiesc restartului).
- **De ce merită:** *Personal:* „ce s-a întâmplat cu taskul de azi-noapte?" primește un răspuns; azi cozile mor cu procesul. *CV:* demo-ul de interviu: deschizi dashboard-ul și arăți traseul unei decizii (semantic 0.87 → T2 → cache miss → 2 tool calls → 3.2s). Foarte puține proiecte personale au așa ceva.
- **Efort:** mediu (1–2 săptămâni de seri).
- **Pași:** (1) schema + `_run_start/_run_event/_run_end` în `orchestrator.py`; (2) instrumentează `decide_tier`, `_cache_lookup`, `_memory_retrieve`, `_background_task_exec`; (3) mută `pending_risk`/`pending_risk_meta` în tabel; (4) endpoint `/api/runs` + tab în dashboard; (5) fixează D9 (istoricul pe cache hit/disconnect) ca parte din același refactor.

### #6 — Memorie v2: extracție de fapte + consolidare nocturnă (sleep-time compute) — **proiect mediu**
- **Ce e:** D12 + ideea Letta (3.4): la finalul conversației, Qwen local extrage fapte persistente („preferă X", „lucrează la Y") sau NONE; la 03:00, un job APScheduler consolidează global — dedup, fuziune, ștergere stale, rescrierea unui bloc de profil injectat mereu.
- **De ce merită:** *Personal:* memoria devine semnal, nu zgomot de snippets trunchiate. *CV:* „sleep-time compute pe hardware propriu, zero cost cloud" — concept de frontieră (Letta l-a consacrat în 2025-2026) implementat la scară personală; poveste excelentă despre tradeoff-uri (de ce noaptea și nu per-mesaj).
- **Efort:** mediu.
- **Pași:** (1) `_memory_extract_facts(session_id)` cu prompt de extracție pe T2, apelat din `history_caching_gen` la final de conversație (sau batch nocturn); (2) job `_memory_consolidate` la 03:00 (înainte de vacuum/backup — pipeline nocturn coerent); (3) bloc `user_profile` rescris periodic, injectat în `_build_system_prompt`; (4) dedup global (scoate `where session_id`); (5) extinde `tests/test_memory.py`.

### #7 — Budget v2: cost real în $, care acoperă și agenții — **quick win spre mediu**
- **Ce e:** D11: parsează `total_cost_usd` din evenimentul `result` al CLI, gate pe `/task/run` și pe fallback-ul LiteLLM→cloud, buget în $/zi cu override explicit.
- **De ce merită:** *Personal:* azi limita nu atinge exact apelurile scumpe (agenții); un `!swarm` lung poate costa cât 50 de mesaje de chat. *CV:* cost engineering măsurabil — „bugetul meu e în dolari reali din telemetria providerului, nu în număr de apeluri".
- **Efort:** mic–mediu.
- **Pași:** (1) captează evenimentul `result` în `_background_task_exec`/`_route_claude_autonomous` → `cost_usd` în usage log; (2) `_budget_check` pe sumă $, apelat și în `task_run` + ramura de fallback din `_route_litellm`; (3) dashboard: card „$ azi".

### #8 — Cache v2: context-aware — **quick win**
- **Ce e:** fixul D8: fără lookup/store pe conversații multi-tură, fără store pe interogări temporale, prefixele curățate din cheie.
- **De ce merită:** *Personal:* dispare clasa „mi-a răspuns cache-ul altei conversații" — bug-uri care erodează încrederea în sistem. *CV:* minor singur, dar arată maturitate: știi *când să nu folosești* propriul feature.
- **Efort:** mic (o seară).
- **Pași:** condiție `len(conv_turns) == 1` pentru cache în `chat_completions`; regex temporal la store; strip prefixe în `cache_query`; teste noi în `tests/`.

### #9 — Tools locale pentru T2 (buclă smolagents-style) — **proiect mediu**
- **Ce e:** 2.2 + 3.3: registru de 4–5 tools (vault read/write, status, schedule) expus lui Qwen 35B prin function calling LiteLLM, cu o buclă simplă de execuție (max 5 iterații) și risk gate pe fiecare call.
- **De ce merită:** *Personal:* taskurile mărunte („notează în vault", „ce am programat?") devin gratis și rapide, fără cloud. *CV:* „function calling pe model local cu buclă proprie de agent" — demonstrează că înțelegi mecanismul, nu doar API-ul unui vendor.
- **Efort:** mediu.
- **Pași:** (1) `local_tools.py` cu definițiile + dispatch; (2) în `_route_litellm`, payload cu `tools=[...]` și bucla tool_call→execute→append; (3) gate prin `evaluate_risk` importat din `risk_hook`; (4) teste.

### #10 — Recipes pentru automatizări (format goose) — **proiect mediu**
- **Ce e:** 3.5: `recipes/*.yaml` (instrucțiuni, tier, cwd, tools, parametri); `!schedule` și `!run` pot referi un recipe; `scheduled_tasks.json` migrat.
- **De ce merită:** *Personal:* automatizările devin repetabile și editabile în git, nu string-uri într-un JSON. *CV:* workflow-as-code, validat public de Block la scară.
- **Efort:** mediu. **Pași:** schema YAML + loader; `_run_scheduled_task` acceptă recipe; comandă `!recipe <nume> [param=...]`.

### #11 — Igienă de repo: depersonalizare + cod mort + rotație — **quick win**
- **Ce e:** D14 + D15: persona/proiecte în config/vault, șterge `_build_chat_html`, `UNCERTAINTY_PHRASES`, `MULTI_TENANT_ARCH.md`, unifică schedule-add, usage în SQLite sau rotit.
- **De ce merită:** *Personal:* minor. *CV:* semnificativ — e primul lucru pe care îl vede cineva care deschide repo-ul; ~250 de linii de cod mort și datele personale hardcodate sunt anti-semnal ieftin de eliminat.
- **Efort:** mic.

### #12 — Skills în format Agent Skills (standard, nu recipes custom) — **quick win la start, crește apoi**
- **Ce e:** folder `skills/` în format SKILL.md (3.9): 3–5 skill-uri scrise de mână pentru taskurile tale recurente (ex: „briefing de dimineață", „triage StefanBrain", „raport săptămânal facultate"), preluate automat de Claude Code CLI la rulările agentice; potrivire pe descriere pentru tier-ele locale prin embeddings (infrastructura există). Mai târziu: auto-distilare Hermes-style ca draft + aprobare (3.7), abia după #4/#5.
- **De ce merită:** *Personal:* automatizările devin repetabile și versionabile. *CV:* interoperabilitate cu standardul care a câștigat (Claude Code/Codex/Gemini CLI) > format propriu.
- **Efort:** mic pentru skills manuale; mediu pentru auto-distilare.
- **Pași:** (1) `skills/` cu 3 SKILL.md; (2) copiere/symlink în `.claude/skills` la cwd-ul rulărilor `!run`; (3) `!skill list`/`!skill new` în chat; (4) mai târziu, job nocturn de distilare + aprobare pe Telegram. Înlocuiește itemul #10.

### #13 — Voice memos pe Telegram (whisper.cpp local) — **quick win, valoare personală mare**
- **Ce e:** mesajele voice de pe Telegram (OGG/Opus) sunt descărcate, transcrise local cu whisper.cpp (`large-v3-turbo`, ~1,6GB, română solidă) și intră în pipeline-ul normal ca text — dictezi taskuri când n-ai timp să scrii.
- **De ce merită:** *Personal:* exact fluxul cerut — voice memo → task; transcrierea rămâne 100% locală. *CV:* „STT self-hosted integrat în gateway multi-canal" — și demonstrează arhitectura channel-agnostic (3.11).
- **Efort:** mic (o seară–un weekend). **Depinde de #1** (fără fixul NameError + stream:false, răspunsul nu ajunge înapoi pe Telegram).
- **Pași:** (1) endpoint `POST /v1/audio/transcriptions` în `orchestrator.py` (subprocess whisper.cpp sau server mode); (2) în `telegram_gateway.py` `_dispatch`: ramură pentru `message.voice` → `getFile` → download → transcrie → `_handle_message(transcript)`, cu reply „📝 Am înțeles: …"; (3) config: `whisper_bin`, `whisper_model`; (4) test cu fixture audio.

### #14 — Push-to-talk pe Mac, apoi „Hey Jarvis" — **proiect mediu, în două etape**
- **Ce e:** Etapa 1: hotkey global în widget-ul menubar (`status_widget.py`) → înregistrare → transcriere (același endpoint din #13) → chat completions → răspuns rostit cu Piper ro_RO sau `say -v Ioana`. Etapa 2: wake word „Hey Jarvis" (model pre-antrenat în openWakeWord) sau „Kage" (antrenat custom), cu RealtimeSTT ca strat de VAD + ascultare continuă.
- **De ce merită:** *Personal:* Jarvis-ul cerut, complet offline. *CV:* pipeline voice local end-to-end (wake word → VAD → STT → orchestrator → TTS) pe hardware propriu — demo de interviu spectaculos și tehnic onest (fără API-uri cloud).
- **Efort:** etapa 1 mediu-mic (un weekend); etapa 2 mediu.
- **Pași:** (1) în `status_widget.py`: hotkey (pynput) + înregistrare (sounddevice) + POST la orchestrator; (2) TTS: Piper cu voce ro_RO, fallback `say`; (3) etapa 2: proces separat `voice_daemon.py` cu RealtimeSTT + openWakeWord, care POST-ează la orchestrator; (4) răspunsurile lungi: rostește doar primul paragraf + „restul în Kage".
- **Notă:** decizia „voice — nu în core loop" din ROADMAP e răsturnată conștient aici; de actualizat `ROADMAP.md`.

### #15 — „Kage Mission Control": frontend propriu pe AG-UI + Phoenix pentru observabilitate — **proiect mare, în două etape**
- **Ce e:** înlocuiește `kage.html` cu un mission control React/Next.js construit pe stack-ul din 3.12: carduri live de agenți (stream + status), activity feed în timp real, dashboards de buget/cost/cache/memorie, inbox de aprobări de risc, panou de briefing-uri de la agenții programați (știri/date zilnice). Kage emite evenimente [AG-UI](https://docs.copilotkit.ai/agentic-protocols/ag-ui) din run ledger; [CopilotKit](https://github.com/copilotkit/copilotkit) le randează. În paralel, [Arize Phoenix](https://github.com/arize-ai/phoenix) (pip install, un proces, fără Docker) dă trace-uri waterfall gratis prin [integrarea nativă LiteLLM](https://docs.litellm.ai/docs/observability/phoenix_integration).
- **De ce merită:** *Personal:* exact supravegherea cerută — vezi ce fac agenții zilnici, ce costă, ce așteaptă aprobare, cu wow factor la prezentare. *CV:* frontend pe protocolul-standard al industriei (AG-UI, adoptat de Google/AWS/Microsoft/LangChain) + observabilitate OpenTelemetry — vocabularul complet MCP/A2A/AG-UI într-un singur proiect personal. Povestea de interviu: „am despărțit viziunea de produs de viziunea de inginer".
- **Efort:** etapa A (Phoenix peste LiteLLM + SDK): mic, câteva seri. Etapa B (mission control complet): mare (o lună+ de seri). **Depinde de #1 (API funcțional) și #5 (run ledger — fără evenimente, dashboard-ul e carcasă goală); #4 îl face spectaculos.**
- **Pași:** (1) etapa A: `pip install arize-phoenix`, `phoenix serve`, callback Phoenix în `litellm_config.yaml`; (2) schema de evenimente AG-UI peste tabelele `runs`/`run_events` din #5 (endpoint SSE `/agui`); (3) app Next.js + CopilotKit: panouri runs / activity / budget / approvals / briefings; (4) migrarea aprobărilor de risc din `kage.html` (API-ul `/api/pending` există); (5) agenții zilnici (#12 skills + `!schedule`) scriu briefing-uri structurate (JSON/MD) randate ca și carduri; (6) `kage.html` se pensionează când paritatea e atinsă.
- **Referințe de design:** AgentOS (topologie live), builderz-labs/mission-control (activity stream), MeisnerDan (agent inbox), Vibe Kanban (carduri de sesiuni) — linkuri în 3.12.

### Ordinea recomandată
`#1 → #2 → #3 → #8 → #11` (toate mici) → `#13` (voice pe Telegram, deblocat de #1) → `#15A` (Phoenix — observabilitate instant) → `#5 → #4` (coloana vertebrală de evenimente) → `#15B` (mission control) → `#12 → #14 → #7 → #6 → #9`. Itemul #10 e absorbit în #12.

### Top 3 pentru un singur weekend liber
1. **#1 Reparația fundației** — fără asta, restul evaluării e teorie: sistemul nu funcționează.
2. **#2 Risk gate v2 + confinement funcțional** — feature-urile tale de siguranță încep să corespundă documentației, iar `!run` remerge de pe telefon.
3. **#3 Router cu feedback loop** — cel mai bun raport „poveste de CV / oră investită" din întreaga listă; și e satisfăcător imediat în uz zilnic.

Dacă rămâne timp duminică seara: **#8** (cache context-aware) e două ore și închide o clasă întreagă de comportament derutant.
