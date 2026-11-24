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
