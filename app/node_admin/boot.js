/**
 * The ordering, and nothing else.
 *
 * `console.js` is the shell and never imports a module; a module imports the
 * shell. This file is the one place that knows which modules exist and that they
 * have to be registered before the shell starts — which keeps the dependency
 * pointing one way. (It used to be a top-level `await import()` inside the shell,
 * and a circular import with a top-level await deadlocks silently: the page said
 * "Loading…" for ever and logged nothing.)
 *
 * Adding a panel is one line here plus one file in `modules/`.
 */
import { boot } from "./console.js";

import "./modules/users-rooms.js";
import "./modules/storage.js";
import "./modules/health.js";

// ── the seams, declared and NOT built ────────────────────────────────────────
//
// Four panels are wanted next, and each one is the same three things: a file in
// `modules/`, an operator-scoped endpoint, a read-mostly render. They are listed
// here rather than sketched in code, because a nav entry that opens an empty
// panel is worse than one that is not there — and because the LIST is the useful
// artefact: it says what the console is for.
//
//   modules/corpus.js   → GET /v1/admin/corpus    the resident DTC register:
//                         how many acts, by whom, which digests carry rights
//   modules/iiif.js     → GET /v1/admin/iiif      Cantaloupe: cache size, the
//                         derivatives it holds, the forward_auth refusals
//   modules/catalog.js  → GET /v1/admin/catalog   em-catalog: indexed studies,
//                         last reindex, what the index disagrees with
//   modules/drift.js    → GET /v1/admin/drift     datamodel/version drift across
//                         the consumers (the Health panel already shows this
//                         node's half — see `versions` there)
//
// Adding one does not touch `console.js`. That is the property this file exists
// to keep true.

await boot();
