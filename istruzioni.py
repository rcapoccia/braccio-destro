"""
Braccio destro - Istruzioni di Roberto

Roberto inoltra una mail a se stesso con l'oggetto che inizia per "AI:" e scrive in cima cosa fare.
L'agente esegue: prepara la bozza per il destinatario giusto, cerca nella posta i documenti richiesti,
compila i moduli mappati e li allega. Non invia mai nulla: lascia una bozza.
"""

import email.utils
import json
import logging
import re

import mistral
import pratiche
import questionari
import ricerca

log = logging.getLogger("posta")

PREFISSO = re.compile(r"(?i)^\s*(?:(?:i|r|re|fw|fwd|inoltro|rif)\s*:\s*)*ai\s*:\s*")


def e_istruzione(msg, indirizzo_roberto):
    mittente = email.utils.parseaddr(msg.get("From", ""))[1].lower()
    return mittente == indirizzo_roberto.lower() and bool(PREFISSO.match(msg.get("Subject", "") or ""))


SISTEMA = """Sei il braccio destro di Roberto Capoccia, broker assicurativo di Stile Italiano (Stital).
Roberto ti inoltra una mail e in cima scrive un'ISTRUZIONE (a volte dettata a voce, quindi con qualche errore di
trascrizione). Il tuo compito è eseguire ESATTAMENTE l'istruzione, preparando la mail che Roberto rivedrà prima di inviarla.

COME LAVORI:
1. Leggi l'istruzione e capisci cosa vuole Roberto: a chi scrivere, cosa dire, cosa allegare.
2. Leggi la mail inoltrata per il contesto: chi ha scritto, cosa ha chiesto, chi era in copia.
3. Destinatario: quello indicato da Roberto; se non lo indica, chi ha scritto la mail inoltrata.
   Usa SOLO indirizzi email che compaiono nella mail inoltrata o nell'istruzione. Mai inventarli.
   Mantieni in copia le persone che erano in copia, salvo diversa istruzione.
4. Testo: fai quello che chiede Roberto, senza aggiungere promesse, impegni o informazioni che lui non ha dato.
   Se per eseguire manca un dato, metti [DA COMPLETARE: cosa].
5. Allegati: se Roberto chiede di allegare documenti, elencali in "allegati". Tipi possibili:
   - "modulo_compilato": un questionario di una compagnia da compilare con i dati del questionario già compilato dal
     cliente e della visura (indica "compagnia", es. "ITAS", e "ramo", es. "CAR");
   - "documento": un documento da cercare nella posta (indica "descrizione" con le parole che lo identificano,
     es. "visura camerale", "questionario", "polizza", "libretto").
   Per ogni allegato indica "parola_chiave": la parola più distintiva dell'azienda o persona a cui si riferisce (es. "COMPASS").

STILE DI ROBERTO: informale ma professionale. "Ciao Nome," (o "Buongiorno Nome," se il contatto è formale).
Frasi brevi e dirette, elenchi con il trattino, nessun burocratese (vietati: "ho preso visione", "con la presente",
"resto a disposizione", "cordiali saluti"). Chiusura: "Grazie e ciao" o "A presto". Firma: "Roberto".
Se la mail parla di allegati, citali in modo naturale ("ti giro in allegato...").

AZIONE: di solito Roberto vuole una mail da mandare a qualcuno ("azione": "bozza").
Se invece chiede di CERCARE o RACCOGLIERE documenti per sé (es. "cerca le polizze e i preventivi di E-ALL",
"trovami la visura di Rossi"), usa "azione": "ricerca" e compila "ricerca":
{"parola_chiave": la parola più distintiva del cliente così come comparirebbe nelle mail (es. "E-ALL"),
 "tipi": tipi di documento richiesti tra "polizza", "preventivo", "quietanza", "appendice", "questionario", "visura",
 "sinistro", "contratto", "fattura" (lista vuota se vuole tutti i documenti), "descrizione": la richiesta in breve}.
In quel caso "a", "cc", "bozza", "allegati" restano vuoti.

Rispondi SOLO in JSON:
{"azione": "bozza" | "ricerca", "ricerca": {...} o null, "a": [indirizzi], "cc": [indirizzi], "oggetto": testo (di solito "R: " + oggetto originale),
 "bozza": testo della mail, "allegati": [{"tipo": ..., "compagnia": ..., "ramo": ..., "descrizione": ..., "parola_chiave": ...}],
 "dubbi": [cose che Roberto deve controllare]}"""


def _indirizzi_validi(lista, testo):
    validi = []
    for x in lista or []:
        a = email.utils.parseaddr(str(x))[1].strip().lower()
        if a and "@" in a and a in testo.lower() and a not in validi:
            validi.append(a)
    return validi


