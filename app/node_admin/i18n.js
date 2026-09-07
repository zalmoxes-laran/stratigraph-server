/**
 * SIX LANGUAGES, and English is the source.
 *
 * Beside it, the languages of the project's case studies (T2.3) — `it` `ro` `el`
 * `es` `pl` — because those are the languages somebody will actually excavate
 * in. `en` and `it` are complete; the other four carry THE SAME KEYS with empty
 * values, which fall back to English.
 *
 * That is not laziness, and it is the honest arrangement: **translating is the
 * partners' work**, each for their own language and their own dig. A string
 * invented by us in a language none of us re-reads is worse than the English it
 * replaced. What we build is the slot, and a test keeps it from having holes.
 *
 * **One convention, one implementation per surface.** This is the room server's
 * two faces (the node's front door and the operator console). The catalogue has
 * its own, and the field assistant keeps its dictionaries INLINE because it is
 * one HTML file on purpose. A library across the three stacks would be the fifth
 * place to keep aligned — see the report of 2026-09-01.
 *
 * **Keys are English phrases**, not `msg_1`: a key you can read is a key you can
 * check against the screen without running anything.
 *
 * **No endpoint serves this.** A surface that has to ask somebody how to say
 * "Sign out" does not speak offline.
 *
 * WHERE THE CHOICE LIVES: `localStorage`, and the reason is not laziness either.
 * The language is not a credential, and it belongs to the DEVICE and not to the
 * person — like the field assistant's queue, and unlike its token. *A borrowed
 * tablet must change author, not language.*
 */

export const LOCALES = ["en", "it", "ro", "el", "es", "pl"];
export const LOCALE_NAMES = { en: "English", it: "Italiano", ro: "Română",
                              el: "Ελληνικά", es: "Español", pl: "Polski" };
const LOCALE_KEY = "sg.locale.v1";

