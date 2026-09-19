# Android port

Plan for shipping Chronos on Android. Nothing here is built yet — this is the
agreed approach and the order to do it in. Every line reference below was checked
against the tree at `e80114f`; if the pages have moved since, re-check before
editing rather than trusting the number.

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

## Phase A — make the site usable on a phone — DONE (9a2dac4)

**Built.** Pure web work: no Android toolchain, no Play account, and every fix
also helps anyone opening Chronos on a phone browser today. The rest of this
section is what was done, kept as the record of how it works.

Two things are still outstanding and neither blocks Phase B:

- **Nobody has looked at this on a real device.** It was verified in Chromium at
  360x640 and 1280x900, but the test sandbox cannot reach the Tailwind CDN or
  Google Fonts, so Tailwind was built locally from the page's own config and the
  icon spans were constrained to one em (what a real glyph occupies) before
  widths were measured. Layout numbers are sound; the look is not confirmed.
- The `[data-kpis]` and `.gap-row` behaviour on `teacherstats.html` was reasoned
  about but never rendered with real analytics data in it.

### A0 — two new shared files

Four pages need the same drawer and the same mobile rules. Rather than paste
them four times, add two files and serve them exactly like `theme.css` /
`theme.js`:

- **`mobile.css`** — the drawer transform, the scrim, and every mobile
  tap-target and overflow rule that is not page-specific.
- **`mobile.js`** — attribute-driven, mirroring how `theme.js` finds
  `[data-theme-toggle]`. It wires up `[data-drawer]`, `[data-drawer-toggle]`,
  `[data-drawer-scrim]` and `[data-drawer-close]` on `DOMContentLoaded` and
  exposes `window.ChronosDrawer = {open, close, toggle}`. It owns
  `aria-expanded`, Escape, scrim tap, moving focus into the drawer on open and
  back to the toggle on close, and closing on a click of any
  `[data-drawer-close]` element.

Two routes in the static block of `app.py`, after `transition_js` (~1355),
copying the existing shape:

```python
@app.route('/mobile.css')
def mobile_css():
    return send_from_directory(BASE_DIR, 'mobile.css', max_age=3600)


@app.route('/mobile.js')
def mobile_js():
    return send_from_directory(BASE_DIR, 'mobile.js', max_age=3600)
```

Reduced motion needs no new code on three of the four pages —
`student.html:233`, `teacherstats.html:73`, `login.html:74` and
`transition.css:106` already kill every animation and transition globally.
**`teacherknowledge.html` has no such block at all**, so put one in
`mobile.css` and all four are covered at once.

### A1 — `student.html`, the drawer

The sidebar is `<nav class="hidden md:flex …">` at line 262. Below 768px it
takes `#classSwitcher` (272), `#joinAnotherBtn` (275), `#newChatBtn` (281),
`#teacherBackLink` (284), `#chatList` (289), `#tutHelp` (298),
`[data-theme-toggle]` (301) and `#logoutBtn` (304) with it. The mobile header
(315-320) has only the wordmark and `#newChatBtnMobile`. **A student on a phone
cannot switch course, open a past conversation, or sign out.**

Turn that same `<nav>` into the drawer. Do not build a second copy of it —
duplicate ids would break every `getElementById` in the file.

```html
<nav data-drawer class="flex h-full w-64 fixed left-0 top-0 bg-surface-container flex-col z-[35] transition-transform -translate-x-full md:translate-x-0" style="border-right:1px solid var(--c-rule)">
```

- Leave `md:ml-64` on `<main>` (313) alone, so desktop behaviour is unchanged.
- **Watch line 101.** `.hidden.kbtn, .hidden.ibtn, .hidden.chat-row{display:none}`
  is narrowed like that precisely because a blanket
  `.hidden{display:none!important}` would break `md:flex` on this nav. The
  drawer must be driven by the transform, never by `.hidden`.
- Hamburger as the **first** child of the mobile header, sized to match
  `#newChatBtnMobile` so the bar reads icon / title / icon. Give it
  `data-drawer-toggle`, `aria-controls` and `aria-expanded="false"`.
