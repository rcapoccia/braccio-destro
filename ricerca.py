"""
Braccio destro - Ricerca documenti nella posta

Su istruzione di Roberto (es. "cerca polizze e preventivi di E-ALL") cerca nella posta arrivata e inviata
le mail che nominano il cliente (anche con varianti del nome: E-ALL, E ALL, EALL), raccoglie gli allegati,
toglie loghi, firme e doppioni, e fa decidere a Mistral quali documenti riguardano DAVVERO quel cliente
(contraente, assicurato o oggetto del documento): i documenti di altri clienti finiti nella stessa mail
vengono scartati. Non salva nulla sul server.
"""

import datetime
import email
import email.policy
import email.utils
import hashlib
import json
import logging
import re

import mistral
import pratiche
import questionari

log = logging.getLogger("posta")

ANNI_RICERCA = 3
MAX_MAIL_PER_CARTELLA = 80
MAX_DOCUMENTI = 80
MAX_ALLEGATI_BYTE = 18 * 1024 * 1024       # limite prudente per la nota con allegati
ESTENSIONI = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".p7m", ".zip", ".eml", ".msg")
IMMAGINI = (".jpg", ".jpeg", ".png")
ORDINE_TIPI = ["polizza", "preventivo", "appendice", "quietanza", "questionario", "visura", "sinistro",
               "contratto", "fattura", "documento", "altro"]
TITOLI_TIPI = {"polizza": "Polizze", "preventivo": "Preventivi e quotazioni", "appendice": "Appendici",
               "quietanza": "Quietanze e pagamenti", "questionario": "Questionari", "visura": "Visure",
               "sinistro": "Sinistri", "contratto": "Condizioni e contratti", "fattura": "Fatture",
               "documento": "Documenti d'identità", "altro": "Altro"}

SISTEMA = """Sei l'assistente di un broker assicurativo. Il broker cerca i documenti del cliente "{cliente}".
Ricevi documenti trovati nella posta (nome file, oggetto della mail, inizio del testo).
Per ciascuno decidi:
"riguarda": true SOLO se il documento è del cliente {cliente} (è contraente, assicurato, intestatario o
oggetto del documento) oppure è un modulo/questionario/condizioni usato per una pratica di {cliente}.
false se riguarda un altro cliente (es. preventivi di altre aziende allegati per confronto), se è un logo,
una firma, un'immagine decorativa o se non si capisce.
"tipo": uno tra "polizza", "preventivo", "appendice", "quietanza", "questionario", "visura", "sinistro",
"contratto" (condizioni, set informativo, informative), "fattura", "documento" (identità), "altro".
"compagnia": nome breve della compagnia (es. "Generali", "Helvetia", "Tutela Legale") o "".
"numero": numero di polizza o preventivo se scritto, altrimenti "".
"scadenza": data di scadenza gg/mm/aaaa se scritta, altrimenti "".
"descrizione": max 8 parole, chiare per il broker (es. "RCT/O impianti fotovoltaici", "Perdite pecuniarie Bonet").
Usa solo ciò che vedi: non inventare.
Rispondi SOLO in JSON: {{"documenti": [{{"n": ..., "riguarda": true/false, "tipo": ..., "compagnia": ...,
"numero": ..., "scadenza": ..., "descrizione": ...}}]}}"""


def _ascii(parola):
    return re.sub(r"[^A-Za-z0-9 .&\-]", "", parola).strip() or parola


def varianti(nome):
    """E-ALL -> E-ALL, E ALL, EALL; "Rossi Srl" -> "Rossi Srl", "Rossi"."""
    base = _ascii(nome)
    v = [base]
    senza_forma = re.sub(r"(?i)[\s.,]+(s\.?r\.?l\.?s?|s\.?p\.?a\.?|s\.?n\.?c\.?|s\.?a\.?s\.?)$", "", base).strip()
    candidati = [senza_forma, re.sub(r"[-_.]", " ", senza_forma)]
    if " " not in senza_forma:          # sigle come E-ALL: anche EALL
        candidati.append(re.sub(r"[-_.]", "", senza_forma))
    for x in candidati:
        x = re.sub(r"\s+", " ", x).strip()
        if len(x) >= 3 and x.lower() not in {y.lower() for y in v}:
            v.append(x)
    return v[:4]


