# STMAA — Structural Trading Multi-Agent Architecture

<p align="center">
  <img src="./Logo.png" alt="Logo STMAA" width="160" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?logo=python&style=for-the-badge" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/Stato-Alpha-orange?style=for-the-badge" alt="Stato alpha" />
  <img src="https://img.shields.io/badge/Trading-Solo%20Paper-blue?style=for-the-badge" alt="Solo paper trading" />
  <a href="https://github.com/iFabbro/STMAA/actions/workflows/ci.yml"><img src="https://github.com/iFabbro/STMAA/actions/workflows/ci.yml/badge.svg" alt="Stato CI" /></a>
  <img src="https://img.shields.io/badge/Licenza-MIT-green?style=for-the-badge" alt="Licenza MIT" />
</p>

STMAA è un framework modulare e sperimentale per la ricerca sul trading sistematico, la generazione dei pesi di portafoglio, il backtesting, i controlli del rischio e l'integrazione con il broker. L'architettura separa la validazione dei dati di mercato, l'elaborazione delle variabili, la generazione delle strategie, i filtri di rischio, l'esecuzione, la persistenza dello stato e il monitoraggio operativo.

**Ambito attuale: ricerca e Alpaca Paper Trading. STMAA non è certificato né presentato come software pronto per la produzione; il punto di ingresso operativo incluso nel repository è configurato esclusivamente per il paper trading.** Il superamento dei test non dimostra la redditività, non elimina i rischi di esecuzione e non certifica l'idoneità a operare con capitale reale.

## Funzionalità attuali

- **Interfaccia basata sui pesi:** le strategie esprimono le allocazioni obiettivo tramite pesi di portafoglio.
- **Integrazione dei dati di mercato:** accesso a barre storiche e quotazioni recenti tramite Alpaca, con controlli pensati per individuare dati mancanti, malformati, incompleti o obsoleti.
- **Controlli del rischio:** vincoli sull'esposizione di portafoglio e delle singole posizioni, controlli relativi al drawdown e logica di overlay del rischio basata sul VIX.
- **Protezioni nell'esecuzione tramite broker:** verifiche sull'account, sugli ordini aperti o pendenti, sulla coerenza tra ordini e posizioni e sui dati necessari a stimare l'esposizione. Quando lo stato necessario non è affidabile, i percorsi interessati sono progettati per bloccare l'operazione invece di inviare un nuovo ordine.
- **Riconciliazione degli ordini:** controlli per gestire aggiornamenti ritardati delle posizioni dopo l'esecuzione, inclusi i riempimenti parziali e le discrepanze non risolte.
- **Persistenza dello stato di esecuzione:** gestione dello stato locale con protezioni per la scrittura atomica, blocco dei processi concorrenti e verifiche contro scritture obsolete.
- **Componenti di backtesting:** motore di ricerca per il backtesting e interfacce condivise basate sui pesi.
- **Visibilità operativa:** registrazione di eventi e sessioni tramite SQLite e dashboard Textual da terminale. Overview e System mostrano snapshot e cronologia locale; Portfolio mostra esclusivamente le posizioni contenute nello snapshot persistito; Orders & Executions mostra separatamente le evidenze persistite del broker executor.
- **Test di regressione automatizzati:** copertura di strategie, dati, esecuzione, rischio e scenari di errore.

Queste descrizioni riguardano funzionalità presenti nel codice; non garantiscono che ogni possibile errore di mercato, broker, rete o processo sia coperto. Prima di fare affidamento su un comportamento, esaminare il codice e i risultati CI più recenti.

## Dashboard terminale: stato e scritture locali

La dashboard Textual legge le fonti locali tramite i provider read-only in `src/dashboard/local_providers.py`. Overview distingue lo snapshot JSON dall'ultimo snapshot SQLite; System mostra disponibilità delle fonti, metadati dell'ultima run e una pagina limitata di eventi recenti. Portfolio mostra soltanto le posizioni contenute nello snapshot di monitoraggio persistito, con provenienza e timestamp: non interroga il broker e non garantisce che le posizioni siano attuali. La freschezza delle posizioni è valutata separatamente tramite `positions_snapshot_at`, con soglia della dashboard di 900 secondi: una raccolta verificata ma troppo vecchia è `STALE`; timestamp assente è `MISSING`; timestamp malformato o materialmente futuro è `INVALID`; fonte non verificabile è `UNKNOWN`. Le posizioni non vengono mostrate come correnti quando il relativo stato non è `OK`. Orders & Executions legge `logs/execution/broker_state.json` (o il percorso configurato da `STMAA_BROKER_STATE_FILE`) e presenta in sezioni distinte gli intenti di submission non risolti, gli ordini confermati persistiti, i batch OPG e gli stati delle liquidazioni di emergenza (pending e unresolved). Questo file contiene evidenza locale storica dell'ultima persistenza, non un registro live degli ordini, dei fill o dello stato corrente del broker. Uno stato mancante, non valido o non leggibile viene mostrato come sconosciuto/non disponibile; non viene convertito in una collezione vuota.

**La dashboard non è un processo zero-write.** `hub.py` conserva le scritture di audit della propria sessione tramite `start_run()`, `log_event()` ed `end_run()`. In particolare, `start_run()` usa il lifecycle SQLite esistente, che può inizializzare il database e recuperare sessioni orfane. Queste operazioni sono separate dai provider: lettura, refresh e rendering dei provider non devono scrivere, migrare, recuperare o modificare dati. Nessuna di queste operazioni di audit implica una chiamata al broker o l'invio di ordini.

## Architettura

```mermaid
flowchart LR
  MD[Dati di mercato] --> DV[Validazione dati]
  DV --> FE[Feature]
  FE --> ST[Strategia / PPO quando disponibile]
  ST --> PW[Pesi di portafoglio]
  PW --> RO[Overlay del rischio]
  RO --> EX[Motore di esecuzione]
  EX <--> BR[API Alpaca Paper]
  EX --> PS[Stato di esecuzione persistito]
  EX --> LG[Log SQLite]
  LG --> HUB[Hub di monitoraggio]
```

Il repository contiene codice relativo a PPO, ma la presenza di un agente o di dipendenze per l'addestramento non dimostra che esista un modello addestrato, validato o redditizio. La qualità del modello e le prestazioni della strategia richiedono valutazioni separate.

## Sicurezza e limiti operativi

- Il file `.env.example` configura `STMAA_TRADING_MODE=paper` e l'endpoint API di Alpaca Paper.
- Il punto di ingresso `trade.py` costruisce il broker executor con il paper trading abilitato.
- La configurazione del rischio include `MAX_PORTFOLIO_EXPOSURE` (valore predefinito `0.50`) e `MAX_DRAWDOWN` (valore predefinito `0.15`); i valori vengono validati all'avvio. Altri controlli sono configurati nei moduli di strategia ed esecuzione pertinenti.
- Il limite operativo di esposizione nozionale lorda è fissato a **1.000 USD**, condiviso tra DAY e OPG. Somma il valore lordo delle posizioni aperte e le riserve degli ordini BUY pendenti, inclusi quelli parzialmente eseguiti; il nuovo BUY viene ridotto o bloccato se supererebbe il limite. Le SELL di riduzione restano consentite e gli ordini SELL pendenti non vengono conteggiati come nuova esposizione. I BUY DAY sono inviati come limit order al prezzo della quotazione verificata, così il fill non può superare il prezzo usato per il sizing. Nel preflight OPG le SELL pianificate non vengono sottratte prima del fill: un batch misto può inviare BUY soltanto se posizioni correnti, BUY pendenti e nuovi BUY restano già entro 1.000 USD. Dati di posizione, prezzi, lato o stato ordine non determinabili bloccano fail-closed le nuove esposizioni. Il limite è indipendente dal capitale strategico di riferimento.
- Il percorso di esecuzione include controlli fail-closed per importanti condizioni di errore o indeterminatezza relative a broker, ordini, posizioni, dati di mercato e stato persistito.
- La liquidazione di emergenza al superamento della soglia di drawdown è opzionale e, nel file `.env.example`, è disabilitata per impostazione predefinita.
- Molti scenari di errore vengono verificati tramite mock e stato locale. Un test superato con mock non prova il comportamento end-to-end del broker in tutte le condizioni reali.

