"""Look up each film's poster and synopsis on TMDB.

Posters are added to data/movies.json; synopses are written to data/synopses/NN.json
(100 small files keyed by the last two digits of the IMDb id) so the site only downloads
a synopsis file when someone opens a film, instead of growing movies.json for everyone.

TMDB (https://www.themoviedb.org) is free for non-commercial use with attribution.
Get a key at https://www.themoviedb.org/settings/api, then set ONE of these in the
git-ignored .env file at the repo root (see .env.example) or as an environment variable:

    TMDB_READ_ACCESS_TOKEN   the long "API Read Access Token" (preferred)
    TMDB_API_KEY             the short "API Key"

Usage:
    python scripts/fetch_tmdb.py                  # look up films not yet in the caches
    python scripts/fetch_tmdb.py --retry-missing  # also retry films that had no poster/synopsis

Results are cached in scripts/posters.json and scripts/synopses.json (commit both), so
later runs only query new films and build_movies.py can add posters without a key.
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOVIES = ROOT / "data" / "movies.json"
SYNOPSES_DIR = ROOT / "data" / "synopses"
CACHE = Path(__file__).resolve().parent / "posters.json"
SYNOPSES_CACHE = Path(__file__).resolve().parent / "synopses.json"
ENV_FILE = ROOT / ".env"
API = "https://api.themoviedb.org/3/find/{}?external_source=imdb_id&language=en-US"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Read KEY=value lines from .env into os.environ (real env vars win; blanks are skipped)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if value and not os.environ.get(key):
            os.environ[key] = value


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_posters() -> dict[str, str | None]:
    """imdb id -> TMDB poster path (e.g. "/abc.jpg"), or None if TMDB has none."""
    return _load(CACHE)


def load_synopses() -> dict[str, str | None]:
    """imdb id -> TMDB overview text, or None if TMDB has none."""
    return _load(SYNOPSES_CACHE)


def apply_posters(movies: list[dict], posters: dict[str, str | None]) -> int:
    """Set m["p"] on each movie that has a poster. Returns how many have one."""
    count = 0
    for m in movies:
        path = posters.get(m["id"])
        if path:
            m["p"] = path
            count += 1
        else:
            m.pop("p", None)
    return count


def shard(imdb_id: str) -> str:
    return imdb_id[-2:]


def write_synopsis_shards(movies: list[dict], synopses: dict[str, str | None]) -> int:
    """Write data/synopses/00.json ... 99.json for the films on the site. Returns how many have one."""
    shards: dict[str, dict[str, str]] = {f"{i:02d}": {} for i in range(100)}
    for m in movies:
        text = synopses.get(m["id"])
        if text:
            shards[shard(m["id"])][m["id"]] = text
    SYNOPSES_DIR.mkdir(parents=True, exist_ok=True)
    for name, entries in shards.items():
        path = SYNOPSES_DIR / f"{name}.json"
        path.write_text(json.dumps(dict(sorted(entries.items())), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return sum(len(e) for e in shards.values())


def make_request(imdb_id: str) -> urllib.request.Request:
    token = os.environ.get("TMDB_READ_ACCESS_TOKEN")
    key = os.environ.get("TMDB_API_KEY")
    url = API.format(urllib.parse.quote(imdb_id))
    headers = {"Accept": "application/json", "User-Agent": "nominated-film-tracker"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        url += f"&api_key={urllib.parse.quote(key)}"
    return urllib.request.Request(url, headers=headers)


def lookup(imdb_id: str) -> dict:
    """Returns {"poster": path or None, "overview": text or None}."""
    for attempt in range(5):
        try:
            with urllib.request.urlopen(make_request(imdb_id), timeout=20) as res:
                data = json.load(res)
            results = data.get("movie_results", []) + data.get("tv_results", [])
            poster = next((r["poster_path"] for r in results if r.get("poster_path")), None)
            overview = next((r["overview"].strip() for r in results if (r.get("overview") or "").strip()), None)
            return {"poster": poster, "overview": overview}
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise SystemExit("TMDB rejected the key (401). Check TMDB_READ_ACCESS_TOKEN / TMDB_API_KEY.")
            if err.code == 404:
                return {"poster": None, "overview": None}
            if err.code == 429 or err.code >= 500:
                time.sleep(float(err.headers.get("Retry-After") or 2 ** attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Gave up on {imdb_id} after repeated errors")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--retry-missing", action="store_true", help="retry films cached as having no poster or synopsis")
    parser.add_argument("--workers", type=int, default=8, help="parallel requests (default 8)")
    args = parser.parse_args()

    load_dotenv()
    if not (os.environ.get("TMDB_READ_ACCESS_TOKEN") or os.environ.get("TMDB_API_KEY")):
        raise SystemExit(f"Add TMDB_READ_ACCESS_TOKEN (or TMDB_API_KEY) to {ENV_FILE} first (see .env.example).")

    data = json.loads(MOVIES.read_text(encoding="utf-8"))
    posters, synopses = load_posters(), load_synopses()

    def needed(imdb_id: str) -> bool:
        missing = imdb_id not in posters or imdb_id not in synopses
        retry = args.retry_missing and (posters.get(imdb_id) is None or synopses.get(imdb_id) is None)
        return missing or retry

    todo = [m["id"] for m in data["movies"] if needed(m["id"])]
    print(f"{len(todo)} films to look up on TMDB", file=sys.stderr)

    lock = threading.Lock()

    def save() -> None:
        CACHE.write_text(json.dumps(dict(sorted(posters.items())), indent=0), encoding="utf-8")
        SYNOPSES_CACHE.write_text(json.dumps(dict(sorted(synopses.items())), ensure_ascii=False, indent=0), encoding="utf-8")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(lookup, imdb_id): imdb_id for imdb_id in todo}
        for future in as_completed(futures):
            imdb_id = futures[future]
            try:
                result = future.result()
            except Exception as err:  # keep going; the film is retried next run
                print(f"  {imdb_id}: {err}", file=sys.stderr)
                continue
            with lock:
                # Keep a poster we already have if this lookup didn't return one.
                posters[imdb_id] = result["poster"] or posters.get(imdb_id)
                synopses[imdb_id] = result["overview"]
                done += 1
                if done % 250 == 0:
                    save()
                    print(f"  {done}/{len(todo)}", file=sys.stderr)
    save()

    found = apply_posters(data["movies"], posters)
    MOVIES.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with_synopsis = write_synopsis_shards(data["movies"], synopses)
    total = len(data["movies"])
    print(f"Posters for {found} of {total} films, synopses for {with_synopsis}; updated data/", file=sys.stderr)


if __name__ == "__main__":
    main()
