#!/usr/bin/env bash
# Configurazione di sistema del braccio destro. La lancia da solo l'aggiornamento automatico (come root)
# quando questo file cambia su GitHub. Si può rilanciare quante volte si vuole: rifà solo ciò che serve.
set -euo pipefail
DEST=/opt/braccio-destro
DOMINIO=braccio.stital.it
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT="apt-get -o DPkg::Lock::Timeout=1200"

# Schermo virtuale + browser per i collegamenti (Navisio, intermediari.ai)
$APT install -yq xvfb x11vnc novnc websockify fonts-dejavu-core >/dev/null
export PLAYWRIGHT_BROWSERS_PATH=$DEST/ms-playwright
$DEST/venv/bin/playwright install --with-deps chromium >/dev/null
chmod -R a+rX $DEST/ms-playwright
install -m 755 $DEST/schermo.sh /usr/local/sbin/braccio-schermo
install -d -o braccio -g braccio -m 700 /var/lib/braccio-destro/browser

cat > /etc/systemd/system/braccio-schermo.service <<'U'
[Unit]
Description=Braccio destro - schermo virtuale per i collegamenti
After=network-online.target
[Service]
User=braccio
ExecStart=/usr/local/sbin/braccio-schermo
Restart=always
RestartSec=3
PrivateTmp=false
[Install]
WantedBy=multi-user.target
U

# La pagina deve poter avviare il browser sullo schermo virtuale
mkdir -p /etc/systemd/system/braccio-web.service.d
cat > /etc/systemd/system/braccio-web.service.d/browser.conf <<U
[Service]
Environment=DISPLAY=:99
Environment=PLAYWRIGHT_BROWSERS_PATH=$DEST/ms-playwright
PrivateTmp=false
U

cat > /etc/caddy/Caddyfile <<U
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
		Permissions-Policy "camera=(), geolocation=()"
		-Server
	}
	handle_path /schermo/* {
		forward_auth 127.0.0.1:8080 {
			uri /api/verifica
			header_up -Upgrade
			header_up -Connection
		}
		header Content-Security-Policy "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' wss://$DOMINIO; frame-ancestors 'none'"
		reverse_proxy 127.0.0.1:6080
	}
	handle {
		header Content-Security-Policy "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
		reverse_proxy 127.0.0.1:8080
	}
}
U

systemctl daemon-reload
systemctl enable --now braccio-schermo.service
systemctl restart braccio-schermo.service braccio-web.service
systemctl restart caddy   # ripartendo, Caddy richiede subito il certificato HTTPS
echo "Configurazione di sistema aggiornata."
