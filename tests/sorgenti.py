"""Come si guarda un sorgente, quando una guardia deve guardarlo.

════════════════════════════════════════════════════════════════════════════════
## NOVE VOLTE NON È SFORTUNA

In questo ecosistema una guardia ha morso l'autore che stava dicendo una cosa
giusta **nove volte**, sempre nello stesso modo:

    `anno` dentro «cannot»                    `white` dentro `--sg-off-white`
    `area` dentro una frase italiana          `gc_watermark` in una docstring
    `compact_section` in una spiegazione      `d{1,5}` in un commento
    `open(` dentro `urlopen(`                 `localStorage` in un commento
    `pie` dentro «ripiego»                    ← la nona, il 3 ottobre

A nove ripetizioni il difetto non è nella singola guardia:

> **Una guardia che cerca una parola nel testo sta misurando il file, non il
> programma.**

Ed è la gemella dell'altro difetto della stessa notte, `test_chi_manda_ack`, che
*«cercava un percorso e credeva di cercare un fatto»*.

## E LA CURA NON È UNO SPOGLIATORE PIÙ BRAVO

Togliere i commenti aiuta e non basta, e non basterà mai: `pie` sta dentro
`ripiego` **nel codice**, `chart` dentro `charter`, `canvas` dentro un URL,
`d3.` dentro un numero di versione. Si smette di cercare nel testo.

Quindi questo modulo, e la ragione per cui è **uno** invece di cinque copie:
cinque spogliatori scritti a mano sono cinque posti dove lo stesso difetto si
ripresenta, e l'ecosistema ne aveva già cinque.

════════════════════════════════════════════════════════════════════════════════
## LE TRE FORZE, IN ORDINE

1. **il programma** — `chiama_python`, `importa_python`, `chiama_js`: si guarda
   cosa il codice FA. Per Python è un albero vero (`ast`); per JavaScript sono
   espressioni ancorate alla sintassi, e il perché ci si è fermati lì è scritto
   sotto.
2. **il documento** — `blocco_css`, `attributi`: per CSS e HTML si guardano le
   regole e i nodi, non le righe.
3. **il confine di parola** — `parola`: il minimo sindacale, dove niente di
   meglio è possibile. **Un minimo dichiarato vale più di un massimo taciuto.**

E `senza_prosa` resta, ma **declassato**: non è più una cura, è un'igiene da
usare *insieme* a una delle tre.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Iterable, List, Set

# ── igiene ──────────────────────────────────────────────────────────────────


def senza_prosa(source: str) -> str:
    """Il testo senza commenti `//` e `/* */`.

    **Non è una guardia**: è quello che si fa PRIMA di una guardia, e da solo
    non ha mai chiuso niente — otto delle nove volte qui sopra sono state morsi
    su codice vero, non su prosa. Serve a togliere il rumore, non a decidere.
    """
    senza = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(riga.split("//")[0] for riga in senza.split("\n"))


def parola(termine: str) -> "re.Pattern[str]":
    """Il confine di parola, con la `s` finale facoltativa.

    Il minimo sindacale: non distingue un identificatore da una parola in una
    stringa, e non pretende di farlo. Serve dove il termine cercato **è** una
    parola — un nome di campo, un'etichetta — e dove guardare la sintassi non
    direbbe niente di più.
    """
    return re.compile(rf"\b{re.escape(termine)}s?\b", re.I)


# ── il programma · Python ───────────────────────────────────────────────────


def _albero(source: str) -> ast.AST:
    return ast.parse(source)


def chiama_python(source: str, nomi: Iterable[str]) -> Set[str]:
    """Quali di questi nomi vengono CHIAMATI in questo sorgente Python.

    Un albero e non una sottostringa, ed è il caso che ha aperto tutto: `open(`
    dentro `urlopen(` sono due chiamate diverse e una regex non lo sa. Qui
    `urlopen` è un `Name(id='urlopen')`, `open` è un `Name(id='open')`, e non si
    somigliano affatto.

    Riconosce sia `open(...)` sia `qualcosa.open(...)`: il primo è la builtin,
    il secondo è `Path(...).open()`, e per «questo modulo non tocca il disco»
    sono la stessa violazione.
    """
    cercati = set(nomi)
    trovati: Set[str] = set()
    for nodo in ast.walk(_albero(source)):
        if not isinstance(nodo, ast.Call):
            continue
        funzione = nodo.func
        if isinstance(funzione, ast.Name) and funzione.id in cercati:
            trovati.add(funzione.id)
        elif isinstance(funzione, ast.Attribute) and funzione.attr in cercati:
            trovati.add(funzione.attr)
    return trovati


def importa_python(source: str, moduli: Iterable[str]) -> Set[str]:
    """Quali di questi moduli vengono IMPORTATI. `import x` e `from x import y`.

    Distingue da una menzione: `pathlib` in una docstring che spiega perché non
    si usa `pathlib` non è un import, ed è precisamente il morso che questa
    funzione esiste per non dare.
    """
    cercati = set(moduli)
    trovati: Set[str] = set()
    for nodo in ast.walk(_albero(source)):
        if isinstance(nodo, ast.Import):
            for alias in nodo.names:
                radice = alias.name.split(".")[0]
                if radice in cercati:
                    trovati.add(radice)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            radice = nodo.module.split(".")[0]
            if radice in cercati:
                trovati.add(radice)
    return trovati


# ── il programma · JavaScript ───────────────────────────────────────────────
#
# NON C'È UN PARSER, ED È UNA SCELTA DICHIARATA. Un parser JavaScript vero
# vorrebbe una dipendenza (o cinquecento righe scritte a mano) per una suite
# che oggi non ne ha nessuna, ed è più di un micro. Quello che c'è sono
# espressioni **ancorate alla sintassi**: una chiamata, un attributo, un
# import — forme che una parola dentro un'altra parola non può assumere.
#
# Il confine di quello che riconosce, detto: un `createElement(nome)` con il
# nome in una variabile non viene visto. È il prezzo del non avere un parser, e
# si paga sapendo di pagarlo.

#: `document.createElement("canvas")` — e con qualunque spaziatura.
_CREA = r"""createElement(?:NS)?\s*\(\s*(?:[^,)]*,\s*)?["'`]{tag}["'`]"""
#: `new Chart(`, `new ApexCharts(`
_NUOVO = r"""\bnew\s+{nome}\s*\("""
#: `import … from "chart.js"` / `require("d3")`
_IMPORTA = r"""(?:\bimport\b[^\n;]*from\s*|\brequire\s*\(\s*)["'`][^"'`]*{nome}"""
#: un `<svg` costruito in una stringa o in un template
_LETTERALE = r"""["'`]\s*<\s*{tag}\b"""


def crea_elemento(source: str, tag: str) -> bool:
    """Questo codice CREA un elemento di questo tipo?"""
    return bool(re.search(_CREA.format(tag=re.escape(tag)), source, re.I))


def costruisce(source: str, nome: str) -> bool:
    """`new <nome>(` — una libreria che si istanzia."""
    return bool(re.search(_NUOVO.format(nome=re.escape(nome)), source))


def importa_js(source: str, nome: str) -> bool:
    """Un `import` o un `require` che nomina questa libreria."""
    return bool(re.search(_IMPORTA.format(nome=re.escape(nome)), source, re.I))


def markup_letterale(source: str, tag: str) -> bool:
    """Un `<tag` scritto dentro una stringa — l'altro modo di disegnare."""
    return bool(re.search(_LETTERALE.format(tag=re.escape(tag)), source, re.I))


def usa_oggetto(source: str, nome: str) -> bool:
    """`nome.qualcosa` o `nome[…]` — un oggetto globale davvero usato.

    Distingue `localStorage.setItem(…)` da `"localStorage"` dentro un messaggio,
    che è la nona classe di morso vista dal lato degli identificatori.

    **E non può essere preceduto da un punto**, che il `\b` da solo permette:
    `1.d3.0` — un numero di versione — soddisfa `\bd3\s*\.` e non è un uso di
    `d3`. Trovato costruendo il falso positivo invece che immaginandolo, che è
    il motivo per cui il falso positivo si costruisce.
    """
    return bool(re.search(rf"(?<![\w.$]){re.escape(nome)}\s*[.\[]", source))


def indirizzi(source: str) -> List[str]:
    """Gli indirizzi che questo documento CHIEDE DAVVERO: `href`, `src`,
    `@import`, `url(`.

    Il documento e non le righe: un nome di host dentro una frase — «questa
    pagina non carica niente da fonts.googleapis.com» — è una promessa, non una
    richiesta, e una guardia che le confonde morde chi la sta mantenendo.
    """
    fuori: List[str] = []
    fuori += re.findall(r"""(?:href|src)\s*=\s*["']([^"']+)["']""", source, re.I)
    fuori += re.findall(r"""@import\s+(?:url\()?\s*["']([^"']+)["']""", source, re.I)
    fuori += re.findall(r"""\burl\(\s*["']?([^"')]+)["']?\s*\)""", source, re.I)
    return fuori


# ── il documento · CSS ──────────────────────────────────────────────────────


def blocco_css(source: str, apertura: str) -> str:
    """Il corpo di una regola, contando le graffe.

    E non `source[source.index(apertura):]`, che è come si faceva: quella fetta
    arriva **alla fine del file**, quindi una regola scritta dopo — o un
    commento — finisce dentro una guardia che crede di guardare un blocco solo.
    Misurato il 4 ottobre su `@media (max-width: 40rem)`.

    Torna «» se quella regola non c'è: una guardia su un blocco assente deve
    poterlo dire, non passare per vuoto.
    """
    dove = source.find(apertura)
    if dove < 0:
        return ""
    aperta = source.find("{", dove)
    if aperta < 0:
        return ""
    livello, i = 0, aperta
    while i < len(source):
        if source[i] == "{":
            livello += 1
        elif source[i] == "}":
            livello -= 1
            if livello == 0:
                return source[aperta + 1:i]
        i += 1
    return source[aperta + 1:]


def dichiara(source: str, proprieta: str, valore: str) -> bool:
    """Una DICHIARAZIONE `proprietà: valore`, con la spaziatura che capita.

    `display:none`, `display : none`, `display:  none !important` — e non la
    stessa coppia di parole dentro una frase.
    """
    senza = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return bool(re.search(rf"{re.escape(proprieta)}\s*:\s*{re.escape(valore)}\b",
                          senza, re.I))


# ── comodità ────────────────────────────────────────────────────────────────

REPO = pathlib.Path(__file__).resolve().parent.parent


def leggi(*parti: str) -> str:
    return (REPO.joinpath(*parti)).read_text(encoding="utf-8")