**Non configurare questo progetto per un account di broker live e non usarlo per negoziare fondi reali basandosi soltanto su questo README.** Un eventuale supporto futuro al live trading richiederebbe una revisione separata dell'implementazione, protezioni esplicite, test operativi e una validazione indipendente del rischio.

## Demo

<p align="center">
  <img src="./docs/dashboard-screenshot-2026-11-12.png" alt="Dashboard terminale STMAA" />
</p>

## Requisiti

- Python 3.12.
- Git.
- [uv](https://docs.astral.sh/uv/) per risolvere e installare le dipendenze dal lock file.
- Credenziali Alpaca Paper Trading per i flussi che richiedono dati di mercato o accesso al broker.

Il repository usa `pyproject.toml` per dichiarare le dipendenze dirette e `uv.lock` per bloccare le versioni risolte, incluse quelle transitive. Il lock file è condiviso tra le piattaforme supportate; `uv` seleziona i pacchetti compatibili con il sistema operativo e l'architettura.

## Avvio rapido

Clonare il repository e installare le dipendenze runtime in un ambiente isolato:

```bash
git clone https://github.com/iFabbro/STMAA.git
cd STMAA
uv sync --locked
```

Per installare anche le dipendenze di sviluppo e test:

```bash
uv sync --locked --group dev
```

Per aggiornare intenzionalmente il lock file dopo una modifica alle dipendenze:

```bash
uv lock
uv sync --locked --group dev
```

Non usare `uv lock --upgrade` come aggiornamento automatico ordinario: aggiorna le versioni solo nell'ambito di una modifica deliberata e revisionata.

Creare un file di configurazione locale a partire dal modello:

```bash
cp .env.example .env
```

Inserire in `.env` le credenziali API di **Alpaca Paper Trading**. Non salvare credenziali o altri segreti nel repository. Mantenere la modalità paper e l'endpoint paper, salvo modifiche esplicitamente revisionate.

Eseguire la suite di test usando l'ambiente bloccato:

```bash
uv run --locked pytest src/tests tests -q
uv pip check
```

### Addestramento PPO (ricerca, non paper execution)

Il training PPO è un'attività di ricerca separata dal ciclo di paper trading. Usa lo stesso contratto versionato dell'inferenza:

Il capitale strategico predefinito è **100.000 USD** (`STMAA_INITIAL_CAPITAL`), coerente con l'equity dell'account Alpaca Paper di riferimento attualmente adottato per lo sviluppo. Può essere sovrascritto tramite `.env` o `--initial-capital` durante il training. Questo valore è la base di calcolo della strategia: **non costituisce un limite operativo agli ordini**. Il limite operativo separato del percorso DAY/OPG è fissato a 1.000 USD di esposizione nozionale lorda aggregata.

- **Contratto modello:** versione `7`.
- **Contratto osservazioni:** `dimensionless_ohlc_features_v1`. Per ogni ticker la policy riceve rendimento close-to-close a una barra, RSI centrato, MACD/Close, Close/BB_middle - 1 e ATR/Close; il vettore include inoltre liquidità e pesi correnti.
- **Contratto dati:** versione `4`, barre giornaliere Alpaca SIP consolidate con `adjustment=all`, campi OHLCV e campo `Open` richiesto per l'esecuzione al successivo open.
- **Esecuzione simulata:** il segnale è calcolato sulla barra completata (t) e il nuovo target è applicato all'Open della barra (t+1). Le posizioni preesistenti assorbono il gap overnight; il nuovo target parte dall'open successivo. Costi e slippage sono applicati nel modello di ambiente.
- **Separazione temporale:** il periodo più recente è riservato alla validazione cronologica e non viene usato per l'ottimizzazione PPO.

Esempio di training esplicito:

```bash
uv run --locked python train.py \
  --start-date 2018-01-01 \
  --end-date 2026-01-01 \
  --total-timesteps 50000 \
  --validation-fraction 0.2 \
  --max-position-size 0.20 \
  --max-gross-exposure 0.50 \
  --transaction-cost 0.001 \
  --slippage 0.0005 \
  --learning-rate 0.0003 \
  --n-steps 2048 \
  --batch-size 64 \
  --n-epochs 10 \
  --gamma 0.99 \
  --seed 42 \
  --include-timeseries
```

`--end-date` è esclusiva. Il percorso base predefinito è `models/ppo_trading_model.zip`; può essere cambiato con `--model-path`. Il percorso base non è il file binario pubblicato: dopo il salvataggio il sistema produce un artefatto immutabile con nome content-addressed, `ppo_trading_model.<sha256>.zip`, e un manifest attivo `ppo_trading_model.zip.metadata.json`. Il manifest punta all'artefatto esatto tramite nome e SHA-256. I file temporanei vengono preparati nella stessa directory; il manifest viene pubblicato atomicamente dopo l'artefatto. Non rinominare o modificare manualmente questi file.

Il manifest registra: versione del contratto modello, ticker e dimensione dello stato, contratti di azione/esecuzione/osservazione, costi e limiti, contratto dati completo, SHA-256 dell'artefatto, nome dell'artefatto e metadati di training (fonte dati, intervalli training/validation, frazione holdout, timesteps, seed e metriche di validazione). La specifica dei campi e il relativo JSON Schema sono in [`docs/PPO_ARTIFACTS.md`](docs/PPO_ARTIFACTS.md) e [`docs/schemas/ppo-artifact-manifest.schema.json`](docs/schemas/ppo-artifact-manifest.schema.json).

Il caricamento verifica la compatibilità dei contratti, dei parametri, del nome content-addressed e dell'hash prima di deserializzare il modello. Gli artefatti PPO v6 o con semantica delle osservazioni precedente non sono compatibili con v7: devono essere rigenerati con un nuovo training controllato. La manutenzione degli artefatti è esplicita e il pruning è dry-run per impostazione predefinita.

La validazione holdout è diagnostica e non applica una soglia automatica di redditività. Un risultato positivo non garantisce prestazioni future. Il training effettua richieste autenticate all'API di mercato Alpaca tramite `StockHistoricalDataClient` per scaricare lo storico SIP; richiede quindi credenziali Alpaca abilitate all'accesso ai dati richiesti. Non usa il client di trading, non legge né modifica lo stato dell'account, non invia, cancella o modifica ordini e non avvia il ciclo operativo Paper. Non viene eseguito automaticamente all'avvio di `trade.py`. Alla data del 15 novembre 2026 il repository non distribuisce ancora un artefatto PPO pre-addestrato ai nuovi checkout. Il requisito di prodotto è fornire un modello già addestrato e verificato, senza richiedere training all'utente finale; packaging e distribuzione dell'artefatto restano da completare. La procedura di training descritta qui sotto è destinata a ricerca, manutenzione e riproducibilità del modello, non deve essere interpretata come passaggio ordinario richiesto a ogni utente.

Il manifest registra inoltre i parametri PPO effettivi (`learning_rate`, `n_steps`, `batch_size`, `n_epochs`, `gamma`), il capitale iniziale, il seed, l'hash SHA-256 del dataset processato effettivamente passato al training e il commit Git sorgente quando il comando viene eseguito in un checkout Git. Registra anche se il working tree Git è pulito: un commit identificato con modifiche locali non committate non descrive da solo l'intero codice effettivamente eseguito. Se Git non è disponibile, i campi di provenienza Git sono `null`. L'hash identifica valori, colonne e timestamp della matrice di feature dopo pulizia e calcolo degli indicatori; non sostituisce l'archiviazione del dataset. Se il codice viene eseguito da un archivio privo di metadati Git, `git_commit` è `null`.

Per avviare il flusso Alpaca Paper dopo aver generato e verificato un modello compatibile:

```bash
uv run --locked python trade.py
```

#### Preflight operativo senza avviare il ciclo

Per verificare la configurazione e l'accesso all'account Alpaca Paper senza avviare il ciclo di trading:

```bash
uv run --locked python trade.py --preflight-only
```

Questa modalità richiede credenziali Alpaca Paper valide e un account raggiungibile. Esegue la validazione locale della configurazione e acquisisce snapshot read-only di account, posizioni e ordini aperti; riconcilia inoltre le submission DAY e i batch OPG persistiti usando evidenza del broker. Calcola l'esposizione nozionale lorda aggregata (posizioni più riserve BUY pendenti) e la confronta con il limite operativo di **1.000 USD**. Il report indica separatamente se il broker è riconciliato e se una nuova decisione sarebbe consentita. Se esistono ordini aperti/irrisolti o l'esposizione supera il limite, il comando termina con codice di uscita `2` e segnala il blocco: non tenta di correggere la situazione.

Il preflight non avvia il polling, non carica né richiede un modello PPO e non invia, modifica, sostituisce, cancella o chiude ordini. La riconciliazione può aggiornare metadati e stato locale persistito, ma le chiamate al broker restano di lettura. Gli snapshot Alpaca non sono atomici tra endpoint: il report è una fotografia diagnostica del momento, non una garanzia che lo stato non cambi subito dopo. Un esito senza blocchi non autorizza automaticamente un test con ordini e non dimostra che il ciclo Paper completo possa essere eseguito.

Avviare l'hub di monitoraggio da terminale in una finestra separata:

```bash
uv run --locked python hub.py
```

L'avvio corretto non dimostra che la strategia sia redditizia né che tutti gli scenari di esecuzione siano stati validati. STMAA resta alpha/sperimentale e non va configurato per operare con capitale reale.

### Validazione PPO walk-forward

Per valutare la robustezza temporale oltre il singolo holdout cronologico, è disponibile una validazione expanding-window:

```bash
uv run --locked python walk_forward.py \
  --start-date 2018-01-01 \
  --end-date 2026-01-01 \
  --min-train-observations 756 \
  --test-observations 126 \
  --total-timesteps 50000 \
  --seed 42
```

Ogni fold addestra un nuovo agente PPO soltanto sul periodo precedente e valuta il modello sul successivo intervallo non sovrapposto. I parametri restano fissi durante l'esecuzione; i risultati OOS non devono essere usati per ottimizzarli. Il report JSON viene scritto in `reports/` oppure nel percorso indicato da `--report-path`.

I fold partono ciascuno da capitale indipendente in liquidità: i risultati sono quindi diagnostici per fold e non vengono concatenati in una curva patrimoniale continua. La metodologia, i limiti e i dettagli sono descritti in [docs/PPO_WALK_FORWARD.md](docs/PPO_WALK_FORWARD.md). Questa valutazione non invia ordini e non usa il client di trading Alpaca.

Per ripetere gli stessi fold con più inizializzazioni PPO, usare
`--seeds` (ad esempio `--seeds 11 22 33 44 55`). Il report conserva i
risultati per ogni seed/fold e aggiunge media, mediana, deviazione standard,
minimo e massimo delle metriche numeriche, sia complessivamente sia per fold.
Metriche non definite non vengono convertite in zero. Le statistiche descrivono
run indipendenti: i rendimenti non vengono concatenati o composti in una curva
patrimoniale continua. Dettagli: [docs/PPO_WALK_FORWARD.md](docs/PPO_WALK_FORWARD.md).

### Benchmark PPO versus equal-weight buy-and-hold

Per confrontare il PPO walk-forward con una baseline passiva senza riaddestrare
gli agenti, usare il report JSON già prodotto da `walk_forward.py`:

```bash
uv run --locked python benchmark_walk_forward.py \
  --ppo-report reports/ppo_walk_forward_20261109.json \
  --start-date 2018-01-01 \
  --end-date 2026-01-01 \
  --report-path reports/ppo_benchmark_comparison_20261109.json \
  --include-timeseries
```

Il benchmark equal-weight usa gli stessi fold OOS, capitale, costi, slippage e limiti di
esposizione del report PPO. Parte in liquidità, entra all'Open della seconda
barra del fold (come il valutatore PPO), acquista a peso uguale e mantiene le
quote senza ribilanciamento. Il report aggiunge SPY come serie separata dall'universo PPO, con due simulazioni buy-and-hold: esposizione fissa al 50% (riferimento
a esposizione comparabile) e al 100% (riferimento di mercato non scalato). Per
SPY non si applica il limite del 20% per singola posizione, perché è un solo ETF;
entrambi gli scenari usano ingresso al successivo Open e gli stessi costi e
slippage. Il report confronta le metriche per fold; le
simulazioni restano indipendenti e non costituiscono una curva patrimoniale
continua. La metodologia è descritta in [docs/PPO_BENCHMARK.md](docs/PPO_BENCHMARK.md).

