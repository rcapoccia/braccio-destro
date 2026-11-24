"""
Braccio destro - Pratiche questionario

Quando una mail riguarda un questionario da mandare a una compagnia (es. CAR per ITAS):
1. cerca nella posta il questionario già compilato dal cliente e la visura camerale dell'azienda;
2. compila il modulo della compagnia (se mappato) con quei dati;
3. restituisce testo della bozza, allegati e una nota per Roberto con ciò che manca.
Non salva nulla su disco.
"""

import datetime
import email
import email.policy
import json
import logging
import re

import mistral
import questionari

log = logging.getLogger("posta")
GIORNI_RICERCA = 120
MAX_MAIL_PER_CARTELLA = 15


def _pdf_allegati(msg):
    for parte in msg.walk():
        nome = parte.get_filename() or ""
        if nome.lower().endswith(".pdf") or parte.get_content_type() == "application/pdf":
            dati = parte.get_payload(decode=True)
            if dati:
                yield nome or "documento.pdf", dati


def _testo_documento(nome, dati):
    try:
        testo = questionari.testo_pdf(dati)
    except Exception:
        testo = ""
    if questionari.testo_leggibile(testo):
        return testo
    # Scansione o testo illeggibile: OCR solo per documenti che sembrano pertinenti.
    if re.search(r"(?i)visura|questionario|car|proposta|modulo", nome):
        try:
            return mistral.ocr_pdf(dati)[:30000]
        except mistral.TettoRaggiunto:
            raise
        except Exception as e:
            log.warning("OCR non riuscito su un allegato: %s", type(e).__name__)
    return ""


def _cartella_inviata(imap):
    """Cartella della posta inviata (attributo \\Sent o nomi comuni), già tra virgolette."""
    typ, righe = imap.list()
    nomi = []
    for riga in righe or []:
        m = re.match(r'\((?P<flag>[^)]*)\) "(?P<sep>[^"]*)" (?P<nome>.+)$', riga.decode(errors="replace"))
        if not m:
            continue
        nome = m.group("nome").strip().strip('"')
        nomi.append(nome)
        if "\\Sent" in m.group("flag"):
            return f'"{nome}"'
    for c in ("INBOX.Sent", "Sent", "INBOX.Posta inviata", "Posta inviata", "Sent Items", "INBOX.Sent Items"):
        if c in nomi:
            return f'"{c}"'
    return None


def _candidati(imap, msg_corrente, parola_chiave, cartella_attuale):
    """PDF pertinenti alla parola chiave: prima la mail attuale, poi la posta (arrivata e inviata), dal più recente."""
    chiave = parola_chiave.lower()

    def da_messaggio(m, origine):
        for nome, dati in _pdf_allegati(m):
            testo = _testo_documento(nome, dati)
            if chiave in testo.lower() or chiave in nome.lower():
                yield {"nome": nome, "dati": dati, "testo": testo, "origine": origine}

    yield from da_messaggio(msg_corrente, "mail attuale")
    dal = (datetime.date.today() - datetime.timedelta(days=GIORNI_RICERCA)).strftime("%d-%b-%Y")
    parola_ascii = re.sub(r"[^A-Za-z0-9 ]", "", parola_chiave).strip() or parola_chiave
    cartelle = ["INBOX"]
    inviata = _cartella_inviata(imap)
    if inviata:
        cartelle.append(inviata)
    try:
        for cartella in cartelle:
            if imap.select(cartella, readonly=True)[0] != "OK":
                continue
            typ, ris = imap.uid("SEARCH", None, "SINCE", dal, "TEXT", f'"{parola_ascii}"')
            uids = (ris[0] or b"").split()[-MAX_MAIL_PER_CARTELLA:][::-1]
            for u in uids:
                typ, parti = imap.uid("FETCH", u, "(BODY.PEEK[])")
                grezzo = next((p[1] for p in parti if isinstance(p, tuple)), None)
                if grezzo:
                    m = email.message_from_bytes(grezzo, policy=email.policy.default)
                    yield from da_messaggio(m, f"mail del {m.get('Date', '')[:16]} - {m.get('Subject', '')[:60]}")
    finally:
        imap.select(cartella_attuale)


