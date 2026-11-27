#!/usr/bin/env bash
# Installa (o aggiorna) la "scatola chiusa" di Vega. Lo lancia sistema.sh sul server e le prove su GitHub:
# così si prova esattamente quello che gira in produzione.
set -euo pipefail
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
SystemCallFilter=~@privileged @mount @debug @cpu-emulation @obsolete @raw-io @reboot @swap
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
