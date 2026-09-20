# Aegis online: guida di una pagina (pilota ospitato)

Obiettivo: Aegis raggiungibile su un indirizzo tuo (`https://app.tuodominio.it`), con
gli agenti dei clienti che chiamano `https://gateway.tuodominio.it`. Un server
piccolo, un comando.

> **Stato onesto:** pronto da provare, **non ancora messo online da nessuno**. I
> passi qui sotto sono quelli che gli script fanno; l'avvio del container e il
> certificato HTTPS reali non sono stati provati (vedi «Cosa è stato verificato» in
> [PHASE_20_HOSTED_PILOT.md](PHASE_20_HOSTED_PILOT.md)). Prova tutto una volta su un
> server di prova prima di dare un indirizzo a un cliente.

## Cosa ti serve (30 minuti, una volta)

1. **Un server in UE** con Ubuntu 22.04 o 24.04 e almeno 2 GB di RAM. Per un pilota
   basta un VPS da pochi euro al mese (controlla il listino del fornitore). Accesso
   SSH con chiave.
2. **Un dominio** e due record DNS di tipo **A** che puntano all'IP del server:
   `app.tuodominio.it` e `gateway.tuodominio.it`.
3. **Porte 80 e 443 aperte** (servono al certificato HTTPS automatico). Il resto
   chiuso; SSH solo con chiave.

Queste tre cose non si possono fare da uno script: sono l'unica parte che resta a te.

## Installazione

Sul server:

```bash
curl -fsSL https://get.docker.com | sh          # installa Docker e Compose (metodo ufficiale)
git clone <URL-del-repo> aegis && cd aegis
bash scripts/deploy-hosted.sh
```

Lo script chiede i due indirizzi e un'email (per il certificato), crea `.env` con
chiavi casuali, avvia tutto e stampa dashboard, gateway e **il codice d'invito**.
Rilanciarlo non cambia le chiavi.

**Salva `.env` in un posto sicuro** (per esempio un password manager). Senza quelle
chiavi i dati già registrati non si possono più verificare.

## Primo accesso (5 minuti)

1. Apri `https://app.tuodominio.it`, poi «Prima volta? Crea il tuo spazio». Inserisci
   nome dell'agenzia, il tuo nome, email, password (almeno 12 caratteri) e il
   **codice d'invito**.
2. «Collega un agente», lascia «Consigliato», «Crea l'agente».
3. Copia **indirizzo** e **chiave** nel tuo n8n: nodo «HTTP Request», «Import cURL»,
   incolla il comando (ha già indirizzo e chiave al posto giusto).
4. Alla prima chiamata dell'agente la scheda passa a «Collegato». Le richieste che
   richiedono una persona compaiono in «Approvazioni», con destinatari, oggetto e
   inizio del testo.

## Per i tuoi clienti

- **Un codice d'invito per cliente.** Sono in `AEGIS_INVITE_CODES` nel `.env`, separati
  da virgole. Per aggiungerne o toglierne, modifica il file e lancia
  `docker compose -f docker-compose.hosted.yml up -d`. Un codice tolto non permette più
  nuove registrazioni; chi si è già registrato resta.
- Ogni cliente ha il suo spazio, separato dagli altri.

## Email di approvazione (facoltativo, consigliato)

Compila le righe `AEGIS_SMTP_*` del `.env` e riavvia come sopra. Agli amministratori
arriva un link firmato: un tocco dal telefono, senza accesso, valido per quella
richiesta e scade con lei. Senza SMTP non parte nessuna email; le approvazioni
compaiono comunque in dashboard (con il contatore nel menu).

## Ogni tanto

- **Stato:** `docker compose -f docker-compose.hosted.yml ps` (tutto «healthy»).
  **Log:** `docker compose -f docker-compose.hosted.yml logs -f control-plane`.
  Controllo rapido: `https://app.tuodominio.it/api/health` deve rispondere
  `"posture":{"secure":true}`.
- **Aggiornare:** `git pull && docker compose -f docker-compose.hosted.yml up -d --build`.
  Le chiavi nel `.env` restano.
