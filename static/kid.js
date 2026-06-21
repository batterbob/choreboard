/* Chore Tracker kid page. No ES2022+ syntax for broad browser compatibility.
   Core idea: taps don't hit the API immediately. They show a 3-second "Undo"
   toast; the POST fires only when that window closes. Undo cancels it. This
   makes accidental taps on a slow tablet reversible. */
(function () {
  "use strict";

  var CHORE = window.CHORE || {};
  var DEBOUNCE_MS = 600;
  var UNDO_MS = 3000;

  /* ---- tiny helpers ---- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function body(extra) {
    var b = { slug: CHORE.slug };
    if (CHORE.today) { b.today = CHORE.today; } // inert in prod (CHORE_DEBUG off)
    for (var k in extra) { if (extra.hasOwnProperty(k)) { b[k] = extra[k]; } }
    return JSON.stringify(b);
  }

  function api(method, path, payload) {
    return fetch(path, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body(payload),
      keepalive: true // lets a pagehide-flush POST still go out
    });
  }

  function hm(mins) {
    var h = Math.floor(mins / 60), m = mins % 60;
    if (h && m) { return h + " hr " + m + " min"; }
    if (h) { return h + " hr"; }
    return m + " min";
  }

  /* ---- single-slot pending action (the undo window) ---- */
  var pending = null; // { commit: fn, undo: fn, timer: id }
  var toast = $("#toast"), toastMsg = $("#toast-msg"), toastUndo = $("#toast-undo");

  function showToast(msg) {
    toastMsg.textContent = msg;
    toast.hidden = false;
  }
  function hideToast() { toast.hidden = true; }

  function startPending(msg, commitFn, undoFn) {
    flushPending(); // any prior pending action commits immediately
    showToast(msg);
    var timer = setTimeout(function () {
      var p = pending; pending = null; hideToast();
      if (p) { p.commit(); }
    }, UNDO_MS);
    pending = { commit: commitFn, undo: undoFn, timer: timer };
  }

  function flushPending() {
    if (!pending) { return; }
    clearTimeout(pending.timer);
    var p = pending; pending = null; hideToast();
    p.commit();
  }

  if (toastUndo) {
    toastUndo.addEventListener("click", function () {
      if (!pending) { return; }
      clearTimeout(pending.timer);
      var p = pending; pending = null; hideToast();
      p.undo();
    });
  }
  // If the page is hidden/closed mid-window, commit rather than lose the action.
  window.addEventListener("pagehide", flushPending);

  /* ---- debounce guard (ignore a 2nd activation within 600ms) ---- */
  function debounced(el) {
    var now = Date.now();
    var last = Number(el.getAttribute("data-last") || 0);
    if (now - last < DEBOUNCE_MS) { return true; }
    el.setAttribute("data-last", String(now));
    return false;
  }

  /* ---- confetti ---- */
  var COLORS = ["#ff3366","#ff6600","#ffcc00","#33cc33","#3399ff","#cc33ff","#ff99cc"];
  var GOLD   = ["#FFD700","#FFA500","#FF6347","#FFD700"];

  function fireConfetti(big) {
    if (typeof confetti === "undefined") { return; }
    if (big) {
      // Wave 1 — massive center explosion
      confetti({ particleCount: 250, spread: 100, startVelocity: 60,
                 scalar: 1.5, ticks: 300, origin: { x: 0.5, y: 0.45 },
                 colors: COLORS });
      // Wave 2 — side cannons
      setTimeout(function () {
        confetti({ particleCount: 200, spread: 130, angle: 65, startVelocity: 65,
                   scalar: 1.3, origin: { x: 0, y: 0.75 }, colors: COLORS });
        confetti({ particleCount: 200, spread: 130, angle: 115, startVelocity: 65,
                   scalar: 1.3, origin: { x: 1, y: 0.75 }, colors: COLORS });
      }, 300);
      // Wave 3 — big gold stars from center
      setTimeout(function () {
        confetti({ particleCount: 100, spread: 90, startVelocity: 50,
                   scalar: 2.5, shapes: ["star"], ticks: 350,
                   origin: { x: 0.5, y: 0.5 }, colors: GOLD });
      }, 550);
      // Wave 4 — rain from both corners
      setTimeout(function () {
        confetti({ particleCount: 180, spread: 160, startVelocity: 25,
                   scalar: 1.2, ticks: 400, origin: { x: 0.2, y: -0.1 },
                   colors: COLORS, gravity: 1.2 });
        confetti({ particleCount: 180, spread: 160, startVelocity: 25,
                   scalar: 1.2, ticks: 400, origin: { x: 0.8, y: -0.1 },
                   colors: COLORS, gravity: 1.2 });
      }, 750);
      // Wave 5 — second cannon salvo
      setTimeout(function () {
        confetti({ particleCount: 150, spread: 120, angle: 70, startVelocity: 55,
                   scalar: 1.4, origin: { x: 0, y: 0.6 }, colors: GOLD });
        confetti({ particleCount: 150, spread: 120, angle: 110, startVelocity: 55,
                   scalar: 1.4, origin: { x: 1, y: 0.6 }, colors: GOLD });
      }, 1050);
      // Wave 6 — final straight-down shower
      setTimeout(function () {
        confetti({ particleCount: 300, spread: 180, startVelocity: 15,
                   scalar: 1.1, ticks: 500, origin: { x: 0.5, y: -0.05 },
                   colors: COLORS, gravity: 0.9 });
      }, 1350);
    } else {
      confetti({ particleCount: 75, spread: 70, startVelocity: 45,
                 scalar: 1.3, origin: { x: 0.5, y: 0.55 }, colors: COLORS });
    }
  }

  /* ============================ CHECKLIST ============================ */
  function lockRow(row) {
    row.classList.add("locked");
    row.setAttribute("disabled", "disabled");
    row.setAttribute("aria-disabled", "true");
  }
  function checkRow(row) {
    row.classList.add("checked");
    $(".checkbox", row).textContent = "✓";
  }
  function uncheckRow(row) {
    row.classList.remove("checked");
    $(".checkbox", row).textContent = "";
  }

  function onCheckRow(row) {
    if (row.classList.contains("locked") || debounced(row)) { return; }
    var kind = row.getAttribute("data-kind");
    var id = Number(row.getAttribute("data-id"));
    var label = $(".check-label", row).textContent;
    checkRow(row);
    startPending(
      label + " marked done — Undo",
      function () { commitChore(row, kind, id); },
      function () { uncheckRow(row); }
    );
  }

  function commitChore(row, kind, id) {
    lockRow(row);
    api("POST", "/api/chore/complete", { kind: kind, id: id })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) { return; }
        if (data.checklist_done) {
          var list = $("#checklist"), cel = $("#celebration");
          if (list) { list.hidden = true; }
          if (cel) { cel.hidden = false; }
          fireConfetti(true);   // big celebration — whole checklist done
        } else {
          fireConfetti(false);  // small burst — single chore done
        }
        if (data.bonus_reinstated) { markBonusReinstated(); }
      })
      .catch(function () {});
  }

  function markBonusReinstated() {
    var b = $("#makeup-banner");
    if (!b) { return; }
    b.className = "banner banner-green";
    b.innerHTML = '<div class="banner-big">✅ Bonus earned back — nice work!</div>';
  }

  /* ============================ LOG SECTIONS ============================ */
  function sectionState(section) {
    return {
      kind: section.getAttribute("data-kind"),
      unit: section.getAttribute("data-unit"),
      target: Number(section.getAttribute("data-target")),
      weekly: Number(section.getAttribute("data-weekly")),
      today: Number(section.getAttribute("data-today"))
    };
  }

  function unitSuffix(st) { return st.unit === "count" ? "" : " min"; }

  function renderTotals(section, weekly, today) {
    var st = sectionState(section);
    section.setAttribute("data-weekly", String(weekly));
    section.setAttribute("data-today", String(today));

    var wt = $(".weekly-text", section);
    if (st.unit === "hm") {
      wt.textContent = "This week: " + hm(weekly) + " / " + hm(st.target);
    } else {
      wt.textContent = "This week: " + weekly + " / " + st.target;
    }
    var pct = st.target > 0 ? Math.min(100, Math.floor(weekly / st.target * 100)) : 0;
    $(".bar-fill", section).style.width = pct + "%";
    $(".today-text", section).textContent = "Today: " + today + unitSuffix(st) + " logged";
  }

  function renderPace(section, data) {
    var pace = $(".pace-text", section);
    if (data.met) { pace.textContent = "Goal met! 🎉"; }
    else if (data.pace_state === "behind") {
      pace.textContent = "Need ~" + data.pace_needed + " min/day to finish by Sunday";
    } else if (data.pace_state === "no_days_left") {
      pace.textContent = "Last day — finish strong!";
    } else { pace.textContent = ""; }
  }

  function renderEntries(section, entries) {
    var box = $(".entries", section);
    if (!entries || !entries.length) { box.innerHTML = ""; return; }
    var html = "Today: ";
    entries.forEach(function (e) {
      html += '<span class="entry"><span class="entry-min">+' + e.minutes +
        '</span><button type="button" class="entry-x" data-id="' + e.id +
        '" aria-label="remove">✕</button></span>';
    });
    box.innerHTML = html;
  }

  function renderServer(section, data) {
    renderTotals(section, data.weekly, data.today_total);
    renderPace(section, data);
    renderEntries(section, data.entries);
    if (data.bonus_reinstated) { markBonusReinstated(); }
  }

  function addMinutes(section, minutes, sourceEl) {
    var st = sectionState(section);
    var kind = st.kind;
    // Optimistic update + confirmation flash.
    renderTotals(section, st.weekly + minutes, st.today + minutes);
    if (sourceEl) {
      sourceEl.classList.add("flash");
      setTimeout(function () { sourceEl.classList.remove("flash"); }, 250);
    }
    startPending(
      "+" + minutes + unitSuffix(st) + " added — Undo",
      function () {
        api("POST", "/api/log", { kind: kind, minutes: minutes })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (data) { if (data) { renderServer(section, data); } })
          .catch(function () {});
      },
      function () {
        var s2 = sectionState(section);
        renderTotals(section, s2.weekly - minutes, s2.today - minutes);
      }
    );
  }

  function removeEntry(section, id) {
    var kind = section.getAttribute("data-kind");
    api("DELETE", "/api/log", { kind: kind, id: id })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) { renderServer(section, data); } })
      .catch(function () {});
  }

  /* ============================ WIRE-UP ============================ */
  $all(".check-row").forEach(function (row) {
    row.addEventListener("click", function () { onCheckRow(row); });
  });

  $all(".log-card").forEach(function (section) {
    $all(".quick-btn", section).forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (debounced(btn)) { return; }
        addMinutes(section, Number(btn.getAttribute("data-min")), btn);
      });
    });
    var submit = $(".custom-submit", section);
    if (submit) {
      submit.addEventListener("click", function () {
        if (debounced(submit)) { return; }
        var input = $(".custom-input", section);
        var v = Math.floor(Number(input.value));
        if (!v || v < 1 || v > 600) { return; }
        input.value = "";
        addMinutes(section, v, submit);
      });
    }
    section.addEventListener("click", function (ev) {
      var x = ev.target;
      if (x && x.classList && x.classList.contains("entry-x")) {
        removeEntry(section, Number(x.getAttribute("data-id")));
      }
    });
  });
})();