def esegui(imap, msg, corpo, cartella, indirizzo_roberto):
    """Ritorna (bozza_dati, allegati, nota) per l'istruzione di Roberto."""
    istruzione, inoltrata = pratiche_separa(corpo)
    oggetto = PREFISSO.sub("", msg.get("Subject", "") or "")
    utente = (f"OGGETTO: {msg.get('Subject', '')}\n\n=== ISTRUZIONE DI ROBERTO ===\n{istruzione[:3000]}\n\n"
              f"=== MAIL INOLTRATA ===\n{inoltrata[:14000] or '(nessuna)'}")
    piano = mistral.chat_json(SISTEMA, utente)
    r = piano.get("ricerca")
    if piano.get("azione") == "ricerca" and isinstance(r, dict) and r.get("parola_chiave"):
        return {"azione": "ricerca"}, ricerca.esegui(imap, r, cartella), None

    testo_completo = corpo + "\n" + msg.get("Subject", "")
    a = [x for x in _indirizzi_validi(piano.get("a"), testo_completo) if x != indirizzo_roberto.lower()]
    cc = [x for x in _indirizzi_validi(piano.get("cc"), testo_completo) if x != indirizzo_roberto.lower() and x not in a]
    problemi = list(piano.get("dubbi") or [])
    if not a:
        problemi.insert(0, "Non ho trovato con certezza l'indirizzo del destinatario: inseriscilo tu nella bozza.")

    allegati, documenti_usati = [], []
    for richiesta in piano.get("allegati") or []:
        if not isinstance(richiesta, dict):
            continue
        parola = (richiesta.get("parola_chiave") or "").strip()
        tipo = richiesta.get("tipo")
        if not parola:
            problemi.append(f"Allegato non chiaro, non l'ho cercato: {json.dumps(richiesta, ensure_ascii=False)}")
            continue
        if tipo == "modulo_compilato":
            nome_modulo = questionari.trova_modulo(richiesta.get("compagnia", ""), richiesta.get("ramo", ""))
            documenti = pratiche.cerca_documenti(imap, msg, parola, cartella)
            if not nome_modulo:
                problemi.append(f"Il modulo {richiesta.get('compagnia')} {richiesta.get('ramo')} non è ancora mappato: "
                                "chiedi a Claude di mapparlo. Intanto ho allegato i documenti trovati.")
                for k in ("questionario", "visura"):
                    if documenti[k]:
                        allegati.append((documenti[k]["nome"], documenti[k]["dati"]))
                        documenti_usati.append(f"{documenti[k]['nome']} ({documenti[k]['origine']})")
                continue
            if not documenti["questionario"]:
                problemi.append(f"Non ho trovato nella posta il questionario compilato dal cliente per '{parola}': "
                                "il modulo non è stato compilato.")
                continue
            risultato = pratiche.compila(msg, nome_modulo, documenti, inoltrata)
            allegati.append((risultato["nome_pdf"], risultato["pdf"]))
            fonti = [documenti["questionario"]] + ([documenti["visura"]] if documenti["visura"] else [])
            documenti_usati.append(f"{risultato['nome_pdf']} - compilato usando: " +
                                   "; ".join(f"{d['nome']} ({d['origine']})" for d in fonti))
            problemi += [f"Da verificare - {x}" for x in risultato["da_verificare"]]
            if risultato["troncati"]:
                problemi.append("Testo tagliato nel modulo: " + "; ".join(risultato["troncati"]))
            if risultato["mancanti"]:
                problemi.append(f"Modulo {richiesta.get('compagnia')} compilato {risultato['compilati']}/{risultato['totale']}. "
                                "Domande senza risposta nel questionario del cliente:\n  - " + "\n  - ".join(risultato["mancanti"]))
        else:
            descrizione = richiesta.get("descrizione") or ""
            doc = pratiche.cerca_documento(imap, msg, parola, descrizione, cartella)
            if doc:
                if all(doc["nome"] != n for n, _ in allegati):
                    allegati.append((doc["nome"], doc["dati"]))
                    documenti_usati.append(f"{doc['nome']} ({doc['origine']})")
            else:
                problemi.append(f"Non ho trovato nella posta: {descrizione} ({parola}).")

    bozza = {"a": a, "cc": cc, "oggetto": piano.get("oggetto") or ("R: " + oggetto),
             "bozza": piano.get("bozza", ""), "riferimento": _id_originale(inoltrata)}
    nota = None
    if problemi or documenti_usati:
        righe = [f"Istruzione: {istruzione.strip()[:300]}", ""]
        if documenti_usati:
            righe += ["Allegati messi nella bozza:"] + [f"- {d}" for d in documenti_usati] + [""]
        if problemi:
            righe += ["DA CONTROLLARE:"] + [f"- {p}" for p in problemi]
        righe += ["", "-- Braccio destro (nota interna, non inoltrare)"]
        nota = "\n".join(righe)
    return bozza, allegati, nota


def pratiche_separa(corpo):
    """Istruzione (in cima) e mail inoltrata (sotto)."""
    from posta import separa_storico   # import locale per evitare dipendenze circolari
    return separa_storico(corpo)


def _id_originale(testo):
    m = re.search(r"(?im)^message-id:\s*(<[^>]+>)", testo)
    return m.group(1) if m else None
