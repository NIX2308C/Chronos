/* Chronos shared auth helper.
 *
 * Wraps Firebase Email/Password auth so every page can sign in, stay signed in,
 * read the user's role, and call the backend with a Bearer token — without each
 * page re-implementing it (and without re-prompting for a password when you move
 * between panels: Firebase persists the session in the browser by default).
 *
 * Load order on a page:
 *   <script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>
 *   <script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"></script>
 *   <script src="auth.js"></script>
 *
 * Then: await Chronos.ready;  // config loaded + firebase initialized
 */
(function () {
  // Same-origin when served by Flask (deployed); the dev server when opened via
  // Live Server (port 5500) or straight from disk.
  const BASE = (location.port === "5500" || location.protocol === "file:")
    ? "http://127.0.0.1:5000" : "";

  let _auth = null;
  let _role = null;          // cached role for the current user
  let _roleUid = null;       // uid the cached role belongs to
  const CONFIG_KEY = "chronos-auth-config";
  const ROLE_KEY = "chronos-role";
  const CONFIG_TTL = 24 * 60 * 60 * 1000;
  const ROLE_TTL = 10 * 60 * 1000;
  // What the last verified sign-in looked like: {uid, email, role, dev}. It lets
  // the login page forward a returning user before Firebase has even loaded and
  // lets the app pages paint their shell straight away. It is a hint, never a
  // grant — every API call is still authorized by the server — and it is cleared
  // on sign-out and whenever a page bounces the user back to login.
  const HINT_KEY = "chronos-hint";

  function readHint() {
    try {
      const h = JSON.parse(localStorage.getItem(HINT_KEY) || "null");
      return h && h.uid && h.role ? h : null;
    } catch (_) { return null; }
  }

  function writeHint(info) {
    try { localStorage.setItem(HINT_KEY, JSON.stringify(info)); } catch (_) {}
  }

  function clearHint() {
    try { localStorage.removeItem(HINT_KEY); } catch (_) {}
  }

  function readCache(storage, key, maxAge) {
    try {
      const item = JSON.parse(storage.getItem(key) || "null");
      return item && Date.now() - item.saved < maxAge ? item.value : null;
    } catch (_) { return null; }
  }

  function writeCache(storage, key, value) {
    try { storage.setItem(key, JSON.stringify({ saved: Date.now(), value })); } catch (_) {}
  }

  function clearRole() {
    _role = null; _roleUid = null;
    try { sessionStorage.removeItem(ROLE_KEY); } catch (_) {}
    clearHint();
  }

  function rememberRole(uid, role, email) {
    _role = role; _roleUid = uid;
    writeCache(sessionStorage, ROLE_KEY, { uid, role });
    // `dev` is left unknown until /auth/me has answered once.
    const prev = readHint();
    writeHint({ uid, role, email: email || (prev && prev.uid === uid ? prev.email : ""),
                dev: prev && prev.uid === uid ? prev.dev : undefined });
  }

  // Resolve once Firebase is configured and initialized.
  const ready = (async () => {
    let cfg = readCache(localStorage, CONFIG_KEY, CONFIG_TTL);
    if (!cfg) {
      try {
        const res = await fetch(BASE + "/auth/config");
        if (!res.ok) throw new Error("config request failed");
        cfg = await res.json();
        writeCache(localStorage, CONFIG_KEY, cfg);
      } catch (e) {
        throw new Error("Cannot reach server for auth config — is app.py running on port 5000?");
      }
    }
    if (!cfg.apiKey) {
      throw new Error("Firebase is not configured: set FIREBASE_WEB_API_KEY on the server.");
    }
    firebase.initializeApp({
      apiKey: cfg.apiKey,
      authDomain: cfg.authDomain,
      projectId: cfg.projectId,
    });
    _auth = firebase.auth();
    // Firebase's default browser persistence is LOCAL. Calling setPersistence
    // on every navigation makes session restoration wait for an extra storage
    // operation before onAuthStateChanged can settle.
    return _auth;
  })();

  // Fire cb(user) whenever auth state settles (after `ready`).
  function onUser(cb) {
    ready.then(() => _auth.onAuthStateChanged(cb)).catch((e) => cb(null, e));
  }

  // A fresh ID token for the current user, or null if signed out.
  async function idToken() {
    await ready;
    const u = _auth.currentUser;
    return u ? await u.getIdToken() : null;
  }

  // fetch() against the backend with the Bearer token attached.
  async function apiFetch(path, opts = {}) {
    // Developer tool (Settings → Developer): add artificial latency to every call.
    const lag = Number(localStorage.getItem("chronos-dev-lag")) || 0;
    if (lag > 0) await new Promise((r) => setTimeout(r, lag));
    const token = await idToken();
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers["Authorization"] = "Bearer " + token;
    return fetch(BASE + path, Object.assign({}, opts, { headers }));
  }

  // JSON POST helper. Throws Error(message) on non-2xx / {error}.
  async function apiJson(path, body, method = "POST") {
    let res;
    try {
      res = await apiFetch(path, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (e) {
      throw new Error("Cannot reach server — is app.py running on port 5000?");
    }
    let data = {};
    try { data = await res.json(); } catch (e) {}
    if (res.status === 401) throw new Error(data.error || "Please sign in again.");
    if (res.status === 403) throw new Error(data.error || "You don't have access to this.");
    if (!res.ok || data.error) throw new Error(data.details || data.error || "Request failed");
    return data;
  }

  function friendlyAuthError(e) {
    const c = (e && e.code) || "";
    // user-not-found shares the wrong-password copy so sign-in never reveals
    // whether an email is registered.
    if (c.includes("wrong-password") || c.includes("invalid-credential") ||
        c.includes("user-not-found")) return "Wrong email or password.";
    if (c.includes("email-already-in-use")) return "An account with that email already exists.";
    if (c.includes("weak-password")) return "Password must be at least 6 characters.";
    if (c.includes("invalid-email")) return "That doesn't look like a valid email.";
    if (c.includes("too-many-requests")) return "Too many attempts. Try again in a moment.";
    return (e && e.message) || "Something went wrong.";
  }

  async function login(email, password) {
    await ready;
    try {
      await _auth.signInWithEmailAndPassword(email, password);
    } catch (e) {
      throw new Error(friendlyAuthError(e));
    }
    clearRole();
    return me();
  }

  // Record the role on the backend for an account that already exists in Firebase.
  // `rollback` says whether we created that account moments ago and may therefore
  // delete it again: for a resumed signup the account predates this call, and
  // deleting it over a wrong teacher code would destroy an account that may
  // already be someone's. The server does its own cleanup either way — this is
  // only the fast path for the tab that's still open.
  async function register(cred, role, teacherCode, rollback) {
    try {
      const data = await apiJson("/auth/register", { role, teacher_code: teacherCode });
      rememberRole(cred.user.uid, data.role, cred.user.email);
      return data;
    } catch (e) {
      if (rollback) { try { await cred.user.delete(); } catch (_) {} }
      await _auth.signOut().catch(() => {});
      throw e;
    }
  }

  // Finish a signup that died between "Firebase account created" and "role
  // assigned" — a dropped request, or a tab closed at the wrong moment. That
  // leaves the email taken by an account with no role, which nothing can sign
  // into and which a plain retry can only ever bounce off as email-already-in-use.
  // Signing in first means this can only ever repair an account whose password
  // the caller already knows.
  async function resumeSignup(email, password, role, teacherCode, takenErr) {
    let cred;
    try {
      cred = await _auth.signInWithEmailAndPassword(email, password);
    } catch (_) {
      throw new Error(friendlyAuthError(takenErr));   // not ours to finish
    }
    // /auth/me answers only for accounts that have a role, so a failure here is
    // the "unfinished" signal we're looking for.
    let complete = false;
    try { complete = !!(await me()); } catch (_) {}
    if (complete) {
      await _auth.signOut().catch(() => {});
      throw new Error(friendlyAuthError(takenErr));   // a real, finished account
    }
    return register(cred, role, teacherCode, false);
  }

  // Create the Firebase account, then record the role on the backend (teacher
  // requires the signup code). Rolls the account back if role setup fails so we
  // don't leave a half-created teacher.
  async function signup(email, password, role, teacherCode) {
    await ready;
    let cred;
    try {
      cred = await _auth.createUserWithEmailAndPassword(email, password);
    } catch (e) {
      if (e && e.code && e.code.indexOf("email-already-in-use") !== -1) {
        return resumeSignup(email, password, role, teacherCode, e);
      }
      throw new Error(friendlyAuthError(e));
    }
    return register(cred, role, teacherCode, true);
  }

  async function logout() {
    await ready;
    clearRole();
    await _auth.signOut();
  }

  // Current user's identity + role from the backend (cached per uid).
  async function me() {
    await ready;
    const u = _auth.currentUser;
    if (!u) return null;
    if (!_role) {
      const cached = readCache(sessionStorage, ROLE_KEY, ROLE_TTL);
      if (cached && cached.uid === u.uid && cached.role) {
        _role = cached.role; _roleUid = cached.uid;
      }
    }
    const hint = readHint();
    // A cached role is trusted only once the server has also said whether this is
    // a developer account; a hint written at signup doesn't know yet.
    if (_role && _roleUid === u.uid && hint && hint.uid === u.uid && typeof hint.dev === "boolean") {
      return { uid: u.uid, email: u.email, role: _role, is_dev: hint.dev };
    }
    const data = await apiJson("/auth/me", undefined, "GET");
    _role = data.role; _roleUid = u.uid;
    writeCache(sessionStorage, ROLE_KEY, { uid: u.uid, role: data.role });
    writeHint({ uid: u.uid, role: data.role, email: data.email || u.email, dev: !!data.is_dev });
    return data;
  }

  // Gate a page: ensure a signed-in user whose role is allowed, else redirect to
  // login. `roles` is a single role or an array (e.g. ["student","teacher"] for a
  // page both may use). Returns the user object, or never resolves (redirects).
  function requireRole(roles) {
    const allowed = Array.isArray(roles) ? roles : [roles];
    const primary = allowed[0];
    return new Promise((resolve) => {
      onUser(async (user) => {
        if (!user) {
          clearHint();
          location.replace("/login?role=" + encodeURIComponent(primary));
          return;
        }
        let info;
        try { info = await me(); } catch (e) { info = null; }
        if (!info || (allowed.length && allowed.indexOf(info.role) === -1)) {
          // Signed in but role not allowed here — send to login to pick correctly.
          clearHint();
          location.replace("/login?role=" + encodeURIComponent(primary) + "&denied=1");
          return;
        }
        resolve(info);
      });
    });
  }

  // Resolves with the Firebase user as soon as a session is restored, before the
  // role has been verified. Lets a page start its data requests immediately
  // while requireRole() confirms the role in parallel; the server authorizes
  // each of those requests itself.
  function whenSignedIn() {
    return new Promise((resolve) => {
      onUser((user) => { if (user) resolve(user); });
    });
  }

  // --- classes ---
  // List the caller's classes (teacher: owned + join codes; student: joined).
  async function listClasses() {
    const d = await apiJson("/classes", undefined, "GET");
    return d.classes || [];
  }
  // Teacher: create a class. Returns { id, name, join_code, migrated_rules }.
  function createClass(name) { return apiJson("/classes", { name }); }
  // Student/teacher: join a class by its code. Returns { id, name }.
  function joinClass(code) { return apiJson("/classes/join", { join_code: code }); }
  // Teacher: delete a class and its rules.
  function deleteClass(id) { return apiJson("/classes/" + encodeURIComponent(id), undefined, "DELETE"); }

  window.Chronos = {
    BASE, ready, onUser, idToken, apiFetch, apiJson,
    login, signup, logout, me, requireRole, friendlyAuthError, hint: readHint, whenSignedIn,
    listClasses, createClass, joinClass, deleteClass,
    get auth() { return _auth; },
  };
})();
