// Shared appearance for the app pages (student, teacherknowledge, teacherstats,
// login). Theme is "light" (default), "dark" or "system". Other per-device UI
// preferences (reduced motion, text size) live in one small object. The pages'
// inline <head> snippets apply both before first paint; this file keeps them
// current and wires the toggles.
(function () {
  var KEY = "chronos-theme";
  var UI_KEY = "chronos-ui";
  var dark = matchMedia("(prefers-color-scheme: dark)");

  function stored() {
    try {
      var v = localStorage.getItem(KEY);
      return v === "dark" || v === "system" ? v : "light";
    } catch (e) { return "light"; }
  }

  function resolved() {
    var t = stored();
    return t === "system" ? (dark.matches ? "dark" : "light") : t;
  }

  function apply() {
    document.documentElement.classList.toggle("dark", resolved() === "dark");
    syncToggleIcons();
  }

  function setTheme(theme) {
    try { localStorage.setItem(KEY, theme); } catch (e) {}
    apply();
  }

  function toggleTheme() {
    setTheme(resolved() === "dark" ? "light" : "dark");
  }

  function syncToggleIcons() {
    var isDark = resolved() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var icon = btn.querySelector(".msym, .material-symbols-outlined");
      if (icon) icon.textContent = isDark ? "light_mode" : "dark_mode";
      var label = isDark ? "Switch to light mode" : "Switch to dark mode";
      btn.setAttribute("title", label);
      btn.setAttribute("aria-label", label);
    });
  }

  function ui() {
    try { return JSON.parse(localStorage.getItem(UI_KEY) || "{}") || {}; } catch (e) { return {}; }
  }

  function applyUi() {
    var u = ui(), d = document.documentElement;
    if (u.motion === "reduce") d.dataset.motion = "reduce"; else delete d.dataset.motion;
    if (u.text === "large") d.dataset.text = "large"; else delete d.dataset.text;
  }

  function setUi(patch) {
    var next = Object.assign(ui(), patch);
    try { localStorage.setItem(UI_KEY, JSON.stringify(next)); } catch (e) {}
    applyUi();
    return next;
  }

  dark.addEventListener && dark.addEventListener("change", function () {
    if (stored() === "system") apply();
  });

  document.addEventListener("DOMContentLoaded", function () {
    syncToggleIcons();
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", toggleTheme);
    });
  });

  window.ChronosTheme = {
    setTheme: setTheme, toggleTheme: toggleTheme,
    current: resolved, stored: stored, ui: ui, setUi: setUi,
  };
})();
