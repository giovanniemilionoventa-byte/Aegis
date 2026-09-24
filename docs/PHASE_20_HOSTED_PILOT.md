# Fase 20 — pilota ospitato

**Stato:** implementato e provato in locale, anche con Docker (immagine, stack completo
dietro Caddy, backup e ripristino); **non ancora messo online**, quindi il certificato
pubblico e un server vero restano da provare. Branch `phase20-hosted-pilot`, base
`975411a` (Fase 19.1). Specifica:
[`superpowers/specs/2026-09-19-aegis-phase1-hosted-pilot-design.md`](superpowers/specs/2026-09-19-aegis-phase1-hosted-pilot-design.md).
Piano: [`superpowers/plans/2026-09-19-aegis-phase1-hosted-pilot.md`](superpowers/plans/2026-09-19-aegis-phase1-hosted-pilot.md).
Come metterlo online: [`DEPLOY_HOSTED.md`](DEPLOY_HOSTED.md).

Nessun cliente ha usato Aegis. Questa fase non cambia il verdetto del 2026-09-19: il
gate di validazione di 14 giorni (landing, pre-ordini, interviste, concierge con 2–3
agenzie) resta la condizione per investire oltre.

## Che cosa cambia

**Per chi lo usa.** Si apre da un indirizzo, in italiano o inglese secondo il browser,
con tema chiaro o scuro secondo il sistema. Il menu passa da 11 voci a 5 più
«Avanzate». «Collega un agente» dà in due passi un indirizzo, una chiave e un comando
da incollare in n8n; la scheda dice quando arriva la prima chiamata. Chi approva vede
destinatari, oggetto e inizio del testo, con due pulsanti grandi, anche da telefono
tramite il link nell'email di notifica, senza accesso.

**Per chi lo mette online.** Un interruttore `AEGIS_ENV=production` fa rifiutare
all'avvio segreti di default, di esempio o corti, il ruolo `all` e il seed demo, e
chiude ciò che un servizio pubblico non deve esporre. Un compose (`docker-compose.hosted.yml`)
con Caddy per HTTPS, tre script (`deploy-hosted.sh`, `backup.sh`, `start.ps1`) e una
guida di una pagina.

## Stato per voce

Legenda: la colonna «Provato come» dice come; «Non provato» dice cosa manca.

