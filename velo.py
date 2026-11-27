"""
Braccio destro - Velo (pseudonimizzazione per conversazione)

Tutto ciò che esce dal server verso il modello passa dal velo: nomi di persone e aziende, codici fiscali,
partite IVA, IBAN, email, telefoni, targhe, indirizzi e numeri di polizza diventano segnaposto coerenti
([PERSONA_1], [AZIENDA_2], [POLIZZA_1]...). La tabella per rimettere i dati veri resta in memoria sul server,
solo per la durata del lavoro, e non viene mai inviata.

Riconoscimento: modello GLiNER multilingua per dati personali (urchade/gliner_multi_pii-v1, Apache-2.0, gira
sul server) + regole fisse per i codici (codice fiscale, P.IVA, IBAN, email, telefono, targa).
Se GLiNER non è disponibile si usa il riconoscitore di riserva (Presidio + spaCy, anonimizza.py).
"""

import logging
import os
import re
import threading

log = logging.getLogger("velo")
MODELLO_GLINER = os.environ.get("VELO_MODELLO", "urchade/gliner_multi_pii-v1")
SOGLIA = float(os.environ.get("VELO_SOGLIA", "0.4"))
_modello = {"m": None, "ok": None}
_blocco = threading.Lock()

ETICHETTE = {"persona": "PERSONA", "azienda": "AZIENDA", "indirizzo": "INDIRIZZO", "numero di telefono": "TELEFONO",
             "email": "EMAIL", "targa": "TARGA", "codice fiscale": "CF", "partita iva": "PIVA", "iban": "IBAN",
             "numero di polizza": "POLIZZA", "data di nascita": "DATA_NASCITA"}

# Compagnie, enti e parole di mestiere: servono al ragionamento e non sono dati dei clienti.
NON_TOGLIERE = {
    "roberto", "roberto capoccia", "stital", "stile italiano", "vega", "braccio destro",
    "ivass", "consap", "inail", "inps", "ania", "agenzia delle entrate", "camera di commercio", "registro imprese",
    "itas", "itas mutua", "reale", "reale mutua", "generali", "generali italia", "allianz", "unipol", "unipolsai",
    "axa", "zurich", "cattolica", "groupama", "helvetia", "hdi", "sara", "vittoria", "nobis", "chubb", "aig",
    "lloyd's", "lloyds", "tokio marine", "mapfre", "arag", "das", "europ assistance", "coface", "atradius", "sace",
    "elba", "revo", "net insurance", "tutela legale", "prima assicurazioni", "navisio", "mistral",
}

