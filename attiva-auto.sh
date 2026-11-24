#!/usr/bin/env bash
# Da lanciare UNA volta sul server. Dopo, il braccio destro si aggiorna da solo da GitHub (come Railway).
set -euo pipefail
# 1. Aggiornamento da GitHub ogni 2 minuti (ramo "produzione")
curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/produzione/auto-aggiorna.sh -o /usr/local/sbin/braccio-auto-aggiorna
chmod 755 /usr/local/sbin/braccio-auto-aggiorna
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
# 2. Registri tecnici conservati al massimo 30 giorni (DPIA)
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nMaxRetentionSec=30day\n' > /etc/systemd/journald.conf.d/braccio.conf
systemctl restart systemd-journald
# 3. Riavvio automatico alle 4 di notte solo quando un aggiornamento di sicurezza lo richiede
printf 'Unattended-Upgrade::Automatic-Reboot "true";\nUnattended-Upgrade::Automatic-Reboot-Time "04:00";\n' > /etc/apt/apt.conf.d/52braccio-riavvio
systemctl daemon-reload
systemctl enable --now braccio-auto-aggiorna.timer
/usr/local/sbin/braccio-auto-aggiorna || true
echo "FATTO: da ora il braccio destro si aggiorna da solo. Non serve più il terminale."
