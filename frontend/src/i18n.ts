import { useSyncExternalStore } from "react";

/**
 * Two languages, no library. Italian is the default for Italian browsers,
 * English for everyone else, and the choice sticks.
 *
 * Only the pages a customer uses every day are translated (login, home, agents,
 * connecting an agent, approvals). The advanced pages (contracts, evidence,
 * security status, rules, audit log, Gmail) are still English.
 *
 * `en` is typed against the keys of `it`, so a string added to one language and
 * forgotten in the other fails the build instead of showing a blank.
 */
export type Lang = "it" | "en";
const STORAGE_KEY = "aegis_lang";

function detect(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "it" || saved === "en") return saved;
  } catch {
    /* storage blocked: use the browser language */
  }
  return navigator.language.toLowerCase().startsWith("it") ? "it" : "en";
}

let current: Lang = detect();
document.documentElement.lang = current;
const listeners = new Set<() => void>();
const subscribe = (notify: () => void) => {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
};

export const getLang = () => current;

export function setLang(lang: Lang) {
  current = lang;
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* not remembered, but it still applies now */
  }
  document.documentElement.lang = lang;
  listeners.forEach((notify) => notify());
}

const it = {
  "common.loading": "Caricamento…",
  "common.copy": "Copia",
  "common.copied": "Copiato",
  "common.back": "Indietro",
  "common.wait": "Attendere…",
  "common.failed": "Qualcosa non ha funzionato",

  "shell.skip": "Vai al contenuto",
  "shell.tagline": "Controllo per agenti AI",
  "shell.menu": "Menu principale",
  "shell.signOut": "Esci",
  "shell.language": "Lingua",
  "shell.pending": "{n} in attesa",
  "nav.home": "Home",
  "nav.agents": "Agenti",
  "nav.approvals": "Approvazioni",
  "nav.rules": "Regole",
  "nav.activity": "Attività",
  "nav.advanced": "Avanzate",
  "nav.contracts": "Contratti",
  "nav.gmail": "Gmail",
  "nav.security": "Stato sicurezza",
  "nav.audit": "Registro eventi",
  "nav.evidence": "Prove",
  "nav.try": "Prova una richiesta",

  "login.title": "Accedi ad Aegis",
  "login.registerTitle": "Crea il tuo spazio",
  "login.lead":
    "Decidi tu cosa possono fare i tuoi agenti AI. Aegis controlla ogni richiesta prima che parta.",
  "login.email": "Email",
  "login.password": "Password",
  "login.passwordHint": "Almeno 12 caratteri.",
  "login.org": "Nome della tua azienda o agenzia",
  "login.name": "Il tuo nome",
  "login.invite": "Codice d'invito",
  "login.inviteHint": "Te lo ha dato chi ti ha invitato ad Aegis.",
  "login.submit": "Accedi",
  "login.registerSubmit": "Crea lo spazio",
  "login.toRegister": "Prima volta? Crea il tuo spazio",
  "login.toLogin": "Hai già un account? Accedi",
  "login.demo": "Prova con l'account demo",
  "login.badLogin": "Email o password non corretti.",
  "login.badInvite": "Codice d'invito non valido.",
  "login.tooMany": "Troppi tentativi. Riprova tra un minuto.",

  "home.title": "Home",
  "home.lead": "Cosa stanno facendo i tuoi agenti e cosa aspetta te.",
  "home.waitingOne": "1 richiesta aspetta la tua decisione",
  "home.waitingMany": "{n} richieste aspettano la tua decisione",
  "home.waitingSub": "L'agente resta fermo finché non rispondi.",
  "home.review": "Rivedi ora",
  "home.startTitle": "Inizia da qui",
  "home.startLead": "In tre passi il tuo primo agente è controllato da Aegis.",
  "home.step1":
    "Crea l'agente e scegli cosa può fare: il profilo «Consigliato» va bene per iniziare.",
  "home.step2": "Copia indirizzo e chiave nel tuo n8n, Make o script.",
  "home.step3": "Appena l'agente fa una richiesta, la decisione appare qui.",
  "home.startCta": "Collega il tuo primo agente",
  "home.noEvents":
    "Nessuna richiesta ricevuta. Appena il tuo agente chiama Aegis, la vedrai qui.",
  "home.statAgents": "Agenti",
  "home.statChecked": "Richieste controllate",
  "home.statBlocked": "Bloccate",
  "home.statWaiting": "In attesa di te",
  "home.recent": "Ultime decisioni",
  "home.colDecision": "Decisione",
  "home.colAction": "Azione",
  "home.colRisk": "Rischio",
  "home.alerts": "Avvisi",
  "home.noAlerts": "Nessun avviso aperto.",
  "home.how":
    "Il tuo agente chiede ad Aegis prima di agire. Aegis risponde: consentito, serve un umano, oppure bloccato.",

  "decision.ALLOW": "Consentita",
  "decision.APPROVAL": "Serve un umano",
  "decision.BLOCK": "Bloccata",
  "status.pending": "In attesa",
  "status.approved": "Approvata",
  "status.denied": "Rifiutata",
  "status.expired": "Scaduta",
  "status.consumed": "Usata",
  "risk.low": "rischio basso",
  "risk.medium": "rischio medio",
  "risk.high": "rischio alto",
  "risk.critical": "rischio critico",

  "agents.title": "Agenti",
  "agents.lead":
    "Un agente è il tuo workflow o assistente AI (n8n, Make, uno script…). Parte senza permessi: decidi tu cosa può fare.",
  "agents.add": "Collega un agente",
  "agents.empty": "Nessun agente. Collega il primo: bastano pochi minuti.",
  "agents.colName": "Nome",
  "agents.colStatus": "Stato",
  "agents.colAuthority": "Permessi",
  "agents.revoked": "Revocato",
  "agents.waitingFirst": "In attesa della prima chiamata",
  "agents.connected": "Collegato",
  "agents.lastCall": "ultima chiamata {when}",
  "agents.noContract": "Nessun permesso: tutto negato",
  "agents.testAgent": "agente di test",

  "appr.title": "Approvazioni",
  "appr.lead":
    "Approvare fa eseguire all'agente questa richiesta esatta, una volta sola. Se la richiesta cambia, serve una nuova approvazione.",
  "appr.waiting": "In attesa di te ({n})",
  "appr.none": "Niente da approvare, per ora.",
  "appr.decided": "Già decise",
  "appr.noneDecided": "Ancora nessuna decisione.",
  "appr.wants": "{agent} vuole: {action}",
  "appr.approve": "Approva",
  "appr.deny": "Rifiuta",
  "appr.doneApproved": "Approvata. L'agente potrà eseguire questa richiesta una sola volta.",
  "appr.doneDenied": "Rifiutata. L'agente riceve subito un blocco.",
  "appr.conflict": "La richiesta è già stata decisa oppure è scaduta.",
  "appr.raised": "Richiesta {when}",
  "appr.expires": "Scade {when}",
  "appr.to": "A",
  "appr.cc": "Cc",
  "appr.bcc": "Ccn",
  "appr.subject": "Oggetto",
  "appr.body": "Testo (anteprima)",
  "appr.noPreview":
    "Nessuna anteprima: il contenuto non è stato registrato o è già stato eliminato.",
  "appr.external": "Ci sono destinatari esterni",
  "appr.old": "Richiesta vecchia: l'agente potrebbe aver smesso di aspettare.",
  "appr.details": "Dettagli tecnici",
  "appr.execution": "Esecuzione",
  "appr.request": "Richiesta",
  "appr.contract": "Contratto",
  "appr.digest": "Impronta dei parametri",
  "appr.digestNote":
    "L'approvazione vale solo per questi parametri esatti: una richiesta diversa non è coperta.",
  "appr.reason": "Perché serve un umano",
  "appr.colAgent": "Agente",
  "appr.colAction": "Azione",
  "appr.colResult": "Esito",
  "appr.colWhen": "Quando",
  "appr.usedOnce": "usata una volta",
  "appr.notUsed": "non ancora usata",

  "link.title": "Approvazione richiesta",
  "link.doneApproved": "Approvata",
  "link.doneApprovedSub": "L'agente potrà eseguire la richiesta una sola volta.",
  "link.doneDenied": "Rifiutata",
  "link.doneDeniedSub": "L'agente riceve un blocco e si ferma.",
  "link.already": "Questa richiesta è già stata decisa.",
  "link.expired": "Questa richiesta è scaduta: l'agente dovrà ripeterla.",
  "link.invalid": "Link non valido o scaduto.",
  "link.tooMany": "Troppi tentativi. Riprova tra poco.",
  "link.openApp": "Apri Aegis",

  "new.title": "Collega un agente",
  "new.lead":
    "Un agente parte senza alcun permesso. Quello che scegli qui viene registrato e tutto il resto resta negato.",
  "new.name": "Nome",
  "new.namePh": "Assistente vendite",
  "new.purpose": "A cosa serve? (facoltativo)",
  "new.purposePh": "Leggere i clienti e preparare le risposte",
  "new.presetLegend": "Cosa può fare?",
  "new.presetRecommended": "Consigliato",
  "new.presetRecommendedDesc":
    "Legge liberamente. Le email verso l'esterno e le modifiche ai dati passano da te. Cancellare, spostare denaro ed esportare sono sempre vietati.",
  "new.presetCustom": "Personalizzato",
  "new.presetCustomDesc": "Scegli tu, azione per azione.",
  "new.create": "Crea l'agente",
  "new.creating": "Creazione…",
  "new.toCaps": "Avanti: permessi",
  "new.stepCaps": "Cosa può fare?",
  "new.capsLead": "Tutto è negato finché non dici il contrario.",
  "new.colCap": "Azione",
  "new.colMode": "Esecuzione",
  "new.allow": "Consenti",
  "new.human": "Chiedi a un umano",
  "new.deny": "Nega",
  "new.executes": "eseguita da Aegis",
  "new.decisionOnly": "solo decisione",
  "new.irreversible": "irreversibile",
  "new.decisionOnlyNote":
    "«Solo decisione»: Aegis risponde consentito, umano o bloccato, ma è il tuo workflow a eseguire l'azione.",
  "new.review": "Riepilogo",
  "new.toReview": "Avanti: riepilogo",
  "new.nothingGranted":
    "Non hai concesso nulla: l'agente verrà creato ma ogni sua richiesta sarà rifiutata.",
  "new.stepConnect": "Fatto. Ora collega l'agente",
  "new.gmailNote":
    "Manca un passo: collega una casella Gmail e concedi l'accesso a questo agente, dalla sua pagina. Finché non lo fai, ogni richiesta Gmail viene rifiutata.",
  "new.openAgent": "Apri la pagina dell'agente",

  "cap.crm.READ": "Leggere il CRM",
  "cap.crm.UPDATE": "Modificare il CRM",
  "cap.crm.DELETE": "Cancellare dal CRM",
  "cap.gmail.SEARCH": "Cercare in Gmail",
  "cap.gmail.READ": "Leggere Gmail",
  "cap.gmail.DRAFT": "Preparare bozze Gmail",
  "cap.gmail.SEND": "Inviare da Gmail",
  "cap.gmail.DELETE": "Cancellare da Gmail",
  "cap.email.SEND": "Inviare email",
  "cap.files.READ": "Leggere file",
  "cap.files.EXPORT": "Esportare file",
  "cap.payments.TRANSFER": "Trasferire denaro",

  "ad.noDescription": "Nessuna descrizione.",
  "ad.lastContact": "Ultimo contatto",
  "ad.never": "Mai: l'agente non ha ancora chiamato Aegis.",
  "ad.lastNote": "È l'ultima chiamata ricevuta, non la garanzia che l'agente sia acceso adesso.",
  "ad.governed": "Governato da un contratto",
  "ad.noContractDenied": "Nessun contratto attivo: tutto negato",
  "ad.revoked": "revocato",
  "ad.id": "ID agente",
  "ad.created": "Creato",
  "ad.revokedAt": "Revocato",
  "ad.caps": "Cosa può fare",
  "ad.noPerms": "Nessun permesso: ogni richiesta di questo agente viene rifiutata.",
  "policy.note":
    "«Consentita» vale salvo le regole dell'organizzazione (menu «Regole») che richiedano comunque una persona: per esempio, le email verso l'esterno.",
  "ad.colCap": "Azione",
  "ad.colScope": "Ambito",
  "ad.colEffect": "Effetto",
  "ad.colContract": "Nel contratto",
  "ad.human": "serve un umano",
  "ad.allow": "consentita",
  "ad.deny": "negata",
  "ad.inContract": "sì",
  "ad.notInContract": "no: il contratto la rifiuta",
  "ad.contract": "Contratto",
  "ad.revokeContract": "Revoca il contratto",
  "ad.noContracts": "Nessun contratto: questo agente non ha alcun permesso.",
  "ad.colContractId": "Contratto",
  "ad.colVersion": "Ver.",
  "ad.colStatus": "Stato",
  "ad.colPurpose": "Scopo",
  "ad.colExpires": "Scade",
  "ad.revokeAgent": "Revoca l'agente",
  "ad.confirmRotate": "Generare una nuova chiave? La vecchia smette subito di funzionare.",
  "ad.confirmRevoke": "Revocare questo agente? Smetterà subito di funzionare.",
  "ad.revokeNote":
    "Revocare disattiva l'agente e tutte le sue chiavi: la sua prossima richiesta viene rifiutata.",

  "cc.title": "Collega il tuo agente",
  "cc.lead":
    "Il tuo agente (n8n, Make, uno script…) chiede ad Aegis prima di agire. Servono due cose: un indirizzo e una chiave.",
  "cc.waiting": "In attesa della prima chiamata…",
  "cc.seen": "Collegato: ultima chiamata {when}",
  "cc.address": "1. Indirizzo di Aegis",
  "cc.addressLocal":
    "L'indirizzo pubblico non è configurato: questo è l'indirizzo dell'Aegis che stai usando.",
  "cc.key": "2. Chiave dell'agente",
  "cc.keyOnce": "Copiala ora: per sicurezza Aegis non potrà mostrartela di nuovo.",
  "cc.keyLost":
    "La chiave si vede una sola volta, alla creazione. Se l'hai persa, genera una nuova chiave: la vecchia smette di funzionare.",
  "cc.rotate": "Genera nuova chiave",
  "cc.tool": "3. Collega il tuo strumento",
  "cc.n8nTitle": "n8n (consigliato)",
  "cc.n8nHow":
    "Aggiungi un nodo «HTTP Request», scegli «Import cURL» e incolla il comando qui sotto. Se la risposta è APPROVAL, aspetta con un nodo «Wait» e ripeti la stessa richiesta con lo stesso request_id: la risposta diventa ALLOW o BLOCK.",
  "cc.pythonTitle": "Python",
  "cc.keyIn": "La tua chiave è già inserita: non condividere questo testo.",
  "cc.keyOut": "Sostituisci YOUR_AGENT_TOKEN con la tua chiave.",
} as const;

