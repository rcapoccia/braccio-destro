# Braccio destro – agente posta

Agente per Stile Italiano. Lavora **su istruzione di Roberto**:

1. Roberto inoltra una mail a se stesso con l'oggetto che inizia per **"AI:"** e scrive in cima cosa fare
   (es. "AI: rispondi allegando il questionario ITAS compilato e la visura dell'azienda").
2. Entro 5 minuti l'agente prepara la **bozza** (destinatari, testo nello stile di Roberto, allegati) nella cartella Bozze.
   Cerca i documenti nella posta e compila i moduli delle compagnie già mappati (`moduli/`).
3. Se c'è qualcosa da controllare, lascia una **nota interna** "[Braccio destro]" nella posta in arrivo.

- Non invia email: non usa SMTP. Ogni bozza la rivede e la invia Roberto.
- Le altre mail non vengono lette dall'AI.
- Mistral (endpoint UE, zero data retention). Nessun documento salvato sul server.
- Nessuna password in questo archivio: si inseriscono sul server durante l'installazione.

Installazione (Ubuntu 24.04, root): `bash <(curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/main/install.sh)`

Aggiornamento: `bash <(curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/main/aggiorna.sh)`

## Pagina web (braccio.stital.it)

- `web/`: pagina privata con accesso solo tramite passkey (Face ID / impronta), chat per le istruzioni,
  bozze da approvare, posta recente. Gira sul server dietro Caddy (HTTPS automatico).
- Una mail parte solo quando si tocca "Approva e invia" su una bozza; nessun invio automatico.
- `installa-web.sh`: installazione una tantum; `braccio-codice`: codice monouso per attivare un nuovo dispositivo.
- Aggiornamento automatico dal ramo `produzione` ogni 2 minuti (`auto-aggiorna.sh`, elenco in `file-elenco.txt`).

## Collegamenti (Navisio, intermediari.ai)

- Sezione "Collegamenti" della pagina: apre un browser sul server (schermo virtuale Xvfb + noVNC), visibile solo dopo
  l'accesso con Face ID (Caddy `forward_auth`). Il login lo fa Roberto; il salvataggio delle password è disattivato,
  restano solo i cookie di sessione in `/var/lib/braccio-destro/browser/`.
- `sistema.sh`: configurazione di sistema, rilanciata dall'aggiornamento automatico quando cambia.