### Confronto multi-seed: risultati e limiti (10 novembre 2026)

L'analisi descrittiva completata usa 3 inizializzazioni PPO (seed 42, 7 e 21) sugli stessi 11 fold walk-forward: 33 valutazioni seed/fold, ma soltanto 11 periodi di mercato distinti. Il dataset analizzato comprendeva 7 ticker, dal 30 gennaio 2018 al 6 novembre 2026, per 2.206 osservazioni. Le inizializzazioni multiple misurano la sensibilità alla casualità del training; non moltiplicano il numero di periodi OOS indipendenti.

| Indicatore | Risultato descrittivo |
|---|---:|
| Fold con rendimento netto positivo | 24/33 (72,7%) |
| Rendimento netto medio per fold | +3,5535% |
| Rendimento netto mediano per fold | +4,5912% |
| Intervallo dei rendimenti | -15,6523% / +19,0003% |
| Drawdown medio per fold, aggregato descrittivo | circa -9,48% |
| Peggior drawdown osservato | -27,1549% |

Le medie dei rendimenti per seed sono: +5,6436% (seed 42), +2,9976% (seed 7) e +2,0193% (seed 21). Le medie Sharpe per seed sono rispettivamente 0,976, 0,675 e 0,419; le medie Sortino sono 1,524, 1,147 e 0,624. La volatilità annualizzata media per seed è compresa tra 13,68% e 14,45%. Questi valori sono medie aritmetiche di metriche calcolate separatamente per fold: non sono metriche di un portafoglio aggregato e non devono essere interpretati come un track record composto.

