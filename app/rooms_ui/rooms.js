/**
 * THE NODE'S FRONT DOOR — sign in, see what is here, and go somewhere.
 *
 * This file is the room browser's, grown. What it gained is a HEAD (which node
 * is this, who am I on it, what does it run) and two more scopes beside the
 * rooms — the studies and the monuments.
 *
 * **It owns nothing.** Not a list of services, not an address of a neighbour,
 * not a rule about who may see what. Every one of those is asked:
 *
 *   · `/v1/node` says what this node offers, WITH LIVE STATES and with public
 *     addresses where the deployment published one. A service that is down
 *     appears down; one whose address nobody configured appears without a link,
 *     because a guessed path is a button that 404s;
 *   · `/v1/rooms` is ALREADY ACL-filtered. There is no visibility logic here and
 *     there must not be: a second implementation of the rule, in a place that is
 *     not the one that decides, is a rule with two answers;
 *   · every door — a room's, a study's — is ASKED (`…/open`) and never
 *     assembled. There is a test in another repository that forbids exactly the
 *     hand-built `stratigraph://`.
 *
 * **It imports the node console's sign-in module rather than copying it**
 * (`../admin/auth.js`, relative so the `/em` prefix a proxy adds comes along).
 * One PKCE implementation for the faces this server serves.
 *
 * **What it never holds:** a token on disk. The access token lives in a
 * module-scoped variable for this tab, refreshed at 80% of its lifetime, and the
 * links it produces carry no credential at all — that is the handoff contract,
 * not a convention (`app/handoff.py`).
 */

import * as oidc from "../admin/auth.js";

/** The API, derived from where this page is served — the same reasoning (and the
 *  same measured trap) as the console's: at `/em/rooms/index.html` a naive strip
 *  produces `/em/rooms/index.html/v1` and every call 404s behind a "Loading…".
 *
 *  Taken from the document's own DIRECTORY rather than from its path, which is
 *  what makes `index.html` and the bare directory the same case (the chatbot's
 *  page learned this first). Measured on the three spellings that exist:
 *
 *      /em/rooms/  → /em/v1      behind the node's Caddy
 *      /rooms/     → /v1         bare, `uvicorn --port 8000`
 *      /em/rooms/index.html      the directory is still /em/rooms/
 */
const BASE = new URL(".", window.location.href).pathname
  .replace(/rooms\/$/, "").replace(/\/$/, "") + "/v1";

let token = "";
let refreshToken = "";
let refreshTimer = 0;
let authConfig = null;
let node = null;          // what /v1/node answered: this node's own description
let me = null;

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// ── the node, with the token attached once ──────────────────────────────────

async function request(method, path, body) {
  return await call(`${BASE}${path}`, method, body);
}

/** …and the same call to an ABSOLUTE address, for the catalogue next door. The
 *  address is never written here: it comes from `/v1/node`. */
