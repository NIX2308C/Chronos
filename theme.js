// Shared dark-mode toggle for the app pages (student, teacherknowledge,
// teacherstats, login). Light is the default brand experience; dark is an
// opt-in the user can flip and which then persists via localStorage.
// landing.html intentionally never loads this file and stays light-only.
(function () {
  var KEY = "chronos-theme";

  function apply(theme) {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }

  function current() {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  }

  function setTheme(theme) {
    localStorage.setItem(KEY, theme);
    apply(theme);
    syncToggleIcons(theme);
  }

  function toggleTheme() {
    setTheme(current() === "dark" ? "light" : "dark");
  }

  function syncToggleIcons(theme) {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      var icon = btn.querySelector(".material-symbols-outlined");
      if (icon) icon.textContent = theme === "dark" ? "light_mode" : "dark_mode";
      btn.setAttribute("title", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    syncToggleIcons(current());
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
      btn.addEventListener("click", toggleTheme);
    });
  });

  window.ChronosTheme = { setTheme: setTheme, toggleTheme: toggleTheme, current: current };
})();
