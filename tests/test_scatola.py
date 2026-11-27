"""
Prova della scatola chiusa VERA (servizio systemd vega-scatola, come sul server). Si lancia come utente braccio.
Verifica che i programmi girino e che NON possano: usare la rete, leggere chiavi e dati del braccio destro,
scrivere fuori dalla loro cartella, durare all'infinito.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import scatola  # noqa: E402

SOCK = os.environ.get("SCATOLA_SOCKET", "/run/vega-scatola/socket")


def prova(codice, file=()):
    return scatola.chiedi(codice, list(file), socket_path=SOCK, attesa=200)


def main():
    errori = []
    r = prova("print(sum(range(10)))")
    if r["uscita"].strip() != "45":
        errori.append(f"il programma semplice non gira: {r}")
    r = prova("open('nuovo.txt','w').write('ciao'); print(open('dati.txt').read())", [("dati.txt", b"finto")])
    if r["uscita"].strip() != "finto" or not any(n == "nuovo.txt" for n, _ in r["file"]):
        errori.append(f"file in entrata/uscita non funzionano: {r['uscita']} {[n for n, _ in r['file']]}")
    vietati = {
        "rete": "import socket; s=socket.create_connection(('1.1.1.1',443),timeout=5); print('APERTO')",
        "chiavi": "print(open('/etc/braccio-destro/posta.env').read()); print('APERTO')",
        "dati braccio destro": "import os; print(os.listdir('/var/lib/braccio-destro')); print('APERTO')",
        "scrittura fuori": "open('/opt/braccio-destro/x','w').write('x'); print('APERTO')",
        "home": "import os; print(os.listdir('/root')); print('APERTO')",
    }
    for nome, codice in vietati.items():
        r = prova(codice)
        if "APERTO" in r["uscita"]:
            errori.append(f"la scatola NON blocca: {nome}")
        else:
            print(f"bloccato correttamente: {nome}")
    r = prova("while True: pass")
    if r["codice_uscita"] == 0:
        errori.append("un programma infinito non viene fermato")
    else:
        print("programma infinito fermato")
    for lib in ("pypdf", "pdfplumber", "reportlab", "pypdfium2", "openpyxl", "docx", "PIL", "pandas"):
        r = prova(f"import {lib}; print('ok')")
        if r["uscita"].strip() != "ok":
            errori.append(f"libreria {lib} non funziona nella scatola (uscita {r['codice_uscita']}): {r['errori'][-300:]}")
        else:
            print(f"libreria {lib} ok")
    print("\nESITO:", "OK" if not errori else "ERRORI:\n- " + "\n- ".join(errori))
    for e in errori:
        print(f"::error title=Scatola chiusa::{e}")
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
