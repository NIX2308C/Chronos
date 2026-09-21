/* ============================================================
   Chronos navigation and view loading. Styles live in transition.css.

   Page changes are plain navigations; the browser crossfades them
   (see @view-transition in transition.css). ChronosNav.go() stays the
   single place script navigations go through, so callers don't care.

   createViewTransition() keeps a view mounted while its replacement is
   prepared, showing only a thin progress line if that takes a moment.
   ============================================================ */
(function () {
  function go(href, replace) {
    replace ? location.replace(href) : (location.href = href);
  }

  function isInternalPage(a) {
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return false;
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") return false;
    if (/^(mailto:|tel:|javascript:)/i.test(href)) return false;
    if (a.host && a.host !== location.host) return false;
    return /^\/(?:login|student|teacher(?:-stats)?)(?:\?|#|$)/i.test(href)
      || /\.html(\?|#|$)/i.test(href);
  }

  // Warm up internal destinations while the pointer is heading for them.
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

  // Keep the current view mounted while its replacement is prepared. A later
  // request supersedes an earlier one, including a request that finishes late.
  // options.instant runs `commit` in the same frame with no loading state, for
  // views whose data is already in memory.
  function createViewTransition(container) {
    var sequence = 0;
    var timer = null;
    var revealTimer = null;
    var indicator = document.createElement("div");
    indicator.className = "chronos-view-loading";
    indicator.setAttribute("role", "status");
    indicator.setAttribute("aria-live", "polite");
    indicator.hidden = true;
    indicator.innerHTML = '<span class="chronos-sr"></span>';
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
      if (options.instant) {
        commit(prepare());
        return true;
      }
      indicator.firstElementChild.textContent = options.label || "Loading";
      container.setAttribute("aria-busy", "true");
      timer = setTimeout(function () {
        if (current === sequence) indicator.hidden = false;
      }, 200);
      try {
        var data = await prepare();
        if (current !== sequence) return false;
        clear();
        commit(data);
        container.classList.add("chronos-view-reveal");
        revealTimer = setTimeout(function () { container.classList.remove("chronos-view-reveal"); }, 160);
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

  window.ChronosNav = { go: go, createViewTransition: createViewTransition };
  window.ChronosWipe = window.ChronosNav; // older call sites
})();
