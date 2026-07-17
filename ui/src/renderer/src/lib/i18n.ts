/** Tiny UI translation layer.
 *
 *  - Dictionaries are keyed by the ENGLISH source string, so untranslated
 *    strings simply render in English (partial coverage degrades gracefully
 *    and `t()` calls keep the code readable).
 *  - The language defaults to the WINDOWS display language (Electron's
 *    navigator.language reflects the OS locale) and can be overridden in
 *    Settings. Switching reloads the window — no reactive plumbing needed.
 */
import es from '../locales/es.json'
import hi from '../locales/hi.json'
import id from '../locales/id.json'
import ja from '../locales/ja.json'
import pt from '../locales/pt.json'

const LOCALES: Record<string, Record<string, string>> = { es, pt, hi, id, ja }

export const APP_LANGUAGES = [
  ['system', 'System (Windows) language'],
  ['en', 'English'],
  ['es', 'Español'],
  ['pt', 'Português'],
  ['hi', 'हिन्दी'],
  ['id', 'Bahasa Indonesia'],
  ['ja', '日本語']
] as const

const STORE_KEY = 'app-language'

export function appLanguageSetting(): string {
  return localStorage.getItem(STORE_KEY) ?? 'system'
}

/** The locale actually in effect (resolving 'system' to the OS language). */
export function activeLocale(): string {
  const setting = appLanguageSetting()
  if (setting !== 'system') return setting
  const sys = (navigator.language || 'en').slice(0, 2).toLowerCase()
  return sys in LOCALES ? sys : 'en'
}

/** Switch language IN PLACE — no reload: the dictionary is swapped and an
 *  event tells App to re-render the tree, so the user stays exactly where
 *  they are (e.g. mid-way through the Settings page). */
export function setAppLanguage(code: string): void {
  localStorage.setItem(STORE_KEY, code)
  dict = LOCALES[activeLocale()]
  window.dispatchEvent(new CustomEvent('app-language-changed'))
}

let dict = LOCALES[activeLocale()]

/** Translate a UI string; falls back to the English source text. */
export function t(s: string): string {
  return (dict && dict[s]) || s
}
