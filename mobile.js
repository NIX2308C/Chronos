/* ============================================================
   Chronos mobile nav — behaviour half. Styles live in mobile.css,
   which explains the two breakpoints and the visibility handling.

   Attribute-driven, the way theme.js finds [data-theme-toggle], so the
   same code serves student.html and both teacher pages without any of
   them naming an element:

     [data-drawer="sm"|"md"]        the panel that slides in
     [data-drawer-toggle="sm"|"md"] the hamburger
     [data-drawer-scrim="sm"|"md"]  the backdrop
     [data-drawer-close]            a container whose clicks dismiss it
     [data-drawer-keep]             ...except inside one of these

   The token ("sm" 767px, "md" 980px) has to match mobile.css, which is
   why it is read back here rather than hardcoded.

   Exposes window.ChronosDrawer = {open, close, toggle, isOpen}.
   ============================================================ */
(function () {
  var OPEN = "drawer-open";
  var WIDTHS = { sm: 767, md: 980 };

  var drawer = document.querySelector("[data-drawer]");
  if (!drawer) return;

  var token = drawer.getAttribute("data-drawer") || "md";
  var mq = matchMedia("(max-width:" + (WIDTHS[token] || WIDTHS.md) + "px)");
  var toggles = document.querySelectorAll("[data-drawer-toggle]");
  var lastToggle = null;

  function isOpen() {
    return document.documentElement.classList.contains(OPEN);
  }

  // A drawer parked off-screen is still in the tab order and still read by
  // screen readers. mobile.css handles that with `visibility`; `inert` is
  // the better tool where it exists, and it also blocks pointer events on
  // the sliver that a transform can leave behind mid-transition.
  function syncInert() {
    if (!("inert" in HTMLElement.prototype)) return;
    drawer.inert = mq.matches && !isOpen();
  }

  function syncToggles() {
    var expanded = isOpen() ? "true" : "false";
    for (var i = 0; i < toggles.length; i++) {
      toggles[i].setAttribute("aria-expanded", expanded);
    }
  }

  function firstFocusable() {
    return drawer.querySelector(
      'a[href], button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
  }

  function open(fromToggle) {
    if (!mq.matches || isOpen()) return;
    lastToggle = fromToggle || null;
    document.documentElement.classList.add(OPEN);
    syncInert();
    syncToggles();
    var target = firstFocusable();
    if (target) target.focus();
  }

  function close() {
    if (!isOpen()) return;
    document.documentElement.classList.remove(OPEN);
    // Order matters: the focused element is inside the drawer, and setting
    // `inert` while it still holds focus drops focus to <body>. Move it out
    // first, then park the drawer.
    if (lastToggle && document.contains(lastToggle) && mq.matches) lastToggle.focus();
    lastToggle = null;
    syncInert();
    syncToggles();
  }

  function toggle(fromToggle) {
    isOpen() ? close() : open(fromToggle);
  }

  for (var i = 0; i < toggles.length; i++) {
    (function (btn) {
      if (!btn.hasAttribute("aria-controls") && drawer.id) {
        btn.setAttribute("aria-controls", drawer.id);
      }
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        toggle(btn);
      });
    })(toggles[i]);
  }

  var scrims = document.querySelectorAll("[data-drawer-scrim]");
  for (var j = 0; j < scrims.length; j++) {
    scrims[j].addEventListener("click", close);
  }

  // Delegated, because the rows that should dismiss the drawer — the chat
  // history, the course list — are rebuilt from script every time they change.
  drawer.addEventListener("click", function (e) {
    if (!isOpen() || !e.target.closest) return;
    if (e.target.closest("[data-drawer-keep]")) return;
    if (e.target.closest("[data-drawer-close]")) close();
  });

  drawer.addEventListener("change", function (e) {
    if (isOpen() && e.target.closest && e.target.closest("[data-drawer-close]")) close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && isOpen()) close();
  });

  // Rotating to landscape, or resizing past the breakpoint, must not leave
  // the page stuck in the open state — above the breakpoint the panel is
  // permanent and the scrim would sit over everything.
  function onChange() {
    if (!mq.matches) {
      document.documentElement.classList.remove(OPEN);
      lastToggle = null;
      syncToggles();
    }
    syncInert();
  }
  mq.addEventListener ? mq.addEventListener("change", onChange) : mq.addListener(onChange);

  syncInert();
  syncToggles();

  window.ChronosDrawer = { open: open, close: close, toggle: toggle, isOpen: isOpen };
})();