| Voce | Cosa c'è | Provato come | Non provato |
|---|---|---|---|
| A1 seed Gmail idempotente, niente seed demo in produzione | sì | test; smoke in produzione (nessun account demo, `features.demo=false`) | — |
| A2 date con `Z` | sì (`UTCDatetime`) | test; dashboard nel browser (orari corretti) | — |
| A3 rifiuto e scadenza rispondono BLOCK | sì | test; giro reale: link «Rifiuta», l'agente riprova e riceve BLOCK | — |
| A4 righe pending abbandonate, tetto 25 per agente | sì (scadenza letta a richiesta) | test | — |
| A5 timeout di hop, `gmail.search` | **no** | — | serve una misura prima di cambiare |
| B1 segreti, segnaposto, `role=all` | sì | test; app avviata davvero in produzione con segreti casuali (`posture.secure=true`) | — |
| B2 niente demo, `/docs` spento, health senza nomi dei segreti | sì | smoke in produzione (le rotte `/docs` e `/openapi.json` rispondono con la pagina della dashboard, non con Swagger) | — |
| B3 CORS senza wildcard né credenziali | sì | test; smoke (origine estranea: nessuna intestazione `allow-origin`) | — |
| B4 registrazione a invito, password ≥ 12, email valida, 10 tentativi/min | sì | test; smoke (403 senza codice, 400 password corta, 429 dopo 10) | quota per organizzazione: **no** |
| B5 limiti (`limit` 1–500, corpo 64 KB, lunghezze) | sì | test; smoke (413 a 70 KB, 422 con `limit=0`) | — |
| B6 sessione 60 min; biglietto monouso di 60 s per l'avvio OAuth | sì | test (8 nuovi), con controllo per mutazione: senza il controllo i test falliscono | con un account Google reale |
| B7 stato OAuth legato al browser (cookie HttpOnly, SameSite=Lax) e usa-una-volta | sì | come B6 | con un account Google reale |
| B8 log senza query string, indirizzo reale del cliente | sì (`UVICORN_ACCESS_LOG=0`, `UVICORN_PROXY_HEADERS=1`) | uvicorn locale con quelle variabili: nessuna riga di richiesta nei log; limite di frequenza per indirizzo letto da `X-Forwarded-For` | dentro il compose |
| B9 `agentctl` e `verification` non montati in produzione | sì | smoke (404) | — |
| C un solo indirizzo: la dashboard servita dal backend | sì (Dockerfile multi-stage, mount con fallback SPA) | locale: `/` 200, `/agents/new` 200, `/api/…` sconosciuto 404 JSON, tentativi di uscire dalla cartella rifiutati (test); **immagine costruita con Docker** (build multi-stage, dashboard compresa) per x86-64 e per arm64 (questa in emulazione), e avviata in produzione | — |
| D1 scheda «Collega» con comando pronto | sì (`ConnectCard`) | nel browser, con l'agente simulato via `curl` copiato dalla pagina | n8n reale |
| D2 `last_seen_at` e stato «Collegato» | sì | test; nel browser: da «In attesa della prima chiamata» a «Collegato» in ≤ 4 s | — |
| D3 preset «Consigliato» | sì | test; nel browser | — |
| D4 regole di partenza per ogni organizzazione | sì | test; smoke | — |
| E1 destinazione ricavata dal server | sì | test; smoke: l'agente dichiara `scope=internal` ma scrive a un estraneo, il server risponde APPROVAL | — |
| E2 anteprima cifrata, mai nell'evidenza, cancellata dopo 7 giorni | sì | test; nel browser (i campi con nome sensibile, es. `api_key`, compaiono come «•••») | — |
| E3 email agli admin con link firmato a un tocco | sì | test con la funzione di invio sostituita da un registratore (chi, quando, cosa contiene, che non parta senza SMTP); `send_mail` eseguita contro un finto server SMTP locale senza TLS (messaggio consegnato con mittente, destinatario, oggetto e corpo); pagina del link nel browser (finestra da telefono, approva e rifiuta) | **STARTTLS, SMTP su SSL (porta 465) e accesso con utente e password**: mai eseguiti |
| E4 stato della richiesta per l'agente | sì (`GET /api/authorize/approvals/{id}`) | test; smoke | — |
| E5 decisione atomica | sì | test | — |
| F1 SQLite in WAL con `busy_timeout` | sì | `journal_mode = wal` sul database dello smoke; due processi (control plane e gateway) sullo stesso file | — |
| F2 limite di concorrenza e pool limitato | sì (variabili del compose, opzioni del pool) | — | **nessun test di carico** |
| F3 esperimento sulla causa del collasso a 75–100 richieste | **no** | — | la causa resta un'ipotesi |
| F4 tetto sulla catena di evidenza (costo O(n²)) | **no** | — | — |
| G server, compose, Caddy, backup | sì | con Docker, lanciando davvero `scripts/deploy-hosted.sh` (nomi `app.localhost` e `gateway.localhost`): build, avvio, servizi «healthy»; HTTPS di Caddy con certificato locale e intestazioni (HSTS, CSP, `X-Frame-Options`, `nosniff`, `no-referrer`); container con utente 10001, file system in sola lettura, nessuna capability; database in WAL nel volume; log senza righe di richiesta e senza errori; lo smoke test di produzione ripetuto attraverso Caddy (stesso esito); `scripts/backup.sh` vero e ripristino con il comando scritto nella guida (dopo il ripristino i dati tornano a quelli del backup) | **certificato pubblico di Let's Encrypt** (serve un dominio vero), un server vero, n8n vero |
| Interfaccia (richiesta dell'utente dopo la specifica) | shell, menu, tema, focus, bersagli 44 px, italiano/inglese nelle pagine di uso quotidiano, pagina mobile del link | `tsc`, `vite build` da `npm ci`, percorso completo nel browser (scuro, chiaro, telefono), controlli strutturali (etichette, punti di riferimento, un solo `h1`, id unici), contrasti della palette calcolati (tutte le coppie ≥ 4,5:1 testo e ≥ 3:1 bordi) | lettore di schermo, giro solo tastiera su ogni pagina, Safari e Firefox; **le pagine avanzate** (Contratti, Prove, Stato sicurezza, Regole, Registro, Attività, Prova una richiesta, Gmail) sono in inglese e non riviste |

## Criteri di successo della specifica

| | Esito |
|---|---|
| S1 tempo dall'invito alla prima decisione ≤ 15 min con 2 persone | **non misurato** (nessuna persona vera l'ha provato) |
| S2 approvazione: notifica, link, anteprima, una volta sola; BLOCK su rifiuto e scadenza | verificato in locale; l'invio via SMTP è provato solo senza TLS e senza accesso (server finto locale) |
| S3 postura di produzione | verificato (test e app avviata in produzione) |
| S4 date corrette e destinazione ricavata dal server | verificato |
| S5 capacità (75 e 100 richieste in parallelo, p95 < 3 s) | **non misurato** |
| S6 nessuna regressione | suite completa: **842 passed, 47 skipped, 0 failed** |

## Test

- **Nuovi:** 113 funzioni di test (alcune parametrizzate), in `test_phase20_production.py`
  (28), `test_phase20_approvals.py` (32), `test_phase20_destination.py` (16),
  `test_phase20_registration.py` (15), `test_phase20_connect.py` (14) e 8 in
  `test_phase19_dashboard_oauth.py` (serie `test_20`).
