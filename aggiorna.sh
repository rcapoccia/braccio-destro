#!/usr/bin/env bash
# Aggiorna l'agente all'ultima versione (o a quella indicata): bash aggiorna.sh [commit]
set -euo pipefail
REF="${1:-main}"
RAW="https://raw.githubusercontent.com/rcapoccia/braccio-destro/$REF"
apt-get -o DPkg::Lock::Timeout=600 install -yq python3-pypdf python3-reportlab >/dev/null
install -d -m 755 /opt/braccio-destro/moduli
for f in posta.py mistral.py pratiche.py questionari.py moduli/itas_car.json moduli/itas_car.pdf moduli/compagnie.json; do
  curl -fsSL "$RAW/$f" -o "/opt/braccio-destro/$f.new" && mv "/opt/braccio-destro/$f.new" "/opt/braccio-destro/$f"
  chmod 644 "/opt/braccio-destro/$f"
done
python3 -m py_compile /opt/braccio-destro/posta.py && echo "Aggiornamento riuscito ($REF)."
