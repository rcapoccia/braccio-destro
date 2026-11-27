"""
Prova della pagina VERA (servizio systemd braccio-web, come sul server, stessi limiti di memoria), con dati finti.
Verifica: la pagina risponde, lo stato del sistema è sano (librerie, scatola), i documenti si aprono in fretta,
la memoria usata sta nei limiti del server da 4 GB.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_moduli  # noqa: E402

BASE = "http://127.0.0.1:8080"
TOKEN = os.environ["TOKEN_PROVA"]
ORIGINE = "https://braccio.stital.it"


def chiama(percorso, dati=None, tipo=None, attesa=60):
    h = {"Cookie": f"sessione={TOKEN}", "x-braccio": "1", "Origin": ORIGINE}
    if tipo:
        h["Content-Type"] = tipo
    req = urllib.request.Request(BASE + percorso, data=dati, headers=h, method="POST" if dati else "GET")
    t = time.time()
    with urllib.request.urlopen(req, timeout=attesa) as r:
        corpo = r.read()
    return corpo, time.time() - t


def multipart(campi, file):
    confine = "----vegaprova"
    pezzi = []
    for k, v in campi.items():
        pezzi.append(f'--{confine}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for nome, dati in file:
        pezzi.append(f'--{confine}\r\nContent-Disposition: form-data; name="file"; filename="{nome}"\r\n'
                     f'Content-Type: application/pdf\r\n\r\n'.encode() + dati + b"\r\n")
    pezzi.append(f"--{confine}--\r\n".encode())
    return b"".join(pezzi), f"multipart/form-data; boundary={confine}"


def memoria_mb():
    r = subprocess.run(["systemctl", "show", "braccio-web", "-p", "MemoryCurrent", "-p", "MemoryPeak"],
                       capture_output=True, text=True).stdout
    v = dict(x.split("=", 1) for x in r.split())
    return {k: int(v[k]) // (1024 * 1024) if v.get(k, "").isdigit() else None for k in v}


def main():
    errori = []
    for _ in range(90):   # la pagina parte e carica il riconoscitore dei dati personali
        try:
            chiama("/api/conversazioni", attesa=10)
            break
        except Exception:
            time.sleep(5)
    else:
        errori.append("la pagina non risponde")
    corpo, t = chiama("/api/sistema", attesa=60)
    s = json.loads(corpo)
    print("Stato del sistema:", s, f"({t:.1f}s)")
    for nome, ok in s["librerie"].items():
        if not ok:
            errori.append(f"libreria mancante: {nome}")
    if not s["scatola"]:
        errori.append("la pagina non riesce a usare la scatola chiusa")
    pdf = test_moduli.modulo_finto()
    corpo, _ = chiama("/api/chat", *multipart({"testo": "prova con dati finti", "conv": ""}, [("Modulo finto.pdf", pdf)]))
    conv = json.loads(corpo)["conv"]
    voci = json.loads(chiama(f"/api/chat?conv={conv}")[0])["voci"]
    fid = voci[0]["file"][0]["id"]
    corpo, t1 = chiama(f"/api/file/{fid}/info", attesa=20)
    info = json.loads(corpo)
    corpo, t2 = chiama(f"/api/file/{fid}/pagina/1", attesa=20)
    print(f"documento: {info} — info in {t1:.1f}s, pagina in {t2:.1f}s, {len(corpo) // 1024} KB")
    if not info.get("pdf") or info.get("pagine") != 1 or not corpo.startswith(b"\x89PNG"):
        errori.append("il visore dei documenti non funziona")
    if t1 > 5 or t2 > 10:
        errori.append(f"documento troppo lento da aprire ({t1:.1f}s / {t2:.1f}s)")
    time.sleep(20)
    m = memoria_mb()
    print("Memoria della pagina (MB):", m)
    print(f"::notice title=Memoria pagina::attuale {m.get('MemoryCurrent')} MB, picco {m.get('MemoryPeak')} MB (limite 2800, server 4 GB)")
    if (m.get("MemoryPeak") or 0) > 2600:
        errori.append(f"la pagina usa troppa memoria per il server da 4 GB: picco {m.get('MemoryPeak')} MB")
    print("\nESITO:", "OK" if not errori else "ERRORI:\n- " + "\n- ".join(errori))
    for e in errori:
        print(f"::error title=Pagina::{e}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
