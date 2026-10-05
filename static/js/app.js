/* ==========================================================================
   Kaushal Parinam (कौशल परिणाम) — Front-End Controller
   Modern civic intelligence interactions, accessible loading, HTMX integration
   ========================================================================== */

(function () {
  'use strict';

  var LOADING_TIMEOUT_MS = 6000;

  /* ---------- Language Switching Helper ---------- */
  window.setLanguage = function (langCode) {
    document.body.classList.remove('language-en', 'language-mr', 'language-hi');
    document.body.classList.add('language-' + langCode);
    document.documentElement.lang = langCode;

    // Update active states on switcher buttons
    document.querySelectorAll('.civic-lang-btn').forEach(function (btn) {
      if (btn.textContent.toLowerCase().includes(langCode) ||
          (langCode === 'mr' && btn.textContent.includes('मराठी')) ||
          (langCode === 'hi' && btn.textContent.includes('हिन्दी')) ||
          (langCode === 'en' && btn.textContent.includes('English'))) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // Announce for accessibility screen readers
    announce('Language switched to ' + langCode);
  };

  /* ---------- Skeleton screens with a timeout fallback (APP-FLOW J-11) -------- */
  function armLoading(el) {
    var target = document.querySelector(el.getAttribute('hx-target') || 'body');
    if (!target) return;
    var timer = setTimeout(function () {
      var note = document.querySelector('[data-loading-timeout]');
      if (!note) {
        note = document.createElement('div');
        note.className = 'alert alert-warning';
        note.setAttribute('data-loading-timeout', '');
        note.setAttribute('role', 'status');
        note.innerHTML = '<span class="alert-icon">!</span><span>Data query is taking longer than usual. Refresh or check connection.</span>';
        target.prepend(note);
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
    if (el && (el.getAttribute('hx-boost') || el.classList.contains('needs-skeleton'))) {
      armLoading(el);
    }
  });

  document.body.addEventListener('htmx:afterSwap', function (evt) {
    announce('View updated successfully.');
    var focusTarget = evt.detail.elt.getAttribute('data-focus-after-swap');
    if (focusTarget) {
      var el = document.querySelector(focusTarget);
      if (el) el.focus();
    }
  });

  document.body.addEventListener('htmx:responseError', function (evt) {
    announce('Network request error.');
    var box = document.getElementById('request-error');
    if (box) {
      box.hidden = false;
      box.innerHTML = '<span class="alert-icon">!</span><span>Could not complete request. Please verify connection and retry.</span>';
    }
  });

  /* ---------- Auto-submit on filter change (plain bookmarkable GET) ---------- */
  document.querySelectorAll('[data-auto-submit]').forEach(function (field) {
    field.addEventListener('change', function () {
      if (field.form) {
        field.form.submit();
      }
    });
  });

  /* ---------- Minimum-group-size masking for small cells (F-10) ------------ */
  document.querySelectorAll('[data-k]').forEach(function (el) {
    var count = parseInt(el.getAttribute('data-count') || '0', 10);
    var k = parseInt(el.getAttribute('data-k') || '5', 10);
    if (count < k) {
      el.textContent = 'Data withheld';
      el.setAttribute('title', 'This group has fewer than ' + k + ' people, so it is masked for statistical privacy.');
    }
  });

  /* ---------- Close Details dropdowns when clicking outside ---------- */
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details[open]').forEach(function (details) {
      if (!details.contains(e.target) && !details.classList.contains('footer-disclosure-card')) {
        details.removeAttribute('open');
      }
    });
  });
})();