// What is NOT here, ever: US, DTC, HDT, ORCID, em.json, crmdig:D7, a room's id,
// a study's em_id. Those are TERMS, not text, and a translated term is a term
// lost. See `stratigraph-brand/GLOSSARY.md`.
const STRINGS = {
  en: {
    "app.title": "This node",
    "app.sub": "this node",
    "action.signin": "Sign in",
    "action.signout": "Sign out",
    "gate.why": "Sign in to see the rooms you work in, and to open one in a tool.",
    "gate.noOidc": "This node does not say how to sign in, so there is nobody to sign in as.",
    "gate.devMode": "This node is in dev mode: it verifies no identity",
    "gate.devMode.missing": "Missing: {what}.",
    "gate.unreachable": "Cannot reach this node's API at {base} — {error}",
    "node.line": "{service} {version} · {where} · {auth}",
    "node.auth.on": "identities are verified",
    "node.auth.off": "identities are NOT verified (dev mode)",
    "node.silent": "This node did not say who it is.",
    "here.title": "What is here",
    "here.sub": "What this node runs, and what you run.",
    "here.go": "go →",
    "here.yours": "on your own machine",
    "here.cannotKnow": "This node cannot know whether you have it — only where it is.",
    "here.download": "download →",
    // A capability's NAME and STATE come from the neighbour and are shown raw:
    // translating them would mean this page keeping a list of which capabilities
    // exist in the world, and then a third one would not appear until somebody
    // edited it. The only chrome here is this one word.
    "here.capability.needs": "needs",
    // The service LABELS are chrome and are translated. Their `detail` is the
    // NODE talking (a probe's sentence) and is not: it is diagnostic prose in
    // the source language, like an answer from the field assistant's node, and
    // it is shown only when something is wrong.
    "service.stratigraph-catalog": "Catalogue",
    "service.iiif": "Images (IIIF)",
    "service.stratigraph-chatbot": "Field assistant",
    "service.nodeodm": "Photogrammetric engine",
    "here.manual": "manual →",
    "rooms.title": "Rooms",
    "rooms.sub": "where the work is happening now",
    "rooms.none": "No rooms yet. Make one above — you will be its owner.",
    "rooms.newName": "A new room's name…",
    "rooms.create": "Create room",
    "rooms.needsName": "A room needs a name.",
    "rooms.created": "Created — you are its owner.",
    "rooms.missingRefs": "container not in the store: {refs}",
    "rooms.verb": "enter",
    // ── WHERE TO GO · the vestibule's four verbs ─────────────────────────────
    // «HDT» is a TERM and is not translated — the dictionary's own rule, and
    // «Monumenti» was the translation of one. Putting it back closes an
    // incoherence instead of opening one.
    "face.door": "This node",
    "face.work": "Work",
    "face.tools": "Tools",
    "face.admin": "Console",
    "bar.silent": "this node did not answer",
    "go.back": "← this node",
    "go.title": "Where to go",
    "go.sub": "This node, and what you came for.",
    "go.consult": "Consult — studies and HDT",
    "go.consult.sub": "The published, still and citable. In the catalogue.",
    "go.work": "Work — rooms",
    "go.work.sub": "Enter a room, create one, bring a file in. A room is the live copy.",
    "go.tools": "Equip yourself",
    "go.tools.sub": "What runs on this node, and what you install on your own machine.",
    "go.admin": "Administer",
    "go.admin.sub": "The console, and the map of this node.",
    // ── CONDIVIDI · layers 1 and 2 of the design note, and NOT layers 3 and 4 ──
    // Presence (who may be at the table) and property (whose the live copy is)
    // live on this node. CONTRIBUTION is not a datum of the system and
    // AUTHORSHIP is agreed when publishing, on the version — so no string here
    // names an author, an order of names or a scientific direction, and
    // `test_share_panel.py` fails if one does.
    // The four roles, named where chrome is named. They are words on a button,
    // not domain terms: `viewer`/`editor`/`admin`/`owner` travel over the wire
    // untranslated and this is only how they READ.
    "role.viewer": "viewer",
    "role.editor": "editor",
    "role.admin": "admin",
    "role.owner": "owner",
    "share.title": "Share",
    "share.sub": "who is at this table, and whose the live copy is",
    "share.room": "Room {room} — {title}",
    "share.notAllowed": "Sharing a room needs admin or owner. You can work in it; who else may is somebody else's to decide.",
    "share.unreachable": "This node did not answer about {room} — {error}",
    "share.who": "Who is here",
    "share.you": "you",
    "share.team": "team",
    "share.teamOf": "{n} people",
    "share.teamUnknown": "this node has no team by that name any more — the grant is still here, and grants nobody",
    "share.nobody": "Nobody but the owner.",
    "board.title": "The room",
    "board.sub": "who is here, what is waiting, and what it still owes",
    "board.who.head": "Who is here",
    "board.who.in": "in",
    "board.who.quiet": "quiet for {for}",
    "board.who.justQuiet": "less than a minute",
    "board.who.writes": "writes",
    "board.who.reads": "reads",
    "board.who.left": "closed and left",
    "board.who.lost": "went quiet, then the connection went",
    "board.who.nobody": "Nobody is seated in this room right now.",
    "board.waiting.head": "Waiting for you",
    "board.waiting.fields": "{n} fields",
    "board.waiting.none": "Nothing is waiting for your signature here.",
    "board.unsaved": "{n} operations this room has not written down yet",
    "board.changes.head": "Since you last looked",
    "board.changes.none": "Nothing has changed since then.",
    "board.changes.never": "Pick a window: this room is asked from an instant, not «everything».",
    "board.changes.more": "…and {n} more units touched: the list is cut, the count is not.",
    "board.since.mine": "since I last wrote here",
    "board.since.hour": "the last hour",
    "board.since.day": "the last day",
    "board.since.week": "the last week",
    "board.since.note": "{n} operations after {from}.",
    "board.horizon.none": "This room has no durable register: it cannot look back past this process.",
    "board.horizon.reads": "This room can read back to {from}.",
    "board.horizon.replays": "It can replay from {from} — older than that is readable and not applicable.",
    "board.horizon.short": "You are asking from further back than {from}: what is below is a PART, not everything.",
    "board.chat.head": "What we said to each other",
    "board.chat.none": "Nothing has been said in this room yet.",
    "board.chat.nobody": "no name",
    "board.chat.retracted": "— retracted —",
    "board.chat.retractedN": "{n} retracted.",
    "board.chat.more": "{n} earlier messages are not shown.",
    "board.chat.voices": "{n} voices.",
    "board.chat.boundary": "A conversation stays in the room: none of this travels in a published study until somebody promotes it, and nobody can yet.",
    "board.people.head": "Who dug what",
    "board.people.made": "{n} made",
    "board.people.fields": "{n} fields",
    "board.people.noTool": "tool not declared",
    "board.people.none": "Nobody has written in this room yet.",
    "board.people.whence": "Every name here is the identity of the token that wrote, stamped by this server — not a field somebody typed.",
    "board.numbers.head": "The numbers, and their holes",
    "board.n.units": "stratigraphic units",
    "board.n.nodes": "nodes",
    "board.n.edges": "relations",
    "board.n.fields": "fields declaring an author",
    "board.h.epoch": "{n} without an epoch",
    "board.h.alone": "{n} with no relation",
    "board.h.author": "{n} with nobody's name on them",
    "board.h.waiting": "{n} written by a machine and not signed",
    "board.hole.none": "no hole",
    "board.hole.na": "no hole to count: a relation is between two things that exist, so a missing one is a missing unit and is counted there",
    "board.cannot": "What cannot be counted from here:",
    "share.add": "Add",
    "share.addTeam": "Add team",
    "share.noTeams": "No teams on this node yet.",
    "share.allTeamsHere": "Every team on this node is already at this table.",
    "share.remove": "remove",
    "share.removed": "{who} has no role here any more.",
    "share.granted": "{who}: {role}.",
    "share.invite": "Invite by link",
    "share.makeLink": "Make a link",
    "share.linkOnce": "Copy it now — this node keeps only a checksum, so the link cannot be shown again.",
    "share.inviteState": "{role} · {state}",
    "share.inviteUses": "used {uses} of {max}",
    "share.inviteExpires": "expires {when}",
    "share.inviteNever": "no expiry",
    "share.revoke": "revoke",
    "share.noInvites": "No links.",
    "share.live": "The live copy",
    "share.owner": "Owner: {who}",
    "share.ownerNobody": "This room has no owner recorded.",
    "share.transfer": "transfer…",
    "share.transferOne": "A room has one owner. Transferring hands over the operational job — letting people in, managing the table, pushing a version. It does NOT hand over the right to sign the work: the names on a published version are agreed when publishing.",
    "share.transferAsk": "Transfer {room} to which ORCID iD? The previous owner stays as an admin.",
    "share.archive": "Archive",
    "share.archiveWhy": "A mark, never a deletion: a workspace goes quiet for a season and comes back. It stays listed, keeps its title and its references.",
    "share.archiveDo": "archive this room",
    "share.restoreDo": "bring it back",
    "share.archived": "Archived {when}.",
    "share.notArchived": "This room is live.",
    "map.title": "Node map",
    "map.sub": "where every face is, and how to ask it the same question from a terminal",
    "map.entrances": "This server",
    "map.neighbours": "Around it",
    "map.verdict": "The node's own verdict: {verdict}. Each probe was bounded at {deadline}s — «unreachable» means it did not answer in that time, not that it is gone.",
    "map.unreachable": "This map could not be loaded: {error}. The node is answering enough to serve this page, so the failure is in the health report itself.",
    "map.open": "open →",
    "map.noBrowser": "no browser address configured for this face",
    "map.curl": "copy curl",
    "map.curlTitle": "Copies the exact question the probe just asked. Paste it in a terminal: if the two answers differ, this row is lying and that is the bug.",
    "map.copied": "copied",
    "map.curlWhere": "this address is on the node's own network — run this where the node runs",
    "door.desktop": "desktop",
    "door.browser": "browser",
    "door.emjson": "em.json",
    "door.desktop.title": "Open {tool} on this machine (stratigraph:// handler)",
    "door.browser.title": "Open {tool} in a new tab ({url})",
    "door.emjson.title": "The container, to import by hand",
    "door.copy": "Copy link",
    "door.copied": "Link copied — it carries no token.",
    "door.nothingOpened": "Nothing opened — no handler for {scheme}:// on this machine. Copy the link and open it inside the tool.",
    "session.notRefreshed": "Your session could not be refreshed ({error}).",
    "session.incomplete": "Sign-in did not complete: {error}",
    "lang.label": "Language",
    // ── the operator console's CHROME. Its modules' diagnostics are NOT here
    // and that is a line, not an omission: a probe's sentence is the NODE
    // talking, in the source language, like the field assistant's answers. The
    // chrome is what the reader navigates; the diagnosis is what the node says.
    "console.notSignedIn": "not signed in",
    "console.sub": "node console",
    "console.title": "StratiGraph Server · node console",
    "console.reload": "Re-read everything from the node",
    "console.loading": "Loading…",
    "console.token.title": "Operator token",
    "console.token.why": "This console reads the node's API with your bearer token. It is kept in memory for this tab only — nothing is stored.",
    "console.token.use": "Use it",
    "console.token.paste": "Paste a bearer token…",
    "console.token.pasteInstead": "Paste a token instead (dev, or when the realm is down)",
    "console.token.refused": "That token was not accepted by this node.",
    "console.signin.realm": "Sign in with the node's realm",
    "console.signin.where": "This node authenticates against {issuer}. You will come back here signed in — the token stays in this tab and is never stored.",
    "console.signin.devMode": "This node enforces no authentication (dev-no-auth), so there is nothing to sign in to.",
    "console.signin.silent": "This node did not answer /v1/auth-config, so this console cannot tell you how to sign in.",
    "console.notOperator": "You are signed in{who} but you are not an operator of this node. Capability: {capability}. Owning a room does not make you one — an operator is named by the deployment.",
    "console.unreachable": "Cannot reach this node's API at {base} — {error}",
    "console.session.notRefreshed": "Your session could not be refreshed ({error}). Sign in again.",
    "console.confirm": "{what}\n\n{name}\n\nThis is somebody's workspace. Continue?",
    "console.confirm.typed": "{what}\n\nThis CANNOT be undone: the container and its card both go, and nothing here remembers.\n\nType the name to confirm:\n{name}",
  },
  it: {
    "app.title": "Questo nodo",
    "app.sub": "questo nodo",
    "action.signin": "Firma",
    "action.signout": "Esci",
    "gate.why": "Firma per vedere le stanze in cui lavori e per aprirne una in uno strumento.",
    "gate.noOidc": "Questo nodo non dice come si firma, quindi non c'è nessuno come cui firmare.",
    "gate.devMode": "Questo nodo è in modo di sviluppo: non verifica le identità",
    "gate.devMode.missing": "Manca: {what}.",
    "gate.unreachable": "Non raggiungo l'API di questo nodo a {base} — {error}",
    "node.line": "{service} {version} · {where} · {auth}",
    "node.auth.on": "verifica le identità",
    "node.auth.off": "NON verifica le identità (modo di sviluppo)",
    "node.silent": "Questo nodo non dice chi è.",
    "here.title": "Cosa c'è qui",
    "here.sub": "Quello che esegue questo nodo, e quello che esegui tu.",
    "here.go": "vai →",
    "here.yours": "sul tuo computer",
    "here.cannotKnow": "Questo nodo non può sapere se ce l'hai: può solo dirti dov'è.",
    "here.download": "scarica →",
    "here.capability.needs": "serve",
    "service.stratigraph-catalog": "Catalogo",
    "service.iiif": "Immagini (IIIF)",
    "service.stratigraph-chatbot": "Assistente di campo",
    "service.nodeodm": "Motore fotogrammetrico",
    "here.manual": "manuale →",
    "rooms.title": "Le stanze",
    "rooms.sub": "dove si lavora adesso",
    "rooms.none": "Nessuna stanza ancora. Creane una qui sopra: ne sarai il proprietario.",
    "rooms.newName": "Il nome di una nuova stanza…",
    "rooms.create": "Crea stanza",
    "rooms.needsName": "Una stanza ha bisogno di un nome.",
    "rooms.created": "Creata — ne sei il proprietario.",
    "rooms.missingRefs": "container non nello store: {refs}",
    "rooms.verb": "entra",
    "face.door": "Questo nodo",
    "face.work": "Lavorare",
    "face.tools": "Attrezzarsi",
    "face.admin": "Console",
    "bar.silent": "questo nodo non ha risposto",
    "go.back": "← questo nodo",
    "go.title": "Dove andare",
    "go.sub": "Questo nodo, e quello per cui sei venuto.",
    "go.consult": "Consultare — studi e HDT",
    "go.consult.sub": "Il pubblicato, fermo e citabile. Nel catalogo.",
    "go.work": "Lavorare — stanze",
    "go.work.sub": "Entra in una stanza, creane una, portaci dentro un file. Una stanza è la copia viva.",
    "go.tools": "Attrezzarsi",
    "go.tools.sub": "Cosa gira su questo nodo, e cosa installi sulla tua macchina.",
    "go.admin": "Amministrare",
    "go.admin.sub": "La console, e la mappa di questo nodo.",
    "role.viewer": "in lettura",
    "role.editor": "in scrittura",
    "role.admin": "amministra",
    "role.owner": "proprietario",
    "share.title": "Condividi",
    "share.sub": "chi è a questo tavolo, e di chi è la copia viva",
    "share.room": "Stanza {room} — {title}",
    "share.notAllowed": "Condividere una stanza richiede admin o owner. Puoi lavorarci; chi altro può è una decisione di qualcun altro.",
    "share.unreachable": "Questo nodo non ha risposto su {room} — {error}",
    "share.who": "Chi c'è",
    "share.you": "tu",
    "share.team": "squadra",
    "share.teamOf": "{n} persone",
    "share.teamUnknown": "questo nodo non ha più una squadra con quel nome — il grant è ancora qui, e non dà niente a nessuno",
    "share.nobody": "Nessuno oltre al proprietario.",
    "board.title": "La stanza",
    "board.sub": "chi c'è, cosa aspetta, e cosa deve ancora",
    "board.who.head": "Chi c'è",
    "board.who.in": "dentro",
    "board.who.quiet": "silenzioso da {for}",
    "board.who.justQuiet": "meno di un minuto",
    "board.who.writes": "scrive",
    "board.who.reads": "legge",
    "board.who.left": "ha chiuso ed è uscito",
    "board.who.lost": "ha smesso di parlare, poi è caduta la connessione",
    "board.who.nobody": "In questa stanza adesso non è seduto nessuno.",
    "board.waiting.head": "Cosa aspetta te",
    "board.waiting.fields": "{n} campi",
    "board.waiting.none": "Qui non c'è niente che aspetti la tua firma.",
    "board.unsaved": "{n} operazioni che questa stanza non ha ancora scritto",
    "board.changes.head": "Da quando non guardavi",
    "board.changes.none": "Da allora non è cambiato niente.",
    "board.changes.never": "Scegli una finestra: a questa stanza si chiede da un istante, non «tutto».",
    "board.changes.more": "…e altre {n} unità toccate: l'elenco è tagliato, il conto no.",
    "board.since.mine": "dal mio ultimo intervento",
    "board.since.hour": "l'ultima ora",
    "board.since.day": "l'ultimo giorno",
    "board.since.week": "l'ultima settimana",
    "board.since.note": "{n} operazioni dopo {from}.",
    "board.horizon.none": "Questa stanza non ha un registro durevole: non sa guardare indietro oltre questo processo.",
    "board.horizon.reads": "Questa stanza sa leggere indietro fino a {from}.",
    "board.horizon.replays": "Rigiocare si può da {from} — più indietro si legge e non si riapplica.",
    "board.horizon.short": "Stai chiedendo da più indietro di {from}: quello che segue è una PARTE, non tutto.",
    "board.chat.head": "Cosa ci siamo detti",
    "board.chat.none": "In questa stanza non si è ancora detto niente.",
    "board.chat.nobody": "senza nome",
    "board.chat.retracted": "— ritrattato —",
    "board.chat.retractedN": "{n} ritrattati.",
    "board.chat.more": "{n} messaggi più vecchi non sono mostrati.",
    "board.chat.voices": "{n} voci.",
    "board.chat.boundary": "Una conversazione resta nella stanza: niente di questo viaggia in uno studio pubblicato finché qualcuno non lo promuove, e per ora nessuno può.",
    "board.people.head": "Chi ha scavato cosa",
    "board.people.made": "{n} create",
    "board.people.fields": "{n} campi",
    "board.people.noTool": "attrezzo non dichiarato",
    "board.people.none": "In questa stanza non ha ancora scritto nessuno.",
    "board.people.whence": "Ogni nome qui è l'identità del token che ha scritto, timbrata da questo server — non un campo che qualcuno ha digitato.",
    "board.numbers.head": "I numeri, e i loro buchi",
    "board.n.units": "unità stratigrafiche",
    "board.n.nodes": "nodi",
    "board.n.edges": "rapporti",
    "board.n.fields": "campi che dichiarano un autore",
    "board.h.epoch": "{n} senza epoca",
    "board.h.alone": "{n} senza nessun rapporto",
    "board.h.author": "{n} senza il nome di nessuno",
    "board.h.waiting": "{n} scritti da una macchina e non firmati",
    "board.hole.none": "nessun buco",
    "board.hole.na": "nessun buco da contare: un rapporto sta fra due cose che esistono, quindi quello che manca è un'unità e si conta lì",
    "board.cannot": "Cosa non si può contare da qui:",
    "share.add": "Aggiungi",
    "share.addTeam": "Aggiungi squadra",
    "share.noTeams": "Nessuna squadra su questo nodo, per ora.",
    "share.allTeamsHere": "Tutte le squadre di questo nodo sono già a questo tavolo.",
    "share.remove": "togli",
    "share.removed": "{who} non ha più nessun ruolo qui.",
    "share.granted": "{who}: {role}.",
    "share.invite": "Invita con un link",
    "share.makeLink": "Crea un link",
    "share.linkOnce": "Copialo adesso — questo nodo ne conserva solo un'impronta, quindi il link non può essere mostrato di nuovo.",
    "share.inviteState": "{role} · {state}",
    "share.inviteUses": "usato {uses} volte su {max}",
    "share.inviteExpires": "scade {when}",
    "share.inviteNever": "senza scadenza",
    "share.revoke": "revoca",
    "share.noInvites": "Nessun link.",
    "share.live": "La copia viva",
    "share.owner": "Proprietario: {who}",
    "share.ownerNobody": "Questa stanza non ha un proprietario registrato.",
    "share.transfer": "trasferisci…",
    "share.transferOne": "Una stanza ha un proprietario solo. Trasferire consegna il mestiere operativo — far entrare le persone, gestire il tavolo, spingere una versione. NON consegna il diritto di firmare il lavoro: i nomi su una versione pubblicata si concordano al momento di pubblicare.",
    "share.transferAsk": "Trasferire {room} a quale ORCID iD? Il proprietario precedente resta come admin.",
    "share.archive": "Archivia",
    "share.archiveWhy": "Un segno, mai una cancellazione: un tavolo tace per una stagione e poi torna. Resta elencato, tiene il titolo e i suoi riferimenti.",
    "share.archiveDo": "archivia questa stanza",
    "share.restoreDo": "rimettila in piedi",
    "share.archived": "Archiviata il {when}.",
    "share.notArchived": "Questa stanza è viva.",
    "map.title": "Mappa del nodo",
    "map.sub": "dove sta ogni faccia, e come farle la stessa domanda da terminale",
    "map.entrances": "Questo server",
    "map.neighbours": "Attorno",
    "map.verdict": "Il verdetto del nodo: {verdict}. Ogni sonda ha avuto {deadline}s — «unreachable» vuol dire che non ha risposto in quel tempo, non che non c'è.",
    "map.unreachable": "Non ho potuto caricare la mappa: {error}. Il nodo risponde abbastanza per servire questa pagina, quindi il guasto è nel rapporto di salute.",
    "map.open": "apri →",
    "map.noBrowser": "nessun indirizzo browser configurato per questa faccia",
    "map.curl": "copia curl",
    "map.curlTitle": "Copia la domanda esatta che la sonda ha appena fatto. Incollala in un terminale: se le due risposte divergono, questa riga sta mentendo ed è quello il baco.",
    "map.copied": "copiato",
    "map.curlWhere": "questo indirizzo è sulla rete del nodo — esegui questa riga dove gira il nodo",
    "door.desktop": "desktop",
    "door.browser": "browser",
    "door.emjson": "em.json",
    "door.desktop.title": "Apri {tool} su questa macchina (handler stratigraph://)",
    "door.browser.title": "Apri {tool} in una scheda ({url})",
    "door.emjson.title": "Il contenitore, da importare a mano",
    "door.copy": "Copia link",
    "door.copied": "Link copiato — non porta nessun token.",
    "door.nothingOpened": "Non si è aperto nulla: su questa macchina non c'è un handler per {scheme}://. Copia il link e aprilo dentro lo strumento.",
    "session.notRefreshed": "La sessione non si è potuta rinnovare ({error}).",
    "session.incomplete": "La firma non si è completata: {error}",
    "lang.label": "Lingua",
    "console.notSignedIn": "non firmato",
    "console.sub": "console del nodo",
    "console.title": "StratiGraph Server · console del nodo",
    "console.reload": "Rileggi tutto dal nodo",
    "console.loading": "Sto caricando…",
    "console.token.title": "Token dell'operatore",
    "console.token.why": "Questa console legge l'API del nodo col tuo bearer token. Resta in memoria solo per questa scheda: niente viene salvato.",
    "console.token.use": "Usalo",
    "console.token.paste": "Incolla un bearer token…",
    "console.token.pasteInstead": "Incolla invece un token (sviluppo, o quando il realm è giù)",
    "console.token.refused": "Questo nodo non ha accettato quel token.",
    "console.signin.realm": "Firma con il realm del nodo",
    "console.signin.where": "Questo nodo autentica contro {issuer}. Tornerai qui firmato — il token resta in questa scheda e non viene mai salvato.",
    "console.signin.devMode": "Questo nodo non impone autenticazione (dev-no-auth), quindi non c'è nulla in cui firmare.",
    "console.signin.silent": "Questo nodo non ha risposto a /v1/auth-config, quindi questa console non può dirti come si firma.",
    "console.notOperator": "Sei firmato{who} ma non sei un operatore di questo nodo. Capability: {capability}. Possedere una stanza non ti rende tale — un operatore lo nomina il deployment.",
    "console.unreachable": "Non raggiungo l'API di questo nodo a {base} — {error}",
    "console.session.notRefreshed": "La sessione non si è potuta rinnovare ({error}). Rifirma.",
    "console.confirm": "{what}\n\n{name}\n\nQuesto è lo spazio di lavoro di qualcuno. Continuo?",
    "console.confirm.typed": "{what}\n\nQuesto NON si torna indietro: il contenitore e la sua scheda vanno via tutti e due, e qui niente li ricorda.\n\nScrivi il nome per confermare:\n{name}",
  },
  // ── the partners' slots: same keys, empty values, falling back to `en` ──────
  // The four other project locales hold the SAME KEYS with empty values, which
  // is what `tests/test_locales.py` defends: an empty value falls back to
  // English, a MISSING key is a hole nobody can see until somebody switches
  // language. The map's keys go in here the day they are translated.
  ro: {
    "map.title": "",
    "map.sub": "",
    "map.entrances": "",
    "map.neighbours": "",
    "map.verdict": "",
    "map.unreachable": "",
    "map.open": "",
    "map.noBrowser": "",
    "map.curl": "",
    "map.curlTitle": "",
    "map.copied": "",
    "map.curlWhere": "",
  },
  el: {
    "map.title": "",
    "map.sub": "",
    "map.entrances": "",
    "map.neighbours": "",
    "map.verdict": "",
    "map.unreachable": "",
    "map.open": "",
    "map.noBrowser": "",
    "map.curl": "",
    "map.curlTitle": "",
    "map.copied": "",
    "map.curlWhere": "",
  },
  es: {
    "map.title": "",
    "map.sub": "",
    "map.entrances": "",
    "map.neighbours": "",
    "map.verdict": "",
    "map.unreachable": "",
    "map.open": "",
    "map.noBrowser": "",
    "map.curl": "",
    "map.curlTitle": "",
    "map.copied": "",
    "map.curlWhere": "",
  },
  pl: {
    "map.title": "",
    "map.sub": "",
    "map.entrances": "",
    "map.neighbours": "",
    "map.verdict": "",
    "map.unreachable": "",
    "map.open": "",
    "map.noBrowser": "",
    "map.curl": "",
    "map.curlTitle": "",
    "map.copied": "",
    "map.curlWhere": "",
  },
};

