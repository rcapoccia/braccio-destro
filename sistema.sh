#!/usr/bin/env bash
# Configurazione di sistema del braccio destro. La lancia da solo l'aggiornamento automatico (come root)
# quando questo file cambia su GitHub. Si può rilanciare quante volte si vuole: rifà solo ciò che serve.
set -euo pipefail
DEST=/opt/braccio-destro
DOMINIO=braccio.stital.it
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT="apt-get -o DPkg::Lock::Timeout=1200"

# Vega: cartella per il modello che riconosce i dati dei clienti, più memoria per il servizio della pagina,
# riserva di memoria su disco (swap) se manca. Va per primo, così funziona anche se il resto fallisce.
install -d -o braccio -g braccio -m 700 /var/lib/braccio-destro/modelli
mkdir -p /etc/systemd/system/braccio-web.service.d
VEGA_CONF=$(printf '[Service]\nEnvironment=HF_HOME=/var/lib/braccio-destro/modelli\nMemoryMax=2800M\n')
VEGA_CAMBIATO=0
if [[ "$(cat /etc/systemd/system/braccio-web.service.d/vega.conf 2>/dev/null)" != "$VEGA_CONF" ]]; then
  printf '%s\n' "$VEGA_CONF" > /etc/systemd/system/braccio-web.service.d/vega.conf
  VEGA_CAMBIATO=1
fi
if [[ -z "$(swapon --noheadings)" && ! -f /swapfile ]] && [[ $(df --output=avail -k / | tail -1) -gt 8000000 ]]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
# si riavvia la pagina solo se la configurazione è cambiata davvero (un riavvio interrompe i lavori in corso)
if [[ $VEGA_CAMBIATO == 1 ]]; then
  systemctl daemon-reload
  systemctl try-restart braccio-web.service || true
fi

# Vega: la "scatola chiusa" dove girano i programmi che Vega scrive (come il code interpreter di ChatGPT/Claude).
# Utente dedicato senza privilegi, niente rete, nessun accesso a posta, chiavi e dati del braccio destro.
id vegasbx >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin vegasbx
WEB_RIAVVIO=0
if ! id -nG braccio | tr ' ' '\n' | grep -qx vegasbx; then usermod -aG vegasbx braccio; WEB_RIAVVIO=1; fi
SCATOLA_UNIT=$(cat <<'U'
[Unit]
Description=Vega - scatola chiusa per i programmi
After=network.target

[Service]
User=vegasbx
Group=vegasbx
ExecStart=/opt/braccio-destro/venv/bin/python -I /opt/braccio-destro/scatola.py
Environment=SCATOLA_PYTHON=/opt/braccio-destro/venv/bin/python SCATOLA_LAVORO=/var/lib/vega-scatola SCATOLA_SOCKET=/run/vega-scatola/socket
RuntimeDirectory=vega-scatola
RuntimeDirectoryMode=0750
StateDirectory=vega-scatola
StateDirectoryMode=0700
UMask=0077
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
IPAddressDeny=any
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
InaccessiblePaths=/etc/braccio-destro /var/lib/braccio-destro /var/log /root /home
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources @mount @debug @cpu-emulation @obsolete @raw-io @reboot @swap
MemoryMax=1800M
CPUQuota=150%
TasksMax=96
Restart=on-failure

[Install]
WantedBy=multi-user.target
U
)
SCATOLA_CAMBIATA=0
if [[ "$(cat /etc/systemd/system/vega-scatola.service 2>/dev/null)" != "$SCATOLA_UNIT" ]]; then
  printf '%s\n' "$SCATOLA_UNIT" > /etc/systemd/system/vega-scatola.service
  SCATOLA_CAMBIATA=1
fi
if [[ $SCATOLA_CAMBIATA == 1 ]]; then
  systemctl daemon-reload
  systemctl enable vega-scatola.service >/dev/null 2>&1 || true
  systemctl restart vega-scatola.service || true
else
  systemctl is-active --quiet vega-scatola.service || systemctl start vega-scatola.service || true
fi
[[ $WEB_RIAVVIO == 1 ]] && { systemctl try-restart braccio-web.service || true; }

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
