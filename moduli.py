"""
Braccio destro - Compilare moduli PDF "piatti" (senza campi), scrivendo sopra il modulo originale.

Molti moduli (ASL, Comuni, compagnie) sono PDF con righe "_____" e caselle "□" ma senza campi compilabili.
Qui si trovano gli spazi da riempire (righe e caselle) con la loro posizione e l'etichetta vicina, e poi si
scrive sopra al modulo di Roberto, che resta identico: si aggiunge solo il testo.
"""

import io
import re

CASELLE = "□☐▢❑"
SOTTOLINEATO = re.compile(r"_{3,}")


def _righe(pagina):
    """Parole della pagina raggruppate per riga (stessa altezza)."""
    parole = [w for w in pagina.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])
              if not SOTTOLINEATO.fullmatch(w["text"])]
    righe = []
    for p in sorted(parole, key=lambda w: (round(w["top"]), w["x0"])):
        if righe and abs(righe[-1][0]["top"] - p["top"]) < 3:
            righe[-1].append(p)
        else:
            righe.append([p])
    return [sorted(r, key=lambda w: w["x0"]) for r in righe]


def spazi(dati_pdf, max_spazi=400):
    """Elenco degli spazi da compilare: [{id, pagina, tipo (riga|casella), etichetta, x0, x1, top, bottom}]."""
    import pdfplumber
    out = []
    with pdfplumber.open(io.BytesIO(dati_pdf)) as pdf:
        for n, pagina in enumerate(pdf.pages, start=1):
            caratteri = pagina.chars
            # righe "____": sequenze di trattini bassi consecutivi sulla stessa riga
            gruppo = []
            for c in caratteri + [None]:
                if c is not None and c["text"] == "_" and (not gruppo or (abs(c["top"] - gruppo[-1]["top"]) < 2
                                                                         and c["x0"] - gruppo[-1]["x1"] < 2)):
                    gruppo.append(c)
                    continue
                if len(gruppo) >= 3:
                    out.append({"pagina": n, "tipo": "riga", "x0": gruppo[0]["x0"], "x1": gruppo[-1]["x1"],
                                "top": gruppo[0]["top"], "bottom": gruppo[0]["bottom"], "size": gruppo[0]["size"]})
                gruppo = [c] if c is not None and c["text"] == "_" else []
            caselle = [{"x0": c["x0"], "x1": c["x1"], "top": c["top"], "bottom": c["bottom"], "size": c["size"]}
                       for c in caratteri if c["text"] in CASELLE]
            # caselle disegnate come piccoli quadrati (molti moduli Word/PDF le fanno così)
            for r in pagina.rects:
                if 4 <= r["width"] <= 13 and 4 <= r["height"] <= 13 and abs(r["width"] - r["height"]) <= 3.5:
                    caselle.append({"x0": r["x0"], "x1": r["x1"], "top": r["top"], "bottom": r["bottom"],
                                    "size": r["height"]})
            # stessa casella disegnata due volte (quadrato + simbolo accanto, o bordo doppio): si tiene la più
            # a destra, cioè quella vicina all'etichetta
            parole_pag = pagina.extract_words()
            tenute = []
            for c in sorted(caselle, key=lambda c: (round(c["top"] / 4), c["x0"])):
                if tenute:
                    t = tenute[-1]
                    stessa_riga = abs((c["top"] + c["bottom"]) - (t["top"] + t["bottom"])) / 2 < 5
                    testo_tra = any(w["x0"] >= t["x1"] - 1 and w["x1"] <= c["x0"] + 1 and
                                    w["top"] - 4 <= (c["top"] + c["bottom"]) / 2 <= w["bottom"] + 4 for w in parole_pag)
                    if stessa_riga and c["x0"] - t["x1"] < 14 and not testo_tra:
                        tenute[-1] = c
                        continue
                tenute.append(c)
            for c in tenute:
                out.append({"pagina": n, "tipo": "casella", **c})
            # etichette: testo vicino sulla stessa riga (prima della riga, dopo la casella)
            righe = _righe(pagina)
            della_pagina = [s for s in out if s["pagina"] == n]
            for s in della_pagina:
                centro = (s["top"] + s["bottom"]) / 2
                riga = next((r for r in righe if any(w["top"] - 3 <= centro <= w["bottom"] + 3 for w in r)), [])
                vicini = sorted([t for t in della_pagina if t is not s and abs((t["top"] + t["bottom"]) / 2 - centro) < 5],
                                key=lambda t: t["x0"])
                prima_di = max([t["x1"] for t in vicini if t["x1"] <= s["x0"] + 1], default=0)
                dopo_di = min([t["x0"] for t in vicini if t["x0"] >= s["x1"] - 1], default=10 ** 6)
                if s["tipo"] == "riga":
                    etichetta = " ".join(w["text"] for w in riga if w["x0"] >= prima_di - 1 and w["x1"] <= s["x0"] + 1)
                    if not etichetta.strip():   # riga su due linee: l'etichetta sta nella riga sopra
                        sopra = [r for r in righe if r[0]["bottom"] <= s["top"] + 1]
                        etichetta = " ".join(w["text"] for w in sopra[-1]) if sopra else ""
                    etichetta = etichetta[-70:]
                else:
                    etichetta = " ".join(w["text"] for w in riga if w["x0"] >= s["x1"] - 1 and w["x1"] <= dopo_di + 1)
                    if not etichetta.strip():
                        etichetta = " ".join(w["text"] for w in riga if w["x1"] <= s["x0"] + 1 and w["x0"] >= prima_di - 1)
                    etichetta = etichetta[:70]
                s["etichetta"] = etichetta.strip()
    out.sort(key=lambda s: (s["pagina"], round(s["top"]), s["x0"]))
    for i, s in enumerate(out[:max_spazi], start=1):
        s["id"] = f"S{i}"
    return out[:max_spazi]


