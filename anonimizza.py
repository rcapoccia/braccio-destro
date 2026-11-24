"""
Braccio destro - Anonimizzatore locale (gira solo sul server, nessun dato esce)

Prima che un testo vada a servizi esterni senza contratto GDPR (Navisio, intermediari.ai, Grok) toglie:
nomi di persone e aziende clienti, codici fiscali, partite IVA, IBAN, email, telefoni, targhe, indirizzi,
documenti d'identità. Ogni dato diventa un segnaposto coerente ([PERSONA_1], [AZIENDA_1], ...).
La risposta che torna indietro si può "ripristinare" sul server con la mappa, che non lascia mai il server.

Usa Microsoft Presidio + spaCy (modello italiano), entrambi open source e locali.
"""

import re
import threading

# Nomi di compagnie, enti e termini di settore da NON togliere: servono alla domanda e non sono dati di clienti.
NON_TOGLIERE = {
    "ivass", "consap", "inail", "inps", "ania", "agenzia delle entrate", "camera di commercio", "registro imprese",
    "itas", "reale", "reale mutua", "generali", "allianz", "unipol", "unipolsai", "axa", "zurich", "cattolica",
    "groupama", "helvetia", "hdi", "sara", "vittoria", "nobis", "chubb", "aig", "lloyd's", "lloyds", "tokio marine",
    "mapfre", "arag", "das", "europ assistance", "coface", "atradius", "sace", "elba", "revo", "net insurance",
    "stital", "stile italiano", "navisio", "intermediari.ai", "mistral", "grok",
    "rc", "rct", "rco", "rca", "car", "d&o", "all risks", "polizza", "contraente", "assicurato", "codice civile",
}

ETICHETTE = {
    "PERSON": "PERSONA", "ORGANIZATION": "AZIENDA", "AZIENDA": "AZIENDA", "IT_FISCAL_CODE": "CODICE_FISCALE",
    "IT_VAT_CODE": "PARTITA_IVA", "IBAN_CODE": "IBAN", "EMAIL_ADDRESS": "EMAIL", "PHONE_NUMBER": "TELEFONO",
    "TELEFONO": "TELEFONO", "TARGA": "TARGA", "INDIRIZZO": "INDIRIZZO", "IT_DRIVER_LICENSE": "PATENTE",
    "IT_PASSPORT": "PASSAPORTO", "NUMERO_PRATICA": "NUMERO", "IT_IDENTITY_CARD": "DOCUMENTO", "CREDIT_CARD": "CARTA", "NOME_NOTO": "CLIENTE",
}

# Numeri di polizza, sinistro, pratica, contratto: si toglie il numero, la parola resta.
NUMERI_PRATICA = re.compile(r"(?i)\b(?:polizz[ae]|contratto|sinistro|pratica|certificato|appendice|quietanza)"
                            r"\s*(?:n\.?|nr\.?|num\.?|numero|n°)?\s*[:°]?\s*([A-Z0-9][A-Z0-9/.\-]{4,})")

_motore = None
_blocco = threading.Lock()


def _crea_motore():
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "it", "model_name": "it_core_news_md"}],
        "ner_model_configuration": {
            "model_to_presidio_entity_mapping": {"PER": "PERSON", "ORG": "ORGANIZATION"},
            "labels_to_ignore": ["LOC", "MISC"],
        },
    }).create_engine()
    registro = RecognizerRegistry(supported_languages=["it"])
    registro.load_predefined_recognizers(languages=["it"], nlp_engine=nlp)
    aggiunte = [
        ("AZIENDA", r"\b[A-Z0-9][A-Za-z0-9&.'\-]*(?:[ \t]+[A-Z0-9&][A-Za-z0-9&.'\-]*){0,5}[ \t]+"
                    r"(?i:S\.?R\.?L\.?S?|S\.?P\.?A\.?|S\.?N\.?C\.?|S\.?A\.?S\.?|SOC\.?\s*COOP\.?)(?=\W|$)", 0.85),
        ("TARGA", r"\b[A-HJ-NPR-Z]{2}\s?\d{3}\s?[A-HJ-NPR-Z]{2}\b", 0.75),
        ("TELEFONO", r"(?<!\d)(?:\+39[\s.]?)?(?:3\d{2}|0\d{1,3})[\s./-]?\d{3,4}[\s./-]?\d{3,4}(?!\d)", 0.7),
        ("INDIRIZZO", r"(?i)\b(?:via|viale|v\.le|piazza|p\.za|piazzale|corso|c\.so|largo|vicolo|strada|località|loc\.)"
                      r"\s+[A-ZÀ-Ü][\w'.À-ü\s]{1,40}?,?\s*\d+[a-zA-Z]?\b", 0.8),
    ]
    for entita, regex, punteggio in aggiunte:
        registro.add_recognizer(PatternRecognizer(supported_entity=entita, supported_language="it",
                                                  patterns=[Pattern(entita, regex, punteggio)],
                                                  global_regex_flags=re.DOTALL | re.MULTILINE))
    return AnalyzerEngine(nlp_engine=nlp, registry=registro, supported_languages=["it"])


