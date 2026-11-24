#!/usr/bin/env bash
# Aggiornamento automatico: ogni 2 minuti scarica il ramo "produzione" se è cambiato.
# Prima di sostituire i file controlla che il programma sia valido; se no tiene la versione attuale.
set -euo pipefail
REPO="rcapoccia/braccio-destro"
DEST=/opt/braccio-destro
STATO=/var/lib/braccio-destro-aggiornamenti

install -d -m 700 "$STATO"
SHA=$(curl -fsS "https://api.github.com/repos/$REPO/commits/produzione" | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha"])')
[[ "$SHA" == "$(cat "$STATO/versione" 2>/dev/null || true)" ]] && exit 0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
RAW="https://raw.githubusercontent.com/$REPO/$SHA"
curl -fsSL "$RAW/file-elenco.txt" -o "$TMP/file-elenco.txt"
mapfile -t FILE < <(grep -v '^\s*$' "$TMP/file-elenco.txt")
for f in "${FILE[@]}" auto-aggiorna.sh braccio-codice.sh; do
  mkdir -p "$TMP/$(dirname "$f")"
  curl -fsSL "$RAW/$f" -o "$TMP/$f"
done

if ! (cd "$TMP" && python3 -c 'import ast,sys; [ast.parse(open(f).read()) for f in sys.argv[1:]]' $(printf '%s\n' "${FILE[@]}" | grep '\.py$')); then
  echo "Versione $SHA non valida: tengo quella attuale."
  exit 1
fi

REQ_PRIMA=$(sha256sum "$DEST/requirements-web.txt" 2>/dev/null | cut -d' ' -f1 || true)
for f in "${FILE[@]}"; do
  install -d -m 755 "$DEST/$(dirname "$f")"
  install -m 644 "$TMP/$f" "$DEST/$f"
done
install -m 755 "$TMP/auto-aggiorna.sh" /usr/local/sbin/braccio-auto-aggiorna
install -m 755 "$TMP/braccio-codice.sh" /usr/local/sbin/braccio-codice
if [[ -x "$DEST/venv/bin/pip" && "$REQ_PRIMA" != "$(sha256sum "$DEST/requirements-web.txt" | cut -d' ' -f1)" ]]; then
  "$DEST/venv/bin/pip" install -q -r "$DEST/requirements-web.txt"
fi
systemctl try-restart braccio-web.service || true
echo "$SHA" > "$STATO/versione"
echo "Aggiornato alla versione $SHA."
