"""
Braccio destro - Biblioteca professionale (sul server)

Testi ufficiali (CAP, Regolamenti IVASS, Codice civile sull'assicurazione, prevenzione incendi, guide di risk
management) + i materiali caricati da Roberto (condizioni di polizza, corsi, appunti).
Ogni testo viene diviso in brani con fonte e pagina; i brani vengono indicizzati con mistral-embed (UE).
Le risposte del braccio destro citano i brani usati, così si può verificare.
I testi pubblici non contengono dati di clienti; i materiali di Roberto restano sul server.
"""

import html
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.request

import mistral

log = logging.getLogger("biblioteca")
DB = os.path.join(os.environ.get("BIBLIOTECA_DATI", "/var/lib/braccio-destro/biblioteca"), "biblioteca.sqlite")
MODELLO_EMBED = os.environ.get("MISTRAL_EMBED_MODEL", "mistral-embed")
_blocco = threading.Lock()

FONTI_UFFICIALI = [
    {"id": "CAP", "titolo": "Codice delle assicurazioni private (D.Lgs. 209/2005)", "edizione": "consolidato IVASS",
     "url": "https://www.ivass.it/normativa/nazionale/primaria/Cap.pdf"},
    {"id": "IVASS40", "titolo": "Regolamento IVASS 40/2018 - Distribuzione assicurativa",
     "edizione": "consolidato IVASS (Provv. 169/2026)",
     "url": "https://www.ivass.it/normativa/nazionale/secondaria-ivass/regolamenti/2018/n40/"
            "Regolamento_IVASS_40_del_2_agosto_2018_consolidato.pdf"},
    {"id": "IVASS41", "titolo": "Regolamento IVASS 41/2018 - Informativa, pubblicità e realizzazione dei prodotti",
     "edizione": "testo originale 2018 (verificare modifiche successive, es. Provv. 163/2025)",
     "url": "https://www.dirittobancario.it/wp-content/uploads/sites/default/files/allegati/"
            "regolamento_ivass_2_agosto_2018_n._41.pdf"},
    {"id": "IVASS163", "titolo": "Provvedimento IVASS 163/2025 - Arbitro Assicurativo (modifiche Reg. 40 e 41)",
     "edizione": "25 novembre 2025",
     "url": "https://www.dirittobancario.it/wp-content/uploads/2025/11/Provvedimento-IVASS-25-novembre-2025-n.-163.pdf"},
    {"id": "ISVAP24", "titolo": "Regolamento ISVAP 24/2008 - Gestione dei reclami (anche intermediari)",
     "edizione": "consolidato con le modifiche fino al Provv. IVASS 76/2018",
     "url": "https://www.bancadicaraglio.it/media/12274/regolamento-ivass.pdf"},
    {"id": "CC_ASSICURAZIONE", "titolo": "Codice civile, artt. 1882-1932 - Dell'assicurazione", "edizione": "testo vigente",
     "url": "https://www.filodiritto.com/codici/codice-civile/capo-xx-dellassicurazione", "html": True},
    {"id": "VVF_CODICE", "titolo": "Codice di prevenzione incendi (DM 3 agosto 2015, Allegato I)",
     "edizione": "VVF, testo coordinato 2023",
     "url": "https://www.vigilfuoco.it/sites/default/files/2023-05/COORD_DM_03_08_2015_Codice_Prevenzione_Incendi.pdf"},
    {"id": "VVF_NOTE", "titolo": "Note e chiarimenti al DM 3 agosto 2015", "edizione": "VVF, agosto 2026",
     "url": "https://www.vigilfuoco.it/sites/default/files/2026-09/NOTE_E_CHIARIMENTI_AL_DM_03_08_2015%20agosto%202026.pdf"},
    {"id": "NIST_RISK", "titolo": "NIST SP 800-30 Rev.1 - Guide for Conducting Risk Assessments", "edizione": "2012",
     "url": "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-30r1.pdf"},
    {"id": "NIST_BIA", "titolo": "NIST SP 800-34 Rev.1 - Contingency Planning Guide", "edizione": "2010",
     "url": "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication800-34r1.pdf"},
    {"id": "HSE_INDAGINI", "titolo": "HSE HSG245 - Investigating accidents and incidents", "edizione": "2004/2014",
     "url": "https://www.hse.gov.uk/pubns/hsg245.pdf"},
]