REGOLE = [
    ("CF", re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.I)),
    ("IBAN", re.compile(r"\bIT\d{2}[A-Z]\d{10}[0-9A-Z]{12}\b", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("PIVA", re.compile(r"\b(?:IT)?\d{11}\b")),
    ("TELEFONO", re.compile(r"(?<![\w/])(?:\+39[\s.]?)?(?:3\d{2}|0\d{1,3})[\s./-]?\d{3,4}[\s./-]?\d{3,4}(?![\w/])")),
    ("TARGA", re.compile(r"\b[A-HJ-NPR-Z]{2}\s?\d{3}\s?[A-HJ-NPR-Z]{2}\b")),
]


def _gliner():
    with _blocco:
        if _modello["ok"] is None:
            try:
                from gliner import GLiNER
                _modello["m"] = GLiNER.from_pretrained(MODELLO_GLINER)
                _modello["ok"] = True
            except Exception as e:
                log.warning("GLiNER non disponibile (%s): uso il riconoscitore di riserva.", type(e).__name__)
                _modello["ok"] = False
    return _modello["m"] if _modello["ok"] else None


def _pezzi(testo, lunghezza=1500):
    """GLiNER lavora su testi brevi: spezza su fine riga mantenendo le posizioni."""
    inizio = 0
    while inizio < len(testo):
        fine = min(len(testo), inizio + lunghezza)
        if fine < len(testo):
            a_capo = testo.rfind("\n", inizio + lunghezza // 2, fine)
            fine = a_capo + 1 if a_capo > 0 else fine
        yield inizio, testo[inizio:fine]
        inizio = fine


def _plausibile(tipo, valore):
    """Scarta gli abbagli del modello (es. la parola «mail» presa per un indirizzo email)."""
    if tipo == "EMAIL":
        return "@" in valore
    if tipo in ("TELEFONO", "CF", "PIVA", "IBAN", "TARGA", "POLIZZA", "DATA_NASCITA"):
        return any(c.isdigit() for c in valore)
    return True


SEGNAPOSTO = re.compile(r"\[[A-Z_]+_\d+\]")


def trova(testo, nomi_noti=()):
    """Elenco (inizio, fine, tipo) dei dati da coprire."""
    trovati = []
    modello = _gliner()
    if modello is not None:
        for base, pezzo in _pezzi(testo):
            for e in modello.predict_entities(pezzo, list(ETICHETTE), threshold=SOGLIA):
                tipo = ETICHETTE[e["label"]]
                if _plausibile(tipo, pezzo[e["start"]:e["end"]]):
                    trovati.append((base + e["start"], base + e["end"], tipo))
    else:
        import anonimizza
        risultati = anonimizza._motore_pronto().analyze(text=testo, language="it", score_threshold=0.5)
        conv = {"PERSON": "PERSONA", "ORGANIZATION": "AZIENDA", "AZIENDA": "AZIENDA", "INDIRIZZO": "INDIRIZZO"}
        trovati += [(r.start, r.end, conv[r.entity_type]) for r in risultati if r.entity_type in conv]
        trovati += [(m.start(1), m.end(1), "POLIZZA") for m in anonimizza.NUMERI_PRATICA.finditer(testo)]
    for tipo, regola in REGOLE:
        trovati += [(m.start(), m.end(), tipo) for m in regola.finditer(testo)]
    for nome in nomi_noti:
        if nome and len(nome) >= 3:
            trovati += [(m.start(), m.end(), "AZIENDA") for m in re.finditer(re.escape(nome), testo, re.I)]
    # se due pezzi si sovrappongono vince il più lungo
    trovati.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    scelti, fine = [], -1
    for a, b, t in trovati:
        valore = testo[a:b].strip(" ,.;:")
        if a >= fine and len(valore) >= 2 and valore.lower() not in NON_TOGLIERE:
            scelti.append((a, b, t))
            fine = b
    return scelti


class Velo:
    """Una tabella di segnaposto per un intero lavoro: lo stesso dato ha sempre lo stesso segnaposto."""

    def __init__(self):
        self.da_valore = {}
        self.da_segnaposto = {}
        self.contatori = {}

    def _segnaposto(self, valore, tipo):
        chiave = re.sub(r"\s+", " ", valore.strip()).lower()
        if chiave not in self.da_valore:
            self.contatori[tipo] = self.contatori.get(tipo, 0) + 1
            s = f"[{tipo}_{self.contatori[tipo]}]"
            self.da_valore[chiave] = s
            self.da_segnaposto[s] = valore.strip()
        return self.da_valore[chiave]

    def copri(self, testo, nomi_noti=()):
        if not testo:
            return testo
        pezzi, pos = [], 0
        for a, b, tipo in trova(testo, nomi_noti):
            pezzi += [testo[pos:a], self._segnaposto(testo[a:b], tipo)]
            pos = b
        pezzi.append(testo[pos:])
        coperto = "".join(pezzi)
        # anche i valori già noti in questa conversazione, se il riconoscitore stavolta li ha mancati:
        # parole intere, in un solo passaggio, senza toccare i segnaposto già messi
        chiavi = sorted((k for k in self.da_valore if len(k) >= 4), key=len, reverse=True)
        if not chiavi:
            return coperto
        regola = re.compile(r"(?<!\w)(" + "|".join(re.escape(k).replace("\\ ", r"\s+") for k in chiavi) + r")(?!\w)", re.I)
        sostituisci = lambda m: self.da_valore.get(re.sub(r"\s+", " ", m.group(0)).lower(), m.group(0))
        parti = SEGNAPOSTO.split(coperto)
        segni = SEGNAPOSTO.findall(coperto)
        out = [regola.sub(sostituisci, parti[0])]
        for segno, parte in zip(segni, parti[1:]):
            out += [segno, regola.sub(sostituisci, parte)]
        return "".join(out)

    def scopri(self, testo):
        if not isinstance(testo, str) or "[" not in testo:
            return testo
        return re.sub(r"\[[A-Z_]+_\d+\]", lambda m: self.da_segnaposto.get(m.group(0), m.group(0)), testo)

    def scopri_tutto(self, valore):
        """Ripristina anche dentro dizionari e liste (argomenti degli attrezzi)."""
        if isinstance(valore, dict):
            return {k: self.scopri_tutto(v) for k, v in valore.items()}
        if isinstance(valore, list):
            return [self.scopri_tutto(v) for v in valore]
        return self.scopri(valore)
