#!/usr/bin/env bash
# Aggiornamento automatico di Vega (ogni 2 minuti, come root).
#
# Regole:
#  1. Si installa SOLO ciò che sta sul ramo "produzione-verificata": lo scrivono i collaudi di GitHub,
#     e solo quando sono tutti verdi su quella versione esatta.
#  2. Accesso al repo con una chiave di SOLA LETTURA di questo server (repo privato). Finché il repo è
#     pubblico e la chiave non è ancora stata autorizzata, si usa l'accesso pubblico: così non si ferma nulla.
#  3. Prima di sostituire i file si salva la versione attuale. Se dopo l'aggiornamento la pagina non risponde,
#     si torna da soli alla versione di prima e quella difettosa non viene più riprovata.
#  4. Nessun dato personale nei messaggi.
#
# Limite noto: il controllo dei collaudi protegge dagli errori, non da chi riuscisse a scrivere nel repo
# (potrebbe cambiare anche i collaudi). Per questo il repo va tenuto privato e con accesso a due fattori.
set -euo pipefail

REPO="${VEGA_REPO:-rcapoccia/braccio-destro}"
RAMO="${VEGA_RAMO:-produzione-verificata}"
DEST="${VEGA_DEST:-/opt/braccio-destro}"
STATO="${VEGA_STATO:-/var/lib/braccio-destro-aggiornamenti}"
SBIN="${VEGA_SBIN:-/usr/local/sbin}"
SERVIZIO="${VEGA_SERVIZIO:-braccio-web.service}"
SALUTE="${VEGA_SALUTE:-http://127.0.0.1:8080/api/stato}"
ATTESA_SALUTE="${VEGA_ATTESA_SALUTE:-150}"
CHIAVE="${VEGA_CHIAVE:-/etc/braccio-destro/aggiornamenti/chiave}"
VETRINA="${VEGA_VETRINA:-/run/vega-aggiornamenti}"        # ciò che la pagina può leggere (niente segreti)
URL_PUBBLICO="${VEGA_URL_PUBBLICO:-https://github.com/$REPO.git}"
URL_PRIVATO="${VEGA_URL_PRIVATO:-git@github.com:$REPO.git}"
# Chiave pubblica ufficiale di GitHub: il server parla solo con chi la possiede.
GITHUB_HOST='github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'

export GIT_TERMINAL_PROMPT=0
TMP=""
trap '[[ -n "$TMP" ]] && rm -rf "$TMP"' EXIT

dire() { echo "$*"; }

pubblica_stato() {   # per la pagina "Stato del sistema": solo informazioni non riservate
  install -d -m 755 "$VETRINA"
  python3 - "$VETRINA/stato.json" "$STATO" "${CANALE:-?}" "$CHIAVE.pub" "${1:-}" <<'P'
import json, os, sys, time
dest, stato, canale, pub, esito = sys.argv[1:6]
def leggi(p):
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return ""
dati = {"versione": leggi(f"{stato}/versione")[:7], "precedente": leggi(f"{stato}/precedente")[:7],
        "scartata": leggi(f"{stato}/scartata")[:7], "canale": canale, "chiave_pubblica": leggi(pub),
        "ultimo_controllo": int(time.time()), "esito": esito}
tmp = dest + ".tmp"
with open(tmp, "w") as f:
    json.dump(dati, f)
os.chmod(tmp, 0o644)
os.replace(tmp, dest)
P
}

esegui_sistema() {   # configurazione di sistema: si rilancia quando sistema.sh cambia
  [[ -f "$DEST/sistema.sh" && -x "$DEST/venv/bin/pip" ]] || return 0
  local h; h=$(sha256sum "$DEST/sistema.sh" | cut -d' ' -f1)
  if [[ "$h" != "$(cat "$STATO/sistema" 2>/dev/null || true)" ]]; then
    bash "$DEST/sistema.sh" && echo "$h" > "$STATO/sistema"
  fi
}

scegli_canale() {    # privato (chiave di sola lettura) se funziona, altrimenti pubblico
  CANALE=pubblico; URL="$URL_PUBBLICO"; unset GIT_SSH_COMMAND
  if [[ -f "$CHIAVE" ]]; then
    printf '%s\n' "$GITHUB_HOST" > "$STATO/known_hosts"
    local ssh="ssh -i $CHIAVE -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$STATO/known_hosts -o ConnectTimeout=15"
    if GIT_SSH_COMMAND="$ssh" git ls-remote "$URL_PRIVATO" "refs/heads/$RAMO" >/dev/null 2>&1; then
      CANALE=privato; URL="$URL_PRIVATO"; export GIT_SSH_COMMAND="$ssh"
    fi
  fi
}