Ogni fold parte da capitale indipendente in liquidità (capitale iniziale di riferimento 100.000 USD) e contiene 125 periodi giornalieri. Il CAGR riportato per fold è un'annualizzazione matematica di un intervallo di circa sei mesi, non un rendimento annuo effettivamente osservato. I costi sono già detratti dall'equity nel simulatore: non devono essere sottratti una seconda volta. Le ipotesi di commissione e slippage restano un modello, non una misura garantita dei costi reali di mercato, spread e market impact.

Il confronto benchmark resta diagnostico: nei 33 confronti seed/fold, equal-weight ha superato il rendimento PPO in 19 casi; SPY al 50% in 14 casi; SPY al 100% in 23 casi. Questi conteggi non sono voti statistici indipendenti. SPY al 100% è un riferimento di mercato non scalato, non un confronto a rischio equivalente. Il benchmark exposure-matched equal-weight richiede le tracce giornaliere PPO: è disponibile per report single-seed con trace completa. Nel report multi-seed è intenzionalmente INCONCLUSIVE, perché usare un'unica traiettoria di esposizione per seed diversi falserebbe il confronto.

**Esito della valutazione:** il campione mostra un segnale storico positivo netto, ma non dimostra ancora una redditività robusta né compatibilità con il profilo di rischio previsto. Il peggior drawdown osservato (-27,15%) supera il limite configurato del 15%. Poiché la liquidazione automatica al drawdown è disabilitata per impostazione predefinita, il valore configurato non va descritto come uno stop-loss effettivamente applicato. La multi-seed è quindi un'analisi descrittiva completata, non un PASS di validazione.

### Protocollo holdout finale: stato e regole

I periodi walk-forward e multi-seed già esaminati durante lo sviluppo non sono più un holdout finale intatto. Non si deve rinominare retroattivamente come "final holdout" un intervallo già consultato per diagnosi, confronto o decisioni di sviluppo. Se non esiste una finestra storica documentata come mai ispezionata, la validazione finale dovrà essere prospettica.

Prima di iniziare la raccolta prospettica, il protocollo richiede di congelare e registrare:

1. commit Git e stato pulito del working tree;
2. file modello content-addressed, SHA-256 e manifest;
3. seed/modello selezionato prima dell'inizio, senza scegliere a posteriori il seed con il miglior risultato;
4. universo ticker, contratti dati/feature e feed;
5. parametri PPO, capitale simulato, limiti di posizione/esposizione e regole di esecuzione;
6. costi e slippage di base, più scenari di sensibilità predefiniti;
7. metriche, benchmark e criteri di valutazione.

La finestra prospettica raccomandata è di 252 sedute NYSE completate. Un'eventuale lettura intermedia può controllare soltanto integrità dei dati, disponibilità del processo e correttezza della raccolta; non deve essere usata per cambiare modello, parametri o criteri. Se si interrompe il protocollo per un difetto tecnico, l'interruzione e la causa devono essere registrate prima di decidere se ripartire.

La valutazione finale deve riportare rendimento totale netto dei costi, drawdown massimo, volatilità, Sharpe/Sortino, esposizione, turnover, costi e confronto con equal-weight, SPY 50%, SPY 100% e, quando metodologicamente disponibile, equal-weight a esposizione PPO abbinata. Criteri preliminari: rendimento netto positivo e drawdown massimo non superiore al 15%; le altre metriche sono diagnostiche e vanno lette insieme, non isolate. Il superamento di questi criteri non garantisce risultati futuri.

La raccolta prospettica è inizialmente **shadow/read-only**: nessun ordine viene inviato, cancellato o modificato per effetto del protocollo. Un eventuale test con ordini Alpaca Paper richiederà un'autorizzazione separata e limiti operativi espliciti. Il protocollo non autorizza in alcun modo operazioni live.

### Qualità delle quotazioni (monitoraggio)

Gli snapshot espongono età, spread assoluto/percentuale e stato qualitativo
(ok, stale, future_timestamp, wide_spread, clock_skew, missing, invalid o
unavailable). Le soglie predefinite sono 1.200 secondi per la freschezza
(compatibili con delayed SIP), 5 secondi di tolleranza per timestamp futuri e
2% per segnalare spread ampi. Sono configurabili tramite
STMAA_QUOTE_MAX_AGE_SECONDS, STMAA_QUOTE_FUTURE_TOLERANCE_SECONDS e
STMAA_QUOTE_WIDE_SPREAD_PCT.

Il feed delle barre storiche e quello delle quotazioni recenti sono configurazioni distinte: `DATA_FEED` governa le barre strategiche (SIP), mentre `DATA_QUOTE_FEED` governa le quotazioni (delayed SIP nella configurazione attuale). La telemetria distingue la sorgente della quotazione e controlla freschezza, timestamp futuri e spread; il confronto con IEX è diagnostico e non sostituisce il feed SIP del contratto PPO. Un dato obsoleto o incoerente deve restare visibile come tale e non essere interpretato come prova di freschezza del feed.

I campi sono esclusivamente diagnostici: non modificano il prezzo medio,
il contratto dati PPO, i manifest, i segnali, i pesi, il risk overlay o il
percorso di esecuzione. Non costituiscono un filtro ordini.

### Feed dati Alpaca e ritardo

Il contratto versionato del modello usa barre giornaliere consolidate **SIP** con
`adjustment=all` per training e inferenza. Il feed IEX non viene usato per
costruire lo storico del modello, perché la sua copertura non garantisce tutte
le sedute NYSE richieste dal controllo di completezza.

Per le quotazioni recenti, l'adapter richiede il feed **delayed SIP** quando
l'endpoint delle ultime quotazioni lo supporta. Le barre storiche continuano a
usare il feed `sip`: `delayed_sip` non è supportato dall'endpoint storico
delle barre. Sul piano gratuito le richieste storiche SIP devono rispettare il
ritardo previsto da Alpaca; l'adapter applica un margine temporale e, durante
una sessione NYSE in corso, esclude la barra giornaliera parziale della sessione
corrente.

Il passaggio da IEX a SIP incrementa la versione del contratto dati. I modelli
salvati con il precedente contratto IEX non sono compatibili: occorre
riaddestrarli e verificare i nuovi metadati prima di caricarli. Questo cambio
non dimostra la qualità predittiva del modello e non abilita il live trading.

### Frequenza di polling e decisione giornaliera

`STMAA_INTERVAL_SECONDS` (default `300`) determina la frequenza con cui il processo controlla i dati e lo stato del broker; **non** determina la frequenza delle decisioni PPO. Il feed strategico usa barre giornaliere: il sistema registra in modo persistente il timestamp UTC della barra e consente al massimo una decisione PPO per barra valida. Le iterazioni successive sulla stessa barra non rigenerano i pesi e non inviano nuove submission.

A ogni polling il broker executor continua le verifiche operative read-only su submission irrisolte, ordini confermati, riconciliazione delle posizioni e ordini aperti. Un ordine attivo o non riconciliato blocca una nuova decisione su una barra successiva, ma non interrompe il monitoraggio periodico.

La prenotazione della decisione viene salvata prima dell'inferenza. Se il processo si interrompe prima del completamento persistito, il ciclo resta bloccato: al riavvio STMAA tenta esclusivamente la riconciliazione read-only degli ordini e delle posizioni. Se l'evidenza del broker non è conclusiva, non viene effettuata una nuova submission. La barra interrotta resta consumata anche dopo una riconciliazione riuscita. Uno stato broker preesistente con storico di esecuzione ma senza il nuovo marcatore giornaliero richiede una verifica manuale dell'operatore e non viene azzerato automaticamente.

