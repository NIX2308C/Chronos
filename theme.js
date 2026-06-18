// Shared dark-mode toggle for all Chronos pages.
// The early inline snippet in each page's <head> already applies the saved
// theme (adds the `dark` class) before paint to avoid a flash. This file wires
// the toggle button, keeps the icon in sync, and mirrors changes across tabs.
(function () {
  function current() {
    return document.documentElement.classList.contains("dark") ? "dark" : "light";
  }
  function syncIcons(theme) {
    // Toggle buttons hold a Material Symbol marked with [data-theme-icon].
    document.querySelectorAll("[data-theme-icon]").forEach(function (el) {
      el.textContent = theme === "dark" ? "light_mode" : "dark_mode";
    });
  }
  function apply(theme) {
    document.documentElement.classList.toggle("dark", theme === "dark");
    syncIcons(theme);
    try { localStorage.setItem("teacherai_theme", theme); } catch (e) {}
  }

  window.toggleTheme = function () {
    apply(current() === "dark" ? "light" : "dark");
  };

  // Set the initial icon to match whatever the early snippet applied.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { syncIcons(current()); });
  } else {
    syncIcons(current());
  }

  // Keep theme consistent if changed in another tab.
  addEventListener("storage", function (e) {
    if (e.key === "teacherai_theme" && e.newValue) {
      document.documentElement.classList.toggle("dark", e.newValue === "dark");
      syncIcons(e.newValue);
    }
  });
})();
