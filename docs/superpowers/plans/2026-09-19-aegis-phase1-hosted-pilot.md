# Aegis Fase 1 — pilota ospitato: piano di implementazione

> **Per chi lo esegue:** piano snello scritto per essere eseguito in una sola sessione. Contiene interfacce, file e test, non il codice completo (che vive nel diff). Segna le caselle man mano. Riferimento: la spec approvata.

**Obiettivo:** portare Aegis da "più lento da avviare che da usare" a un servizio che si apre da un URL, con backend ospitabile con un comando, agenti collegabili in pochi minuti e approvazioni che si vedono e si decidono da telefono.

**Architettura:** stesso codice, un interruttore `AEGIS_ENV=production` che rifiuta configurazioni non sicure e chiude ciò che un servizio pubblico non deve esporre. SPA servita dal control-plane (un solo URL). Gateway pubblico su un secondo hostname. Deploy con `docker-compose.hosted.yml` + Caddy su una VPS UE.

**Stack:** FastAPI, SQLAlchemy, SQLite (WAL) per il pilota, React + Vite + TS, Docker, Caddy. Nessuna dipendenza Python nuova (regex al posto di `email-validator`, `smtplib` della stdlib).

**Spec:** `docs/superpowers/specs/2026-09-19-aegis-phase1-hosted-pilot-design.md` (D1–D6 confermate dall'utente il 2026-09-19).

## Vincoli globali
- Sviluppo resta il default: la suite esistente (baseline **700 passed, 47 skipped**) non deve regredire.
- Test backend solo in Docker (Python 3.11): `docker run --rm -v <repo>:/src:ro aegis-test sh -c '...pytest...'`. Python 3.14 locale non è compatibile con i pin.
- Config letta a runtime come `config.X` (mai `from config import X` per valori che i test cambiano).
- Nessun segreto, contenuto di mail o dato personale nei log e nell'evidenza.
- Si committa su un branch (`phase20-hosted-pilot`), mai su `master`. Commit e push solo a lavoro finito, dopo aver avvisato l'utente.

## Focus di revisione (input che la spec non nomina)
1. Destinatari scritti come `"Nome <a@b.it>"`, in lista, separati da virgola o `;`, con maiuscole: la classificazione interno/esterno non deve ingannarsi.
2. Dominio "furbo" (`acme.test.evil.com`, `evilacme.test`): non è interno.
3. Anteprima con campi sensibili (`password`, `token`) o molto lunga: mascherati e troncati.
4. Link di approvazione scaduto, già usato o manomesso: nessuna decisione.
5. Due `decide` concorrenti sulla stessa approvazione: una sola vince.

## Avanzamento
- [x] T1 Modalità produzione (config, posture, main, CORS, body, router) — 53 test verdi con T2
- [x] T2 Registrazione a invito, rate limit, TTL, pacchetto di regole iniziali
- [x] T3 Destinazione derivata dal server
- [x] T4 Approvazioni: negata/scaduta, decide atomico, stato per l'agente, anteprima
- [x] T5 Link a un tocco e notifica email (`send_mail` provata solo contro un finto SMTP locale senza TLS né accesso)
- [x] T6 Collega l'agente: last_seen, setup, preset "Consigliato", date UTC, seed idempotente
- [x] T7 Limiti, capacità (WAL, pool), SPA servita dal backend, Gmail dietro flag (capacità non misurata)
- [x] T8 OAuth: ticket monouso e stato legato al browser (provato con test, non con un account Google reale)
- [x] T9 Frontend: shell accessibile, menu semplice, italiano, Collega, approvazioni, link mobile (pagine avanzate non riviste)
- [x] T10 Kit di deploy (Dockerfile multi-stage, compose hosted, Caddy, script, guida) (scritto, non eseguito su un server)
- [x] T11 Verifica: suite completa, typecheck e build, smoke test in produzione locale; **immagine Docker non costruita** (Docker Desktop non si è avviato), vedi report
- [x] T12 Report di fase, avviso, commit e push del branch

## T1 Modalità produzione
**File:** `backend/app/config.py`, `security_posture.py`, `main.py`, `security.py`; test `test_phase20_production.py`.
**Produce:** `config.ENV`, `config.is_production()`, `config.SEED_DEMO`, `config.ENABLE_GMAIL`, `config.MAX_BODY_BYTES`, `config.MIN_SECRET_LENGTH`.
- Produzione rifiuta: segreti di default, segnaposto (`change-me`, `-dev-`, `example`...), chiavi sotto 32 caratteri, `role=all`; `AEGIS_ALLOW_DEFAULT_SECRETS` non la salva.
- Produzione: niente seed demo (restano i pattern di comportamento), `/docs` spento, `/api/health` senza nomi dei segreti.
- CORS: mai `allow_credentials`, mai `*` in produzione.
- Produzione: `verification` e `agentctl` non montati; `gateway` solo con `BROKER_URL`; `gmail` solo con `AEGIS_ENABLE_GMAIL=1`.
- Corpo oltre `MAX_BODY_BYTES` (produzione 64 KB) → 413.

## T2 Registrazione
**File:** `routers/auth.py`, `schemas.py`, `models.py` (`Organization.internal_domains`), `database.py` (`_add_column`), `ratelimit.py`, `services/starter_pack.py`; test `test_phase20_registration.py`.
**Produce:** `ratelimit.enforce(request, bucket)`, `starter_pack.install(db, org_id) -> int` (idempotente).
- Produzione: invito obbligatorio (`AEGIS_INVITE_CODES`, chiusa se vuoto), password ≥ 12, TTL JWT 60 min.
- Tutti gli ambienti: email con forma valida, lunghezze massime. Limite 10/min/IP su register e login (0 = spento in sviluppo).
- `internal_domains` = dominio dell'admin; pacchetto di partenza: policy blocco pagamenti, blocco cancellazione CRM, blocco export /Finance, approvazione email esterne, ok email interne, gmail: delete mai, send con umano.

## T3 Destinazione derivata
**File:** `engines/destination.py`, `engines/enforcement.py`; test `test_phase20_destination.py`.
**Produce:** `destination.recipients(payload) -> list[str]`, `destination.classify(addresses, internal_domains) -> "internal"|"external"`, `destination.derive(kind, action, payload, internal_domains) -> Derivation|None`.
- Vale per `email.SEND` (sovrascrive scope e destination) e `gmail.SEND` (sovrascrive solo destination).
- Senza destinatari: in produzione BLOCK (`AEGIS_REQUIRE_DERIVED_DESTINATION`), in sviluppo si usa il dichiarato.

## T4 Approvazioni
**File:** `engines/enforcement.py`, `routers/approvals.py`, `routers/authorize.py`, `routers/gateway.py`, `services/approval_preview.py`, `models.py` (`preview_sealed`, `preview_purge_at`), `schemas.py`; test `test_phase20_approvals.py`.
- Replay di una richiesta negata → BLOCK "rifiutata"; scaduta → BLOCK "scaduta". Evidenza originale intatta (override nell'outcome, nessuna riga nuova).
- `decide` con UPDATE condizionale (409 se già deciso).
- `GET /api/authorize/approvals/{id}` con token agente: `pending|approved|denied|expired|consumed`.
- Anteprima cifrata con `secretbox`, chiave derivata da `SECRET_KEY`, mai nell'evidenza, eliminata 7 giorni dopo la decisione (o la scadenza). Tetto di pending per agente (25).
- Output: `effective_status`, `agent_name`, `preview`.

## T5 Link e notifica
**File:** `services/approval_notify.py`, `routers/approvals.py`; test in `test_phase20_approvals.py`.
- Token firmato HMAC (approvazione, organizzazione, utente, scadenza). `GET /api/approvals/link/{token}` e `POST .../decide`, senza login, con rate limit.
- Email agli admin via `smtplib`, in un thread; senza SMTP configurato non fa nulla.

## T6 Collega l'agente
**File:** `models.py` (`Agent.last_seen_at`), `security.py`, `routers/agents.py`, `schemas.py`, `seed.py`; test `test_phase20_connect.py`.
- `last_seen_at` aggiornato al massimo ogni 10 s. `setup` restituisce `gateway_url` e snippet (curl, Python, n8n via "Import cURL").
- `POST /api/agents` con `preset: "recommended"` crea permessi e contratto ACTIVE.
- Date con suffisso `Z` negli schemi in uscita. `_seed_gmail` idempotente.

## T7 Capacità, SPA, Gmail
**File:** `database.py`, `routers/resources.py`, `routers/evidence.py`, `main.py`; test in `test_phase20_production.py`.
- SQLite WAL + `busy_timeout`; pool via env; `Query(ge=1, le=500)` sui `limit`.
- SPA servita da `AEGIS_STATIC_DIR` (o `/app/static`) con fallback su `index.html`, mai su `/api`.

## T8 OAuth (solo pilota Gmail)
**File:** `routers/gmail.py`, `frontend/src/pages/Gmail.tsx`, `components/AgentGmailCard.tsx`.
- `POST /oauth/start` restituisce un ticket monouso di 60 s; lo stato OAuth porta un nonce legato a un cookie HttpOnly e consumato al callback.

## T9 Frontend
**File:** `App.tsx`, `styles.css`, `api.ts`, `i18n.ts`, `time.ts`, `a11y.ts`, `pages/*`, `components/ConnectCard.tsx`, `pages/ApproveLink.tsx`.
- Menu da 11 a 5 voci (+ "Avanzate"), italiano di default con EN, link "salta al contenuto", `<nav>`/`<main>`, titolo e focus per pagina, menu mobile, focus ring, contrasto, bersagli da 44 px, login senza precompilazione né `href="#"`.
- Verifica: `tsc --noEmit`, `vite build`, controllo a vista nel browser.

## T10 Kit di deploy
**File:** `backend/Dockerfile`, `docker-compose.hosted.yml`, `deploy/Caddyfile`, `.env.hosted.example`, `scripts/*`, `docs/DEPLOY_HOSTED.md`.

## T11–T12 Verifica e consegna
Suite completa in Docker, `tsc` e `vite build`, build dell'immagine, smoke test in produzione, `docs/PHASE_20_HOSTED_PILOT.md` con etichette IMPLEMENTED / VERIFIED / NOT VERIFIED. Poi: notifica all'utente, commit per area, push del branch.
