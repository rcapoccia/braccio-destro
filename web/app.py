"""
Braccio destro - pagina web privata (braccio.stital.it)

Gira sul server Aruba dietro Caddy (HTTPS). Accesso solo con passkey (Face ID / impronta), niente password.
I dati dei clienti restano su questo server e vanno solo a Mistral (UE, zero data retention).

SICUREZZA:
- Nessun invio automatico: una mail parte SOLO quando Roberto tocca "Approva e invia" su una bozza.
- Nei log non si scrive mai il contenuto delle mail o dei documenti.
- I documenti trovati restano in memoria per poche ore e non vengono salvati su disco.
"""

import base64
import email
import email.header
import email.policy
import email.utils
import hashlib
import hmac
import imaplib
import json
import logging
import mimetypes
import os
import re
import secrets
import smtplib
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import webauthn
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement, UserVerificationRequirement)

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))

import istruzioni  # noqa: E402
import mistral  # noqa: E402
import posta  # noqa: E402
import pratiche  # noqa: E402
import biblioteca  # noqa: E402
import esperto  # noqa: E402

DOMINIO = os.environ.get("WEB_DOMINIO", "braccio.stital.it")
ORIGINE = f"https://{DOMINIO}"
DATI = os.environ.get("WEB_DATI", "/var/lib/braccio-destro/web")
SMTP_HOST = os.environ.get("SMTP_HOST", posta.IMAP_HOST)
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
DURATA_SESSIONE = 12 * 3600
DURATA_FILE = 6 * 3600
MAX_UPLOAD = 25 * 1024 * 1024

log = logging.getLogger("web")
os.makedirs(DATI, mode=0o700, exist_ok=True)
FILE_CREDENZIALI = os.path.join(DATI, "credenziali.json")
FILE_ATTIVAZIONE = os.path.join(DATI, "attivazione.json")
FILE_CHAT = os.path.join(DATI, "chat.json")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
blocco = threading.Lock()
lavoratori = ThreadPoolExecutor(max_workers=2)
sessioni = {}      # token -> scadenza
sfide = {}         # token -> (sfida, scadenza)
lavori = {}        # id -> {"stato", "risultato"}
documenti = {}     # id -> (nome, dati, scadenza)


# --- File di stato ------------------------------------------------------------
def _leggi_json(percorso, predefinito):
    try:
        with open(percorso) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return predefinito


def _scrivi_json(percorso, dati):
    tmp = percorso + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(dati, f, ensure_ascii=False)
    os.replace(tmp, percorso)


def _pulisci():
    ora = time.time()
    for d in (sessioni,):
        for k in [k for k, v in d.items() if v < ora]:
            d.pop(k, None)
    for k in [k for k, v in sfide.items() if v[1] < ora]:
        sfide.pop(k, None)
    for k in [k for k, v in documenti.items() if v[2] < ora]:
        documenti.pop(k, None)
    for k in [k for k, v in lavori.items() if v.get("fine", ora) < ora - DURATA_FILE]:
        lavori.pop(k, None)


# --- Sicurezza delle richieste ----------------------------------------------------
@app.middleware("http")
async def protezione(request: Request, call_next):
    _pulisci()
    if request.method not in ("GET", "HEAD"):
        # Le richieste che cambiano qualcosa arrivano solo dalla pagina stessa.
        if request.headers.get("origin") not in (ORIGINE, None) or request.headers.get("x-braccio") != "1":
            return JSONResponse({"errore": "Richiesta non valida."}, status_code=403)
    percorso = request.url.path
    libere = ("/api/stato", "/api/registra/", "/api/accesso/")
    if percorso.startswith("/api/") and not percorso.startswith(libere):
        token = request.cookies.get("sessione", "")
        if sessioni.get(token, 0) < time.time():
            return JSONResponse({"errore": "Accesso scaduto. Entra di nuovo."}, status_code=401)
    risposta = await call_next(request)
    risposta.headers["Cache-Control"] = "no-store"
    return risposta


def _apri_sessione(risposta):
    token = secrets.token_urlsafe(32)
    sessioni[token] = time.time() + DURATA_SESSIONE
    risposta.set_cookie("sessione", token, max_age=DURATA_SESSIONE, httponly=True, secure=True, samesite="strict")