def cerca_documenti(imap, msg_corrente, parola_chiave, cartella_attuale):
    """Trova il questionario compilato dal cliente e la visura dell'azienda (i più recenti)."""
    trovati = {"questionario": None, "visura": None}
    gen = _candidati(imap, msg_corrente, parola_chiave, cartella_attuale)
    try:
        for doc in gen:
            t, n = doc["testo"].lower(), doc["nome"].lower()
            if not trovati["visura"] and ("visura" in n or ("visura" in t and "registro delle imprese" in t)):
                trovati["visura"] = doc
            elif not trovati["questionario"] and "questionario" in t:
                trovati["questionario"] = doc
            if all(trovati.values()):
                break
    finally:
        gen.close()
    return trovati


PAROLE_VUOTE = {"di", "del", "della", "il", "la", "lo", "le", "i", "gli", "un", "una", "e", "per", "con", "da", "in"}


def cerca_documento(imap, msg_corrente, parola_chiave, descrizione, cartella_attuale):
    """Il documento più recente che riguarda la parola chiave e corrisponde alla descrizione (es. 'visura camerale')."""
    parole = [w for w in re.findall(r"\w+", descrizione.lower()) if w not in PAROLE_VUOTE and len(w) > 2]
    gen = _candidati(imap, msg_corrente, parola_chiave, cartella_attuale)
    try:
        for doc in gen:
            testo = (doc["nome"] + " " + doc["testo"][:5000]).lower()
            if not parole or any(w in testo for w in parole):
                return doc
    finally:
        gen.close()
    return None


SISTEMA_COMPILA = """Sei l'assistente di Roberto Capoccia, broker assicurativo (Stile Italiano).
Devi compilare il modulo "{modulo}" usando SOLO le fonti fornite: il questionario già compilato dal cliente
per un'altra compagnia, la visura camerale dell'azienda e il testo della mail.

REGOLE:
- Compila un campo solo se il dato è nelle fonti. Non inventare e non stimare. Se manca, non mettere il campo in "valori"
  e aggiungilo a "mancanti".
- Campi "si_no": "si" o "no" solo se la fonte risponde alla stessa domanda (anche con parole diverse). Altrimenti mancante.
- Campi "casella": "si" solo se la fonte lo indica esplicitamente.
- Importi nel formato "€ 181.101,80". Date nel formato gg/mm/aaaa.
- Domicilio di contraente e assicurato: sede legale dalla visura (via, numero, CAP, comune, provincia).
  Se manca l'assicurato, NON copiarlo dal contraente: segnalalo in "da_verificare".
- Se un dato è ambiguo (es. durata o date non chiare), mettilo comunque se ragionevole e spiegalo in "da_verificare".
- Testi brevi: le righe del modulo sono corte.

Poi scrivi la bozza della mail di risposta al destinatario con lo stile di Roberto: "Ciao Nome,", frasi brevi,
nessun burocratese, chiusura "Grazie e ciao", firma "Roberto". La bozza dice che in allegato ci sono
{allegati}. Non prendere impegni su tempi. Non elencare i dati mancanti nella bozza.

Rispondi SOLO in JSON: {{"valori": {{id: valore}}, "mancanti": [id], "da_verificare": [{{"id": id, "motivo": testo}}],
"bozza": testo}}"""


