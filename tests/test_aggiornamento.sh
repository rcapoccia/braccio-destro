#!/usr/bin/env bash
# Collaudo dell'aggiornamento automatico (va lanciato come root, su una macchina di prova: MAI sul server vero).
# Un finto "GitHub" locale e un finto servizio. Verifica:
#   1. una versione buona si installa;
#   2. una versione che non parte viene tolta da sola (ritorno alla precedente) e non viene riprovata;
#   3. una versione con un errore nel codice viene rifiutata prima di toccare i file;
#   4. una versione a cui manca un file dell'elenco viene rifiutata;
#   5. dopo una versione scartata, la successiva buona si installa;
#   6. la chiave di GitHub scritta nello script è quella ufficiale.
set -uo pipefail
QUI=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
ERRORI=0
errore() { echo "::error title=Aggiornamento::$*"; echo "ERRORE: $*"; ERRORI=$((ERRORI + 1)); }
bene() { echo "ok: $*"; }

export VEGA_DEST=$T/dest VEGA_STATO=$T/stato VEGA_SBIN=$T/sbin VEGA_VETRINA=$T/vetrina
export VEGA_SERVIZIO=vega-prova-agg.service VEGA_SALUTE=http://127.0.0.1:8099/ VEGA_ATTESA_SALUTE=15
export VEGA_URL_PUBBLICO=$T/origine.git VEGA_CHIAVE=$T/nessuna-chiave
mkdir -p "$VEGA_DEST" "$VEGA_SBIN" "$T/lavoro"
git init -q --bare "$T/origine.git"
git -C "$T/lavoro" init -q -b produzione-verificata
git -C "$T/lavoro" config user.email prova@esempio.invalid
git -C "$T/lavoro" config user.name Prova
git -C "$T/lavoro" remote add origin "$T/origine.git"

