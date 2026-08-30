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
    assert main.count("_FreshStatic(directory=") == 3, \
        "every static face must revalidate, not just the ones we remembered"
