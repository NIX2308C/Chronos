// Chronos "ink" UI helpers shared by every page: toasts, confirm/prompt
// dialogs (replacing the native ones), and the teacher bottom tab bar.
(function () {
  "use strict";

  // ---------- toasts ----------
  function host() {
    var h = document.querySelector(".ink-toasts");
    if (!h) {
      h = document.createElement("div");
      h.className = "ink-toasts";
      h.setAttribute("role", "status");
      h.setAttribute("aria-live", "polite");
      document.body.appendChild(h);
    }
    return h;
  }
  window.chronosToast = function (msg, kind) {
    var t = document.createElement("div");
    t.className = "ink-toast" + (kind === "err" || kind === false ? " err" : "");
    t.textContent = msg;
    host().appendChild(t);
    setTimeout(function () {
      t.classList.add("leaving");
      setTimeout(function () { t.remove(); }, 300);
    }, 4000);
  };

  // ---------- dialogs ----------
  function dialog(opts) {
    return new Promise(function (resolve) {
      var prev = document.activeElement;
      var scrim = document.createElement("div");
      scrim.className = "ink-scrim";
      var box = document.createElement("div");
      box.className = "ink-dialog";
      box.setAttribute("role", "alertdialog");
      box.setAttribute("aria-modal", "true");
      var h = document.createElement("h2");
      h.textContent = opts.title;
      h.id = "inkDlgTitle";
      box.setAttribute("aria-labelledby", h.id);
      box.appendChild(h);
      if (opts.body) {
        var p = document.createElement("p");
        p.textContent = opts.body;
        box.appendChild(p);
      }
      var input = null;
      if (opts.input) {
        input = document.createElement("input");
        input.className = "fld";
        input.placeholder = opts.placeholder || "";
        input.setAttribute("aria-label", opts.title);
        box.appendChild(input);
      }
      var row = document.createElement("div");
      row.className = "ink-dialog-actions";
      var no = document.createElement("button");
      no.type = "button"; no.className = "kbtn kbtn-ghost"; no.textContent = "Cancel";
      var yes = document.createElement("button");
      yes.type = "button"; yes.className = "kbtn" + (opts.danger ? " kbtn-danger" : ""); yes.textContent = opts.ok || "OK";
      row.append(no, yes);
      box.appendChild(row);
      scrim.appendChild(box);
      document.body.appendChild(scrim);
      function done(v) {
        document.removeEventListener("keydown", onKey, true);
        scrim.classList.add("leaving");
        setTimeout(function () { scrim.remove(); }, 160);
        if (prev && prev.focus) { try { prev.focus(); } catch (e) {} }
        resolve(v);
      }
      function onKey(e) {
        if (e.key === "Escape") { e.preventDefault(); done(opts.input ? null : false); }
        else if (e.key === "Enter" && input && document.activeElement === input) { e.preventDefault(); done(input.value); }
        else if (e.key === "Tab") {
          var f = box.querySelectorAll("button,input");
          var first = f[0], last = f[f.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }
      document.addEventListener("keydown", onKey, true);
      no.onclick = function () { done(opts.input ? null : false); };
      yes.onclick = function () { done(opts.input ? input.value : true); };
      scrim.addEventListener("pointerdown", function (e) { if (e.target === scrim) no.onclick(); });
      (input || (opts.danger ? no : yes)).focus();
    });
  }
  // confirm("Title\n\nbody") → first line is the title, the rest the body.
  window.chronosConfirm = function (message, opts) {
    var parts = String(message).split("\n\n");
    return dialog({ title: parts[0], body: parts.slice(1).join("\n\n"), danger: !opts || opts.danger !== false,
      ok: (opts && opts.ok) || "Delete" });
  };
  window.chronosPrompt = function (message, placeholder) {
    return dialog({ title: message, input: true, placeholder: placeholder, ok: "Create" });
  };

  // ---------- teacher bottom tab bar (phones) ----------
  function tabbar() {
    if (!document.querySelector("[data-side]")) return;
    var path = location.pathname;
    var items = [
      ["/teacher", "folder_open", "Material", /^\/(teacher|teacherknowledge\.html)$/],
      ["/teacher-stats", "insights", "Analytics", /^\/(teacher-stats|teacherstats\.html)$/],
      ["/student", "chat", "Preview", /^\/student/]
    ];
    var nav = document.createElement("nav");
    nav.className = "ink-tabbar";
    nav.setAttribute("aria-label", "Teacher sections");
    items.forEach(function (it) {
      var a = document.createElement("a");
      a.href = it[0];
      if (it[3].test(path)) { a.className = "on"; a.setAttribute("aria-current", "page"); }
      a.innerHTML = '<span class="msym" aria-hidden="true"></span><span></span>';
      a.firstChild.textContent = it[1];
      a.lastChild.textContent = it[2];
      nav.appendChild(a);
    });
    document.body.appendChild(nav);
    document.documentElement.classList.add("has-tabbar");
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", tabbar);
  else tabbar();
})();
