import * as fb from "./firebase.js";

const PAGE_SIZE = 60;

const DEFAULT_FILTERS = {
  q: "",
  category: "",
  genre: "",
  from: "",
  to: "",
  result: "all",
  status: "all",
  friend: "",
  overlap: "any",
  sort: "year-desc",
};

const state = {
  movies: [],
  byId: new Map(),
  categories: [],
  catIndex: new Map(),
  classes: [],
  genres: [],
  minYear: 1927,
  maxYear: new Date().getFullYear(),

  user: null,
  profile: null,
  mine: new Map(), // movieId -> { seen, rating, review, updatedAt }
  friends: [], // uids
  friendProfiles: new Map(),
  friendMovies: new Map(), // uid -> Map(movieId -> entry)
  incoming: [],
  outgoing: [],

  filters: { ...DEFAULT_FILTERS },
  shown: PAGE_SIZE,
};

const subs = { user: [], friendMovies: new Map() };

// ---------- Helpers ----------

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const normalize = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const ordinal = (n) => {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
};

const plural = (n, word, pluralWord = word + "s") => `${n.toLocaleString()} ${n === 1 ? word : pluralWord}`;

const CATEGORY_LABELS = {
  "BEST PICTURE": "Best Picture",
  DIRECTING: "Best Director",
  "ACTOR IN A LEADING ROLE": "Best Actor",
  "ACTRESS IN A LEADING ROLE": "Best Actress",
  "ACTOR IN A SUPPORTING ROLE": "Supporting Actor",
  "ACTRESS IN A SUPPORTING ROLE": "Supporting Actress",
  "WRITING (Original Screenplay)": "Original Screenplay",
  "WRITING (Adapted Screenplay)": "Adapted Screenplay",
  "WRITING (Original Story)": "Original Story",
  "ANIMATED FEATURE FILM": "Animated Feature",
  "INTERNATIONAL FEATURE FILM": "International Feature",
  "DOCUMENTARY (Feature)": "Documentary Feature",
  "DOCUMENTARY (Short Subject)": "Documentary Short",
  "MUSIC (Original Score)": "Original Score",
  "MUSIC (Original Song)": "Original Song",
  "SHORT FILM (Animated)": "Animated Short",
  "SHORT FILM (Live Action)": "Live Action Short",
  "ART DIRECTION": "Production Design",
  "ART DIRECTION (Black-and-White)": "Art Direction (Black-and-White)",
  "ART DIRECTION (Color)": "Art Direction (Color)",
  "SOUND MIXING": "Sound",
};
const SMALL_WORDS = new Set(["and", "of", "in", "a", "the", "or", "for"]);

function catLabel(cat) {
  if (CATEGORY_LABELS[cat]) return CATEGORY_LABELS[cat];
  let first = true;
  return cat.replace(/[A-Z][A-Z.&'-]+|[A-Z]\b/g, (word) => {
    const lower = word.toLowerCase();
    const out = !first && SMALL_WORDS.has(lower) ? lower : word[0] + lower.slice(1);
    first = false;
    return out;
  });
}

const entryOf = (map, id) => map?.get(id);
const hasSeen = (map, id) => Boolean(map?.get(id)?.seen);

