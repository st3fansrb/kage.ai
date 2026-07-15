# Fișă de interviu — Rutare semantică + cache semantic + memorie

> Format §8 v2 (KAGE-HANDOFF): citește fișa (~10 min), răspunde la întrebările adversariale
> FĂRĂ să te uiți la răspunsurile de la final, apoi verifică-te. Sesiunea „Apără" pornește
> de aici.

## Ce e subsistemul

Trei consumatori ai aceluiași motor de embeddings (`nomic-embed-text` via Ollama,
`_get_embedding`), toți pe ChromaDB, dar cu praguri și scopuri diferite:

1. **Rutarea 6-tier** — `decide_tier` → `_classify` → `_semantic_classify`: k-NN (k=5) peste
   colecția `tier_routing` (exemple etichetate + feedback învățat), vot ponderat de
   similaritate cu podea 0.6; tier = argmax pe suma similarităților per tier; confidence =
   similaritatea celui mai bun vecin al tierului câștigător. Fallback în lanț: semantic →
   Qwen 8B clasificator digit-only → heuristic (circuit breaker după 3 eșecuri Ollama).
   Prefixele (`!fast`, `!best`, …) = override determinist, ocolesc tot.
2. **Cache-ul semantic** — `_cache_policy` + `_cache_lookup`: 1-NN cu prag **0.92**, TTL 24h.
   Politica e context-aware: follow-up (>1 tură user) = skip complet; referenți temporali
   (regex `azi|acum|mâine|ieri|astăzi`) = nu stoca; cheia = ultimul mesaj curățat de prefixe.
3. **Memoria long-term** — `_memory_store`/`_memory_retrieve`: perechi (user, assistant)
   trunchiate la 300 chars, dedup la **0.95** per sesiune, retrieve top-5 cu prag de
   relevanță **0.70**, injectat în system prompt.

## Decizia și DE CE

- **k-NN ponderat pe exemple, nu clasificator antrenat:** setul de date e minuscul
  (single-user); exemplele se adaugă incremental (feedback loop) fără retraining; decizia e
  explicabilă — vecinii care au votat sunt vizibili.
- **Praguri diferite per use-case (0.6 / 0.70 / 0.92 / 0.95):** costul erorii diferă. Un
  cache hit greșit servește cu încredere răspunsul altei întrebări → prag strict (0.92). O
  rutare greșită dă doar un tier suboptim, recuperabil cu `!retry` → prag lax (0.6).
  Dedup-ul de memorie (0.95) e și mai strict: o ștergere greșită pierde informație definitiv.
- **Confidence = best neighbor, nu suma voturilor:** suma crește cu *numărul* de vecini, nu
  cu *calitatea* potrivirii — 5 vecini mediocri ar bate un match aproape perfect.
- **Fallback în lanț cu circuit breaker:** pentru rutare, disponibilitatea bate acuratețea —
  mai bine o clasificare heuristică decât un request blocat în timeout-uri repetate.

## Alternative respinse

- **Clasificator LLM ca primar** (istoric, era v1): latență pe fiecare mesaj + parsare
  fragilă a răspunsului; retrogradat la fallback.
- **1-NN pentru rutare:** un singur exemplu prost etichetat sau un outlier decide tierul;
  votul k=5 ponderat amortizează.
- **Cache exact-match (hash pe string):** hit rate ~0 pe limbaj natural — parafrazele nu se
  prind niciodată.
- **Includerea contextului conversației în cheia de cache** (în loc de skip pe follow-up):
  ar exploda cardinalitatea cheilor (hit rate → 0) și ar cere embeddings pe conversații
  întregi; s-a ales varianta onestă — follow-up = necacheabil.

## Trade-off-uri acceptate

- Cu exemple puține, granița de decizie T2/T3 e zgomotoasă — se corectează adăugând exemple,
  nu schimbând algoritmul.
