#!/usr/bin/env bash
# Braccio destro - installazione server (Ubuntu 24.04, Aruba Cloud)
# Installa: aggiornamenti automatici, firewall, protezione accessi e l'agente posta.
# Le password le inserisci tu qui, sul server: non vanno mai in chat.
#
# Uso (come root, dalla console del server):
#   bash <(curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/main/install.sh)
set -euo pipefail

REPO_RAW="${REPO_RAW:-https://raw.githubusercontent.com/rcapoccia/braccio-destro/main}"

if [[ $EUID -ne 0 ]]; then echo "Esegui come root."; exit 1; fi

echo "=== Braccio destro: installazione ==="
read -rp "Server posta in arrivo [mail.stital.it]: " IMAP_HOST; IMAP_HOST=${IMAP_HOST:-mail.stital.it}
read -rp "Indirizzo email [roberto.capoccia@stital.it]: " IMAP_USER; IMAP_USER=${IMAP_USER:-roberto.capoccia@stital.it}
read -rsp "Password della email (non si vede mentre scrivi): " IMAP_PASS; echo
read -rsp "Chiave API Mistral (area di lavoro Braccio destro): " MISTRAL_API_KEY; echo

echo "--- 1/3 Aggiornamenti e sicurezza di base"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
# Al primo avvio Ubuntu installa da solo gli aggiornamenti: aspettiamo che finisca (max 20 minuti).
APT="apt-get -o DPkg::Lock::Timeout=1200"
echo "(se il server sta già installando aggiornamenti, attendo che finisca...)"
$APT update -q
$APT -o Dpkg::Options::=--force-confold upgrade -yq
$APT -o Dpkg::Options::=--force-confold install -yq ca-certificates curl ufw fail2ban unattended-upgrades python3 python3-pypdf python3-reportlab
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now fail2ban

echo "--- 2/3 Firewall: dall'esterno è aperta solo la porta di accesso (22)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable

echo "--- 3/3 Agente posta (solo lettura + bozze, nessun invio)"
id braccio >/dev/null 2>&1 || useradd --system --home /var/lib/braccio-destro --shell /usr/sbin/nologin braccio
install -d -o braccio -g braccio -m 700 /var/lib/braccio-destro
install -d -m 755 /opt/braccio-destro
install -d -m 755 /opt/braccio-destro/moduli
for f in posta.py mistral.py pratiche.py questionari.py istruzioni.py ricerca.py moduli/itas_car.json moduli/itas_car.pdf moduli/compagnie.json; do
  curl -fsSL "$REPO_RAW/$f" -o "/opt/braccio-destro/$f"
  chmod 644 "/opt/braccio-destro/$f"
done
install -d -m 750 -g braccio /etc/braccio-destro

esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
umask 077
{
  echo "IMAP_HOST=\"$(esc "$IMAP_HOST")\""
  echo "IMAP_USER=\"$(esc "$IMAP_USER")\""
  echo "IMAP_PASS=\"$(esc "$IMAP_PASS")\""
  echo "MISTRAL_API_KEY=\"$(esc "$MISTRAL_API_KEY")\""
} > /etc/braccio-destro/posta.env
unset IMAP_PASS MISTRAL_API_KEY
chown root:braccio /etc/braccio-destro/posta.env
chmod 640 /etc/braccio-destro/posta.env

cat > /etc/systemd/system/braccio-posta.service <<'EOF'
[Unit]
Description=Braccio destro - agente posta
After=network-online.target

[Service]
Type=oneshot
User=braccio
EnvironmentFile=/etc/braccio-destro/posta.env
ExecStart=/usr/bin/python3 /opt/braccio-destro/posta.py
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/braccio-destro
PrivateTmp=true
EOF

cat > /etc/systemd/system/braccio-posta.timer <<'EOF'
[Unit]
Description=Controlla la posta ogni 5 minuti

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now braccio-posta.timer
if systemctl start braccio-posta.service; then
  RISULTATO="Collegamento alla posta riuscito."
else
  RISULTATO="ATTENZIONE: il primo controllo è fallito. Leggi il motivo con: journalctl -u braccio-posta -n 20"
fi

echo
echo "=== Fatto ==="
echo "$RISULTATO"
echo "L'agente controlla la posta ogni 5 minuti e mette le bozze nella cartella Bozze."
echo "Al primo avvio non tocca le mail vecchie: lavora solo su quelle che arrivano da adesso."
echo "Per vedere cosa fa: journalctl -u braccio-posta -n 50"
