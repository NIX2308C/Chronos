/* Chronos settings panel, shared by every app page.
 *
 *   ChronosSettings.open({ tutor, onClearChats, onReplayTour })
 *
 * Everything autosaves. Tutor preferences go to the server (they follow the
 * account); appearance lives on this device. Developer tools appear only for
 * accounts the server says are developers.
 */
(function () {
  var PERSONALITY_NOTES = {
    default: "Balanced and clear.", encouraging: "Warm, notices progress.", concise: "Short and to the point.",
    socratic: "Guides with questions.", casual: "Relaxed study partner.", vibetastic: "Dev only. Unhinged 2023 chatbot."
  };
  var root = null, prefs = null, personalities = [], timer = null, pending = {}, savedTimer = null, deploymentTimer = null;
  var savedEl = null, opts = {}, me = null, lastFocus = null;

  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "text") el.textContent = attrs[k];
      else if (k.slice(0, 2) === "on") el.addEventListener(k.slice(2), attrs[k]);
      else el.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) el.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return el;
  }

  function flash(text, err) {
    if (!savedEl) return;
    savedEl.textContent = text;
    savedEl.className = "cs-saved " + (err ? "err" : "on");
    clearTimeout(savedTimer);
    if (!err) savedTimer = setTimeout(function () { savedEl.className = "cs-saved"; }, 1600);
  }

  function queueSave(patch) {
    Object.assign(prefs, patch);
    Object.assign(pending, patch);
    flash("Saving…");
    clearTimeout(timer);
    timer = setTimeout(async function () {
      var body = pending; pending = {};
      try {
        var d = await Chronos.apiJson("/me/preferences", { preferences: body });
        prefs = d.preferences;
        flash("Saved");
      } catch (e) { flash((e && e.message) || "Couldn't save", true); }
    }, 350);
  }

  function row(label, note, control, stack) {
    var left = h("div", {}, [h("div", { class: "cs-label", text: label }), note ? h("div", { class: "cs-note", text: note }) : null]);
    return h("div", { class: "cs-row" + (stack ? " stack" : "") }, [left, control]);
  }

  function seg(options, value, onPick) {
    var box = h("div", { class: "cs-seg", role: "group" });
    options.forEach(function (o) {
      var b = h("button", { type: "button", "aria-pressed": String(o[0] === value), text: o[1] });
      b.addEventListener("click", function () {
        box.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", "false"); });
        b.setAttribute("aria-pressed", "true");
        onPick(o[0]);
      });
      box.appendChild(b);
    });
    return box;
  }

  function toggle(value, onChange, label) {
    var b = h("button", { type: "button", class: "cs-switch", role: "switch", "aria-checked": String(!!value), "aria-label": label });
    b.addEventListener("click", function () {
      var on = b.getAttribute("aria-checked") !== "true";
      b.setAttribute("aria-checked", String(on));
      onChange(on);
    });
    return b;
  }

  // ---- panes ----
  function tutorPane() {
    var pane = h("div");
    var sel = h("select", { class: "cs-select", "aria-label": "Personality" });
    personalities.forEach(function (p) {
      var o = h("option", { value: p.id, text: p.label });
      if (p.id === prefs.personality) o.selected = true;
      sel.appendChild(o);
    });
    var note = h("div", { class: "cs-note", text: PERSONALITY_NOTES[prefs.personality] || "" });
    sel.addEventListener("change", function () { note.textContent = PERSONALITY_NOTES[sel.value] || ""; queueSave({ personality: sel.value }); });
    var pRow = h("div", { class: "cs-row" }, [h("div", {}, [h("div", { class: "cs-label", text: "Personality" }), note]), sel]);
    pane.appendChild(pRow);
    pane.appendChild(row("Answer length", "", seg([["short", "Short"], ["balanced", "Balanced"], ["detailed", "Detailed"]], prefs.length,
      function (v) { queueSave({ length: v }); })));
    pane.appendChild(row("Reading level", "", seg([["auto", "Auto"], ["simple", "Simple"], ["standard", "Standard"], ["advanced", "Advanced"]], prefs.reading_level,
      function (v) { queueSave({ reading_level: v }); })));
    pane.appendChild(row("Explain step by step", "For when a topic is new to you.", toggle(prefs.explain_simply,
      function (v) { queueSave({ explain_simply: v }); }, "Explain step by step")));
    var lang = h("input", { class: "cs-input", type: "text", maxlength: "40", placeholder: "Same as my message", value: prefs.language || "", "aria-label": "Reply language" });
    lang.style.width = "170px";
    lang.addEventListener("input", function () { queueSave({ language: lang.value }); });
    pane.appendChild(row("Reply language", "", lang));
    var ta = h("textarea", { class: "cs-text", maxlength: "300", placeholder: "Anything else about how you like answers written", "aria-label": "Style note" });
    ta.value = prefs.style_note || "";
    ta.addEventListener("input", function () { queueSave({ style_note: ta.value }); });
    pane.appendChild(row("Style note", "Affects tone only. It can't change your course's rules.", ta, true));
    return pane;
  }

  function appearancePane() {
    var pane = h("div"), T = window.ChronosTheme, ui = T.ui();
    pane.appendChild(row("Theme", "", seg([["light", "Light"], ["dark", "Dark"], ["system", "System"]], T.stored(),
      function (v) { T.setTheme(v); flash("Saved"); })));
    pane.appendChild(row("Text size", "", seg([["normal", "Normal"], ["large", "Large"]], ui.text === "large" ? "large" : "normal",
      function (v) { T.setUi({ text: v }); flash("Saved"); })));
    pane.appendChild(row("Reduce motion", "Turns off animations.", toggle(ui.motion === "reduce",
      function (v) { T.setUi({ motion: v ? "reduce" : "" }); flash("Saved"); }, "Reduce motion")));
    if (opts.tutor) {
      pane.appendChild(row("Enter sends", "Off: Enter adds a new line, Ctrl+Enter sends.", toggle(ui.enterSend !== false,
        function (v) { T.setUi({ enterSend: v }); flash("Saved"); }, "Enter sends")));
    }
    return pane;
  }

  function accountPane() {
    var pane = h("div");
    pane.appendChild(row("Signed in as", "", h("div", { class: "cs-kv", text: (me && me.email) || "" })));
    pane.appendChild(row("Role", "", h("div", { class: "cs-kv", text: (me && me.role) || "" })));
    pane.appendChild(row("System status", "See the current availability of Chronos services.", h("a", {
      class: "cs-btn", href: "/status", text: "View status"
    })));
    if (opts.onReplayTour) {
      pane.appendChild(row("How it works", "", h("button", { type: "button", class: "cs-btn", text: "Replay tour",
        onclick: function () { close(); opts.onReplayTour(); } })));
    }
    if (opts.onClearChats) {
      var clear = h("button", { type: "button", class: "cs-btn danger", text: "Delete all" });
      clear.addEventListener("click", async function () {
        if (!confirm("Delete every conversation in every course? This can't be undone.")) return;
        clear.disabled = true; flash("Deleting…");
        try { await opts.onClearChats(); flash("Deleted"); } catch (e) { flash("Couldn't delete everything", true); }
        clear.disabled = false;
      });
      pane.appendChild(row("Conversations", "", clear));
    }
    pane.appendChild(row("Sign out", "", h("button", { type: "button", class: "cs-btn", text: "Sign out",
      onclick: async function () {
        try { await Chronos.logout(); } catch (e) {}
        ChronosNav.go("/login?role=" + ((me && me.role) === "teacher" ? "teacher" : "student"), true);
      } })));
    return pane;
  }

  function devPane() {
    var pane = h("div");
    var dbg = localStorage.getItem("chronos-debug") === "1";
    pane.appendChild(row("Debug mode", "Red-glow chat with timings, retrieval and prompt. Also in the ✦ menu.", toggle(dbg,
      function (v) {
        try { localStorage.setItem("chronos-debug", v ? "1" : "0"); } catch (e) {}
        window.dispatchEvent(new CustomEvent("chronos-debug", { detail: v }));
        flash("Saved");
      }, "Debug mode")));
    var lag = h("select", { class: "cs-select", "aria-label": "Simulated latency" });
    [["0", "Off"], ["400", "+400 ms"], ["1500", "+1.5 s"], ["4000", "+4 s"]].forEach(function (o) {
      var opt = h("option", { value: o[0], text: o[1] });
      if (o[0] === String(localStorage.getItem("chronos-dev-lag") || "0")) opt.selected = true;
      lag.appendChild(opt);
    });
    lag.addEventListener("change", function () { try { localStorage.setItem("chronos-dev-lag", lag.value); } catch (e) {} flash("Saved"); });
    pane.appendChild(row("Simulate slow network", "Delays every API call.", lag));
    pane.appendChild(row("Clear local caches", "Session hint, cached role and auth config.", h("button", {
      type: "button", class: "cs-btn", text: "Clear", onclick: function () {
        ["chronos-hint", "chronos-auth-config"].forEach(function (k) { localStorage.removeItem(k); });
        try { sessionStorage.removeItem("chronos-role"); } catch (e) {}
        flash("Cleared");
      } })));
    var deployment = h("div", { class: "cs-pre", text: "Loading deployment status…", "aria-live": "polite" });
    var refresh = h("button", { type: "button", class: "cs-btn", text: "Refresh" });
    refresh.addEventListener("click", function () { loadDeployment(deployment, true); });
    pane.appendChild(row("Cloud deployment", "Latest build and serving revision. Refreshes while a rollout is active.", refresh));
    pane.appendChild(deployment);
    loadDeployment(deployment);
    pane.appendChild(row("Full status page", "Deployment details in their own page — handy on mobile.", h("a", {
      class: "cs-btn", href: "/status", text: "Open status page"
    })));
    var info = {
      uid: me && me.uid, role: me && me.role, api: Chronos.BASE || "(same origin)",
      hint: Chronos.hint(), preferences: prefs, ui: window.ChronosTheme.ui(),
    };
    pane.appendChild(row("Session", "", h("pre", { class: "cs-pre", text: JSON.stringify(info, null, 2) }), true));
    return pane;
  }

  function loadDeployment(box, manual) {
    clearTimeout(deploymentTimer);
    if (!box || !box.isConnected) return;
    if (manual) box.textContent = "Refreshing deployment status…";
    Chronos.apiFetch("/status/deployment", { cache: "no-store" }).then(function (res) {
      if (res.status === 401 || res.status === 403) throw new Error("forbidden");
      if (!res.ok) throw new Error("request-failed");
      return res.json();
    }).then(function (data) {
      if (!box.isConnected) return;
      if (data.status === "unavailable") {
        box.textContent = "Deployment details aren't configured for this environment.";
        return;
      }
      var lines = ["Overall: " + data.status];
      if (data.build) {
        lines.push("Latest build: " + data.build.state);
        if (data.build.started_at) lines.push("Started: " + data.build.started_at);
        if (data.build.revision) lines.push("Source revision: " + data.build.revision);
      }
      if (data.service) lines.push("Serving revision: " + (data.service.revision || "Deploying"));
      box.replaceChildren();
      box.appendChild(document.createTextNode(lines.join("\n")));
      [data.build, data.service].forEach(function (part) {
        if (!part || !part.console_url) return;
        box.appendChild(document.createElement("br"));
        var link = h("a", { href: part.console_url, target: "_blank", rel: "noopener", text: part === data.build ? "Open build in Cloud Console" : "Open service in Cloud Console" });
        box.appendChild(link);
      });
      if (["queued", "building", "deploying"].indexOf(data.status) !== -1) {
        deploymentTimer = setTimeout(function () { loadDeployment(box); }, 5000);
      }
    }).catch(function (e) {
      if (!box.isConnected) return;
      box.textContent = e && e.message === "forbidden" ? "You don't have access to deployment status."
        : "Couldn't reach the server.";
    });
  }

  function close() {
    if (!root) return;
    clearTimeout(deploymentTimer);
    root.remove(); root = null;
    document.removeEventListener("keydown", onKey);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function onKey(e) { if (e.key === "Escape") close(); }

  async function open(options) {
    if (root) return;
    opts = options || {};
    lastFocus = document.activeElement;
    try { me = await Chronos.me(); } catch (e) { me = null; }
    if (opts.tutor) {
      try {
        var d = await Chronos.apiJson("/me/preferences", undefined, "GET");
        prefs = d.preferences; personalities = d.personalities;
      } catch (e) { opts.tutor = false; }
    }
    var sections = [];
    if (opts.tutor) sections.push(["tutor", "Tutor", tutorPane]);
    sections.push(["appearance", "Appearance", appearancePane], ["account", "Account", accountPane]);
    if (me && me.is_dev) sections.push(["dev", "Developer", devPane]);

    var pane = h("div", { class: "cs-pane", role: "tabpanel" });
    var tabs = h("div", { class: "cs-tabs", role: "tablist", "aria-label": "Settings sections" });
    function show(id) {
      tabs.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.id === id)); });
      pane.replaceChildren(sections.filter(function (s) { return s[0] === id; })[0][2]());
    }
    sections.forEach(function (s) {
      var b = h("button", { type: "button", class: "cs-tab" + (s[0] === "dev" ? " cs-dev" : ""), role: "tab", "data-id": s[0], text: s[1] });
      b.addEventListener("click", function () { show(s[0]); });
      tabs.appendChild(b);
    });
    savedEl = h("span", { class: "cs-saved", role: "status", "aria-live": "polite" });
    var panel = h("div", { class: "cs-panel", role: "dialog", "aria-modal": "true", "aria-label": "Settings" }, [
      h("div", { class: "cs-head" }, [h("h2", { text: "Settings" }),
        h("button", { type: "button", class: "cs-x", "aria-label": "Close", text: "✕", onclick: close })]),
      h("div", { class: "cs-body" }, [tabs, pane]),
      h("div", { class: "cs-foot" }, [savedEl]),
    ]);
    root = h("div", { class: "cs-root" }, [panel]);
    root.addEventListener("mousedown", function (e) { if (e.target === root) close(); });
    document.body.appendChild(root);
    document.addEventListener("keydown", onKey);
    show(sections[0][0]);
    tabs.querySelector("button").focus();
  }

  window.ChronosSettings = { open: open, close: close };
})();