def _motore_pronto():
    global _motore
    with _blocco:
        if _motore is None:
            _motore = _crea_motore()
    return _motore


def anonimizza(testo, nomi_noti=()):
    """Ritorna (testo_anonimo, mappa segnaposto->valore). nomi_noti: nomi di clienti da togliere sempre."""
    risultati = _motore_pronto().analyze(text=testo, language="it", score_threshold=0.5)
    trovati = [(r.start, r.end, r.entity_type) for r in risultati]
    for m in NUMERI_PRATICA.finditer(testo):
        trovati.append((m.start(1), m.end(1), "NUMERO_PRATICA"))
    for nome in nomi_noti:
        nome = (nome or "").strip()
        if len(nome) >= 3:
            for m in re.finditer(re.escape(nome), testo, flags=re.IGNORECASE):
                trovati.append((m.start(), m.end(), "NOME_NOTO"))
    # Se due riconoscimenti si sovrappongono vince quello più affidabile (codici e formati prima dei nomi).
    priorita = {"NOME_NOTO": 0, "NUMERO_PRATICA": 1, "IBAN_CODE": 1, "IT_FISCAL_CODE": 1, "IT_VAT_CODE": 1, "EMAIL_ADDRESS": 1,
                "CREDIT_CARD": 1, "TARGA": 2, "TELEFONO": 2, "PHONE_NUMBER": 2, "INDIRIZZO": 2, "AZIENDA": 3}
    trovati.sort(key=lambda x: (priorita.get(x[2], 5), -(x[1] - x[0])))
    scelti = []
    for inizio, termine, tipo in trovati:
        if all(termine <= a or inizio >= b for a, b, _ in scelti):
            scelti.append((inizio, termine, tipo))
    scelti.sort()
    mappa, inverso, contatori, pezzi, pos = {}, {}, {}, [], 0
    for inizio, termine, tipo in scelti:
        valore = testo[inizio:termine]
        if tipo == "PERSON":   # spaCy a volte include una preposizione finale ("Bianchi di")
            accorciato = re.sub(r"(?i)\s+(?:di|de|del|della|da|e)$", "", valore)
            termine -= len(valore) - len(accorciato)
            valore = accorciato
        if tipo in ("PERSON", "ORGANIZATION") and valore.strip().lower() in NON_TOGLIERE:
            continue
        if tipo == "ORGANIZATION" and any(p in valore.lower().split() for p in NON_TOGLIERE):
            continue
        chiave = valore.strip().lower()
        if chiave not in inverso:
            etichetta = ETICHETTE.get(tipo, tipo)
            contatori[etichetta] = contatori.get(etichetta, 0) + 1
            segnaposto = f"[{etichetta}_{contatori[etichetta]}]"
            inverso[chiave] = segnaposto
            mappa[segnaposto] = valore.strip()
        pezzi += [testo[pos:inizio], inverso[chiave]]
        pos = termine
    pezzi.append(testo[pos:])
    return "".join(pezzi), mappa


def ripristina(testo, mappa):
    """Rimette i dati veri nella risposta (solo sul server)."""
    for segnaposto, valore in sorted(mappa.items(), key=lambda x: -len(x[0])):
        testo = testo.replace(segnaposto, valore)
    return testo