### Esecuzione MOO/OPG pianificata (Paper)

Il percorso MOO/OPG è un'opzione esplicita e separata dal percorso DAY esistente. Per avviarlo:

```bash
uv run --locked python trade.py --execution-mode opg
```

Senza `--execution-mode opg`, `trade.py` mantiene il percorso DAY predefinito. La modalità OPG non effettua fallback automatici al percorso DAY.

Il ciclo OPG usa la barra giornaliera completata come segnale e risolve la sessione successiva tramite il calendario XNYS. La finestra prevista è dalle **19:00 ET della sessione del segnale alle 09:25 ET della sessione target**. Prima dell'apertura della finestra il processo attende; dalle 09:25 ET in poi il segnale è considerato mancato e non viene recuperato né spostato automaticamente a una sessione successiva. Gli orari sono basati su `America/New_York`, quindi seguono automaticamente il cambio tra ora solare e legale.

Durante la finestra:
1. STMAA genera i pesi PPO e applica i controlli del risk overlay già previsti dal ciclo Paper.
2. Il piano OPG converte i target in quantità intere, usando come capitale di sizing il minore tra equity effettiva verificata e limite operativo aggregato di 1.000 USD, insieme al Close della barra completata. L'equity effettiva resta la base per l'inferenza PPO e per i controlli di drawdown; il capitale di sizing limita invece il controvalore degli ordini. Le quantità correnti sono lette dal broker; posizioni short, frazionarie o non modellate bloccano il piano.
3. Sotto il broker execution lock, il preflight legge nuovamente account, posizioni e ordini Paper e verifica identità dell'account, stato operativo, coerenza delle quantità, idoneità degli asset, budget BUY e limite aggregato di 1.000 USD.
4. Solo dopo il preflight approvato, STMAA persiste il batch e le intenzioni d'ordine, quindi invia i BUY come limit-on-open (LOO), con limit price pari al Close usato per il sizing, e le SELL come market-on-open (MOO), in sequenza. Il report del preflight viene conservato nel batch come evidenza della verifica.

Il preflight è read-only: non invia, cancella, sostituisce o chiude ordini. I BUY LOO non possono essere eseguiti sopra il Close usato per il sizing; se l'asta apre sopra il limite, l'ordine può non essere eseguito. Le SELL MOO restano ordini market-on-open di riduzione. Gli asset IPO sono esclusi dal percorso market-on-open. Un asset attivo ma non negoziabile può essere ammesso soltanto per una vendita di chiusura coperta da una posizione long verificata. I proventi SELL non vengono conteggiati nel budget BUY.

**Limite di prezzo:** il budget BUY è stimato con il Close della barra del segnale, non con il prezzo dell'asta successiva. Un gap overnight può rendere il costo effettivo superiore alla stima; il preflight non è un limite di prezzo e non garantisce che il broker accetti ogni ordine all'apertura.

Gli identificativi batch e client-order sono deterministici. In caso di esito ambiguo di una submission, il sistema effettua la verifica prevista tramite client-order ID e non ripete alla cieca l'invio. La riconciliazione read-only degli ordini e dei batch OPG prosegue a ogni polling anche fuori dalla finestra di submission. Dopo un riavvio, il recupero è read-only: ordini attivi o parziali e posizioni non riconciliate mantengono il batch bloccante. Non vengono ritentate quantità residue e non vengono recuperate automaticamente le sessioni mancate. Un errore o una verifica inconcludente lascia il ciclo bloccato per riconciliazione; non attiva il percorso DAY come alternativa.

La modalità OPG è **Paper-only**. Richiede un account Alpaca Paper, un modello PPO compatibile già addestrato e verificato, feed dati validi e un processo STMAA attivo durante la finestra di submission. Il codice OPG non rende la strategia redditizia né abilita il trading con capitale reale.

## Struttura del repository

```text
.
├── src/
│   ├── agents/       # Componenti agent e portafoglio
│   ├── backtest/     # Componenti di backtesting
│   ├── data/         # Accesso ed elaborazione dati di mercato
│   ├── execution/    # Broker executor e protezioni di esecuzione
│   ├── strategy/     # Interfaccia dei pesi e overlay del rischio
│   ├── tests/        # Test di regressione automatizzati
│   └── utils/        # Metriche e logging SQLite
├── tests/            # Test aggiuntivi, se presenti
├── scripts/          # Script di supporto e validazione, se presenti
├── docs/             # Specifiche operative e contratti degli artefatti
├── trade.py          # Punto di ingresso per il paper trading
├── hub.py            # Hub di monitoraggio da terminale
├── train.py          # Punto di ingresso per l'addestramento
├── pyproject.toml
├── uv.lock
├── .env.example      # Modello di configurazione paper
└── ROADMAP.md        # Roadmap: verificare la coerenza con l'implementazione corrente
```

## Installazione e verifica su macOS

Su macOS 12.7 o successivo, installare una versione di Python 3.12 compatibile con l'architettura del Mac e installare `uv`. Dalla directory del repository eseguire:

```bash
uv sync --locked
uv run --locked python -c "import alpaca; import gymnasium; import numpy; import pandas; import stable_baselines3; import torch"
uv pip check
```

Per verificare anche i test e gli strumenti di sviluppo:

```bash
uv sync --locked --group dev
uv run --locked pytest src/tests tests -q
uv run --locked ruff check .
uv run --locked python doctor.py
```

`doctor.py` verifica localmente Python 3.12, presenza del lock file, disponibilità di `uv`,
dipendenze runtime e configurazione Paper. Non effettua richieste di rete, non contatta Alpaca,
non legge lo stato del broker e non invia ordini. Le credenziali mancanti sono segnalate come
avviso perché non servono per test e diagnostica locale; sono necessarie soltanto per i flussi
che si collegano ad Alpaca. Un esito senza errori non sostituisce il preflight read-only
dell'account Paper.

Il test di installazione pulita riproducibile è eseguito dalla CI su Linux, macOS Intel e
Apple Silicon: checkout pulito, installazione con `uv sync --locked --group dev`,
controllo dipendenze, suite completa e Ruff. Sul Mac locale, eseguire i comandi sopra in un
clone aggiornato; non eliminare `.env`, `logs/` o `models/` per simulare una nuova installazione.

Il progetto usa un'unica base di codice e un unico lock file per le architetture Intel (x86_64) e Apple Silicon (arm64): non esistono fork del codice per Mac vecchi e recenti. Il lock file fissa PyTorch 2.2.2 e Stable-Baselines3 2.3.2 per mantenere la compatibilità con i wheel macOS Intel; la stessa combinazione viene verificata anche su Apple Silicon. Il supporto a versioni future di PyTorch richiederà una nuova verifica esplicita della disponibilità di wheel x86_64 e dei contratti PPO. La verifica locale sopra riportata resta necessaria sul computer di destinazione. Il recupero delle sessioni orfane usa il timestamp di avvio del processo fornito dal kernel: Linux tramite `/proc` e macOS tramite `proc_pidinfo(PROC_PIDTBSDINFO)`. Se il sistema operativo non consente di verificare l'identità, STMAA lascia la sessione invariata (fail-closed).

## Integrazione continua

