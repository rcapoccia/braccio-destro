"""
Braccio destro - Memoria di Vega (il "quaderno")

Appunti scritti che Vega rilegge prima di lavorare e aggiorna dopo: una nota per Roberto (come vuole le cose) e
una per ogni cliente o argomento. Stesso principio della memoria di ChatGPT e di Claude: testo leggibile, che
Roberto può vedere, correggere e cancellare dalla pagina (sezione Memoria).

Dati dei clienti: le note restano solo sul server (cartella con permessi ristretti). Verso il modello escono
solo dentro la richiesta, coperte dal velo come tutto il resto. Cancellare una nota la toglie davvero
(diritto all'oblio): non esistono copie di riserva fatte da Vega.
"""

import os
import re
import threading
import time
import unicodedata

CARTELLA = os.environ.get("MEMORIA_DATI", "/var/lib/braccio-destro/memoria")
NOTA_ROBERTO = "roberto"
MAX_NOTA = 12000           # caratteri per nota: oltre, Vega deve riassumere
_blocco = threading.Lock()


def _slug(nome):
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:80]
    if not s:
        raise ValueError("nome della nota non valido")
    return s


def _percorso(nome):
    os.makedirs(CARTELLA, mode=0o700, exist_ok=True)
    return os.path.join(CARTELLA, _slug(nome) + ".md")


def leggi(nome):
    """Testo della nota, oppure "" se non esiste."""
    try:
        with open(_percorso(nome), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def titolo(testo, riserva):
    prima = (testo or "").lstrip().split("\n", 1)[0]
    return prima[2:].strip() if prima.startswith("# ") else riserva


def elenco():
    """Tutte le note: nome (slug), titolo, ultima modifica, lunghezza."""
    os.makedirs(CARTELLA, mode=0o700, exist_ok=True)
    out = []
    for f in os.listdir(CARTELLA):
        if not f.endswith(".md"):
            continue
        p = os.path.join(CARTELLA, f)
        with open(p, encoding="utf-8") as h:
            testo = h.read()
        out.append({"nome": f[:-3], "titolo": titolo(testo, f[:-3]), "modificata": os.path.getmtime(p),
                    "caratteri": len(testo)})
    out.sort(key=lambda x: (x["nome"] != NOTA_ROBERTO, -x["modificata"]))
    return out


def scrivi(nome, testo):
    """Sostituisce la nota (usato dalla pagina quando Roberto la corregge)."""
    testo = (testo or "").strip()
    if len(testo) > MAX_NOTA * 2:
        raise ValueError("nota troppo lunga")
    with _blocco:
        p = _percorso(nome)
        if not testo:
            if os.path.exists(p):
                os.remove(p)
            return
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(testo + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)


def aggiungi(nome, fatto, titolo_nuova=None):
    """Aggiunge un fatto datato alla nota (la crea se manca). Ritorna la lunghezza della nota."""
    fatto = re.sub(r"\s+", " ", fatto or "").strip()
    if not fatto:
        raise ValueError("fatto vuoto")
    with _blocco:
        p = _percorso(nome)
        testo = leggi(nome) if os.path.exists(p) else f"# {titolo_nuova or nome}\n"
        if fatto.lower() in testo.lower():
            return len(testo)
        testo = testo.rstrip("\n") + f"\n- {time.strftime('%d/%m/%Y')}: {fatto}\n"
    scrivi(nome, testo)
    return len(testo)


def elimina(nome):
    scrivi(nome, "")


def cerca(testo, massimo=8):
    """Note che contengono le parole cercate (tutte), con un estratto."""
    parole = [w for w in re.findall(r"\w+", (testo or "").lower()) if len(w) > 2]
    out = []
    for n in elenco():
        contenuto = leggi(n["nome"])
        basso = contenuto.lower()
        if parole and all(w in basso for w in parole):
            i = basso.find(parole[0])
            out.append({"nome": n["nome"], "titolo": n["titolo"], "estratto": contenuto[max(0, i - 200):i + 600]})
    return out[:massimo]
