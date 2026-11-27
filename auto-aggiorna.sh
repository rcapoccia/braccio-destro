#!/usr/bin/env bash
# Aggiornamento automatico: ogni 2 minuti scarica il ramo "produzione" se è cambiato.
# Prima di sostituire i file controlla che il programma sia valido; se no tiene la versione attuale.
set -euo pipefail
REPO="rcapoccia/braccio-destro"
DEST=/opt/braccio-destro
STATO=/var/lib/braccio-destro-aggiornamenti

install -d -m 700 "$STATO"

# Configurazione di sistema: la rilancia quando sistema.sh cambia (anche se il codice è già aggiornato).
esegui_sistema() {
  [[ -f "$DEST/sistema.sh" && -x "$DEST/venv/bin/pip" ]] || return 0
  local h; h=$(sha256sum "$DEST/sistema.sh" | cut -d' ' -f1)
  if [[ "$h" != "$(cat "$STATO/sistema" 2>/dev/null || true)" ]]; then
    bash "$DEST/sistema.sh" && echo "$h" > "$STATO/sistema"
  fi
}
esegui_sistema || echo "Configurazione di sistema non riuscita: riprovo al prossimo giro."

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

# Librerie nuove: si installano PRIMA di sostituire i file. Se l'installazione fallisce si tiene la versione
# attuale e si riprova al giro successivo (il segno dell'ultima installazione riuscita è in $STATO/librerie).
REQ_NUOVE=$(sha256sum "$TMP/requirements-web.txt" | cut -d' ' -f1)
REQ_FATTE=$(cat "$STATO/librerie" 2>/dev/null || true)
if [[ -x "$DEST/venv/bin/pip" && "$REQ_NUOVE" != "$REQ_FATTE" ]]; then
  if ! "$DEST/venv/bin/pip" install -q -r "$TMP/requirements-web.txt"; then
    echo "Librerie della versione $SHA non installate: tengo quella attuale e riprovo."
    exit 1
  fi
  echo "$REQ_NUOVE" > "$STATO/librerie"
fi
for f in "${FILE[@]}"; do
  install -d -m 755 "$DEST/$(dirname "$f")"
  install -m 644 "$TMP/$f" "$DEST/$f"
done
install -m 755 "$TMP/auto-aggiorna.sh" /usr/local/sbin/braccio-auto-aggiorna
install -m 755 "$TMP/braccio-codice.sh" /usr/local/sbin/braccio-codice
echo "$SHA" > "$STATO/versione"
systemctl try-restart braccio-web.service || true
esegui_sistema || echo "Configurazione di sistema non riuscita: riprovo al prossimo giro."
echo "Aggiornato alla versione $SHA."