- Scrim: `<div data-drawer-scrim class="md:hidden fixed inset-0 z-30" style="background:rgba(11,11,12,.55)">`.
- Close the drawer on a `#chatList` selection and on a `#classSwitcher` change.
- Body scroll is already locked — `body` is `overflow-hidden` (237). Nothing to add.

The z-index ladder, with the two new layers slotted in: mobile header 20 →
**scrim 30 → drawer 35** → `#joinGate` 40 → `#authGate` 50 → `#tut` 60 →
`.px` page wipe 9999 (`transition.css:23`).

### A2 — the teacher pages, same drawer, wired twice

`teacherknowledge.html:108-112` and `teacherstats.html:65-72` both do
`[data-side]{display:none!important}`. That aside is the *only* link between the
two pages, so a teacher on a phone is stranded on whichever one they opened,
with no course switcher, no help, no theme toggle and **no sign-out**.

Replace only the `[data-side]` line in each 980px block with the transform.
Keep the rest of both blocks — especially `teacherstats.html:70-71`, where
`.gap-row` is narrowed to `minmax(0,1fr) 112px` and its `> a` hidden. Without
that override `.gap-row` carries 284px of fixed columns into a 288px content box.

The two asides are identical apart from indentation and four differences, so the
markup cannot be shared verbatim — only the CSS and the JS are:

| | `teacherknowledge.html` | `teacherstats.html` |
|---|---|---|
| Course create / delete | `#newClassBtn` 136, `#deleteClassBtn` 139 | absent |
| Refresh | absent | `#refreshBtn` 110 |
| Active nav item | Course material is the `<span class="side-link on">` (144) | Analytics is (102) |
| Wordmark `<a>` | no `color:` | `color:var(--ink);` |

Neither page has any mobile header today. Their only header
(`teacherknowledge.html:166`, `teacherstats.html:124`) is a content header that
already does `flex-wrap:wrap` — put the hamburger there as its first child,
shown only below 980px. Close the drawer on any `.class-row` click and on either
`.side-link` navigation.

For Escape, copy the rule editor at `teacherknowledge.html:589-592`, not the
`#tut` modal — `#tut` deliberately has neither Escape nor backdrop dismiss (the
comment at 926-928 says why) and a drawer wants both.

### A3 — viewport height

- `teacherknowledge.html:124` and `teacherstats.html:85`: `height:100vh` becomes
  `height:100vh;height:100dvh` in the same inline style. The second declaration
  wins where `dvh` is supported and is dropped where it is not, so no
  `@supports` is needed.
- `student.html` has **no `100vh` at all** — it sizes off `html.h-full` /
  `body.h-full` / `main.h-full`, and `100%` of a viewport that does not shrink
  with the URL bar has exactly the same problem. Add
  `html,body{height:100vh;height:100dvh}` to the inline CSS. This is what pushes
  the composer under the URL bar.
- The tutorial cards are the only other `vh`: `student.html:1078`
  `max-h-[85vh]`, `teacherknowledge.html:880` and `teacherstats.html:574`
  `max-height:85vh`. All three to `dvh`.
- `login.html:113` is `min-height:calc(100vh - 104px)`. The 104 hardcodes the
  height of a utility bar that `@media (max-width:1000px)` hides
  (`[data-util]`, 81), so it is wrong on every phone and the footer falls off
  screen. Drop the magic number: make the page a flex column at `100dvh` and let
  `<main>` take `flex:1`.

### A4 — safe areas

`viewport-fit=cover` on the viewport meta of all four pages. It is line 4 in
each and currently identical:

```html
<meta content="width=device-width, initial-scale=1.0, viewport-fit=cover" name="viewport"/>
```

Then `env(safe-area-inset-*)` wherever content meets the notch or the gesture bar:

- `student.html` mobile header (315) gets `padding-top`, and the `pt-16` spacer
  on the chat section (335) has to grow by the same amount. Express both from
  one custom property rather than two magic numbers that can drift apart.
- `student.html` composer wrapper (340) —
  `padding-bottom: calc(0.875rem + env(safe-area-inset-bottom))`.
- The teacher scroll panes and the fixed toasts
  (`teacherknowledge.html:300-302`, `teacherstats.html:237-239`).
