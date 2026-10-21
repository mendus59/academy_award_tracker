"""Build data/movies.json from the Oscar nominations dataset + IMDb genres.

Sources (both free, no API key needed):
  * Oscar nominations: https://github.com/DLu/oscar_data (oscars.csv, tab-separated)
  * Genres/runtime:    https://datasets.imdbws.com/title.basics.tsv.gz

Usage:
    python scripts/build_movies.py            # downloads sources into scripts/.cache
    python scripts/build_movies.py --refresh  # re-download sources first

Re-run after each Oscars ceremony once the oscar_data repo has been updated.
"""

import argparse
import csv
import gzip
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fetch_tmdb import apply_posters, load_posters  # cached TMDB posters (no key needed)

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(__file__).resolve().parent / ".cache"
OUT = ROOT / "data" / "movies.json"
LIVE_DIR = Path(__file__).resolve().parent / "live"  # scraped by scripts/automation.py

OSCARS_URL = "https://raw.githubusercontent.com/DLu/oscar_data/main/oscars.csv"
IMDB_URL = "https://datasets.imdbws.com/title.basics.tsv.gz"

# Scientific/technical awards are not given to films, so they're excluded.
EXCLUDED_CLASSES = {"SciTech"}

# Friendlier labels for the dataset's award classes (used to group categories).
CLASS_LABELS = {
    "Title": "Picture & Feature",
    "Acting": "Acting",
    "Directing": "Directing",
    "Writing": "Writing",
    "Production": "Production & Craft",
    "Music": "Music",
    "Special": "Special & Honorary",
}
CLASS_ORDER = list(CLASS_LABELS)

# Categories listed first (in this order); everything else follows alphabetically.
PRIORITY = [
    "BEST PICTURE",
    "DIRECTING",
    "ACTOR IN A LEADING ROLE",
    "ACTRESS IN A LEADING ROLE",
    "ACTOR IN A SUPPORTING ROLE",
    "ACTRESS IN A SUPPORTING ROLE",
    "WRITING (Original Screenplay)",
    "WRITING (Adapted Screenplay)",
    "ANIMATED FEATURE FILM",
    "INTERNATIONAL FEATURE FILM",
    "DOCUMENTARY (Feature)",
    "CINEMATOGRAPHY",
    "FILM EDITING",
    "MUSIC (Original Score)",
    "MUSIC (Original Song)",
    "COSTUME DESIGN",
    "ART DIRECTION",
    "MAKEUP AND HAIRSTYLING",
    "SOUND MIXING",
    "SOUND EDITING",
    "VISUAL EFFECTS",
    "CASTING",
]

# Classes whose "Detail" column is worth showing (character name / song title).
DETAIL_CLASSES = {"Acting", "Music"}


def fetch(url: str, dest: Path, refresh: bool) -> Path:
    if dest.exists() and not refresh:
        return dest
    CACHE.mkdir(exist_ok=True)
    print(f"Downloading {url} ...", file=sys.stderr)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(dest)
    return dest


def clean(value: str) -> str:
    """The source escapes some quotes (e.g. John \"Divine G\" Whitfield); unescape them."""
    return (value or "").replace('\\"', '"').strip()


def split(value: str) -> list[str]:
    return [clean(v) for v in (value or "").split("|")]


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return [row for row in csv.DictReader(f, delimiter="\t") if row["Class"] not in EXCLUDED_CLASSES]


