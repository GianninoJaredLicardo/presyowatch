// Small enhancements for the auth forms: show/hide password, strength meter,
// confirm-password match, and a loading state on submit. Forms still work without it.
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-toggle-password]");
    if (!btn) return;
    var input = btn.parentElement.querySelector("input");
    var show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    btn.innerHTML = show ? '<i class="fa-regular fa-eye-slash"></i>' : '<i class="fa-regular fa-eye"></i>';
  });

  var LABELS = ["", "Weak", "Fair", "Good", "Strong"];

  function strength(pw) {
    if (!pw) return 0;
    var score = 0;
    if (pw.length >= 6) score++;
    if (pw.length >= 10) score++;
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
    if (/\d/.test(pw) && /[^A-Za-z0-9]/.test(pw)) score++;
    return Math.max(1, score);
  }

  function refresh(form) {
    var pw = form.querySelector("[data-strength]");
    var meter = form.querySelector("[data-meter]");
    if (pw && meter) {
      var level = strength(pw.value);
      meter.hidden = level === 0;
      meter.dataset.level = level;
      meter.querySelector("em").textContent = LABELS[level];
    }
    var confirm = form.querySelector("[data-confirm]");
    if (pw && confirm) {
      var match = confirm.value !== "" && confirm.value === pw.value;
      confirm.closest(".fl-field").classList.toggle("is-match", match);
      confirm.setCustomValidity(confirm.value && !match ? "Passwords do not match." : "");
    }
  }

  document.addEventListener("input", function (e) {
    var form = e.target.form;
    if (form && form.hasAttribute("data-modern-form")) refresh(form);
  });

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-modern-form")) return;
    var btn = form.querySelector(".btn-submit");
    if (btn) btn.classList.add("is-loading");
  });

  // Coming back with the browser's Back button should not leave a spinning button.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".btn-submit.is-loading").forEach(function (b) { b.classList.remove("is-loading"); });
  });
})();

// Styled dropdown for <select data-fancy> (used for Market). The real <select> stays in the
// page underneath, so required-field checks and form submission work exactly as before.
(function () {
  "use strict";

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  document.querySelectorAll("select[data-fancy]").forEach(function (select) {
    var field = select.closest(".fl-field");
    var icon = select.dataset.optionIcon || "circle";
    var options = Array.prototype.filter.call(select.options, function (o) { return o.value !== ""; })
      .map(function (o) { return { value: o.value, text: o.textContent.trim(), sub: o.dataset.sub || "" }; });
    var searchable = options.length > 5;

    var trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "fx-trigger";
    trigger.tabIndex = -1;
    trigger.setAttribute("aria-hidden", "true");

    var panel = document.createElement("div");
    panel.className = "fx-panel";
    panel.hidden = true;
    panel.innerHTML =
      (searchable ? '<div class="fx-search"><i class="fa-solid fa-magnifying-glass"></i>' +
        '<input type="text" placeholder="Search…" autocomplete="off" aria-label="Search options"></div>' : "") +
      '<div class="fx-list" role="listbox"></div>';
    field.appendChild(trigger);
    field.appendChild(panel);

    var search = panel.querySelector(".fx-search input");
    var list = panel.querySelector(".fx-list");
    var shown = [], active = -1;

    function render() {
      var q = search ? search.value.trim().toLowerCase() : "";
      shown = options.filter(function (o) { return !q || (o.text + " " + o.sub).toLowerCase().indexOf(q) >= 0; });
      active = Math.max(0, shown.findIndex(function (o) { return o.value === select.value; }));
      list.innerHTML = shown.length ? shown.map(function (o, i) {
        var picked = o.value === select.value;
        return '<div class="fx-opt' + (picked ? " picked" : "") + (i === active ? " active" : "") +
          '" role="option" aria-selected="' + picked + '" data-i="' + i + '">' +
          '<span class="fx-ico"><i class="fa-solid fa-' + esc(icon) + '"></i></span>' +
          '<span class="fx-txt"><b>' + esc(o.text) + "</b>" + (o.sub ? "<small>" + esc(o.sub) + "</small>" : "") + "</span>" +
          '<i class="fa-solid fa-check fx-check"></i></div>';
      }).join("") : '<div class="fx-empty">No matches.</div>';
    }
    function open() {
      if (!panel.hidden) return;
      if (search) search.value = "";
      render();
      panel.hidden = false;
      field.classList.add("fx-open");
      (search || select).focus();
      var cur = list.querySelector(".fx-opt.active");
      if (cur) cur.scrollIntoView({ block: "nearest" });
    }
    function close(refocus) {
      if (panel.hidden) return;
      panel.hidden = true;
      field.classList.remove("fx-open");
      if (refocus) select.focus();
    }
    function pick(o) {
      select.value = o.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      close(true);
    }
    function move(step) {
      var opts = list.querySelectorAll(".fx-opt");
      if (!opts.length) return;
      active = (active + step + opts.length) % opts.length;
      opts.forEach(function (el, i) { el.classList.toggle("active", i === active); });
      opts[active].scrollIntoView({ block: "nearest" });
    }
    function keys(e) {
      if (e.key === "ArrowDown") { e.preventDefault(); if (panel.hidden) open(); else move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (!panel.hidden) move(-1); }
      else if (e.key === "Enter" || (e.key === " " && e.target === select)) {
        e.preventDefault();
        if (panel.hidden) open(); else if (shown[active]) pick(shown[active]);
      }
      else if (e.key === "Escape" && !panel.hidden) { e.preventDefault(); close(true); }
      else if (e.key === "Tab") close(false);
    }

    trigger.addEventListener("click", function () { if (panel.hidden) open(); else close(true); });
    select.addEventListener("mousedown", function (e) { e.preventDefault(); open(); });
    select.addEventListener("keydown", keys);
    if (search) {
      search.addEventListener("input", render);
      search.addEventListener("keydown", keys);
    }
    list.addEventListener("mousedown", function (e) {
      var el = e.target.closest(".fx-opt");
      if (el) { e.preventDefault(); pick(shown[+el.dataset.i]); }
    });
    document.addEventListener("mousedown", function (e) { if (!field.contains(e.target)) close(false); });
  });
})();