- `login.html` header, and the footer at 190.

### A5 — tap targets

All of this goes inside `@media (max-width:767px)` for `student.html` and
`@media (max-width:980px)` for the teacher pages, so desktop is untouched.

| Rule | Today | Where |
|---|---|---|
| `.ibtn` | 34px | `student.html:149` |
| `.ibtn` | 32px | `teacherknowledge.html:66`, `teacherstats.html:58` |
| `.practice-chip` | ~29px | `student.html:199` |
| `.attach-btn` | ~23px | `student.html:172` |
| `.file-chip button` | 15px glyph, zero padding | `student.html:177-182` |
| `.teacher-tab` | ~33px | `teacherknowledge.html:103` |
| `.rangebtn` | ~34px | `teacherstats.html:54` |
| `.switch` | 22px tall | `teacherknowledge.html:73-78` |
| `.ico-btn`, `#pwToggle` | 34px | `login.html:64`, `login.html:146` |

`.ibtn` is the single highest-leverage edit — one rule per teacher page covers
sign-out, help, theme and refresh at once. For `.switch`, keep the 40×22 visual
and enlarge the hit area with a `::after` overlay rather than redesigning the
toggle.

Two of these are **outright broken on touch, not merely small**. Fix them first:

- `teacherknowledge.html:98-101` — `.doc-row .ibtn` and `.rule-row .ibtn` are
  `opacity:0` until `:hover`. On a phone the edit and delete buttons on every
  document and every rule are invisible. Force `opacity:1` under the breakpoint.
- `student.html:498` — the chat-row delete button is built with
  `opacity-0 group-hover:opacity-100`. Same problem, and it lives inside the
  drawer we are about to make reachable.

### A6 — horizontal overflow

Usable width at 360px is **288px** on the teacher pages (main padding is
`32px 36px` — `teacherknowledge.html:177`, `teacherstats.html:137`), and both
bodies are `overflow:hidden`, so anything wider clips silently instead of
scrolling.

- `.teacher-tabs` (`teacherknowledge.html:102`) is `display:flex` with no wrap
  and no `min-width:0`. The three labels measure ~275-295px, so they clip. Add
  `overflow-x:auto` and allow wrapping.
- `.doc-row` (94) is `minmax(0,1fr) 78px 32px`, which truncates filenames to
  almost nothing. Collapse it to two rows below ~560px. **The column header at
  line 251 repeats that grid as an inline style, not a class** — change both or
  the header desyncs from the rows.
- `.rule-row` (96) is `minmax(0,1fr) 32px 32px`; survivable once its buttons are
  visible.
- The toasts (`teacherknowledge.html:300`, `teacherstats.html:237`) are
  `right:24px` with `max-width:24rem`, so a long message clips off the right
  edge. Use `left:16px;right:16px;max-width:none` on mobile.
- `[data-kpis]` drops to `1fr 1fr` at 980px but keeps its inline `gap:36px`
  (`teacherstats.html:139`), leaving ~126px a column for a 33px Newsreader
  figure. Reduce the gap at the breakpoint.
- `student.html`: `.file-chip` is capped at `max-width:15rem` (177), two thirds
  of a 360px line — make it `max-width:100%`. Add `overflow-wrap:anywhere` to
  `.map-node` / `.map-link` (210) and to inline `.ai-prose code` (228); the
  bubbles at 121-127 already have it.
- `login.html` does **not** overflow. Both `[data-split]` tracks are
  `minmax(0,…)` and the 1000px query collapses it cleanly.

### A7 — the keyboard, and what a phone still cannot see

- On `#input` focus, scroll it into view, and listen to `visualViewport.resize`
  to keep `#messages` (336) pinned to the bottom. Roughly 15 lines, and they
  belong in `student.html` rather than `mobile.js` — this is specific to the
  chat shell.
- `student.html:323` — the desktop header is `hidden md:flex`, so a phone shows
  no `#threadTitle`, no `#threadClass` and no `#statusDot` / `#statusText`.
  Surface the thread title and the status dot in the mobile header. Note that
  `paintHeader()` (513-517) and `setStatus()` (848-852) write to single ids, so
  either move those nodes into a header visible at both sizes, or have both
  functions write to both places.