def live_rows(path: Path) -> tuple[int, list[dict]]:
    """Rows in oscars.csv shape from a live file (scripts/live/<edition>.json)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    edition = int(data["edition"])
    rows = []
    for cat in data["categories"]:
        for n in cat["nominees"]:
            if not n.get("filmId"):
                continue  # not yet matched to IMDb; reported by automation.py
            rows.append({
                "Ceremony": str(edition), "Year": str(edition + 1926), "Class": cat["class"],
                "CanonicalCategory": cat["canonical"], "Category": cat["category"],
                "Film": n["film"], "FilmId": n["filmId"], "Name": n.get("who", ""),
                "Winner": "True" if n.get("winner") else "", "Detail": n.get("detail", ""),
            })
    return edition, rows


def merge_live(rows: list[dict]) -> list[dict]:
    """Use live data for a ceremony when oscar_data doesn't have it yet, or has fewer
    winners (i.e. on and just after ceremony day). Honorary awards stay from oscar_data."""
    def wins(rs):
        return sum(r["Winner"] == "True" for r in rs)

    for path in sorted(LIVE_DIR.glob("*.json")):
        edition, live = live_rows(path)
        if not live:
            continue
        in_edition = lambda r: r["Ceremony"] == str(edition) and r["Class"] != "Special"  # noqa: E731
        dlu = [r for r in rows if in_edition(r)]
        if not dlu or wins(live) > wins(dlu):
            rows = [r for r in rows if not in_edition(r)] + live
            print(f"Ceremony {edition}: using live data ({len(live)} nominations, {wins(live)} winners)", file=sys.stderr)
    return rows


def load_nominations(rows: list[dict]):
    for row in rows:
        ids = split(row["FilmId"])
        titles = split(row["Film"])
        details = split(row.get("Detail") or "")
        for i, film_id in enumerate(ids):
            if not film_id.startswith("tt"):
                continue
            title = titles[i] if i < len(titles) else titles[0]
            # Multi-film nominations list one detail per film; otherwise share it.
            detail = details[i] if len(details) == len(ids) else (details[0] if len(ids) == 1 else "")
            yield film_id, title, row, detail


def load_imdb(path: Path, wanted: set[str]) -> dict[str, dict]:
    info = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f)
        for line in f:
            tconst = line[: line.index("\t")]
            if tconst not in wanted:
                continue
            parts = line.rstrip("\n").split("\t")
            _, title_type, _, _, _, start_year, _, runtime, genres = parts
            info[tconst] = {
                "type": title_type,
                "year": int(start_year) if start_year.isdigit() else None,
                "runtime": int(runtime) if runtime.isdigit() else None,
                "genres": [] if genres == r"\N" else genres.split(","),
            }
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download source data")
    args = parser.parse_args()

    oscars_path = fetch(OSCARS_URL, CACHE / "oscars.csv", args.refresh)
    imdb_path = fetch(IMDB_URL, CACHE / "title.basics.tsv.gz", args.refresh)

    movies: dict[str, dict] = {}
    categories: dict[str, str] = {}  # canonical category -> class

    for film_id, title, row, detail in load_nominations(merge_live(read_rows(oscars_path))):
        category = row["CanonicalCategory"]
        categories.setdefault(category, row["Class"])
        movie = movies.setdefault(film_id, {"id": film_id, "t": title, "n": []})
        nom = {
            "c": category,
            "cer": int(row["Ceremony"]),
            "ay": row["Year"],
        }
        if row["Winner"] == "True":
            nom["w"] = 1
        if row.get("Name"):
            nom["who"] = clean(row["Name"])
        if detail and row["Class"] in DETAIL_CLASSES:
            nom["d"] = detail
        if row["Category"] != category:
            nom["as"] = row["Category"]  # the category's name at the time
        movie["n"].append(nom)

    print(f"Reading IMDb data for {len(movies)} films ...", file=sys.stderr)
    imdb = load_imdb(imdb_path, set(movies))

    priority = {c: i for i, c in enumerate(PRIORITY)}
    ordered_categories = sorted(
        categories,
        key=lambda c: (priority.get(c, len(priority)), CLASS_ORDER.index(categories[c]), c),
    )
    cat_index = {c: i for i, c in enumerate(ordered_categories)}

    out_movies = []
    for movie in movies.values():
        meta = imdb.get(movie["id"], {})
        noms = sorted(movie["n"], key=lambda n: (n["cer"], cat_index[n["c"]]))
        first = noms[0]
        award_year = int(first["ay"].split("/")[0])
        genres = list(meta.get("genres", []))
        if meta.get("type") == "short" and "Short" not in genres:
            genres.append("Short")
        entry = {
            "id": movie["id"],
            "t": movie["t"],
            "y": meta.get("year") or award_year,  # release year
            "ay": award_year,  # Oscar eligibility year of first nomination
            "g": genres,
            "n": noms,
        }
        if meta.get("runtime"):
            entry["rt"] = meta["runtime"]
        out_movies.append(entry)

    out_movies.sort(key=lambda m: (-m["ay"], m["t"]))
    with_posters = apply_posters(out_movies, load_posters())

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "DLu/oscar_data + IMDb non-commercial datasets",
        "classes": [{"id": c, "label": CLASS_LABELS[c]} for c in CLASS_ORDER],
        "categories": [{"name": c, "class": categories[c]} for c in ordered_categories],
        "movies": out_movies,
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    missing = sum(1 for m in out_movies if not m["g"])
    noms = sum(len(m["n"]) for m in out_movies)
    wins = sum(1 for m in out_movies for n in m["n"] if n.get("w"))
    print(
        f"Wrote {OUT.relative_to(ROOT)}: {len(out_movies)} films, {noms} nominations, "
        f"{wins} wins ({missing} films without genre data, {with_posters} with posters)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