# --- Archivio ---------------------------------------------------------------------
def _db():
    os.makedirs(os.path.dirname(DB), mode=0o700, exist_ok=True)
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""CREATE TABLE IF NOT EXISTS fonti (id TEXT PRIMARY KEY, titolo TEXT, edizione TEXT, origine TEXT,
                   tipo TEXT, aggiunta TEXT, brani INTEGER)""")
    con.execute("""CREATE TABLE IF NOT EXISTS brani (fonte TEXT, pagina INTEGER, articolo TEXT, testo TEXT,
                   vettore BLOB)""")
    con.execute("CREATE INDEX IF NOT EXISTS brani_fonte ON brani(fonte)")
    return con


def elenco():
    with _db() as con:
        return [dict(zip(("id", "titolo", "edizione", "origine", "tipo", "aggiunta", "brani"), r))
                for r in con.execute("SELECT id, titolo, edizione, origine, tipo, aggiunta, brani FROM fonti ORDER BY tipo, titolo")]


def rimuovi(id_fonte):
    with _blocco, _db() as con:
        con.execute("DELETE FROM brani WHERE fonte=?", (id_fonte,))
        con.execute("DELETE FROM fonti WHERE id=?", (id_fonte,))


# --- Estrazione del testo -------------------------------------------------------------
def _scarica(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (braccio-destro biblioteca)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _pagine_pdf(dati):
    from pypdf import PdfReader
    pagine = []
    for i, p in enumerate(PdfReader(io.BytesIO(dati)).pages, start=1):
        try:
            pagine.append((i, p.extract_text() or ""))
        except Exception:
            pagine.append((i, ""))
    if sum(len(t.strip()) for _, t in pagine) < 200 * max(1, len(pagine)) // 4:
        # PDF scansionato: OCR Mistral (UE, zero data retention)
        pagine = list(enumerate(mistral.ocr_pagine(dati), start=1))
    return pagine


def _testo_html(dati):
    t = dati.decode("utf-8", errors="replace")
    t = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>|</div>", "\n", t)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t]+", " ", t)
    return [(1, re.sub(r"\n\s*\n+", "\n\n", t).strip())]


def _pagine_file(nome, dati):
    n = nome.lower()
    if n.endswith(".pdf"):
        return _pagine_pdf(dati)
    if n.endswith(".docx"):
        import zipfile
        with zipfile.ZipFile(io.BytesIO(dati)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return [(1, html.unescape(re.sub(r"<[^>]+>", "", xml)))]
    if n.endswith((".htm", ".html")):
        return _testo_html(dati)
    return [(1, dati.decode("utf-8", errors="replace"))]


ARTICOLO = re.compile(r"(?im)^\s*(?:art\.?|articolo)\s*(\d+[\w-]*)")


def _brani(pagine, lunghezza=1400, sovrapposizione=200):
    """Brani di ~1400 caratteri con pagina e articolo di riferimento."""
    brani, articolo = [], ""
    for numero, testo in pagine:
        testo = re.sub(r"[ \t]+", " ", testo or "").strip()
        inizio = 0
        while inizio < len(testo):
            pezzo = testo[inizio:inizio + lunghezza]
            trovati = ARTICOLO.findall(pezzo)
            etichetta = trovati[0] if trovati else articolo
            if trovati:
                articolo = trovati[-1]
            if len(pezzo.strip()) > 80:
                brani.append((numero, f"art. {etichetta}" if etichetta else "", pezzo))
            inizio += lunghezza - sovrapposizione
    return brani


# --- Indicizzazione -------------------------------------------------------------------
def _vettori(testi):
    import numpy as np
    risultati = []
    for i in range(0, len(testi), 32):
        r = mistral._post("/embeddings", {"model": MODELLO_EMBED, "input": testi[i:i + 32]}, timeout=180)
        risultati += [np.asarray(d["embedding"], dtype="float32") for d in r["data"]]
    return risultati


def aggiungi(id_fonte, titolo, edizione, origine, tipo, pagine):
    brani = _brani(pagine)
    if not brani:
        raise ValueError("Nessun testo leggibile nel documento.")
    vettori = _vettori([f"{titolo}\n{a}\n{t}" for _, a, t in brani])
    with _blocco, _db() as con:
        con.execute("DELETE FROM brani WHERE fonte=?", (id_fonte,))
        con.executemany("INSERT INTO brani VALUES (?,?,?,?,?)",
                        [(id_fonte, p, a, t, v.tobytes()) for (p, a, t), v in zip(brani, vettori)])
        con.execute("INSERT OR REPLACE INTO fonti VALUES (?,?,?,?,?,?,?)",
                    (id_fonte, titolo, edizione, origine, tipo, time.strftime("%d/%m/%Y"), len(brani)))
    log.info("Biblioteca: %s indicizzato (%d brani).", id_fonte, len(brani))
    return len(brani)


def aggiungi_file(nome, dati):
    """Materiale caricato da Roberto (condizioni di polizza, corsi, appunti)."""
    id_fonte = "MIO_" + re.sub(r"[^\w]+", "_", os.path.splitext(nome)[0])[:60]
    return id_fonte, aggiungi(id_fonte, nome, "caricato da Roberto", "caricato", "tuo", _pagine_file(nome, dati))


def carica_ufficiali(solo_mancanti=True):
    """Scarica e indicizza i testi ufficiali. Ritorna l'elenco degli esiti."""
    presenti = {f["id"] for f in elenco()}
    esiti = []
    for f in FONTI_UFFICIALI:
        if solo_mancanti and f["id"] in presenti:
            continue
        try:
            dati = _scarica(f["url"])
            pagine = _testo_html(dati) if f.get("html") else _pagine_pdf(dati)
            n = aggiungi(f["id"], f["titolo"], f["edizione"], f["url"], "ufficiale", pagine)
            esiti.append(f"{f['titolo']}: {n} brani")
        except Exception as e:
            log.error("Biblioteca: %s non caricato (%s).", f["id"], type(e).__name__)
            esiti.append(f"{f['titolo']}: NON caricato ({type(e).__name__})")
    return esiti