function initials(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

function avatar(profile, cls = "") {
  if (profile?.photoURL) {
    return `<img class="avatar ${cls}" src="${esc(profile.photoURL)}" alt="" referrerpolicy="no-referrer">`;
  }
  return `<span class="avatar ${cls}" aria-hidden="true">${esc(initials(profile?.displayName))}</span>`;
}

function stars(rating) {
  if (!rating) return "";
  return `<span class="stars" aria-label="${rating} out of 5 stars">${"★".repeat(rating)}<span class="off">${"★".repeat(5 - rating)}</span></span>`;
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function friendName(uid) {
  return state.friendProfiles.get(uid)?.displayName || "Friend";
}

// ---------- Data ----------

async function loadMovies() {
  const res = await fetch("data/movies.json");
  if (!res.ok) throw new Error(`Couldn't load movie data (${res.status})`);
  const data = await res.json();

  state.classes = data.classes;
  state.categories = data.categories;
  data.categories.forEach((c, i) => state.catIndex.set(c.name, i));

  const genreCounts = new Map();
  for (const m of data.movies) {
    m.wins = m.n.filter((n) => n.w).length;
    m.cer = m.n[0].cer;
    const text = [m.t, ...m.n.flatMap((n) => [n.who, n.d])].filter(Boolean).join(" ");
    m._search = normalize(text);
    m.g.forEach((g) => genreCounts.set(g, (genreCounts.get(g) || 0) + 1));
    state.byId.set(m.id, m);
  }
  state.movies = data.movies;
  state.genres = [...genreCounts].sort((a, b) => a[0].localeCompare(b[0]));
  state.minYear = Math.min(...data.movies.map((m) => m.ay));
  state.maxYear = Math.max(...data.movies.map((m) => m.ay));
  $("#data-date").textContent = `Data updated ${data.generated}`;
}

// Nominations that match the active category filter (all of them if none).
function relevantNoms(m, category = state.filters.category) {
  if (!category) return m.n;
  if (category.startsWith("class:")) {
    const cls = category.slice(6);
    return m.n.filter((n) => state.categories[state.catIndex.get(n.c)].class === cls);
  }
  return m.n.filter((n) => n.c === category.slice(4));
}

function filteredMovies() {
  const f = state.filters;
  const q = normalize(f.q.trim());
  const from = f.from ? Number(f.from) : null;
  const to = f.to ? Number(f.to) : null;
  const friendMap = f.friend ? state.friendMovies.get(f.friend) || new Map() : null;

  const list = state.movies.filter((m) => {
    if (q && !m._search.includes(q)) return false;
    if (f.genre && !m.g.includes(f.genre)) return false;
    if (from && m.ay < from) return false;
    if (to && m.ay > to) return false;

    const noms = relevantNoms(m);
    if (!noms.length) return false;
    if (f.result === "won" && !noms.some((n) => n.w)) return false;
    if (f.result === "lost" && noms.some((n) => n.w)) return false;

    if (state.user) {
      const iSaw = hasSeen(state.mine, m.id);
      if (f.status === "seen" && !iSaw) return false;
      if (f.status === "unseen" && iSaw) return false;
      if (friendMap) {
        const theySaw = hasSeen(friendMap, m.id);
        if (f.overlap === "both" && !(iSaw && theySaw)) return false;
        if (f.overlap === "neither" && (iSaw || theySaw)) return false;
        if (f.overlap === "friend-only" && !(theySaw && !iSaw)) return false;
        if (f.overlap === "me-only" && !(iSaw && !theySaw)) return false;
      }
    }
    return true;
  });

  const byTitle = (a, b) => a.t.localeCompare(b.t);
  const byYearDesc = (a, b) => b.ay - a.ay || byTitle(a, b);
  const rating = (map, id) => entryOf(map, id)?.rating || 0;
  const topCat = (m) => Math.min(...relevantNoms(m).map((n) => state.catIndex.get(n.c) + (n.w ? 0 : 0.5)));

  const sorters = {
    "year-desc": byYearDesc,
    "year-asc": (a, b) => a.ay - b.ay || byTitle(a, b),
    title: byTitle,
    "noms-desc": (a, b) => relevantNoms(b).length - relevantNoms(a).length || byYearDesc(a, b),
    "wins-desc": (a, b) =>
      relevantNoms(b).filter((n) => n.w).length - relevantNoms(a).filter((n) => n.w).length || byYearDesc(a, b),
    category: (a, b) => topCat(a) - topCat(b) || byYearDesc(a, b),
    genre: (a, b) => (a.g[0] || "~").localeCompare(b.g[0] || "~") || byTitle(a, b),
    runtime: (a, b) => (a.rt || 9999) - (b.rt || 9999) || byTitle(a, b),
    "my-rating": (a, b) => rating(state.mine, b.id) - rating(state.mine, a.id) || byYearDesc(a, b),
    "friend-rating": (a, b) => rating(friendMap, b.id) - rating(friendMap, a.id) || byYearDesc(a, b),
  };
  return list.sort(sorters[f.sort] || byYearDesc);
}

// ---------- Router ----------

const routes = {
  browse: renderBrowse,
  friends: renderFriends,
  me: renderMe,
  user: renderUser,
};

function currentRoute() {
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  return { name: routes[name] ? name : "browse", arg };
}

function route() {
  const { name, arg } = currentRoute();
  document.querySelectorAll("#main-nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  routes[name](arg);
}

// Re-render whatever is on screen after data changes, without clobbering inputs.
function refresh() {
  renderAccount();
  const { name, arg } = currentRoute();
  if (name === "browse") {
    renderCompareBox();
    renderStatusFilter();
    renderList();
  } else if (name === "friends") {
    renderFriendSections();
  } else {
    routes[name](arg);
  }
}

// ---------- Header / account ----------

function renderAccount() {
  const el = $("#account");
  const badge = $("#request-badge");
  badge.hidden = !state.incoming.length;
  badge.textContent = state.incoming.length;

  if (!fb.isConfigured) {
    el.innerHTML = "";
    return;
  }
  if (!state.user) {
    el.innerHTML = `<button class="btn primary" data-action="sign-in">Sign in</button>`;
    return;
  }
  const name = state.profile?.displayName || state.user.displayName || "Me";
  el.innerHTML = `
    <a href="#/me" title="${esc(name)}">${avatar({ displayName: name, photoURL: state.profile?.photoURL })}</a>
    <button class="btn small" data-action="sign-out">Sign out</button>`;
}

document.addEventListener("click", (e) => {
  const action = e.target.closest("[data-action]")?.dataset.action;
  if (action === "sign-in") openAuth();
  if (action === "sign-out") fb.signOut().then(() => toast("Signed out"));
});

// ---------- Browse ----------

function renderBrowse() {
  const f = state.filters;
  const yearOpts = `min="${state.minYear}" max="${state.maxYear}"`;
  const classGroups = state.classes
    .map((cls) => {
      const cats = state.categories.filter((c) => c.class === cls.id);
      if (!cats.length) return "";
      return `<optgroup label="${esc(cls.label)}">
        <option value="class:${esc(cls.id)}">All ${esc(cls.label)} categories</option>
        ${cats.map((c) => `<option value="cat:${esc(c.name)}">${esc(catLabel(c.name))}</option>`).join("")}
      </optgroup>`;
    })
    .join("");

  view.innerHTML = `
    <div class="page-head">
      <div>
        <h1>Every Oscar-nominated film</h1>
        <p class="muted">${plural(state.movies.length, "film")} from ${state.minYear} to ${state.maxYear}. Winners are marked 🏆.</p>
      </div>
      <button class="btn" data-action="random">🎲 Pick a random film</button>
    </div>

    <form class="filters" id="filters" onsubmit="return false">
      <label class="field search">Search
        <input type="search" name="q" placeholder="Film title, actor, director, song…" value="${esc(f.q)}" autocomplete="off">
      </label>
      <label class="field">Award category
        <select name="category"><option value="">All categories</option>${classGroups}</select>
      </label>
      <label class="field">Genre
        <select name="genre"><option value="">All genres</option>
          ${state.genres.map(([g, n]) => `<option value="${esc(g)}">${esc(g)} (${n})</option>`).join("")}
        </select>
      </label>
      <label class="field">Result
        <select name="result">
          <option value="all">Winners &amp; nominees</option>
          <option value="won">🏆 Winners only</option>
          <option value="lost">Nominated, didn't win</option>
        </select>
      </label>
      <div class="field"><span>Oscar year</span>
        <div class="year-range">
          <input type="number" name="from" placeholder="${state.minYear}" ${yearOpts} value="${esc(f.from)}" aria-label="From year">
          <span>–</span>
          <input type="number" name="to" placeholder="${state.maxYear}" ${yearOpts} value="${esc(f.to)}" aria-label="To year">
        </div>
      </div>
      <label class="field" id="status-field">My status
        <select name="status">
          <option value="all">All films</option>
          <option value="seen">Seen it</option>
          <option value="unseen">Haven't seen</option>
        </select>
      </label>
      <label class="field">Sort by
        <select name="sort">
          <option value="year-desc">Year (newest first)</option>
          <option value="year-asc">Year (oldest first)</option>
          <option value="title">Title (A–Z)</option>
          <option value="category">Award category</option>
          <option value="genre">Genre</option>
          <option value="noms-desc">Most nominations</option>
          <option value="wins-desc">Most wins</option>
          <option value="runtime">Runtime (shortest)</option>
          <option value="my-rating" data-needs="user">My rating</option>
          <option value="friend-rating" data-needs="friend">Friend's rating</option>
        </select>
      </label>
      <div class="compare-box" id="compare-box"></div>
    </form>

    <div class="summary-bar" id="summary"></div>
    <div class="movie-list" id="movie-list"></div>
    <div class="load-more" id="load-more"></div>`;

  const form = $("#filters");
  for (const name of ["category", "genre", "result", "status", "sort"]) form.elements[name].value = f[name];
  renderCompareBox();
  renderStatusFilter();

  let debounce;
  form.addEventListener("input", (e) => {
    const { name, value } = e.target;
    if (!(name in state.filters)) return;
    state.filters[name] = value;
    state.shown = PAGE_SIZE;
    clearTimeout(debounce);
    debounce = setTimeout(renderList, name === "q" ? 150 : 0);
  });

  renderList();
}

function renderStatusFilter() {
  const field = $("#status-field");
  if (!field) return;
  field.hidden = !state.user;
  const sort = $("#filters").elements.sort;
  sort.querySelector('[data-needs="user"]').hidden = !state.user;
  sort.querySelector('[data-needs="friend"]').hidden = !state.filters.friend;
}

function renderCompareBox() {
  const box = $("#compare-box");
  if (!box) return;
  if (state.filters.friend && !state.friends.includes(state.filters.friend)) {
    state.filters.friend = "";
    state.filters.overlap = "any";
  }
  if (!state.user || !state.friends.length) {
    box.hidden = true;
    box.dataset.key = "";
    return;
  }
  const f = state.filters;
  // Skip re-rendering (which would close an open dropdown) if nothing changed.
  const key = JSON.stringify([state.friends.map(friendName), state.friends, f.friend, f.overlap]);
  if (box.dataset.key === key && !box.hidden) return;
  box.dataset.key = key;
  box.hidden = false;
  box.innerHTML = `
    <label class="field">Compare with a friend
      <select name="friend">
        <option value="">— No one —</option>
        ${state.friends.map((uid) => `<option value="${esc(uid)}">${esc(friendName(uid))}</option>`).join("")}
      </select>
    </label>
    <label class="field">Show films
      <select name="overlap" ${f.friend ? "" : "disabled"}>
        <option value="any">Everything</option>
        <option value="neither">Neither of us has seen (pick one together!)</option>
        <option value="both">We've both seen</option>
        <option value="friend-only">They've seen, I haven't</option>
        <option value="me-only">I've seen, they haven't</option>
      </select>
    </label>
    <p class="hint">Pick a friend to see their ratings on each film and find something new to watch together.</p>`;
  box.querySelector('[name="friend"]').value = f.friend;
  box.querySelector('[name="overlap"]').value = f.overlap;
  box.querySelector('[name="friend"]').addEventListener("change", (e) => {
    box.querySelector('[name="overlap"]').disabled = !e.target.value;
    if (!e.target.value) {
      state.filters.overlap = "any";
      box.querySelector('[name="overlap"]').value = "any";
      if (state.filters.sort === "friend-rating") {
        state.filters.sort = "year-desc";
        $("#filters").elements.sort.value = "year-desc";
      }
    }
    renderStatusFilter();
  });
}

let currentList = [];
let loadObserver;

function renderList() {
  const listEl = $("#movie-list");
  if (!listEl) return;
  currentList = filteredMovies();
  const visible = currentList.slice(0, state.shown);
  const winners = currentList.filter((m) => relevantNoms(m).some((n) => n.w)).length;
  const seenHere = state.user ? currentList.filter((m) => hasSeen(state.mine, m.id)).length : 0;
  const filtersActive = JSON.stringify(state.filters) !== JSON.stringify({ ...DEFAULT_FILTERS, sort: state.filters.sort });

  $("#summary").innerHTML = `
    <span><strong>${plural(currentList.length, "film")}</strong> · ${winners.toLocaleString()} with a win
      ${state.user ? ` · you've seen <strong>${seenHere.toLocaleString()}</strong>` : ""}</span>
    ${filtersActive ? `<button class="btn small" data-action="reset-filters">Clear filters</button>` : ""}`;

  listEl.innerHTML = visible.length
    ? visible.map(movieCard).join("")
    : `<p class="empty">No films match those filters.</p>`;

  const more = $("#load-more");
  const remaining = currentList.length - visible.length;
  more.innerHTML = remaining > 0 ? `<button class="btn" data-action="more">Show more (${remaining.toLocaleString()} left)</button>` : "";
  loadObserver?.disconnect();
  if (remaining > 0 && "IntersectionObserver" in window) {
    loadObserver = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        loadObserver.disconnect();
        state.shown += PAGE_SIZE;
        renderList();
      }
    }, { rootMargin: "400px" });
    loadObserver.observe(more);
  }
}

function movieCard(m) {
  const noms = relevantNoms(m);
  const wins = noms.filter((n) => n.w).length;
  const mine = entryOf(state.mine, m.id);
  const seen = Boolean(mine?.seen);
  const filteringCat = Boolean(state.filters.category);

  // One chip per category, winners first.
  const chipMap = new Map();
  for (const n of m.n) {
    const chip = chipMap.get(n.c) || { cat: n.c, count: 0, won: false, match: false };
    chip.count++;
    chip.won ||= Boolean(n.w);
    chip.match ||= filteringCat && noms.includes(n);
    chipMap.set(n.c, chip);
  }
  const chipList = [...chipMap.values()].sort(
    (a, b) => b.match - a.match || b.won - a.won || state.catIndex.get(a.cat) - state.catIndex.get(b.cat),
  );
  const MAX_CHIPS = 5;
  const chips = chipList
    .slice(0, MAX_CHIPS)
    .map(
      (c) =>
        `<span class="chip ${c.won ? "win" : ""} ${c.match ? "match" : ""}">${c.won ? "🏆 " : ""}${esc(catLabel(c.cat))}${c.count > 1 ? ` ×${c.count}` : ""}</span>`,
    )
    .join("");
  const extra = chipList.length > MAX_CHIPS ? `<span class="chip more">+${chipList.length - MAX_CHIPS} more</span>` : "";

  const totalWins = m.wins;
  const winBadge = totalWins ? `<span class="win-badge">🏆 ${totalWins === 1 ? "Winner" : `${totalWins} wins`}</span>` : "";
  const nomBadge = `<span class="nom-badge">${plural(m.n.length, "nomination")}</span>`;
  const meta = [m.y, m.g.join(", "), m.rt ? `${m.rt} min` : "", `${ordinal(m.cer)} Oscars`].filter(Boolean).join(" · ");

  let side = "";
  if (state.user && mine?.rating) side += `<span>${stars(mine.rating)}</span>`;
  if (state.filters.friend) {
    const theirs = entryOf(state.friendMovies.get(state.filters.friend), m.id);
    const name = esc(friendName(state.filters.friend));
    side += theirs?.seen
      ? `<span class="friend-mark seen">✓ ${name}${theirs.rating ? " " + stars(theirs.rating) : ""}</span>`
      : `<span class="friend-mark">${name} hasn't seen</span>`;
  }

  return `
    <article class="movie-card ${seen ? "seen" : ""} ${wins ? "has-win" : ""}" data-id="${m.id}" tabindex="0">
      <button class="seen-toggle ${seen ? "on" : ""}" data-action="toggle-seen" aria-pressed="${seen}"
        title="${seen ? "Seen — click to unmark" : "Mark as seen"}">${seen ? "✓" : "👁"}</button>
      <div>
        <h3 class="movie-title">${esc(m.t)} ${winBadge}${nomBadge}</h3>
        <div class="movie-meta">${esc(meta)}</div>
        <div class="chips">${chips}${extra}</div>
      </div>
      <div class="card-side">${side}</div>
    </article>`;
}

view.addEventListener("click", (e) => {
  const actionEl = e.target.closest("[data-action]");
  const action = actionEl?.dataset.action;
  const card = e.target.closest(".movie-card");

  if (action === "toggle-seen" && card) {
    e.stopPropagation();
    toggleSeen(card.dataset.id);
  } else if (action === "more") {
    state.shown += PAGE_SIZE;
    renderList();
  } else if (action === "reset-filters") {
    state.filters = { ...DEFAULT_FILTERS, sort: state.filters.sort };
    state.shown = PAGE_SIZE;
    renderBrowse();
  } else if (action === "random") {
    const pool = filteredMovies();
    if (pool.length) openMovie(pool[Math.floor(Math.random() * pool.length)].id);
    else toast("No films match your filters");
  } else if (card) {
    openMovie(card.dataset.id);
  } else if (e.target.closest("[data-movie]")) {
    openMovie(e.target.closest("[data-movie]").dataset.movie);
  }
});

view.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.classList.contains("movie-card")) openMovie(e.target.dataset.id);
});

