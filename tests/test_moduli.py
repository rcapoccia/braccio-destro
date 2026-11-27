"""
Prova della compilazione di moduli PDF "piatti" (righe ____ e caselle) con SOLI DATI FINTI.
Uso:  python3 tests/test_moduli.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import moduli  # noqa: E402


def modulo_finto():
    from reportlab.pdfgen import canvas
    b = io.BytesIO()
    c = canvas.Canvas(b, pagesize=(595, 842))
    c.setFont("Helvetica", 11)
    c.drawString(50, 780, "Richiesta a favore di ______________________________")
    c.drawString(50, 755, "residente ____________________ Via ____________________")
    c.drawString(50, 730, "Tipo utente:")
    for i, et in enumerate(["adulti", "minori", "anziani"]):
        x = 130 + i * 90
        c.rect(x, 728, 8, 8)
        c.drawString(x + 12, 730, et)
    c.drawString(50, 705, "Allegati: 1) ______________ 2) ______________")
    c.save()
    return b.getvalue()


def main():
    errori = []
    pdf = modulo_finto()
    spazi = moduli.spazi(pdf)
    vista = moduli.vista(pdf, spazi)
    print(vista)
    righe = [s for s in spazi if s["tipo"] == "riga"]
    caselle = [s for s in spazi if s["tipo"] == "casella"]
    if len(righe) != 5:
        errori.append(f"righe trovate {len(righe)} invece di 5")
    if len(caselle) != 3:
        errori.append(f"caselle trovate {len(caselle)} invece di 3")
    minori = next((s for s in caselle if s["etichetta"].startswith("minori")), None)
    if not minori:
        errori.append("casella 'minori' senza etichetta giusta")
    favore = next((s for s in righe if "favore di" in s["etichetta"]), None)
    valori = {favore["id"]: "Mario Rossi (finto)"} if favore else {}
    if minori:
        valori[minori["id"]] = "X"
    compilato, usati, ignoti = moduli.scrivi(pdf, valori, spazi)
    if usati != len(valori) or ignoti:
        errori.append(f"scritti {usati}, sconosciuti {ignoti}")
    from pypdf import PdfReader
    testo = PdfReader(io.BytesIO(compilato)).pages[0].extract_text()
    if "Mario Rossi (finto)" not in testo or "Richiesta a favore di" not in testo:
        errori.append("il testo non è stato scritto sul modulo originale")
    png, n = moduli.anteprima(compilato, 1)
    if n != 1 or not png.startswith(b"\x89PNG"):
        errori.append("anteprima non riuscita")
    print("\nESITO:", "OK" if not errori else "ERRORI:\n- " + "\n- ".join(errori))
    return 1 if errori else 0


if __name__ == "__main__":
    sys.exit(main())
