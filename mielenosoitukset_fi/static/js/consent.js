/*
 * Cookie consent for the optional Matomo tracker.
 *
 * Necessary cookies (login, security, language, theme) need no consent. Matomo
 * sets analytics cookies, so it loads only after the visitor presses "Hyväksy".
 * The choice is kept in the first-party cookie `moso_consent` for 180 days.
 * The bar is small, non-blocking and gives "Hyväksy" and "Hylkää" equal weight.
 * Pages that should never load the tracker simply do not set data-matomo.
 */
(function () {
  'use strict';
  if (window.mosoConsent) return;

  var COOKIE = 'moso_consent';
  var MAX_AGE = 60 * 60 * 24 * 180;
  var script = document.currentScript;
  var matomo = !!(script && script.getAttribute('data-matomo') === '1');
  var matomoSrc = (script && script.getAttribute('data-matomo-src')) || '/static/js/matomo.js';

  function read() {
    var m = document.cookie.match(/(?:^|; )moso_consent=(granted|denied)/);
    return m ? m[1] : null;
  }

  function write(value) {
    var secure = location.protocol === 'https:' ? '; Secure' : '';
    document.cookie = COOKIE + '=' + value + '; Max-Age=' + MAX_AGE + '; Path=/; SameSite=Lax' + secure;
  }

  function clearMatomoCookies() {
    document.cookie.split('; ').forEach(function (pair) {
      var name = pair.split('=')[0];
      if (/^_pk_|^mtm_/.test(name)) {
        document.cookie = name + '=; Max-Age=0; Path=/';
      }
    });
  }

  function loadMatomo() {
    if (!matomo || document.getElementById('matomo-tracker')) return;
    var s = document.createElement('script');
    s.id = 'matomo-tracker';
    s.async = true;
    s.src = matomoSrc;
    document.head.appendChild(s);
  }

  function closeBar() {
    var bar = document.getElementById('moso-consent');
    if (bar) bar.remove();
  }

  function choose(value) {
    write(value);
    closeBar();
    if (value === 'granted') loadMatomo();
    else clearMatomoCookies();
  }

  function injectStyle() {
    if (document.getElementById('moso-consent-style')) return;
    var st = document.createElement('style');
    st.id = 'moso-consent-style';
    st.textContent =
      '#moso-consent{position:fixed;left:12px;right:12px;bottom:12px;z-index:1080;max-width:560px;' +
      'margin:0 auto;padding:12px 14px;border:1px solid var(--product-border,#dce3ec);border-radius:12px;' +
      'background:var(--product-surface,#fff);color:var(--product-text,#172033);' +
      'box-shadow:0 6px 24px rgba(0,0,0,.18);font:400 14px/1.45 Inter,system-ui,sans-serif}' +
      '#moso-consent p{margin:0 0 10px}' +
      '#moso-consent a{color:var(--product-primary,#0033a0);text-decoration:underline}' +
      '#moso-consent .moso-consent-actions{display:flex;gap:8px}' +
      '#moso-consent button{flex:1 1 0;min-height:40px;padding:8px 12px;border-radius:8px;cursor:pointer;' +
      'font:600 14px Inter,system-ui,sans-serif;border:1px solid var(--product-primary,#0033a0);' +
      'background:var(--product-surface,#fff);color:var(--product-primary,#0033a0)}' +
      '#moso-consent button:hover{background:var(--product-primary-soft,#edf4ff)}' +
      '#moso-consent button:focus-visible{outline:3px solid var(--product-primary,#0033a0);outline-offset:2px}' +
      '@media (min-width:700px){#moso-consent{left:auto;right:16px;bottom:16px;margin:0}}';
    document.head.appendChild(st);
  }

  function showBar() {
    if (document.getElementById('moso-consent')) return;
    injectStyle();
    var bar = document.createElement('div');
    bar.id = 'moso-consent';
    bar.setAttribute('role', 'region');
    bar.setAttribute('aria-label', 'Evästeet');
    bar.innerHTML =
      '<p>Käytämme valinnaisia analytiikkaevästeitä palvelun kehittämiseen. Välttämättömät evästeet ovat aina käytössä. ' +
      '<a href="/privacy">Lue lisää</a></p>' +
      '<div class="moso-consent-actions">' +
      '<button type="button" data-choice="denied">Hylkää</button>' +
      '<button type="button" data-choice="granted">Hyväksy</button></div>';
    bar.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('button[data-choice]');
      if (btn) choose(btn.getAttribute('data-choice'));
    });
    document.body.appendChild(bar);
  }

  window.mosoConsent = {
    state: read,
    open: showBar,
    set: choose
  };

  if (!matomo) return;
  if (navigator.doNotTrack === '1') return;
  var state = read();
  if (state === 'granted') loadMatomo();
  else if (state === null) {
    if (document.body) showBar();
    else document.addEventListener('DOMContentLoaded', showBar);
  }
})();
