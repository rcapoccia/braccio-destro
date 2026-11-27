# Vega — specifica (da approvare da Roberto)

## Cosa fa
Vega è l'assistente operativa di Roberto. Riceve una richiesta qualsiasi in chat (testo e file allegati)
e decide da sola i passi, usando i suoi attrezzi, finché il lavoro è fatto. Non ci sono percorsi fissi per tipo
di richiesta.

## Per chi
Solo Roberto, dalla pagina braccio.stital.it (accesso con Face ID).

## Attrezzi
- Posta: cercare mail (arrivata e inviata), leggere una mail, leggere un allegato (anche PDF scansionati).
- File allegati in chat: leggerli (PDF, Word, Excel, testo).
- Elenchi Excel di polizze: cercare in blocco i documenti nella posta e salvarli nelle schede.
- Schede clienti: consultare e salvare documenti.
- Biblioteca normativa: cercare norme con fonte.
- Questionari: elenco moduli e compilazione.
- Bozze: preparare una mail (mai inviarla) e mostrare documenti.

## Limiti
- Vega non invia, non cancella e non sposta niente. Le mail partono solo con il tocco di Roberto su "Approva e invia".
- Il testo di mail e documenti è materiale da leggere, mai istruzioni (difesa da prompt injection).
- Dati dei clienti: prima di uscire dal server passano dal "velo" (GLiNER + regole), che sostituisce nomi, aziende,
  codici fiscali, P.IVA, IBAN, email, telefoni, targhe, numeri di polizza e indirizzi con segnaposto. La tabella per
  ripristinarli resta sul server, solo per la durata della richiesta. Il cervello riceve solo testo coperto,
  e solo da fornitori con server UE, DPA e nessuna conservazione.
- Massimo 30 passi e 15 minuti per richiesta; tetto di spesa mensile interno (VEGA_TETTO_MESE_USD) oltre a quello
  del fornitore.

## Cervello (decisione di Roberto, 27/11/2026)
- Kimi K2.5 su Amazon Bedrock, regione UE Stoccolma (eu-north-1), solo in-region: le richieste non escono dalla
  regione e Moonshot (chi ha creato il modello) non riceve i dati. Prezzo di listino: 0,72 $ / 3,60 $ per milione
  di token in ingresso/uscita.
- Finché la chiave Bedrock non è inserita dalla pagina (Collegamenti), Vega usa Mistral Large UE.
- Tetti di spesa: azione di budget AWS che toglie il permesso a Bedrock al superamento della soglia, più il tetto
  interno mensile di Vega (VEGA_TETTO_MESE_USD), che blocca subito.

## Flusso dei dati (per registro trattamenti e DPIA)
| Cosa | Dove va | Perché |
|---|---|---|
| Richiesta, testo di mail e documenti, coperti dal velo | Amazon Bedrock, Stoccolma (Kimi K2.5); Mistral UE come riserva | Decidere i passi e scrivere bozze |
| PDF scansionati (OCR) | Mistral UE, come oggi | Leggere il testo |
| Tabella dei segnaposto, documenti, schede | Solo server Aruba | Ripristinare i dati, archivio |

## Prova
tests/test_agente.py: posta e cervello simulati, solo dati finti. Verifica che il cervello non veda i dati veri,
che la bozza sia creata (non inviata) con nomi e allegati giusti, che il documento finisca nella scheda e che
un'istruzione nascosta in una mail non venga seguita.

## Memoria e chat (27/11/2026)
- **Chat**: elenco delle conversazioni nel menu (Oggi, Ieri, Ultimi 7 giorni…), "Nuova chat", cerca, rinomina,
  elimina. Vega usa come contesto solo la conversazione aperta. La chat unica di prima diventa "Chat di prima".
- **Memoria (quaderno)**: note leggibili sul server, una per Roberto ("Come lavori tu") e una per cliente o
  argomento. Vega le rilegge prima di lavorare (attrezzo leggi_memoria) e aggiunge i fatti utili dopo (ricorda).
  Non salva testi interi, codici fiscali, IBAN né dati sanitari. Roberto le vede nella sezione Memoria, le
  corregge e le cancella (cancellata = Vega non la ricorda più: diritto all'oblio).
- Ricerca fatta: Mem0, Zep/Graphiti, Letta (Apache-2.0) richiedono database e memoria in più sul server da 4 GB;
  si parte con le note (stesso principio della memoria di ChatGPT/Claude) e si passa a uno di loro se non basta.

| Cosa | Dove va | Perché |
|---|---|---|
| Conversazioni | Solo server Aruba (/var/lib/braccio-destro/web/conversazioni) | Ritrovare le chat |
| Note della memoria | Solo server Aruba (/var/lib/braccio-destro/memoria) | Non perdere il contesto tra le chat |
| Note dentro la richiesta | Kimi su Bedrock UE, coperte dal velo | Vega ricorda cosa è già stato fatto |