in_salute() {        # la pagina risponde?
  local fine=$((SECONDS + ATTESA_SALUTE))
  while (( SECONDS < fine )); do
    curl -fsS -o /dev/null --max-time 5 "$SALUTE" 2>/dev/null && return 0
    sleep 3
  done
  return 1
}

installa_da() {      # $1 = cartella con i file, $2 = elenco dei file
  local origine=$1 f
  while read -r f; do
    [[ -z "$f" ]] && continue
    install -d -m 755 "$DEST/$(dirname "$f")"
    install -m 644 "$origine/$f" "$DEST/$f"
  done < "$2"
  install -m 755 "$origine/auto-aggiorna.sh" "$SBIN/braccio-auto-aggiorna"
  install -m 755 "$origine/braccio-codice.sh" "$SBIN/braccio-codice"
}

librerie() {         # $1 = requirements; installa solo se cambiate. Torna 1 se non riesce.
  [[ -x "$DEST/venv/bin/pip" ]] || return 0
  local nuove; nuove=$(sha256sum "$1" | cut -d' ' -f1)
  [[ "$nuove" == "$(cat "$STATO/librerie" 2>/dev/null || true)" ]] && return 0
  "$DEST/venv/bin/pip" install -q -r "$1" || return 1
  echo "$nuove" > "$STATO/librerie"
}

gestito() {          # il servizio è di quelli che devono stare accesi?
  systemctl is-active -q "$SERVIZIO" || systemctl is-enabled -q "$SERVIZIO" 2>/dev/null || systemctl is-failed -q "$SERVIZIO"
}

ripristina() {       # rimette la versione salvata. $1 = elenco dei file della versione da togliere (facoltativo)
  local salva="$STATO/salvata" f
  [[ -d "$salva/file" ]] || return 1
  if [[ -n "${1:-}" && -f "$1" ]]; then
    while read -r f; do [[ -f "$salva/file/$f" ]] || rm -f "$DEST/$f"; done < "$1"
  fi
  (cd "$salva/file" && find . -type f -print0 | while IFS= read -r -d '' f; do install -D -m 644 "$f" "$DEST/$f"; done)
  for f in braccio-auto-aggiorna braccio-codice; do [[ -f "$salva/$f" ]] && install -m 755 "$salva/$f" "$SBIN/$f"; done
  if [[ -f "$DEST/requirements-web.txt" ]]; then
    librerie "$DEST/requirements-web.txt" || rm -f "$STATO/librerie"   # si riproverà al prossimo giro
  fi
  systemctl reset-failed "$SERVIZIO" 2>/dev/null || true
  if gestito; then systemctl restart "$SERVIZIO" || true; fi
  rm -f "$STATO/in-corso" "$STATO/in-corso.elenco"
}

