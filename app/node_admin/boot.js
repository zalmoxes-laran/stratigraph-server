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

await boot();
