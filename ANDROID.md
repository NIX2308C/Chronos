# Android port

Plan for shipping Chronos on Android. Nothing here is built yet — this is the
agreed approach and the order to do it in.

## The shape of it

Chronos ships as a **Trusted Web Activity**: an Android package, built with
Bubblewrap, that opens the deployed site full-screen in Chrome with no browser
chrome. The pages stay exactly what they are today; there is no second
implementation of the tutor, the knowledge panel or the analytics to keep in
sync, which is the only realistic way to get *everything* onto a phone.

Two things decided this over the alternatives:

- **File uploads.** `POST /upload` and `POST /student/files` are driven by plain
  `<input type="file">`. In a TWA that is Chrome, so the document picker, the
  10 MB limit and the 413 path all work untouched. In a hand-rolled `WebView`
  none of it works until you implement `WebChromeClient.onShowFileChooser` and
  wire up the Storage Access Framework yourself.
- **Play review.** A thin `WebView` wrapper around a website is the textbook
  rejection under Play's "minimum functionality" policy. A TWA is Google's own
  packaging path for exactly this and is not treated that way.

A native Kotlin rewrite was considered and rejected: four screens, a markdown
chat renderer, the quiz/flashcard/concept-map UI and the analytics views would
all have to be rebuilt and then kept in step with the web app forever.

What a TWA gives us for free: Firebase `Persistence.LOCAL` and the
`chronos-theme` / `chronos-role` / `chronos_student_class` keys survive app
restarts, and a user already signed in on the web is signed in in the app.

## Phase A — make the site usable on a phone

**This is the largest phase and the only genuinely blocking one.** It is pure
web work: no Android toolchain, no Play account, and every fix also helps
anyone opening Chronos on a phone browser today. Do it first, ship it on its own.

Two navigation gaps currently make the app unusable below the breakpoint:

- `student.html:258` — the sidebar is `hidden md:flex`. Under 768px it takes
  `#classSwitcher`, `#chatList`, `#newChatBtn`, `#joinAnotherBtn`, `#tutHelp`
  and `#logoutBtn` with it. The mobile header (`student.html:311`) has only the
  wordmark and `#newChatBtnMobile`. **A student on a phone cannot switch course,
  open a past conversation, or sign out.**
- `teacherknowledge.html:110` and `teacherstats.html:67` — both do
  `@media (max-width:980px){ [data-side]{display:none!important} }`. That aside
  is the *only* link between the two teacher pages, so a teacher on a phone is
  stranded on whichever one they opened.

Fix both with one off-canvas drawer pattern. Keep every existing id and class —
several are referenced from runtime-built class strings — and change only the
hiding:

- Swap `display:none` for a transform: `fixed inset-y-0 left-0 w-64 z-40`,
  `-translate-x-full`, and an open state that sets `translate-x-0`. Keep
  `md:translate-x-0` so desktop is untouched.
- Add a hamburger as the *first* child of the mobile header, sized to match
  `#newChatBtnMobile` so the bar stays icon / title / icon.
- Add a scrim behind the drawer. Close on scrim tap, on Escape, on any
  `#chatList` selection and on `#classSwitcher` change. Set `aria-expanded`,
  move focus into the drawer on open and back to the button on close, and lock
  body scroll while it is open.
- Honour `prefers-reduced-motion` by skipping the transform transition. The
  pattern already exists in `transition.js`.

The teacher pages carry byte-identical asides, so the CSS and the ~20 lines of
toggle JS are written once and used in both.

Then the cross-cutting items:

- `height:100vh` → `100dvh` on `[data-shell]` in `teacherknowledge.html:124` and
  `teacherstats.html:85`, and on the student shell. On mobile `100vh` ignores
  the browser chrome, which is what pushes the composer under the URL bar.
- Add `viewport-fit=cover` to the viewport meta on all four pages, then
  `env(safe-area-inset-*)` padding on the fixed mobile headers, the student
  composer and the teacher scroll panes.
- Keyboard handling in the chat view: on `#input` focus, scroll it into view,
  and listen to `visualViewport.resize` to keep `#messages` pinned to the bottom.
- Tap targets: `.ibtn`, `.practice-chip`, `.teacher-tab` and the 32px action
  columns on `.doc-row` / `.rule-row` are below 44px. Raise them at the mobile
  breakpoint only.
- Horizontal overflow: collapse `.doc-row`, `.rule-row` and `.gap-row` to two
  rows under ~560px rather than letting them squeeze, and add
  `overflow-wrap:anywhere` to filename and question cells.

**Done when:** at 360×640 in device emulation, on all four pages, in both
themes, every control listed above is reachable, there is no horizontal scroll,
and the composer stays visible with the keyboard open.