- `login.html:88-89` — `#statusDot` / `#statusText` live only inside
  `[data-util]`, which the 1000px query hides, so "Server offline" never appears
  on a phone even though the check still runs (289-296). Move them out.

**Done when:** at 360×640 in device emulation, on all four pages, in both
themes, every control listed above is reachable, there is no horizontal scroll,
and the composer stays visible with the keyboard open. Then at 1280 wide,
confirm all four pages are visually unchanged.

## Phase B — PWA layer — DONE

**Built.** What shipped, and the two things worth knowing before Phase C:

- **The TWA status bar will be light in dark mode.** A manifest carries one
  `theme_color`, and a TWA reads the manifest rather than the page's
  `<meta name="theme-color">`. The two media-scoped metas are there and work in
  a browser, but the installed app gets `#F7F4EC` either way. Fixable in Phase C
  by setting the Bubblewrap theme colours, not here.
- **Lighthouse was never run** — it needs a headless Chrome run this sandbox
  can't do reliably. The installability criteria were checked directly instead
  (manifest parses and is linked from all four pages, `start_url`, `scope`,
  `display: standalone`, 192 and 512 `any` icons present and really those sizes,
  a `maskable` icon declared). What is *not* checked is the HTTPS requirement,
  which only the deployed host can satisfy.

`manifest.json` at the repo root, with values taken from the real tokens at
`student.html:73-77` rather than invented:

| Field | Value | Why |
|---|---|---|
| `name` / `short_name` | `Chronos` | |
| `start_url` | `/login.html` | `/` is a 302 to it (`app.py:1303-1308`); this avoids a redirect on every launch |
| `scope` | `/` | |
| `display` | `standalone` | |
| `orientation` | `any` | the analytics page genuinely wants landscape |
| `background_color` | `#F7F4EC` | `--c-paper`, what the pages actually paint |
| `theme_color` | `#F7F4EC` | matches the mobile header |

Plus `<link rel="manifest">` and two `<meta name="theme-color">` with
`media="(prefers-color-scheme: …)"` — `#F7F4EC` light and `#16161B` dark, which
is `--c-paper` under `html.dark` — on all four heads.

Routes, alongside the others in the static block:

```python
@app.route('/manifest.json')
def page_manifest():
    return send_from_directory(BASE_DIR, 'manifest.json', max_age=3600)


@app.route('/icons/<path:name>')
def page_icon(name):
    return send_from_directory(os.path.join(BASE_DIR, 'icons'), name, max_age=86400)
```

`send_from_directory` refuses traversal, so the `<path:name>` converter is safe.

### Icons

There is not a single icon in the repo today, not even a favicon. Generate 192,
512, a 512 marked `"purpose":"maskable"`, and a favicon — crimson `#E2001A` mark
on paper `#F7F4EC`, from the `Chronos<span class="text-primary">.</span>`
wordmark. Three things about that are easy to get wrong:

- **Pillow is not a dependency and must not become one.** `requirements.txt`
  ships to Cloud Run; an image library that runs once on a laptop does not
  belong in it. Generate the PNGs in a throwaway venv and commit only the PNGs.
- **The brand typeface is not installed anywhere here.** The wordmark is
  Newsreader (`student.html:63`), a Google Font pulled from a CDN at runtime.
  Fetch `Newsreader-Regular.ttf` for the render, and if that is not available
  fall back to a serif that is — and say which one was used in the commit
  message, so nobody later wonders why the mark looks slightly off.
- **The maskable icon is a different image, not a copy of the 512.** Android
  crops it to an arbitrary shape, so the mark has to sit inside the inner ~80%
  (a 409px box within 512) with the paper ground bled to all four edges.

**Do not ship a service worker.** A TWA does not need one, and this app is
auth-gated and almost entirely dynamic, so there is very little worth caching
and a great deal that must not be. If one is ever added for browser
installability, it must never cache `/auth/*`, `/chat`, `/chats`, `/stats`,
`/classes`, `/student/files`, `/upload`, or any authenticated HTML — a cached
`student.html` handed to the next signed-in user on a shared device is a data
leak. Use a versioned cache name, network-first for documents, skip
`skipWaiting()` so a bad version cannot take over mid-session, and keep an
unregister kill switch. A buggy service worker here is strictly worse than none.