- **Risultato finale:** 842 passed, 47 skipped in 4 min 24 s (linea di base del piano:
  700 passed, 47 skipped). Le 47 saltate sono quelle che richiedono un demone Docker,
  come prima. Stesso esito su **Python 3.13** (dipendenze bloccate, sul PC) e su
  **Python 3.11.16**, la versione dell'immagine di produzione, nel container di test
  (3 min 4 s).
- **Test esistenti modificati, e perché:**
  - `test_phase17_approval_loop.py::test_expired_grant_cannot_execute`: una
    approvazione scaduta ora risponde BLOCK e non APPROVAL (A3, voluto). La proprietà
    che il test difende, «non viene eseguito nulla», è invariata.
  - `test_phase19_policy.py::test_delete_is_outside_the_runtime_contract`: le
    organizzazioni nuove partono con le regole iniziali, che includono «Gmail delete
    mai»; il test isola lo strato del contratto e prima toglie lo strato delle regole.
  - `test_phase13f_fail_closed.py` e `test_eat_binding.py`: gli esiti finti delle prove
    hanno due campi in più (`final_decision`, `final_reason`), aggiunti a
    `AuthorizationOutcome`.
  - `test_phase19_dashboard_oauth.py`: due asserzioni sull'indirizzo di avvio OAuth
    (ora ha `?ticket=`, B6).

## Come è stato verificato

- **Suite backend** su una copia del repo fuori dalla cartella di lavoro (il file
  `backend/aegis.db` locale non è stato toccato).
- **Server in modalità produzione, in locale:** control plane e gateway come due
  processi sullo stesso file SQLite, segreti casuali, codici d'invito. Controlli:
  superficie esposta, registrazione, agente con preset, decisioni via gateway,
  approvazione e nuovo invio, CORS, dimensione del corpo, limite di frequenza.
- **Percorso completo nel browser:** creazione dell'agente col preset, comando copiato
  dalla pagina ed eseguito, stato che passa a «Collegato», richiesta di invio a un
  esterno in «Approvazioni» con anteprima, approvazione dal pannello e ALLOW una volta
  sola all'invio successivo, rifiuto dal link a un tocco (schermo da telefono) e BLOCK
  all'agente.
- **Script:** `deploy-hosted.sh` con un finto `docker` (scrive `.env` con chiavi di 44
  caratteri, un secondo lancio non le cambia); `start.ps1` col demone spento. Il
  secondo ha fatto emergere un difetto vero, corretto: in Windows PowerShell 5.1 lo
  stderr di `docker` faceva terminare lo script con `NativeCommandError`.
- **Con Docker (dopo la consegna del branch):** costruzione dell'immagine per x86-64 e
  per arm64 (le macchine gratuite di Oracle sono ARM: i quattro basi usati, `caddy`,
  `python`, `node` e `alpine`, hanno tutti la variante arm64, e l'immagine arm64 costruita
  parte in produzione, risponde e registra un'organizzazione, sebbene in emulazione);
  stack completo con `deploy-hosted.sh`, prova attraverso Caddy, backup e ripristino
  come sopra. Lo stack locale di sviluppo dell'utente, già acceso sulla porta 8000, non è
  stato toccato: `scripts\start.ps1` con Docker acceso e la parte Docker dei test
  saltati (47) non sono stati rieseguiti.
- **Bug trovati e corretti durante la verifica:** l'effetto «consentita» nella scheda
  dell'agente non diceva che le regole dell'organizzazione possono richiedere comunque
  una persona (ora c'è una nota); il preset «Consigliato» dava permessi Gmail anche
  dove Gmail non è offerto (ora no); il controllo periodico saltava il primo
  caricamento in una scheda in background.

## Limiti e rischi noti

- Un solo server e un solo file SQLite, nessuna alta affidabilità. Il limite di
  frequenza e i biglietti OAuth stanno in memoria del processo: control plane e
  gateway (due processi) contano separatamente.
- Le chiavi di Aegis sono variabili d'ambiente: chi amministra il server le legge.
- In questa modalità l'esecuzione resta al workflow del cliente: Aegis decide su ciò
  che riceve e ferma solo ciò che passa da Aegis.
- Gmail pubblico richiede verifica Google e valutazione di sicurezza annuale; qui è
  solo funzione da pilota.
- Cloudflare davanti non è configurato (serve `trusted_proxies` in Caddy).
- **Non fatti:** A5, quota per organizzazione (B4), F3 (spike di capacità), F4, lettore
  di schermo e giro solo tastiera, pagine avanzate in italiano.
- Il ripristino da backup è stato provato in locale con Docker; va comunque provato una
  volta sul server vero.

## Cosa resta a te

1. Affittare un server in UE (Ubuntu, 2 GB).
2. Un dominio, e due record A (`app.` e `gateway.`) verso l'IP del server.
3. Sul server: `git clone`, poi `bash scripts/deploy-hosted.sh`.

Poi, prima di dare l'indirizzo a un cliente: una prova completa su quel server (incluso
un ripristino da backup) e, se vuoi le email di approvazione, i dati SMTP nel `.env`.
