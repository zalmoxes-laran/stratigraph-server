"""The two StratiGraph faces this server shows, and what keeps them honest.

Same three claims the field assistant's suite makes, because it is the same
brand and the same reasoning:

* **nothing is fetched at runtime** — the theme, the faces and the marks are
  VENDORED (`sync-brand.sh`) and served same-origin. A node may be deployed
  where there is no route out, and a console that lost its typeface with the
  uplink would lose it exactly when somebody is trying to find out why;
* **the pages use ROLES, not hexes** — a colour written into a stylesheet is a
  colour that will not follow the brand when the brand moves;
* **no text on a pure accent** — the three digital accents measure 1.36, 2.27
  and 2.98 against the ground. They are fills and washes; the theme ships the
  inks that can carry text.

EMStudio and EMtools are NOT covered here and never will be: they are Extended
Matrix tools and keep the EM look.
"""

from __future__ import annotations

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
BRAND = APP / "brand"
FACES = {
    "node console": (APP / "node_admin" / "index.html",
                     APP / "node_admin" / "console.css"),
    "room browser": (APP / "rooms_ui" / "index.html",
                     APP / "rooms_ui" / "rooms.css"),
}

#: the WP1 guidebook's palette, plus the derived values the theme declares
GUIDEBOOK = {"#F1EBE3", "#D9D1CF", "#383838", "#2E2D2C", "#C4B282", "#8A8021",
             "#A64724", "#E85B1A", "#CAD531", "#4AA7D9", "#1E275C"}

#: the pure accents: fills and washes only, never an ink
PURE_ACCENTS = ("--sg-info", "--sg-ok", "--sg-accent")


def test_the_brand_is_vendored_here():
    assert (BRAND / "stratigraph-theme.css").is_file(), "run ./sync-brand.sh"
    fonts = sorted(p.name for p in (BRAND / "fonts").glob("*.woff2"))
    assert len(fonts) == 8, fonts
    assert (BRAND / "logo" / "favicon-deep-charcoal.svg").is_file()


def test_neither_face_reaches_a_cdn():
    for name, (page, sheet) in FACES.items():
        for path in (page, sheet):
            source = path.read_text(encoding="utf-8")
            code = re.sub(r"/\*.*?\*/|<!--.*?-->", "", source, flags=re.S)
            for host in ("fonts.googleapis.com", "fonts.gstatic.com",
                         "api.fontshare.com", "cdn.fontshare.com",
                         "cdn.jsdelivr.net", "unpkg.com"):
                assert host not in code, f"{name}: {path.name} reaches {host}"


def test_the_theme_is_imported_RELATIVELY_so_it_survives_the_proxy():
    """`/admin/` and `/em/admin/` are the same page behind a prefix. An absolute
    `/brand/…` would be right for one and 404 for the other."""
    console = (APP / "node_admin" / "console.css").read_text(encoding="utf-8")
    assert '@import url("../brand/stratigraph-theme.css")' in console
    # …and the room browser gets it through that same import, not a second one
    rooms = (APP / "rooms_ui" / "index.html").read_text(encoding="utf-8")
    assert '"../admin/console.css"' in rooms
    assert rooms.index('"../admin/console.css"') < rooms.index('"./rooms.css"'), \
        "the console sheet must come first: it is the one that imports the theme"


def test_neither_stylesheet_names_a_colour():
    for name, (_page, sheet) in FACES.items():
        found = re.findall(r"#[0-9A-Fa-f]{3,8}\b", sheet.read_text(encoding="utf-8"))
        assert not found, f"{name}: {found}"


def test_the_only_hexes_in_a_page_are_the_two_theme_colors():
    """`<meta name=theme-color>` cannot take a CSS variable. Both values are the
    guidebook's."""
    for name, (page, _sheet) in FACES.items():
        source = page.read_text(encoding="utf-8")
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{3,8}\b", source)}
        assert found <= {"#F1EBE3", "#2E2D2C"}, f"{name}: {found}"
        assert found <= GUIDEBOOK
        for hexed in found:
            assert f'content="{hexed}"' in source, f"{name}: {hexed} is not a theme-color"


