"""Read Academy Awards dates, nominees and winners from the English Wikipedia ceremony
article ("99th Academy Awards", etc.), which is updated within minutes of announcements.

Used by scripts/automation.py. Pure parsing lives here so it can be tested offline.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime

USER_AGENT = "nominated-film-tracker/1.0 (https://github.com/mendus59/academy_award_tracker)"
API = "https://en.wikipedia.org/w/api.php"


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def page_title(edition: int) -> str:
    return f"{ordinal(edition)} Academy Awards"


def api_get(params: dict, url: str = API) -> dict:
    query = urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def fetch_wikitext(edition: int) -> tuple[str, int] | None:
    """Returns (wikitext, revision id), or None if the article doesn't exist yet."""
    data = api_get({"action": "parse", "page": page_title(edition), "prop": "wikitext|revid", "redirects": 1})
    if "error" in data:
        return None
    return data["parse"]["wikitext"], data["parse"]["revid"]


# ---------- Markup helpers ----------

def strip_comments_and_refs(text: str) -> str:
    text = re.sub(r"<!--.*?(-->|$)", "", text, flags=re.S)  # also drops an unclosed comment
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
    return text


def expand_templates(text: str) -> str:
    """Replace the templates that appear in nominee lists with their visible text."""

    def repl(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(1).split("|")]
        name = parts[0].lower().replace("_", " ")
        args = [p for p in parts[1:] if "=" not in p]
        if name in ("!",):
            return "|"
        if name in ("double dagger", "double-dagger", "ddagger"):
            return "‡"
        if name in ("ill", "interlanguage link", "illm", "nowrap", "nobr", "small", "abbr", "lang", "sic", "nobold", "noitalic"):
            if name == "lang" and len(args) > 1:
                return args[1]
            return args[0] if args else ""
        if name in ("sortname", "sort name") and len(args) >= 2:
            return f"{args[0]} {args[1]}"
        if name in ("sort",) and len(args) >= 2:
            return args[1]
        return ""

    # Innermost first, so nested templates collapse correctly.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\{\{([^{}]*)\}\}", repl, text)
    return text


LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")


def plain(text: str) -> str:
    text = re.sub(r"<small>.*?</small>", "", text, flags=re.S)  # e.g. "(posthumously)"
    text = LINK.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), text)
    text = text.replace("'''", "").replace("''", "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("‡", "").replace(" ", " ")
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"\s+([,;.)])", r"\1", text).strip(" ,;")


def split_dash(text: str) -> tuple[str, str]:
    m = re.search(r"\s+[–—]\s+|\s+-\s+", text)
    return (text[: m.start()], text[m.end():]) if m else (text, "")


# ---------- Categories ----------

