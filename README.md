# HLX Watch

Strumento di data mining per Steam che sorveglia i giochi Valve su Source 2 (Counter-Strike 2, Dota 2,
Deadlock, Half-Life: Alyx) e avvisa quando compaiono riferimenti nuovi a **HLX**, il presunto nome in
codice di Half-Life 3: stringhe, ConVar, entità FGD, percorsi di file, build Steam e app Valve nuove.

Usa **solo fonti pubbliche e legali**:

| Fonte | Cosa dà | Come |
|---|---|---|
| Repository GitHub `SteamTracking/GameTracking-*` (ex `SteamDatabase`) | file estratti e dump di stringhe/ConVar/schemi per ogni build di CS2, Dota 2, Deadlock, Alyx, SteamVR | REST API ufficiale di GitHub, diff di ogni commit |
| Repository `SteamTracking/SteamTracking` e `SteamTracking/Protobufs` | file del client Steam (stringhe, localizzazione, flag) e messaggi protobuf | stessa sorgente GitHub |
| Steam PICS con login **anonimo** (libreria `steam`) | build id per branch, depot, metadati app, elenco app cambiate | sottoprocesso isolato, nessun account |
| Steam Web API (opzionale, chiave gratuita) | lista completa delle app per trovare quelle nuove | `IStoreService/GetAppList` |
| File locali dei giochi che possiedi (opzionale) | lista dei file nei VPK, file di testo, stringhe nei binari | `Source2Viewer-CLI` di ValveResourceFormat |

Non fa scraping di steamdb.info, non usa account o depot privati, non aggira protezioni.

---

## Architettura

```
hlx-watch/
├── config.example.yaml      # configurazione completa commentata (copia in config.yaml)
├── .env.example             # segreti: GITHUB_TOKEN, DISCORD_WEBHOOK_URL, TELEGRAM_*, STEAM_WEB_API_KEY
├── requirements.txt
├── Dockerfile, docker-compose.yml
├── tests/                   # pytest: analizzatori e storage
└── hlxwatch/
    ├── cli.py               # comandi: init, scan, backfill, daemon, report, serve, search, test-notify
    ├── config.py            # YAML + .env
    ├── models.py            # BuildInfo, Finding
    ├── scheduler.py         # daemon: scansione ogni N minuti + report giornaliero
    ├── core/pipeline.py     # orchestrazione sorgenti -> analisi -> storage -> deduplica -> notifiche
    ├── sources/
    │   ├── github_tracking.py   # commit GameTracking, patch e diff manuale dei file grandi
    │   ├── steam_pics.py        # build/branch/depot/metadati via PICS (worker in sottoprocesso)
    │   ├── steam_pics_worker.py # il worker: login anonimo, stampa JSON
    │   ├── steam_webapi.py      # app nuove (lista completa con chiave, euristica appid senza)
    │   └── local_files.py       # VPK e binari locali con Source2Viewer-CLI
    ├── analyzers/
    │   ├── keywords.py          # regex configurabili, punteggio, token
    │   ├── diff_analyzer.py     # parsing patch unificate, diff per insiemi, accoppiamento righe cambiate
    │   └── enrich.py            # entità FGD, ConVar, percorsi
    ├── storage/db.py        # SQLite: builds, findings, seen_strings, steam_apps, steam_builds, snapshots
    ├── notifiers/           # Discord (webhook, embed) e Telegram (bot, HTML)
    ├── reports/daily.py     # report Markdown + HTML per giorno/periodo
    └── dashboard/           # FastAPI + Jinja2: timeline, ricerca, dettaglio, build, app Steam, API JSON
```

### Flusso di una scansione

1. **GitHub**: per ogni repo legge i commit nuovi dopo il cursore salvato (o dopo una data per il backfill),
   scarica il dettaglio con tutti i file (pagine da 300, massimo 3000 per commit), analizza le patch.
   Se GitHub non fornisce la patch (file enorme, es. `pak01_dir.txt` con 137.000 righe) scarica le due
   versioni da `raw.githubusercontent.com` e confronta le righe localmente; i download vanno in parallelo
   (`github.fallback_workers`, default 8): un commit con 1300 file senza patch richiede circa un minuto.
2. **Steam PICS**: login anonimo in un sottoprocesso; per ogni app osservata registra le build per branch,
   salva uno snapshot appiattito di tutte le info app e confronta con il precedente; chiede l'elenco delle
   app cambiate dall'ultimo change number.
