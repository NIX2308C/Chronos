// Shared toast/banner component. One <div id="toast" class="hidden"></div> per
// page, this script, then call showToast(msg, ok). Self-styling (inline
// cssText) so it only needs theme.css's --field/--ink/--gold/--crimson vars,
// not a separate stylesheet. Extracted from the copy duplicated in
// teacherknowledge.html and teacherstats.html.
function showToast(msg, ok) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.className = "";
  t.style.cssText = "position:fixed;bottom:24px;right:24px;z-index:60;max-width:24rem;padding:12px 16px;" +
    "font-size:14px;font-weight:500;background:var(--field);color:var(--ink);border:1px solid " +
    (ok ? "var(--gold)" : "var(--crimson)") + ";border-left-width:4px";
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { t.className = "hidden"; t.style.cssText = ""; }, 4000);
}
