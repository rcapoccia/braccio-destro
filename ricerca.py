"""
Braccio destro - Ricerca documenti nella posta

Su istruzione di Roberto (es. "AI: cerca polizze e preventivi di E-ALL") cerca nella posta arrivata e inviata
le mail che nominano il cliente, raccoglie gli allegati, li classifica (polizza, preventivo, quietanza...)
e consegna a Roberto una nota con l'elenco e gli allegati. Non salva nulla sul server.
"""

import datetime
import email
import email.policy
import email.utils
import json
import logging
import re

import mistral
import pratiche
import questionari

log = logging.getLogger("posta")

ANNI_RICERCA = 3
MAX_MAIL_PER_CARTELLA = 60
MAX_ALLEGATI_BYTE = 18 * 1024 * 1024       # limite prudente per la nota con allegati
ESTENSIONI = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".p7m", ".zip")

SISTEMA = """Sei l'assistente di un broker assicurativo. Ricevi un elenco di documenti trovati nella posta
(nome del file, mail di provenienza, inizio del testo). Per ciascuno indica:
"tipo": uno tra "polizza", "preventivo", "quietanza", "appendice", "questionario", "visura", "sinistro",
"contratto", "fattura", "altro"; "compagnia" (se riconoscibile, altrimenti ""); "numero" (numero di polizza o
preventivo se presente, altrimenti ""); "scadenza" (data di scadenza gg/mm/aaaa se presente, altrimenti "");
"descrizione" (max 10 parole: ramo o oggetto, es. "RC auto furgone", "CAR cantiere via Roma").
Usa solo ciò che vedi nel testo o nel nome del file: non inventare.
Rispondi SOLO in JSON: {"documenti": [{"n": numero d'ordine, "tipo": ..., "compagnia": ..., "numero": ...,
"scadenza": ..., "descrizione": ...}]}"""


def _parola_ascii(parola):
    return re.sub(r"[^A-Za-z0-9 .\-]", "", parola).strip() or parola


def _allegati(msg):
    for parte in msg.walk():
        nome = parte.get_filename()
        if not nome or not nome.lower().endswith(ESTENSIONI):
            continue
        dati = parte.get_payload(decode=True)
        if dati and len(dati) > 1024:          # salta loghi e firme
            yield nome, dati


def cerca(imap, parola_chiave, cartella_attuale):
    """Tutti gli allegati delle mail che nominano la parola chiave, dal più recente."""
    dal = (datetime.date.today() - datetime.timedelta(days=365 * ANNI_RICERCA)).strftime("%d-%b-%Y")
    parola = _parola_ascii(parola_chiave)
    cartelle = [("INBOX", "ricevuta")]
    inviata = pratiche._cartella_inviata(imap)
    if inviata:
        cartelle.append((inviata, "inviata"))
    documenti, viste = [], set()
    try:
        for cartella, verso in cartelle:
            if imap.select(cartella, readonly=True)[0] != "OK":
                continue
            typ, ris = imap.uid("SEARCH", None, "SINCE", dal, "TEXT", f'"{parola}"')
            uids = (ris[0] or b"").split()[-MAX_MAIL_PER_CARTELLA:][::-1]
            for u in uids:
                typ, parti = imap.uid("FETCH", u, "(BODY.PEEK[])")
                grezzo = next((p[1] for p in parti if isinstance(p, tuple)), None)
                if not grezzo:
                    continue
                m = email.message_from_bytes(grezzo, policy=email.policy.default)
                if (m.get("Subject") or "").startswith("[Braccio destro]"):
                    continue
                try:
                    data = email.utils.parsedate_to_datetime(m.get("Date")).strftime("%d/%m/%Y")
                except Exception:
                    data = ""
                for nome, dati in _allegati(m):
                    impronta = (nome, len(dati))
                    if impronta in viste:          # stesso file inoltrato più volte
                        continue
                    viste.add(impronta)
                    testo = ""
                    if nome.lower().endswith(".pdf"):
                        try:
                            testo = questionari.testo_pdf(dati, 6000)
                        except Exception:
                            testo = ""
                    documenti.append({
                        "nome": nome, "dati": dati, "testo": testo, "data": data, "verso": verso,
                        "contatto": (m.get("To") if verso == "inviata" else m.get("From")) or "",
                        "oggetto": (m.get("Subject") or "")[:80],
                    })
    finally:
        imap.select(cartella_attuale)
    return documenti


