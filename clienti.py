"""
Braccio destro - Agente Clienti

Una scheda per ogni cliente: anagrafica (letta dalla visura), polizze, preventivi, questionari, visure,
contatti e note. I documenti si aggiungono dalle ricerche in posta e restano SOLO sul server
(cartella /var/lib/braccio-destro/clienti). Gli altri agenti (scadenze, preventivi, sinistri) lavorano su queste schede.
"""

import datetime
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time

import mistral
import questionari
import ricerca

log = logging.getLogger("clienti")
CARTELLA = os.environ.get("CLIENTI_DATI", "/var/lib/braccio-destro/clienti")
DB = os.path.join(CARTELLA, "clienti.sqlite")
FILE = os.path.join(CARTELLA, "file")
_blocco = threading.Lock()
CAMPI = ("nome", "piva", "cf", "sede", "legale", "ateco", "attivita", "pec", "email", "telefono", "note")

SISTEMA_VISURA = """Dal testo di una visura camerale italiana estrai i dati dell'impresa.
Rispondi SOLO in JSON: {"denominazione": "", "piva": "", "cf": "", "sede": "indirizzo completo della sede legale",
"legale": "nome e cognome del legale rappresentante", "ateco": "codice ATECO principale", "attivita": "attività
prevalente in max 12 parole", "pec": ""}. Lascia "" ciò che non c'è: non inventare."""