function requireUser() {
  if (state.user) return true;
  if (!fb.isConfigured) toast("Accounts aren't set up yet — see the README to connect Firebase");
  else openAuth("Sign in to track the films you've seen.");
  return false;
}

async function saveEntry(movieId, entry) {
  try {
    await fb.saveMovieEntry(state.user.uid, movieId, entry);
    return true;
  } catch (err) {
    console.error(err);
    toast("Couldn't save — check your connection and try again");
    return false;
  }
}

async function toggleSeen(movieId) {
  if (!requireUser()) return;
  const current = entryOf(state.mine, movieId) || { seen: false, rating: 0, review: "" };
  if (current.seen && (current.rating || current.review)) {
    if (!confirm("Unmark this film as seen? Your rating and review will be removed.")) return;
    return saveEntry(movieId, { seen: false, rating: 0, review: "" });
  }
  const ok = await saveEntry(movieId, { ...current, seen: !current.seen });
  if (ok && !current.seen) toast(`Marked “${state.byId.get(movieId).t}” as seen`);
}

// ---------- Movie details modal ----------

const modal = $("#modal");
const modalBody = $("#modal-body");

function showModal(html) {
  modalBody.innerHTML = html;
  if (!modal.open) modal.showModal();
}

modal.addEventListener("click", (e) => {
  if (e.target === modal || e.target.closest("[data-close]")) modal.close();
});