def _nuova_sfida(risposta, sfida):
    token = secrets.token_urlsafe(24)
    sfide[token] = (sfida, time.time() + 300)
    risposta.set_cookie("sfida", token, max_age=300, httponly=True, secure=True, samesite="strict")


def _prendi_sfida(request):
    voce = sfide.pop(request.cookies.get("sfida", ""), None)
    if not voce or voce[1] < time.time():
        raise HTTPException(400, "Tempo scaduto, riprova.")
    return voce[0]


# --- Accesso con passkey -----------------------------------------------------------
@app.get("/api/stato")
def stato(request: Request):
    return {"registrato": bool(_leggi_json(FILE_CREDENZIALI, [])),
            "dentro": sessioni.get(request.cookies.get("sessione", ""), 0) > time.time()}


def _codice_valido(codice):
    """Codice di attivazione monouso generato sul server con braccio-codice (5 tentativi, 24 ore)."""
    with blocco:
        att = _leggi_json(FILE_ATTIVAZIONE, None)
        if not att or att.get("scade", 0) < time.time() or att.get("errori", 0) >= 5:
            return False
        hash_dato = hashlib.sha256(re.sub(r"\D", "", codice or "").encode()).hexdigest()
        if hmac.compare_digest(hash_dato, att.get("hash", "")):
            return True
        att["errori"] = att.get("errori", 0) + 1
        _scrivi_json(FILE_ATTIVAZIONE, att)
        return False


