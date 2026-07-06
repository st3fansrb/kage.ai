# Mission: Exemplu — smoke test al runner-ului

Misiune minimă de demonstrație (WP11). Rul-o cu `!mission start exemplu`. Fiecare
pachet are un criteriu de acceptare VERIFICABIL (comandă shell între backtick-uri) pe
care runner-ul îl rulează singur; dacă trece, marchează pachetul ✅ și avansează.

## WP1 — Creează un fișier de test
- Creează fișierul `/tmp/kage-mission-demo.txt` cu textul `pas1`.

### Acceptare
- `test -f /tmp/kage-mission-demo.txt`

## WP2 — Adaugă o linie
- Adaugă în `/tmp/kage-mission-demo.txt` o a doua linie cu textul `done`.

### Acceptare
- `grep -q done /tmp/kage-mission-demo.txt`