def compila(msg, nome_modulo, documenti, contesto_mail):
    mappa = questionari.carica_modulo(nome_modulo)
    campi = [{"id": c["id"], "tipo": c["tipo"], "domanda": c["domanda"]} for c in mappa["campi"]]
    allegati = "il questionario " + mappa["compagnia"] + " compilato" + (" e la visura camerale" if documenti["visura"] else "")
    utente = (
        f"=== MAIL A CUI RISPONDERE ===\n{contesto_mail[:4000]}\n\n"
        f"=== QUESTIONARIO COMPILATO DAL CLIENTE ===\n{documenti['questionario']['testo'][:16000]}\n\n"
        f"=== VISURA CAMERALE ===\n{(documenti['visura'] or {}).get('testo', '(non trovata)')[:9000]}\n\n"
        f"=== CAMPI DEL MODULO DA COMPILARE ===\n{json.dumps(campi, ensure_ascii=False)}"
    )
    r = mistral.chat_json(SISTEMA_COMPILA.format(modulo=mappa["modulo"], allegati=allegati), utente, temperatura=0.1)
    valori = r.get("valori") or {}
    pdf, troncati = questionari.compila_pdf(mappa, valori)
    etichette = {c["id"]: c["domanda"] for c in mappa["campi"]}
    ids_validi = set(etichette)
    mancanti = [i for i in (r.get("mancanti") or []) if i in ids_validi]
    # Tutti i campi non compilati contano come mancanti.
    mancanti += [i for i in etichette if i not in valori and i not in mancanti]
    # Le domande "se sì..." e i costi contano solo se la domanda principale ha risposta sì: non le elenchiamo.
    condizionali = {c["id"] for c in mappa["campi"]
                    if c["domanda"].lower().startswith("se sì") or c["id"].startswith("costo_")}
    allegato = [i for i in mancanti if i.startswith("all2_")]
    mancanti = [i for i in mancanti if i not in condizionali and not i.startswith("all2_")]
    return {
        "pdf": pdf,
        "nome_pdf": f"Questionario_{mappa['compagnia']}_{mappa['ramo']}.pdf",
        "bozza": r.get("bozza", ""),
        "mancanti": [etichette[i] for i in mancanti],
        "mancanti_allegato": [etichette[i] for i in allegato],
        "da_verificare": [f"{etichette.get(d.get('id'), d.get('id'))}: {d.get('motivo', '')}"
                          for d in (r.get("da_verificare") or []) if isinstance(d, dict)],
        "troncati": [etichette[i] for i in troncati],
        "compilati": len(valori),
        "totale": len(etichette),
    }


def nota_per_roberto(info, documenti, risultato, oggetto_mail):
    righe = [f"Pratica: questionario {info.get('compagnia', '')} {info.get('ramo', '')} - {info.get('azienda', '')}",
             f"Mail: {oggetto_mail}", ""]
    righe.append("Documenti usati:")
    for k, etichetta in (("questionario", "Questionario del cliente"), ("visura", "Visura camerale")):
        d = documenti.get(k)
        righe.append(f"- {etichetta}: " + (f"{d['nome']} ({d['origine']})" if d else "NON TROVATO"))
    righe.append("")
    if risultato:
        righe.append(f"Campi compilati: {risultato['compilati']} su {risultato['totale']}. "
                     "La bozza con il questionario compilato è nelle Bozze.")
        if risultato["da_verificare"]:
            righe += ["", "DA VERIFICARE:"] + [f"- {x}" for x in risultato["da_verificare"]]
        if risultato["troncati"]:
            righe += ["", "TESTO TAGLIATO (non entrava nello spazio del modulo):"] + [f"- {x}" for x in risultato["troncati"]]
        if risultato["mancanti"]:
            righe += ["", "DOMANDE SENZA RISPOSTA nel questionario del cliente:"] + [f"- {x}" for x in risultato["mancanti"]]
        if risultato.get("mancanti_allegato"):
            righe += ["", f"Allegato 2 Fabbricati (solo se pertinente): {len(risultato['mancanti_allegato'])} campi vuoti."]
    else:
        righe.append("Non ho potuto compilare il modulo della compagnia: nella bozza ho allegato "
                     "i documenti che ho trovato (soluzione di riserva).")
    righe += ["", "-- Braccio destro (nota interna, non inoltrare)"]
    return "\n".join(righe)
