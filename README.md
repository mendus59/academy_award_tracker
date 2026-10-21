# Nominated: an Academy Award film tracker

Track every Academy Award–nominated film you've seen, rate and review them, and find something to watch with friends.

- **Every nominated film.** 5,264 films and 10,876 nominations from the 1st Academy Awards (1927/28) through the 98th (2025 films), including every film an actor was nominated for. Winners are labeled 🏆.
- **Browse and filter** by title or person (actor, director, songwriter), award category, genre, award year range, and winner or nominee. Pick any number of categories and genres: within each filter it's "any of", and filters combine with each other. When categories are selected, only wins in those categories are highlighted in gold. Sort by year, title, award category, genre, number of nominations or wins, runtime, or rating.
- **Two layouts.** Cards show details and award tags. Compact shows one line per film, grouped under a header for each award year when sorted by year.
- **Accounts** (Google or email/password). Mark films as seen, rate them 1–5 stars, and write short reviews of up to 500 characters.
- **Settings.** Each user can save a default search (e.g. Best Picture winners only, oldest first) and a default layout. These apply whenever they open the site, on any device.
- **Friends.** Add a friend by email. The request appears as a *Pending Request* you can cancel. If the email belongs to an account, that person gets a 🔔 notification and can **Accept** or **Cancel** it. The app never reveals whether an email has an account. Friends can see each other's seen films, ratings and reviews, and compare lists: films you've *both* seen, films *neither* of you has seen ("pick a film together"), and so on.

It's a static site (plain HTML/CSS/JS with no build step) hosted on **GitHub Pages**, with **Firebase** Authentication and Cloud Firestore for users and data.

## Project layout

```
.env.example            Template for the git-ignored .env (TMDB token)
index.html              App shell
css/styles.css          Styles (light and dark mode)
js/app.js               UI: browse, filters, movie details, friends, profiles
js/firebase.js          Firebase Auth + Firestore calls
js/firebase-config.js   ← your Firebase project settings go here
data/movies.json        Generated film and nomination data
scripts/build_movies.py Regenerates data/movies.json
scripts/fetch_posters.py Adds TMDB poster paths (cached in scripts/posters.json)
scripts/automation.py   Yearly jobs: dates, nominations, live winners (with wiki_awards.py, resolve_ids.py)
scripts/live/           Nominations/winners scraped by the automation
data/schedule.json      Upcoming dates + automation progress (also drives the banner)
firestore.rules         Firestore security rules
firebase.json           Firebase CLI config (points at firestore.rules)
.firebaserc             Firebase project id for the CLI
.github/workflows/      GitHub Actions: deploy site to Pages, deploy Firestore rules
```

## 1. Set up Firebase (about 10 minutes)

1. Go to <https://console.firebase.google.com>, click **Add project** and create one. Google Analytics is optional.
2. **Register a web app:** Project overview → the `</>` (Web) icon → give it a nickname → **Register app**. You don't need Firebase Hosting. Copy the `firebaseConfig` values it shows into [`js/firebase-config.js`](js/firebase-config.js).
   These values are safe to commit. They identify the project, and access is enforced by the security rules.
3. **Turn on sign-in methods:** Build → **Authentication** → Get started → Sign-in method:
   - Enable **Email/Password**.
   - Enable **Google** and pick a support email.
4. **Authorize your domain:** Authentication → Settings → **Authorized domains** → Add domain → `mendus59.github.io`.
   (`localhost` is already there for local testing.)
5. **Create the database:** Build → **Firestore Database** → Create database → choose a location → start in **production mode**.
6. **Publish the security rules:** in Firestore → **Rules**, replace the contents with [`firestore.rules`](firestore.rules) and click **Publish**.
   Or, with the Firebase CLI: `firebase deploy --only firestore:rules`, or automatically through GitHub Actions (see below).

The rules make sure that:
- only you can change your own seen list, ratings and reviews;
- only you and your accepted friends can read them;
- your settings are private to you;
- a friendship can only be created by the person accepting a pending friend request;
- friend requests are addressed to an email and can only be seen by the sender and by an account that has **verified** that email. Sending a request never reveals whether the email has an account, and there is no user search or directory;
- profiles (name and photo) can be read one at a time by id but not listed, and photos must be Google profile images.

Email/password users get a verification email at sign-up and must click it before sending or receiving friend requests; Google accounts are verified already. Firebase sends the email using its default template, which you can customise under **Authentication → Templates**.

## 2. Publish with GitHub Actions

Deployment is automated by two workflows in [`.github/workflows/`](.github/workflows):