def test_no_text_sits_on_a_pure_accent():
    """The rule the theme's contrast table exists for. A `color:` that resolves
    to `--sg-info`, `--sg-ok` or `--sg-accent` is 2.27, 1.36 or 2.98 against the
    ground — the theme ships `--sg-info-ink` and `--sg-ok-ink` for exactly this.
    """
    for name, (_page, sheet) in FACES.items():
        for line in sheet.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(("*", "/*", "//")) or "color-mix" in stripped:
                continue
            for accent in PURE_ACCENTS:
                assert not re.search(rf"(?<!-)\bcolor:\s*var\({accent}\)", stripped), \
                    f"{name}: text on a pure accent — {stripped}"


# ── the other half of the colour rule: an element with NONE ──────────────────
#
# Forbidding an invented colour was only half a rule, and the missing half was
# measured in Chrome on 5 September 2026: the node map's «open →» links came out
# `rgb(0, 0, 238)` — the browser's default blue for an unstyled `<a>` — on a dark
# ground, about 1.6:1. Unreadable.
#
# How it happened is the argument for this test existing. The literal colours in
# that block were rightly refused by `test_neither_stylesheet_names_a_colour`,
# they went, and those anchors were never given the theme's token in exchange. A
# test that only says «not that colour» cannot notice «no colour», which is the
# quietest way to get this wrong.
#
# WHY IT IS SOUND, and it turns on a detail of CSS rather than on taste: `a[href]`
# and `button` carry a colour from the USER AGENT (`-webkit-link`, `buttontext`),
# and a UA declaration beats inheritance. So an anchor or a button inside a
# coloured container does NOT take the container's colour — it takes the browser's.
# Every one of them needs a rule that reaches it, and «reaches it» is checkable
# from the selectors.
#
# What it CANNOT see, said plainly: it matches on the last compound of a
# selector, so it approximates the cascade rather than computing it. It will not
# catch a rule that is overridden by a later, more specific one. That is a
# narrower gap than the one it closes, and closing it properly would mean
# shipping a CSS engine in a test.

