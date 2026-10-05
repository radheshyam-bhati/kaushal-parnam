/* ==========================================================================
   Kaushal Parinam front end.

   HTMX gives interactivity without a framework bundle, which matters because
   trainee and employer pages must work on a feature phone (docs/04-UI-UX-BRIEF.md
   §3, docs/02-TRD.md §3). Charts are Chart.js, loaded only on dashboard pages.

   docs/04-UI-UX-BRIEF.md §9: no infinite spinners. Loading uses skeleton
   screens plus a timeout message instead.
   ========================================================================== */

(function () {
  'use strict';

  var LOADING_TIMEOUT_MS = 5000;

  /* ---------- Skeleton screens with a timeout fallback (APP-FLOW J-11) -------- */
  function armLoading(el) {
    var target = document.querySelector(el.getAttribute('hx-target') || 'body');
    if (!target) return;
    var timer = setTimeout(function () {
      var note = document.querySelector('[data-loading-timeout]');
      if (!note) {
        note = document.createElement('p');
        note.className = 'helptext';
        note.setAttribute('data-loading-timeout', '');
        note.setAttribute('role', 'status');
        note.textContent = 'Data is taking a while. Refresh or try again?';
        target.appendChild(note);
      }
    }, LOADING_TIMEOUT_MS);
    el.addEventListener('htmx:afterRequest', function () { clearTimeout(timer); });
    el.addEventListener('htmx:responseError', function () { clearTimeout(timer); });
  }

  /* ---------- Live region announcements for screen readers ---------- */
  var liveRegion = document.getElementById('live-region');
  function announce(message) {
    if (!liveRegion) return;
    liveRegion.textContent = '';
    setTimeout(function () { liveRegion.textContent = message; }, 50);
  }

  document.body.addEventListener('htmx:beforeRequest', function (evt) {
    var el = evt.detail.elt;
    if (el.getAttribute('hx-boost') || el.classList.contains('needs-skeleton')) armLoading(el);
  });

  document.body.addEventListener('htmx:afterSwap', function (evt) {
    announce('Updated.');
    var focusTarget = evt.detail.elt.getAttribute('data-focus-after-swap');
    if (focusTarget) {
      var el = document.querySelector(focusTarget);
      if (el) el.focus();
    }
  });

  document.body.addEventListener('htmx:responseError', function (evt) {
    announce('That did not load. Try again.');
    var box = document.getElementById('request-error');
    if (box) {
      box.hidden = false;
      box.textContent = 'We could not load that. Check your connection and try again.';
    }
  });

  /* ---------- Language class on <body> so Devanagari fonts load (§3) -------- */
  var lang = document.documentElement.lang || 'en';
  document.body.classList.add('language-' + lang.slice(0, 2));

  /* ---------- Minimum-group-size masking for small cells (F-10) ------------ */
  document.querySelectorAll('[data-k]').forEach(function (el) {
    var count = parseInt(el.getAttribute('data-count') || '0', 10);
    var k = parseInt(el.getAttribute('data-k') || '5', 10);
    if (count < k) {
      el.textContent = 'Data withheld';
      el.setAttribute('title', 'This group has fewer than ' + k + ' people, so it is not shown.');
    }
  });

  /* ---------- Grade filter is a plain GET so the URL is bookmarkable ------ */
  document.querySelectorAll('[data-auto-submit]').forEach(function (field) {
    field.addEventListener('change', function () { field.form.submit(); });
  });
})();