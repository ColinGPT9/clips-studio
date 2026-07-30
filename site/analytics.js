/* Google Analytics 4 for the website.
 *
 * ─────────────────────────────────────────────────────────────────────────
 *  PASTE YOUR MEASUREMENT ID BELOW. It is the only thing to change here.
 *
 *  Where to find it: analytics.google.com -> Admin -> Data streams -> your
 *  web stream. It looks like G-ABC123XYZ4. Not the "GA4 property ID" (a
 *  number) and not a UA- id, which is the retired version of Analytics.
 * ─────────────────────────────────────────────────────────────────────────
 *
 * Until an ID is set this file does nothing at all: no script is fetched, no
 * cookie is written. That way it can sit in the repository, and on the
 * Hugging Face mirror, without quietly tracking anyone.
 *
 * This measures the WEBSITE only. The desktop app has no telemetry and this
 * changes nothing about that.
 */

var MEASUREMENT_ID = ''

;(function () {
  if (!MEASUREMENT_ID) return // not configured yet: stay inert

  // Don't count yourself. Previewing the site locally would otherwise show up
  // as real traffic, and on a site with few visitors a handful of your own
  // page loads is enough to make the numbers meaningless.
  var host = location.hostname
  if (
    location.protocol === 'file:' ||
    host === 'localhost' ||
    host === '127.0.0.1' ||
    host === '' ||
    host.endsWith('.local')
  ) {
    return
  }

  var s = document.createElement('script')
  s.async = true
  s.src = 'https://www.googletagmanager.com/gtag/js?id=' + encodeURIComponent(MEASUREMENT_ID)
  document.head.appendChild(s)

  window.dataLayer = window.dataLayer || []
  function gtag() {
    window.dataLayer.push(arguments)
  }
  window.gtag = gtag
  gtag('js', new Date())
  gtag('config', MEASUREMENT_ID, {
    // Shortens the visitor's IP before it is stored. GA4 does this by
    // default; setting it explicitly means the intent survives anyone
    // reading this file later.
    anonymize_ip: true
  })

  // Named events for the three things worth knowing, because "visits" on its
  // own says very little. What matters is how many of those visitors went on
  // to do something: a sponsor asks "how many people actually download it?",
  // and "8% of visitors clicked through to the release" is an answer.
  //
  // GA4's enhanced measurement already logs outbound clicks generically.
  // These are named, so they can be read straight off the Events report
  // instead of being dug out of a list of every external link on the site.
  document.addEventListener(
    'click',
    function (e) {
      var a = e.target && e.target.closest ? e.target.closest('a[href]') : null
      if (!a) return
      var href = a.getAttribute('href') || ''

      // Parse it rather than search it. The first version of this asked
      // whether the string CONTAINED "github.com" and "/releases", which
      // "https://example.com/?ref=github.com/releases" satisfies happily.
      // Only the host decides where a link goes, so only the host is asked.
      var url
      try {
        // Relative to this page, so "twitch.html" resolves to our own origin
        // and drops out as internal on the next line.
        url = new URL(href, location.href)
      } catch (e) {
        return // "javascript:", "#", or something malformed
      }
      if (url.origin === location.origin) return // internal navigation

      var host = url.hostname.toLowerCase()
      var onHost = function (domain) {
        // Label boundary: accepts www.github.com, rejects github.com.evil.net
        return host === domain || host.endsWith('.' + domain)
      }
      var isGitHub = onHost('github.com')
      var isPayPal = onHost('paypal.me') || onHost('paypal.com')

      // A whole path SEGMENT, so /ColinGPT9/clips-studio/releases matches and
      // so does /releases/latest, while a repository called "releases-notes"
      // does not. Safe to match loosely here: the host is already confirmed.
      var isReleases = /\/releases(\/|$)/.test(url.pathname)

      var name =
        isGitHub && isReleases
          ? 'download_click'
          : isPayPal
            ? 'donate_click'
            : isGitHub
              ? 'github_click'
              : null
      if (!name) return
      gtag('event', name, {
        // Which page sent them. The Twitch and Kick pages exist to be found
        // by search, so knowing which one converts is the point of having
        // written them.
        page: location.pathname,
        link_url: href
      })
    },
    // Capture phase: the click still navigates away immediately, and a
    // listener that waits for bubbling can lose the event to the unload.
    true
  )
})()
