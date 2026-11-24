#!/usr/bin/env bash
# Aggiornamento automatico: ogni 2 minuti scarica il ramo "produzione" se è cambiato.
# Prima di sostituire i file controlla che il programma sia valido; se no tiene la versione attuale.
set -euo pipefail
REPO="rcapoccia/braccio-destro"
DEST=/opt/braccio-destro
STATO=/var/lib/braccio-destro-aggiornamenti
FILE="posta.py mistral.py pratiche.py questionari.py istruzioni.py ricerca.py moduli/itas_car.json moduli/itas_car.pdf moduli/compagnie.json"

install -d -m 700 "$STATO"
SHA=$(curl -fsS "https://api.github.com/repos/$REPO/commits/produzione" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$SHA" == "$(cat "$STATO/versione" 2>/dev/null || true)" ]] && exit 0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/moduli"
for f in $FILE; do
  curl -fsSL "https://raw.githubusercontent.com/$REPO/$SHA/$f" -o "$TMP/$f"
done
# Il file elenco può aggiungere file nuovi alla versione successiva.
curl -fsSL "https://raw.githubusercontent.com/$REPO/$SHA/auto-aggiorna.sh" -o "$TMP/auto-aggiorna.sh"

if ! (cd "$TMP" && python3 -c 'import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]' *.py); then
  echo "Versione $SHA non valida: tengo quella attuale."
  exit 1
fi

install -d -m 755 "$DEST/moduli"
for f in $FILE; do install -m 644 "$TMP/$f" "$DEST/$f"; done
install -m 755 "$TMP/auto-aggiorna.sh" /usr/local/sbin/braccio-auto-aggiorna
echo "$SHA" > "$STATO/versione"
echo "Aggiornato alla versione $SHA."
