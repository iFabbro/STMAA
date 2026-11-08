# STMAA — Stato del progetto e lavoro residuo

**Ultimo riesame documentale:** 8 novembre 2026  
**Repository:** `iFabbro/STMAA`  
**Documento operativo principale:** [README.md](README.md)

## Scopo e stato

STMAA è un framework sperimentale per ricerca, training PPO, backtesting, controlli di rischio e integrazione Alpaca Paper. Il repository non rappresenta un prodotto certificato, non dimostra redditività e non deve essere usato con capitale reale.

Questa roadmap sostituisce le note storiche di giugno 2026. Descrive lo stato verificato nel repository al momento dell'ultimo riesame; il README resta la fonte primaria per installazione, configurazione e uso. Per i dettagli delle attività residue e delle relative evidenze consultare [STMAA_RESIDUAL_WORK_REGISTER.md](docs/STMAA_RESIDUAL_WORK_REGISTER.md).

## Componenti presenti

| Area | Implementazione presente | Limite da tenere esplicito |
|---|---|---|
| Market data | Adapter Alpaca, processing, feature engineering e contratti versionati dei dati | La correttezza del feed e la continuità operativa richiedono verifica nell'ambiente configurato |
| PPO | Training, manifest e metadati degli artefatti, hash e controlli di compatibilità | Un artefatto caricabile non dimostra validità statistica o redditività |
| Backtesting | Ambiente e motore di backtest con costi e vincoli di esecuzione modellati | I risultati dipendono da dati, ipotesi e protocollo; non sono previsioni |
| Risk management | Overlay, limiti nozionali, circuit breaker e blocchi fail-closed | I limiti implementati non sostituiscono il monitoraggio e la verifica Paper |
| Broker execution | Executor Alpaca con stato persistito, identificativi idempotenti, gestione ambiguità, ordini pendenti/parziali e riconciliazione | Ambito corrente: singolo host e Paper; nessun coordinamento distribuito |
| DAY / OPG | Percorsi separati, preflight e recupero persistito | Non equivalgono a validazione operativa prolungata |
| Persistence | Stato esecuzione su file e monitoraggio SQLite | Backup, restore e runbook operativi restano da completare |
| Terminal dashboard | Overview, Portfolio, Orders & Executions e System, basati su fonti locali | È una vista locale di evidenze persistite, non un terminale broker live |
| Paper soak | Monitor, shadow runner, provider read-only, stop controller e report | Il soak continuo di 48 ore non risulta ancora eseguito e accettato |

## Installazione e verifiche

Il progetto dichiara Python `>=3.12,<3.13` e usa `uv.lock` per la risoluzione riproducibile delle dipendenze. I comandi canonici sono:

```bash
uv sync --locked
uv sync --locked --group dev
uv run --locked pytest src/tests tests -q
uv run --locked ruff check .
```

La CI principale esegue la suite su Linux, macOS Intel e Apple Silicon, oltre a Ruff e al controllo del lock file. Per i passaggi completi di installazione, configurazione e preflight consultare il README; non duplicare qui istruzioni che possono divergere.

## Priorità operative

1. **Consolidamento tecnico:** allineare i workflow manuali Alpaca a Python 3.12 e `uv.lock`; applicare protezioni effettive al branch `main`; completare la validazione semantica dei record mostrati dalla dashboard; mantenere documentazione e register coerenti.
2. **Verifica locale della dashboard:** eseguire installazione pulita e prova su macOS supportato, verificando navigazione, refresh manuale, fonti mancanti/obsolete/invalid e assenza di chiamate broker dalla UI.
3. **Paper preflight e soak:** controllare configurazione, modello compatibile, identità account Paper e stop conditions; solo dopo avviare un nuovo run continuo di 48 ore secondo il protocollo in `docs/PAPER_SOAK_MONITOR.md`. Un test breve non sostituisce il requisito di 48 ore.
4. **Onboarding downloader:** rendere ripetibile il percorso clone → installazione → configurazione → training/verifica artefatto → diagnostica → preflight read-only → monitoraggio.
5. **Qualità quantitativa PPO:** definire un protocollo di valutazione out-of-sample con benchmark, costi/turnover, drawdown, stabilità per periodi e seed e criteri espliciti di rifiuto/promozione.

## Limiti e lavoro differito

- Nessuna autorizzazione implicita a usare capitale reale o a dichiarare il sistema pronto per il live trading.
- Il broker execution lock è host-local; l'esecuzione multi-host richiede un servizio coordinatore separato ed è differita.
- Il Paper soak di 48 ore è una verifica operativa ancora aperta.
- Mancano ancora definizioni e prove complete per alert esterni, backup/restore, rotazione credenziali, ownership/escalation e gestione incidenti.
- La dashboard non interroga il broker e non deve essere descritta come fonte di stato live.

## Regole di modifica

Ogni intervento deve essere isolato in un branch e una pull request dedicati. Nessuna modifica diretta a `main`. Prima del merge devono risultare verdi i controlli CI, test, lint e secrets scan applicabili. Le verifiche che coinvolgono Alpaca devono rispettare il perimetro Paper autorizzato; le prove read-only non devono inviare, cancellare, sostituire o chiudere ordini.
