/* ============================================================
   Chronos page wipe — behaviour half. Styles live in transition.css,
   which explains the two-page handoff.

   Exposes window.ChronosWipe.go(href, replace) so the pages that
   navigate from script (the login redirect, sign-out) get the same
   transition as a plain link click.
   ============================================================ */
(function () {
  var KEY = "chronos-wipe";
  var COVER_MS = 420;   // bars fully across (last bar starts at 160ms + 300ms travel)
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  var px = null;

  function el() {
    if (!px) px = document.querySelector(".px");
    return px;
  }

  function flag() { try { sessionStorage.setItem(KEY, "1"); } catch (e) {} }
  function unflag() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

  // Leaving: cover, then navigate.
  function go(href, replace) {
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
        setTimeout(function () { node.classList.remove("act", "reveal"); }, 620);
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

  // Back/forward out of the bfcache would otherwise restore a covered page.
  addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    var node = el();
    if (node) node.classList.remove("act", "cover", "reveal");
    document.documentElement.classList.remove("wiping");
    unflag();
  });

  if (document.documentElement.classList.contains("wiping")) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
    else reveal();
  } else {
    unflag();
  }

  window.ChronosWipe = { go: go };
})();
