#!/usr/bin/env python3
"""
Braccio destro - Agente posta (Stile Italiano)

Legge le nuove mail via IMAP, le classifica con Mistral (endpoint UE,
zero data retention) e salva una bozza di risposta nella cartella Bozze.

SICUREZZA:
- Questo programma NON invia email: non usa SMTP e non ha credenziali di invio.
- Legge le mail senza segnarle come lette (BODY.PEEK).
- Nei log non scrive mai il contenuto delle mail, solo identificativi e categoria.
- Al primo avvio non elabora le mail vecchie: parte dalle successive.
"""

import email
import email.policy
import email.utils
import html
import imaplib
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from email.message import EmailMessage

# --- Configurazione (da /etc/braccio-destro/posta.env) ---------------------
IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ["IMAP_USER"]
IMAP_PASS = os.environ["IMAP_PASS"]
MISTRAL_API_KEY = os.environ["MISTRAL_API_KEY"]
MISTRAL_URL = os.environ.get("MISTRAL_URL", "https://api.eu.mistral.ai/v1/chat/completions")
# Modello fissato (non "latest", per evitare cambi improvvisi o modelli Labs).
MISTRAL_MODEL = os.environ.get("MISTRAL_MODEL", "mistral-large-2512")
FIRMA = os.environ.get("FIRMA", "Roberto Capoccia\nStile Italiano - Stital")
STATE_FILE = os.environ.get("STATE_FILE", "/var/lib/braccio-destro/posta-state.json")
MAX_MAIL_PER_GIRO = int(os.environ.get("MAX_MAIL_PER_GIRO", "30"))
MAX_CARATTERI = 12000