| Workflow | Runs when | What it does |
|---|---|---|
| `deploy-pages.yml` | every push to `main`, or manually | Checks that `data/movies.json` is valid, copies the site files (`index.html`, `css/`, `js/`, `data/`) into an artifact, and publishes it to GitHub Pages |
| `deploy-firestore-rules.yml` | a push to `main` that changes `firestore.rules`, `firebase.json` or `.firebaserc`, or manually | Deploys the security rules to the Firebase project in `.firebaserc` |

### One-time setup

1. **Turn on Pages via Actions:** on GitHub go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.
2. Push to `main`. Watch the run in the **Actions** tab. When it finishes, the site is live at **https://mendus59.github.io/academy_award_tracker/**.
3. *(Optional)* **Deploy Firestore rules automatically.** Until this is set up, the rules workflow skips itself with a notice, and you publish rules by hand in the Firebase console.
   1. In the [Google Cloud console](https://console.cloud.google.com/iam-admin/serviceaccounts?project=academy-award-tracker), create a service account (e.g. `github-rules-deployer`). Give it the **Firebase Rules Admin** and **Service Usage Consumer** roles.
   2. Open the service account → **Keys → Add key → Create new key → JSON**, which downloads a file.
   3. On GitHub, go to **Settings → Secrets and variables → Actions → New repository secret**. Set Name to `FIREBASE_SERVICE_ACCOUNT` and Value to the entire contents of that JSON file. Then delete the downloaded file.
   4. Run **Actions → Deploy Firestore rules → Run workflow** once to confirm it works.

You can re-run either workflow at any time from the Actions tab (**Run workflow**).

## Run locally

The app uses ES modules and `fetch`, so it must be served over HTTP rather than opened as a file:

```bash
python -m http.server 8000
# open http://localhost:8000
```

Without a Firebase config you can still browse and filter every film. Account features show a notice.

## Updating the film data

`data/movies.json` is generated from two free sources:

- **Nominations:** [DLu/oscar_data](https://github.com/DLu/oscar_data), which includes an IMDb ID for every nominated film.
- **Genres and runtimes:** [IMDb non-commercial datasets](https://developer.imdb.com/non-commercial-datasets/) (`title.basics.tsv.gz`, about 230 MB, cached in `scripts/.cache/`).

After each Oscars ceremony, once oscar_data has been updated:

```bash
python scripts/build_movies.py --refresh
git add data/movies.json && git commit -m "Update Oscar data" && git push
```

The push triggers the Pages workflow, which publishes the new data.

Notes on the data:
- Scientific & Technical awards are left out because they honor technologies rather than films.
- When one nomination covered several films (common in the early years, e.g. Janet Gaynor's Best Actress win for *7th Heaven*, *Street Angel* and *Sunrise*), every one of those films is listed with that nomination.
- The "award year" is the film-eligibility year (the ceremony is held the following year).

## Yearly automation

Three GitHub Actions workflows keep the site current each awards season with no manual work. All three read and update [`data/schedule.json`](data/schedule.json), which holds each ceremony's dates and what has been captured so far. The site's top banner reads the same file.

| Workflow | When it runs | What it does |
|---|---|---|
| **Awards: check dates** (`awards-dates.yml`) | Daily from the day after the ceremony through May 31, then the 1st of each month | Reads the next ceremony's **nominations announcement** and **ceremony** dates from the "Key dates" table of its Wikipedia article and saves them. The Academy usually announces them in mid-to-late April, so daily checks catch them within a day; May 1 is covered. Monthly runs pick up any later date changes. If the dates still aren't known by June 1, it opens a GitHub issue. |
| **Awards: nominations** (`awards-nominations.yml`) | Hourly, Jan 10 – Feb 15; does nothing outside the window | From the announcement time (5:30 a.m. PT by default) it fetches every category and nominee: film, people, character or song, and country. It matches each film to IMDb (via Wikidata, then TMDB, then IMDb's own data) and runs the **full** data build, so new nominees get genres, runtime and posters like every other film, plus anything the build adds in future. It retries hourly for 24 hours until every category and film is captured. If something is still missing, it publishes what it has and opens an issue. |
| **Awards: winners** (`awards-winners.yml`) | Hourly, Feb – May; does nothing outside the window | From an hour before the ceremony (4 p.m. PT by default), one job checks Wikipedia every minute and redeploys the site each time new winners are confirmed. The site shows them within a few minutes. A winner is published only after it has appeared on two checks in a row, which guards against brief vandal edits. The job doesn't stop until every category has a winner; if GitHub's 6-hour job limit hits first, the next hourly run continues. It opens an issue if winners are still incomplete 12 hours after the ceremony. |
| **Keep scheduled workflows enabled** (`keepalive.yml`) | Monthly | GitHub switches off scheduled workflows after 60 quiet days; this keeps them on between seasons. |

**How live data fits in.** Scraped results are saved to `scripts/live/<ceremony>.json`. `build_movies.py` uses them for a ceremony that [DLu/oscar_data](https://github.com/DLu/oscar_data) doesn't have yet, or has fewer winners for. Once that curated dataset catches up, it takes over again. On nominations morning and ceremony night the site checks for new data every 90 seconds and updates in place. The banner switches to "Nominations announced today" or "Live now".

**One-time setup:**
1. Add your TMDB token as a repository secret: **Settings → Secrets and variables → Actions → New repository secret**, named `TMDB_READ_ACCESS_TOKEN`. It's used for posters and for matching films to IMDb.
2. Pages must deploy via Actions (see above). The jobs push data commits to `main` and then trigger the deploy. If you add branch protection to `main`, allow GitHub Actions to push.

**Manual runs and fixes:**
- Every workflow has **Run workflow** on the Actions tab. For nominations or winners you can enter a ceremony number and tick **force** to run immediately, outside the scheduled window.
- To correct dates or announcement times, edit `data/schedule.json`. Times are Pacific and optional: `"nominationsTime": "05:30"`, `"ceremonyTime": "16:00"`. The `nominationsAt` and `ceremonyAt` fields are calculated automatically.
- To re-run a step that already finished, clear `nominationsCaptured` or `winnersComplete` for that ceremony.
- Locally, `python scripts/automation.py dates|nominations|winners` writes files without committing. Add `--now 2027-01-21T14:00Z` to pretend it's another time, and `--force --edition 99` to skip the time checks.

## Movie posters (TMDB)

Cards and the film details dialog show posters from [TMDB](https://www.themoviedb.org/). Films without one get a lettered placeholder.

To fetch posters (needed once, then again for new films after each ceremony):

1. Create a free TMDB account. Under **Settings → API**, request an API key (choose "Personal"/non-commercial use).
2. Copy the **API Read Access Token** (the long one) into the `.env` file at the repo root. If it doesn't exist, copy `.env.example` to `.env` first:

   ```
   TMDB_READ_ACCESS_TOKEN=eyJ...
   ```

   `.env` is listed in `.gitignore`, so it stays on your machine. An environment variable with the same name takes priority over the file.
3. Run:

   ```bash
   python scripts/fetch_posters.py
   ```

   This looks up every film by IMDb ID (a few minutes for about 5,300 films) and saves the results to `scripts/posters.json`. It also writes the poster paths into `data/movies.json`.
4. Commit `scripts/posters.json` and `data/movies.json`.

Later runs only look up films that aren't in the cache. Add `--retry-missing` to re-check films that had no poster. `build_movies.py` re-applies cached posters automatically, so rebuilding the data doesn't need the token.

Never commit `.env`. Only poster paths are stored; images load directly from TMDB's image server. TMDB's terms require attribution, which is in the site footer ("This product uses the TMDB API but is not endorsed or certified by TMDB"). Their [logo guidelines](https://www.themoviedb.org/about/logos-attribution) also ask for one of the TMDB logos; download one there and add it next to the footer credit. TMDB is free for non-commercial use; a commercial app needs a commercial agreement with TMDB.

## Firestore data model

| Path | Contents | Who can read |
|---|---|---|
| `profiles/{uid}` | `displayName`, `nameLower`, `photoURL` | any signed-in user, one at a time by id (not listable) |
| `users/{uid}/movies/{imdbId}` | `seen`, `rating` (0–5), `review` (≤500 chars), `updatedAt` | the user and their friends |
| `users/{uid}/friends/{friendUid}` | `since` | the two friends |
| `users/{uid}/settings/prefs` | `filters` (default category, genre, result, years, status, sort), `layout` (`cards`/`compact`) | the user only |
| `friendRequests/{fromUid}_{toEmail}` | `from`, `fromEmail`, `toEmail`, `createdAt` | the sender, and an account with that verified email |

## Design and trademarks

The visual style (white and charcoal surfaces, gold accents, a burgundy top strip, uppercase letter-spaced type) is an original design built from general styling choices. It uses no logos, artwork, photography, page text or fonts from any other site.

- **Typeface:** [Jost](https://fonts.google.com/specimen/Jost), licensed under the SIL Open Font License.
- **Icons:** standard Unicode emoji only (🏆 ★ 👁 🎲). No statuette imagery is used.
- **Name:** "Nominated" deliberately avoids the Academy's marks.

"Oscar®," "Oscars®," "Academy Awards®" and the Oscar® statuette are registered trademarks and service marks of the Academy of Motion Picture Arts and Sciences. This project is not affiliated with, sponsored by or endorsed by the Academy. The marks are used only to describe the awards and their nominees, and the site footer carries this notice. If you rename or rebrand the app, keep the name and logo free of those marks.