**Done when:** Lighthouse reports the site installable. (Substituted: the
criteria above were asserted directly, including a pixel check that nothing but
the flat ground sits outside the maskable icon's 80% safe circle, so no launcher
crops the mark.)

## Phase C — the wrapper

This phase needs a JDK, the Android SDK, a Play account and a deployed host to
point the manifest at. None of that is available in a Claude Code container, so
Phase C is for a real machine.

```
npx @bubblewrap/cli init --manifest=https://<cloud-run-host>/manifest.json
```

Application id `com.chronos.tutor`, `targetSdk` 35+, release keystore kept out of
the repo, Play App Signing enabled. Bubblewrap emits a normal Gradle project
with no Java to write. Keep it in `android/`.

Two ignore files need edits:

- `.dockerignore` — add `android/`, so the Gradle project never enters the Cloud
  Run build context.
- `.gitignore` — add `*.keystore`, `*.jks`, `android/build/`,
  `android/app/build/` and `android/.gradle/`. The secrets block already covers
  `*.p12` and `*.key` but neither keystore extension.

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

- **Back button.** Chrome maps it to history automatically. `transition.js` sets
  `sessionStorage["chronos-wipe"]` before navigating, and a back-navigation out
  of the bfcache could in principle leave `html.wiping` painted — but
  `transition.js:98-106` already handles `pageshow` with `e.persisted`, clearing
  the classes and the flag, and each `<head>` carries a 2500 ms failsafe on top.
  So this is a confirm-on-device item, not a known defect.
- **CDN dependencies.** Tailwind, the gstatic Firebase SDK, Google Fonts and
  Material Symbols are all needed for first paint, so an offline cold start
  renders unstyled and auth never initialises. Self-hosting them is the real
  fix, and it would also remove the last obstacle to adding a CSP —
  `app.py:286-296` explains why there isn't one today.
- **Non-issues, confirmed by reading the code:** auth is email/password only,
  with no `signInWithPopup` or `signInWithRedirect` anywhere, so the hardest
  WebView problem does not apply. `X-Frame-Options: DENY` (`app.py:295`) governs
  iframe embedding, and a TWA is a top-level context, so leave that header
  alone. CORS is a browser mechanism scoped to origins — the TWA loads the
  site's own origin, so `ALLOWED_ORIGINS` needs no change.

**Done when:** on a real device, upload works, sign-out works, back behaves, and
a cold launch in airplane mode fails gracefully.

## Risks

- **Asset-links fingerprint mismatch** → URL bar in the shipped build. Test an
  internal-track build from Play, not just a local APK.
- **Play review.** Ship as a TWA, fill the data-safety form honestly — the app
  uploads user documents — and have a privacy policy URL ready.
- **Phase A regressing desktop.** Keep every change inside the `md:`,
  `max-width:767px` and `max-width:980px` scopes, and do not remove an id or a
  class: several are referenced from class strings built at runtime, and
  `student.html:97-101` documents one such trap already.

## Rough sizing

| Phase | Size | Needs |
|---|---|---|
| A responsiveness | Large | Nothing but a browser |
| B PWA | Small | A throwaway Pillow venv and one font file |
| C TWA | Medium | Play account, keystore, deployed host — none available in a container |
| D hardening | Small–Medium | A physical device |

## Running the checks

Recorded here because it costs half an hour to rediscover. `app.py` refuses to
import without config: it exits unless `TEACHER_SIGNUP_CODE` is set, and
Firebase Admin needs credentials before any route is importable. Write a
throwaway `.env` (it is gitignored) with a dummy signup code and a
`FIREBASE_CREDENTIALS_JSON` pointing at a fake service-account JSON, run the
checks, then delete it.

Phase A touches no Python logic the tests cover, so the six plain-`assert`
scripts are a regression net rather than a proof — but run them anyway, along
with `node --check` on every inline `<script>` block of each page touched.