def _allegati(msg):
    for parte in msg.walk():
        nome = parte.get_filename()
        if not nome or not nome.lower().endswith(ESTENSIONI):
            continue
        dati = parte.get_payload(decode=True)
        if not dati or len(dati) < 2048:
            continue
        if nome.lower().endswith(IMMAGINI):
            # loghi e firme: immagini piccole o inserite nel testo della mail
            if len(dati) < 60 * 1024 or parte.get("Content-ID") or (parte.get_content_disposition() == "inline"):
                continue
        yield nome, dati


def _testo(nome, dati):
    n = nome.lower()
    try:
        if n.endswith(".pdf"):
            return questionari.testo_pdf(dati, 4000)
        if n.endswith(".docx"):
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(dati)) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            return re.sub(r"<[^>]+>", " ", re.sub(r"</w:p>", "\n", xml))[:4000]
    except Exception:
        return ""
    return ""


def cerca(imap, nomi, cartella_attuale):
    """Allegati delle mail che nominano uno dei nomi, dal più recente, senza doppioni."""
    dal = (datetime.date.today() - datetime.timedelta(days=365 * ANNI_RICERCA)).strftime("%d-%b-%Y")
    cartelle = [("INBOX", "ricevuta")]
    inviata = pratiche._cartella_inviata(imap)
    if inviata:
        cartelle.append((inviata, "inviata"))
    documenti, viste = [], set()
    try:
        for cartella, verso in cartelle:
            if imap.select(cartella, readonly=True)[0] != "OK":
                continue
            uids = set()
            for nome in nomi:
                typ, ris = imap.uid("SEARCH", None, "SINCE", dal, "TEXT", f'"{nome}"')
                uids.update((ris[0] or b"").split())
            for u in sorted(uids, key=int, reverse=True)[:MAX_MAIL_PER_CARTELLA]:
                typ, parti = imap.uid("FETCH", u, "(BODY.PEEK[])")
                grezzo = next((p[1] for p in parti if isinstance(p, tuple)), None)
                if not grezzo:
                    continue
                m = email.message_from_bytes(grezzo, policy=email.policy.default)
                if str(m.get("Subject") or "").startswith("[Braccio destro]"):
                    continue
                try:
                    quando = email.utils.parsedate_to_datetime(str(m.get("Date")))
                except Exception:
                    quando = None
                persona = str((m.get("To") if verso == "inviata" else m.get("From")) or "")
                nome_p, indirizzo = email.utils.parseaddr(persona.split(",")[0])
                for nome, dati in _allegati(m):
                    impronta = hashlib.sha256(dati).hexdigest()
                    if impronta in viste:          # stesso file arrivato o inoltrato più volte
                        continue
                    viste.add(impronta)
                    documenti.append({
                        "nome": nome, "dati": dati, "testo": _testo(nome, dati),
                        "data": quando.strftime("%d/%m/%Y") if quando else "",
                        "ordine": quando.timestamp() if quando else 0, "verso": verso,
                        "contatto": nome_p or indirizzo, "oggetto": str(m.get("Subject") or "")[:90],
                    })
    finally:
        imap.select(cartella_attuale)
    documenti.sort(key=lambda d: -d["ordine"])
    return documenti[:MAX_DOCUMENTI]