def vista(dati_pdf, elenco_spazi=None):
    """Il modulo come testo, riga per riga, con gli spazi segnati al loro posto: [S4] è una riga da scrivere,
    [☐S7] una casella. Così chi compila vede ogni spazio nel suo contesto, come sul foglio."""
    import pdfplumber
    elenco_spazi = elenco_spazi or spazi(dati_pdf)
    pagine_testo = []
    with pdfplumber.open(io.BytesIO(dati_pdf)) as pdf:
        for n, pagina in enumerate(pdf.pages, start=1):
            mie = [x for x in elenco_spazi if x["pagina"] == n]
            pezzi = [(c["x0"], c["x1"], (c["top"] + c["bottom"]) / 2, c["text"]) for c in pagina.chars
                     if not any(x["tipo"] == "riga" and x["x0"] - .5 <= c["x0"] and c["x1"] <= x["x1"] + .5
                                and abs(c["top"] - x["top"]) < 2 for x in mie)]
            pezzi += [(x["x0"], x["x1"], (x["top"] + x["bottom"]) / 2,
                       f" [{x['id']}] " if x["tipo"] == "riga" else f" [☐{x['id']}] ") for x in mie]
            righe = []
            for p_ in sorted(pezzi, key=lambda p_: (p_[2], p_[0])):
                if righe and abs(righe[-1][-1][2] - p_[2]) < 4:
                    righe[-1].append(p_)
                else:
                    righe.append([p_])
            testo = []
            for r in righe:
                r.sort(key=lambda p_: p_[0])
                linea, fine = "", None
                for x0, x1, _, t in r:
                    if fine is not None and x0 - fine > 1.8 and not linea.endswith(" "):
                        linea += " "
                    linea += t
                    fine = x1
                linea = re.sub(r"\s+", " ", linea).strip()
                if linea:
                    testo.append(linea)
            pagine_testo.append(f"--- Pagina {n} ---\n" + "\n".join(testo))
    return "\n".join(pagine_testo)


def scrivi(dati_pdf, valori, elenco_spazi=None):
    """Scrive i valori {id_spazio: testo, oppure 'X' per le caselle} sopra al modulo. Ritorna i byte del PDF."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import Color
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas

    elenco_spazi = elenco_spazi or spazi(dati_pdf)
    per_id = {s["id"]: s for s in elenco_spazi}
    lettore = PdfReader(io.BytesIO(dati_pdf))
    scrittore = PdfWriter(clone_from=lettore)
    blu = Color(0.05, 0.15, 0.45)
    usati, ignoti = 0, []
    for n, pagina in enumerate(scrittore.pages, start=1):
        larghezza, altezza = float(pagina.mediabox.width), float(pagina.mediabox.height)
        strato = io.BytesIO()
        c = canvas.Canvas(strato, pagesize=(larghezza, altezza))
        c.setFillColor(blu)
        scritto = False
        for chiave, valore in valori.items():
            s = per_id.get(chiave)
            if not s:
                if n == 1:
                    ignoti.append(chiave)
                continue
            if s["pagina"] != n or valore in (None, ""):
                continue
            testo = str(valore).strip()
            if s["tipo"] == "casella":
                if testo.lower() in ("", "no", "false", "0"):
                    continue
                dim = max(7.0, s["bottom"] - s["top"])
                c.setFont("Helvetica-Bold", dim)
                c.drawString(s["x0"] + 0.5, altezza - s["bottom"] + 1.5, "X")
            else:
                spazio = s["x1"] - s["x0"] - 4
                corpo = min(10.0, max(s.get("size", 10) - 1, 7.0))
                while corpo > 5.5 and stringWidth(testo, "Helvetica", corpo) > spazio:
                    corpo -= 0.5
                c.setFont("Helvetica", corpo)
                c.drawString(s["x0"] + 2, altezza - s["bottom"] + 3, testo)
            usati += 1
            scritto = True
        c.save()
        if scritto:
            pagina.merge_page(PdfReader(io.BytesIO(strato.getvalue())).pages[0])
    uscita = io.BytesIO()
    scrittore.write(uscita)
    return uscita.getvalue(), usati, ignoti


def anteprima(dati_pdf, pagina, scala=1.6):
    """Immagine PNG di una pagina (per vedere il documento nella pagina di Vega senza uscire)."""
    import pypdfium2
    doc = pypdfium2.PdfDocument(dati_pdf)
    try:
        if not 1 <= pagina <= len(doc):
            raise IndexError("pagina inesistente")
        img = doc[pagina - 1].render(scale=scala).to_pil()
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue(), len(doc)
    finally:
        doc.close()


def pagine(dati_pdf):
    import pypdfium2
    doc = pypdfium2.PdfDocument(dati_pdf)
    try:
        return len(doc)
    finally:
        doc.close()