function openMovie(id) {
  const m = state.byId.get(id);
  if (!m) return;

  // Group nominations by ceremony.
  let rows = "";
  let lastCer = null;
  for (const n of m.n) {
    if (n.cer !== lastCer) {
      rows += `<tr class="ceremony-row"><td colspan="3">${ordinal(n.cer)} Academy Awards · ${esc(n.ay)} films · held ${1928 + n.cer}</td></tr>`;
      lastCer = n.cer;
    }
    const people = [n.who, n.d ? (n.c.startsWith("MUSIC") ? `“${n.d}”` : `as ${n.d}`) : ""].filter(Boolean);
    rows += `
      <tr class="${n.w ? "win" : ""}">
        <td class="cat">${esc(catLabel(n.as || n.c))}</td>
        <td>${people.map(esc).join("<br>")}</td>
        <td class="result">${n.w ? `<span class="result-won">🏆 WON</span>` : `<span class="result-nom">Nominated</span>`}</td>
      </tr>`;
  }

  const friendTakes = state.friends
    .map((uid) => ({ uid, entry: entryOf(state.friendMovies.get(uid), id) }))
    .filter((x) => x.entry?.seen);

  const meta = [m.y, m.g.join(", "), m.rt ? `${m.rt} min` : ""].filter(Boolean).join(" · ");

  showModal(`
    <div class="modal-inner">
      <div class="modal-head">
        <div>
          <h2>${esc(m.t)}</h2>
          <div class="muted">${esc(meta)}</div>
          <div style="margin-top:8px">
            ${m.wins ? `<span class="win-badge" style="margin-left:0">🏆 ${m.wins === 1 ? "1 win" : `${m.wins} wins`}</span>` : ""}
            <span class="nom-badge" ${m.wins ? "" : 'style="margin-left:0"'}>${plural(m.n.length, "nomination")}</span>
            <a href="https://www.imdb.com/title/${m.id}/" target="_blank" rel="noopener" style="margin-left:8px;font-size:.85rem">IMDb ↗</a>
          </div>
        </div>
        <button class="close-btn" data-close aria-label="Close">✕</button>
      </div>

      <section id="my-entry"></section>

      ${state.user && state.friends.length ? `
      <section>
        <h3 class="section-title">Friends who've seen it</h3>
        ${friendTakes.length ? friendTakes.map(({ uid, entry }) => `
          <div class="friend-review">
            ${avatar(state.friendProfiles.get(uid))}
            <div>
              <strong>${esc(friendName(uid))}</strong> ${stars(entry.rating)}
              ${entry.review ? `<p>${esc(entry.review)}</p>` : ""}
            </div>
          </div>`).join("") : `<p class="muted" style="margin:0">None of your friends have marked this one yet.</p>`}
      </section>` : ""}

      <section>
        <h3 class="section-title">Academy Award nominations</h3>
        <table class="nom-table"><tbody>${rows}</tbody></table>
      </section>
    </div>`);

  renderMyEntry(m);
}

