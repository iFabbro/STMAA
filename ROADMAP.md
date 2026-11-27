# STMAA — Stato del progetto e lavoro residuo

**Ultimo allineamento:** 27 novembre 2026  
**Repository:** `iFabbro/STMAA`  
**Riferimento `main`:** `66fb4466958728bfb385153725c7dc75efde8faf`  
**Documentazione primaria di uso e configurazione:** [README.md](README.md)  
**Registro di evidenza audit:** [A08 Evidence Dossier Register](docs/audit/A08_EVIDENCE_DOSSIER_REGISTER.md)

## Stato e perimetro

STMAA è un framework sperimentale/alpha per ricerca quantitativa, PPO, backtesting, controlli del rischio e integrazione Alpaca Paper. Non è certificato, non ha dimostrato redditività robusta e non è autorizzato per il trading live. Il superamento di CI, test o audit circoscritti non costituisce una garanzia di sicurezza assoluta o di performance futura.

Il sistema ha raggiunto una base tecnica ampia: pipeline dati, contratti PPO, backtesting e benchmark, risk overlay, broker executor Paper-only, percorsi DAY/OPG, persistenza e riconciliazione, dashboard locale e raccolta shadow read-only. La presenza di questi componenti non significa che il prodotto sia completo o pronto per utenti esterni.

## Stato degli audit

| Audit | Stato | Valutazione | Residuo principale |
|---|---|---:|---|
| A01 — Software Engineering & Architecture | CLOSED — PASS WITH RESIDUAL RISKS | — | Report originale e mappa finding/remediation/retest non ancora recuperati nel dossier A08. |
| A02 — Security & Supply Chain | CLOSED — PARTIAL | 6,5/10 | A02-DEP-01 aperto: PyTorch 2.2.2 su macOS Intel non è stato rimediato. |
| A03 — AI Governance & Model Risk | CLOSED — PASS WITH RESIDUAL RISKS | 8/10 | Holdout PRE-FREEZE / NOT ACTIVE; raccolta non autorizzata. |
| A04 — Quantitative & Statistical Validation | CLOSED — PASS WITH RESIDUAL RISKS | 7/10 | Seed sensitivity, nessun vantaggio economico robusto dimostrato, costi elevati; comparator exposure-matched multi-seed INCONCLUSIVE. |
| A05 — Data Governance & Provenance | CLOSED — PASS WITH RESIDUAL RISKS | 7,5/10 | Provenienza persistita ma non centralizzata; i metadati non provano da soli la verità della fonte upstream. |
| A06 — Execution Safety & Broker Boundary | CLOSED — PASS WITH RESIDUAL RISKS | 8/10 | Lock host-local, race guard-to-SDK, OPG senza catch-up automatico; nessun fault-injection broker reale o retest locale completo su macOS 12.7.6. |
| A07 — Operational Resilience & Reliability | CLOSED — PASS WITH RESIDUAL RISKS | 8/10 | Soak read-only di 48 ore completato; report automatico INCOMPLETE per il predicato coverage e gap heartbeat massimo di 165.285 secondi. |
| A08 — Audit Readiness & Evidence Dossier | IN PROGRESS | — | Consolidare evidenze, finding, remediation, retest e rischi residui; preservare i gap non dimostrati. |

I dettagli e le fonti per ciascun audit sono nel registro A08 e nei report originali disponibili. Dove il report originale o il retest non è recuperato, il registro lo dichiara come gap: non inferire una catena di evidenza mancante.

## Risultati quantitativi PPO: limiti

Il benchmark multi-seed copre 3 seed (42, 7, 21), 11 fold per seed e 33 esecuzioni PPO. I risultati sono descrittivi e mostrano sensibilità al seed, costi di negoziazione elevati e assenza di un vantaggio economico robusto dimostrato. Il comparator exposure-matched equal-weight multi-seed resta INCONCLUSIVE dove manca il payload di risultato; il confronto matched single-seed del seed 42 è una verifica separata e non può essere estesa agli altri seed.

Il campione storico non autorizza ottimizzazione opportunistica, scelta retrospettiva del seed o dichiarazioni di redditività. Nessun tuning/retraining PPO va avviato senza un protocollo approvato e una motivazione basata su evidenza.

