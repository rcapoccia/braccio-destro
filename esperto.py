"""
Braccio destro - Esperto assicurativo con ciclo di revisione

Stesso principio dei modelli "loop" (GLM-5.4): il lavoro ripassa più volte prima di uscire.
1. Ricerca nella biblioteca (CAP, IVASS, Codice civile, materiali di Roberto) i brani pertinenti.
2. Mistral (UE) scrive la risposta usando SOLO quei brani, citandoli [1], [2]...
3. Un revisore indipendente controlla: affermazioni senza fonte, errori, punti mancanti.
   - se è collegato Scaleway (Parigi, nessun dato conservato): un modello diverso (es. GLM), sul testo anonimizzato;
   - altrimenti Mistral stesso con un ruolo da revisore severo.
4. Se il revisore trova problemi, Mistral corregge. Massimo 2 giri.
"""

import json
import os
import urllib.error
import urllib.request

import anonimizza
import biblioteca
import mistral

SCALEWAY_URL = os.environ.get("SCALEWAY_URL", "https://api.scaleway.ai/v1")
PREFERITI = ["glm-5.2", "qwen3.5-397b-a17b", "deepseek-v4-flash", "llama-3.3-70b-instruct",
             "mistral-small-3.2-24b-instruct-2506"]
FILE_CHIAVI = os.path.join(os.environ.get("WEB_DATI", "/var/lib/braccio-destro/web"), "chiavi.json")
MAX_GIRI = 2

SISTEMA_RISPOSTA = """Sei il consulente tecnico di Roberto Capoccia, broker assicurativo (Stile Italiano), anche risk manager
e perito di parte. Rispondi in italiano, in modo pratico e diretto, come a un collega esperto.
REGOLE:
- Basati SOLO sulle FONTI numerate fornite. Ogni affermazione normativa deve avere il riferimento [n].
- Cita articolo e fonte quando ci sono (es. "art. 1917 c.c. [2]").
- Se le fonti non bastano per rispondere, dillo chiaramente e indica cosa manca; non inventare.
- Se la domanda riguarda una polizza specifica e non hai le condizioni, dillo.
Rispondi in JSON: {"risposta": "testo con riferimenti [n]", "fonti_usate": [numeri]}"""

SISTEMA_REVISORE = """Sei un revisore severo e indipendente (esperto di diritto assicurativo italiano).
Ricevi una DOMANDA, le FONTI numerate e una RISPOSTA scritta da un altro modello.
Controlla:
1. affermazioni non supportate dalle fonti o con riferimento [n] sbagliato;
2. errori giuridici o di interpretazione;
3. punti importanti per la domanda che la risposta non tratta;
4. promesse o certezze eccessive.
I segnaposto tra parentesi quadre (es. [AZIENDA_1]) sostituiscono dati riservati: lasciali così.
Rispondi SOLO in JSON: {"ok": true/false, "errori": ["..."], "senza_fonte": ["..."], "mancanze": ["..."]}
"ok" è true solo se non c'è nulla di rilevante da correggere."""

SISTEMA_CORREZIONE = SISTEMA_RISPOSTA + """
Ti viene data anche una REVISIONE della tua risposta precedente: correggi tutti i punti fondati,
ignora quelli non supportati dalle fonti. Aggiungi in fondo, se servono, i punti ancora da verificare."""


def chiave_scaleway():
    try:
        with open(FILE_CHIAVI) as f:
            return json.load(f).get("scaleway") or os.environ.get("SCALEWAY_API_KEY")
    except (FileNotFoundError, ValueError):
        return os.environ.get("SCALEWAY_API_KEY")


def _http(url, chiave, corpo=None):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode() if corpo else None,
                                 headers={"Authorization": f"Bearer {chiave}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


_modello_scelto = {}


def _modello_scaleway(chiave):
    if chiave not in _modello_scelto:
        disponibili = {m["id"] for m in _http(f"{SCALEWAY_URL}/models", chiave).get("data", [])}
        _modello_scelto[chiave] = next((m for m in PREFERITI if m in disponibili), None) or sorted(disponibili)[0]
    return _modello_scelto[chiave]


def _revisione(domanda, fonti, risposta):
    """Ritorna (revisione, nome_revisore, testo_inviato_fuori_o_None)."""
    chiave = chiave_scaleway()
    testo = f"DOMANDA:\n{domanda}\n\nFONTI:\n{fonti}\n\nRISPOSTA DA CONTROLLARE:\n{risposta}"
    if chiave:
        try:
            anonimo, mappa = anonimizza.anonimizza(testo)
            modello = _modello_scaleway(chiave)
            r = _http(f"{SCALEWAY_URL}/chat/completions", chiave, {
                "model": modello, "temperature": 0.1, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SISTEMA_REVISORE}, {"role": "user", "content": anonimo}]})
            contenuto = r["choices"][0]["message"]["content"]
            contenuto = contenuto[contenuto.find("{"):contenuto.rfind("}") + 1]
            revisione = json.loads(anonimizza.ripristina(contenuto, mappa))
            return revisione, f"{modello} (Scaleway, Parigi)", anonimo
        except (urllib.error.URLError, ValueError, KeyError, IndexError):
            pass   # se il revisore europeo non risponde, il controllo lo fa Mistral
    return mistral.chat_json(SISTEMA_REVISORE, testo, temperatura=0.1), "Mistral (revisore)", None


def chiedi(domanda, contesto=""):
    passaggi = biblioteca.cerca(f"{domanda}\n{contesto[:1500]}", k=8)
    fonti = "\n\n".join(f"[{i}] {p['fonte']} ({p['edizione']}), pag. {p['pagina']}"
                        f"{', ' + p['articolo'] if p['articolo'] else ''}:\n{p['testo']}"
                        for i, p in enumerate(passaggi, start=1)) or "(biblioteca vuota)"
    richiesta = f"DOMANDA:\n{domanda}\n\n" + (f"CONTESTO (mail o documento):\n{contesto[:6000]}\n\n" if contesto else "") + \
                f"FONTI:\n{fonti}"
    risposta = mistral.chat_json(SISTEMA_RISPOSTA, richiesta)
    storia, inviato, revisore = [], None, ""
    for giro in range(1, MAX_GIRI + 1):
        rev, revisore, testo_inviato = _revisione(domanda, fonti, risposta.get("risposta", ""))
        inviato = inviato or testo_inviato
        problemi = [*(rev.get("errori") or []), *(rev.get("senza_fonte") or []), *(rev.get("mancanze") or [])]
        storia.append({"giro": giro, "ok": bool(rev.get("ok")) or not problemi, "problemi": problemi})
        if rev.get("ok") or not problemi:
            break
        risposta = mistral.chat_json(SISTEMA_CORREZIONE, richiesta + "\n\nRISPOSTA PRECEDENTE:\n" +
                                     risposta.get("risposta", "") + "\n\nREVISIONE:\n" + json.dumps(rev, ensure_ascii=False))
    usate = {int(n) for n in risposta.get("fonti_usate") or [] if str(n).isdigit()}
    return {"tipo": "parere", "risposta": risposta.get("risposta", "").strip(),
            "fonti": [{"n": i, "fonte": p["fonte"], "pagina": p["pagina"], "articolo": p["articolo"],
                       "estratto": p["testo"][:400]} for i, p in enumerate(passaggi, 1) if i in usate or not usate],
            "revisore": revisore, "giri": storia, "inviato_al_revisore": inviato,
            "nota": None if passaggi else "La biblioteca è ancora vuota: la risposta non ha fonti. "
                                          "Caricala dalla sezione Biblioteca."}
