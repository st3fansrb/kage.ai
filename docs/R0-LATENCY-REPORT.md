# R0 — raport de latență API

Măsurat la 14 iulie 2026, local, cu 100 cereri per endpoint prin FastAPI `TestClient`, Python
3.12.13. Măsurătoarea izolează handler-ele aplicației (nu include rețeaua, startup-ul sau un
model LLM); pentru un server pornit, benchmark-ul repetabil este `python scripts/benchmark_api.py`.

| Endpoint | p50 | p95 | n |
| --- | ---: | ---: | ---: |
| `GET /health` | 0.758 ms | 0.920 ms | 100 |
| `GET /api/missions?limit=20` | 0.601 ms | 0.716 ms | 100 |
| `GET /api/usage?limit=20` | 0.610 ms | 0.675 ms | 100 |

Nu a fost repornit și nici încărcat serviciul de producție pentru această măsurătoare. Endpoint-ul
de chat nu este inclus: latența sa este dominată de modelul selectat și trebuie măsurată separat
pe un backend disponibil, folosind trafic controlat.