Il workflow GitHub Actions esegue l'intera suite di test su Linux, macOS Intel (`macos-15-intel`) e Apple Silicon (`macos-15`). I test di identità dei processi e recupero delle sessioni orfane vengono eseguiti su tutte e tre le piattaforme, senza esclusioni specifiche per macOS. CI controlla inoltre la coerenza delle dipendenze ed esegue Ruff. Il lock file è verificato con `uv lock --check`; le installazioni CI usano `uv sync --locked`.

## Stato dello sviluppo e handoff operativo (17 novembre 2026)

Questa sezione è il punto di ripartenza per chiunque continui il lavoro su STMAA. Ha priorità sulle descrizioni storiche più sotto quando riportano uno stato precedente. **Prima di eseguire un test, benchmark, training o audit, leggere questa sezione e controllare gli artefatti già prodotti. Non ripetere attività già completate solo per ricostruire il contesto.**

### Repository e ambiente

- Repository: `iFabbro/STMAA`.
- Ultimo commit verificato su `main`: `ed0e90040e90f1ac7c1973be7dd88fdfd30b8d3e` — `Fix multi-seed benchmark PPO provenance audit (#366)`; integra anche il lavoro di benchmark exposure-matched della PR #369.
- Ultime modifiche integrate: PR #363–#369. In particolare: handoff e registro anti-duplicazione (#363); audit metodologico multi-seed con selezione seed esplicita (#364–#365); verifica della provenienza PPO e coerenza dei report, più comparator Equal Weight exposure-matched per seed (#366, che include #369). Le PR #367–#368 riguardano il publisher assistant-exchange, non il runtime di trading.
- Ambiente locale di riferimento: iMac Intel x86_64, macOS 12.7.6; checkout `~/Projects/STMAA`; Python di progetto 3.12.13 tramite `.venv`/uv. Il Python globale può essere 3.13 e non va usato per eseguire il progetto.
- La configurazione locale resta Paper-only. `.env`, `logs/` e `models/` sono dati locali: non sovrascriverli o eliminarli durante prove di installazione.

### Registro delle verifiche già eseguite