- Cheia de cache = doar ultimul mesaj → toată clasa follow-up e sacrificată (capcana:
  „continuă" ar primi răspunsul altei conversații).
- TTL fix 24h = compromis staleness/hit-rate; detecția temporală e un regex simplu,
  doar pe română.
- Similaritate = `1.0 - distance` peste distanța ChromaDB — corect pentru cosine pe
  vectori normalizați, dar pragurile sunt cuplate implicit de metrica colecției.

## Întrebări adversariale (răspunde întâi, verifică după)

1. De ce k-NN ponderat cu k=5 și nu 1-NN? Ce se întâmplă concret când un exemplu din
   `tier_routing` e etichetat greșit, în fiecare dintre cele două scheme?
2. Cache-ul are prag 0.92, rutarea 0.6. Ce s-ar strica, exact, dacă le-ai egaliza la 0.8
   pe amândouă?
3. De ce follow-up-urile sar cache-ul complet, în loc să intre contextul în cheia de cache?
   Care ar fi fost alternativa și de ce pierde?
4. Confidence-ul rutării = similaritatea celui mai bun vecin al tierului câștigător. De ce
   nu suma voturilor sau media lor? Dă un caz în care fiecare dintre cele trei minte.
5. Mesajul „what's the weather today?" trece de `_TEMPORAL_RE` (regex doar pe română) și se
   stochează în cache. Bug sau trade-off? Cum l-ai repara fără să tai hit rate-ul?
6. (bonus) Dedup-ul memoriei filtrează cu `where={"session_id": ...}` — același fapt din
   altă sesiune NU deduplichează. Intenționat sau scăpare? Apără ambele poziții.

---

## Răspunsuri (self-check — nu citi înainte să răspunzi)

1. Cu 1-NN, un exemplu greșit etichetat „capturează" toate mesajele din vecinătatea lui —
   eroare sistematică, greu de observat. Cu k=5 ponderat, exemplul greșit e doar un vot din
   cinci și pierde în fața a doi-trei vecini corecți; eroarea devine marginală, nu
   sistematică. Prețul: la granițe între tiers, votul poate fi mai difuz.
2. Cache la 0.8: parafraze *apropiate dar cu intenție diferită* („explică X" vs „dă-mi un
   exemplu de X") încep să primească răspunsul celeilalte — erori vizibile, cu încredere.
   Rutare la 0.8: majoritatea mesajelor nu mai au vecini peste prag → cad permanent pe
   fallback-ul Qwen/heuristic, adică rutarea semantică (și feedback-ul învățat) devine moartă.
3. Alternativa: embedding pe (context + mesaj) sau pe un rezumat al conversației. Pierde
   pentru că fiecare conversație e unică → cheile nu se mai repetă → hit rate ~0, dar
   plătești în continuare costul embedding-ului la fiecare tură. Skip-ul e onest: recunoaște
   că doar prima tură e cacheabilă cu cheia aleasă.
4. Suma voturilor crește cu numărul de vecini peste prag, nu cu calitatea — 5 vecini de 0.62
   ar da „confidence" mai mare decât un match de 0.98. Media suferă invers: un match perfect
   plus doi vecini slabi din același tier trag media în jos deși evidența e puternică.
   Best-neighbor minte când un singur vecin e foarte similar dar prost etichetat — exact
   riscul pe care votul k=5 îl acoperă la *alegerea* tierului, dar nu și la *confidence*.
5. Trade-off devenit bug latent când interfața acceptă engleză. Fix ieftin: extinde regexul
   cu echivalentele engleze (today/now/tomorrow/yesterday) — cost zero pe hit rate, fiindcă
   blochează doar stocarea (store_ok), nu lookup-ul. Fix „corect" dar scump: clasificator de
   perisabilitate pe T1 local — nu merită la volumul actual.
6. Ca scăpare: faptul e global (memoria se injectează cross-session la retrieve), deci și
   dedup-ul ar trebui să fie global — acum același fapt se acumulează din N sesiuni și
   ocupă top-K la retrieve cu duplicate. Ca intenție: același text în sesiuni diferite are
   context diferit (timestamp, evoluție), iar dedup-ul global cu prag 0.95 ar putea șterge
   nuanțe legitime; plus filtrarea `where` e ieftină. Poziția defensabilă la interviu:
   scăpare benignă — corect ar fi dedup global la retrieve (post-filtrare pe similaritate
   între rezultate), nu la store.