export type Key = keyof typeof it;

const en: Record<Key, string> = {
  "common.loading": "Loading…",
  "common.copy": "Copy",
  "common.copied": "Copied",
  "common.back": "Back",
  "common.wait": "Please wait…",
  "common.failed": "Something went wrong",

  "shell.skip": "Skip to content",
  "shell.tagline": "Control for AI agents",
  "shell.menu": "Main menu",
  "shell.signOut": "Sign out",
  "shell.language": "Language",
  "shell.pending": "{n} waiting",
  "nav.home": "Home",
  "nav.agents": "Agents",
  "nav.approvals": "Approvals",
  "nav.rules": "Rules",
  "nav.activity": "Activity",
  "nav.advanced": "Advanced",
  "nav.contracts": "Contracts",
  "nav.gmail": "Gmail",
  "nav.security": "Security status",
  "nav.audit": "Audit log",
  "nav.evidence": "Evidence",
  "nav.try": "Try a request",

  "login.title": "Sign in to Aegis",
  "login.registerTitle": "Create your workspace",
  "login.lead":
    "You decide what your AI agents may do. Aegis checks every request before it goes out.",
  "login.email": "Email",
  "login.password": "Password",
  "login.passwordHint": "At least 12 characters.",
  "login.org": "Your company or agency name",
  "login.name": "Your name",
  "login.invite": "Invite code",
  "login.inviteHint": "Given to you by whoever invited you to Aegis.",
  "login.submit": "Sign in",
  "login.registerSubmit": "Create workspace",
  "login.toRegister": "First time? Create your workspace",
  "login.toLogin": "Already have an account? Sign in",
  "login.demo": "Try the demo account",
  "login.badLogin": "Wrong email or password.",
  "login.badInvite": "Invalid invite code.",
  "login.tooMany": "Too many attempts. Try again in a minute.",

  "home.title": "Home",
  "home.lead": "What your agents are doing, and what is waiting for you.",
  "home.waitingOne": "1 request is waiting for your decision",
  "home.waitingMany": "{n} requests are waiting for your decision",
  "home.waitingSub": "The agent stays stopped until you answer.",
  "home.review": "Review now",
  "home.startTitle": "Start here",
  "home.startLead": "Three steps and your first agent is under Aegis control.",
  "home.step1":
    "Create the agent and choose what it may do: the “Recommended” profile is fine to begin.",
  "home.step2": "Copy the address and key into your n8n, Make or script.",
  "home.step3": "As soon as the agent makes a request, the decision shows up here.",
  "home.startCta": "Connect your first agent",
  "home.noEvents": "No requests yet. As soon as your agent calls Aegis, you will see it here.",
  "home.statAgents": "Agents",
  "home.statChecked": "Requests checked",
  "home.statBlocked": "Blocked",
  "home.statWaiting": "Waiting for you",
  "home.recent": "Latest decisions",
  "home.colDecision": "Decision",
  "home.colAction": "Action",
  "home.colRisk": "Risk",
  "home.alerts": "Alerts",
  "home.noAlerts": "No open alerts.",
  "home.how":
    "Your agent asks Aegis before it acts. Aegis answers: allowed, needs a person, or blocked.",

  "decision.ALLOW": "Allowed",
  "decision.APPROVAL": "Needs a person",
  "decision.BLOCK": "Blocked",
  "status.pending": "Waiting",
  "status.approved": "Approved",
  "status.denied": "Denied",
  "status.expired": "Expired",
  "status.consumed": "Used",
  "risk.low": "low risk",
  "risk.medium": "medium risk",
  "risk.high": "high risk",
  "risk.critical": "critical risk",

  "agents.title": "Agents",
  "agents.lead":
    "An agent is your workflow or AI assistant (n8n, Make, a script…). It starts with no permissions: you decide what it may do.",
  "agents.add": "Connect an agent",
  "agents.empty": "No agents yet. Connect the first one: it takes a few minutes.",
  "agents.colName": "Name",
  "agents.colStatus": "Status",
  "agents.colAuthority": "Permissions",
  "agents.revoked": "Revoked",
  "agents.waitingFirst": "Waiting for the first call",
  "agents.connected": "Connected",
  "agents.lastCall": "last call {when}",
  "agents.noContract": "No permissions: everything denied",
  "agents.testAgent": "test agent",

  "appr.title": "Approvals",
  "appr.lead":
    "Approving lets the agent run this exact request, once. If the request changes, a new approval is needed.",
  "appr.waiting": "Waiting for you ({n})",
  "appr.none": "Nothing to approve right now.",
  "appr.decided": "Already decided",
  "appr.noneDecided": "No decisions yet.",
  "appr.wants": "{agent} wants to: {action}",
  "appr.approve": "Approve",
  "appr.deny": "Deny",
  "appr.doneApproved": "Approved. The agent can run this request one time only.",
  "appr.doneDenied": "Denied. The agent gets a block right away.",
  "appr.conflict": "The request was already decided or has expired.",
  "appr.raised": "Asked {when}",
  "appr.expires": "Expires {when}",
  "appr.to": "To",
  "appr.cc": "Cc",
  "appr.bcc": "Bcc",
  "appr.subject": "Subject",
  "appr.body": "Text (preview)",
  "appr.noPreview": "No preview: the content was not recorded or has already been deleted.",
  "appr.external": "Includes external recipients",
  "appr.old": "Old request: the agent may have stopped waiting.",
  "appr.details": "Technical details",
  "appr.execution": "Execution",
  "appr.request": "Request",
  "appr.contract": "Contract",
  "appr.digest": "Parameter digest",
  "appr.digestNote":
    "The approval covers these exact parameters only: a different request is not covered.",
  "appr.reason": "Why a person is needed",
  "appr.colAgent": "Agent",
  "appr.colAction": "Action",
  "appr.colResult": "Outcome",
  "appr.colWhen": "When",
  "appr.usedOnce": "used once",
  "appr.notUsed": "not used yet",

  "link.title": "Approval requested",
  "link.doneApproved": "Approved",
  "link.doneApprovedSub": "The agent can run the request one time only.",
  "link.doneDenied": "Denied",
  "link.doneDeniedSub": "The agent gets a block and stops.",
  "link.already": "This request has already been decided.",
  "link.expired": "This request has expired: the agent will have to ask again.",
  "link.invalid": "Invalid or expired link.",
  "link.tooMany": "Too many attempts. Try again shortly.",
  "link.openApp": "Open Aegis",

  "new.title": "Connect an agent",
  "new.lead":
    "An agent starts with no permissions at all. What you choose here is recorded and everything else stays denied.",
  "new.name": "Name",
  "new.namePh": "Sales assistant",
  "new.purpose": "What is it for? (optional)",
  "new.purposePh": "Read customers and draft replies",
  "new.presetLegend": "What may it do?",
  "new.presetRecommended": "Recommended",
  "new.presetRecommendedDesc":
    "Reads freely. Email to outside recipients and changes to data go through you. Deleting, moving money and exporting are always forbidden.",
  "new.presetCustom": "Custom",
  "new.presetCustomDesc": "You choose, action by action.",
  "new.create": "Create the agent",
  "new.creating": "Creating…",
  "new.toCaps": "Next: permissions",
  "new.stepCaps": "What may it do?",
  "new.capsLead": "Everything is denied until you say otherwise.",
  "new.colCap": "Action",
  "new.colMode": "Execution",
  "new.allow": "Allow",
  "new.human": "Ask a person",
  "new.deny": "Deny",
  "new.executes": "run by Aegis",
  "new.decisionOnly": "decision only",
  "new.irreversible": "irreversible",
  "new.decisionOnlyNote":
    "“Decision only”: Aegis answers allowed, person or blocked, but your workflow performs the action.",
  "new.review": "Summary",
  "new.toReview": "Next: summary",
  "new.nothingGranted":
    "Nothing is granted: the agent will be created but every request it makes will be refused.",
  "new.stepConnect": "Done. Now connect the agent",
  "new.gmailNote":
    "One step left: connect a Gmail mailbox and grant this agent access, from its page. Until you do, every Gmail request is refused.",
  "new.openAgent": "Open the agent page",

  "cap.crm.READ": "Read the CRM",
  "cap.crm.UPDATE": "Change the CRM",
  "cap.crm.DELETE": "Delete from the CRM",
  "cap.gmail.SEARCH": "Search Gmail",
  "cap.gmail.READ": "Read Gmail",
  "cap.gmail.DRAFT": "Draft Gmail messages",
  "cap.gmail.SEND": "Send from Gmail",
  "cap.gmail.DELETE": "Delete from Gmail",
  "cap.email.SEND": "Send email",
  "cap.files.READ": "Read files",
  "cap.files.EXPORT": "Export files",
  "cap.payments.TRANSFER": "Transfer money",

  "ad.noDescription": "No description.",
  "ad.lastContact": "Last contact",
  "ad.never": "Never: the agent has not called Aegis yet.",
  "ad.lastNote": "This is the last call received, not proof that the agent is running right now.",
  "ad.governed": "Governed by a contract",
  "ad.noContractDenied": "No active contract: everything denied",
  "ad.revoked": "revoked",
  "ad.id": "Agent ID",
  "ad.created": "Created",
  "ad.revokedAt": "Revoked",
  "ad.caps": "What it may do",
  "ad.noPerms": "No permissions: every request from this agent is refused.",
  "policy.note":
    "“Allowed” applies unless an organization rule (menu “Rules”) still requires a person: for example, email to outside recipients.",
  "ad.colCap": "Action",
  "ad.colScope": "Scope",
  "ad.colEffect": "Effect",
  "ad.colContract": "In contract",
  "ad.human": "needs a person",
  "ad.allow": "allowed",
  "ad.deny": "denied",
  "ad.inContract": "yes",
  "ad.notInContract": "no: the contract refuses it",
  "ad.contract": "Contract",
  "ad.revokeContract": "Revoke contract",
  "ad.noContracts": "No contract: this agent has no permissions at all.",
  "ad.colContractId": "Contract",
  "ad.colVersion": "Ver.",
  "ad.colStatus": "Status",
  "ad.colPurpose": "Purpose",
  "ad.colExpires": "Expires",
  "ad.revokeAgent": "Revoke the agent",
  "ad.confirmRotate": "Generate a new key? The old one stops working immediately.",
  "ad.confirmRevoke": "Revoke this agent? It stops working immediately.",
  "ad.revokeNote": "Revoking disables the agent and all its keys: its next request is refused.",

  "cc.title": "Connect your agent",
  "cc.lead":
    "Your agent (n8n, Make, a script…) asks Aegis before it acts. You need two things: an address and a key.",
  "cc.waiting": "Waiting for the first call…",
  "cc.seen": "Connected: last call {when}",
  "cc.address": "1. Aegis address",
  "cc.addressLocal":
    "The public address is not configured: this is the address of the Aegis you are using.",
  "cc.key": "2. Agent key",
  "cc.keyOnce": "Copy it now: for safety Aegis cannot show it to you again.",
  "cc.keyLost":
    "The key is shown once, when it is created. If you lost it, generate a new key: the old one stops working.",
  "cc.rotate": "Generate a new key",
  "cc.tool": "3. Connect your tool",
  "cc.n8nTitle": "n8n (recommended)",
  "cc.n8nHow":
    "Add an “HTTP Request” node, choose “Import cURL” and paste the command below. If the answer is APPROVAL, wait with a “Wait” node and repeat the same request with the same request_id: the answer becomes ALLOW or BLOCK.",
  "cc.pythonTitle": "Python",
  "cc.keyIn": "Your key is already inserted: do not share this text.",
  "cc.keyOut": "Replace YOUR_AGENT_TOKEN with your key.",
};

const dictionaries: Record<Lang, Record<Key, string>> = { it, en };

export function t(key: Key, vars: Record<string, string | number> = {}): string {
  return dictionaries[current][key].replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
}

/** For keys built from server values (a decision, a status): the raw value when untranslated. */
export function tOr(key: string, fallback: string): string {
  return (dictionaries[current] as Record<string, string>)[key] ?? fallback;
}

export const capLabel = (kind: string, action: string) =>
  tOr(`cap.${kind}.${action.toUpperCase()}`, `${kind}.${action}`);
export const decisionLabel = (decision: string) => tOr(`decision.${decision}`, decision);
export const statusLabel = (status: string) => tOr(`status.${status}`, status);

/** Subscribes the component, so it re-renders when the language changes. */
export function useT() {
  useSyncExternalStore(subscribe, getLang);
  return t;
}

export function useLang(): Lang {
  return useSyncExternalStore(subscribe, getLang);
}
