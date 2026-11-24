"""
Braccio destro - Questionari

Compila un modulo di compagnia (PDF "piatto", senza campi) scrivendo le risposte
sopra le righe, usando la mappa del modulo in moduli/<nome>.json.
"""

import io
import json
import os
import textwrap

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

CARTELLA_MODULI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "moduli")
FONT, CORPO = "Helvetica", 8


def carica_modulo(nome):
    with open(os.path.join(CARTELLA_MODULI, nome + ".json"), encoding="utf-8") as f:
        mappa = json.load(f)
    mappa["percorso_pdf"] = os.path.join(CARTELLA_MODULI, mappa["file"])
    return mappa


def _spezza(testo, larghezza):
    """Divide il testo in righe che stanno nella larghezza data (in punti)."""
    righe = []
    for paragrafo in str(testo).splitlines() or [""]:
        parole, riga = paragrafo.split(), ""
        for p in parole:
            prova = (riga + " " + p).strip()
            if stringWidth(prova, FONT, CORPO) <= larghezza:
                riga = prova
            else:
                if riga:
                    righe.append(riga)
                while stringWidth(p, FONT, CORPO) > larghezza:   # parola troppo lunga
                    n = max(1, int(len(p) * larghezza / stringWidth(p, FONT, CORPO)))
                    righe.append(p[:n]); p = p[n:]
                riga = p
        righe.append(riga)
    return [r for r in righe if r] or []


def compila_pdf(mappa, valori, percorso_uscita=None):
    """
    valori: {id_campo: testo | "si" | "no" | True/False}
    Ritorna (pdf_in_byte, campi_troncati): i campi il cui testo non è entrato tutto nello spazio.
    """
    lettore = PdfReader(mappa["percorso_pdf"])
    altezze = {i + 1: float(p.mediabox.height) for i, p in enumerate(lettore.pages)}
    per_pagina = {}
    troncati = []

    def scrivi(pagina, x, top, testo, corpo=CORPO):
        per_pagina.setdefault(pagina, []).append((x, altezze[pagina] - top + 2, testo, corpo))

    for campo in mappa["campi"]:
        v = valori.get(campo["id"])
        if v in (None, "", [], False) and campo["tipo"] != "si_no":
            continue
        if campo["tipo"] == "testo":
            righe = campo["righe"]
            spazio = [r["x1"] - r["x"] - 4 for r in righe]
            testo_righe, resto = [], str(v)
            for i, larg in enumerate(spazio):
                pezzi = _spezza(resto, larg)
                if not pezzi:
                    break
                testo_righe.append(pezzi[0])
                resto = resto[resto.find(pezzi[0]) + len(pezzi[0]):].strip()
                if not resto:
                    break
            if resto:
                troncati.append(campo["id"])
                testo_righe[-1] = testo_righe[-1][:-3] + "..."
            for r, t in zip(righe, testo_righe):
                scrivi(r["pagina"], r["x"] + 2, r["top"], t)
        elif campo["tipo"] == "si_no":
            if v is None or v == "":
                continue
            scelta = "si" if str(v).strip().lower() in ("si", "sì", "true", "1", "yes") or v is True else "no"
            x, top = campo[scelta]
            scrivi(campo["pagina"], x + 1.6, top + 9.2, "X", 10)
        elif campo["tipo"] == "casella":
            if str(v).strip().lower() in ("si", "sì", "true", "1", "x") or v is True:
                x, top = campo["pos"]
                scrivi(campo["pagina"], x + 1.6, top + 9.2, "X", 10)

    scrittore = PdfWriter()
    for i, pagina in enumerate(lettore.pages, start=1):
        if i in per_pagina:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(float(pagina.mediabox.width), altezze[i]))
            for x, y, testo, corpo in per_pagina[i]:
                c.setFont(FONT, corpo)
                c.setFillColorRGB(0.05, 0.15, 0.45)
                c.drawString(x, y, testo)
            c.save()
            buf.seek(0)
            pagina.merge_page(PdfReader(buf).pages[0])
        scrittore.add_page(pagina)
    uscita = io.BytesIO()
    scrittore.write(uscita)
    if percorso_uscita:
        with open(percorso_uscita, "wb") as f:
            f.write(uscita.getvalue())
    return uscita.getvalue(), troncati


def testo_pdf(dati_pdf, max_caratteri=30000):
    """Testo di un PDF (vuoto se è una scansione: in quel caso serve l'OCR)."""
    lettore = PdfReader(io.BytesIO(dati_pdf))
    testo = "\n".join((p.extract_text() or "") for p in lettore.pages)
    return testo[:max_caratteri]


def testo_leggibile(testo):
    """Vero se il testo estratto è italiano leggibile e non caratteri senza senso."""
    if len(testo) < 200:
        return False
    parole = [w.lower() for w in testo.split()[:400]]
    comuni = {"di", "e", "il", "la", "del", "della", "per", "in", "che", "le", "dei", "delle", "a", "con"}
    return sum(w in comuni for w in parole) >= 15


def trova_modulo(compagnia, ramo):
    """Nome della mappa per compagnia e ramo (es. ITAS + CAR -> itas_car), o None."""
    for nome in os.listdir(CARTELLA_MODULI):
        if nome.endswith(".json") and nome != "compagnie.json":
            with open(os.path.join(CARTELLA_MODULI, nome), encoding="utf-8") as f:
                m = json.load(f)
            if m.get("compagnia", "").lower() == str(compagnia).lower() and m.get("ramo", "").lower() == str(ramo).lower():
                return nome[:-5]
    return None


def compagnie_note():
    """Domini email delle agenzie/compagnie note (es. un'agenzia ITAS)."""
    try:
        with open(os.path.join(CARTELLA_MODULI, "compagnie.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
