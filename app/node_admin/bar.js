/**
 * THE BAR — where you are on this node, and where else you can go.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## WHAT WAS MEASURED BEFORE THIS FILE EXISTED (7 October 2026)
 *
 * Four pages are served by this process — `/rooms/`, `/work/`, `/tools/`,
 * `/admin/` — plus the catalogue's next door. Asked of the DOCUMENT (an HTML
 * parser, not a text search):
 *
 *     rooms_ui/index      0 anchors     ← the vestibule: it SENDS, by design
 *     rooms_ui/work       1  ../rooms/  ← «← this node»
 *     rooms_ui/tools      1  ../rooms/
 *     node_admin          0 anchors     ← no way back
 *     catalog/ui          0 anchors     ← no way back
 *
 * So two of the four already went back, relatively, which is the right shape.
 * The console and the catalogue were the islands. And the vestibule — the page
 * whose own docstring says «IT OWNS NOTHING. Not a list, not a state, not a
 * configuration saying where things live» — carried `"../work/"`,
 * `"../tools/"` and `"../admin/"` in `rooms.js`.
 *
 * ## WHERE THE FACES COME FROM, AND WHY NOT THE OTHER SOURCE
 *
 * Two sources knew something a bar needs:
 *
 *     ENTRANCES via /v1/admin/health   the faces of THIS server, operator-scoped
 *     node_services via /v1/node       the NEIGHBOURS and their states, PUBLIC
 *
 * The bar reads **`/v1/node`**, and `ENTRANCES` now travels on it too — the same
 * list, not a second one. The deciding reason is the degradation §2 asks for: a
 * visitor with no operator capability must get a bar with fewer rows, not a
 * broken one, and a bar fed by `/v1/admin/health` would be empty for exactly
 * the person who most needs to find their way. What that publishes was already
 * discoverable: `/docs` lists every route of this build.
 *
 * ## NO PAGE WRITES AN ADDRESS. NOT EVEN A RELATIVE ONE
 *
 * `Caddyfile.dev:295` has the real case: on a node that also carries Heriverse
 * the root is Heriverse's, so a «home» button pointed at `/em/rooms/` is FALSE
 * there. Where you go back to is the node's answer, never the page's.
 *
 * So a face's `path` is the node's (`/rooms/`), and this file turns it into
 * something this page can follow by **subtracting the page's own face from its
 * own URL** — `/em/work/` minus `/work/` leaves `/em`, and `/em` + `/rooms/` is
 * where the front door is on this deployment. No prefix is configured anywhere
 * and none is guessed: it is arithmetic on two strings the node and the browser
 * each already have.
 */

const $ = (id) => document.getElementById(id);

/** The mount prefix this deployment serves us under — "" when we are at the root.
 *
 *  `here` is `window.location.pathname`, `mine` is this page's own face path
 *  (`/work/`). Bare: `/work/` − `/work/` = "". Proxied: `/em/work/` − `/work/`
 *  = `/em`. A page that is not one of the node's faces passes `mine = ""` and
 *  gets the prefix from its own directory instead.
 */
export function prefixOf(here, mine) {
  const path = String(here || "/");
  const face = String(mine || "");
  if (face && path.endsWith(face)) return path.slice(0, -face.length);
  if (face) {
    //  a deeper page under the same face (`/em/work/index.html`): cut at the face
    const at = path.lastIndexOf(face);
    if (at >= 0) return path.slice(0, at);
  }
  return path.replace(/\/[^/]*$/, "");
}

/** Which of the node's faces is the page we are on, by its path. "" when none. */
export function faceHere(here, faces) {
  const path = String(here || "/");
  const pages = (faces || []).filter((f) => f.page && f.path.endsWith("/"));
  //  the LONGEST match, so `/rooms/` does not win over a hypothetical
  //  `/rooms/work/` — measured as a rule rather than trusted to list order
  let best = "";
  for (const face of pages) {
    if (path.includes(face.path) && face.path.length > best.length) {
      best = face.path;
    }
  }
  return best;
}

/**
 * What the bar should draw, given the node's answer and who is reading.
 *
 * PURE, and that is the point: this is the line that can be wrong in silence —
 * a bar that offers a console to somebody who cannot open it, or that drops the
 * way back — and `tests/test_the_bar.py` executes it with node.
 *
 * `state` is `{faces, offers, here, operator, catalog}`. Returns the rows in
 * the order they are drawn, each with an `href` this page can follow.
 */
