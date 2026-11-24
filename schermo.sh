#!/usr/bin/env bash
# Schermo virtuale del server: qui gira il browser per i login a Navisio e intermediari.ai.
# Raggiungibile solo dal server stesso; dall'esterno passa da Caddy e solo dopo l'accesso con Face ID.
set -euo pipefail
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 600x1100x24 -nolisten tcp &
sleep 1
x11vnc -display :99 -localhost -rfbport 5900 -forever -shared -nopw -quiet -noxdamage &
exec websockify --web /usr/share/novnc 127.0.0.1:6080 127.0.0.1:5900