def classifica(documenti):
    if not documenti:
        return
    elenco = [{"n": i, "file": d["nome"], "mail": d["oggetto"], "testo": d["testo"][:1200]}
              for i, d in enumerate(documenti[:40])]
    r = mistral.chat_json(SISTEMA, json.dumps(elenco, ensure_ascii=False), temperatura=0.1)
    for x in r.get("documenti") or []:
        try:
            documenti[int(x["n"])].update({k: x.get(k, "") for k in ("tipo", "compagnia", "numero", "scadenza", "descrizione")})
        except (KeyError, ValueError, IndexError, TypeError):
            continue


def esegui(imap, richiesta, cartella_attuale):
    """Ritorna (oggetto_nota, testo_nota, allegati)."""
    parola = (richiesta.get("parola_chiave") or "").strip()
    tipi = [t.lower() for t in (richiesta.get("tipi") or [])]
    documenti = cerca(imap, parola, cartella_attuale)
    classifica(documenti)
    if tipi:
        pertinenti = [d for d in documenti if d.get("tipo", "altro") in tipi]
        altri = [d for d in documenti if d not in pertinenti]
    else:
        pertinenti, altri = documenti, []

    righe = [f"Ricerca: {richiesta.get('descrizione') or parola}",
             f"Parola cercata: \"{parola}\" nella posta arrivata e inviata degli ultimi {ANNI_RICERCA} anni.", ""]
    if not documenti:
        righe.append("Non ho trovato mail con allegati che la nominano. Prova con un'altra parola "
                     "(es. ragione sociale completa o partita IVA).")
    allegati, peso = [], 0
    for titolo, gruppo in (("DOCUMENTI RICHIESTI", pertinenti), ("ALTRI DOCUMENTI TROVATI", altri)):
        if not gruppo:
            continue
        righe += [f"{titolo} ({len(gruppo)}):", ""]
        for d in gruppo:
            dettagli = " · ".join(x for x in (d.get("tipo", ""), d.get("compagnia", ""), d.get("descrizione", ""),
                                              f"n. {d['numero']}" if d.get("numero") else "",
                                              f"scad. {d['scadenza']}" if d.get("scadenza") else "") if x)
            righe.append(f"- {d['nome']}")
            if dettagli:
                righe.append(f"  {dettagli}")
            righe.append(f"  mail {d['verso']} il {d['data']} - {d['contatto'][:60]} - \"{d['oggetto']}\"")
            if titolo.startswith("DOCUMENTI RICHIESTI") and peso + len(d["dati"]) <= MAX_ALLEGATI_BYTE:
                allegati.append((d["nome"], d["dati"]))
                peso += len(d["dati"])
        righe.append("")
    if pertinenti and len(allegati) < len(pertinenti):
        righe.append(f"Allegati alla nota: {len(allegati)} su {len(pertinenti)} (gli altri superano il limite di "
                     "dimensione: li trovi nelle mail indicate).")
    elif allegati:
        righe.append(f"Tutti i {len(allegati)} documenti richiesti sono allegati a questa nota.")
    righe += ["", "-- Braccio destro (nota interna, non inoltrare)"]
    oggetto = f"Ricerca {parola}: {len(pertinenti)} documenti"
    log.info("Ricerca eseguita: %d documenti trovati, %d pertinenti", len(documenti), len(pertinenti))
    return oggetto, "\n".join(righe), allegati