def _rules(sheet: str):
    """(selector, declarations) for each block, comments stripped."""
    text = re.sub(r"/\*.*?\*/", " ", sheet, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector = " ".join(match.group(1).split())
        if selector.startswith("@"):
            continue
        yield selector, match.group(2)


def _colouring_targets(sheet: str) -> set:
    """Tags and classes that some `color:` declaration reaches.

    The LAST compound of each comma-separated selector is what the declaration
    lands on: in `.tool button { color: … }` it is `button`, and that is what
    colours every button inside a `.tool`.
    """
    targets = set()
    for selector, body in _rules(sheet):
        if not re.search(r"(?<!-)\bcolor\s*:", body):
            continue
        for part in selector.split(","):
            last = re.split(r"[\s>+~]+", part.strip())[-1]
            last = re.sub(r"::?[a-z-]+(\([^)]*\))?", "", last)   # pseudo off
            tag = re.match(r"^[a-zA-Z][\w-]*", last)
            if tag:
                targets.add(tag.group(0).lower())
            targets.update(re.findall(r"\.([\w-]+)", last))
    return targets


def _interactive_elements(page: str, scripts: list) -> list:
    """(where, tag, classes) for every anchor and button these faces build.

    Both sources, because most of this page is built at runtime: the markup for
    what ships in the HTML, and the scripts for what the zones create — an
    element that only exists after a fetch is exactly as visible to a reader as
    one that was in the file.
    """
    found = []
    for match in re.finditer(r"<(a|button)\b([^>]*)>", page, flags=re.I):
        tag, attrs = match.group(1).lower(), match.group(2)
        if tag == "a" and "href" not in attrs:
            continue                      # an anchor with no href is not a link
        classes = re.search(r'class="([^"]*)"', attrs)
        found.append(("markup", tag, (classes.group(1).split() if classes else [])))
    for name, code in scripts:
        # the pages' own helper: `el("a", "map-open", …)` / `el("button", "x", …)`
        for match in re.finditer(r'\bel\(\s*"(a|button)"\s*,\s*"([^"]*)"', code):
            found.append((name, match.group(1), match.group(2).split()))
        # …and the long form: createElement("a") then className = "…"
        for match in re.finditer(
                r'createElement\(\s*"(a|button)"\s*\)[\s\S]{0,400}?'
                r'\.className\s*=\s*(?:"([^"]*)"|`([^`]*)`)', code):
            raw = (match.group(2) or match.group(3) or "")
            raw = re.sub(r"\$\{[^}]*\}", " ", raw)          # template holes out
            found.append((name, match.group(1), raw.split()))
    return found


def test_no_interactive_element_inherits_the_user_agent_s_colour():
    """Everything you can click takes its colour from the theme.

    The bug this closes: «open →» in the node map was the browser's default blue
    on a dark ground. Not a wrong colour — NO colour, which the other tests could
    not see.
    """
    for name, (page_path, sheet_path) in FACES.items():
        page = page_path.read_text(encoding="utf-8")
        sheet = sheet_path.read_text(encoding="utf-8")
        # the console sheet is imported by the room browser, so its rules reach
        # both faces — the same relative import the test above pins
        shared = (APP / "node_admin" / "console.css").read_text(encoding="utf-8")
        theme = (BRAND / "stratigraph-theme.css").read_text(encoding="utf-8")
        coloured = (_colouring_targets(sheet) | _colouring_targets(shared)
                    | _colouring_targets(theme))
        scripts = [(p.name, p.read_text(encoding="utf-8"))
                   for p in sheet_path.parent.glob("*.js")]
        for where, tag, classes in _interactive_elements(page, scripts):
            reached = tag in coloured or any(c in coloured for c in classes)
            assert reached, (
                f"{name}: <{tag} class=\"{' '.join(classes)}\"> (from {where}) is "
                f"not reached by any `color:` rule, so it takes the USER AGENT's "
                f"— the browser's link blue or button grey, whatever the ground "
                f"is. Give it a theme token; `--sg-info-ink` is the one for a "
                f"link, `--sg-text` for a button."
            )


def test_a_filled_button_is_charcoal_because_burnt_cannot_carry_14px_text():
    """White on Burnt Orange is 3.53; Off-White on Deep Charcoal is 11.61. Burnt
    stays the guidebook's ACTION colour — as a fill at a size where 3:1 applies,
    which this dense panel does not have."""
    console = (APP / "node_admin" / "console.css").read_text(encoding="utf-8")
    assert "--fill:     var(--sg-deep-charcoal)" in console
    assert "--on-fill:  var(--sg-off-white)" in console


def test_both_faces_wear_the_hourglass_and_the_display_face():
    for name, (page, _sheet) in FACES.items():
        source = page.read_text(encoding="utf-8")
        assert "favicon-deep-charcoal.svg" in source, name
        assert 'class="wordmark">StratiGraph' in source, name
    console = (APP / "node_admin" / "console.css").read_text(encoding="utf-8")
    assert "font-family: var(--sg-font-display)" in console


def test_the_static_mounts_revalidate_rather_than_go_stale():
    """Found while restyling: an edited stylesheet did not reach a browser that
    already had the page open — ETag but no `Cache-Control`, so the browser
    applied heuristic freshness. `no-cache` means "store, but revalidate", which
    with the ETag is a 304."""
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "class _FreshStatic(StaticFiles)" in main
    assert 'response.headers.setdefault("Cache-Control", "no-cache")' in main
    # EVERY static face, and not a count of the ones we remembered. The count was
    # 3, then the pages separated by verb and it became 4 — so the test failed
    # for a face that was correctly built, which is a test measuring the wrong
    # thing. What actually matters is that no mount slips through with the plain
    # class: a stylesheet that goes stale is invisible until somebody edits it and
    # nothing changes on screen.
    mounted = re.findall(r"app\.mount\(\s*[^,]+,\s*(\w+)\(directory=", main)
    assert mounted, "no static mounts found — did `app.mount` change shape?"
    assert set(mounted) == {"_FreshStatic"}, (
        f"a static face is mounted without revalidation: {sorted(set(mounted))}")
