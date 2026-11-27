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

## Cervello
Scelto con VEGA_MODELLO (libreria LiteLLM):
- oggi: Mistral Large UE (già in uso, nessun nuovo fornitore);
- dopo l'ok di Roberto: Claude su Amazon Bedrock, regione Francoforte, con tetto di spesa bloccante.

## Flusso dei dati (per registro trattamenti e DPIA)
| Cosa | Dove va | Perché |
|---|---|---|
| Richiesta, testo di mail e documenti, coperti dal velo | Fornitore del cervello (Mistral UE oppure Bedrock Francoforte) | Decidere i passi e scrivere bozze |
| PDF scansionati (OCR) | Mistral UE, come oggi | Leggere il testo |
| Tabella dei segnaposto, documenti, schede | Solo server Aruba | Ripristinare i dati, archivio |

## Prova
tests/test_agente.py: posta e cervello simulati, solo dati finti. Verifica che il cervello non veda i dati veri,
che la bozza sia creata (non inviata) con nomi e allegati giusti, che il documento finisca nella scheda e che
un'istruzione nascosta in una mail non venga seguita.