// Every locale carries every key of `en` — empty where nobody has translated it
// yet, so a translator sees the whole list and a test can count the holes.
for (const code of LOCALES) {
  for (const key of Object.keys(STRINGS.en)) {
    if (!(key in STRINGS[code])) STRINGS[code][key] = "";
  }
}

function pick() {
  try {
    const saved = localStorage.getItem(LOCALE_KEY);
    if (saved && LOCALES.includes(saved)) return saved;
  } catch { /* no storage: the browser's own language decides */ }
  const asked = (navigator.language || "en").slice(0, 2).toLowerCase();
  return LOCALES.includes(asked) ? asked : "en";
}

export let LOCALE = pick();

/** A string in the active locale, falling back to English when nobody has
 *  translated it. `{placeholders}` are filled from `values`. */
export function t(key, values) {
  const text = (STRINGS[LOCALE] && STRINGS[LOCALE][key]) || STRINGS.en[key] || key;
  return values
    ? text.replace(/\{(\w+)\}/g, (_m, name) => String(values[name] ?? ""))
    : text;
}

/** Change language and remember it on THIS DEVICE. `onChange` repaints. */
export function setLocale(code, onChange) {
  if (!LOCALES.includes(code)) return;
  LOCALE = code;
  try { localStorage.setItem(LOCALE_KEY, code); } catch { /* nothing to do */ }
  document.documentElement.lang = code;
  if (onChange) onChange();
}

/** The picker, written so that each option is IN its own language: somebody who
 *  cannot read the current one must still find theirs in the list. */
export function mountPicker(select, onChange) {
  if (!select) return;
  select.innerHTML = "";
  select.setAttribute("aria-label", t("lang.label"));
  for (const code of LOCALES) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = LOCALE_NAMES[code];
    if (code === LOCALE) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener("change", () => setLocale(select.value, onChange));
}