servizio() {   # $1 = testo servito; $2 = "rotto" per farlo uscire subito
  if [[ "${2:-}" == rotto ]]; then
    printf 'import sys\nsys.exit(3)\n' > "$T/lavoro/servizio.py"
  else
    cat > "$T/lavoro/servizio.py" <<P
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"$1")
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 8099), H).serve_forever()
P
  fi
}
pubblica() {   # crea una versione sul finto GitHub
  cp "$QUI/auto-aggiorna.sh" "$QUI/braccio-codice.sh" "$T/lavoro/"
  git -C "$T/lavoro" add -A && git -C "$T/lavoro" commit -q -m "$1" && git -C "$T/lavoro" push -q -f origin produzione-verificata
  git -C "$T/lavoro" rev-parse HEAD
}
servito() { curl -fsS --max-time 5 http://127.0.0.1:8099/ 2>/dev/null || echo "(nessuna risposta)"; }
aggiorna() { bash "$QUI/auto-aggiorna.sh" > "$T/uscita" 2>&1; local r=$?; sed 's/^/   | /' "$T/uscita"; return $r; }
stato() { cat "$VEGA_STATO/$1" 2>/dev/null || true; }

cat > /etc/systemd/system/vega-prova-agg.service <<U
[Unit]
Description=Finto servizio per il collaudo degli aggiornamenti
[Service]
ExecStart=/usr/bin/python3 $VEGA_DEST/servizio.py
U
systemctl daemon-reload

echo "== 1. prima installazione e avvio"
printf 'servizio.py\nrequirements-web.txt\n' > "$T/lavoro/file-elenco.txt"
: > "$T/lavoro/requirements-web.txt"
servizio uno
V1=$(pubblica uno)
aggiorna
systemctl start vega-prova-agg.service; sleep 2
[[ "$(stato versione)" == "$V1" && "$(servito)" == uno ]] && bene "versione uno installata" || errore "la prima versione non si è installata"

echo "== 2. versione buona"
servizio due
V2=$(pubblica due)
aggiorna || errore "un aggiornamento riuscito ha segnalato un errore"
[[ -z "$(ls -d /tmp/tmp.* 2>/dev/null | xargs -r -I{} find {} -maxdepth 1 -name nuova)" ]] || errore "restano cartelle temporanee dopo l'aggiornamento"
[[ "$(stato versione)" == "$V2" && "$(servito)" == due ]] && bene "versione due installata" || errore "la versione buona non si è installata (servito: $(servito))"
[[ "$(stato precedente)" == "$V1" ]] || errore "non ricorda la versione precedente"

echo "== 3. versione che non parte: deve tornare indietro da sola"
servizio tre rotto
echo "nuovo.py" >> "$T/lavoro/file-elenco.txt"; echo "x = 1" > "$T/lavoro/nuovo.py"
V3=$(pubblica tre-rotta)
aggiorna && errore "una versione che non parte è stata accettata"
[[ "$(stato versione)" == "$V2" ]] || errore "dopo la versione rotta la versione registrata non è più la due"
[[ "$(stato scartata)" == "$V3" ]] || errore "la versione rotta non è stata segnata come scartata"
[[ "$(servito)" == due ]] && bene "tornato alla versione due, che risponde" || errore "dopo il ritorno indietro il servizio non risponde (servito: $(servito))"
[[ -f "$VEGA_DEST/nuovo.py" ]] && errore "è rimasto un file della versione scartata"
grep -q difettosa "$VEGA_VETRINA/stato.json" || errore "la pagina non saprebbe che la versione è stata scartata"

echo "== 3b. la versione scartata non viene riprovata"
aggiorna
[[ "$(stato versione)" == "$V2" && "$(servito)" == due ]] && bene "non riprovata" || errore "la versione scartata è stata riprovata"

echo "== 4. errore nel codice: rifiutata prima di toccare i file"
printf 'def rotto(:\n' > "$T/lavoro/servizio.py"
V4=$(pubblica quattro-sintassi)
aggiorna && errore "una versione con un errore nel codice è stata accettata"
[[ "$(stato versione)" == "$V2" && "$(servito)" == due ]] && bene "rifiutata, la due continua a rispondere" || errore "l'errore nel codice ha toccato il servizio"

echo "== 5. manca un file dell'elenco: rifiutata"
servizio cinque; echo "non-esiste.py" >> "$T/lavoro/file-elenco.txt"
V5=$(pubblica cinque-manca-file)
aggiorna && errore "una versione incompleta è stata accettata"
[[ "$(stato versione)" == "$V2" && "$(servito)" == due ]] && bene "rifiutata" || errore "la versione incompleta ha toccato il servizio"

echo "== 5b. nomi di file che escono dalla cartella, e collegamenti: rifiutati"
sed -i '/non-esiste.py/d' "$T/lavoro/file-elenco.txt"
cp "$T/lavoro/file-elenco.txt" "$T/elenco-buono"
echo "../fuori.py" >> "$T/lavoro/file-elenco.txt"; servizio cinque-b
pubblica cinque-b-fuori >/dev/null
aggiorna && errore "una versione che scrive fuori dalla cartella è stata accettata"
[[ ! -e "$T/fuori.py" && "$(stato versione)" == "$V2" ]] && bene "percorso esterno rifiutato" || errore "scritto un file fuori dalla cartella di Vega"
cp "$T/elenco-buono" "$T/lavoro/file-elenco.txt"; echo "segreto.txt" >> "$T/lavoro/file-elenco.txt"
echo riservato > "$T/riservato"; ln -s "$T/riservato" "$T/lavoro/segreto.txt"
pubblica cinque-c-collegamento >/dev/null
aggiorna && errore "una versione con un collegamento a un file esterno è stata accettata"
[[ ! -e "$VEGA_DEST/segreto.txt" && "$(stato versione)" == "$V2" ]] && bene "collegamento rifiutato" || errore "un collegamento ha copiato un file esterno"
rm -f "$T/lavoro/segreto.txt"; cp "$T/elenco-buono" "$T/lavoro/file-elenco.txt"

echo "== 5c. aggiornamento interrotto a metà: al giro dopo si rimette la versione salvata"
echo "$V2" > "$VEGA_STATO/in-corso"; cp "$T/elenco-buono" "$VEGA_STATO/in-corso.elenco"
echo "print('mezzo installato')" > "$VEGA_DEST/servizio.py"
git -C "$T/lavoro" push -q -f origin "$V2:refs/heads/produzione-verificata"
aggiorna || errore "il ripristino dopo un'interruzione ha segnalato un errore"
[[ ! -f "$VEGA_STATO/in-corso" && "$(servito)" == due ]] && bene "versione salvata rimessa" || errore "dopo un'interruzione il servizio non è tornato alla versione salvata (servito: $(servito))"

echo "== 6. dopo gli scarti, una versione buona si installa"
servizio sei
V6=$(pubblica sei)
aggiorna || errore "un aggiornamento riuscito ha segnalato un errore"
[[ "$(stato versione)" == "$V6" && "$(servito)" == sei ]] && bene "versione sei installata" || errore "dopo gli scarti la versione buona non si è installata (servito: $(servito))"
python3 -c "import json,sys; d=json.load(open('$VEGA_VETRINA/stato.json')); assert d['canale']=='pubblico' and d['esito']=='aggiornato', d" || errore "lo stato per la pagina non è corretto"

echo "== 7. la chiave di GitHub nello script è quella ufficiale"
NOSTRA=$(grep -o "ssh-ed25519 [A-Za-z0-9+/=]*" "$QUI/auto-aggiorna.sh" | head -1)
UFFICIALE=$(curl -fsS --max-time 20 https://api.github.com/meta | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin).get('ssh_keys', [])))" || true)
if [[ -z "$UFFICIALE" ]]; then echo "::warning title=Aggiornamento::non riesco a leggere le chiavi ufficiali di GitHub: controllo saltato"
elif grep -qxF "$NOSTRA" <<<"$UFFICIALE"; then bene "chiave ufficiale"
else errore "la chiave di GitHub nello script NON è tra quelle ufficiali"; fi

systemctl stop vega-prova-agg.service 2>/dev/null; rm -f /etc/systemd/system/vega-prova-agg.service; systemctl daemon-reload
rm -rf "$T"
echo; [[ $ERRORI == 0 ]] && echo "ESITO: OK" || echo "ESITO: $ERRORI ERRORI"
exit $((ERRORI > 0))
