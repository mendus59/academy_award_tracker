"""Find the IMDb id (tt...) for newly nominated films.

Tried in order, stopping at the first hit:
  1. films already in data/movies.json (same title, released within a year)
  2. the film's Wikipedia article -> Wikidata -> IMDb id (P345)
  3. TMDB search (needs TMDB_READ_ACCESS_TOKEN or TMDB_API_KEY) -> external ids
  4. IMDb's title.basics dataset, if exactly one title matches name and year
"""

import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from wiki_awards import USER_AGENT, api_get

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
TMDB = "https://api.themoviedb.org/3"


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\s*\(\d{4}(?: film)?\)$", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


def from_existing(films: list[dict], movies: list[dict], year: int) -> None:
    index: dict[str, list[dict]] = {}
    for m in movies:
        index.setdefault(norm_title(m["t"]), []).append(m)
    for f in films:
        if f.get("filmId"):
            continue
        hits = [m for m in index.get(norm_title(f["film"]), []) if abs(m["y"] - year) <= 1]
        if len(hits) == 1:
            f["filmId"], f["via"] = hits[0]["id"], "existing"


def from_wikidata(films: list[dict]) -> None:
    todo = [f for f in films if not f.get("filmId") and f.get("wiki")]
    titles = sorted({f["wiki"] for f in todo})
    qids: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        data = api_get({"action": "query", "prop": "pageprops", "ppprop": "wikibase_item", "redirects": 1, "titles": "|".join(batch)})
        q = data.get("query", {})
        # Map requested titles through normalization and redirects to final titles.
        alias = {t: t for t in batch}
        for step in ("normalized", "redirects"):
            for r in q.get(step, []):
                for k, v in list(alias.items()):
                    if v == r["from"]:
                        alias[k] = r["to"]
        page_qid = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in q.get("pages", [])}
        for t in batch:
            if page_qid.get(alias[t]):
                qids[t] = page_qid[alias[t]]

    ids = sorted(set(qids.values()))
    imdb: dict[str, str] = {}
    for i in range(0, len(ids), 50):
        data = api_get({"action": "wbgetentities", "ids": "|".join(ids[i : i + 50]), "props": "claims"}, url=WIKIDATA_API)
        for qid, entity in data.get("entities", {}).items():
            for claim in entity.get("claims", {}).get("P345", []):
                value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", "")
                if value.startswith("tt"):
                    imdb[qid] = value
                    break
    for f in todo:
        qid = qids.get(f["wiki"])
        if qid and qid in imdb:
            f["filmId"], f["via"] = imdb[qid], "wikidata"


def tmdb_get(path: str, **params) -> dict:
    token, key = os.environ.get("TMDB_READ_ACCESS_TOKEN"), os.environ.get("TMDB_API_KEY")
    if not token:
        params["api_key"] = key
    url = f"{TMDB}{path}?{urllib.parse.urlencode(params)}"
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as res:
        return json.load(res)


def from_tmdb(films: list[dict], year: int) -> None:
    if not (os.environ.get("TMDB_READ_ACCESS_TOKEN") or os.environ.get("TMDB_API_KEY")):
        return
    for f in films:
        if f.get("filmId"):
            continue
        try:
            for y in (year, year - 1, None):
                params = {"query": f["film"], "include_adult": "false"}
                if y:
                    params["primary_release_year"] = y
                results = [r for r in tmdb_get("/search/movie", **params).get("results", [])
                           if norm_title(r.get("title", "")) == norm_title(f["film"])
                           or norm_title(r.get("original_title", "")) == norm_title(f["film"])]
                if results:
                    imdb_id = tmdb_get(f"/movie/{results[0]['id']}/external_ids").get("imdb_id")
                    if imdb_id:
                        f["filmId"], f["via"] = imdb_id, "tmdb"
                    break
        except Exception as err:  # network hiccup: leave unresolved, retried next run
            print(f"  TMDB lookup failed for {f['film']!r}: {err}", file=sys.stderr)


def from_imdb_basics(films: list[dict], basics: Path, year: int) -> None:
    wanted = {norm_title(f["film"]): f for f in films if not f.get("filmId")}
    if not wanted or not basics.exists():
        return
    hits: dict[str, list[str]] = {k: [] for k in wanted}
    kinds = {"movie", "short", "tvMovie", "video", "tvShort", "tvSpecial"}
    with gzip.open(basics, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if parts[1] not in kinds or not parts[5].isdigit() or abs(int(parts[5]) - year) > 1:
                continue
            for title in {parts[2], parts[3]}:
                key = norm_title(title)
                if key in hits and parts[0] not in hits[key]:
                    hits[key].append(parts[0])
    for key, f in wanted.items():
        if len(hits[key]) == 1:
            f["filmId"], f["via"] = hits[key][0], "imdb"


def resolve(films: list[dict], movies: list[dict], year: int, basics: Path | None = None, use_existing: bool = True) -> list[dict]:
    """Fill in f["filmId"] for each film dict ({"film", "wiki"}). Returns the unresolved ones."""
    if use_existing:
        from_existing(films, movies, year)
    from_wikidata(films)
    from_tmdb(films, year)
    if basics:
        from_imdb_basics(films, basics, year)
    return [f for f in films if not f.get("filmId")]
