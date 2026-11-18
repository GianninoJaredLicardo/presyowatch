/*
 * Floating notifications:
 * - the bell opens a floating panel with the latest notifications (no page change),
 * - new notifications pop up as floating cards while the app is open (checked every 25 s),
 * - the red count on the bell stays up to date.
 */
(function () {
  var bell = document.getElementById("notifBell");
  var panel = document.getElementById("notifPanel");
  if (!bell || !panel) return;

  var list = document.getElementById("notifList");
  var count = bell.querySelector(".count");
  var FEED = bell.dataset.feed;
  var READ_ALL = bell.dataset.readAll;
  var SEEN_KEY = "pw-notif-seen-" + bell.dataset.user;   // newest notification already announced
  var POLL_MS = 25000;
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var items = [];

  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  function setCount(n) {
    count.hidden = !n;
    count.textContent = n > 99 ? "99+" : String(n);
    bell.classList.toggle("has-unread", !!n);
    bell.setAttribute("aria-label", "Notifications" + (n ? " (" + n + " unread)" : ""));
  }

  function render() {
    if (!items.length) {
      list.innerHTML = '<div class="notif-panel-empty"><i class="fa-regular fa-bell"></i>You’re all caught up.</div>';
      return;
    }
    list.innerHTML = items.slice(0, 8).map(function (n) {
      return '<a class="notif-item tone-' + esc(n.tone) + (n.unread ? " unread" : "") + '" href="' + esc(n.url) + '">'
        + '<span class="notif-icon"><i class="fa-solid fa-' + esc(n.icon) + '"></i></span>'
        + '<span class="notif-body"><b>' + esc(n.title) + "</b>"
        + (n.message ? '<span class="notif-msg">' + esc(n.message) + "</span>" : "")
        + "<small>" + esc(n.when) + "</small></span>"
        + (n.unread ? '<span class="notif-dot" aria-label="Unread"></span>' : "") + "</a>";
    }).join("");
  }

  // ---- floating pop-ups for new notifications ----
  function stack() {
    var s = document.querySelector(".toast-stack");
    if (!s) {
      s = document.createElement("div");
      s.className = "toast-stack";
      s.setAttribute("aria-live", "polite");
      document.body.appendChild(s);
    }
    return s;
  }

  function dismiss(t) {
    if (t.classList.contains("is-leaving")) return;
    t.classList.add("is-leaving");
    t.addEventListener("animationend", function () {
      var s = t.parentElement;
      t.remove();
      if (s && !s.children.length) s.remove();
    }, { once: true });
  }

  var TONES = { good: "success", warn: "warning", bad: "danger", info: "info" };
  function popup(n) {
    var t = document.createElement("a");
    t.href = n.url;
    t.className = "pw-toast pw-toast-" + (TONES[n.tone] || "info") + " pw-toast-live";
    t.setAttribute("role", "status");
    t.style.setProperty("--toast-time", "8000ms");
    t.innerHTML = '<span class="pw-toast-icon"><i class="fa-solid fa-' + esc(n.icon) + '"></i></span>'
      + '<div class="pw-toast-body"><div class="pw-toast-kicker"><i class="fa-solid fa-bell"></i> New notification</div>'
      + '<div class="pw-toast-title">' + esc(n.title) + "</div>"
      + (n.message ? '<div class="pw-toast-msg">' + esc(n.message) + "</div>" : "") + "</div>"
      + '<button type="button" class="pw-toast-close" aria-label="Dismiss"><i class="fa-solid fa-xmark"></i></button>'
      + '<span class="pw-toast-bar"></span>';
    t.querySelector(".pw-toast-close").addEventListener("click", function (e) { e.preventDefault(); dismiss(t); });
    t.querySelector(".pw-toast-bar").addEventListener("animationend", function () { dismiss(t); });
    stack().appendChild(t);
    bell.classList.remove("ring");
    void bell.offsetWidth;
    bell.classList.add("ring");
  }

  function announce() {
    if (!items.length) return;
    var newest = Math.max.apply(null, items.map(function (n) { return n.id; }));
    var seen = parseInt(localStorage.getItem(SEEN_KEY) || "0", 10);
    if (seen) {
      items.filter(function (n) { return n.unread && n.id > seen; })
        .slice(0, 3).reverse().forEach(popup);
    }
    // First visit on this device: remember where we are without popping up old notifications.
    if (newest > seen) localStorage.setItem(SEEN_KEY, String(newest));
  }

  function load(showPopups) {
    return fetch(FEED, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        items = data.items || [];
        setCount(data.unread || 0);
        if (!panel.hidden) render();
        if (showPopups) announce();
      })
      .catch(function () {});
  }

  // ---- the floating panel ----
  function open() {
    panel.hidden = false;
    bell.setAttribute("aria-expanded", "true");
    requestAnimationFrame(function () { panel.classList.add("is-open"); });
    render();
    load(false);
  }
  function close() {
    panel.classList.remove("is-open");
    bell.setAttribute("aria-expanded", "false");
    setTimeout(function () { if (!panel.classList.contains("is-open")) panel.hidden = true; }, 180);
  }

  bell.addEventListener("click", function (e) {
    e.preventDefault();
    if (panel.hidden) open(); else close();
  });
  document.addEventListener("click", function (e) {
    if (!panel.hidden && !panel.contains(e.target) && !bell.contains(e.target)) close();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !panel.hidden) { close(); bell.focus(); }
  });

  document.getElementById("notifReadAll").addEventListener("click", function () {
    var headers = { "X-Requested-With": "fetch" };
    if (csrfMeta) headers["X-CSRF-Token"] = csrfMeta.content;
    fetch(READ_ALL, { method: "POST", headers: headers, credentials: "same-origin" }).then(function () {
      items.forEach(function (n) { n.unread = false; });
      setCount(0);
      render();
    });
  });

  // ---- keep it live ----
  load(true);
  setInterval(function () { if (!document.hidden) load(true); }, POLL_MS);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(true); });
})();
