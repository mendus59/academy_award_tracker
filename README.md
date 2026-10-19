# 🏆 Oscar Tracker

Track every Academy Award–nominated film you've seen, rate and review them, and find something to watch with friends.

- **Every nominated film.** 5,264 films and 10,876 nominations from the 1st Academy Awards (1927/28) through the 98th (2025 films), including every film an actor was nominated for. Winners are labeled 🏆.
- **Browse and filter** by title or person (actor, director, songwriter), award category, genre, Oscar year range, and winner or nominee. Sort by year, title, award category, genre, number of nominations or wins, runtime, or rating.
- **Accounts** (Google or email/password). Mark films as seen, rate them 1–5 stars, and write short reviews of up to 500 characters.
- **Friends.** Send and accept friend requests, see friends' seen films, ratings and reviews, and compare lists: films you've *both* seen, films *neither* of you has seen ("pick a film together"), and so on.

It's a static site (plain HTML/CSS/JS with no build step) hosted on **GitHub Pages**, with **Firebase** Authentication and Cloud Firestore for users and data.

## Project layout

```
index.html              App shell
css/styles.css          Styles (light and dark mode)
js/app.js               UI: browse, filters, movie details, friends, profiles
js/firebase.js          Firebase Auth + Firestore calls
js/firebase-config.js   ← your Firebase project settings go here
data/movies.json        Generated film and nomination data
scripts/build_movies.py Regenerates data/movies.json
firestore.rules         Firestore security rules
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
   Or, with the Firebase CLI: `firebase deploy --only firestore:rules`.

The rules make sure that:
- only you can change your own seen list, ratings and reviews;
- only you and your accepted friends can read them;
- a friendship can only be created by the person accepting a pending friend request;
- emails can be looked up one at a time (to add a friend) but never listed.

## 2. Publish on GitHub Pages

1. Commit and push to `main` on `github.com/mendus59/academy_award_tracker`.
2. On GitHub: **Settings → Pages → Build and deployment**. Set Source to *Deploy from a branch*, Branch to `main` and folder to `/ (root)` → Save.
3. After a minute the site is live at **https://mendus59.github.io/academy_award_tracker/**.

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

Notes on the data:
- Scientific & Technical awards are left out because they honor technologies rather than films.
- When one nomination covered several films (common in the early years, e.g. Janet Gaynor's Best Actress win for *7th Heaven*, *Street Angel* and *Sunrise*), every one of those films is listed with that nomination.
- The "Oscar year" is the film-eligibility year (the ceremony is held the following year).

## Firestore data model

| Path | Contents | Who can read |
|---|---|---|
| `profiles/{uid}` | `displayName`, `nameLower`, `photoURL` | any signed-in user (for friend search) |
| `emailIndex/{email}` | `uid` | single lookups by signed-in users |
| `users/{uid}/movies/{imdbId}` | `seen`, `rating` (0–5), `review` (≤500 chars), `updatedAt` | the user and their friends |
| `users/{uid}/friends/{friendUid}` | `since` | the two friends |
| `friendRequests/{from}_{to}` | `from`, `to`, `createdAt` | sender and recipient |