# --- Ricerca --------------------------------------------------------------------------
def cerca(domanda, k=8):
    import numpy as np
    with _db() as con:
        righe = con.execute("SELECT b.fonte, b.pagina, b.articolo, b.testo, b.vettore, f.titolo, f.edizione "
                            "FROM brani b JOIN fonti f ON f.id=b.fonte").fetchall()
    if not righe:
        return []
    q = _vettori([domanda[:6000]])[0]
    matrice = np.vstack([np.frombuffer(r[4], dtype="float32") for r in righe])
    punteggi = matrice @ q / (np.linalg.norm(matrice, axis=1) * np.linalg.norm(q) + 1e-9)
    # Se la domanda cita un articolo preciso, i brani di quell'articolo salgono in cima.
    citati = {a.lower() for a in re.findall(r"(?i)\bart(?:icolo|\.)?\s*(\d+[\w-]*)", domanda)}
    for i, r in enumerate(righe):
        if r[2] and r[2].lower().replace("art. ", "") in citati:
            punteggi[i] += 0.15
    migliori = np.argsort(-punteggi)[:k]
    return [{"fonte": righe[i][5], "edizione": righe[i][6], "pagina": righe[i][1], "articolo": righe[i][2],
             "testo": righe[i][3], "punteggio": float(punteggi[i])} for i in migliori]
