"""
Braccio destro - La "scatola chiusa" di Vega: esegue i piccoli programmi Python che Vega scrive per lavorare
qualsiasi file (PDF, Excel, Word, immagini, CSV...), come il code interpreter di ChatGPT e Claude.

Gira come servizio separato (vega-scatola), con un utente dedicato senza privilegi e l'isolamento di systemd:
niente rete, sistema in sola lettura, nessun accesso a posta, chiavi, schede o altre cartelle del server, memoria,
processi e tempo limitati. Ogni lavoro ha una cartella sua, che viene cancellata alla fine.

Protocollo (socket Unix, una richiesta per connessione): JSON {"codice": str, "file": [{"nome", "b64"}]}
seguito dalla chiusura in scrittura; risposta JSON {"uscita", "errori", "codice_uscita", "file": [{"nome","b64"}]}.
"""

import base64
import json
import os
import resource
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

SOCKET = os.environ.get("SCATOLA_SOCKET", "/run/vega-scatola/socket")
LAVORO = os.environ.get("SCATOLA_LAVORO", "/var/lib/vega-scatola")
PYTHON = os.environ.get("SCATOLA_PYTHON", sys.executable)
SECONDI = int(os.environ.get("SCATOLA_SECONDI", "90"))
MAX_RICHIESTA = 60 * 1024 * 1024
MAX_USCITA = 20000
MAX_FILE_USCITA = 25 * 1024 * 1024
_un_lavoro_alla_volta = threading.Semaphore(2)


def _limiti():
    """Limiti per il programma di Vega (oltre a quelli del servizio systemd)."""
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)        # memoria
    resource.setrlimit(resource.RLIMIT_CPU, (SECONDI, SECONDI))              # tempo di calcolo
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_USCITA,) * 2)        # file scritti
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))                      # niente fork a catena
    os.setsid()


def _nome_sicuro(nome):
    nome = os.path.basename(str(nome or "file")).replace("\x00", "")
    return nome[:150] or "file"


def esegui(richiesta):
    cartella = tempfile.mkdtemp(prefix="lavoro-", dir=LAVORO)
    try:
        entrata = os.path.join(cartella, "file")
        os.makedirs(entrata)
        prima = {}
        for f in richiesta.get("file", [])[:40]:
            p = os.path.join(entrata, _nome_sicuro(f["nome"]))
            with open(p, "wb") as h:
                h.write(base64.b64decode(f["b64"]))
            prima[os.path.basename(p)] = os.path.getmtime(p), os.path.getsize(p)
        programma = os.path.join(cartella, "programma.py")
        with open(programma, "w", encoding="utf-8") as h:
            h.write(str(richiesta.get("codice", "")))
        ambiente = {"PATH": "/usr/bin:/bin", "HOME": cartella, "TMPDIR": cartella, "MPLBACKEND": "Agg",
                    "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1"}
        try:
            r = subprocess.run([PYTHON, "-I", programma], cwd=entrata, env=ambiente, capture_output=True,
                               timeout=SECONDI + 5, preexec_fn=_limiti)
            uscita, errori, codice = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            uscita, errori, codice = e.stdout or b"", (e.stderr or b"") + b"\n[tempo scaduto]", -9
        nuovi = []
        for nome in sorted(os.listdir(entrata)):
            p = os.path.join(entrata, nome)
            if not os.path.isfile(p) or os.path.islink(p):
                continue
            stato = os.path.getmtime(p), os.path.getsize(p)
            if prima.get(nome) == stato or stato[1] > MAX_FILE_USCITA:
                continue
            with open(p, "rb") as h:
                nuovi.append({"nome": nome, "b64": base64.b64encode(h.read()).decode()})
        return {"uscita": uscita.decode("utf-8", "replace")[-MAX_USCITA:],
                "errori": errori.decode("utf-8", "replace")[-MAX_USCITA:],
                "codice_uscita": codice, "file": nuovi[:20]}
    finally:
        shutil.rmtree(cartella, ignore_errors=True)


def _servi(conn):
    with conn:
        try:
            dati = b""
            while True:
                pezzo = conn.recv(1 << 20)
                if not pezzo:
                    break
                dati += pezzo
                if len(dati) > MAX_RICHIESTA:
                    raise ValueError("richiesta troppo grande")
            with _un_lavoro_alla_volta:
                risposta = esegui(json.loads(dati))
        except Exception as e:
            risposta = {"uscita": "", "errori": f"scatola: {type(e).__name__}", "codice_uscita": -1, "file": []}
        conn.sendall(json.dumps(risposta).encode())


def main():
    os.makedirs(LAVORO, exist_ok=True)
    if os.path.exists(SOCKET):
        os.remove(SOCKET)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET)
    os.chmod(SOCKET, 0o660)          # solo il gruppo della pagina di Vega può usarla
    s.listen(4)
    while True:
        conn, _ = s.accept()
        threading.Thread(target=_servi, args=(conn,), daemon=True).start()


def chiedi(codice, file, socket_path=SOCKET, attesa=SECONDI + 30):
    """Usata da Vega: manda il programma e i file alla scatola, ritorna la risposta."""
    richiesta = json.dumps({"codice": codice, "file": [{"nome": n, "b64": base64.b64encode(d).decode()}
                                                       for n, d in file]}).encode()
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(attesa)
    try:
        c.connect(socket_path)
        c.sendall(richiesta)
        c.shutdown(socket.SHUT_WR)
        dati = b""
        while True:
            pezzo = c.recv(1 << 20)
            if not pezzo:
                break
            dati += pezzo
    finally:
        c.close()
    r = json.loads(dati)
    r["file"] = [(f["nome"], base64.b64decode(f["b64"])) for f in r.get("file", [])]
    return r


if __name__ == "__main__":
    main()
