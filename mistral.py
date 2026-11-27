"""Chiamate a Mistral (endpoint UE, zero data retention): chat in JSON e OCR."""

import base64
import json
import os
import time
import urllib.error
import urllib.request

API_KEY = os.environ.get("MISTRAL_API_KEY", "")
BASE = os.environ.get("MISTRAL_BASE", "https://api.eu.mistral.ai/v1")
MODELLO = os.environ.get("MISTRAL_MODEL", "mistral-large-2512")
MODELLO_OCR = os.environ.get("MISTRAL_OCR_MODEL", "mistral-ocr-latest")


class TettoRaggiunto(Exception):
    """Tetto di spesa o limite di frequenza raggiunto (HTTP 429)."""


def _post(percorso, corpo, timeout=180):
    req = urllib.request.Request(
        BASE + percorso,
        data=json.dumps(corpo).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    for tentativo in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise TettoRaggiunto() from e
            if e.code in (500, 502, 503, 504) and tentativo < 2:
                time.sleep(10 * (tentativo + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if tentativo < 2:
                time.sleep(10 * (tentativo + 1))
                continue
            raise


def chat_json(sistema, utente, temperatura=0.2):
    risposta = _post("/chat/completions", {
        "model": MODELLO,
        "temperature": temperatura,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
    })
    return json.loads(risposta["choices"][0]["message"]["content"])


def ocr_pdf(dati_pdf):
    """Testo (markdown) di un PDF scansionato. Mistral OCR ha la zero data retention di default."""
    url = "data:application/pdf;base64," + base64.b64encode(dati_pdf).decode()
    risposta = _post("/ocr", {"model": MODELLO_OCR, "document": {"type": "document_url", "document_url": url}},
                     timeout=300)
    return "\n\n".join(p.get("markdown", "") for p in risposta.get("pages", []))


def ocr_pagine(dati_pdf):
    """Come ocr_pdf, ma una stringa per pagina (serve per citare la pagina)."""
    url = "data:application/pdf;base64," + base64.b64encode(dati_pdf).decode()
    risposta = _post("/ocr", {"model": MODELLO_OCR, "document": {"type": "document_url", "document_url": url}},
                     timeout=300)
    return [p.get("markdown", "") for p in risposta.get("pages", [])]


def ocr_immagine(dati, tipo="image/png"):
    """Testo di una foto o immagine (es. foto di un documento fatta col telefono)."""
    url = f"data:{tipo};base64," + base64.b64encode(dati).decode()
    risposta = _post("/ocr", {"model": MODELLO_OCR, "document": {"type": "image_url", "image_url": url}},
                     timeout=300)
    return "\n\n".join(p.get("markdown", "") for p in risposta.get("pages", []))


def cerca_web(domanda, modello=None):
    """Ricerca sul web con lo strumento web_search di Mistral (Conversations API, endpoint UE).
    La domanda deve essere generica: chi chiama non deve metterci dati dei clienti."""
    r = _post("/conversations", {"model": modello or os.environ.get("MISTRAL_WEB_MODEL", "mistral-medium-latest"),
                                 "inputs": [{"role": "user", "content": domanda}],
                                 "tools": [{"type": "web_search"}], "store": False}, timeout=180)
    testo, fonti = [], []
    for uscita in r.get("outputs", []):
        if uscita.get("type") != "message.output":
            continue
        contenuto = uscita.get("content")
        if isinstance(contenuto, str):
            testo.append(contenuto)
            continue
        for pezzo in contenuto or []:
            if pezzo.get("type") == "text":
                testo.append(pezzo.get("text", ""))
            elif pezzo.get("type") == "tool_reference":
                fonti.append({"titolo": pezzo.get("title", ""), "url": pezzo.get("url", "")})
    return {"risposta": "".join(testo).strip(), "fonti": fonti[:12]}
