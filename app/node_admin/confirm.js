/**
 * ASKING BEFORE SOMETHING CANNOT BE TAKEN BACK — and asking DIFFERENTLY when it
 * really cannot.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * ## WHY THERE ARE TWO, MEASURED ON 7 OCTOBER 2026
 *
 * `confirmNamed` lived in `console.js` and is used three times: revoke access,
 * archive a room, archive a container. **All three are reversible.** What it
 * asks is one dialog with the name shown in it:
 *
 *     «{what}\n\n{name}\n\nThis is somebody's workspace. Continue?»
 *
 * One OK click. That is the right ceremony for an act somebody can undo, and it
 * is the wrong one for `DELETE /catalog/study/{id}`, whose own docstring says
 * *«Not a tombstone… withdrawing it is withdrawing the statement»* — the
 * container and the card both go, and nothing in the catalogue remembers.
 *
 * So `confirmTyped` asks the person to **write the name**. Not for ceremony: a
 * dialog you dismiss by reflex is a dialog that protects nothing, and typing
 * `study:8f2a…` is the one gesture that cannot be done by reflex. It is the
 * confirmation the 7 October prompt described and this repo did not have — the
 * prompt said `confirmNamed` already asked for the name; it does not, and that
 * is worth having found before reusing it on a deletion.
 *
 * ## AND WHY THEY LIVE TOGETHER, IN A FILE WITH NO IMPORTS
 *
 * One place where destructive confirmations are decided, so a fourth caller
 * finds both and picks. And self-contained — no `t`, no DOM helper, nothing —
 * because the catalogue next door needs the same two questions with its OWN
 * dictionary, and a copy of the reasoning is what we are avoiding.
 *
 * `makeConfirm(t)` binds a dictionary and returns the pair.
 */

/**
 * @param {(key: string, values?: object) => string} t  a dictionary lookup
 * @returns {{confirmNamed: Function, confirmTyped: Function}}
 */
export function makeConfirm(t) {
  /** A reversible act, with the thing named in the dialog. One OK. */
  function confirmNamed(what, name) {
    return window.confirm(t("console.confirm", { what, name }));
  }

  /**
   * An IRREVERSIBLE act: the name has to be typed, exactly.
   *
   * Returns true only when what was typed matches `name` after trimming. A
   * cancelled prompt gives `null`, an empty one gives `""`, and both are false
   * — so there is no path where an empty answer counts as agreement, which is
   * the failure a `.includes()` or a lowercase compare would eventually have.
   */
  function confirmTyped(what, name) {
    const typed = window.prompt(t("console.confirm.typed", { what, name }));
    if (typed === null) return false;              // cancelled
    return String(typed).trim() === String(name).trim();
  }

  return { confirmNamed, confirmTyped };
}