| Area | Stato verificato | Evidenza / limite |
|---|---|---|
| Dashboard Textual su macOS Intel | Verificata localmente | Avvio diretto e tramite `hub.py`, navigazione e quattro sezioni Overview, Portfolio, Orders & Executions e System. Verifica eseguita sul checkout indicato nel registro precedente; non equivale a una verifica live del broker. |
| PPO walk-forward multi-seed | Eseguito | 11 fold temporali, seed 42/7/21, 33 run seed/fold; 7 ticker, 2.206 osservazioni fino al 6 novembre 2026. Ogni fold è indipendente e parte da liquidità: non concatenare i fold in una curva composta. |
| Benchmark PPO | Eseguito | Confronti equal-weight buy-and-hold, equal-weight exposure-matched (dove la traccia PPO è disponibile), SPY 50% e SPY 100%. Le simulazioni sono per fold indipendenti. |
| Allineamento benchmark del seed 42 | Verificato | Confronto puntuale su 11 fold: date, `next_bar_open`, fingerprint dei dati, metriche PPO e timeseries embedded coincidono tra il report PPO seed 42 e il report benchmark matched. |
| Audit metodologico benchmark, seed 42 | **PASS in memoria** | Chiamando `audit_reports()` con il report multi-seed normalizzato sul seed 42 e il benchmark matched, tutti i 7 controlli risultano PASS su 11 fold: parametri/costi/cap, allineamento fold, identità dati, entry execution, traccia esposizione PPO, traccia benchmark e valori costi. |
| Auditor metodologico multi-seed | Implementato; esecuzione sui report locali da completare | PR #364–#366 supportano i formati multi-seed, selezione esplicita del seed e verifica delle mirror PPO. PR #369 aggiunge l'audit di coerenza e il comparator Equal Weight exposure-matched distinto per seed. Nessun esito PASS sui file locali viene assunto finché gli auditor non sono eseguiti sui JSON effettivi. |
| Soak read-only Alpaca Paper | Da ripetere | Un tentativo precedente si è interrotto dopo 22 cicli per il difetto temporale delle barre giornaliere, senza inviare ordini. La correzione è stata integrata (#342); il soak sul codice corretto non risulta ancora completato. |
| Preflight Alpaca Paper zero-write | Implementato | PR #361 aggiunge un diagnostico read-only distinto dal preflight di `trade.py`; non è un soak, non avvia il ciclo e non invia ordini. Va eseguito localmente solo se serve una nuova fotografia dello stato Paper. |
| Raccolta PPO shadow prospettica | Non avviata | Registro PRE-FREEZE; PR #333 resta aperta in Draft. Nessuna raccolta va attivata senza chiusura dei gate e autorizzazione esplicita. |

### Audit benchmark: dettaglio indispensabile per la ripresa

Artefatti di riferimento generati localmente:

- `reports/benchmark_methodology_audit_20261110.json`
- `reports/ppo_benchmark_comparison_matched_20261110.json`
- `reports/ppo_benchmark_comparison_spy_20261110.json`
- `reports/ppo_benchmark_multiseed_20261110_42_7_21.json`
- `reports/ppo_benchmark_multiseed_audit.txt`
- `reports/ppo_benchmark_multiseed_summary.txt`
- `reports/ppo_walk_forward_multiseed_20261110_42_7_21.json`
- `reports/ppo_walk_forward_timeseries_9fold_20261110.json`

La verifica conclusiva già svolta per il seed 42 ha prodotto:

```text
Status: PASS
Fold count: 11
cost_and_cap_parameters: PASS
fold_alignment: PASS
execution_data_identity: PASS
entry_execution: PASS
ppo_daily_exposure_trace: PASS
benchmark_daily_exposure_trace: PASS
reported_cost_values: PASS
```

**Interpretazione corretta:** questo è un PASS dell'auditor ottenuto in memoria fornendo i fold del seed 42 nel formato single-seed atteso. Non è un PASS del report multi-seed nativo. Il file `reports/benchmark_methodology_audit_20261110.json` resta quello precedente e può ancora riportare INCONCLUSIVE. Inoltre, il campo `execution_data_identity` nei fold del report benchmark matched risulta ancora INCONCLUSIVE: non confondere tale campo con il controllo di fingerprint indipendente che ha verificato la corrispondenza 11/11.

Il PASS indica che i controlli implementati dispongono di evidenza coerente nei report. Non dimostra fill equivalenti, market impact identico, redditività futura o validazione operativa Alpaca.

### Prossimo lavoro effettivo

**Priorità immediata: eseguire gli auditor sui report multi-seed già esistenti.** Non riaddestrare PPO e non rigenerare walk-forward o benchmark in questa fase.

Report locali attesi:

- `reports/ppo_walk_forward_multiseed_20261110_42_7_21.json`
- `reports/ppo_benchmark_multiseed_20261110_42_7_21.json`

Prima verificare che i file esistano e registrare gli hash; poi eseguire l'auditor di coerenza benchmark e l'auditor metodologico separatamente per i seed 42, 7 e 21:

```bash
cd ~/Projects/STMAA
git status --short --branch
test -s reports/ppo_walk_forward_multiseed_20261110_42_7_21.json
test -s reports/ppo_benchmark_multiseed_20261110_42_7_21.json
shasum -a 256 \
  reports/ppo_walk_forward_multiseed_20261110_42_7_21.json \
  reports/ppo_benchmark_multiseed_20261110_42_7_21.json

uv run --locked python benchmark_results_audit.py \
  --benchmark-report reports/ppo_benchmark_multiseed_20261110_42_7_21.json \
  --output reports/benchmark_results_consistency_audit_20261117.json

for seed in 42 7 21; do
  uv run --locked python benchmark_methodology_audit.py \
    --ppo-report reports/ppo_walk_forward_multiseed_20261110_42_7_21.json \
    --benchmark-report reports/ppo_benchmark_multiseed_20261110_42_7_21.json \
    --ppo-seed "$seed" \
    --output "reports/benchmark_methodology_audit_20261117_seed${seed}.json"
done
```

Interpretazione degli esiti:

- `PASS`: tutti i controlli implementati dispongono di evidenza coerente nel report.
- `FAIL`: mismatch concreto; fermarsi e investigare il campo/seed/fold indicato.
- `INCONCLUSIVE`: evidenza assente o ambigua; non equivale a PASS.
- Per il comparator `ppo_exposure_matched_equal_weight_by_seed`, verificare che la copertura attesa sia 33/33 (3 seed × 11 fold) e che ogni seed abbia usato la propria traccia giornaliera. Se il report benchmark è precedente alla PR #369 e non contiene questi campi, l'auditor deve restare INCONCLUSIVE: non rigenerare il benchmark in questo passaggio. Un'eventuale ricalcolazione del solo confronto, senza training PPO, richiederà una decisione separata.

I report JSON di input non devono essere modificati. Conservare gli hash prima/dopo e archiviare i nuovi output di audit separatamente. Il comando benchmark comparison non fa parte di questa fase.

### Regole anti-duplicazione per l'assistente successivo

- Prima di lanciare un comando, cercare nel README, in `reports/`, nei test e nella cronologia PR l'evidenza di un'esecuzione equivalente.
- Non ripetere training PPO, walk-forward o benchmark se codice, dati, parametri e artefatti di input non sono cambiati e i report esistenti sono integri.
- Non ripetere la suite completa per una modifica documentale o per un controllo già coperto da CI verde sullo stesso commit. Per modifiche di codice, partire dai test mirati e verificare poi i gate CI pertinenti.
- Un risultato ottenuto in memoria non aggiorna automaticamente i report su disco: registrare sempre separatamente esito, file aggiornato e commit.
- Per ogni nuova verifica annotare comando esatto, commit, piattaforma, data, risultato e percorso dell'artefatto.
- Prima di affermare che una verifica è completata, controllare il risultato effettivo; non dedurlo dal fatto che una PR sia stata aperta o che il codice sia stato integrato.

### Vincoli permanenti

- Ogni modifica al codice o alla documentazione passa da branch e PR dedicati; nessuna modifica diretta a `main`.
- Verificare lo stato CI della PR prima del merge; il merge richiede autorizzazione dell'utente.
- Paper-only, fail-closed. Nessun ordine, cancellazione, sostituzione o chiusura broker senza autorizzazione esplicita per un test Alpaca Paper controllato. Nessun live trading.
- La dashboard non deve chiamare il broker né eseguire operazioni di trading. Le fonti UI restano read-only; le scritture di audit di sessione restano in `hub.py`.
- STMAA resta alpha/sperimentale: test e benchmark non certificano sicurezza, redditività o idoneità alla produzione.

### Modello PPO pronto per l'utente: requisito e stato reale

L'obiettivo di prodotto è distribuire STMAA con un modello PPO già addestrato e validato, senza richiedere all'utente finale di eseguire training. **Questo requisito non va confuso con lo stato attuale del repository:** il README e il repository non documentano ancora un artefatto pre-addestrato versionato e distribuito ai nuovi checkout. Il modello content-addressed presente nell'ambiente locale di sviluppo non equivale a un artefatto pubblicato e installabile da ogni utente. La strategia di packaging, distribuzione, verifica SHA-256/manifest e aggiornamento del modello resta da definire e validare prima di dichiarare il prodotto pronto all'uso.

## Aggiornamenti recenti (9–17 novembre 2026)

Le PR recenti hanno consolidato soprattutto cinque aree:

- **Feed e qualità dei dati (#323–#324):** telemetria read-only per età, spread e qualità delle quotazioni Alpaca, con distinzione tra feed SIP storico e quote feed; correzione del limite temporale delle barre giornaliere SIP durante la sessione NYSE.
- **Ricerca PPO e benchmark (#325–#332):** baseline equal-weight e SPY allineate all'esecuzione al successivo Open; valutazioni multi-seed e walk-forward; tracce giornaliere di esposizione, turnover e costi; fingerprint dei dati di esecuzione; audit metodologico fail-closed; metriche di performance condivise. I fold restano simulazioni indipendenti e i risultati descrittivi non costituiscono una prova di redditività né un track record continuo.
- **PPO shadow e provenienza (#334–#336):** collector virtuale esplicitamente attivabile e read-only, benchmark SPY a capitale comparabile, ledger append-only con hash chain, snapshot immutabili degli input e valutatore prospettico basato su 252 sessioni NYSE di rendimento. L'attivazione resta separata dal normale ciclo di trading e subordinata ai gate di freeze.
- **Hardening operativo (#337–#341):** confine broker executor Paper-only verificato prima delle mutazioni; Doctor locale offline e senza chiamate di rete; correzione della classificazione della storia del risk peak; distinzione esplicita tra accesso broker read-only e mutazioni broker disabilitate nei metadati del soak.
- **Dashboard e documentazione (#342–#348):** correzione del limite temporale post-chiusura per le barre giornaliere Alpaca (#342); specifica e redesign visuale della dashboard (#344–#345); classificazione indipendente della freschezza dello snapshot posizioni (#347); aggiornamento dello screenshot nel README (#348). Le modifiche dashboard restano locali/read-only e non introducono chiamate o mutazioni broker.
- **Dashboard, sicurezza e qualità del codice (#349–#357):** correzione della documentazione di stato, navigazione mouse e composizione Overview, copertura dei componenti dashboard, remediation dipendenze con compatibilità macOS Intel, workflow Bandit/Semgrep, coverage multipiattaforma e pin immutabili delle GitHub Actions.
- **Qualità dati, rischio e onboarding (#358–#362):** separazione tra qualità di monitoraggio delle quote e idoneità all'esecuzione; consolidamento dei limiti di esposizione/posizione/drawdown; wizard First-Run Paper offline; diagnostica Alpaca Paper zero-write; caricamento dotenv differito al runtime e isolamento dei test Doctor dall'ambiente ereditato.
- **Audit multi-seed e benchmark (#363–#369):** handoff verificato e regole anti-duplicazione (#363); supporto metodologico ai report multi-seed `runs[]` e `seed_results[]`, con selezione esplicita del seed (#364–#365); verifica della provenienza PPO e delle mirror per seed/fold (#366); audit read-only dei contatori, confronti, medie/mediane e copertura exposure-matched, con Equal Weight exposure-matched calcolato usando la traccia specifica di ogni seed (#369, integrata nel merge #366). I report sorgente non vengono riscritti dall'auditor.

### Stato della validazione operativa

La validazione end-to-end su Alpaca Paper è ancora in corso. Un precedente tentativo di soak read-only si è interrotto dopo 22 cicli per una discrepanza temporale tra il limite della richiesta delle barre giornaliere e il calcolo dell'ultima sessione NYSE completata; in quel tentativo non sono stati inviati ordini. La correzione è stata integrata in `main` con la PR #342 (merge commit `e684f82c7ac68e798129599b266ce3a6780d08f0`). **Il soak deve ancora essere ripetuto sul codice corretto**: il merge della correzione non dimostra da solo il superamento della verifica operativa.

La raccolta shadow prospettica resta non avviata e subordinata ai gate del registro di freeze. Qualsiasi test che comporti mutazioni broker richiede un'autorizzazione separata ed esplicita. Nessun risultato di test, benchmark, dashboard o soak deve essere interpretato come autorizzazione al live trading.

## Analisi statica di sicurezza

Il workflow dedicato `Security Static Analysis` esegue Bandit e Semgrep su ogni pull request, sui push a `main` e con scansione settimanale. Le versioni CLI sono fissate nel workflow per rendere riproducibili i controlli.

- **Bandit** analizza il codice Python del repository, escludendo le directory di test e gli ambienti virtuali; la CI blocca finding con severità e confidenza almeno medie.
- **Semgrep** applica le regole locali in `.semgrep/stmaa-security.yml`, limitate a pattern ad alta confidenza: subprocess con `shell=True`, `os.system/os.popen`, esecuzione dinamica tramite `eval/exec` e deserializzazione pickle. I finding configurati come errori bloccano la CI.
- Le regole non escludono indiscriminatamente interi moduli applicativi. I test sono esclusi da Bandit per evitare rumore da fixture e assert; Semgrep segue le regole di ignore del repository.
- Un finding va verificato nel contesto prima di essere corretto o soppresso. Le eccezioni devono essere puntuali, motivate e accompagnate da test quando pertinenti.

Queste scansioni statiche sono controlli euristici: un esito pulito non dimostra l'assenza di vulnerabilità e non sostituisce code review, test, audit delle dipendenze o verifica operativa. Nessuna scansione autorizza operazioni broker o modifica il confine Paper-only.

## Contribuire

1. Creare un branch dedicato e circoscritto.
2. Aggiungere o aggiornare test di regressione per le modifiche comportamentali, in particolare per gli scenari di errore e quelli in cui non deve essere inviato alcun ordine.
3. Eseguire la suite di test e i controlli pertinenti del repository.
4. Aprire una pull request e verificare i risultati CI prima del merge.

## Licenza

STMAA è distribuito con licenza MIT. Consultare il file [`LICENSE`](LICENSE) per i dettagli.


## Broker execution lock and deployment boundary

The broker execution lock is a host-local operating-system file lock (fcntl.flock).
All cooperating STMAA processes on the same host must use the same
STMAA_BROKER_STATE_FILE path on a filesystem that supports advisory locks.
At startup, configured paths are resolved to canonical absolute paths (including
existing symlink targets) before lock-file names are derived.
Lock acquisition is bounded by STMAA_BROKER_EXECUTION_LOCK_TIMEOUT_SECONDS
(default: 30 seconds); timeout or lock errors stop the execution cycle before
broker mutations are attempted.

This mechanism is not a distributed lock and does not coordinate independent
hosts or filesystems. During the current single-host development phase, operate
only one active STMAA executor per broker account. Multi-host execution requires
a separately designed centralized execution service and is outside the scope of
the current implementation.


## Dependency security and macOS Intel compatibility

The dependency set keeps a dedicated compatibility path for macOS Intel (x86_64): PyTorch 2.2.2 is retained on that platform because newer upstream PyTorch releases do not provide a supported macOS Intel wheel. This preserves local installation and validation on older Intel Macs, including macOS 12.7, but **does not remediate the known PyTorch 2.2.2 security advisories on that platform**. Treat the Intel environment as a local, experimental validation target; do not expose it to untrusted model artifacts or untrusted input, and do not use it as a production or live-trading deployment.

Other supported platforms use PyTorch 2.13.0, while Soup Sieve is constrained to the patched 4.x line. Platform-specific dependency resolution must remain represented in the committed `uv.lock`; CI must verify the lock and exercise Linux, macOS Intel, and macOS ARM separately. The Intel exception is an explicit residual-risk acceptance, not a claim that the dependency is vulnerability-free.


## Guided First-Run Setup

For a new local checkout, install the locked environment first:

```bash
uv sync --locked --group dev
```

Create the local Paper configuration with the offline wizard:

```bash
uv run python first_run.py
uv run python doctor.py
```

The wizard requests the Alpaca Paper API key and secret through masked terminal prompts.
It creates `.env` only when that file does not already exist, uses restrictive
permissions (`0600` on POSIX), and refuses to overwrite an existing configuration.
The credentials are never printed. If `.env` already exists, preserve it and edit it
deliberately rather than rerunning the wizard.

The wizard is **offline**: it does not authenticate credentials, contact Alpaca, inspect
an account, or submit/cancel/replace/close orders. The Doctor is also offline. It resolves
configuration using the same precedence as the runtime loader: process environment
variables override values in `.env`; absent Paper mode and endpoint values use the
runtime's Paper-only defaults. An exported non-Paper mode or custom endpoint remains a
failure. Credential presence is checked locally, not authenticated.

This setup change does not modify the trading engine, executor, PPO model, or any risk
limits. A successful Doctor result is not a broker preflight and does not authorize
Paper order submission or live trading.


## Zero-write Alpaca Paper preflight

After the dedicated diagnostic change is merged and pulled locally, run:

```bash
uv run python paper_preflight.py
```

The command is separate from `trade.py` and the normal trading lifecycle. It uses the effective local `.env`/process environment, authenticates only against the exact Alpaca Paper endpoint, and performs account, positions, and open-orders GET requests. It reads `logs/execution/broker_state.json` (or the path selected by `STMAA_BROKER_STATE_FILE`) without creating, updating, or recovering it. It does not open SQLite, initialize the PPO agent, start a trading session, or call order submission, cancellation, replacement, or close operations.

The JSON report contains five independent controls: (1) Paper authentication and account identity; (2) account availability, currency, equity, cash and buying power; (3) positions snapshot integrity; (4) open orders and aggregate gross notional, including active BUY reserves, against the hard USD 1,000 cap; and (5) reconciliation of local unresolved/confirmed submissions, OPG batches, emergency-liquidation state and broker open orders. Any missing, malformed, unpriceable, mismatched, unresolved or over-cap state is reported as `BLOCKED`. An active broker open order also blocks the final reconciliation result.

The diagnostic is read-only by design. It does not authorize order submission and does not make STMAA live-trading ready. Run it from the local checkout that contains the actual credentials and broker state; GitHub Actions cannot validate those local inputs.


## Multi-seed benchmark consistency audit

The consistency auditor reads an existing multi-seed benchmark JSON report without
downloading market data or rerunning simulations:

```bash
uv run --locked python benchmark_results_audit.py \
  --benchmark-report reports/ppo_benchmark_multiseed_YYYYMMDD.json \
  --output reports/benchmark_results_consistency_audit_YYYYMMDD.json
```

It reconciles the per-seed win counters against fold-level comparisons, checks
the duplicated `seed_results` / `seed_comparisons` / `ppo_by_seed` evidence,
and recomputes the shared benchmark mean and median returns. A concrete mismatch
is reported as FAIL; missing per-seed exposure-matched evidence remains
INCONCLUSIVE rather than being treated as parity.

For multi-seed comparisons, the exposure-matched equal-weight baseline is now
calculated separately from each seed's own PPO target-exposure trace. The
shared passive benchmarks remain evaluated once per market fold. The new
`ppo_exposure_matched_equal_weight_by_seed` fold field and per-seed summary
fields must be regenerated by running the benchmark comparison command against
the existing PPO report and the same historical market data. This operation
does not retrain PPO, but it does recalculate the benchmark report.
