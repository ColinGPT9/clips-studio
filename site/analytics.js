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
})()
