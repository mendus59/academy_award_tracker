# Nominated: an Academy Award film tracker

Track every Academy Award–nominated film you've seen, rate and review them, and find something to watch with friends.

- **Every nominated film.** 5,264 films and 10,876 nominations from the 1st Academy Awards (1927/28) through the 98th (2025 films), including every film an actor was nominated for. Winners are labeled 🏆.
- **Browse and filter** by title or person (actor, director, songwriter), award category, genre, award year range, and winner or nominee. Pick any number of categories and genres: within each filter it's "any of", and filters combine with each other. When categories are selected, only wins in those categories are highlighted in gold. Sort by year, title, award category, genre, number of nominations or wins, runtime, or rating.
- **Two layouts.** Cards show details and award tags. Compact shows one line per film, grouped under a header for each award year when sorted by year.
- **Accounts** (Google or email/password). Mark films as seen, rate them 1–5 stars, and write short reviews of up to 500 characters.
- **Settings.** Each user can save a default search (e.g. Best Picture winners only, oldest first) and a default layout. These apply whenever they open the site, on any device.
- **Friends.** Send and accept friend requests, see friends' seen films, ratings and reviews, and compare lists: films you've *both* seen, films *neither* of you has seen ("pick a film together"), and so on.

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
- emails can be looked up one at a time (to add a friend) but never listed.

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
| `profiles/{uid}` | `displayName`, `nameLower`, `photoURL` | any signed-in user (for friend search) |
| `emailIndex/{email}` | `uid` | single lookups by signed-in users |
| `users/{uid}/movies/{imdbId}` | `seen`, `rating` (0–5), `review` (≤500 chars), `updatedAt` | the user and their friends |
| `users/{uid}/friends/{friendUid}` | `since` | the two friends |
| `users/{uid}/settings/prefs` | `filters` (default category, genre, result, years, status, sort), `layout` (`cards`/`compact`) | the user only |
| `friendRequests/{from}_{to}` | `from`, `to`, `createdAt` | sender and recipient |

## Design and trademarks

The visual style (white and charcoal surfaces, gold accents, a burgundy top strip, uppercase letter-spaced type) is an original design built from general styling choices. It uses no logos, artwork, photography, page text or fonts from any other site.

- **Typeface:** [Jost](https://fonts.google.com/specimen/Jost), licensed under the SIL Open Font License.
- **Icons:** standard Unicode emoji only (🏆 ★ 👁 🎲). No statuette imagery is used.
- **Name:** "Nominated" deliberately avoids the Academy's marks.

"Oscar®," "Oscars®," "Academy Awards®" and the Oscar® statuette are registered trademarks and service marks of the Academy of Motion Picture Arts and Sciences. This project is not affiliated with, sponsored by or endorsed by the Academy. The marks are used only to describe the awards and their nominees, and the site footer carries this notice. If you rename or rebrand the app, keep the name and logo free of those marks.