def _db():
    os.makedirs(FILE, mode=0o700, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS clienti (id INTEGER PRIMARY KEY, nome TEXT UNIQUE, piva TEXT, cf TEXT,
                   sede TEXT, legale TEXT, ateco TEXT, attivita TEXT, pec TEXT, email TEXT, telefono TEXT, note TEXT,
                   nomi_ricerca TEXT, creato TEXT, aggiornato TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS documenti (id INTEGER PRIMARY KEY, cliente_id INTEGER, sha TEXT, nome TEXT,
                   tipo TEXT, compagnia TEXT, numero TEXT, scadenza TEXT, descrizione TEXT, data TEXT, verso TEXT,
                   contatto TEXT, oggetto TEXT, aggiunto TEXT, UNIQUE(cliente_id, sha))""")
    return con


def _oggi():
    return time.strftime("%d/%m/%Y")


def _data(testo):
    try:
        return datetime.datetime.strptime((testo or "").strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


# --- Clienti ----------------------------------------------------------------------------
def elenco():
    with _db() as con:
        righe = con.execute("SELECT * FROM clienti ORDER BY nome COLLATE NOCASE").fetchall()
        out = []
        for c in righe:
            docs = con.execute("SELECT tipo, scadenza FROM documenti WHERE cliente_id=?", (c["id"],)).fetchall()
            future = sorted(d for d in (_data(x["scadenza"]) for x in docs if x["tipo"] == "polizza")
                            if d and d >= datetime.date.today())
            out.append({"id": c["id"], "nome": c["nome"], "piva": c["piva"] or "", "attivita": c["attivita"] or "",
                        "documenti": len(docs), "polizze": sum(1 for x in docs if x["tipo"] == "polizza"),
                        "prossima_scadenza": future[0].strftime("%d/%m/%Y") if future else ""})
        return out


def crea(nome, nomi_ricerca=None):
    nome = (nome or "").strip()
    if len(nome) < 2:
        raise ValueError("Nome cliente mancante.")
    with _blocco, _db() as con:
        esiste = con.execute("SELECT id FROM clienti WHERE nome=? COLLATE NOCASE", (nome,)).fetchone()
        if esiste:
            return esiste["id"]
        cur = con.execute("INSERT INTO clienti (nome, nomi_ricerca, creato, aggiornato) VALUES (?,?,?,?)",
                          (nome, json.dumps(nomi_ricerca or ricerca.varianti(nome)), _oggi(), _oggi()))
        log.info("Cliente creato (id %s).", cur.lastrowid)
        return cur.lastrowid


def modifica(id_cliente, campi):
    valori = {k: (str(v).strip()[:2000] if v is not None else "") for k, v in campi.items() if k in CAMPI}
    if not valori:
        return
    with _blocco, _db() as con:
        con.execute(f"UPDATE clienti SET {', '.join(k + '=?' for k in valori)}, aggiornato=? WHERE id=?",
                    (*valori.values(), _oggi(), id_cliente))


def elimina(id_cliente):
    """Cancella scheda e documenti (es. richiesta di cancellazione GDPR)."""
    with _blocco, _db() as con:
        shas = [r["sha"] for r in con.execute("SELECT sha FROM documenti WHERE cliente_id=?", (id_cliente,))]
        con.execute("DELETE FROM documenti WHERE cliente_id=?", (id_cliente,))
        con.execute("DELETE FROM clienti WHERE id=?", (id_cliente,))
        for sha in shas:
            if not con.execute("SELECT 1 FROM documenti WHERE sha=?", (sha,)).fetchone():
                try:
                    os.remove(os.path.join(FILE, sha))
                except FileNotFoundError:
                    pass
    log.info("Cliente %s eliminato con i suoi documenti.", id_cliente)


# --- Documenti --------------------------------------------------------------------------
def aggiungi_documenti(id_cliente, documenti):
    """documenti: dizionari come quelli di ricerca.trova (con "dati"). Ritorna quanti sono nuovi."""
    nuovi = 0
    with _blocco, _db() as con:
        for d in documenti:
            sha = hashlib.sha256(d["dati"]).hexdigest()
            percorso = os.path.join(FILE, sha)
            if not os.path.exists(percorso):
                with open(os.open(percorso, os.O_WRONLY | os.O_CREAT, 0o600), "wb") as f:
                    f.write(d["dati"])
            cur = con.execute("""INSERT OR IGNORE INTO documenti (cliente_id, sha, nome, tipo, compagnia, numero, scadenza,
                                 descrizione, data, verso, contatto, oggetto, aggiunto) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (id_cliente, sha, d.get("nome", ""), d.get("tipo", "altro"), d.get("compagnia", ""),
                               d.get("numero", ""), d.get("scadenza", ""), d.get("descrizione", ""), d.get("data", ""),
                               d.get("verso", ""), d.get("contatto", ""), d.get("oggetto", ""), _oggi()))
            nuovi += cur.rowcount
        con.execute("UPDATE clienti SET aggiornato=? WHERE id=?", (_oggi(), id_cliente))
    return nuovi


def togli_documento(id_cliente, id_doc):
    with _blocco, _db() as con:
        con.execute("DELETE FROM documenti WHERE id=? AND cliente_id=?", (id_doc, id_cliente))


def file_documento(id_cliente, id_doc):
    with _db() as con:
        r = con.execute("SELECT nome, sha FROM documenti WHERE id=? AND cliente_id=?", (id_doc, id_cliente)).fetchone()
    if not r:
        return None
    with open(os.path.join(FILE, r["sha"]), "rb") as f:
        return r["nome"], f.read()


def scheda(id_cliente):
    with _db() as con:
        c = con.execute("SELECT * FROM clienti WHERE id=?", (id_cliente,)).fetchone()
        if not c:
            return None
        docs = [dict(r) for r in con.execute("SELECT * FROM documenti WHERE cliente_id=?", (id_cliente,))]
    oggi = datetime.date.today()
    for d in docs:
        sc = _data(d["scadenza"])
        d["giorni"] = (sc - oggi).days if sc else None
        d["ordine"] = (_data(d["data"]) or datetime.date.min).toordinal()
    docs.sort(key=lambda d: (ricerca.ORDINE_TIPI.index(d["tipo"]) if d["tipo"] in ricerca.ORDINE_TIPI else 99, -d["ordine"]))
    polizze = [d for d in docs if d["tipo"] == "polizza"]
    in_corso = [d for d in polizze if d["giorni"] is not None and d["giorni"] >= 0]
    scadute = [d for d in polizze if d["giorni"] is not None and d["giorni"] < 0]
    return {**{k: c[k] or "" for k in CAMPI}, "id": c["id"], "creato": c["creato"], "aggiornato": c["aggiornato"],
            "nomi_ricerca": json.loads(c["nomi_ricerca"] or "[]"),
            "polizze_in_corso": sorted(in_corso, key=lambda d: d["giorni"]),
            "polizze_senza_scadenza": [d for d in polizze if d["giorni"] is None],
            "polizze_scadute": scadute, "documenti": docs, "titoli": ricerca.TITOLI_TIPI}


# --- Anagrafica dalla visura e aggiornamento dalla posta -----------------------------------
def anagrafica_da_visura(id_cliente):
    with _db() as con:
        visure = con.execute("SELECT id, sha, nome FROM documenti WHERE cliente_id=? AND tipo='visura'",
                             (id_cliente,)).fetchall()
        c = con.execute("SELECT * FROM clienti WHERE id=?", (id_cliente,)).fetchone()
    for v in visure:
        with open(os.path.join(FILE, v["sha"]), "rb") as f:
            dati = f.read()
        if not v["nome"].lower().endswith(".pdf"):
            continue
        testo = questionari.testo_pdf(dati, 12000)
        if not questionari.testo_leggibile(testo):
            testo = mistral.ocr_pdf(dati)[:12000]
        estratti = mistral.chat_json(SISTEMA_VISURA, testo, temperatura=0.0)
        nome_visura = (estratti.get("denominazione") or "").lower()
        if nome_visura and not any(re.sub(r"\W", "", n.lower()) in re.sub(r"\W", "", nome_visura)
                                   for n in [c["nome"]] + json.loads(c["nomi_ricerca"] or "[]")):
            continue   # visura di un'altra impresa
        modifica(id_cliente, {k: estratti.get(k) for k in ("piva", "cf", "sede", "legale", "ateco", "attivita", "pec")
                              if estratti.get(k) and not c[k]})
        return True
    return False


def aggiorna_dalla_posta(imap, id_cliente):
    with _db() as con:
        c = con.execute("SELECT nome, nomi_ricerca, piva FROM clienti WHERE id=?", (id_cliente,)).fetchone()
    varianti = json.loads(c["nomi_ricerca"] or "[]") + ([c["piva"]] if c["piva"] else [])
    esito = ricerca.trova(imap, {"parola_chiave": c["nome"], "varianti": varianti}, "INBOX")
    nuovi = aggiungi_documenti(id_cliente, esito["documenti"])
    if nuovi:
        anagrafica_da_visura(id_cliente)
    return nuovi
