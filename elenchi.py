"""
Braccio destro - Lavori su un elenco (Excel)

Esempio: Roberto allega l'estrazione dei titoli e chiede "trovami nella posta tutte le polizze dell'elenco
con decorrenza 2025; se nel 2025 trovi solo la quietanza, cerca la polizza nel 2024; salvale nelle schede clienti".
Per ogni polizza dell'elenco: cerca nella posta il numero di polizza e il nome del cliente, legge i PDF trovati, fa decidere a Mistral
quale è il documento di polizza e con che decorrenza, e salva il documento giusto nella scheda del cliente.
Alla fine prepara un Excel con l'esito riga per riga.
"""

import datetime
import email
import email.policy
import hashlib
import io
import json
import logging
import re

import mistral
import pratiche
import questionari

log = logging.getLogger("elenchi")
MAX_MAIL_PER_POLIZZA = 12
MAX_MAIL_PER_NOME = 15

SISTEMA = """Sei l'assistente di un broker. Cerco il documento della polizza n. {numero} ({compagnia}, {rischio},
cliente {cliente}). Ricevi PDF trovati nella posta (nome file, oggetto della mail, inizio del testo).
Per ciascuno indica:
"riferito": true se il documento riguarda proprio la polizza {numero} (o il suo cliente e rischio), altrimenti false;
"tipo": "polizza" (contratto, frontespizio, scheda di polizza, certificato), "quietanza" (rinnovo/pagamento premio
di un'annualità), "appendice", "preventivo" o "altro";
"decorrenza": data di decorrenza/effetto del documento gg/mm/aaaa se scritta, altrimenti "".
Non inventare. Rispondi SOLO in JSON: {{"documenti": [{{"n": ..., "riferito": true/false, "tipo": ..., "decorrenza": ...}}]}}"""

INTESTAZIONI = {
    "cliente": ("cliente", "contraente", "assicurato", "ragione sociale"),
    "polizza": ("polizza", "n. polizza", "numero polizza", "n° polizza"),
    "compagnia": ("compagnia", "impresa"),
    "rischio": ("rischio", "ramo", "prodotto"),
    "inizio": ("data inizio", "decorrenza", "effetto"),
    "fine": ("data fine", "scadenza"),
    "tipo": ("tipo carico", "tipo titolo", "tipo"),
    "targa": ("targa",),
}


def leggi_excel(dati):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(dati), read_only=True, data_only=True)
    righe = list(wb.worksheets[0].iter_rows(values_only=True))
    if not righe:
        return []
    testa = [str(x or "").strip().lower() for x in righe[0]]
    colonne = {}
    for campo, nomi in INTESTAZIONI.items():
        for i, h in enumerate(testa):
            if i not in colonne.values() and any(h == n or h.startswith(n) for n in nomi):
                colonne[campo] = i
                break
    if "polizza" not in colonne or "cliente" not in colonne:
        raise ValueError("Nell'Excel non trovo le colonne Cliente e Polizza.")
    out = []
    for r in righe[1:]:
        voce = {}
        for campo, i in colonne.items():
            v = r[i] if i < len(r) else None
            voce[campo] = v.strftime("%d/%m/%Y") if isinstance(v, (datetime.date, datetime.datetime)) else \
                re.sub(r"\s+", " ", str(v or "")).strip()
        if voce.get("polizza"):
            out.append(voce)
    return out


def polizze_uniche(righe):
    viste = {}
    for r in righe:
        viste.setdefault(r["polizza"], r)
    return list(viste.values())


def _varianti_numero(numero):
    n = numero.strip()
    v = [n]
    for x in (n.replace("/", ""), n.replace(" ", ""), n.split("/")[-1]):
        if len(re.sub(r"\D", "", x)) >= 5 and x not in v:
            v.append(x)
    return v


def _varianti_nome(cliente):
    """"FUMANTI ALVARO" -> anche "ALVARO FUMANTI"; "ROSSI SRL" -> anche "ROSSI"."""
    import ricerca
    nome = re.sub(r"\s+", " ", cliente or "").strip()
    if len(nome) < 4:
        return []
    v = ricerca.varianti(nome)
    parole = nome.split(" ")
    if len(parole) == 2 and not re.search(r"(?i)\b(srl|spa|snc|sas|ss)\b", nome):
        v.append(f"{parole[1]} {parole[0]}")
    return [x for x in dict.fromkeys(v) if len(x) >= 4][:4]


def _solo_cifre(testo):
    return re.sub(r"[^0-9A-Za-z]", "", testo or "").upper()


def _anno(data):
    m = re.search(r"(\d{4})\s*$", data or "")
    return int(m.group(1)) if m else None