- **Backup ogni giorno** (cron, `crontab -e`):

  ```
  17 3 * * *  cd /percorso/aegis && bash scripts/backup.sh >> backups/backup.log 2>&1
  ```

  Crea `backups/aegis-<data>.db.gz` e tiene 14 giorni. **Copia `backups/` anche su
  un'altra macchina**: un backup sullo stesso disco non è un backup, e la catena di
  prove dipende da quel file.
- **Ripristino** (da provare una volta su un server di prova, prima di averne bisogno):

  ```bash
  docker compose -f docker-compose.hosted.yml stop control-plane enforcement-gateway
  docker run --rm -v aegis-hosted_aegis-data:/data -v "$PWD/backups:/backups:ro" alpine sh -c \
    'gunzip -c /backups/aegis-AAAA-MM-GG-HHMM.db.gz > /data/aegis.db \
     && rm -f /data/aegis.db-wal /data/aegis.db-shm && chown 10001:10001 /data/aegis.db'
  docker compose -f docker-compose.hosted.yml up -d
  ```

  Il nome del volume (`aegis-hosted_aegis-data`) si controlla con `docker volume ls`.
  I file `-wal` e `-shm` si cancellano perché appartengono al database vecchio.

## Se qualcosa non va

| Sintomo | Causa probabile |
|---|---|
| Nessun certificato HTTPS | DNS non ancora propagato o porte 80/443 chiuse: `docker compose -f docker-compose.hosted.yml logs caddy` |
| L'app non parte e parla di segreti | `.env` incompleto o con valori corti/di esempio (minimo 32 caratteri): rilancia `bash scripts/deploy-hosted.sh` su un `.env` nuovo |
| «Troppi tentativi» | Limite di frequenza: 10 tentativi al minuto per indirizzo |
| Nessuna email di approvazione | `AEGIS_SMTP_*` vuoto o errato; l'invio è in secondo piano e non blocca l'agente |
| 503 dal gateway | Oltre 60 richieste in parallelo: il server risponde subito invece di accodare |

## Cosa promette questa installazione, e cosa no

**Sì**

- Ogni approvazione vale per **una sola richiesta esatta**, una volta sola (legata a
  richiesta, parametri e contratto). Un rifiuto o una scadenza fanno rispondere
  BLOCK all'agente, che smette di aspettare.
- A chi approva si mostrano destinatari, oggetto e inizio del testo, **ricavati dal
  server dai parametri ricevuti**, non da ciò che l'agente dichiara. L'anteprima è
  cifrata, non entra mai nelle prove e viene cancellata 7 giorni dopo.
- La chiave dell'agente si vede una volta sola; Aegis ne conserva solo l'impronta.
- In produzione l'avvio rifiuta chiavi di default, di esempio o troppo corte, il
  ruolo `all` e il seed demo; la registrazione è a invito; il CORS non ha wildcard;
  la documentazione API è spenta; ci sono limiti di frequenza e di dimensione.

**No**

- **Non è un firewall contro la prompt injection** e non è un sistema di identità
  aziendale.
- **Ferma solo ciò che passa da Aegis.** In questa modalità il workflow del cliente
  chiede il permesso e poi esegue *lui*: Aegis decide su ciò che il workflow gli invia
  e non può impedire a un workflow che ha altre credenziali di agire senza chiedere.
- **Un solo server e un solo file SQLite:** nessuna alta affidabilità. Un guasto del
  disco senza backup fuori macchina significa perdere i dati.
- Le chiavi di Aegis stanno in variabili d'ambiente del server: **chi amministra il
  server può leggerle.** Scrivilo nei termini del pilota.
- **Gmail non è incluso** (resta nel compose di sviluppo, profilo `gmail`). Per un uso
  pubblico Google richiede la verifica dell'app e una valutazione di sicurezza annuale
  per lo scope usato. In questa fase l'avvio OAuth è stato messo a posto (un biglietto
  monouso al posto del token di sessione nell'URL, stato legato al browser che ha
  iniziato), ma non è stato provato con un account Google reale.
- **La capacità non è misurata.** Il limite è 60 richieste in parallelo per processo;
  il test di carico non è stato rieseguito.
- Se metti **Cloudflare** davanti: non è configurato. Caddy dovrebbe fidarsi degli
  indirizzi di Cloudflare (`trusted_proxies`), altrimenti il limite di frequenza
  vedrebbe l'indirizzo di Cloudflare al posto di quello del cliente.