function renderMyEntry(m) {
  const box = $("#my-entry", modalBody);
  if (!box) return;
  if (!state.user) {
    box.innerHTML = fb.isConfigured
      ? `<div class="my-entry"><div class="row between"><span>Sign in to mark this film as seen, rate it and write a review.</span>
          <button class="btn primary" data-action="sign-in">Sign in</button></div></div>`
      : "";
    return;
  }
  const entry = { seen: false, rating: 0, review: "", ...entryOf(state.mine, m.id) };

  box.innerHTML = `
    <div class="my-entry">
      <div class="row between">
        <button class="btn ${entry.seen ? "primary" : ""}" data-entry="seen">${entry.seen ? "✓ I've seen this" : "👁 Mark as seen"}</button>
        <div class="star-input" role="radiogroup" aria-label="Your rating">
          ${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-entry="rate" data-value="${n}" class="${n <= entry.rating ? "on" : ""}"
              role="radio" aria-checked="${n === entry.rating}" aria-label="${n} star${n > 1 ? "s" : ""}">★</button>`).join("")}
        </div>
      </div>
      <div>
        <textarea id="review-text" maxlength="500" placeholder="Write a short review (optional)…">${esc(entry.review)}</textarea>
        <div class="row between">
          <span class="char-count" id="char-count">${entry.review.length}/500</span>
          <button class="btn small primary" data-entry="save-review">Save review</button>
        </div>
      </div>
    </div>`;

  const textarea = $("#review-text", box);
  textarea.addEventListener("input", () => ($("#char-count", box).textContent = `${textarea.value.length}/500`));

  box.onclick = async (e) => {
    const btn = e.target.closest("[data-entry]");
    if (!btn) return;
    const latest = { seen: false, rating: 0, review: "", ...entryOf(state.mine, m.id) };
    let next;
    if (btn.dataset.entry === "seen") {
      if (latest.seen && (latest.rating || latest.review)) {
        if (!confirm("Unmark this film as seen? Your rating and review will be removed.")) return;
        next = { seen: false, rating: 0, review: "" };
      } else {
        next = { ...latest, seen: !latest.seen };
      }
    } else if (btn.dataset.entry === "rate") {
      const value = Number(btn.dataset.value);
      next = { ...latest, seen: true, rating: latest.rating === value ? 0 : value };
    } else {
      next = { ...latest, seen: true, review: textarea.value };
    }
    if (await saveEntry(m.id, next)) {
      if (btn.dataset.entry === "save-review") toast(next.review.trim() ? "Review saved" : "Review removed");
      const draft = textarea.value;
      renderMyEntry(m);
      if (btn.dataset.entry !== "save-review") {
        const t = $("#review-text", box);
        t.value = draft;
        $("#char-count", box).textContent = `${draft.length}/500`;
      }
    }
  };
}

// ---------- Auth modal ----------

const AUTH_ERRORS = {
  "auth/invalid-credential": "That email and password don't match an account.",
  "auth/wrong-password": "That email and password don't match an account.",
  "auth/user-not-found": "That email and password don't match an account.",
  "auth/email-already-in-use": "There's already an account with that email. Try signing in.",
  "auth/weak-password": "Please use a password with at least 6 characters.",
  "auth/invalid-email": "That doesn't look like a valid email address.",
  "auth/popup-closed-by-user": "The sign-in window was closed before finishing.",
  "auth/too-many-requests": "Too many attempts. Please wait a moment and try again.",
  "auth/unauthorized-domain": "This site's domain isn't authorized in Firebase Authentication settings.",
};
const authError = (err) => AUTH_ERRORS[err.code] || err.message || "Something went wrong.";

function openAuth(reason = "", mode = "signin") {
  if (!fb.isConfigured) return;
  const signup = mode === "signup";
  showModal(`
    <div class="modal-inner" style="max-width:420px;margin:0 auto">
      <div class="modal-head">
        <h2>${signup ? "Create your account" : "Sign in"}</h2>
        <button class="close-btn" data-close aria-label="Close">✕</button>
      </div>
      ${reason ? `<p class="muted" style="margin:0">${esc(reason)}</p>` : ""}
      <button class="btn" id="google-btn" style="justify-content:center">
        <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>
        Continue with Google
      </button>
      <div class="divider">or use email</div>
      <form class="auth-form" id="auth-form">
        ${signup ? `<label class="field">Display name (friends will see this)<input type="text" name="name" required maxlength="40" autocomplete="nickname"></label>` : ""}
        <label class="field">Email<input type="email" name="email" required autocomplete="email"></label>
        <label class="field">Password<input type="password" name="password" required minlength="6" autocomplete="${signup ? "new-password" : "current-password"}"></label>
        <p class="error" id="auth-error" hidden></p>
        <button class="btn primary" type="submit" style="justify-content:center">${signup ? "Create account" : "Sign in"}</button>
      </form>
      <div class="row between" style="font-size:.88rem">
        ${signup
          ? `<span>Already have an account? <button class="btn link" id="switch-mode">Sign in</button></span>`
          : `<span>New here? <button class="btn link" id="switch-mode">Create an account</button></span>
             <button class="btn link" id="forgot">Forgot password?</button>`}
      </div>
    </div>`);

  const form = $("#auth-form", modalBody);
  const errorEl = $("#auth-error", modalBody);
  const showError = (err) => {
    errorEl.textContent = typeof err === "string" ? err : authError(err);
    errorEl.hidden = false;
  };

  $("#google-btn", modalBody).onclick = () =>
    fb.signInGoogle().then(() => modal.close()).catch(showError);
  $("#switch-mode", modalBody).onclick = () => openAuth(reason, signup ? "signin" : "signup");
  const forgot = $("#forgot", modalBody);
  if (forgot)
    forgot.onclick = () => {
      const email = form.elements.email.value.trim();
      if (!email) return showError("Enter your email above, then click “Forgot password?” again.");
      fb.resetPassword(email)
        .then(() => toast("Password reset email sent"))
        .catch(showError);
    };

  form.onsubmit = async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    const btn = form.querySelector('[type="submit"]');
    btn.disabled = true;
    const { email, password } = form.elements;
    try {
      if (signup) await fb.signUpEmail(form.elements.name.value.trim(), email.value.trim(), password.value);
      else await fb.signInEmail(email.value.trim(), password.value);
      modal.close();
    } catch (err) {
      showError(err);
    } finally {
      btn.disabled = false;
    }
  };
}

// ---------- Friends ----------

function renderFriends() {
  if (!state.user) {
    view.innerHTML = signedOutPage("Friends", "Sign in to add friends, see what they've watched, and find a film to watch together.");
    return;
  }
  view.innerHTML = `
    <div class="page-head"><div><h1>Friends</h1>
      <p class="muted">Friends can see which films you've seen, plus your ratings and reviews.</p></div></div>
    <div class="grid-2">
      <section class="panel">
        <h2>Add a friend</h2>
        <form class="search-row" id="friend-search">
          <input type="search" name="q" placeholder="Display name or exact email" autocomplete="off" required>
          <button class="btn primary">Search</button>
        </form>
        <div id="search-results"></div>
      </section>
      <section class="panel" id="requests"></section>
    </div>
    <section class="panel" id="friend-list"></section>`;

  $("#friend-search").onsubmit = async (e) => {
    e.preventDefault();
    const results = $("#search-results");
    results.innerHTML = `<p class="muted">Searching…</p>`;
    try {
      const people = await fb.findUsers(e.target.elements.q.value, state.user.uid);
      renderSearchResults(people);
    } catch (err) {
      console.error(err);
      results.innerHTML = `<p class="error">Search failed. Please try again.</p>`;
    }
  };
  renderFriendSections();
}

let lastSearch = [];
function renderSearchResults(people = lastSearch) {
  lastSearch = people;
  const el = $("#search-results");
  if (!el) return;
  if (!people.length) {
    el.innerHTML = `<p class="muted">No one found. Names match from the start (e.g. “Sam” finds “Samantha”); emails must match exactly.</p>`;
    return;
  }
  el.innerHTML = people
    .map((p) => {
      let action;
      if (state.friends.includes(p.id)) action = `<span class="muted">Friends ✓</span>`;
      else if (state.outgoing.some((r) => r.to === p.id)) action = `<span class="muted">Request sent</span>`;
      else if (state.incoming.some((r) => r.from === p.id))
        action = `<button class="btn small primary" data-friend-action="accept" data-uid="${esc(p.id)}">Accept request</button>`;
      else action = `<button class="btn small primary" data-friend-action="add" data-uid="${esc(p.id)}">Add friend</button>`;
      return `<div class="person">${avatar(p)}<div class="info"><div class="name">${esc(p.displayName)}</div></div><div class="actions">${action}</div></div>`;
    })
    .join("");
}

const profileCache = new Map();
async function profileFor(uid) {
  if (state.friendProfiles.has(uid)) return state.friendProfiles.get(uid);
  if (!profileCache.has(uid)) profileCache.set(uid, fb.getProfile(uid).catch(() => ({ id: uid, displayName: "Unknown user" })));
  return profileCache.get(uid);
}

async function renderFriendSections() {
  if (!$("#requests")) return;
  renderSearchResults();

  const people = async (list, key) => Promise.all(list.map(async (r) => ({ req: r, profile: await profileFor(r[key]) })));
  const [incoming, outgoing] = await Promise.all([people(state.incoming, "from"), people(state.outgoing, "to")]);
  const reqEl = $("#requests");
  if (!reqEl) return;
  reqEl.innerHTML = `
    <h2>Friend requests</h2>
    ${incoming.length || outgoing.length ? "" : `<p class="muted">No pending requests.</p>`}
    ${incoming.map(({ req, profile }) => `
      <div class="person">${avatar(profile)}
        <div class="info"><div class="name">${esc(profile.displayName)}</div><div class="muted">wants to be friends</div></div>
        <div class="actions">
          <button class="btn small primary" data-friend-action="accept" data-uid="${esc(req.from)}">Accept</button>
          <button class="btn small" data-friend-action="decline" data-uid="${esc(req.from)}">Decline</button>
        </div>
      </div>`).join("")}
    ${outgoing.map(({ req, profile }) => `
      <div class="person">${avatar(profile)}
        <div class="info"><div class="name">${esc(profile.displayName)}</div><div class="muted">request sent</div></div>
        <div class="actions"><button class="btn small" data-friend-action="cancel" data-uid="${esc(req.to)}">Cancel</button></div>
      </div>`).join("")}`;

  const listEl = $("#friend-list");
  listEl.innerHTML = `
    <h2>Your friends (${state.friends.length})</h2>
    ${state.friends.length ? "" : `<p class="muted">No friends yet. Search for someone above to send a request.</p>`}
    ${state.friends.map((uid) => {
      const theirs = state.friendMovies.get(uid);
      const seenCount = theirs ? [...theirs.values()].filter((e) => e.seen).length : 0;
      const both = theirs ? [...theirs].filter(([id, e]) => e.seen && hasSeen(state.mine, id)).length : 0;
      return `
      <div class="person">${avatar(state.friendProfiles.get(uid))}
        <div class="info">
          <div class="name">${esc(friendName(uid))}</div>
          <div class="muted">${plural(seenCount, "film")} seen · ${both.toLocaleString()} in common with you</div>
        </div>
        <div class="actions">
          <a class="btn small" href="#/user/${esc(uid)}">View</a>
          <button class="btn small primary" data-friend-action="pick" data-uid="${esc(uid)}">Pick a film together</button>
          <button class="btn small danger" data-friend-action="remove" data-uid="${esc(uid)}">Remove</button>
        </div>
      </div>`;
    }).join("")}`;
}

view.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-friend-action]");
  if (!btn || !state.user) return;
  const uid = btn.dataset.uid;
  const me = state.user.uid;
  btn.disabled = true;
  try {
    switch (btn.dataset.friendAction) {
      case "add":
        await fb.sendFriendRequest(me, uid);
        toast("Friend request sent");
        break;
      case "accept":
        await fb.acceptFriendRequest({ from: uid, to: me });
        toast("You're now friends!");
        break;
      case "decline":
        await fb.deleteFriendRequest({ from: uid, to: me });
        break;
      case "cancel":
        await fb.deleteFriendRequest({ from: me, to: uid });
        break;
      case "remove":
        if (!confirm(`Remove ${friendName(uid)} from your friends?`)) break;
        await fb.removeFriend(me, uid);
        toast("Friend removed");
        break;
      case "pick":
        pickTogether(uid);
        return;
    }
  } catch (err) {
    console.error(err);
    toast("That didn't work — please try again");
  } finally {
    btn.disabled = false;
  }
});

function pickTogether(uid) {
  state.filters = { ...DEFAULT_FILTERS, friend: uid, overlap: "neither", sort: "year-desc" };
  state.shown = PAGE_SIZE;
  location.hash = "#/browse";
}

// ---------- My movies & friend profiles ----------

function signedOutPage(title, message) {
  if (!fb.isConfigured) {
    return `<div class="page-head"><h1>${esc(title)}</h1></div>
      <p class="empty">Accounts aren't enabled yet. Connect Firebase (see README) to use this page.</p>`;
  }
  return `<div class="page-head"><h1>${esc(title)}</h1></div>
    <div class="empty"><p>${esc(message)}</p><button class="btn primary" data-action="sign-in">Sign in</button></div>`;
}