# Wikipedia article for each award -> (canonical category, class, category name at the time),
# matching the naming in DLu/oscar_data so live and historical data line up.
CATEGORY_MAP = {
    "academy award for best picture": ("BEST PICTURE", "Title", "BEST PICTURE"),
    "academy award for best director": ("DIRECTING", "Directing", "DIRECTING"),
    "academy award for best actor": ("ACTOR IN A LEADING ROLE", "Acting", "ACTOR IN A LEADING ROLE"),
    "academy award for best actress": ("ACTRESS IN A LEADING ROLE", "Acting", "ACTRESS IN A LEADING ROLE"),
    "academy award for best supporting actor": ("ACTOR IN A SUPPORTING ROLE", "Acting", "ACTOR IN A SUPPORTING ROLE"),
    "academy award for best supporting actress": ("ACTRESS IN A SUPPORTING ROLE", "Acting", "ACTRESS IN A SUPPORTING ROLE"),
    "academy award for best original screenplay": ("WRITING (Original Screenplay)", "Writing", "WRITING (Original Screenplay)"),
    "academy award for best adapted screenplay": ("WRITING (Adapted Screenplay)", "Writing", "WRITING (Adapted Screenplay)"),
    "academy award for best animated feature": ("ANIMATED FEATURE FILM", "Title", "ANIMATED FEATURE FILM"),
    "academy award for best international feature film": ("INTERNATIONAL FEATURE FILM", "Title", "INTERNATIONAL FEATURE FILM"),
    "academy award for best documentary feature film": ("DOCUMENTARY (Feature)", "Title", "DOCUMENTARY FEATURE FILM"),
    "academy award for best documentary short film": ("DOCUMENTARY (Short Subject)", "Title", "DOCUMENTARY SHORT FILM"),
    "academy award for best live action short film": ("SHORT FILM (Live Action)", "Title", "LIVE ACTION SHORT FILM"),
    "academy award for best animated short film": ("SHORT FILM (Animated)", "Title", "ANIMATED SHORT FILM"),
    "academy award for best original score": ("MUSIC (Original Score)", "Music", "MUSIC (Original Score)"),
    "academy award for best original song": ("MUSIC (Original Song)", "Music", "MUSIC (Original Song)"),
    "academy award for best sound": ("SOUND MIXING", "Production", "SOUND"),
    "academy award for best casting": ("CASTING", "Production", "CASTING"),
    "academy award for best production design": ("ART DIRECTION", "Production", "PRODUCTION DESIGN"),
    "academy award for best cinematography": ("CINEMATOGRAPHY", "Production", "CINEMATOGRAPHY"),
    "academy award for best makeup and hairstyling": ("MAKEUP AND HAIRSTYLING", "Production", "MAKEUP AND HAIRSTYLING"),
    "academy award for best costume design": ("COSTUME DESIGN", "Production", "COSTUME DESIGN"),
    "academy award for best film editing": ("FILM EDITING", "Production", "FILM EDITING"),
    "academy award for best visual effects": ("VISUAL EFFECTS", "Production", "VISUAL EFFECTS"),
    "academy award for best stunt design": ("STUNT DESIGN", "Production", "STUNT DESIGN"),
}
ALIASES = {
    "academy award for best animated feature film": "academy award for best animated feature",
    "academy award for best documentary feature": "academy award for best documentary feature film",
    "academy award for best documentary short subject": "academy award for best documentary short film",
    "academy award for best live action short": "academy award for best live action short film",
    "academy award for best animated short": "academy award for best animated short film",
    "academy award for best foreign language film": "academy award for best international feature film",
    "academy award for best sound mixing": "academy award for best sound",
    "academy award for best art direction": "academy award for best production design",
    "academy award for best makeup": "academy award for best makeup and hairstyling",
    "academy award for best directing": "academy award for best director",
}


def map_category(target: str, label: str) -> tuple[str, str, str]:
    key = target.strip().lower().replace("_", " ")
    key = ALIASES.get(key, key)
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    # A brand-new category: derive a name and file it under craft awards.
    name = re.sub(r"^best\s+", "", plain(label or target), flags=re.I).upper()
    return name, "Production", name


# ---------- Nominees ----------

def parse_nominees(wikitext: str) -> dict:
    """Parse the "Winners and nominees" table.

    Returns {"expected": int | None, "categories": [{"canonical", "class", "category",
    "label", "nominees": [{"film", "wiki", "who", "detail", "winner"}]}]}.
    """
    text = strip_comments_and_refs(wikitext)
    m = re.search(r"in (\d+) categories", text)
    expected = int(m.group(1)) if m else None

    start = re.search(r"^==\s*Winners and nominees\s*==", text, re.M)
    if not start:
        return {"expected": expected, "categories": []}
    section = text[start.end():]
    nxt = re.search(r"^==[^=]", section, re.M)
    if nxt:
        section = section[: nxt.start()]

    # Film display text -> article, collected from every link in the section, so an
    # unlinked repeat mention (''One Battle After Another'') can still be resolved.
    expanded = expand_templates(section)
    known_links: dict[str, str] = {}
    for lm in re.finditer(r"''\s*\[\[([^\]|]+)(?:\|([^\]]*))?\]\]\s*''", expanded):
        known_links.setdefault(plain(lm.group(2) or lm.group(1)).lower(), lm.group(1).strip())

    categories: list[dict] = []
    current = None
    in_table = False
    for raw in section.split("\n"):
        line = raw.strip()
        if categories and line.startswith("="):
            break  # next sub-section (presenters, honorary awards, ...)
        header = re.search(r"\[\[(Academy Award for [^\]|]+)(?:\|([^\]]*))?\]\]", line)
        # A category header is a table cell holding just the award name (optionally in
        # {{Award category}}), not a sentence that merely mentions it.
        if header and not line.startswith("*") and (
            "award category" in line.lower()
            or re.fullmatch(r"[|!]\s*(?:[^|\[]*\|)?\s*'*\s*\[\[Academy Award for [^\]]+\]\]\s*'*\s*", line)
        ):
            canonical, cls, category = map_category(header.group(1), header.group(2) or header.group(1))
            current = {"canonical": canonical, "class": cls, "category": category,
                       "label": plain(header.group(2) or header.group(1)), "nominees": []}
            categories.append(current)
            in_table = True
            continue
        if in_table and line.startswith("|}"):
            break  # end of the awards table (honorary awards etc. follow)
        if current and line.startswith("*"):
            nominee = parse_nominee_line(line.lstrip("*").strip(), current["canonical"], known_links)
            if nominee:
                current["nominees"].append(nominee)
    return {"expected": expected, "categories": categories}