def classifica(documenti, cliente):
    sistema = SISTEMA.format(cliente=cliente)
    for inizio in range(0, len(documenti), 20):
        blocco = documenti[inizio:inizio + 20]
        elenco = [{"n": i, "file": d["nome"], "mail": d["oggetto"], "testo": d["testo"][:1200]}
                  for i, d in enumerate(blocco)]
        r = mistral.chat_json(sistema, json.dumps(elenco, ensure_ascii=False), temperatura=0.1)
        for x in r.get("documenti") or []:
            try:
                d = blocco[int(x["n"])]
            except (KeyError, ValueError, IndexError, TypeError):
                continue
            d.update({k: str(x.get(k) or "") for k in ("tipo", "compagnia", "numero", "scadenza", "descrizione")})
            d["riguarda"] = bool(x.get("riguarda"))
            if d["tipo"] not in ORDINE_TIPI:
                d["tipo"] = "altro"


def trova(imap, richiesta, cartella_attuale):
    """Ritorna {"cliente", "nomi", "documenti" (solo del cliente, ordinati), "scartati"}."""
    cliente = (richiesta.get("parola_chiave") or "").strip()
    nomi = varianti(cliente) + [_ascii(v) for v in (richiesta.get("varianti") or []) if len(_ascii(v)) >= 3]
    nomi = list(dict.fromkeys(nomi))[:6]
    tutti = cerca(imap, nomi, cartella_attuale)
    classifica(tutti, cliente)
    del_cliente = [d for d in tutti if d.get("riguarda")]
    tipi = [t.lower() for t in (richiesta.get("tipi") or [])]
    if tipi:   # prima i tipi chiesti, poi il resto
        del_cliente.sort(key=lambda d: (d.get("tipo") not in tipi, ORDINE_TIPI.index(d.get("tipo", "altro")), -d["ordine"]))
    else:
        del_cliente.sort(key=lambda d: (ORDINE_TIPI.index(d.get("tipo", "altro")), -d["ordine"]))
    log.info("Ricerca eseguita: %d allegati esaminati, %d del cliente", len(tutti), len(del_cliente))
    return {"cliente": cliente, "nomi": nomi, "documenti": del_cliente, "scartati": len(tutti) - len(del_cliente)}


def riga(d):
    return " · ".join(x for x in (d.get("compagnia", ""), f"n. {d['numero']}" if d.get("numero") else "",
                                  f"scad. {d['scadenza']}" if d.get("scadenza") else "") if x)


def esegui(imap, richiesta, cartella_attuale):
    """Per la nota via mail: ritorna (oggetto_nota, testo_nota, allegati)."""
    esito = trova(imap, richiesta, cartella_attuale)
    documenti = esito["documenti"]
    righe = [f"Documenti di {esito['cliente']}: {len(documenti)}",
             f"(cercato come: {', '.join(esito['nomi'])} · posta arrivata e inviata, ultimi {ANNI_RICERCA} anni"
             + (f" · scartati {esito['scartati']} allegati di altri clienti o non utili)" if esito["scartati"] else ")"),
             ""]
    if not documenti:
        righe.append("Nessun documento trovato. Prova con la ragione sociale completa o la partita IVA.")
    allegati, peso, tipo_corrente = [], 0, None
    for d in documenti:
        if d["tipo"] != tipo_corrente:
            tipo_corrente = d["tipo"]
            righe += ["", TITOLI_TIPI[tipo_corrente].upper()]
        righe.append(f"• {d.get('descrizione') or d['nome']}" + (f"  —  {riga(d)}" if riga(d) else ""))
        righe.append(f"  {d['data']} · {'da' if d['verso'] == 'ricevuta' else 'a'} {d['contatto']} · file: {d['nome']}")
        if peso + len(d["dati"]) <= MAX_ALLEGATI_BYTE:
            allegati.append((d["nome"], d["dati"]))
            peso += len(d["dati"])
    if len(allegati) < len(documenti):
        righe += ["", f"Allegati: {len(allegati)} su {len(documenti)} (gli altri superano il limite di dimensione)."]
    righe += ["", "-- Braccio destro (nota interna, non inoltrare)"]
    return f"Documenti di {esito['cliente']}: {len(documenti)}", "\n".join(righe), allegati