function statsHtml(map) {
  const seen = [...map].filter(([, e]) => e.seen).map(([id]) => state.byId.get(id)).filter(Boolean);
  const isBP = (n) => n.c === "BEST PICTURE";
  const bpWinners = state.movies.filter((m) => m.n.some((n) => isBP(n) && n.w));
  const bpSeen = seen.filter((m) => m.n.some((n) => isBP(n) && n.w)).length;
  const winners = state.movies.filter((m) => m.wins).length;
  const winnersSeen = seen.filter((m) => m.wins).length;
  const ratings = [...map.values()].filter((e) => e.seen && e.rating).map((e) => e.rating);
  const avg = ratings.length ? (ratings.reduce((a, b) => a + b, 0) / ratings.length).toFixed(1) : "–";
  const pct = (a, b) => `${Math.round((a / b) * 100)}%`;
  const stat = (value, label, a, b) => `
    <div class="stat"><div class="value">${value}</div><div class="label">${label}</div>
      ${b ? `<div class="progress"><span style="width:${pct(a, b)}"></span></div>` : ""}</div>`;
  return `<div class="stat-grid">
    ${stat(seen.length.toLocaleString(), `of ${state.movies.length.toLocaleString()} nominated films seen`, seen.length, state.movies.length)}
    ${stat(winnersSeen.toLocaleString(), `of ${winners.toLocaleString()} Oscar-winning films`, winnersSeen, winners)}
    ${stat(bpSeen, `of ${bpWinners.length} Best Picture winners`, bpSeen, bpWinners.length)}
    ${stat(avg, `average rating (${plural(ratings.length, "rating")})`)}
  </div>`;
}

