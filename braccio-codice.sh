#!/usr/bin/env bash
# Genera un codice monouso (valido 24 ore) per attivare Face ID su un nuovo dispositivo.
set -euo pipefail
DIR=/var/lib/braccio-destro/web
install -d -o braccio -g braccio -m 700 "$DIR"
CODICE=$(python3 -c 'import secrets; print(f"{secrets.randbelow(10**8):08d}")')
python3 - "$CODICE" "$DIR/attivazione.json" <<'P'
import hashlib, json, os, sys, time
codice, percorso = sys.argv[1], sys.argv[2]
with open(os.open(percorso, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
    json.dump({"hash": hashlib.sha256(codice.encode()).hexdigest(), "scade": time.time() + 86400}, f)
P
chown braccio:braccio "$DIR/attivazione.json"
echo
echo "CODICE DI ATTIVAZIONE: ${CODICE:0:4} ${CODICE:4:4}"
echo "Vale una volta sola, per 24 ore. Aprilo su https://braccio.stital.it dal dispositivo da attivare."
