// Open PayPal in a small popup rather than navigating away, matching what
// the desktop app does when you press Donate.
//
// The links work without this file: they carry target="_blank" already, so
// with JavaScript off, or if a popup blocker refuses, the donation page
// still opens in a new tab. This only upgrades that to a sized window and
// keeps the site itself on screen behind it.
(function () {
  'use strict';

  var WIDTH = 500;
  var HEIGHT = 720;

  document.addEventListener('click', function (event) {
    var link = event.target.closest && event.target.closest('a[href*="paypal.me"]');
    if (!link) return;

    // Let modified clicks behave normally — middle-click and ctrl-click are
    // how people deliberately open things in their own tab.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;

    // Centre it on whichever screen the window is actually on.
    var left = window.screenX + Math.max(0, (window.outerWidth - WIDTH) / 2);
    var top = window.screenY + Math.max(0, (window.outerHeight - HEIGHT) / 2);

    var popup = window.open(
      link.href,
      'clips-studio-donate',
      'width=' + WIDTH + ',height=' + HEIGHT + ',left=' + Math.round(left) +
        ',top=' + Math.round(top) + ',resizable=yes,scrollbars=yes'
    );

    // Blocked, or the browser refused: fall through to the normal link so
    // the click is never simply swallowed.
    if (!popup) return;

    popup.focus();
    event.preventDefault();
  });
})();
