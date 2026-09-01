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
    "studies.title": "Studies",
    "studies.sub": "what has been published",
    "studies.verb": "read",
    "studies.all": "See them all in the catalogue →",
    "studies.someOf": "Showing {shown} of {total} — see them all in the catalogue →",
    "studies.embargo": "under embargo until {date}",
    "studies.openIn": "open in…",
    "catalog.silent": "The catalogue at {base} did not answer this page — {error}. On this node it is reachable, but not from a browser on this origin.",
    "hdt.title": "Monuments",
    "hdt.sub": "the subject, which endures",
    "hdt.verb": "explore",
    "hdt.unnamed": "unnamed",
    "hdt.someOf": "Showing {shown} of {total} monuments — see them all in the catalogue →",
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
    "hdt.studies.one": "1 study",
    "hdt.studies.many": "{n} studies",
    "hdt.itsStudies": "its studies",
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
    "studies.title": "Gli studi",
    "studies.sub": "cosa è stato pubblicato",
    "studies.verb": "leggi",
    "studies.all": "Vedi tutti nel catalogo →",
    "studies.someOf": "Ne mostro {shown} su {total} — vedili tutti nel catalogo →",
    "studies.embargo": "sotto embargo fino al {date}",
    "studies.openIn": "apri in…",
    "catalog.silent": "Il catalogo a {base} non ha risposto a questa pagina — {error}. Da questo nodo è raggiungibile, ma non da un browser su questa origine.",
    "hdt.title": "I monumenti",
    "hdt.sub": "il soggetto, che dura",
    "hdt.verb": "esplora",
    "hdt.unnamed": "senza nome",
    "hdt.someOf": "Ne mostro {shown} su {total} — vedili tutti nel catalogo →",
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
    "hdt.studies.one": "1 studio",
    "hdt.studies.many": "{n} studi",
    "hdt.itsStudies": "gli studi",
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
