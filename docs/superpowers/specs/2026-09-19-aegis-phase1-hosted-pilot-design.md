# Aegis — Fase 1: pilota ospitato (design)

- **Stato:** bozza per la tua revisione, non committata · 2026-09-19
- **Base di codice:** `origin/master` `975411a` (Phase 19.1). I riferimenti `file:riga` vengono da audit su quel commit: riverificarli prima di modificare.
- **Direzione approvata:** A, GO condizionato sulle agenzie di automazione (Italia/UE), dietro un gate di validazione di 14 giorni.
- **Sostituisce** il primo piano, costruito per errore su un checkout fermo alla fase 16C.

## 1. Contesto e verdetto

**Mercato (verificato alla fonte il 2026-09-19).** Un "livello di sicurezza indipendente per agenti AI" generico non ha senso per un piccolo team. AWS AgentCore Policy è GA dal 3 mar 2026 (regole Cedar sulle chiamate di tool, default-deny), Microsoft Agent 365 dal 1 mag 2026, Auth0 for AI Agents dal 19 nov 2025 (Token Vault e approvazione umana asincrona, con piano gratuito), Palo Alto ha chiuso l'acquisto di Portkey il 29 mag 2026.

**Concorrenza diretta sulla nicchia.** n8n ha l'approvazione umana per ogni tool call dell'agente: 9 canali (tra cui Gmail), e l'approvatore vede tool e parametri (piano e data di uscita non confermati nella documentazione). gotoHuman la vende per n8n da 0 a 950 $/mese, con audit log solo nel piano top. Quindi "chiedi prima di inviare" non basta come proposta. Ipotesi da testare: **controllo cross-piattaforma e cross-cliente** (un'inbox unica, un kill switch, regole centrali, evidenza per il cliente). Non esiste nessun dato di disponibilità a pagare.

**Il codice è più avanti di quanto stimato all'inizio.** A `975411a`: approvazioni vere (grant monouso legato a richiesta, parametri e contratto), contratti con API, evidenza HMAC, connettore Gmail reale con una prova manuale del proprietario il 2026-09-18 (cerca, leggi, bozza, invio approvato, revoca, prompt injection: ok), 699 test dichiarati (47 saltati per mancanza di Docker). Nessun cliente lo ha usato.

**Cosa manca per un pilota** (prova reale del 2026-09-18 e audit del 2026-09-19):
- Si approva alla cieca: la UI mostra un digest, e la destinazione di una mail la dichiara l'agente, non il server. Nella prova reale l'invio approvato in 26 s è finito a un indirizzo di newsletter.
- Nessuna notifica. Nessun modo di collegare l'agente di un cliente: esistono solo agenti scritti da noi e il gateway non è raggiungibile da fuori.
- Registrazione e CORS aperti, seed demo con login precompilato, JWT nell'URL dell'avvio OAuth, stato OAuth non legato al browser, invio non sempre vincolato a un umano.
- Bug aperti: seed Gmail che non parte su un DB esistente e date senza UTC ("121 min fa"), entrambi dalla prova reale; rifiuto e scadenza che rispondono ancora APPROVAL, dall'audit.

**Anti-obiettivi.** Non è un firewall anti prompt-injection, non è un IAM aziendale, non protegge da un agente che ha altre credenziali. Aegis ferma ciò che passa da Aegis.

## 2. Decisioni da confermare (default proposti)

