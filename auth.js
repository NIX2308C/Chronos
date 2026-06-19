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

  // Resolve once Firebase is configured and initialized.
  const ready = (async () => {
    let cfg;
    try {
      const res = await fetch(BASE + "/auth/config", { cache: "no-store" });
      cfg = await res.json();
    } catch (e) {
      throw new Error("Cannot reach server for auth config — is app.py running on port 5000?");
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
    // LOCAL persistence (the default) keeps the user signed in across page
    // navigations and reloads — this is what stops the constant re-login.
    await _auth.setPersistence(firebase.auth.Auth.Persistence.LOCAL);
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
    if (c.includes("wrong-password") || c.includes("invalid-credential")) return "Wrong email or password.";
    if (c.includes("user-not-found")) return "No account found with that email.";
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
    _role = null; _roleUid = null;
    return me();
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
      throw new Error(friendlyAuthError(e));
    }
    try {
      const data = await apiJson("/auth/register", { role, teacher_code: teacherCode });
      _role = data.role; _roleUid = cred.user.uid;
      return data;
    } catch (e) {
      // Couldn't set the role (e.g. wrong teacher code) — undo the account.
      try { await cred.user.delete(); } catch (_) {}
      await _auth.signOut().catch(() => {});
      throw e;
    }
  }

  async function logout() {
    await ready;
    _role = null; _roleUid = null;
    await _auth.signOut();
  }

  // Current user's identity + role from the backend (cached per uid).
  async function me() {
    await ready;
    const u = _auth.currentUser;
    if (!u) return null;
    if (_role && _roleUid === u.uid) {
      return { uid: u.uid, email: u.email, role: _role };
    }
    const data = await apiJson("/auth/me", undefined, "GET");
    _role = data.role; _roleUid = u.uid;
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
          location.replace("login.html?role=" + encodeURIComponent(primary));
          return;
        }
        let info;
        try { info = await me(); } catch (e) { info = null; }
        if (!info || (allowed.length && allowed.indexOf(info.role) === -1)) {
          // Signed in but role not allowed here — send to login to pick correctly.
          location.replace("login.html?role=" + encodeURIComponent(primary) + "&denied=1");
          return;
        }
        resolve(info);
      });
    });
  }

  window.Chronos = {
    BASE, ready, onUser, idToken, apiFetch, apiJson,
    login, signup, logout, me, requireRole, friendlyAuthError,
    get auth() { return _auth; },
  };
})();
