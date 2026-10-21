// Thin wrapper around Firebase Auth + Firestore.
//
// Firestore layout:
//   profiles/{uid}                    { displayName, nameLower, photoURL, updatedAt }   readable by id (not listable)
//   users/{uid}/movies/{imdbId}       { seen, rating, review, updatedAt }               owner + friends can read
//   users/{uid}/friends/{friendUid}   { since }
//   users/{uid}/settings/prefs        { filters, layout, updatedAt }                     owner only
//   friendRequests/{fromUid}_{toEmail} { from, fromEmail, toEmail, createdAt }          sender + owner of toEmail

import { firebaseConfig } from "./firebase-config.js";

const SDK = "https://www.gstatic.com/firebasejs/11.0.2";

export const isConfigured = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId);

let auth, db, A, F;
let pendingSignup = null; // lets onAuth wait until a new account's profile is written

if (isConfigured) {
  const [{ initializeApp }, authMod, fsMod] = await Promise.all([
    import(`${SDK}/firebase-app.js`),
    import(`${SDK}/firebase-auth.js`),
    import(`${SDK}/firebase-firestore.js`),
  ]);
  A = authMod;
  F = fsMod;
  const app = initializeApp(firebaseConfig);
  auth = A.getAuth(app);
  db = F.getFirestore(app);
}

const ref = (...path) => F.doc(db, ...path);
const col = (...path) => F.collection(db, ...path);
const snapList = (snap) => snap.docs.map((d) => ({ id: d.id, ...d.data() }));

// ---------- Auth ----------

export function onAuth(callback) {
  if (!isConfigured) {
    callback(null);
    return () => {};
  }
  return A.onAuthStateChanged(auth, async (user) => {
    if (user) {
      if (pendingSignup) await pendingSignup.catch(() => {});
      else await ensureProfile(user).catch((err) => console.error("Profile setup failed", err));
    }
    callback(user);
  });
}

export const signInGoogle = () => A.signInWithPopup(auth, new A.GoogleAuthProvider());
export const signInEmail = (email, password) => A.signInWithEmailAndPassword(auth, email, password);
export const resetPassword = (email) => A.sendPasswordResetEmail(auth, email);
export const signOut = () => A.signOut(auth);

export async function signUpEmail(displayName, email, password) {
  pendingSignup = (async () => {
    const cred = await A.createUserWithEmailAndPassword(auth, email, password);
    await A.updateProfile(cred.user, { displayName });
    await ensureProfile(cred.user, true);
    // Friend requests are addressed by email, so the address has to be proven first.
    await A.sendEmailVerification(cred.user).catch((err) => console.warn("Verification email failed", err));
    return cred;
  })();
  try {
    return await pendingSignup;
  } finally {
    pendingSignup = null;
  }
}

export const resendVerification = () => A.sendEmailVerification(auth.currentUser);

// After the user clicks the verification link, reload their account and refresh the ID
// token so Firestore sees email_verified = true. Returns the refreshed user.
export async function refreshUser() {
  await auth.currentUser.reload();
  await auth.currentUser.getIdToken(true);
  return auth.currentUser;
}

async function ensureProfile(user, force = false) {
  const profileRef = ref("profiles", user.uid);
  const existing = await F.getDoc(profileRef);
  if (!existing.exists() || force) {
    const displayName = (user.displayName || user.email?.split("@")[0] || "Movie fan").slice(0, 40);
    await saveProfile(user.uid, { displayName, photoURL: safePhoto(user.photoURL) });
  }
}