CATEGORIE = ["clienti", "compagnie", "sinistri", "scadenze", "altro"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("posta")

SYSTEM_PROMPT = f"""Sei l'assistente operativo di Roberto Capoccia, broker assicurativo di Stile Italiano (Stital).
Ricevi una email arrivata nella sua casella. Devi:
1. Classificarla in UNA categoria:
   - "clienti": richieste o comunicazioni di clienti (privati o aziende)
   - "compagnie": comunicazioni da compagnie assicurative, agenzie, intermediari
   - "sinistri": denunce, aggiornamenti, perizie, liquidazioni di sinistri (prevale su "clienti" e "compagnie")
   - "scadenze": rinnovi, scadenze di polizza, quietanze, pagamenti premi in scadenza
   - "altro": tutto il resto (newsletter, pubblicità, fornitori, comunicazioni automatiche)
2. Assegnare la priorità: "alta" (scadenze entro 7 giorni, sinistri, richieste urgenti), "media", "bassa".
3. Scrivere un riassunto di massimo 2 frasi.
4. Decidere se serve una risposta di Roberto (false per newsletter, notifiche automatiche, pubblicità, mail dove è solo in copia).
5. Se serve, scrivere la bozza di risposta in italiano, tono professionale e cordiale, a nome di Roberto.

REGOLE PER LA BOZZA:
- Non inventare mai coperture, massimali, franchigie, importi, date o esiti di sinistri.
- Non confermare mai che un sinistro sarà pagato o che una garanzia è operante: scrivi che verificherai.
- Ogni dato che non risulta dalla mail va scritto come [DA VERIFICARE: ...].
- Se mancano informazioni o documenti per procedere, chiedili in modo chiaro ed elencali.
- Se la mail contiene dati sanitari, non ripeterli nella bozza: riferisciti a "la documentazione medica inviata".
- Termina con la firma:
{FIRMA}

Rispondi SOLO con un oggetto JSON con queste chiavi:
"categoria" (una tra {", ".join(CATEGORIE)}), "priorita" ("alta"|"media"|"bassa"),
"riassunto" (stringa), "serve_risposta" (true|false), "bozza" (stringa, vuota se non serve),
"da_verificare" (lista di stringhe)."""


# --- Stato -------------------------------------------------------------------
def carica_stato():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def salva_stato(stato):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(stato, f)
    os.replace(tmp, STATE_FILE)


# --- Lettura mail -------------------------------------------------------------
def testo_da_messaggio(msg):
    """Estrae il testo leggibile e l'elenco degli allegati."""
    testo, html_testo, allegati = [], [], []
    for parte in msg.walk():
        if parte.is_multipart():
            continue
        nome = parte.get_filename()
        disp = (parte.get_content_disposition() or "").lower()
        if nome or disp == "attachment":
            allegati.append(nome or "allegato senza nome")
            continue
        tipo = parte.get_content_type()
        try:
            contenuto = parte.get_content()
        except Exception:
            payload = parte.get_payload(decode=True) or b""
            contenuto = payload.decode("utf-8", errors="replace")
        if tipo == "text/plain":
            testo.append(contenuto)
        elif tipo == "text/html":
            html_testo.append(contenuto)
    if testo:
        corpo = "\n".join(testo)
    else:
        corpo = "\n".join(html_testo)
        corpo = re.sub(r"(?is)<(script|style).*?</\1>", " ", corpo)
        corpo = re.sub(r"(?s)<br\s*/?>|</p>", "\n", corpo)
        corpo = re.sub(r"(?s)<[^>]+>", " ", corpo)
        corpo = html.unescape(corpo)
    corpo = re.sub(r"[ \t]+", " ", corpo)
    corpo = re.sub(r"\n\s*\n+", "\n\n", corpo).strip()
    return corpo[:MAX_CARATTERI], allegati


def trova_cartella_bozze(imap):
    """Cerca la cartella Bozze (attributo \\Drafts o nomi comuni)."""
    stato, cartelle = imap.list()
    nomi = []
    for riga in cartelle or []:
        r = riga.decode(errors="replace")
        m = re.match(r'\((?P<flag>[^)]*)\) "(?P<sep>[^"]*)" (?P<nome>.+)$', r)
        if not m:
            continue
        nome = m.group("nome").strip()
        nomi.append(nome)
        if "\\Drafts" in m.group("flag"):
            return nome
    for candidato in ["Drafts", "Bozze", "INBOX.Drafts", "INBOX.Bozze", '"INBOX.Drafts"', '"Drafts"']:
        if candidato in nomi or f'"{candidato}"' in nomi:
            return candidato if candidato in nomi else f'"{candidato}"'
    raise RuntimeError("Cartella Bozze non trovata. Cartelle disponibili: " + ", ".join(nomi))


# --- Mistral -------------------------------------------------------------------
def chiedi_a_mistral(mittente, oggetto, data, corpo, allegati):
    contenuto = (
        f"Mittente: {mittente}\nData: {data}\nOggetto: {oggetto}\n"
        f"Allegati: {', '.join(allegati) if allegati else 'nessuno'}\n\n"
        f"Testo:\n{corpo}"
    )
    richiesta = {
        "model": MISTRAL_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": contenuto},
        ],
    }
    req = urllib.request.Request(
        MISTRAL_URL,
        data=json.dumps(richiesta).encode(),
        headers={
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    for tentativo in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                risposta = json.load(r)
            testo = risposta["choices"][0]["message"]["content"]
            dati = json.loads(testo)
            if dati.get("categoria") not in CATEGORIE:
                dati["categoria"] = "altro"
            return dati
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Tetto di spesa o limite di frequenza raggiunto: ci fermiamo.
                raise TettoRaggiunto() from e
            if e.code in (500, 502, 503, 504) and tentativo < 2:
                time.sleep(10 * (tentativo + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if tentativo < 2:
                time.sleep(10 * (tentativo + 1))
                continue
            raise


class TettoRaggiunto(Exception):
    pass


# --- Bozza -------------------------------------------------------------------
def crea_bozza(originale, dati):
    bozza = EmailMessage()
    rispondi_a = originale.get("Reply-To") or originale.get("From", "")
    oggetto = originale.get("Subject", "") or ""
    if not re.match(r"(?i)^(re|r)\s*:", oggetto):
        oggetto = "Re: " + oggetto
    bozza["From"] = IMAP_USER
    bozza["To"] = rispondi_a
    bozza["Subject"] = oggetto
    bozza["Date"] = email.utils.formatdate(localtime=True)
    msg_id = originale.get("Message-ID")
    if msg_id:
        bozza["In-Reply-To"] = msg_id
        refs = (originale.get("References", "") + " " + msg_id).strip()
        bozza["References"] = refs
    # Intestazioni interne: non visibili al destinatario nel testo.
    bozza["X-Braccio-Destro-Categoria"] = dati.get("categoria", "altro")
    bozza["X-Braccio-Destro-Priorita"] = dati.get("priorita", "media")
    bozza.set_content(dati.get("bozza", "").strip() + "\n")
    return bozza


def segna_categoria(imap, uid, dati):
    """Aggiunge un'etichetta (keyword IMAP) alla mail originale, se il server lo consente."""
    etichetta = f"BD_{dati.get('categoria', 'altro')}"
    try:
        imap.uid("STORE", uid, "+FLAGS", f"({etichetta})")
    except Exception:
        pass  # Alcuni server non supportano le etichette: non è un problema.


# --- Giro principale ------------------------------------------------------------
def giro():
    stato = carica_stato()
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(IMAP_USER, IMAP_PASS)
    try:
        bozze = trova_cartella_bozze(imap)
        typ, dati_sel = imap.select("INBOX")
        uidvalidity = imap.untagged_responses.get("UIDVALIDITY", [b"0"])[0].decode()
        typ, risultato = imap.uid("SEARCH", None, "ALL")
        uids = [int(u) for u in (risultato[0] or b"").split()]
        if not uids:
            return

        # Primo avvio (o casella cambiata): non elabora le mail vecchie.
        if stato.get("uidvalidity") != uidvalidity:
            stato = {"uidvalidity": uidvalidity, "ultimo_uid": max(uids)}
            salva_stato(stato)
            log.info("Primo avvio: si parte dalle prossime mail (ultimo UID %s).", max(uids))
            return

        nuove = sorted(u for u in uids if u > stato["ultimo_uid"])[:MAX_MAIL_PER_GIRO]
        for uid in nuove:
            uid_s = str(uid)
            typ, parti = imap.uid("FETCH", uid_s, "(BODY.PEEK[])")
            grezzo = next((p[1] for p in parti if isinstance(p, tuple)), None)
            if grezzo is None:
                stato["ultimo_uid"] = uid
                salva_stato(stato)
                continue
            msg = email.message_from_bytes(grezzo, policy=email.policy.default)
            mittente = msg.get("From", "")

            # Salta le mail inviate da Roberto stesso.
            if IMAP_USER.lower() in mittente.lower():
                stato["ultimo_uid"] = uid
                salva_stato(stato)
                continue

            corpo, allegati = testo_da_messaggio(msg)
            try:
                dati = chiedi_a_mistral(mittente, msg.get("Subject", ""), msg.get("Date", ""), corpo, allegati)
            except TettoRaggiunto:
                log.warning("Mistral ha rifiutato la richiesta (tetto di spesa o limite). Riprovo al prossimo giro.")
                return

            segna_categoria(imap, uid_s, dati)
            if dati.get("serve_risposta") and dati.get("bozza", "").strip():
                bozza = crea_bozza(msg, dati)
                imap.append(bozze, "(\\Draft)", imaplib.Time2Internaldate(time.time()),
                            bozza.as_bytes(policy=email.policy.SMTP))
                log.info("UID %s: categoria=%s priorita=%s -> bozza creata",
                         uid, dati.get("categoria"), dati.get("priorita"))
            else:
                log.info("UID %s: categoria=%s priorita=%s -> nessuna risposta necessaria",
                         uid, dati.get("categoria"), dati.get("priorita"))
            stato["ultimo_uid"] = uid
            salva_stato(stato)
    finally:
        try:
            imap.logout()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        giro()
    except Exception as e:
        # Mai stampare il contenuto della mail: solo il tipo di errore.
        log.error("Errore: %s %s", type(e).__name__, str(e)[:200])
        sys.exit(1)
