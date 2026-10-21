"""Yearly Academy Awards automation, run by GitHub Actions (.github/workflows/awards-*.yml).

    python scripts/automation.py dates        # find the announced nominations/ceremony dates
    python scripts/automation.py nominations  # on nominations day: add the new nominees
    python scripts/automation.py winners      # on ceremony day: publish winners as they're announced

Each command first checks data/schedule.json and exits quickly unless it's time to act,
so the workflows can run on a frequent schedule. Add --publish to commit, push and
redeploy the site (GitHub Actions does this); without it files are only written locally.

Testing options: --now 2027-01-21T14:00Z, --force, --edition 99, --wikitext FILE|DIR.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wiki_awards  # noqa: E402
from build_movies import IMDB_URL, OSCARS_URL, fetch  # noqa: E402
from fetch_posters import load_dotenv  # noqa: E402
from resolve_ids import resolve  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCHEDULE = ROOT / "data" / "schedule.json"
MOVIES = ROOT / "data" / "movies.json"
LIVE_DIR = ROOT / "scripts" / "live"
BASICS = ROOT / "scripts" / ".cache" / "title.basics.tsv.gz"

DEFAULT_NOMINATIONS_TIME = "05:30"  # Pacific; the Academy's usual announcement time
DEFAULT_CEREMONY_TIME = "16:00"  # Pacific (7 p.m. Eastern)
NOMINATIONS_WINDOW = timedelta(hours=24)  # keep retrying this long after the announcement
WINNERS_LEAD = timedelta(hours=1)  # start watching this long before the ceremony
MIN_NOMINEES = 5  # every current category has at least five nominees


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------- Time (US Pacific, without needing tz data installed) ----------

def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)  # first Sunday
    return d + timedelta(weeks=n - 1)


def pacific_to_utc(day: date, hhmm: str) -> datetime:
    """Convert a Pacific wall-clock time to UTC (DST: 2nd Sunday of March to 1st Sunday of November)."""
    h, m = map(int, hhmm.split(":"))
    local = datetime(day.year, day.month, day.day, h, m)
    dst = _nth_sunday(day.year, 3, 2) <= day < _nth_sunday(day.year, 11, 1)
    return (local + timedelta(hours=7 if dst else 8)).replace(tzinfo=timezone.utc)


def utc_to_pacific_date(moment: datetime) -> date:
    guess = (moment - timedelta(hours=8)).date()
    return (moment - timedelta(hours=7)).date() if pacific_to_utc(guess, "12:00").hour == 19 else guess


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------- Schedule ----------

def load_schedule() -> dict:
    if SCHEDULE.exists():
        return json.loads(SCHEDULE.read_text(encoding="utf-8"))
    return {"timezone": "America/Los_Angeles", "editions": []}


def save_schedule(schedule: dict) -> None:
    schedule["editions"].sort(key=lambda e: e["edition"])
    for e in schedule["editions"]:
        # Precomputed UTC instants for the website (banner and live updates).
        e["nominationsAt"] = iso(pacific_to_utc(date.fromisoformat(e["nominations"]), e.get("nominationsTime", DEFAULT_NOMINATIONS_TIME))) if e.get("nominations") else None
        e["ceremonyAt"] = iso(pacific_to_utc(date.fromisoformat(e["ceremony"]), e.get("ceremonyTime", DEFAULT_CEREMONY_TIME))) if e.get("ceremony") else None
    SCHEDULE.write_text(json.dumps(schedule, indent=2) + "\n", encoding="utf-8")


def edition_entry(schedule: dict, edition: int) -> dict:
    for e in schedule["editions"]:
        if e["edition"] == edition:
            return e
    e = {"edition": edition, "nominations": None, "ceremony": None,
         "nominationsCaptured": None, "winnersComplete": None}
    schedule["editions"].append(e)
    return e


def latest_ceremony() -> int:
    return max(n["cer"] for m in json.loads(MOVIES.read_text(encoding="utf-8"))["movies"] for n in m["n"])


# ---------- Publishing ----------

def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True)


def publish(args, message: str) -> bool:
    """Commit data changes, push, and trigger the Pages deploy. Returns True if anything was pushed."""
    if not args.publish:
        log(f"[dry run] would publish: {message}")
        return False
    paths = [p for p in ("data", "scripts/live", "scripts/posters.json") if (ROOT / p).exists()]
    run(["git", "add", *paths], check=False)
    if run(["git", "diff", "--cached", "--quiet"], check=False).returncode == 0:
        log("No data changes to publish.")
        return False
    run(["git", "commit", "-m", message])
    for attempt in range(5):
        if run(["git", "push"], check=False).returncode == 0:
            break
        log("Push rejected; rebasing on the latest main and retrying.")
        run(["git", "pull", "--rebase", "--autostash"], check=False)
        time.sleep(2 * (attempt + 1))
    else:
        raise RuntimeError("Could not push data changes")
    # Pushes made with the workflow token don't trigger other workflows, so ask for a deploy.
    if shutil.which("gh"):
        result = run(["gh", "workflow", "run", "deploy-pages.yml", "--ref", "main"], check=False)
        log("Requested site deploy." if result.returncode == 0 else f"Deploy request failed: {result.stderr.strip()}")
    log(f"Published: {message}")
    return True


def open_issue(args, title: str, body: str) -> None:
    """Open a GitHub issue (once) so a person knows to step in."""
    log(f"ALERT: {title}\n{body}")
    if not (args.publish and shutil.which("gh")):
        return
    existing = run(["gh", "issue", "list", "--state", "open", "--search", f'"{title}" in:title', "--json", "title"], check=False)
    if existing.returncode == 0 and title in existing.stdout:
        return
    run(["gh", "issue", "create", "--title", title, "--body", body], check=False)


def set_output(key: str, value: str) -> None:
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"{key}={value}\n")


# ---------- Wikipedia access (overridable for tests) ----------

class Wiki:
    """Fetch the ceremony article; --wikitext FILE serves a file, DIR serves 1.txt, 2.txt, ... in turn."""

    def __init__(self, source: str | None):
        self.source = Path(source) if source else None
        self.step = 0

    def get(self, edition: int) -> tuple[str, int] | None:
        if not self.source:
            return wiki_awards.fetch_wikitext(edition)
        if self.source.is_file():
            return self.source.read_text(encoding="utf-8"), 1
        files = sorted(self.source.glob("*.txt"), key=lambda p: int(p.stem))
        if not files:
            return None  # an empty directory stands for "article doesn't exist"
        self.step = min(self.step + 1, len(files))
        return files[self.step - 1].read_text(encoding="utf-8"), self.step


# ---------- dates ----------

def cmd_dates(args) -> None:
    now = args.now
    schedule = load_schedule()
    upcoming = latest_ceremony() + 1
    changed = []
    for edition in (upcoming, upcoming + 1):  # the Academy often announces two years at once
        page = args.wiki.get(edition)
        if not page:
            log(f"No Wikipedia article for the {wiki_awards.ordinal(edition)} Academy Awards yet.")
            continue
        found = wiki_awards.parse_key_dates(page[0])
        # Sanity check: the Nth ceremony (and its nominations) fall in year N + 1928.
        found = {k: v if v and v.year == edition + 1928 else None for k, v in found.items()}
        log(f"{wiki_awards.ordinal(edition)}: found {found}")
        entry = edition_entry(schedule, edition) if any(found.values()) else None
        for key in ("nominations", "ceremony"):
            if entry is not None and found[key] and entry.get(key) != found[key].isoformat():
                changed.append(f"{wiki_awards.ordinal(edition)} {key} {entry.get(key)} -> {found[key]}")
                entry[key] = found[key].isoformat()
                entry["source"] = f"https://en.wikipedia.org/wiki/{wiki_awards.page_title(edition).replace(' ', '_')}"
    if changed:
        save_schedule(schedule)
        log("Updated: " + "; ".join(changed))
        publish(args, "Update Academy Awards dates: " + "; ".join(changed))

    # After May (dates are normally announced in April), missing dates need a person to look.
    entry = next((e for e in schedule["editions"] if e["edition"] == upcoming), {})
    today = utc_to_pacific_date(now)
    if today >= date(upcoming + 1927, 6, 1) and not (entry.get("nominations") and entry.get("ceremony")):
        open_issue(args, f"Dates not found for the {wiki_awards.ordinal(upcoming)} Academy Awards",
                   "The yearly date check couldn't find the nominations and/or ceremony date. "
                   "Add them to `data/schedule.json` (see README → Yearly automation).")


# ---------- shared sync: scrape -> resolve -> live file -> build ----------

def sync_edition(args, edition: int, page: tuple[str, int] | None, refresh: bool) -> dict:
    """Scrape the article into scripts/live/<edition>.json and rebuild data when it changed.

    Returns {"categories", "complete_nominations", "complete_winners", "problems", "changed", "winners"}.
    """
    status = {"categories": 0, "complete_nominations": False, "complete_winners": False,
              "problems": [], "changed": False, "winners": 0}
    if not page:
        status["problems"].append("The Wikipedia article doesn't exist yet.")
        return status
    parsed = wiki_awards.parse_nominees(page[0])
    cats = parsed["categories"]
    status["categories"] = len(cats)
    if not cats or not any(c["nominees"] for c in cats):
        status["problems"].append("Nominees aren't on the page yet.")
        return status

    movies = json.loads(MOVIES.read_text(encoding="utf-8"))["movies"]
    expected = parsed["expected"] or len({n["c"] for m in movies for n in m["n"] if n["cer"] == latest_ceremony()})
    live_path = LIVE_DIR / f"{edition}.json"
    previous = json.loads(live_path.read_text(encoding="utf-8")) if live_path.exists() else None

    # Reuse ids found on earlier runs; only look up films we haven't matched yet.
    known = {}
    if previous:
        for c in previous["categories"]:
            for n in c["nominees"]:
                if n.get("filmId"):
                    known[(n["film"], n.get("wiki"))] = n["filmId"]
    films = {}
    for c in cats:
        for n in c["nominees"]:
            key = (n["film"], n["wiki"])
            films.setdefault(key, {"film": n["film"], "wiki": n["wiki"], "filmId": known.get(key)})
    todo = [f for f in films.values() if not f["filmId"]]
    if todo:
        log(f"Matching {len(todo)} films to IMDb ...")
        if refresh or not BASICS.exists():
            fetch(IMDB_URL, BASICS, refresh=True)  # new films appear in IMDb's daily export
            fetch(OSCARS_URL, BASICS.parent / "oscars.csv", refresh=True)
        resolve(todo, movies, edition + 1926, basics=BASICS)
    for c in cats:
        for n in c["nominees"]:
            n["filmId"] = films[(n["film"], n["wiki"])]["filmId"]

    unresolved = sorted({n["film"] for c in cats for n in c["nominees"] if not n["filmId"]})
    short = [f"{c['label']} ({len(c['nominees'])})" for c in cats if len(c["nominees"]) < MIN_NOMINEES]
    if len(cats) < expected:
        status["problems"].append(f"Only {len(cats)} of {expected} categories are listed.")
    if short:
        status["problems"].append("Categories with fewer than five nominees: " + ", ".join(short))
    if unresolved:
        status["problems"].append("Couldn't match these films to IMDb: " + ", ".join(unresolved))
    status["complete_nominations"] = not status["problems"]
    status["winners"] = sum(any(n["winner"] for n in c["nominees"]) for c in cats)
    status["complete_winners"] = status["complete_nominations"] and status["winners"] == len(cats)

    live = {"edition": edition, "source": f"https://en.wikipedia.org/wiki/{wiki_awards.page_title(edition).replace(' ', '_')}",
            "revision": page[1], "expected": expected, "categories": cats}
    comparable = lambda d: json.dumps(d and d["categories"], sort_keys=True)  # noqa: E731
    if comparable(previous) != comparable(live):
        LIVE_DIR.mkdir(exist_ok=True)
        live["fetched"] = iso(datetime.now(timezone.utc))
        live_path.write_text(json.dumps(live, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        status["changed"] = True
        rebuild(refresh=False)
    return status


def rebuild(refresh: bool) -> None:
    cmd = [sys.executable, "scripts/build_movies.py"] + (["--refresh"] if refresh else [])
    result = run(cmd, check=False)
    log(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError("build_movies.py failed")
    if os.environ.get("TMDB_READ_ACCESS_TOKEN") or os.environ.get("TMDB_API_KEY"):
        posters = run([sys.executable, "scripts/fetch_posters.py"], check=False)
        log(posters.stderr.strip().splitlines()[-1] if posters.stderr.strip() else "")


# ---------- nominations ----------

def nominations_target(args, schedule: dict) -> tuple[dict | None, datetime | None]:
    """The edition whose nominations should be fetched now, and when its retry window ends."""
    now = args.now
    for e in schedule["editions"]:
        if args.edition and e["edition"] != args.edition:
            continue
        if e.get("nominationsCaptured") and not args.force:
            continue
        if args.force and args.edition:
            return e, now + timedelta(hours=1)
        if e.get("nominations"):
            start = pacific_to_utc(date.fromisoformat(e["nominations"]), e.get("nominationsTime", DEFAULT_NOMINATIONS_TIME))
            if start - timedelta(minutes=30) <= now <= start + NOMINATIONS_WINDOW:
                return e, start + NOMINATIONS_WINDOW
        elif e.get("ceremony"):
            # Date never announced: keep checking during the usual season before the ceremony.
            ceremony = pacific_to_utc(date.fromisoformat(e["ceremony"]), e.get("ceremonyTime", DEFAULT_CEREMONY_TIME))
            if ceremony - timedelta(days=75) <= now < ceremony:
                return e, ceremony
    return None, None


def cmd_nominations(args) -> None:
    schedule = load_schedule()
    entry, window_end = nominations_target(args, schedule)
    if args.check:
        set_output("run", "true" if entry else "false")
        log(f"Nominations window open for edition {entry['edition']}." if entry else "Not within a nominations window.")
        return
    if not entry:
        log("Not within a nominations window; nothing to do.")
        return
    edition = entry["edition"]
    log(f"Fetching nominations for the {wiki_awards.ordinal(edition)} Academy Awards ...")
    status = sync_edition(args, edition, args.wiki.get(edition), refresh=True)
    log(f"{status['categories']} categories; problems: {status['problems'] or 'none'}")

    last_attempt = args.now >= window_end - timedelta(minutes=65)
    if status["complete_nominations"]:
        entry["nominationsCaptured"] = iso(args.now)
        save_schedule(schedule)
        publish(args, f"Add {wiki_awards.ordinal(edition)} Academy Awards nominations")
        set_output("complete", "true")
    elif last_attempt:
        if status["changed"] or (LIVE_DIR / f"{edition}.json").exists():
            publish(args, f"Add {wiki_awards.ordinal(edition)} Academy Awards nominations (partial)")
        open_issue(args, f"Nominations incomplete for the {wiki_awards.ordinal(edition)} Academy Awards",
                   "Retried hourly for 24 hours. Remaining problems:\n\n- " + "\n- ".join(status["problems"])
                   + "\n\nRun the **Awards: nominations** workflow manually with `force` once fixed.")
    else:
        log("Incomplete; the next hourly run will retry.")


# ---------- winners ----------

def winners_target(args, schedule: dict) -> dict | None:
    now = args.now
    for e in schedule["editions"]:
        if args.edition and e["edition"] != args.edition:
            continue
        if not e.get("ceremony") or (e.get("winnersComplete") and not args.force):
            continue
        if args.force and args.edition:
            return e
        start = pacific_to_utc(date.fromisoformat(e["ceremony"]), e.get("ceremonyTime", DEFAULT_CEREMONY_TIME))
        if start - WINNERS_LEAD <= now <= start + timedelta(days=60):
            return e
    return None


def cmd_winners(args) -> None:
    schedule = load_schedule()
    entry = winners_target(args, schedule)
    if args.check:
        set_output("run", "true" if entry else "false")
        log(f"Ceremony window open for edition {entry['edition']}." if entry else "No ceremony in progress.")
        return
    if not entry:
        log("No ceremony in progress; nothing to do.")
        return
    edition = entry["edition"]
    start = pacific_to_utc(date.fromisoformat(entry["ceremony"]), entry.get("ceremonyTime", DEFAULT_CEREMONY_TIME))
    minutes = args.max_minutes
    if args.now > start + timedelta(hours=12):
        minutes = min(minutes, 10)  # after ceremony night, follow-up runs are short checks
    deadline = time.monotonic() + minutes * 60
    log(f"Watching the {wiki_awards.ordinal(edition)} Academy Awards for winners (until all categories are decided) ...")

    published_sig = None
    last_seen_sig = None
    last_rev = None
    first = True
    while True:
        page = args.wiki.get(edition)
        rev = page[1] if page else None
        if page and rev != last_rev or first:
            parsed = wiki_awards.parse_nominees(page[0]) if page else {"categories": []}
            sig = json.dumps([[c["canonical"], sorted(n["film"] + "|" + n["who"] for n in c["nominees"] if n["winner"])]
                              for c in parsed["categories"]])
            last_rev = rev
        # Publish a change only once it has been seen on two consecutive polls, so a
        # brief vandal edit or mistaken bolding on Wikipedia doesn't go live.
        if sig == last_seen_sig and sig != published_sig:
            status = sync_edition(args, edition, page, refresh=first)
            published_sig = sig
            first = False
            log(f"Winners decided: {status['winners']}/{status['categories']}")
            if status["changed"]:
                publish(args, f"{wiki_awards.ordinal(edition)} Academy Awards: {status['winners']}/{status['categories']} winners")
            if status["winners"] == status["categories"] and status["problems"]:
                open_issue(args, f"Winners need attention for the {wiki_awards.ordinal(edition)} Academy Awards",
                           "Every category has a winner on Wikipedia, but:\n\n- " + "\n- ".join(status["problems"]))
            if status["complete_winners"]:
                entry["winnersComplete"] = iso(datetime.now(timezone.utc) if not args.fake_now else args.now)
                save_schedule(schedule)
                publish(args, f"{wiki_awards.ordinal(edition)} Academy Awards: all winners announced")
                set_output("complete", "true")
                log("All categories have winners. Done.")
                return
        last_seen_sig = sig
        if time.monotonic() >= deadline:
            log("Time limit reached; the next scheduled run will keep watching.")
            if args.now > start + timedelta(hours=12):
                open_issue(args, f"Winners incomplete for the {wiki_awards.ordinal(edition)} Academy Awards",
                           "The ceremony was over 12 hours ago but not every category has a published winner. "
                           "The hourly job keeps checking; see the latest **Awards: winners** run log for details.")
            return
        time.sleep(args.interval)


# ---------- CLI ----------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["dates", "nominations", "winners"])
    parser.add_argument("--publish", action="store_true", help="commit, push and redeploy changes")
    parser.add_argument("--check", action="store_true", help="only report (as a GitHub output) whether it's time to act")
    parser.add_argument("--force", action="store_true", help="ignore time windows (use with --edition)")
    parser.add_argument("--edition", type=int, help="only act on this ceremony number, e.g. 99")
    parser.add_argument("--now", help="pretend it's this UTC time (testing), e.g. 2027-01-21T14:00Z")
    parser.add_argument("--wikitext", help="read wikitext from this file, or numbered files in this directory (testing)")
    parser.add_argument("--interval", type=float, default=60, help="seconds between checks while watching winners")
    parser.add_argument("--max-minutes", type=float, default=345, help="stop watching after this long (GitHub's job limit is 6h)")
    args = parser.parse_args()

    load_dotenv()
    args.fake_now = bool(args.now)
    args.now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    args.wiki = Wiki(args.wikitext)
    {"dates": cmd_dates, "nominations": cmd_nominations, "winners": cmd_winners}[args.command](args)


if __name__ == "__main__":
    main()
