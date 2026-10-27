# STMAA — Fase 1: audit end-to-end di sicurezza operativa

**Data di aggiornamento:** 27 ottobre 2026  
**Repository:** `iFabbro/STMAA`  
**Commit verificato al termine della verifica PR #89:** `bfb88c29443bee0de655c0eca2e5cca124e132c8`  
**Stato:** controlli di codice della Fase 1 completati fino alla PR #90; documentazione in revisione tramite PR #91.

## 1. Obiettivo e confini

La Fase 1 ha esaminato soprattutto la capacità di STMAA di gestire in modo conservativo guasti, risultati ambigui e dati incompleti durante l'esecuzione, la liquidazione, il recupero dopo riavvio e il calcolo del rischio.

Il criterio centrale è **fail closed**: se il sistema non può determinare in modo affidabile posizione, esposizione, stato degli ordini, equity o validità dei dati di mercato, deve bloccare l'apertura di nuove posizioni invece di stimare valori mancanti o procedere con fallback silenziosi.

Il lavoro è stato svolto principalmente attraverso revisione del codice e test automatizzati con broker/provider simulati. Non sono stati inviati ordini reali durante queste verifiche. Gli smoke test Paper che possono inviare ordini non sono stati eseguiti come parte di questo audit.

Questa documentazione non è una certificazione per il trading live e non sostituisce una validazione operativa indipendente.

## 2. Risultati per area

### 2.1 Fondamenta, backtest e risk overlay — PR #1–19

Le prime modifiche hanno stabilizzato CI e controlli di qualità, corretto il calcolo dei rendimenti multi-asset e delle metriche di rischio, verificato l'ordinamento temporale dei dati, reso monotono l'high-water mark del drawdown e rafforzato i limiti di esposizione.

È stato inoltre consolidato il comportamento fail-closed quando le posizioni del broker sono illeggibili o l'esposizione degli ordini aperti non è stimabile. Il risk overlay deve essere applicato prima dell'esecuzione Paper.