function reviewListHtml(map, emptyText) {
  const items = [...map]
    .filter(([id, e]) => e.seen && state.byId.has(id))
    .sort((a, b) => (b[1].updatedAt?.toMillis?.() || 0) - (a[1].updatedAt?.toMillis?.() || 0));
  if (!items.length) return `<p class="muted">${emptyText}</p>`;
  return items
    .map(([id, e]) => {
      const m = state.byId.get(id);
      return `<div class="review-item" data-movie="${esc(id)}">
        <div><strong>${esc(m.t)}</strong> <span class="muted">(${m.y})</span>
          ${m.wins ? `<span class="win-badge">🏆 ${m.wins === 1 ? "Winner" : `${m.wins} wins`}</span>` : ""} ${stars(e.rating)}</div>
        ${e.review ? `<p>${esc(e.review)}</p>` : ""}
      </div>`;
    })
    .join("");
}

function renderMe() {
  if (!state.user) {
    view.innerHTML = signedOutPage("My Movies", "Sign in to see your stats, ratings and reviews.");
    return;
  }
  const name = state.profile?.displayName || state.user.displayName || "Me";
  view.innerHTML = `
    <div class="profile-head">
      ${avatar({ displayName: name, photoURL: state.profile?.photoURL }, "lg")}
      <div class="grow"><h1 style="margin:0">${esc(name)}</h1><div class="muted">${esc(state.user.email || "")}</div></div>
      <button class="btn" id="edit-name">Change display name</button>
    </div>
    ${statsHtml(state.mine)}
    <section class="panel">
      <h2>Films I've seen</h2>
      ${reviewListHtml(state.mine, `You haven't marked any films yet. <a href="#/browse">Start browsing</a> and tap 👁 on films you've seen.`)}
    </section>`;

  $("#edit-name").onclick = async () => {
    const next = prompt("Display name (friends will see this):", name)?.trim();
    if (!next || next === name) return;
    try {
      await fb.saveProfile(state.user.uid, { displayName: next, photoURL: state.profile?.photoURL });
      state.profile = { ...state.profile, displayName: next.slice(0, 40) };
      toast("Display name updated");
      refresh();
    } catch (err) {
      console.error(err);
      toast("Couldn't update your name");
    }
  };
}

