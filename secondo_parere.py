"""
Braccio destro - Secondo parere

La domanda di Roberto va:
- a Mistral (UE, zero data retention) così com'è;
- a Grok (xAI) SOLO dopo l'anonimizzazione locale, e SOLO se la ZDR di xAI risulta attiva
  (lo verifica a ogni risposta con l'intestazione x-zero-data-retention: se non è "true" scarta la risposta).
Poi Mistral confronta i due pareri e segnala dove non concordano.
La mappa per rimettere i nomi resta sul server.
"""

import json
import os
import urllib.error
import urllib.request

import anonimizza
import mistral

GROK_URL = os.environ.get("GROK_URL", "https://api.x.ai/v1/chat/completions")
GROK_MODELLO = os.environ.get("GROK_MODEL", "grok-4.7")
FILE_CHIAVI = os.path.join(os.environ.get("WEB_DATI", "/var/lib/braccio-destro/web"), "chiavi.json")

SISTEMA_ESPERTO = """Sei un esperto di assicurazioni italiane (normativa IVASS, Codice delle Assicurazioni Private,
Codice Civile, condizioni di polizza, sinistri, risk management). Rispondi in italiano, in modo chiaro e pratico,
a un broker assicurativo esperto. Cita articoli e norme quando li usi. Se non sei sicuro, dillo.
I segnaposto tra parentesi quadre (es. [AZIENDA_1]) sostituiscono dati riservati: usali così come sono."""

SISTEMA_CONFRONTO = """Confronta due pareri sulla stessa domanda assicurativa (A = Mistral, B = Grok).
Rispondi SOLO in JSON: {"concordano": true/false, "sintesi": "risposta finale in 3-6 frasi",
"differenze": ["punti in cui non concordano o in cui uno dei due sbaglia, con il motivo"],
"da_verificare": ["affermazioni da controllare su fonti ufficiali"]}"""


def chiave_grok():
    try:
        with open(FILE_CHIAVI) as f:
            return json.load(f).get("grok") or os.environ.get("GROK_API_KEY")
    except (FileNotFoundError, ValueError):
        return os.environ.get("GROK_API_KEY")


class ZdrNonAttiva(Exception):
    """xAI non ha confermato la zero data retention: la risposta viene scartata."""


def _grok(testo_anonimo, chiave):
    corpo = {"model": GROK_MODELLO, "temperature": 0.2,
             "messages": [{"role": "system", "content": SISTEMA_ESPERTO}, {"role": "user", "content": testo_anonimo}]}
    req = urllib.request.Request(GROK_URL, data=json.dumps(corpo).encode(),
                                 headers={"Authorization": f"Bearer {chiave}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        if (r.headers.get("x-zero-data-retention") or "").lower() != "true":
            raise ZdrNonAttiva()
        return json.load(r)["choices"][0]["message"]["content"]


def esegui(domanda, contesto="", nomi_noti=()):
    """Ritorna un dizionario da mostrare nella chat."""
    testo = domanda + (f"\n\nCONTESTO:\n{contesto[:8000]}" if contesto else "")
    parere_a = mistral.chat_json(SISTEMA_ESPERTO + '\nRispondi in JSON: {"risposta": "..."}', testo).get("risposta", "")
    esito = {"tipo": "parere", "mistral": parere_a.strip()}

    chiave = chiave_grok()
    if not chiave:
        esito["nota"] = "Grok non è ancora collegato: inserisci la chiave nella sezione Collegamenti."
        return esito
    testo_anonimo, mappa = anonimizza.anonimizza(testo, nomi_noti)
    esito["inviato_a_grok"] = testo_anonimo
    try:
        parere_b = anonimizza.ripristina(_grok(testo_anonimo, chiave), mappa)
    except ZdrNonAttiva:
        esito["nota"] = ("Risposta di Grok scartata: xAI non conferma la zero data retention. Attivala nel pannello "
                         "xAI (Team Settings > Zero Data Retention).")
        return esito
    except urllib.error.HTTPError as e:
        esito["nota"] = f"Grok non ha risposto (errore {e.code}): controlla la chiave o il credito su console.x.ai."
        return esito
    esito["grok"] = parere_b.strip()
    confronto = mistral.chat_json(SISTEMA_CONFRONTO,
                                  f"DOMANDA:\n{testo}\n\nPARERE A (Mistral):\n{parere_a}\n\nPARERE B (Grok):\n{parere_b}",
                                  temperatura=0.1)
    esito.update({"concordano": bool(confronto.get("concordano")), "sintesi": confronto.get("sintesi", ""),
                  "differenze": confronto.get("differenze") or [], "da_verificare": confronto.get("da_verificare") or []})
    return esito
