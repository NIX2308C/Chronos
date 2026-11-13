/* System-wide maintenance screen. Loaded by every page; asks /maintenance/state
   and, when maintenance is on, covers the page with a status screen. Fails open:
   if the request fails the page is left alone. Flip it in maintenance.json (or
   with the MAINTENANCE_MODE env var) - see AGENTS.md. */
(function () {
  "use strict";
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }

  var css =
    "#chronos-maint{position:fixed;inset:0;z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:24px;" +
    "background:rgb(var(--k-page,245 244 239));color:rgb(var(--k-ink,31 33 28));font-family:'Schibsted Grotesk',system-ui,sans-serif;overflow:auto;" +
    "animation:chronos-maint-in .35s ease both}" +
    "#chronos-maint .cm-card{max-width:460px;width:100%;text-align:center;padding:44px 32px;border-radius:20px;" +
    "background:rgb(var(--k-paper,251 250 246));border:1px solid rgb(var(--k-rule,221 221 210));box-shadow:0 12px 40px rgb(0 0 0/.08)}" +
    "#chronos-maint .cm-pill{display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:999px;font-size:13px;font-weight:600;" +
    "letter-spacing:.02em;background:rgb(var(--k-gold-soft,240 232 204));color:rgb(var(--k-gold,150 114 24))}" +
    "#chronos-maint .cm-dot{width:8px;height:8px;border-radius:50%;background:currentColor;animation:chronos-maint-pulse 1.6s ease-in-out infinite}" +
    "#chronos-maint h1{font-family:Newsreader,Georgia,serif;font-weight:500;font-size:30px;line-height:1.2;margin:22px 0 12px}" +
    "#chronos-maint p{margin:0;font-size:16px;line-height:1.55;color:rgb(var(--k-sub,94 98 88))}" +
    "#chronos-maint .cm-bar{height:3px;border-radius:3px;margin:28px auto 0;width:140px;overflow:hidden;background:rgb(var(--k-hi,232 232 223))}" +
    "#chronos-maint .cm-bar i{display:block;height:100%;width:40%;border-radius:3px;background:rgb(var(--k-accent,74 93 67));animation:chronos-maint-slide 1.6s ease-in-out infinite}" +
    "@keyframes chronos-maint-in{from{opacity:0}to{opacity:1}}" +
    "@keyframes chronos-maint-pulse{50%{opacity:.3}}" +
    "@keyframes chronos-maint-slide{0%{transform:translateX(-100%)}100%{transform:translateX(350%)}}" +
    "html[data-motion=reduce] #chronos-maint *,html[data-motion=reduce] #chronos-maint{animation:none!important}" +
    "@media (prefers-reduced-motion:reduce){#chronos-maint *,#chronos-maint{animation:none!important}}";

  function show(s) {
    if (document.getElementById("chronos-maint")) return;
    var st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    var el = document.createElement("div");
    el.id = "chronos-maint";
    el.setAttribute("role", "alertdialog");
    el.setAttribute("aria-modal", "true");
    el.setAttribute("aria-labelledby", "cm-title");
    el.innerHTML =
      '<div class="cm-card"><span class="cm-pill"><span class="cm-dot"></span>' + esc(s.detail || "Scheduled maintenance") + "</span>" +
      '<h1 id="cm-title">' + esc(s.title || "Down for maintenance") + "</h1>" +
      "<p>" + esc(s.message || "We'll be back shortly.") + "</p>" +
      '<div class="cm-bar"><i></i></div></div>';
    document.body.appendChild(el);
    document.documentElement.style.overflow = "hidden";
  }

  function hide() {
    var el = document.getElementById("chronos-maint");
    if (el) { el.remove(); document.documentElement.style.overflow = ""; }
  }

  function check() {
    fetch("/maintenance/state", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) { if (s && s.enabled) show(s); else hide(); })
      .catch(function () {});
  }

  function start() { check(); setInterval(check, 60000); }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