3. **App nuove**: gli appid mai visti e superiori alla "frontiera" (o assenti dalla lista completa, con la
   chiave Web API) vengono interrogati via PICS: developer/publisher Valve, nome sospetto, info private o
   parole chiave fanno salire il punteggio.
4. **File locali** (se abilitati): hash del VPK, lista file, estrazione dei file di testo, stringhe dai
   binari, tutto confrontato con lo snapshot precedente.
5. Ogni riga interessante diventa un **finding** con punteggio, keyword, token, contesto e tipo di modifica
   (`added`, `removed`, `changed`, `file_added`, `file_removed`, `file_renamed`, `build`, `new_app`).
6. **Deduplica**: il token (es. `hlx_fsr3_min_reactiveness`) o la riga normalizzata viene hashato in
   `seen_strings`. Solo le stringhe mai viste prima con punteggio sopra `notify_min_score` generano una
   notifica. Tutto il resto finisce comunque nello storico.

### Punteggio

`peso del match più alto + 25% degli altri pesi + bonus`, massimo 250. Soglie: **alta ≥ 100**, media ≥ 50.

| Pattern (config `keywords.items`) | Peso |
|---|---|
| `hlx` parola isolata | 100 |
| `hlx_*` identificatori | 110 |
| `maps/hlx`, `models/hlx`, `scripts/hlx`... | 130 |
| `hl3` | 90 |
| `half-life` generico (escluso alyx, hl2, episode) | 40 |
| nomi in codice (`keywords.codenames`, es. `tf`) | 30 |
| `npc_*`, `ai_basenpc` | 15 |
| veicoli, vento/meteo/termodinamica | 12 |
| fisica (`phys_*`, rubikon) | 6 |

Bonus: file nuovo +20, entità nuova in `.fgd` +25, riga di ConVar +15, token che è un percorso +15.
`citadel` è il nome in codice di Deadlock: le righe che lo contengono vengono scartate a meno che non
abbiano anche un match alto. Tutto è modificabile in `config.yaml`.

---

## Requisiti

- Windows 11 (o Linux/macOS), **Python 3.12 o superiore** (provato su 3.13)
- facoltativo: token GitHub personale (5000 richieste/ora invece di 60), chiave Steam Web API,
  webhook Discord o bot Telegram, `Source2Viewer-CLI` per i file locali, Docker

## Installazione passo passo (Windows)

```powershell
cd hlx-watch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m hlxwatch init
```