@app.post("/api/registra/inizio")
async def registra_inizio(request: Request):
    corpo = await request.json()
    if not _codice_valido(corpo.get("codice")):
        raise HTTPException(403, "Codice non valido o scaduto. Generane uno nuovo dal server.")
    esistenti = _leggi_json(FILE_CREDENZIALI, [])
    opzioni = webauthn.generate_registration_options(
        rp_id=DOMINIO, rp_name="Braccio destro", user_name=posta.IMAP_USER, user_display_name="Roberto",
        user_id=hashlib.sha256(posta.IMAP_USER.encode()).digest()[:16],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"])) for c in esistenti])
    risposta = Response(webauthn.options_to_json(opzioni), media_type="application/json")
    _nuova_sfida(risposta, opzioni.challenge)
    return risposta


@app.post("/api/registra/fine")
async def registra_fine(request: Request):
    corpo = await request.json()
    sfida = _prendi_sfida(request)
    if not _codice_valido(corpo.get("codice")):
        raise HTTPException(403, "Codice non valido o scaduto.")
    try:
        v = webauthn.verify_registration_response(credential=corpo["credenziale"], expected_challenge=sfida,
                                                  expected_rp_id=DOMINIO, expected_origin=ORIGINE,
                                                  require_user_verification=True)
    except Exception:
        raise HTTPException(400, "Registrazione non riuscita, riprova.")
    with blocco:
        cred = _leggi_json(FILE_CREDENZIALI, [])
        cred.append({"id": bytes_to_base64url(v.credential_id), "chiave": bytes_to_base64url(v.credential_public_key),
                     "contatore": v.sign_count, "nome": (corpo.get("nome") or "Dispositivo")[:40],
                     "creata": time.strftime("%Y-%m-%d %H:%M")})
        _scrivi_json(FILE_CREDENZIALI, cred)
        os.remove(FILE_ATTIVAZIONE)   # il codice vale una volta sola
    log.info("Nuovo dispositivo registrato.")
    risposta = JSONResponse({"ok": True})
    _apri_sessione(risposta)
    return risposta


@app.post("/api/accesso/inizio")
def accesso_inizio():
    opzioni = webauthn.generate_authentication_options(rp_id=DOMINIO,
                                                       user_verification=UserVerificationRequirement.REQUIRED)
    risposta = Response(webauthn.options_to_json(opzioni), media_type="application/json")
    _nuova_sfida(risposta, opzioni.challenge)
    return risposta


@app.post("/api/accesso/fine")
async def accesso_fine(request: Request):
    corpo = await request.json()
    sfida = _prendi_sfida(request)
    cred_id = (corpo.get("credenziale") or {}).get("id", "")
    with blocco:
        tutte = _leggi_json(FILE_CREDENZIALI, [])
        cred = next((c for c in tutte if c["id"] == cred_id), None)
        if not cred:
            raise HTTPException(403, "Dispositivo non riconosciuto.")
        try:
            v = webauthn.verify_authentication_response(
                credential=corpo["credenziale"], expected_challenge=sfida, expected_rp_id=DOMINIO,
                expected_origin=ORIGINE, credential_public_key=base64url_to_bytes(cred["chiave"]),
                credential_current_sign_count=cred["contatore"], require_user_verification=True)
        except Exception:
            raise HTTPException(403, "Accesso non riuscito.")
        cred["contatore"] = v.new_sign_count
        _scrivi_json(FILE_CREDENZIALI, tutte)
    risposta = JSONResponse({"ok": True})
    _apri_sessione(risposta)
    return risposta


@app.post("/api/esci")
def esci(request: Request):
    sessioni.pop(request.cookies.get("sessione", ""), None)
    risposta = JSONResponse({"ok": True})
    risposta.delete_cookie("sessione")
    return risposta


# --- Posta -------------------------------------------------------------------------
def _imap():
    imap = imaplib.IMAP4_SSL(posta.IMAP_HOST, posta.IMAP_PORT)
    imap.login(posta.IMAP_USER, posta.IMAP_PASS)
    return imap


def _chiudi(imap):
    try:
        imap.logout()
    except Exception:
        pass


def _intestazione(valore):
    try:
        return str(email.header.make_header(email.header.decode_header(valore or "")))
    except Exception:
        return valore or ""


@app.get("/api/posta")
def elenco_posta():
    imap = _imap()
    try:
        imap.select("INBOX", readonly=True)
        typ, r = imap.uid("SEARCH", None, "ALL")
        uids = (r[0] or b"").split()[-40:]
        if not uids:
            return []
        typ, parti = imap.uid("FETCH", b",".join(uids).decode(),
                              "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        elenco = []
        for p in parti:
            if not isinstance(p, tuple):
                continue
            uid = re.search(rb"UID (\d+)", p[0])
            h = email.message_from_bytes(p[1], policy=email.policy.default)
            if not uid or "[Braccio destro]" in str(h.get("Subject", "")):
                continue
            nome, indirizzo = email.utils.parseaddr(str(h.get("From", "")))
            elenco.append({"uid": uid.group(1).decode(), "da": nome or indirizzo,
                           "oggetto": str(h.get("Subject", "")) or "(senza oggetto)",
                           "data": _data_breve(h.get("Date"))})
        return list(reversed(elenco))
    finally:
        _chiudi(imap)


def _data_breve(valore):
    try:
        d = email.utils.parsedate_to_datetime(str(valore))
        return d.strftime("%d/%m %H:%M")
    except Exception:
        return ""


# --- Chat: istruzioni ------------------------------------------------------------------
def _salva_chat(voce):
    with blocco:
        chat = _leggi_json(FILE_CHAT, [])
        chat.append(voce)
        _scrivi_json(FILE_CHAT, chat[-150:])


@app.get("/api/chat")
def leggi_chat():
    chat = _leggi_json(FILE_CHAT, [])
    for v in chat:   # i file scaduti non si scaricano più
        for f in v.get("file", []):
            f["disponibile"] = f["id"] in documenti
    return chat


@app.post("/api/chat")
async def nuova_istruzione(testo: str = Form(""), uid_mail: str = Form(""), file: list[UploadFile] = File(default=[])):
    testo = testo.strip()
    if not testo:
        raise HTTPException(400, "Scrivi cosa deve fare.")
    caricati, peso = [], 0
    for f in file:
        dati = await f.read()
        peso += len(dati)
        if peso > MAX_UPLOAD:
            raise HTTPException(400, "File troppo grandi (massimo 25 MB in tutto).")
        caricati.append((os.path.basename(f.filename or "documento"), dati))
    if uid_mail and not uid_mail.isdigit():
        raise HTTPException(400, "Mail non valida.")
    id_lavoro = uuid.uuid4().hex
    _salva_chat({"id": uuid.uuid4().hex, "chi": "roberto", "testo": testo, "ora": time.strftime("%d/%m %H:%M"),
                 "allegati": [n for n, _ in caricati], "mail": uid_mail or None})
    lavori[id_lavoro] = {"stato": "in corso"}
    m = re.match(r"(?is)^\s*(?:secondo\s+parere|esperto|chiedi\s+all'esperto|domanda)\s*[:\-]\s*(.+)$", testo)
    if m:
        lavoratori.submit(_esegui_parere, id_lavoro, m.group(1).strip(), uid_mail)
    else:
        lavoratori.submit(_esegui, id_lavoro, testo, uid_mail, caricati)
    return {"lavoro": id_lavoro}


@app.get("/api/lavoro/{id_lavoro}")
def stato_lavoro(id_lavoro: str):
    lav = lavori.get(id_lavoro)
    if not lav:
        raise HTTPException(404, "Lavoro non trovato.")
    return lav


def _esegui(id_lavoro, testo, uid_mail, caricati):
    voce = {"id": uuid.uuid4().hex, "chi": "braccio", "ora": "", "testo": ""}
    imap = None
    try:
        imap = _imap()
        imap.select("INBOX")
        corpo, allegati = testo, list(caricati)
        if uid_mail:
            originale = posta.leggi(imap, uid_mail)
            if originale is None:
                raise ValueError("mail non trovata")
            testo_orig, _ = posta.testo_da_messaggio(originale)
            corpo += (f"\n\nDa: {originale.get('From', '')}\nA: {originale.get('To', '')}\n"
                      f"Cc: {originale.get('Cc', '') or '-'}\nData: {originale.get('Date', '')}\n"
                      f"Oggetto: {originale.get('Subject', '')}\nMessage-ID: {originale.get('Message-ID', '')}\n\n"
                      f"{testo_orig}")
            for parte in originale.iter_attachments():
                dati = parte.get_payload(decode=True)
                if dati:
                    allegati.append((parte.get_filename() or "allegato", dati))
        msg = EmailMessage()
        msg["From"] = posta.IMAP_USER
        msg["To"] = posta.IMAP_USER
        msg["Subject"] = "AI: " + testo.splitlines()[0][:80]
        msg.set_content(corpo)
        for nome, dati in allegati:
            tipo = (mimetypes.guess_type(nome)[0] or "application/octet-stream").split("/", 1)
            msg.add_attachment(dati, maintype=tipo[0], subtype=tipo[1], filename=nome)

        dati, risultato, nota = istruzioni.esegui(imap, msg, corpo, "INBOX", posta.IMAP_USER)
        if dati.get("azione") == "ricerca":
            oggetto, testo_nota, trovati = risultato
            voce["tipo"] = "ricerca"
            voce["titolo"] = oggetto
            voce["testo"] = testo_nota.replace("-- Braccio destro (nota interna, non inoltrare)", "").strip()
            voce["file"] = [_registra_documento(n, d) for n, d in trovati]
        else:
            bozza = posta.bozza_da_istruzione(dati, risultato)
            imap.append(posta.trova_cartella_bozze(imap), "(\\Draft)", imaplib.Time2Internaldate(time.time()),
                        bozza.as_bytes(policy=email.policy.SMTP))
            voce.update({"tipo": "bozza", "mid": bozza["Message-ID"], "a": dati["a"], "cc": dati["cc"],
                         "oggetto": dati["oggetto"], "testo": dati["bozza"].strip(),
                         "allegati": [n for n, _ in risultato],
                         "nota": (nota or "").replace("-- Braccio destro (nota interna, non inoltrare)", "").strip(),
                         "stato_bozza": "da approvare"})
        log.info("Istruzione eseguita dalla pagina: %s", voce.get("tipo"))
    except mistral.TettoRaggiunto:
        voce.update({"tipo": "errore", "testo": "Mistral ha raggiunto il tetto di spesa o il limite di richieste. "
                                                "Riprova tra qualche minuto o alza il tetto dal pannello Mistral."})
    except Exception as e:
        log.error("Istruzione dalla pagina non riuscita: %s", type(e).__name__)
        voce.update({"tipo": "errore", "testo": "Non sono riuscito a eseguire l'istruzione. Riprova o riformulala."})
    finally:
        if imap:
            _chiudi(imap)
    voce["ora"] = time.strftime("%d/%m %H:%M")
    _salva_chat(voce)
    lavori[id_lavoro] = {"stato": "fatto", "fine": time.time()}


def _esegui_parere(id_lavoro, domanda, uid_mail):
    voce = {"id": uuid.uuid4().hex, "chi": "braccio"}
    try:
        contesto = ""
        if uid_mail:
            imap = _imap()
            try:
                imap.select("INBOX", readonly=True)
                originale = posta.leggi(imap, uid_mail)
                if originale is not None:
                    contesto, _ = posta.testo_da_messaggio(originale)
            finally:
                _chiudi(imap)
        voce.update(esperto.chiedi(domanda, contesto))
        log.info("Domanda all'esperto eseguita (revisore: %s).", voce.get("revisore"))
    except mistral.TettoRaggiunto:
        voce.update({"tipo": "errore", "testo": "Mistral ha raggiunto il tetto di spesa o il limite di richieste."})
    except Exception as e:
        log.error("Secondo parere non riuscito: %s", type(e).__name__)
        voce.update({"tipo": "errore", "testo": "Non sono riuscito a preparare il secondo parere. Riprova."})
    voce["ora"] = time.strftime("%d/%m %H:%M")
    _salva_chat(voce)
    lavori[id_lavoro] = {"stato": "fatto", "fine": time.time()}


def _registra_documento(nome, dati):
    id_doc = secrets.token_urlsafe(16)
    documenti[id_doc] = (nome, dati, time.time() + DURATA_FILE)
    return {"id": id_doc, "nome": nome, "kb": max(1, len(dati) // 1024)}


@app.get("/api/file/{id_doc}")
def scarica(id_doc: str):
    voce = documenti.get(id_doc)
    if not voce:
        raise HTTPException(404, "Documento scaduto: rifai la ricerca.")
    nome, dati, _ = voce
    tipo = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    nome_sicuro = re.sub(r'[^\w .()-]', "_", nome)
    return Response(dati, media_type=tipo, headers={"Content-Disposition": f'inline; filename="{nome_sicuro}"'})


# --- Bozze ------------------------------------------------------------------------------
def _trova_bozza(imap, mid):
    cartella = posta.trova_cartella_bozze(imap)
    imap.select(cartella)
    typ, r = imap.uid("SEARCH", None, "HEADER", "Message-ID", mid)
    uids = (r[0] or b"").split()
    if not uids:
        raise HTTPException(404, "Bozza non trovata: forse l'hai già inviata o cancellata dalla posta.")
    return cartella, uids[-1].decode()


def _mid(valore):
    mid = base64.urlsafe_b64decode(valore + "=" * (-len(valore) % 4)).decode()
    if not re.fullmatch(r"<[^<>\s\"]{3,250}>", mid):
        raise HTTPException(400, "Bozza non valida.")
    return mid


@app.get("/api/bozze")
def elenco_bozze():
    imap = _imap()
    try:
        imap.select(posta.trova_cartella_bozze(imap), readonly=True)
        typ, r = imap.uid("SEARCH", None, "ALL")
        uids = (r[0] or b"").split()[-60:]
        if not uids:
            return []
        typ, parti = imap.uid("FETCH", b",".join(uids).decode(),
                              "(BODY.PEEK[HEADER.FIELDS (TO SUBJECT DATE MESSAGE-ID X-BRACCIO-DESTRO)])")
        elenco = []
        for p in parti:
            if not isinstance(p, tuple):
                continue
            h = email.message_from_bytes(p[1], policy=email.policy.default)
            if not h.get("X-Braccio-Destro") or not h.get("Message-ID"):
                continue
            elenco.append({"id": base64.urlsafe_b64encode(str(h["Message-ID"]).encode()).decode().rstrip("="),
                           "a": str(h.get("To", "")), "oggetto": str(h.get("Subject", "")),
                           "data": _data_breve(h.get("Date"))})
        return list(reversed(elenco))
    finally:
        _chiudi(imap)


@app.get("/api/bozze/{id_bozza}")
def apri_bozza(id_bozza: str):
    mid = _mid(id_bozza)
    imap = _imap()
    try:
        cartella, uid = _trova_bozza(imap, mid)
        msg = posta.leggi(imap, uid)
        testo = next((p.get_content() for p in msg.walk()
                      if p.get_content_type() == "text/plain" and not p.get_filename()), "")
        return {"a": [a for _, a in email.utils.getaddresses(msg.get_all("To", []))],
                "cc": [a for _, a in email.utils.getaddresses(msg.get_all("Cc", []))],
                "oggetto": str(msg.get("Subject", "")), "testo": testo.strip(),
                "allegati": [p.get_filename() for p in msg.iter_attachments()]}
    finally:
        _chiudi(imap)


def _indirizzi(lista):
    puliti = []
    for x in lista or []:
        a = email.utils.parseaddr(str(x))[1].strip()
        if not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+", a):
            raise HTTPException(400, f"Indirizzo non valido: {x}")
        puliti.append(a)
    return puliti


@app.post("/api/bozze/{id_bozza}/invia")
async def invia_bozza(id_bozza: str, request: Request):
    """Unico punto da cui parte una mail: solo dopo il tocco di Roberto su "Approva e invia"."""
    corpo = await request.json()
    mid = _mid(id_bozza)
    a, cc = _indirizzi(corpo.get("a")), _indirizzi(corpo.get("cc"))
    if not a:
        raise HTTPException(400, "Manca il destinatario.")
    imap = _imap()
    try:
        cartella, uid = _trova_bozza(imap, mid)
        msg = posta.leggi(imap, uid)
        for campo in ("To", "Cc", "Subject", "Date", "X-Braccio-Destro", "X-Braccio-Destro-Categoria",
                      "X-Braccio-Destro-Priorita"):
            del msg[campo]
        msg["To"] = ", ".join(a)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = (corpo.get("oggetto") or "").strip()[:200]
        msg["Date"] = email.utils.formatdate(localtime=True)
        testo = (corpo.get("testo") or "").strip()
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain" and not parte.get_filename():
                parte.set_content(testo + "\n")
                break
        else:
            raise HTTPException(400, "Bozza senza testo.")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=60) as s:
            s.login(posta.IMAP_USER, posta.IMAP_PASS)
            s.send_message(msg, from_addr=posta.IMAP_USER, to_addrs=a + cc)
        inviata = pratiche._cartella_inviata(imap)
        if inviata:
            imap.append(inviata, "(\\Seen)", imaplib.Time2Internaldate(time.time()),
                        msg.as_bytes(policy=email.policy.SMTP))
        imap.select(cartella)
        imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        imap.expunge()
    finally:
        _chiudi(imap)
    _segna_bozza(mid, "inviata")
    log.info("Mail inviata su approvazione di Roberto.")
    return {"ok": True}


@app.post("/api/bozze/{id_bozza}/scarta")
def scarta_bozza(id_bozza: str):
    mid = _mid(id_bozza)
    imap = _imap()
    try:
        cartella, uid = _trova_bozza(imap, mid)
        imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        imap.expunge()
    finally:
        _chiudi(imap)
    _segna_bozza(mid, "scartata")
    return {"ok": True}


def _segna_bozza(mid, stato_bozza):
    with blocco:
        chat = _leggi_json(FILE_CHAT, [])
        for v in chat:
            if v.get("mid") == mid:
                v["stato_bozza"] = stato_bozza
        _scrivi_json(FILE_CHAT, chat)


@app.get("/api/dispositivi")
def dispositivi():
    return [{"nome": c["nome"], "creata": c["creata"]} for c in _leggi_json(FILE_CREDENZIALI, [])]


# --- Collegamenti: browser sul server per i login (Navisio, intermediari.ai) ----------------
PIATTAFORME = {
    "navisio": {"nome": "Navisio", "url": "https://www.navisio.it"},
    "intermediari": {"nome": "intermediari.ai", "url": "https://intermediari.ai"},
}
CARTELLA_BROWSER = os.environ.get("BROWSER_DATI", "/var/lib/braccio-destro/browser")
FILE_COLLEGAMENTI = os.path.join(DATI, "collegamenti.json")
browser_aperto = {"processo": None, "piattaforma": None, "scade": 0}


def _chromium():
    import glob
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/braccio-destro/ms-playwright")
    trovati = sorted(glob.glob(f"{base}/chromium-*/chrome-linux*/chrome"))
    if not trovati:
        raise HTTPException(503, "Il browser del server non è ancora installato: riprova tra qualche minuto.")
    return trovati[-1]


def _profilo(chiave):
    """Profilo separato per piattaforma; il salvataggio delle password è disattivato (restano solo i cookie)."""
    cartella = os.path.join(CARTELLA_BROWSER, chiave)
    os.makedirs(os.path.join(cartella, "Default"), mode=0o700, exist_ok=True)
    pref = os.path.join(cartella, "Default", "Preferences")
    if not os.path.exists(pref):
        with open(pref, "w") as f:
            json.dump({"credentials_enable_service": False, "profile": {"password_manager_enabled": False},
                       "autofill": {"profile_enabled": False, "credit_card_enabled": False}}, f)
    return cartella


def chiudi_browser():
    p = browser_aperto["processo"]
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()
    browser_aperto.update({"processo": None, "piattaforma": None, "scade": 0})


def _sorveglia_browser():
    while True:
        time.sleep(30)
        if browser_aperto["processo"] and browser_aperto["scade"] < time.time():
            chiudi_browser()


threading.Thread(target=_sorveglia_browser, daemon=True).start()


@app.get("/api/verifica")
def verifica():
    """Usata da Caddy per lasciar vedere lo schermo del server solo a chi è entrato con Face ID."""
    return {"ok": True}


@app.get("/api/collegamenti")
def collegamenti():
    stato_c = _leggi_json(FILE_COLLEGAMENTI, {})
    aperto = browser_aperto["piattaforma"] if browser_aperto["processo"] and browser_aperto["processo"].poll() is None else None
    return [{"id": k, "nome": v["nome"], "collegato_il": stato_c.get(k), "aperto": aperto == k}
            for k, v in PIATTAFORME.items()]


@app.post("/api/collegamenti/{chiave}/apri")
def apri_collegamento(chiave: str):
    if chiave not in PIATTAFORME:
        raise HTTPException(404, "Piattaforma sconosciuta.")
    import subprocess
    chiudi_browser()
    with blocco:
        browser_aperto["processo"] = subprocess.Popen(
            [_chromium(), f"--user-data-dir={_profilo(chiave)}", "--no-first-run", "--no-default-browser-check",
             "--no-sandbox", "--disable-sync", "--disable-features=PasswordManagerOnboarding,Translate",
             "--window-position=0,0", "--window-size=600,1100", "--test-type", "--lang=it-IT", PIATTAFORME[chiave]["url"]],
            env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        browser_aperto.update({"piattaforma": chiave, "scade": time.time() + 20 * 60})
    log.info("Browser aperto per il login: %s", chiave)
    return {"ok": True, "schermo": "/schermo/vnc.html?autoconnect=1&resize=scale&path=schermo/websockify"}


@app.get("/api/chiavi")
def stato_chiavi():
    return {"scaleway": bool(esperto.chiave_scaleway())}


@app.post("/api/chiavi/scaleway")
async def salva_chiave_scaleway(request: Request):
    """La chiave si inserisce dalla pagina (HTTPS, dopo Face ID): resta solo sul server e non viene mai mostrata."""
    chiave = ((await request.json()).get("chiave") or "").strip()
    if chiave and not re.fullmatch(r"[A-Za-z0-9_\-]{20,200}", chiave):
        raise HTTPException(400, "La chiave non sembra valida.")
    with blocco:
        chiavi = _leggi_json(esperto.FILE_CHIAVI, {})
        if chiave:
            chiavi["scaleway"] = chiave
        else:
            chiavi.pop("scaleway", None)
        chiavi.pop("grok", None)
        _scrivi_json(esperto.FILE_CHIAVI, chiavi)
    esperto._modello_scelto.clear()
    log.info("Chiave Scaleway %s.", "salvata" if chiave else "rimossa")
    return {"ok": True}


# --- Biblioteca -----------------------------------------------------------------------
stato_biblioteca = {"lavoro": None}


def _lavora_biblioteca(funzione, *argomenti):
    stato_biblioteca["lavoro"] = "in corso"
    try:
        stato_biblioteca["lavoro"] = funzione(*argomenti)
    except mistral.TettoRaggiunto:
        stato_biblioteca["lavoro"] = ["Mistral ha raggiunto il tetto di spesa: riprova più tardi."]
    except Exception as e:
        log.error("Biblioteca: errore %s", type(e).__name__)
        stato_biblioteca["lavoro"] = [f"Errore: {type(e).__name__}"]


def _carica_file(file):
    esiti = []
    for nome, dati in file:
        try:
            _, n = biblioteca.aggiungi_file(nome, dati)
            esiti.append(f"{nome}: {n} brani")
        except Exception as e:
            esiti.append(f"{nome}: NON caricato ({type(e).__name__})")
    return esiti


@app.get("/api/biblioteca")
def elenco_biblioteca():
    lavoro = stato_biblioteca["lavoro"]
    return {"fonti": biblioteca.elenco(), "in_corso": lavoro == "in corso",
            "esito": lavoro if isinstance(lavoro, list) else None}


@app.post("/api/biblioteca")
async def carica_biblioteca(file: list[UploadFile] = File(default=[])):
    if stato_biblioteca["lavoro"] == "in corso":
        raise HTTPException(409, "Sto già caricando: aspetta che finisca.")
    caricati = []
    for f in file:
        dati = await f.read()
        if len(dati) > MAX_UPLOAD:
            raise HTTPException(400, f"{f.filename}: file troppo grande (massimo 25 MB).")
        if not re.search(r"(?i)\.(pdf|docx|txt|md|html?)$", f.filename or ""):
            raise HTTPException(400, f"{f.filename}: formato non supportato (PDF, Word, testo).")
        caricati.append((os.path.basename(f.filename), dati))
    if not caricati:
        raise HTTPException(400, "Scegli almeno un file.")
    threading.Thread(target=_lavora_biblioteca, args=(_carica_file, caricati), daemon=True).start()
    return {"ok": True}


@app.post("/api/biblioteca/ufficiali")
def aggiorna_ufficiali():
    if stato_biblioteca["lavoro"] == "in corso":
        raise HTTPException(409, "Sto già caricando: aspetta che finisca.")
    threading.Thread(target=_lavora_biblioteca, args=(biblioteca.carica_ufficiali, False), daemon=True).start()
    return {"ok": True}


@app.post("/api/biblioteca/{id_fonte}/rimuovi")
def rimuovi_fonte(id_fonte: str):
    if not id_fonte.startswith("MIO_"):
        raise HTTPException(400, "Si possono togliere solo i materiali caricati da te.")
    biblioteca.rimuovi(id_fonte)
    return {"ok": True}


def _primo_avvio_biblioteca():
    time.sleep(20)
    if not any(f["tipo"] == "ufficiale" for f in biblioteca.elenco()):
        _lavora_biblioteca(biblioteca.carica_ufficiali, True)


threading.Thread(target=_primo_avvio_biblioteca, daemon=True).start()


@app.post("/api/collegamenti/{chiave}/fatto")
def fine_collegamento(chiave: str):
    if chiave not in PIATTAFORME:
        raise HTTPException(404, "Piattaforma sconosciuta.")
    chiudi_browser()
    with blocco:
        stato_c = _leggi_json(FILE_COLLEGAMENTI, {})
        stato_c[chiave] = time.strftime("%d/%m/%Y %H:%M")
        _scrivi_json(FILE_COLLEGAMENTI, stato_c)
    return {"ok": True}


# --- Pagina -----------------------------------------------------------------------------
app.mount("/", StaticFiles(directory=os.path.join(QUI, "static"), html=True), name="pagina")