Riferimenti: [PR #5](https://github.com/iFabbro/STMAA/pull/5), [PR #8](https://github.com/iFabbro/STMAA/pull/8), [PR #10](https://github.com/iFabbro/STMAA/pull/10), [PR #12](https://github.com/iFabbro/STMAA/pull/12), [PR #15](https://github.com/iFabbro/STMAA/pull/15), [PR #19](https://github.com/iFabbro/STMAA/pull/19).

### 2.2 Liquidazione d'emergenza, idempotenza e recupero — PR #20–31

Il percorso di liquidazione è stato rafforzato per gestire retry, esiti ambigui, ordini di chiusura ancora pendenti e drift tra stato persistito e stato broker. Le modifiche richiedono riconciliazione prima di ripetere un invio quando l'esito precedente non è certo, normalizzano gli stati restituiti dal broker e aggiornano le posizioni dopo gli esiti terminali.

Sono stati aggiunti casi di regressione per timeout di riconciliazione, esecuzioni parziali e retry.

Riferimenti: [PR #20](https://github.com/iFabbro/STMAA/pull/20), [PR #21](https://github.com/iFabbro/STMAA/pull/21), [PR #22](https://github.com/iFabbro/STMAA/pull/22), [PR #24](https://github.com/iFabbro/STMAA/pull/24), [PR #25](https://github.com/iFabbro/STMAA/pull/25), [PR #29](https://github.com/iFabbro/STMAA/pull/29), [PR #31](https://github.com/iFabbro/STMAA/pull/31).

### 2.3 Equity, drawdown, identità del conto e avvio Paper — PR #32–40

Sono stati rafforzati la validazione dei limiti di rischio e degli input di equity, l'uso dell'equity broker verificata come baseline, la provenienza dei dati del conto, il circuito di protezione drawdown e la gestione del caso in cui manchi l'identità del conto. Il dashboard deve riflettere lo stato operativo persistito.

Sono stati introdotti preflight di avvio per ridurre il rischio di iniziare l'esecuzione con configurazioni non valide.

Riferimenti: [PR #32](https://github.com/iFabbro/STMAA/pull/32), [PR #33](https://github.com/iFabbro/STMAA/pull/33), [PR #35](https://github.com/iFabbro/STMAA/pull/35), [PR #36](https://github.com/iFabbro/STMAA/pull/36), [PR #38](https://github.com/iFabbro/STMAA/pull/38), [PR #39](https://github.com/iFabbro/STMAA/pull/39), [PR #40](https://github.com/iFabbro/STMAA/pull/40).

### 2.4 Invio ordini ambiguo e riconciliazione Paper — PR #41–56

È stato rafforzato il comportamento quando l'invio di un ordine ha un esito ambiguo: il sistema deve riconciliare l'ordine e non inviarne automaticamente un duplicato. Sono stati aggiunti workflow di verifica Paper read-only, diagnostica e smoke test isolati, oltre a strumenti di riconciliazione quantitativa degli ordini e delle attività FILL.

La riconciliazione delle commissioni è stata resa più rigorosa nell'attribuzione all'ordine corretto. Errori di lettura delle attività devono produrre uno stato incompleto/errore, non una riconciliazione apparentemente riuscita.

Riferimenti: [PR #41](https://github.com/iFabbro/STMAA/pull/41), [PR #42](https://github.com/iFabbro/STMAA/pull/42), [PR #43](https://github.com/iFabbro/STMAA/pull/43), [PR #45](https://github.com/iFabbro/STMAA/pull/45), [PR #48](https://github.com/iFabbro/STMAA/pull/48), [PR #51](https://github.com/iFabbro/STMAA/pull/51), [PR #52](https://github.com/iFabbro/STMAA/pull/52), [PR #53](https://github.com/iFabbro/STMAA/pull/53), [PR #54](https://github.com/iFabbro/STMAA/pull/54), [PR #56](https://github.com/iFabbro/STMAA/pull/56).

### 2.5 Snapshot malformati, liquidazioni parziali, persistenza e metriche — PR #57–75

Questa serie ha aggiunto controlli fail-closed per snapshot broker e stato persistito malformati, paginazione bloccata o attività incomplete, snapshot di posizioni non validi e intenti di liquidazione incoerenti. I test coprono recupero dopo riavvio, cancellazione, liquidazione parziale, fallimenti di persistenza ed esportazione delle metriche.

È stata inoltre distinta una commissione ancora in attesa da un errore di esecuzione della riconciliazione e rafforzata la verifica dell'identità dell'ordine durante il recupero. I timestamp di fill futuri sono stati rifiutati nella riconciliazione.

Riferimenti: [PR #57](https://github.com/iFabbro/STMAA/pull/57), [PR #58](https://github.com/iFabbro/STMAA/pull/58), [PR #59](https://github.com/iFabbro/STMAA/pull/59), [PR #60](https://github.com/iFabbro/STMAA/pull/60), [PR #63](https://github.com/iFabbro/STMAA/pull/63), [PR #64](https://github.com/iFabbro/STMAA/pull/64), [PR #65](https://github.com/iFabbro/STMAA/pull/65), [PR #66](https://github.com/iFabbro/STMAA/pull/66), [PR #68](https://github.com/iFabbro/STMAA/pull/68), [PR #69](https://github.com/iFabbro/STMAA/pull/69), [PR #70](https://github.com/iFabbro/STMAA/pull/70), [PR #72](https://github.com/iFabbro/STMAA/pull/72), [PR #73](https://github.com/iFabbro/STMAA/pull/73), [PR #74](https://github.com/iFabbro/STMAA/pull/74), [PR #75](https://github.com/iFabbro/STMAA/pull/75).

### 2.6 Percorso persistente dello stato e riconciliazione dopo riavvio — PR #76–79

- **PR #76:** il timestamp futuro viene rifiutato anche quando è presente una commissione collegata.
- **PR #77–78:** il percorso del file di stato broker è ancorato alla root della repository; anche un override relativo viene risolto in modo stabile. È disponibile un override esplicito tramite `STMAA_BROKER_STATE_FILE`.
- **PR #79:** regressioni per il recupero dopo riavvio di un invio ambiguo e per il blocco dell'esecuzione quando i dati del conto sono incompleti.

Riferimenti: [PR #76](https://github.com/iFabbro/STMAA/pull/76), [PR #77](https://github.com/iFabbro/STMAA/pull/77), [PR #78](https://github.com/iFabbro/STMAA/pull/78), [PR #79](https://github.com/iFabbro/STMAA/pull/79).

### 2.7 Esposizione degli ordini pendenti e dati di mercato — PR #80–89

Questa è la parte finale e centrale della Fase 1.

- **PR #80:** riserva in modo conservativo l'esposizione residua degli ordini parzialmente eseguiti.
- **PR #81:** rifiuta quotazioni obsolete o senza timestamp prima del dimensionamento degli ordini.
- **PR #82:** blocca l'esecuzione quando gli snapshot di mercato o degli ordini aperti sono incompleti.
- **PR #83:** applica i controlli fail-closed a tutti gli entry point che possono aprire posizioni.
- **PR #85:** test per dati di mercato obsoleti ed esposizione pendente sconosciuta.
- **PR #86–87:** test combinati dei gate di rischio con ordini pendenti, input inaffidabili, VIX, drawdown e riconciliazione broker.
- **PR #88:** blocca l'intero ribilanciamento se una qualsiasi quotazione richiesta è obsoleta, evitando l'esecuzione parziale del batch.
- **PR #89:** simula un guasto del provider delle quotazioni e verifica che il ribilanciamento si interrompa senza inviare ordini.

Riferimenti: [PR #80](https://github.com/iFabbro/STMAA/pull/80), [PR #81](https://github.com/iFabbro/STMAA/pull/81), [PR #82](https://github.com/iFabbro/STMAA/pull/82), [PR #83](https://github.com/iFabbro/STMAA/pull/83), [PR #85](https://github.com/iFabbro/STMAA/pull/85), [PR #86](https://github.com/iFabbro/STMAA/pull/86), [PR #87](https://github.com/iFabbro/STMAA/pull/87), [PR #88](https://github.com/iFabbro/STMAA/pull/88), [PR #89](https://github.com/iFabbro/STMAA/pull/89).

## 3. Evidenze CI verificate

Per la PR #89, sul commit di merge `bfb88c29443bee0de655c0eca2e5cca124e132c8`, i controlli post-merge risultano tutti completati con successo:

- `pytest`
- `test`
- `ruff`
- `gitleaks`

Sono stati inoltre verificati i controlli post-merge per le PR #76–82 e #85–88: i quattro controlli risultavano completati con esito positivo per i commit controllati. La presente documentazione non afferma che ogni commit storico della repository sia stato rieseguito da zero.

## 4. Residuo da chiudere prima della chiusura formale della Fase 1

La PR #84 è stata chiusa perché il suo branch era precedente alle modifiche successive ed è stata sostituita dalla [PR #90](https://github.com/iFabbro/STMAA/pull/90), integrata in `main` al commit `8157a9aa0e37ff6d132abf2d477f616486aed4c6`. La PR #90 trasferisce sulla versione corrente i test che verificano il blocco in presenza di timestamp di quotazione futuri o malformati e di valori di esposizione pari a NaN, infiniti o non interpretabili.

I controlli pre-merge e post-merge della PR #90 (`pytest`, `test`, `ruff`, `gitleaks`) sono tutti completati con esito positivo. Commit di merge: `8157a9aa0e37ff6d132abf2d477f616486aed4c6`. La documentazione è sottoposta a revisione tramite [PR #91](https://github.com/iFabbro/STMAA/pull/91); i controlli CI della PR documentale devono concludersi prima di integrarla.

Le PR storiche #3, #9, #16 e #55 sono state chiuse senza merge; le relative aree sono state affrontate da modifiche successive.

## 5. Cosa è stato dimostrato e cosa no

**Dimostrato dai test e dalla CI esaminati:** diversi percorsi di errore vengono gestiti in modalità fail-closed; il recupero di liquidazioni e invii ambigui è coperto da regressioni; ordini pendenti, snapshot incompleti, quotazioni obsolete e guasti simulati del provider possono bloccare l'apertura di nuove posizioni nei casi coperti.

**Non ancora dimostrato:** assenza di tutti i difetti in ogni scenario broker reale, affidabilità operativa per periodi prolungati, comportamento sotto carico, latenza e slippage reali, disaster recovery completo, validazione indipendente, né idoneità al trading live.

Il progetto è ancora da considerare in fase di validazione. La CI verde non costituisce una certificazione di sicurezza per il capitale reale.

## 6. Fase 2 proposta: validazione operativa controllata

Dopo la chiusura formale della Fase 1, la Fase 2 dovrebbe essere pianificata separatamente e mantenere i seguenti vincoli:

1. **Matrice di fault injection:** timeout, errori 4xx/5xx, rate limit, snapshot incompleti o discordanti, dati obsoleti, ordini accettati ma non visibili subito, esecuzioni parziali e riavvii in ogni stato.
2. **Riconciliazione broker/stato locale:** confronto sistematico di posizioni, ordini aperti, ordini terminali, quantità eseguite, commissioni e stato persistito; ogni discrepanza deve avere un esito e una procedura documentati.
3. **Paper trading controllato:** periodo e criteri quantitativi stabiliti prima dell'avvio, monitoraggio, allarmi, registro degli eventi e revisione delle anomalie. Qualsiasi test che invii ordini Paper deve essere autorizzato ed eseguito in modo esplicito, non durante l'audit read-only.
4. **Controlli operativi indipendenti:** credenziali e permessi minimi, limiti lato broker, arresto d'emergenza, recovery testato, gestione dei segreti e procedure di rollback.
5. **Criteri di accettazione:** nessun ordine duplicato negli scenari ambigui coperti, nessuna nuova esposizione quando il rischio non è calcolabile, nessun falso esito di riconciliazione, nessuna perdita silenziosa di stato e nessun errore CI aperto.

Il passaggio al live richiede un gate separato, una revisione indipendente e una decisione esplicita dopo le evidenze della Fase 2. Non è autorizzato da questa documentazione.
