# Braccio destro – agente posta

Agente per Stile Italiano: legge le nuove mail via IMAP, le classifica
(clienti, compagnie, sinistri, scadenze, altro) con Mistral (endpoint UE,
zero data retention) e salva una bozza di risposta nella cartella Bozze.

- Non invia email: non usa SMTP.
- Legge senza segnare le mail come lette.
- Non salva il contenuto delle mail: tiene solo il numero dell'ultima mail letta.
- Nessuna password è contenuta in questo archivio: si inseriscono sul server durante l'installazione.

## Installazione (Ubuntu 24.04, come root)

```
bash <(curl -fsSL https://raw.githubusercontent.com/rcapoccia/braccio-destro/main/install.sh)
```