function renderUser(uid) {
  if (!state.user) {
    view.innerHTML = signedOutPage("Friend", "Sign in to see your friends' films.");
    return;
  }
  if (uid === state.user.uid) {
    location.hash = "#/me";
    return;
  }
  if (!state.friends.includes(uid)) {
    view.innerHTML = `<div class="page-head"><h1>Not friends yet</h1></div>
      <p class="empty">You can only see a person's films once you're friends. <a href="#/friends">Go to Friends</a></p>`;
    return;
  }
  const profile = state.friendProfiles.get(uid);
  const theirs = state.friendMovies.get(uid) || new Map();
  view.innerHTML = `
    <div class="profile-head">
      ${avatar(profile, "lg")}
      <div class="grow"><h1 style="margin:0">${esc(friendName(uid))}</h1><div class="muted">Your friend</div></div>
      <button class="btn primary" data-friend-action="pick" data-uid="${esc(uid)}">🍿 Pick a film to watch together</button>
    </div>
    ${statsHtml(theirs)}
    <section class="panel">
      <h2>Films ${esc(friendName(uid))} has seen</h2>
      ${reviewListHtml(theirs, "They haven't marked any films yet.")}
    </section>`;
}

// ---------- Auth state & live data ----------

function stopUserSubs() {
  subs.user.forEach((unsub) => unsub());
  subs.user = [];
  subs.friendMovies.forEach((unsub) => unsub());
  subs.friendMovies.clear();
}

function syncFriendSubscriptions(uids) {
  for (const [uid, unsub] of subs.friendMovies) {
    if (!uids.includes(uid)) {
      unsub();
      subs.friendMovies.delete(uid);
      state.friendMovies.delete(uid);
      state.friendProfiles.delete(uid);
    }
  }
  for (const uid of uids) {
    if (subs.friendMovies.has(uid)) continue;
    profileFor(uid).then((p) => {
      if (!state.friends.includes(uid)) return;
      state.friendProfiles.set(uid, p);
      refresh();
    });
    subs.friendMovies.set(
      uid,
      fb.watchUserMovies(
        uid,
        (map) => {
          state.friendMovies.set(uid, map);
          refresh();
        },
        (err) => {
          // Usually a permissions race right after becoming friends: retry shortly.
          console.warn("Friend data unavailable", uid, err.code);
          subs.friendMovies.delete(uid);
          setTimeout(() => state.friends.includes(uid) && syncFriendSubscriptions(state.friends), 3000);
        },
      ),
    );
  }
}

function onUser(user) {
  stopUserSubs();
  state.user = user;
  state.mine = new Map();
  state.friends = [];
  state.friendMovies.clear();
  state.friendProfiles.clear();
  state.incoming = [];
  state.outgoing = [];
  state.profile = null;
  if (state.filters.friend) state.filters = { ...state.filters, friend: "", overlap: "any" };
  if (!user && ["my-rating", "friend-rating"].includes(state.filters.sort)) state.filters.sort = "year-desc";

  if (user) {
    fb.getProfile(user.uid).then((p) => {
      state.profile = p;
      refresh();
    });
    subs.user.push(
      fb.watchUserMovies(user.uid, (map) => {
        state.mine = map;
        refresh();
      }, (err) => console.error(err)),
      fb.watchFriends(user.uid, (uids) => {
        state.friends = uids;
        syncFriendSubscriptions(uids);
        refresh();
      }),
      fb.watchFriendRequests(user.uid, "to", (list) => {
        state.incoming = list;
        refresh();
      }),
      fb.watchFriendRequests(user.uid, "from", (list) => {
        state.outgoing = list;
        refresh();
      }),
    );
  }
  if (modal.open) modal.close();
  route();
  renderAccount();
}

// ---------- Boot ----------

async function main() {
  $("#config-notice").hidden = fb.isConfigured;
  try {
    await loadMovies();
  } catch (err) {
    view.innerHTML = `<p class="empty">${esc(err.message)}</p>`;
    return;
  }
  window.addEventListener("hashchange", route);
  route();
  renderAccount();
  fb.onAuth(onUser);
}

main();