## Stato operativo e sicurezza

- Perimetro corrente: Alpaca Paper, fail-closed e nessun live trading.
- Limite nozionale lordo operativo documentato: 1.000 USD aggregati DAY/OPG, con posizioni e riserve BUY pendenti incluse.
- Il broker execution lock è host-local: è ammesso il modello single-host/single-active-executor; il coordinamento multi-host non è implementato.
- Le mutazioni broker non fanno parte del lavoro documentale A08. Ogni eventuale test Paper con ordini richiede autorizzazione esplicita separata.
- La dashboard usa provider locali read-only; le scritture di audit di sessione restano nel lifecycle di `hub.py`.
- Holdout A03: PRE-FREEZE / NOT ACTIVE / COLLECTION NOT AUTHORIZED.

### Soak A07

La Roadmap di progetto registra il run read-only di 48 ore `8b6f062e-472f-4e3b-88d9-b692763c61a5`, sul commit `001a66cddf134ea7c5344ff292a1c08d0458c198`, con 2.641 heartbeat e 2.641 shadow observations. Il report automatico resta `INCOMPLETE` per il criterio coverage; due retry market-data sono stati recuperati e l'integrità SQLite è stata riportata `ok`. A07 è stato chiuso con rischi residui secondo la Roadmap di progetto. Questi dati sono riportati dalla Roadmap/handoff: non sono stati rieseguiti durante questo riallineamento. Il report storico A07 relativo al tentativo precedente va preservato, non sovrascritto.

## Gap di prodotto ancora aperti

Questi punti sono limiti concreti da distinguere dalla chiusura degli audit:

1. **Distribuzione del modello PPO:** il repository non distribuisce ancora ai nuovi checkout un artefatto PPO pre-addestrato, versionato e verificato. Il modello presente nell'ambiente locale di sviluppo non è un pacchetto installabile per ogni utente.
2. **Rischio dipendenze su macOS Intel:** PyTorch 2.2.2 resta un finding aperto (A02-DEP-01); la compatibilità della piattaforma non equivale alla risoluzione dell'advisory.
3. **Evidenza A08 incompleta:** report A01/A02 e pacchetto artefatti del run A07 non sono ancora tutti collegati/recuperati nel dossier. Le lacune devono restare esplicite finché non è disponibile la fonte primaria.
4. **Validazione quantitativa:** i risultati non dimostrano un edge robusto; il comparator exposure-matched multi-seed è inconclusivo. Holdout prospettico non attivo e non autorizzato.
5. **Limiti operativi A06:** coordinamento multi-host assente, nessun test fault-injection contro broker reale e nessun retest locale completo sul Mac Intel macOS 12.7.6.
6. **Runbook e operatività di prodotto:** procedure di distribuzione/aggiornamento del modello, gestione backup/restore e gestione incidenti devono essere definite prima di un impiego esterno affidabile. Non sono state validate come capacità complete dal presente riallineamento.

Questi gap non sono una nuova audit campaign né autorizzano automaticamente nuovi workstream. La loro priorità e il relativo perimetro vanno decisi dopo il completamento di A08.

## Prossime azioni approvate

1. **Completare A08:** mantenere il registro aggiornato con collegamenti verificabili a report, finding, PR, commit e retest; non colmare i gap con test o documenti inventati.
2. **Chiudere il riallineamento documentale:** mantenere coerenti README, questa roadmap e registro A08 con lo stato effettivo del repository.
3. **Dopo A08:** presentare all'utente una proposta ordinata per i gap di prodotto, separando blocker di sicurezza, prerequisiti di distribuzione e miglioramenti opzionali. Nessun nuovo sviluppo fuori dal perimetro approvato prima della decisione.

## Regole permanenti

- Ogni modifica passa da branch dedicato e PR; nessuna modifica diretta a `main`.
- Verificare diff e controlli CI richiesti prima del merge. Il merge richiede autorizzazione esplicita dell'utente.
- Non dichiarare CI, test, audit o verifiche superati se il relativo risultato non è stato osservato.
- Preservare `.env`, runtime, modelli, cap, configurazione protetta e holdout.
- Nessun Live Trading e nessuna mutazione broker senza autorizzazione esplicita per uno specifico test Alpaca Paper controllato.
