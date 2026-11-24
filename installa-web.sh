#!/usr/bin/env bash
# Braccio destro - pagina web (braccio.stital.it) + aggiornamento automatico da GitHub.
# Da lanciare UNA volta sul server, come root:
#   bash <(curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/produzione/installa-web.sh)
# Non chiede password: usa quelle già salvate sul server durante la prima installazione.
set -euo pipefail
DOMINIO="${DOMINIO:-braccio.stital.it}"
RAW="https://raw.githubusercontent.com/rcapoccia/braccio-destro/produzione"
DEST=/opt/braccio-destro

if [[ $EUID -ne 0 ]]; then echo "Esegui come root."; exit 1; fi
[[ -f /etc/braccio-destro/posta.env ]] || { echo "Manca la prima installazione (install.sh)."; exit 1; }

echo "--- 1/6 Controllo dell'indirizzo $DOMINIO"
IP_SERVER=$(curl -fsS https://api.ipify.org || true)
IP_DNS=$(getent ahostsv4 "$DOMINIO" | awk 'NR==1{print $1}' || true)
if [[ "$IP_DNS" != "$IP_SERVER" ]]; then
  echo "ATTENZIONE: $DOMINIO non punta ancora a questo server ($IP_SERVER, trovato: ${IP_DNS:-niente})."
  echo "Proseguo: il certificato HTTPS verrà preso da solo appena il record su Fleanet sarà attivo."
fi

echo "--- 2/6 Programmi necessari"
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT="apt-get -o DPkg::Lock::Timeout=1200"
$APT update -q
$APT -o Dpkg::Options::=--force-confold install -yq caddy python3-venv

echo "--- 3/6 File del braccio destro (da GitHub, ramo produzione)"
curl -fsSL "$RAW/auto-aggiorna.sh" -o /usr/local/sbin/braccio-auto-aggiorna
chmod 755 /usr/local/sbin/braccio-auto-aggiorna
rm -f /var/lib/braccio-destro-aggiornamenti/versione
/usr/local/sbin/braccio-auto-aggiorna
[[ -d $DEST/venv ]] || python3 -m venv $DEST/venv
$DEST/venv/bin/pip install -q --upgrade pip
$DEST/venv/bin/pip install -q -r $DEST/requirements-web.txt

echo "--- 4/6 Servizio della pagina"
install -d -o braccio -g braccio -m 700 /var/lib/braccio-destro/web
cat > /etc/systemd/system/braccio-web.service <<EOF
[Unit]
Description=Braccio destro - pagina web
After=network-online.target

[Service]
User=braccio
EnvironmentFile=/etc/braccio-destro/posta.env
Environment=WEB_DOMINIO=$DOMINIO
ExecStart=$DEST/venv/bin/uvicorn app:app --app-dir $DEST/web --host 127.0.0.1 --port 8080 --proxy-headers --no-server-header
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/braccio-destro
PrivateTmp=true
MemoryMax=1500M

[Install]
WantedBy=multi-user.target
EOF

echo "--- 5/6 HTTPS (Caddy) e firewall"
cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO {
	encode gzip
	request_body {
		max_size 30MB
	}
	header {
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options nosniff
		X-Frame-Options DENY
		Referrer-Policy no-referrer
		Content-Security-Policy "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
		Permissions-Policy "camera=(), geolocation=()"
		-Server
	}
	reverse_proxy 127.0.0.1:8080
}
EOF
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null

echo "--- 6/6 Aggiornamento automatico (ogni 2 minuti) e manutenzione"
cat > /etc/systemd/system/braccio-auto-aggiorna.service <<'U'
[Unit]
Description=Braccio destro - aggiornamento da GitHub
After=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/braccio-auto-aggiorna
U
cat > /etc/systemd/system/braccio-auto-aggiorna.timer <<'U'
[Unit]
Description=Braccio destro - controlla GitHub ogni 2 minuti
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
U
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nMaxRetentionSec=30day\n' > /etc/systemd/journald.conf.d/braccio.conf
systemctl restart systemd-journald
printf 'Unattended-Upgrade::Automatic-Reboot "true";\nUnattended-Upgrade::Automatic-Reboot-Time "04:00";\n' > /etc/apt/apt.conf.d/52braccio-riavvio

systemctl daemon-reload
systemctl enable --now braccio-web.service braccio-auto-aggiorna.timer
systemctl restart braccio-web.service caddy

sleep 3
if systemctl is-active --quiet braccio-web.service; then
  echo "Pagina avviata."
else
  echo "ATTENZIONE: la pagina non è partita. Motivo: journalctl -u braccio-web -n 30"
fi
/usr/local/sbin/braccio-codice
echo
echo "=== Fatto ==="
echo "1. Sul telefono apri https://$DOMINIO"
echo "2. Inserisci il codice qui sopra e tocca 'Attiva con Face ID'."
echo "Da ora il braccio destro si aggiorna da solo da GitHub: il terminale non serve più."
