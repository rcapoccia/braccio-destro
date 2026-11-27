# Protocollo di lavoro — Roberto (Stital / Stile Italiano)

Queste regole valgono per **ogni** progetto: app, automazioni, agenti.
Se una regola di progetto contraddice questo file, chiedi prima di procedere.

## 1. Prima capire, poi proporre
- Prima di proporre o modificare: leggi codice, funzionalità e logica esistenti (audit). Niente consigli "a caso", niente affermazioni non verificate.
- Se qualcosa non è chiaro, chiedi. Non inventare requisiti.
- Non ripetere domande su contesto già dato: cercalo prima nel repo e in questo file.
- Prima di costruire qualcosa di nuovo (funzione, componente, agente, modello): cerca se esiste già una soluzione pronta e affidabile su GitHub, Hugging Face, registri di pacchetti (npm, PyPI) o marketplace di plugin/MCP. Presenta cosa hai trovato (licenza, manutenzione, sicurezza, costi) e proponi se riusarlo, adattarlo o costruire da zero. Ciò che si riusa passa comunque dalla regola 8.
- Non aspettare che sia Roberto a suggerire strumenti: all'inizio di ogni progetto (e almeno una volta al mese) proponi di tua iniziativa gli strumenti pertinenti di `STRUMENTI.md` e cerca novità (modelli, agenti, servizi) che riducano costi o errori.

## 2. Una cosa alla volta, con ok esplicito
- Prima di creare progetti, server, account, database o qualunque risorsa: spiega **cosa** e **perché**, poi aspetta l'ok.
- Prima di qualunque spesa: costo stimato + tetto di spesa bloccante impostato sul servizio. Senza tetto, non si attiva.
- Passi piccoli e verificabili. Niente modifiche massive in un colpo solo.

## 3. Segreti
- Le chiavi API le inserisce Roberto direttamente nelle piattaforme. **Mai** chiederle, scriverle o incollarle in chat, nel codice o nei commit.
- Usa solo variabili d'ambiente / secret manager. `.env` sempre in `.gitignore`.
- Ogni repo nasce **privato**. Un token finito in chiaro (chat, prompt, file, log) si considera bruciato: va revocato subito.
- Ogni repo ha la scansione segreti (gitleaks) nella CI.

## 4. Dati dei clienti (GDPR) — regola più importante
- I progetti trattano dati reali dei clienti, anche di categoria particolare (es. sanitari). Progettare conformi al GDPR dal primo giorno.
- I dati dei clienti vanno solo a fornitori con DPA firmato, server in UE, zero data retention e niente addestramento. Nessun altro canale (né log, né analytics, né LLM di prova).
- Sviluppo e test: **solo dati finti**. Mai dati reali in repo, fixture, screenshot o issue.
- Ogni nuovo flusso di dati va annotato (cosa, dove va, perché) per registro trattamenti e DPIA, che Roberto fa verificare al suo consulente privacy.
- Gli agenti preparano bozze: **nulla viene inviato** a clienti o compagnie senza l'ok di Roberto.

## 5. Costi e protezione dei token
- Ogni chiamata a LLM esposta all'esterno deve avere: limite per utente, limite di frequenza, tetto di spesa e timeout.
- Le parti ripetitive possono andare a modelli più economici **solo se** non toccano dati dei clienti, oppure se rispettano la sezione 4.

## 6. Progetti separati
- Ogni progetto ha il suo repo e i suoi dati. Non mischiare (es. RDI e braccio destro sono distinti).

## 7. "Fatto" significa testato
- Prima di dire "fatto": test passati, build ok, e verifica manuale del caso principale.
- Prima di ogni rilascio: security review (permessi, segreti, input non fidati, dati personali nei log).
- Revisione indipendente: prima del rilascio, la modifica viene controllata da un modello diverso da quello che l'ha scritta (es. GLM-5.4 se ha scritto Claude).
- App con AI: test di attacco con Promptfoo (prompt injection, fuga di dati) prima del rilascio.
- App pubblicate: monitoraggio errori (Sentry), senza dati personali nei messaggi.
- Gli strumenti di attacco (pentest) si usano solo su copie di test con dati finti, mai in produzione.

## 8. Strumenti esterni
- L'elenco degli strumenti scelti, con stato e costi, è in `STRUMENTI.md`.
- Plugin, skill e MCP di terzi si installano solo dopo averne letto il codice e bloccata la versione.

## 9. Registro errori
- Ogni problema risolto che poteva ripetersi diventa una riga in `ERRORI.md` (cosa è successo → regola nuova).

---

## Decisioni di Roberto per questo progetto (braccio destro / Vega)
Prima di costruire, rileggi queste righe. Se una modifica le contraddice, chiedi prima.
- Vega è un **agente vero**: capisce la richiesta e decide da solo i passi usando i suoi strumenti. Niente percorsi fissi per tipo di richiesta (errore del 27/11/2026, vedi ERRORI.md del protocollo).
- Vega non invia nulla: prepara bozze; l'invio parte solo dal tocco di Roberto.
- Dati dei clienti: solo server Aruba (IT) e Mistral (UE, zero data retention). Altri fornitori solo con testo anonimizzato.
- Interfaccia: pagina braccio.stital.it ospitata sul server Aruba, accesso con passkey.
- Progetto separato da RDI.
