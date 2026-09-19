/* ============================================================
   Chronos page wipe — behaviour half. Styles live in transition.css,
   which explains the two-page handoff.

   Exposes window.ChronosWipe.go(href, replace) so the pages that
   navigate from script (the login redirect, sign-out) get the same
   transition as a plain link click.
   ============================================================ */
(function () {
  var KEY = "chronos-wipe";
  var COVER_MS = 340;
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var px = null;
  var navigating = false;

  function el() {
    if (!px) px = document.querySelector(".px");
    return px;
  }

  function flag() { try { sessionStorage.setItem(KEY, "1"); } catch (e) {} }
  function unflag() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

  // Leaving: cover, then navigate.
  function go(href, replace) {
    if (navigating) return;
    navigating = true;
    var node = el();
    if (reduce || !node) {
      replace ? location.replace(href) : (location.href = href);
      return;
    }
    flag();
    node.classList.add("act", "cover");
    setTimeout(function () {
      replace ? location.replace(href) : (location.href = href);
    }, COVER_MS);
  }

  // Arriving: <html class="wiping"> already painted the covered state,
  // so hand the bars a transition and sweep them back out.
  function reveal() {
    var node = el();
    unflag();
    if (!node) { document.documentElement.classList.remove("wiping"); return; }
    // .cover holds the same covered transform, but with transitions live —
    // swapping in the same frame is invisible, and gives the next frame
    // something to animate away from.
    node.classList.add("act", "cover");
    document.documentElement.classList.remove("wiping");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        node.classList.remove("cover");
        node.classList.add("reveal");
        setTimeout(function () { node.classList.remove("act", "reveal"); }, 430);
      });
    });
  }

  function isInternalPage(a) {
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return false;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") return false;
    if (/^(mailto:|tel:|javascript:)/i.test(href)) return false;
    if (a.host && a.host !== location.host) return false;
    return /\.html(\?|#|$)/i.test(href);
  }

  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!isInternalPage(a)) return;
    e.preventDefault();
    go(a.getAttribute("href"));
  });

  // Warm up internal destinations while the pointer is heading for them. This
  // is intentionally a document prefetch, not eager page loading on startup.
  function prefetch(e) {
    var a = e.target.closest ? e.target.closest("a[href]") : null;
    if (!isInternalPage(a) || a.dataset.chronosPrefetched) return;
    a.dataset.chronosPrefetched = "1";
    var link = document.createElement("link");
    link.rel = "prefetch";
    link.href = a.href;
    document.head.appendChild(link);
  }
  document.addEventListener("pointerover", prefetch, { passive: true });
  document.addEventListener("touchstart", prefetch, { passive: true });

  // Back/forward out of the bfcache would otherwise restore a covered page.
  addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var node = el();
    if (node) node.classList.remove("act", "cover", "reveal");
    document.documentElement.classList.remove("wiping");
    unflag();
    navigating = false;
  });

  if (document.documentElement.classList.contains("wiping")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
    else reveal();
  } else {
    unflag();
  }

  // A short entrance motion makes ordinary (non-wipe) loads, including the
  // first page after authentication, feel deliberate without ever hiding data.
  function enterPage() {
    requestAnimationFrame(function () { document.body && document.body.classList.add("chronos-page-enter"); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enterPage);
  else enterPage();

  // Keep the current view mounted while its replacement is prepared. A later
  // request supersedes an earlier one, including a request that finishes late.
  function createViewTransition(container) {
    var sequence = 0;
    var timer = null;
    var revealTimer = null;
    var indicator = document.createElement("div");
    indicator.className = "chronos-view-loading";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    indicator.hidden = true;
    indicator.innerHTML = '<span class="chronos-view-spinner" aria-hidden="true"></span><span></span>';
    container.classList.add("chronos-view-host");
    container.appendChild(indicator);

    function clear() {
      clearTimeout(timer);
      indicator.hidden = true;
      container.removeAttribute("aria-busy");
    }

    function cancel() { sequence++; clear(); container.classList.remove("chronos-view-reveal"); }

    async function show(prepare, commit, options) {
      var current = ++sequence;
      clear();
      clearTimeout(revealTimer);
      container.classList.remove("chronos-view-reveal");
      options = options || {};
      indicator.lastElementChild.textContent = options.label || "Loading…";
      container.setAttribute("aria-busy", "true");
      timer = setTimeout(function () {
        if (current === sequence) indicator.hidden = false;
      }, 120);
      try {
        var data = await prepare();
        if (current !== sequence) return false;
        clear();
        commit(data);
        container.classList.add("chronos-view-reveal");
        revealTimer = setTimeout(function () { container.classList.remove("chronos-view-reveal"); }, 240);
        return true;
      } catch (error) {
        if (current === sequence) {
          clear();
          if (options.onError) options.onError(error);
        }
        return false;
      }
    }

    return { show: show, cancel: cancel };
  }

  window.ChronosWipe = { go: go, createViewTransition: createViewTransition };
})();