def parse_nominee_line(line: str, canonical: str, known_links: dict[str, str]) -> dict | None:
    line = expand_templates(line)
    if not plain(line):
        return None  # empty placeholder bullet
    winner = "‡" in line or bool(re.search(r"(?<!')'''(?!')|'''''", line))
    body = line.replace("'''", "")  # drop bold; italics ('') remain

    film_text, wiki = "", None
    im = re.search(r"''(.+?)''", body)
    if im:
        segment = im.group(1)
        lm = LINK.search(segment)
        film_text = plain(segment)
        wiki = lm.group(1).strip() if lm else known_links.get(film_text.lower())
    else:
        # No italics (rare): fall back to the first link.
        lm = LINK.search(body)
        if lm:
            film_text, wiki = plain(lm.group(2) or lm.group(1)), lm.group(1).strip()

    text = plain(body)
    who, detail = "", ""
    if canonical.startswith(("ACTOR", "ACTRESS", "DIRECTING")):
        who, rest = split_dash(text)
        if canonical != "DIRECTING":
            dm = re.search(r"\bas\s+(.+)$", rest)
            detail = dm.group(1).strip() if dm else ""
    elif canonical == "MUSIC (Original Song)":
        sm = re.search(r"[\"“](.+?)[\"”]", text)
        detail = sm.group(1).strip() if sm else ""
        who = split_dash(text)[1]
    elif canonical == "INTERNATIONAL FEATURE FILM":
        # "Film (Country)", optionally followed by languages and director.
        after = text[text.find(film_text) + len(film_text):] if film_text in text else text
        cm = re.match(r"\s*\(([^()]+)\)", after)
        who = cm.group(1).strip() if cm else ""
    else:
        who = split_dash(text)[1]

    if not film_text:
        return None
    return {"film": film_text, "wiki": wiki, "who": who.strip(), "detail": detail, "winner": winner}


# ---------- Dates ----------

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"({MONTHS})\s+(\d{{1,2}}),\s*(\d{{4}})")


def parse_date(text: str) -> date | None:
    m = DATE_RE.search(text)
    return datetime.strptime(" ".join(m.groups()), "%B %d %Y").date() if m else None


def parse_key_dates(wikitext: str) -> dict:
    """Nominations announcement and ceremony dates. Returns {"nominations": date|None, "ceremony": date|None}."""
    raw = strip_comments_and_refs(wikitext)
    text = expand_templates(raw)  # note: this removes the infobox template entirely
    found = {"nominations": None, "ceremony": None}

    # 1) The "Key dates" table: rows of "|<date>" then "|<event>".
    km = re.search(r"^==\s*Key dates\s*==(.*?)(?=^==[^=]|\Z)", text, re.M | re.S)
    if km:
        for row in km.group(1).split("|-"):
            cells = [plain(c) for c in re.split(r"\n\s*[|!]", "\n" + row) if plain(c)]
            row_date = next((parse_date(c) for c in cells if parse_date(c)), None)
            event = " ".join(c for c in cells if not parse_date(c)).lower()
            if not row_date:
                continue
            if "nominations announced" in event or ("nominations" in event and "announce" in event):
                found["nominations"] = row_date
            elif event.strip() in ("ceremony", "academy awards ceremony", "oscars ceremony") or event.startswith("ceremony"):
                found["ceremony"] = row_date

    # 2) Infobox "| date =" for the ceremony.
    if not found["ceremony"]:
        im = re.search(r"^\s*\|\s*date\s*=\s*(.+)$", raw, re.M)
        if im:
            sd = re.search(r"start date\|(\d{4})\|(\d{1,2})\|(\d{1,2})", im.group(1), re.I)
            found["ceremony"] = date(*map(int, sd.groups())) if sd else parse_date(plain(expand_templates(im.group(1))))

    # 3) Prose such as "nominations will be announced on January 21, 2027".
    if not found["nominations"]:
        pm = re.search(rf"nominations\s+(?:were|will be|are|are scheduled to be|is scheduled to be)\s+(?:\w+\s+)?announced\s+on\s+(?:\w+,\s+)?(({MONTHS})\s+\d{{1,2}},\s*\d{{4}})", plain(text), re.I)
        if pm:
            found["nominations"] = parse_date(pm.group(1))
    return found