async function call(url, method = "GET", body) {
  const answer = await fetch(url, {
    method,
    headers: {
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const text = await answer.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = null; }
  if (!answer.ok) {
    const error = new Error((payload && payload.detail) || `HTTP ${answer.status}`);
    error.status = answer.status;
    throw error;
  }
  return payload;
}

/** Where the catalogue is, according to the NODE. "" when this deployment
 *  published none — and then the two catalogue zones stay away rather than
 *  guessing a path beside our own. */
function catalogBase() {
  const offer = (node?.offers || []).find((o) => o.name === "stratigraph-catalog");
  return (offer && offer.state !== "not configured" && offer.url) || "";
}

// ── session ─────────────────────────────────────────────────────────────────

function scheduleRefresh(seconds) {
  window.clearTimeout(refreshTimer);
  if (!refreshToken || !seconds) return;
  const wait = Math.max(15, Math.floor(seconds * 0.8)) * 1000;
  refreshTimer = window.setTimeout(async () => {
    const result = await oidc.refresh(authConfig, refreshToken);
    if (!result.ok) {
      note($("gate-note"), `Your session could not be refreshed (${result.error}).`,
           true);
      return;
    }
    token = result.token;
    refreshToken = result.refresh_token || refreshToken;
    scheduleRefresh(result.expires_in);
  }, wait);
}

function adoptSession(result) {
  token = result.token;
  refreshToken = result.refresh_token || "";
  scheduleRefresh(result.expires_in);
}

function note(element, text, bad = false) {
  if (!element) return;
  element.textContent = text || "";
  element.classList.toggle("warn", Boolean(bad));
}

// ── THE CARD — one component, three scopes ──────────────────────────────────
//
// A monument, a study and a room have the same shape: a name, a subject, who may
// see it, and a set of doors. They differ by the VERB, which is what tells them
// apart on screen — esplora · leggi · entra — and by where the doors come from.
//
// If a second card ever appears in this file, the road that multiplies has been
// taken: the three are the same thing at three moments of its life, and the
// component is the place that says so.

function card({ title, id, tag, notes = [], verb, build }) {
  const box = el("div", "room");
  const head = el("div", "room-head");
  head.append(el("span", "room-title", title));
  if (id) head.append(el("span", "room-id", id));
  if (verb) head.append(el("span", "verb", verb));
  if (tag) head.append(el("span", "role", tag));
  box.append(head);
  for (const line of notes.filter(Boolean)) box.append(el("p", "note", line));
  const actions = el("div", "room-actions");
  const said = el("span", "note");
  box.append(actions);
  if (build) build(actions, said, box);
  actions.append(said);
  return box;
}

/** A tool NAME with up to two doors, the shape both scopes use. */
function toolGroup(label, doors) {
  const group = el("span", "tool");
  group.append(el("span", "tool-name", label));
  for (const [name, title, go] of doors) {
    const button = el("button", "", name);
    button.title = title;
    button.addEventListener("click", go);
    group.append(button);
  }
  return group;
}

// ── zone 1 · the rooms — where the work is happening now ────────────────────

function renderRooms(rooms) {
  const host = $("rooms");
  host.innerHTML = "";
  const live = (rooms || []).filter((r) => !r.archived_at);
  $("zone-rooms").hidden = false;
  if (!live.length) {
    host.append(el("p", "note",
      "No rooms yet. Make one above — you will be its owner."));
    return;
  }
  for (const room of live) host.append(roomCard(room));
}

function roomCard(room) {
  return card({
    title: room.title || room.room_id,
    id: room.room_id,
    tag: room.your_role || "—",
    verb: "enter",
    // REPORTED, never hidden: a workspace whose container was moved still
    // exists, and the honest answer is the name of what is missing.
    notes: (room.missing_refs || []).length
      ? [`container not in the store: ${room.missing_refs.join(", ")}`] : [],
    build(actions, said, box) {
      // The three consumers, from the server's own list (`handoff.CONSUMERS`).
      // Written out rather than looped from the answer because the answer
      // arrives only when a button is pressed, and a row with no buttons until
      // you press one is a row with no buttons.
      for (const [tool, label] of [["emstudio", "EMStudio"],
                                   ["blender", "EMtools"],
                                   ["chatbot", "Field assistant"]]) {
        const group = toolGroup(label, [["desktop",
          `Open ${label} on this machine (stratigraph:// handler)`,
          () => void openIn(room, tool, box, said)]]);
        group.dataset.tool = tool;
        actions.append(group);
      }
      const copy = el("button", "ghost", "Copy link");
      copy.addEventListener("click", () => void copyLink(room, said));
      actions.append(copy);
      // Asked once, up front, so the browser doors are there before anybody
      // presses anything. A failure is silent HERE on purpose: it costs only the
      // extra door, and a red line on every card because one node has no
      // EM_PUBLIC_BASE would be noise about a room that is fine.
      void addBrowserDoors(room, box);
    },
  });
}

/** Draw "browser" beside "desktop" for every tool that has a web build.
 *
 *  What decides is the SERVER's answer (`tools[t].browser`), never this page:
 *  whether a web app is deployed is a fact about the deployment, and a client
 *  that assumed one would offer a button that 404s. */
async function addBrowserDoors(room, box) {
  let targets;
  try { targets = await handoff(room); } catch { return; }
  for (const [tool, target] of Object.entries(targets.tools || {})) {
    if (!target.browser) continue;          // no web build: no button, no lie
    const group = box.querySelector(`.tool[data-tool="${tool}"]`);
    if (!group || group.querySelector(".browser")) continue;
    const button = el("button", "browser", "browser");
    button.title = `Open ${target.label} in a new tab (${target.browser})`;
    button.addEventListener("click", () => {
      // A plain navigation with the same two parameters the scheme carries and
      // the same absence of a token: the web build signs itself in.
      window.open(target.browser, "_blank", "noopener");
    });
    group.append(button);
  }
}

async function handoff(room) {
  // The SERVER builds the link, not this page. It knows its own public address
  // (configuration, never a request header — a link built from `Host` is a link
  // an attacker can aim), and one builder means the consumers are measured
  // against one grammar.
  return await request("GET", `/rooms/${encodeURIComponent(room.room_id)}/open`);
}

async function openIn(room, tool, box, said) {
  let targets;
  try { targets = await handoff(room); }
  catch (error) { note(said, error.message, true); return; }
  showLink(box, targets.scheme);
  let left = false;
  const onBlur = () => { left = true; };
  window.addEventListener("blur", onBlur, { once: true });
  window.location.href = targets.scheme;
  window.setTimeout(() => {
    window.removeEventListener("blur", onBlur);
    if (left) return;
    note(said,
         `Nothing opened — no handler for ${targets.scheme.split(":")[0]}:// `
         + `on this machine. Copy the link and open it inside the tool.`, true);
  }, 1800);
}

async function copyLink(room, said) {
  let targets;
  try { targets = await handoff(room); }
  catch (error) { note(said, error.message, true); return; }
  try {
    await navigator.clipboard.writeText(targets.web);
    note(said, "Link copied — it carries no token.");
  } catch { note(said, targets.web); }
}

function showLink(box, text) {
  let row = box.querySelector(".link-row");
  if (!row) { row = el("div", "link-row"); box.append(row); }
  row.innerHTML = "";
  row.append(el("code", "", text));
}

// ── zone 2 · the studies — what has been published ──────────────────────────

async function loadStudies(base) {
  let answer;
  try {
    answer = await call(`${base}/studies`);
  } catch (error) {
    // A catalogue this node NAMED and cannot reach is not the same as no
    // catalogue: the second is silent (the zone never opens, see `catalogBase`),
    // the first has to say so. Measured the hard way — on a node where the
    // catalogue sits on another ORIGIN the browser refuses the request for CORS,
    // and the zone vanished with no way to tell that from "no studies yet".
    $("zone-studies").hidden = false;
    $("studies").replaceChildren(el("p", "note warn",
      `The catalogue at ${base} did not answer this page — ${error.message}. `
      + `On this node it is reachable, but not from a browser on this origin.`));
    return;
  }
  const studies = (answer.studies || []).slice(0, 8);
  if (!studies.length) return;
  $("zone-studies").hidden = false;
  const host = $("studies");
  host.innerHTML = "";
  for (const study of studies) host.append(studyCard(base, study));
  const all = $("all-studies");
  all.href = base;                  // the SERVICE, not a path we invented
  all.hidden = false;
}

function studyCard(base, study) {
  const authors = (study.authors || []).map((a) => a.name).filter(Boolean);
  return card({
    title: study.title || study.id,
    id: study.em_id || "",
    tag: study.visibility || "",
    verb: "read",
    notes: [authors.join(" · "),
            study.embargo_active ? `under embargo until ${study.embargo}` : "",
            study.license_effective || ""],
    build(actions, said, box) {
      const open = el("button", "", "open in…");
      open.addEventListener("click", () => void studyDoors(base, study, box, said));
      actions.append(open);
    },
  });
}

/** The doors of a study, ASKED — `<catalog>/study/{id}/open`, the twin of the
 *  rooms' one. Drawn on demand rather than up front because there are up to
 *  sixteen studies on this page and sixteen extra requests to draw a button
 *  nobody may press is a page that is slow for everybody. */
async function studyDoors(base, study, box, said) {
  let doors;
  try { doors = await call(`${base}/study/${encodeURIComponent(study.id)}/open`); }
  catch (error) { note(said, error.message, true); return; }
  const row = box.querySelector(".room-actions");
  for (const [tool, target] of Object.entries(doors.apps || {})) {
    if (box.querySelector(`.tool[data-tool="${tool}"]`)) continue;
    const openers = [];
    if (target.scheme) {
      openers.push(["desktop", "Open on the desktop (stratigraph:// handler)",
                    () => { showLink(box, target.scheme);
                            window.location.href = target.scheme; }]);
    }
    if (target.web) {
      openers.push(["browser", `Open in a new tab (${target.web})`,
                    () => window.open(target.web, "_blank", "noopener")]);
    }
    if (target.emjson) {
      openers.push(["em.json", "The container, to import by hand",
                    () => window.open(target.emjson, "_blank", "noopener")]);
    }
    if (!openers.length) continue;      // a tool with no door draws none
    const group = toolGroup(tool, openers);
    group.dataset.tool = tool;
    row.insertBefore(group, said);
  }
}

// ── zone 3 · the monuments — the subject, which endures ─────────────────────
//
// Same card, third source, third verb. The grouping is the CATALOGUE's
// (`?view=hdt` → `group_by_hdt`): doing it here would be a second implementation
// of an identity rule that already has one, and the one here would be the one
// nobody tested.

async function loadMonuments(base) {
  let answer;
  // Silent here, and only here: `loadStudies` ran first against the same
  // service and has already said whatever there was to say. Two identical
  // complaints about one catalogue is noise.
  try { answer = await call(`${base}/studies?view=hdt`); }
  catch { return; }
  // The group WITHOUT an HDT is dropped, and that is presentation and not
  // visibility: `group_by_hdt` deliberately keeps studies with no digital twin
  // under a `None` key ("which of my studies have no twin yet" is a curator's
  // question), and that bucket is not a monument. It is answered in the
  // catalogue, which is where a curator is.
  const groups = (answer.groups || []).filter((g) => g.hdt || g.hc2);
  if (!groups.length) return;
  $("zone-hdt").hidden = false;
  const host = $("hdt");
  host.innerHTML = "";
  for (const group of groups.slice(0, 8)) host.append(monumentCard(base, group));
}

function monumentCard(base, group) {
  const studies = group.studies || [];
  const count = studies.length;
  return card({
    title: group.label || group.name || group.hdt || group.hc2 || "unnamed",
    id: group.hc2 || group.hdt || "",
    verb: "explore",
    notes: [`${count} ${count === 1 ? "study" : "studies"}`],
    build(actions, said, box) {
      const show = el("button", "", "its studies");
      show.addEventListener("click", () => {
        if (box.querySelector(".sub-list")) { box.querySelector(".sub-list").remove(); return; }
        const list = el("div", "sub-list");
        for (const study of studies) list.append(studyCard(base, study));
        box.append(list);
      });
      actions.append(show);
    },
  });
}

// ── the head: which node is this, and what does it run ──────────────────────

function renderNodeLine() {
  if (!node) {
    note($("node-line"), "This node did not say who it is.", true);
    return;
  }
  const where = node.public_base || window.location.origin;
  const auth = node.auth === "keycloak"
    ? "identities are verified"
    : "identities are NOT verified (dev mode)";
  note($("node-line"), `${node.service} ${node.version} · ${where} · ${auth}`,
       node.auth !== "keycloak");
}

/** What the node RUNS — states and all — and what you install yourself. The two
 *  halves are kept apart because they answer different questions: the node knows
 *  whether its own services are up, and cannot know anything about the tool on
 *  your laptop. Two honest links beat a list that pretends to know. */
function renderServices() {
  const host = $("services");
  host.innerHTML = "";
  for (const offer of node?.offers || []) {
    const item = el("div", `service state-${offer.state.replace(/ /g, "-")}`);
    const head = el("div", "service-head");
    head.append(el("span", "dot"), el("span", "service-name", offer.label));
    head.append(el("span", "service-state", offer.state));
    item.append(head, el("p", "note", offer.detail));
    if (offer.url) {
      const link = el("a", "more", "go →");
      link.href = offer.url;
      item.append(link);
    }
    host.append(item);
  }

  const tools = $("tools-install");
  tools.innerHTML = "";
  for (const tool of node?.tools || []) {
    const item = el("div", "service state-install");
    const head = el("div", "service-head");
    // NO status dot, and that is the point of the whole half: a dot means "I
    // checked", and this node cannot check whether EMStudio is on your laptop.
    // A grey dot beside four live ones would read as a fifth service that is
    // down.
    head.append(el("span", "service-name", tool.label));
    head.append(el("span", "service-state", "on your own machine"));
    item.append(head);
    item.append(el("p", "note",
      "This node cannot know whether you have it — only where it is."));
    const row = el("p", "");
    if (tool.download) {
      const a = el("a", "more", "download →"); a.href = tool.download; row.append(a);
    }
    if (tool.manual) {
      const a = el("a", "more", "manual →"); a.href = tool.manual; row.append(a);
    }
    item.append(row);
    tools.append(item);
  }
}

// ── boot ────────────────────────────────────────────────────────────────────

async function boot() {
  // The node's own description first: everything after it is a decision that
  // needs it — where the catalogue is, whether a sign-in is even possible.
  node = await request("GET", "/node").catch(() => null);
  authConfig = await oidc.loadConfig(BASE).catch(() => null);
  renderNodeLine();
  renderServices();

  if (authConfig && oidc.returningFromIdp()) {
    const result = await oidc.completeSignIn(authConfig);
    if (result.ok) adoptSession(result);
    else note($("gate-note"), `Sign-in did not complete: ${result.error}`, true);
  }

  // The catalogue answers anonymous callers with the PUBLIC studies — not an
  // empty list and not a 401 — so these two zones are honest before a sign-in.
  const catalog = catalogBase();
  if (catalog) { await loadStudies(catalog); await loadMonuments(catalog); }

  // A room listing needs only that you are SOMEBODY. So the question is simply
  // whether the listing answers — and if it does not, NO EMPTY LIST is shown:
  // "there is nothing here" would be a lie when the truth is "I do not know who
  // you are". That is the mute gate in a more elegant form, which makes it worse.
  let rooms = null;
  try {
    rooms = await request("GET", "/rooms");
  } catch (error) {
    if (error.status === 401 || error.status === 403) { showGate(); return; }
    note($("gate-note"),
         `Cannot reach this node's API at ${BASE} — ${error.message}`, true);
    showGate();
    return;
  }
  await enter(rooms);
}

function showGate() {
  $("gate").hidden = false;
  $("zone-rooms").hidden = true;
  $("btn-signin").hidden = !(authConfig && authConfig.enforcing
                             && authConfig.authorization_endpoint);
  if (!authConfig) {
    note($("gate-note"),
         "This node does not say how to sign in, so there is nobody to sign "
         + "in as.", true);
  } else if (!authConfig.enforcing) {
    // A node that checks nothing is not a node you sign in to: saying "sign in"
    // there would offer a button that cannot work.
    note($("gate-note"),
         "This node is in dev mode: it verifies no identity"
         + (authConfig.missing?.length
            ? `. Missing: ${authConfig.missing.join(" · ")}.` : "."), true);
  }
}

async function enter(rooms) {
  $("gate").hidden = true;
  try { me = await request("GET", "/whoami").catch(() => null); } catch { me = null; }
  $("who").textContent = (me && (me.name || me.orcid))
    ? [me.name, me.orcid].filter(Boolean).join(" · ") : "";
  $("btn-signout").hidden = !(authConfig && token);
  // The create box appears exactly when creating would be ACCEPTED, and no
  // sooner. MEASURED rather than assumed: `POST /v1/rooms` has no role gate —
  // any identity may create one and becomes its owner — so reaching this line
  // (the listing answered) is the same condition. If a gate is ever added, the
  // right shape is a field on `/v1/whoami`, not a guess here: a page that
  // decided for itself would be a page you can talk out of it.
  $("create-row").hidden = false;
  renderRooms(rooms);
}

// ── create ──────────────────────────────────────────────────────────────────

async function createRoom() {
  const input = $("new-room-title");
  const title = (input.value || "").trim();
  if (!title) { note($("create-note"), "A room needs a name.", true); return; }
  // The id is derived from the name so a person never types two things that must
  // agree. Collisions are the SERVER's to refuse, and its sentence is shown.
  const room_id = title.toLowerCase().replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "").slice(0, 60) || `room-${Date.now().toString(36)}`;
  try { await request("POST", "/rooms", { room_id, title }); }
  catch (error) { note($("create-note"), error.message, true); return; }
  input.value = "";
  note($("create-note"), "Created — you are its owner.");
  renderRooms(await request("GET", "/rooms"));
}

// ── wiring ──────────────────────────────────────────────────────────────────

$("btn-signin").addEventListener("click", async () => {
  if (!authConfig) { note($("gate-note"), "This node has no OIDC configuration.", true); return; }
  await oidc.signIn(authConfig);
});
// Signing out has to close the REALM's session, not only this tab. One signature
// opens four tools on this origin; an exit that forgot a token here would leave
// the device signed in on the other three, and the rule about a device that
// changes hands would hold for one face out of four — which is not holding.
$("btn-signout").addEventListener("click", () => {
  const url = authConfig ? oidc.signOutUrl(authConfig) : "";
  token = ""; refreshToken = ""; window.clearTimeout(refreshTimer);
  if (url) window.location.href = url;
  else window.location.reload();
});
$("btn-create").addEventListener("click", () => void createRoom());
$("new-room-title").addEventListener("keydown", (event) => {
  if (event.key === "Enter") void createRoom();
});

await boot();