// Profile photos are limited to Google's image servers (see firestore.rules).
const safePhoto = (url) => (/^https:\/\/[a-z0-9-]+\.googleusercontent\.com\//.test(url || "") ? url : "");

export function saveProfile(uid, { displayName, photoURL }) {
  const name = displayName.trim().slice(0, 40);
  return F.setDoc(
    ref("profiles", uid),
    { displayName: name, nameLower: name.toLowerCase(), photoURL: safePhoto(photoURL), updatedAt: F.serverTimestamp() },
    { merge: true },
  );
}

export async function getProfile(uid) {
  const snap = await F.getDoc(ref("profiles", uid));
  return snap.exists() ? { id: snap.id, ...snap.data() } : { id: uid, displayName: "Unknown user" };
}

// ---------- Movie tracking ----------

export function watchUserMovies(uid, callback, onError) {
  return F.onSnapshot(
    col("users", uid, "movies"),
    (snap) => {
      const map = new Map();
      snap.forEach((d) => map.set(d.id, d.data()));
      callback(map);
    },
    onError,
  );
}

// Saves {seen, rating, review}; removes the doc when nothing is left to track.
export function saveMovieEntry(uid, movieId, entry) {
  const docRef = ref("users", uid, "movies", movieId);
  const clean = {
    seen: Boolean(entry.seen),
    rating: Math.max(0, Math.min(5, Math.round(entry.rating || 0))),
    review: (entry.review || "").trim().slice(0, 500),
  };
  if (!clean.seen && !clean.rating && !clean.review) return F.deleteDoc(docRef);
  return F.setDoc(docRef, { ...clean, updatedAt: F.serverTimestamp() });
}

// ---------- Settings (private to the user) ----------

export async function getSettings(uid) {
  const snap = await F.getDoc(ref("users", uid, "settings", "prefs"));
  return snap.exists() ? snap.data() : null;
}

export function saveSettings(uid, { filters, layout }) {
  return F.setDoc(ref("users", uid, "settings", "prefs"), { filters, layout, updatedAt: F.serverTimestamp() });
}

// ---------- Friends ----------

export const normalizeEmail = (email) => (email || "").trim().toLowerCase();

// Sends a request to an email address without checking whether anyone uses it, so the
// app never reveals which emails have accounts. Only that email's owner can see it.
export function sendFriendRequest(user, toEmail) {
  const to = normalizeEmail(toEmail);
  return F.setDoc(ref("friendRequests", `${user.uid}_${to}`), {
    from: user.uid,
    fromEmail: normalizeEmail(user.email),
    toEmail: to,
    createdAt: F.serverTimestamp(),
  });
}

// Requests I've sent (from my uid) or received (to my verified email).
export const watchOutgoingRequests = (uid, callback) =>
  F.onSnapshot(F.query(col("friendRequests"), F.where("from", "==", uid)), (snap) => callback(snapList(snap)));
export const watchIncomingRequests = (email, callback, onError) =>
  F.onSnapshot(
    F.query(col("friendRequests"), F.where("toEmail", "==", normalizeEmail(email))),
    (snap) => callback(snapList(snap)),
    onError,
  );

// Accepting creates the friendship on both sides and removes the request in one batch.
export function acceptFriendRequest(request, myUid) {
  const batch = F.writeBatch(db);
  const since = F.serverTimestamp();
  batch.set(ref("users", myUid, "friends", request.from), { since });
  batch.set(ref("users", request.from, "friends", myUid), { since });
  batch.delete(ref("friendRequests", request.id));
  return batch.commit();
}

export const deleteFriendRequest = (requestId) => F.deleteDoc(ref("friendRequests", requestId));

export function removeFriend(myUid, theirUid) {
  const batch = F.writeBatch(db);
  batch.delete(ref("users", myUid, "friends", theirUid));
  batch.delete(ref("users", theirUid, "friends", myUid));
  return batch.commit();
}

// Only reports friendships the server has confirmed, so reading a new friend's
// movies isn't attempted (and denied) before the friendship is committed.
export function watchFriends(uid, callback) {
  return F.onSnapshot(col("users", uid, "friends"), { includeMetadataChanges: true }, (snap) =>
    callback(snap.docs.filter((d) => !d.metadata.hasPendingWrites).map((d) => d.id)),
  );
}