## Phase B — PWA layer

Add `manifest.json` at the repo root and serve it from the static-page block in
`app.py` alongside the others:

```python
@app.route('/manifest.json')
def page_manifest():
    return send_from_directory(BASE_DIR, 'manifest.json', max_age=3600)
```

Contents: `name` and `short_name` "Chronos"; `start_url` `/login.html` rather
than `/`, which avoids a redirect on every launch; `scope` `/`; `display`
`standalone`; `orientation` `any`, because the analytics page genuinely wants
landscape; `theme_color` and `background_color` taken from the existing tokens
in `theme.css` rather than invented. Icons at 192 and 512 plus a 512 marked
`"purpose":"maskable"` with the mark inset into the safe zone — there are no
icons in the repo at all yet, so generate them from the wordmark and serve them
from an `icons/` route. Add `<link rel="manifest">` and the light/dark
`theme-color` metas to all four heads.

**Do not ship a service worker.** A TWA does not need one, and this app is
auth-gated and almost entirely dynamic, so there is very little worth caching
and a great deal that must not be. If one is ever added for browser
installability, it must never cache `/auth/*`, `/chat`, `/chats`, `/stats`,
`/classes`, `/student/files`, `/upload`, or any authenticated HTML — a cached
`student.html` handed to the next signed-in user on a shared device is a data
leak. Use a versioned cache name, network-first for documents, skip
`skipWaiting()` so a bad version cannot take over mid-session, and keep an
unregister kill switch. A buggy service worker here is strictly worse than none.

**Done when:** Lighthouse reports the site installable.

## Phase C — the wrapper

```
npx @bubblewrap/cli init --manifest=https://<cloud-run-host>/manifest.json
```

Application id `com.chronos.tutor`, `targetSdk` 35+, release keystore kept out
of the repo, Play App Signing enabled. Bubblewrap emits a normal Gradle project
with no Java to write. Keep it in `android/` and add that directory to
`.dockerignore` so it never enters the Cloud Run build context.

Digital Asset Links is mandatory — without it the TWA shows a URL bar and looks
like a browser. Serve it from Flask:

```python
@app.route('/.well-known/assetlinks.json')
def assetlinks():
    return send_from_directory(os.path.join(BASE_DIR, '.well-known'),
                               'assetlinks.json', mimetype='application/json')
```

Use the **Play App Signing** SHA-256 fingerprint from the Play Console, not the
local upload key. Using the upload key is the classic reason the URL bar appears
only on the build that came from Play. Verify with:

```
adb shell am start -a android.intent.action.VIEW -d https://<host>/login.html
```

**Done when:** `adb install` gives an app that opens with no URL bar.

## Phase D — hardening

- **Back button.** Chrome maps it to history automatically, but `transition.js`
  sets `sessionStorage["chronos-wipe"]` before navigating and clears it on
  arrival; a back-navigation restored from bfcache can leave `html.wiping`
  painted. The existing 2500 ms failsafe in each `<head>` should cover it —
  confirm on a device, and if it flashes, call `reveal()` from a `pageshow`
  handler.
- **CDN dependencies.** Tailwind, the gstatic Firebase SDK, Google Fonts and
  Material Symbols are all needed for first paint, so an offline cold start
  renders unstyled and auth never initialises. Self-hosting them is the real
  fix, and it would also remove the last obstacle to adding a CSP — `app.py`
  explains why there isn't one today.
- **Non-issues, confirmed:** auth is email/password only, with no
  `signInWithPopup` or `signInWithRedirect` anywhere, so the hardest WebView
  problem does not apply. `X-Frame-Options: DENY` governs iframe embedding; a
  TWA is a top-level context, so leave that header alone. CORS is a browser
  mechanism scoped to origins — the TWA loads the site's own origin, so
  `ALLOWED_ORIGINS` needs no change.

**Done when:** on a real device, upload works, sign-out works, back behaves, and
a cold launch in airplane mode fails gracefully.

## Risks

- **Asset-links fingerprint mismatch** → URL bar in the shipped build. Test an
  internal-track build from Play, not just a local APK.
- **Play review.** Ship as a TWA, fill the data-safety form honestly — the app
  uploads user documents — and have a privacy policy URL ready.
- **Phase A regressing desktop.** Keep every change inside the `md:` and
  `max-width:980px` scopes, and do not remove an id or class that runtime-built
  class strings depend on.

## Rough sizing

| Phase | Size | Needs |
|---|---|---|
| A responsiveness | Large | Nothing but a browser |
| B PWA | Small | Icon assets |
| C TWA | Medium | Play account, keystore, deployed host |
| D hardening | Small–Medium | A physical device |