`init` crea `config.yaml` (copia dell'esempio) e `.env`. Apri `.env` e inserisci almeno:

```
GITHUB_TOKEN=ghp_...            # consigliato: senza token il backfill di 6 mesi è lento (60 richieste/ora)
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...   # oppure TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
STEAM_WEB_API_KEY=...           # opzionale, da https://steamcommunity.com/dev/apikey
```

Poi in `config.yaml` metti `enabled: true` nel notificatore che usi (`notifiers.discord` o
`notifiers.telegram`) e prova:

```powershell
python -m hlxwatch test-notify
```

Il token GitHub va bene anche "classic" senza alcuno scope (serve solo ad alzare il limite).

## Primo avvio: archivio storico di 6 mesi

```powershell
python -m hlxwatch backfill --months 6
```

Scorre tutti i commit degli ultimi sei mesi di CS2, Dota 2, Deadlock e Half-Life: Alyx, riempie il
database e marca come "già viste" tutte le stringhe trovate: da quel momento le notifiche arrivano solo
per novità vere. Il backfill non invia notifiche (aggiungi `--notify` se le vuoi). Opzioni utili:
`--since 2026-04-01`, `--repo cs2` (ripetibile). Se si interrompe, rilancialo: i commit già elaborati
vengono saltati.

Con il token GitHub il backfill richiede in genere qualche decina di minuti (un commit = 1-10 richieste
più i file grandi scaricati in raw). Senza token lo strumento si ferma e aspetta automaticamente il
reset del limite, quindi può durare ore.

Dopo il backfill fai una prima scansione completa, che crea anche la baseline Steam (build attuali,
snapshot PICS, frontiera degli appid) senza generare allarmi:

```powershell
python -m hlxwatch scan
```

## Uso quotidiano

| Comando | Cosa fa |
|---|---|
| `python -m hlxwatch daemon` | scansione ogni `scan_interval_minutes` (default 45) + report giornaliero alle `daily_report_time` con riepilogo via notifica |
| `python -m hlxwatch scan [--sources github,steam,local] [--repo cs2] [--no-notify]` | una scansione singola |
| `python -m hlxwatch report [--date 2026-10-23] [--days 7] [--format md\|html\|both] [--send]` | report in `data/reports/` |
| `python -m hlxwatch serve [--port 8765]` | dashboard su http://127.0.0.1:8765 |
| `python -m hlxwatch search hlx_ [--game cs2] [--min-score 50]` | ricerca da terminale |
| `python -m hlxwatch test-notify` | notifica di prova |

Tutti i comandi accettano `--config PERCORSO` e `--log-level DEBUG`. I log vanno anche in `data/logs/`.

### Tenerlo acceso su Windows

Opzione 1: lascia aperta una finestra con `python -m hlxwatch daemon`.

Opzione 2: Utilità di pianificazione. Crea un'attività "All'accesso" che esegue
`C:\...\hlx-watch\.venv\Scripts\python.exe` con argomenti `-m hlxwatch daemon` e cartella di avvio
`C:\...\hlx-watch`. In alternativa un'attività ogni 45 minuti con `-m hlxwatch scan`, più una
giornaliera con `-m hlxwatch report --send`.

### Senza PC acceso: GitHub Actions

Il file `.github/workflows/hlx-watch.yml` esegue la scansione ogni 30 minuti e il report giornaliero
sui server di GitHub, gratis per un repository privato personale. Il database sopravvive tra i run
nella cache di Actions e viene pubblicato come artifact (`hlxwatch-data`) scaricabile dalla pagina
del run: scaricalo in `data/hlxwatch.sqlite` per sfogliarlo con la dashboard locale.

1. Pubblica la cartella in un repository GitHub **privato** (`.env`, `config.yaml` e `data/` sono
   esclusi da `.gitignore`; la configurazione usata lì è `config.actions.yaml`).
2. In Settings → Secrets and variables → Actions aggiungi `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID`
   (o `DISCORD_WEBHOOK_URL`); opzionali `GH_PAT` (token GitHub personale, per avere 5000 richieste/ora
   invece delle 1000 del token automatico) e `STEAM_WEB_API_KEY`.
3. Nella scheda Actions lancia "HLX Watch" con **Run workflow**: al primo avvio, senza database in
   cache, fa da solo il backfill di 6 mesi. Da lì in poi gira da solo.
4. Per la dashboard online: Settings → Pages → Source "GitHub Actions". A ogni run il workflow genera
   la dashboard statica (`python -m hlxwatch site`: panoramica, timeline, alta rilevanza, ricerca nel
   browser su `findings.json`, report a 1/7/30 giorni) e la pubblica su
   `https://<utente>.github.io/hlx-watch/`. GitHub Pages è gratis solo sui repository pubblici, e il
   sito è comunque visibile a chiunque abbia il link.

Limiti: la pianificazione di GitHub è inaffidabile (i run possono partire con ore di ritardo o essere
saltati: in pratica se ne ottengono 4-10 al giorno invece di 48). Per questo il workflow usa il comando
`cron`, che a ogni run manda il report delle ultime 24 ore se non è ancora stato mandato oggi ed è
passata l'ora in `general.daily_report_time` (UTC). La sorgente dei file locali non è disponibile.
Per una cadenza esatta serve un servizio esterno che chiami `workflow_dispatch` (es. cron-job.org con
un token GitHub) oppure un server proprio con `daemon`.

### Messaggio di benvenuto del bot (Cloudflare Worker, opzionale)

Il bot è solo un mittente: per rispondere subito a /start e /help, anche a PC spento, la cartella
`telegram-welcome/` contiene un Cloudflare Worker gratuito che riceve i messaggi via webhook. Al
proprietario (TELEGRAM_CHAT_ID) risponde con un riepilogo di cosa riceverà, agli altri spiega che gli
avvisi sono privati e linka dashboard e codice.

```powershell
cd telegram-welcome
npx wrangler login                      # una volta, autorizza l'account Cloudflare nel browser
npx wrangler deploy                     # stampa l'indirizzo https://hlx-watch-bot.<sottodominio>.workers.dev
cd ..
.venv\Scripts\python telegram-welcome\setup.py https://hlx-watch-bot.<sottodominio>.workers.dev
```

`setup.py` carica i segreti sul Worker e collega il webhook. Con il webhook attivo `getUpdates` non
funziona più; `setup.py --remove` lo scollega.

### Docker

```bash
cp config.example.yaml config.yaml && cp .env.example .env   # poi compila .env
docker compose up -d --build        # avvia watcher (daemon) e dashboard sulla porta 8765
docker compose run --rm watcher python -m hlxwatch backfill --months 6
```

## Dashboard

- **Panoramica**: contatori, timeline delle scoperte (barre colorate per rilevanza, cliccabili),
  ultime voci rilevanti, token più frequenti, ultime build, app Steam sospette.
- **Ricerca**: testo libero su stringa/token/file/contesto, filtri per gioco, sorgente, tipo, punteggio,
  date, solo stringhe nuove.
- **Dettaglio voce**: riga, riga precedente (per le modifiche), contesto, link al commit GitHub, altre
  occorrenze dello stesso token.
- **Build**: commit GameTracking e build Steam per branch. **App Steam**: app nuove segnalate.
- API JSON: `/api/findings`, `/api/stats`, `/api/timeline`; report HTML al volo: `/report?days=7`.

## Notifiche

Discord: un messaggio con un embed per gioco (rosso ≥ 100, arancio ≥ 50), ogni riga con punteggio,
token, file, tipo di modifica e link al commit. Telegram: stesso contenuto in HTML, spezzato a 4000
caratteri. Le nuove build Steam valgono `steam.build_score` (55): sopra la soglia di default, quindi
notificate; abbassalo se non ti interessano.

## File locali (opzionale)

1. Scarica `Source2Viewer-CLI` dalle release di [ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat).
2. In `config.yaml`: `local.enabled: true`, percorso dell'eseguibile, per ogni gioco i `pak01_dir.vpk`
   e i binari (`engine2.dll`, `client.dll`, `server.dll`).
3. `python -m hlxwatch scan --sources local`. La prima volta crea gli snapshot (baseline), dalle
   successive segnala solo le differenze. Gli snapshot stanno in `data/local_snapshots/`.

Il VPK viene riletto solo se il suo hash cambia (cioè dopo un aggiornamento del gioco), quindi
inserirlo nel daemon costa poco. I flag della CLI (`-i`, `-o`, `-e`, `-d`, `--vpk_list`) sono quelli
delle release 2024-2026; se una versione li cambia, adatta `_run_cli` in `sources/local_files.py`.

## Personalizzare le parole chiave

In `config.yaml`, sezione `keywords`:

```yaml
  codenames: ["tf", "hlvr2"]          # parole intere, peso codename_weight
  items:
    - id: mio_pattern
      pattern: "(?<![a-z0-9])gravity_?gun"   # regex, case-insensitive
      weight: 70
      excludes: ["hl2"]                      # scarta la riga se contiene questo
```

Per repo o app puoi ignorare keyword rumorose: `ignore_keywords: [half_life]` (già impostato su Alyx).
`github.ignore_path_patterns` esclude cartelle intere, `general.max_findings_per_file` limita il rumore
di file giganteschi, `general.notify_min_score` regola quante notifiche ricevi.

## Note e limiti noti

- **Organizzazione GitHub**: i repo sono ora sotto `SteamTracking` (il vecchio `SteamDatabase` fa
  redirect). `GameTracking-HalfLifeAlyx` esiste ed è configurato con `half_life` ignorato.
- **Deadlock via PICS**: in anonimo l'app espone solo info parziali (`_missing_token`), depot e build
  non sono visibili. Il gioco è comunque seguito tramite `GameTracking-Deadlock`.
- **Lista app Steam**: il vecchio endpoint pubblico `ISteamApps/GetAppList/v2` non risponde più (404).
  Con `STEAM_WEB_API_KEY` si usa `IStoreService/GetAppList`; senza chiave vale l'euristica degli appid
  (un'app esistente ma con id superiore alla frontiera può essere segnalata come nuova la prima volta
  che cambia; il punteggio filtra comunque quelle non Valve).
- **Commit enormi**: l'API di GitHub espone al massimo 3000 file per commit; oltre, i file restanti non
  vengono analizzati (viene loggato un avviso). Per i commit di GameTracking è un caso raro.
- **Libreria `steam`**: usa gevent; per questo gira in un sottoprocesso con timeout. Se l'installazione
  fallisce, il resto funziona e la sorgente Steam viene saltata con un errore nel log.
- Le date in database e nei report sono in UTC; l'ora del report giornaliero del daemon è locale.

## Test

```powershell
pip install -r requirements-dev.txt
python -m pytest -q tests
```