export function barPlan(state) {
  const faces = state.faces || [];
  const here = state.here || "/";
  const mine = faceHere(here, faces);
  const prefix = prefixOf(here, mine);
  const rows = [];

  // ── ARE WE ON THIS NODE, OR BESIDE IT? ────────────────────────────────────
  //
  // The two cases take different addresses, and conflating them produced
  // `/catalog/ui/rooms/` the first time this ran — measured, not imagined.
  //
  //   ON a face  →  relative arithmetic. `/em/work/` − `/work/` = `/em`, and
  //                 `/em` + `/rooms/` is where the front door is HERE. Works on
  //                 a node with no public name, which is most dev stacks.
  //   BESIDE it  →  the absolute `url` the NODE composed from its own
  //                 `public_base`. A face without one is not drawn: a node that
  //                 never said its public name cannot be linked to from another
  //                 origin, and inventing `localhost` would be a link that
  //                 works only where it was written.
  const onThisNode = Boolean(mine);
  for (const face of faces) {
    if (!face.page) continue;                      // not a face a person opens
    if (face.needs === "operator" && !state.operator) continue;
    const href = onThisNode ? `${prefix}${face.path}` : (face.url || "");
    if (!href) continue;
    rows.push({
      kind: "face",
      key: face.key,
      label: face.label,
      href,
      current: onThisNode && face.path === mine,
    });
  }

  // THE NEIGHBOURS, and only the ones this deployment actually published: a
  // neighbour with no public address is a row that 404s after the click, and
  // `node_services` already returns "" rather than guessing one.
  for (const offer of state.offers || []) {
    if (!offer.url) continue;
    rows.push({
      kind: "offer",
      key: offer.name,
      label: offer.label,
      href: offer.url,
      state: offer.state || "",
      current: false,
    });
  }
  return { prefix, mine, rows };
}

/**
 * Draw it. `host` is an element; `read` is a function that fetches a path on
 * this node's API and returns JSON; `operator` says whether this viewer has the
 * capability (the caller knows — `/v1/admin/whoami` answered it).
 *
 * Silent failure is not an option and not a crash either: a node that does not
 * answer leaves the bar with what a page always has, which is nothing, and says
 * so in one line. A visitor must never see a broken bar.
 */
export async function mountBar(host, { read, operator = false, t = null }) {
  if (!host) return () => {};
  //: IL DIZIONARIO QUANDO CE L'HA, l'etichetta del nodo altrimenti. `t()` di
  //: questo repo torna LA CHIAVE quando non traduce (`i18n.js`), quindi
  //: `t(k) || label` non ricade mai — mostrerebbe «face.work». Il confronto con
  //: la chiave è l'unico modo di sapere se il dizionario ha risposto.
  const label = (row) => {
    if (!t) return row.label;
    const key = `face.${row.key}`;
    const said = t(key);
    return said && said !== key ? said : row.label;
  };

  async function draw(isOperator = operator) {
    let node = null;
    try { node = await read("/node"); } catch { node = null; }
    host.replaceChildren();
    if (!node) {
      // LO DICE, e non finge una barra. Sotto ogni pagina che monta questa c'è
      // un'ancora relativa in HTML statico (`#back-door`) che non dipende da
      // niente: il pavimento resta anche quando la barra tace.
      const said = document.createElement("span");
      said.className = "bar-quiet";
      said.textContent = t ? t("bar.silent") : "this node did not answer";
      host.append(said);
      return;
    }
    const plan = barPlan({
      faces: node.faces, offers: node.offers,
      here: window.location.pathname, operator: isOperator,
    });
    for (const row of plan.rows) {
      const a = document.createElement("a");
      a.className = "bar-face" + (row.current ? " on" : "")
        + (row.kind === "offer" ? " bar-offer" : "");
      a.href = row.href;
      a.textContent = label(row);
      if (row.current) a.setAttribute("aria-current", "page");
      if (row.state && row.state !== "ok") a.dataset.state = row.state;
      host.append(a);
    }
  }

  await draw();
  //: redrawn when the caller learns something new about the viewer — the
  //: capability arrives after the sign-in, not with the page
  return draw;
}