| # | Decisione | Default proposto |
|---|---|---|
| D1 | Integrazione guida | Decisione + approvazione via nodo HTTP/SDK (l'esecutore resta il workflow). Il connettore Gmail resta funzione da pilota (profilo compose `gmail`), non parte dell'offerta pubblica: `gmail.modify` è "Restricted" per Google, quindi l'uso pubblico richiede verifica dell'app e valutazione CASA annuale; in modalità Testing i refresh token scadono dopo 7 giorni |
| D2 | Hosting del pilota | VPS in UE + docker compose + Caddy dietro Cloudflare. Cloud Run/Fly + Postgres + Secret Manager solo dopo il gate |
| D3 | Anteprima dell'approvazione | Sì: destinatari, oggetto, primi 280 caratteri del corpo, cifrati sulla riga Approval, eliminati 7 giorni dopo la decisione, mai nell'evidenza. Alternativa più prudente: solo destinatari e oggetto |
| D4 | Registrazione | Solo a invito (codici in env) |
| D5 | "Interno" vs "esterno" | Impostazione per organizzazione (`internal_domains`), default: dominio dell'admin che registra |
| D6 | Dominio | Serve un dominio tuo per `app.` e `gateway.` |

## 3. Obiettivo e criteri di successo

**Obiettivo:** un'agenzia partner, con un link d'invito, collega un workflow n8n e vede la prima decisione in dashboard, approva dal telefono vedendo *cosa* approva, senza installare nulla.

- **S1 Tempo:** da invito a "prima decisione visibile" in 15 minuti o meno per una persona non sviluppatrice, provato con 2 persone reali.
- **S2 Approvazione:** notifica, link a un tocco, anteprima, decisione. L'agente riceve ALLOW una sola volta se approvata; BLOCK subito se negata o scaduta.
- **S3 Postura:** con `AEGIS_ENV=production` l'app rifiuta segreti di default o segnaposto, seed demo, `role=all`; nessuna credenziale precompilata; CORS senza wildcard; registrazione a invito.
- **S4 Verità dei dati:** date corrette in ogni fuso; `destination` derivata dal server.
- **S5 Capacità:** lo sweep 16C (`benchmarks/run_benchmark_16c.py`) su `/api/authorize` a 75 e 100 concorrenti: 0% timeout, p95 sotto 3 s, latenza di nuovo entro 10 s dai valori base dopo il carico.
- **S6 Regressione:** i test esistenti passano; test nuovi per S2–S5.

## 4. Perimetro

**Dentro:** WS-A…G qui sotto.

**Fuori (Fase 2 o oltre):** riscrittura della UI (da 11 voci a 4), accessibilità completa, build in un solo `index.html` con `config.js`, template di regole avanzati, proxy HTTP/MCP generico, fatturazione, multi-mailbox, KMS/BYOK, Postgres e PaaS (stadio P), egress-proxy ospitato, verifica Google/CASA, i18n completa.

## 5. Architettura del pilota (stadio V)

```
 n8n / agente del cliente --HTTPS + token agente--> Cloudflare -> Caddy (TLS, HSTS)
                                                       |
        gateway.<dominio>  role=enforcement-gateway    |  solo authorize e gateway
        (pubblico)                                     |  (NO /docs, NO agentctl)
        app.<dominio>      role=control-plane + SPA ---+  dashboard, approvazioni, OAuth
                                   |                      (NO verification)
                     volume dati (SQLite WAL, backup giornaliero)
     [opzionale, --profile gmail: broker + gmail-connector + volume OAuth]
```

- Due servizi (control-plane con SPA, gateway) più due opzionali per il pilota Gmail. `docker-compose.hosted.yml` come override: il gateway su una rete con ingresso (oggi solo reti `internal`), tolto il publish inerte `8001`.
- **Cosa NON promettere in modalità ospitata:** "l'agente non ha rotta verso Gmail", "egress con allow-list", la matrice `NETWORK_BLOCK`: valgono solo per l'agente demo nel nostro compose. **Claim ammesso:** la credenziale Gmail non lascia mai Aegis; l'agente detiene solo un token Aegis; ogni approvazione vale per una sola richiesta esatta.
- Stadio P (dopo il gate, fuori da questa spec): Cloud Run o Fly + Postgres, store OAuth in DB con chiave in Secret Manager, `replay.py` su tabella.

## 6. Workstream

### WS-A Bug bloccanti (prova reale + audit)
- **A1** `_seed_gmail` non parte se l'organizzazione esiste già (`seed.py:242`, dentro `seed_if_empty`). Renderlo idempotente per organizzazione e richiamarlo alla creazione di un agente Gmail. In produzione nessun seed demo.
- **A2** Date senza UTC (`schemas.py`): serializzare i datetime naive-UTC con suffisso `Z` con un serializer base.
- **A3** Rifiuto e scadenza rispondono ancora APPROVAL (`engines/enforcement.py:141`): negata = BLOCK "rifiutata", scaduta = BLOCK "approvazione scaduta". L'agente smette di aspettare.
- **A4** Righe `pending` abbandonate senza spazzino: scadenza lazy alla lettura della coda, tetto di pending per agente.
- **A5** Timeout di hop a 3 s (`config.py:40`) contro `gmail.search` = 1+N chiamate: timeout per tool, tetto ai risultati. Misurare prima.

### WS-B Igiene di produzione (`AEGIS_ENV=production`)
- **B1 Segreti:** rifiutare default e segnaposto (`change-me`, valori di `.env.example`) e chiavi sotto 32 caratteri; includere CRM, token interni e chiave OAuth (`security_posture.py:36-50`); rifiutare `role=all`.
- **B2** Niente seed demo (`main.py:80-86`), niente credenziali precompilate (`Login.tsx:7-8,85`), `/docs` spento, `/api/health` senza elenco dei segreti deboli (`main.py:142`).
- **B3 CORS** solo dalle origini in `AEGIS_CORS_ORIGINS`, nessun wildcard, `allow_credentials=False` (`main.py:103-104`): il Bearer basta. Con la SPA same-origin l'elenco può restare vuoto.
- **B4 Registrazione:** a invito (`auth.py:21`), password da 12 caratteri, `EmailStr`, limite in-process 10/min/IP più regole al bordo, quote per organizzazione.
- **B5 Limiti:** `Query(ge=1, le=500)` dove c'è `limit` (`evidence.py:57`, `verification.py:144`), tetto sulla risposta dell'agente (`agentctl.py:140`), corpo massimo 64 KB, `Field(max_length)`.
- **B6 Sessione:** JWT a 60 minuti (`config.py:22`); la UI attuale fa logout a ogni errore, accettabile fino alla Fase 2. Il token di sessione esce dall'URL: ticket monouso di 60 s per l'avvio OAuth (`Gmail.tsx:52`, `AgentGmailCard.tsx:96`, `routers/gmail.py:205-235`).
- **B7 Stato OAuth** monouso e legato al browser: nonce in cookie HttpOnly SameSite=Lax, consumato al callback (`routers/gmail.py:70-112`). Oggi chi ottiene uno stato loggato più il proprio codice Google può collegare la propria casella a un'altra organizzazione.
- **B8 Log:** senza query string (`--no-access-log` o maschera) e `--proxy-headers`.
- **B9** In produzione non montare `agentctl` e `verification`: sono il canale di prova dell'agente demo.

### WS-C Un solo URL
Dockerfile multi-stage (build Node, `dist` nel control-plane), mount statico con fallback SPA. `api.ts` resta same-origin. Il gateway ha il proprio hostname.

### WS-D Collega il primo agente (minimo)
- **D1** Scheda "Collega" nell'agente (`AgentSetupCard.tsx`): URL reale del gateway (`AEGIS_PUBLIC_GATEWAY_URL`), token mostrato una volta con pulsante Copia, snippet n8n (nodo HTTP e ciclo di attesa), curl, Python.
- **D2** `Agent.last_seen_at` (con la riga di migrazione in `ensure_schema`) aggiornato in `get_agent_from_token`: stato "In attesa della prima chiamata…" e poi "Ultima chiamata: hh:mm".
- **D3** Preset "Consigliato" nel wizard (`NewAgent.tsx`): lettura consentita, invio/cancellazione/pagamento con umano o negati. Sostituisce le 12 righe manuali.
- **D4** Regole di partenza alla registrazione: `starter_pack.py` estratto da `seed.py:80-153` e `_seed_gmail`, applicato a ogni nuova organizzazione. Test: organizzazione nuova, `email.send` esterna = APPROVAL, `gmail.send` = APPROVAL, `payments.TRANSFER` = BLOCK.

### WS-E Approvazioni usabili
- **E1 Destinazione derivata dal server:** normalizzare `to`, `cc`, `bcc` in domini e classificare interno/esterno con `internal_domains`. Policy e contratto usano la destinazione derivata. Destinazione assente su un'azione che invia = BLOCK (fail-closed). Oggi il connettore invia a `payload.to/cc` (`protected/gmail.py:486-491`) mentre il contratto passa se `destination` è `None` (`engines/contract.py:122-123`).
- **E2 Anteprima** sull'approvazione, come D3. Oggi la pagina mostra solo il digest (`Approvals.tsx:198-201`). La catena di evidenza resta sul digest e non contiene contenuto.
- **E3 Notifica:** email (SMTP da env) agli admin con link firmato monouso (HMAC, scadenza = TTL approvazione) verso una pagina mobile "Approva / Rifiuta" con anteprima. Telegram dopo.
- **E4** `GET /api/approvals/{id}/status` con il token agente (utile al nodo Wait di n8n).
- **E5** Race in `decide` (`routers/approvals.py:43-60`): UPDATE condizionale.

### WS-F Capacità (economica)
- **F1** SQLite: `PRAGMA journal_mode=WAL` e `busy_timeout=5000` alla connessione (`database.py:17-21`).
- **F2** `uvicorn --limit-concurrency 60 --timeout-keep-alive 5`; pool `pool_size=10, max_overflow=10, pool_timeout=5`, così si risponde 503 subito invece di restare appesi 30 s.
- **F3 Spike di 1 giorno, causa non provata.** Condizioni presenti nel codice: pool di default 5+10, 40 thread, un processo, SQLite senza WAL, lavoro abbandonato che continua. Esperimenti: E1 stato di pool e thread durante il collasso a c=75 (py-spy); E2 c=100 solo su `/api/authorize`; E3 con F2 attivo; E4 con `--workers 2`. Si adotta la configurazione più economica che passa S5.
- **F4** Catena di evidenza O(n²) (`enforcement.py:272`, `evidence.py:92`): tetto agli eventi per esecuzione, verifica completa solo su richiesta.

### WS-G Hosting stadio V
VPS in UE, compose override, Caddy (TLS e HSTS) dietro Cloudflare (WAF e rate limit di base), firewall (solo 80/443 e SSH con chiave), backup giornaliero (`sqlite3 .backup` su object storage, perché l'integrità della catena dipende dal file), aggiornamenti automatici, runbook di una pagina.

## 7. Verifica
- **Test automatici:** avvio rifiutato con segreti di default/segnaposto e con `role=all` in produzione; niente seed demo; registrazione senza invito rifiutata (403) e limite di frequenza (429); preflight CORS senza credenziali; org nuova con le regole di partenza; `destination` derivata da `to+cc`; destinazione assente = BLOCK; negata/scaduta = BLOCK; grant monouso ancora valido; date con `Z`; query `limit` fuori range rifiutata; ticket OAuth monouso; stato OAuth rifiutato senza cookie; `agentctl` e `verification` assenti in produzione.
- **Suite esistente:** deve restare verde (`conftest.py` imposta già `AEGIS_ALLOW_DEFAULT_SECRETS`; aggiungere `AEGIS_ENV=development`).
- **Prova manuale (S1):** 2 persone non sviluppatrici, n8n Cloud, senza aiuto, con cronometro.
- **Capacità (S5):** rerun dello sweep 16C su `/api/authorize`.
- **Sicurezza:** ri-audit mirato su E1, B6, B7 e D4 a implementazione finita.

## 8. Rischi e decisioni aperte

| Rischio | Mitigazione |
|---|---|
| n8n copre già l'approvazione per tool call | Il gate di Fase 0 chiede esplicitamente: usate già l'human review nativo? cosa manca? Stop se meno di 3 su 10 si impegnano con soldi |
| Il provider può leggere i segreti (chiave in env) | Dichiararlo nei termini del pilota; KMS/BYOK dopo il gate |
| Gmail per uso pubblico richiede verifica Google e CASA annuale (costi dichiarati da poche centinaia ad alcune migliaia di dollari, da fonti secondarie) | Gmail resta pilota (Testing, al massimo 100 utenti di prova, token a 7 giorni) o il cliente porta il proprio client OAuth. Alternativa da valutare: solo `gmail.send`, che Google classifica "Sensitive" e non "Restricted" |
| VPS singolo, nessuna alta affidabilità | Accettabile con 2–3 partner e backup giornaliero; stadio P dopo il gate |
| Privacy dell'anteprima (contenuto delle mail) | D3: cifrata, breve, eliminata dopo 7 giorni, mai nell'evidenza; DPA e informativa per i partner |
| Causa del collasso non provata | F3 come spike con regola di decisione |
| Le stime non sono misurate su clienti reali | S1 le sostituisce con un numero vero |

## 9. Stima e ordine

- **1a Ospitato, igiene, bug** (A, B, C, F, G): circa 1–1,5 settimane. Incondizionata: serve comunque a rendere il repo pubblicabile.
- **1b Collega e approva** (D, E): circa 1–1,5 settimane. Parte quando almeno 2 agenzie accettano un pilota.
- **Ordine:** A → B → G (URL pubblico) → E1 → D → E2, E3 → F3.
- **In parallelo, tu:** Fase 0 (landing IT/EN con pre-ordine a 49 €/mese, 10–15 interviste, concierge con 2–3 agenzie). La prova Gmail del 2026-09-18 è già una demo: basta uno screencast di 2 minuti, con una casella di prova dedicata e non la tua personale.