principale() {
  install -d -m 700 "$STATO"
  exec 9>"$STATO/blocco"; flock -n 9 || exit 0          # mai due aggiornamenti insieme
  command -v git >/dev/null || { apt-get -o DPkg::Lock::Timeout=600 install -yq git openssh-client >/dev/null; }

  # Un aggiornamento interrotto a metà (spegnimento, disco pieno): si rimette la versione salvata.
  if [[ -f "$STATO/in-corso" ]]; then
    dire "L'ultimo aggiornamento si era interrotto a metà: rimetto la versione di prima."
    ripristina "$STATO/in-corso.elenco" || true
  fi

  esegui_sistema || dire "Configurazione di sistema non riuscita: riprovo al prossimo giro."
  scegli_canale

  local sha attuale
  sha=$(git ls-remote "$URL" "refs/heads/$RAMO" 2>/dev/null | cut -f1 || true)
  attuale=$(cat "$STATO/versione" 2>/dev/null || true)
  if [[ -z "$sha" ]]; then pubblica_stato "nessuna versione verificata da leggere"; exit 0; fi
  if [[ "$sha" == "$attuale" ]]; then pubblica_stato "aggiornato"; exit 0; fi
  if [[ "$sha" == "$(cat "$STATO/scartata" 2>/dev/null || true)" ]]; then
    pubblica_stato "l'ultima versione era difettosa: tengo quella di prima"; exit 0
  fi

  TMP=$(mktemp -d); local tmp=$TMP
  mkdir "$tmp/nuova" "$tmp/git"
  git -C "$tmp/git" init -q
  git -C "$tmp/git" fetch -q --depth 1 "$URL" "refs/heads/$RAMO"
  if [[ "$(git -C "$tmp/git" rev-parse FETCH_HEAD)" != "$sha" ]]; then
    dire "La versione è cambiata mentre la scaricavo: riprovo al prossimo giro."; exit 0
  fi
  git -C "$tmp/git" archive FETCH_HEAD | tar -x -C "$tmp/nuova"

  # --- controlli prima di toccare qualunque cosa ---
  local elenco="$tmp/elenco" f ok=1
  grep -v '^\s*$' "$tmp/nuova/file-elenco.txt" > "$elenco" || true
  # nomi dei file: solo dentro la cartella di Vega (niente "..", percorsi assoluti, spazi) e nessun collegamento
  if grep -qvE '^[A-Za-z0-9_][A-Za-z0-9._/-]*$' "$elenco" || grep -qE '(^|/)\.\.(/|$)' "$elenco"; then
    dire "L'elenco dei file contiene nomi non ammessi."; ok=0
  fi
  if git -C "$tmp/git" ls-tree -r FETCH_HEAD | grep -q '^120000'; then
    dire "La versione contiene collegamenti a file (non ammessi)."; ok=0
  fi
  while read -r f; do [[ -f "$tmp/nuova/$f" ]] || { dire "Manca il file $f."; ok=0; }; done < "$elenco"
  for f in auto-aggiorna.sh braccio-codice.sh; do [[ -f "$tmp/nuova/$f" ]] || ok=0; done
  if [[ $ok == 1 ]]; then
    (cd "$tmp/nuova" && grep '\.py$' "$elenco" | xargs -r python3 -c 'import ast,sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]') || ok=0
    (cd "$tmp/nuova" && { grep '\.sh$' "$elenco"; echo auto-aggiorna.sh; } | xargs -r -n1 bash -n) || ok=0
  fi
  if [[ $ok == 0 ]]; then
    echo "$sha" > "$STATO/scartata"
    pubblica_stato "versione ${sha:0:7} non valida: tengo quella attuale"
    dire "Versione ${sha:0:7} non valida: tengo quella attuale."; exit 1
  fi

  # --- copia di sicurezza della versione attuale ---
  local salva="$STATO/salvata"; rm -rf "$salva.nuova"; mkdir -p "$salva.nuova/file"
  local vecchio_elenco="$STATO/elenco"; [[ -f "$vecchio_elenco" ]] || vecchio_elenco="$elenco"
  sort -u "$vecchio_elenco" "$elenco" | while read -r f; do
    [[ -f "$DEST/$f" ]] && install -D -m 644 "$DEST/$f" "$salva.nuova/file/$f"
  done
  sort -u "$vecchio_elenco" > "$salva.nuova/elenco"
  for f in braccio-auto-aggiorna braccio-codice; do [[ -f "$SBIN/$f" ]] && cp -p "$SBIN/$f" "$salva.nuova/$f"; done
  rm -rf "$salva"; mv "$salva.nuova" "$salva"

  # --- librerie (prima dei file: se falliscono non si cambia nulla) ---
  if ! librerie "$tmp/nuova/requirements-web.txt"; then
    pubblica_stato "librerie della versione ${sha:0:7} non installate: riprovo"
    dire "Librerie della versione ${sha:0:7} non installate: tengo quella attuale e riprovo."; exit 1
  fi

  # --- sostituzione, riavvio, verifica ---
  local era_gestito=0; gestito && era_gestito=1
  cp "$elenco" "$STATO/in-corso.elenco"; echo "$sha" > "$STATO/in-corso"
  installa_da "$tmp/nuova" "$elenco"
  if [[ $era_gestito == 1 ]]; then
    systemctl reset-failed "$SERVIZIO" 2>/dev/null || true
    systemctl restart "$SERVIZIO" || true
  fi
  if [[ $era_gestito == 1 ]] && ! in_salute; then
    dire "Con la versione ${sha:0:7} la pagina non risponde: torno alla versione di prima."
    ripristina "$elenco" || true
    if in_salute; then
      # la versione di prima risponde: il difetto è della nuova, che non verrà riprovata
      echo "$sha" > "$STATO/scartata"
      pubblica_stato "versione ${sha:0:7} difettosa: tornato alla precedente"
    else
      # non risponde nemmeno quella di prima: il problema non è la versione, la si riproverà
      pubblica_stato "ATTENZIONE: la pagina non risponde nemmeno con la versione di prima"
    fi
    exit 1
  fi
  rm -f "$STATO/in-corso" "$STATO/in-corso.elenco"

  [[ -n "$attuale" ]] && echo "$attuale" > "$STATO/precedente"
  echo "$sha" > "$STATO/versione"
  cp "$elenco" "$STATO/elenco"
  esegui_sistema || dire "Configurazione di sistema non riuscita: riprovo al prossimo giro."
  if [[ $era_gestito == 1 ]] && ! in_salute; then
    pubblica_stato "ATTENZIONE: dopo la configurazione di sistema la pagina non risponde"
    dire "Dopo la configurazione di sistema la pagina non risponde."; exit 1
  fi
  pubblica_stato "aggiornato"
  dire "Aggiornato alla versione ${sha:0:7} (canale $CANALE)."
}

principale "$@"; exit 0