def cerca_polizza(imap, voce, anno, anno_riserva):
    """Ritorna {"esito", "documento": {nome, dati, decorrenza} o None}."""
    dal = f"01-Jan-{min(anno, anno_riserva)}"
    numeri = _varianti_numero(voce["polizza"])
    chiave = _solo_cifre(voce["polizza"].split("/")[-1])
    nomi = _varianti_nome(voce.get("cliente", ""))
    cartelle = ["INBOX"] + ([pratiche._cartella_inviata(imap)] if pratiche._cartella_inviata(imap) else [])
    candidati, visti = [], set()
    for cartella in cartelle:
        if imap.select(cartella, readonly=True)[0] != "OK":
            continue
        # prima le mail che citano il numero di polizza, poi quelle che citano il cliente
        con_numero, con_nome = set(), set()
        for n in numeri:
            typ, r = imap.uid("SEARCH", None, "SINCE", dal, "TEXT", f'"{n}"')
            con_numero.update((r[0] or b"").split())
        for n in nomi:
            typ, r = imap.uid("SEARCH", None, "SINCE", dal, "TEXT", f'"{n}"')
            con_nome.update((r[0] or b"").split())
        ordinati = sorted(con_numero, key=int, reverse=True) + \
            sorted(con_nome - con_numero, key=int, reverse=True)[:MAX_MAIL_PER_NOME]
        for u in ordinati[:MAX_MAIL_PER_POLIZZA + MAX_MAIL_PER_NOME]:
            typ, parti = imap.uid("FETCH", u, "(BODY.PEEK[])")
            grezzo = next((p[1] for p in parti if isinstance(p, tuple)), None)
            if not grezzo:
                continue
            m = email.message_from_bytes(grezzo, policy=email.policy.default)
            oggetto = str(m.get("Subject") or "")
            for parte in m.walk():
                nome = parte.get_filename() or ""
                if not nome.lower().endswith(".pdf"):
                    continue
                dati = parte.get_payload(decode=True)
                if not dati or len(dati) < 5000:
                    continue
                sha = hashlib.sha256(dati).hexdigest()
                if sha in visti:
                    continue
                visti.add(sha)
                try:
                    testo = questionari.testo_pdf(dati, 5000)
                except Exception:
                    testo = ""
                if chiave and chiave not in _solo_cifre(nome + oggetto) and not questionari.testo_leggibile(testo):
                    try:   # PDF scansionato: lo legge l'OCR di Mistral (UE)
                        testo = mistral.ocr_pdf(dati)[:5000]
                    except mistral.TettoRaggiunto:
                        raise
                    except Exception:
                        testo = ""
                # il numero di polizza deve comparire nel PDF, nel nome del file o nell'oggetto della mail
                # (così si trovano anche le polizze mandate con il solo nome del cliente nella mail)
                if chiave and chiave not in _solo_cifre(testo + nome + oggetto):
                    continue
                candidati.append({"nome": nome, "dati": dati, "testo": testo, "oggetto": oggetto[:90]})
    if not candidati:
        return {"esito": "non trovata", "documento": None}
    elenco = [{"n": i, "file": c["nome"], "mail": c["oggetto"], "testo": c["testo"][:1500]}
              for i, c in enumerate(candidati[:15])]
    r = mistral.chat_json(SISTEMA.format(numero=voce["polizza"], compagnia=voce.get("compagnia", ""),
                                         rischio=voce.get("rischio", ""), cliente=voce.get("cliente", "")),
                          json.dumps(elenco, ensure_ascii=False), temperatura=0.0)
    for x in r.get("documenti") or []:
        try:
            candidati[int(x["n"])].update({"riferito": bool(x.get("riferito")), "tipo": x.get("tipo", ""),
                                           "decorrenza": x.get("decorrenza", "")})
        except (KeyError, ValueError, IndexError, TypeError):
            continue
    polizze = [c for c in candidati if c.get("riferito") and c.get("tipo") == "polizza"]
    quietanze = [c for c in candidati if c.get("riferito") and c.get("tipo") == "quietanza"]
    nell_anno = [c for c in polizze if _anno(c["decorrenza"]) == anno]
    if nell_anno:
        return {"esito": f"polizza {anno}", "documento": nell_anno[0]}
    riserva = [c for c in polizze if _anno(c["decorrenza"]) == anno_riserva]
    if riserva:
        motivo = " (nel %d solo quietanza)" % anno if any(_anno(q["decorrenza"]) == anno for q in quietanze) else ""
        return {"esito": f"polizza {anno_riserva}{motivo}", "documento": riserva[0]}
    if polizze:
        p = polizze[0]
        return {"esito": f"polizza con decorrenza {p['decorrenza'] or 'non letta'}", "documento": p}
    if quietanze:
        return {"esito": "trovate solo quietanze, polizza non trovata", "documento": None}
    return {"esito": "non trovata", "documento": None}


def report_excel(risultati):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Esito"
    ws.append(["Cliente", "Polizza", "Compagnia", "Rischio", "Esito", "Documento salvato", "Decorrenza documento"])
    for c in ws[1]:
        c.font = Font(bold=True)
    colori = {"ok": "E5F4E4", "no": "FBEBEB", "forse": "F9EEDA"}
    for r in risultati:
        ws.append([r["cliente"], r["polizza"], r["compagnia"], r["rischio"], r["esito"], r.get("file", ""),
                   r.get("decorrenza", "")])
        stato = "ok" if r.get("file") else ("forse" if "quietanz" in r["esito"] else "no")
        for c in ws[ws.max_row]:
            c.fill = PatternFill("solid", fgColor=colori[stato])
    for col, larg in zip("ABCDEFG", (34, 16, 40, 28, 38, 40, 16)):
        ws.column_dimensions[col].width = larg
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
