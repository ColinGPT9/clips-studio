/** Tiny UI translation layer.
 *
 *  - Dictionaries are keyed by the ENGLISH source string, so untranslated
 *    strings simply render in English (partial coverage degrades gracefully
 *    and `t()` calls keep the code readable).
 *  - The language defaults to the WINDOWS display language (Electron's
 *    navigator.language reflects the OS locale) and can be overridden in
 *    Settings. Switching reloads the window — no reactive plumbing needed.
 */
import ar from '../locales/ar.json'
import bn from '../locales/bn.json'
import de from '../locales/de.json'
import es from '../locales/es.json'
import fr from '../locales/fr.json'
import hi from '../locales/hi.json'
import id from '../locales/id.json'
import it from '../locales/it.json'
import ja from '../locales/ja.json'
import ko from '../locales/ko.json'
import pt from '../locales/pt.json'
import ru from '../locales/ru.json'
import th from '../locales/th.json'
import tl from '../locales/tl.json'
import tr from '../locales/tr.json'
import ur from '../locales/ur.json'
import vi from '../locales/vi.json'
import zh from '../locales/zh.json'

const LOCALES: Record<string, Record<string, string>> = {
  es, pt, hi, id, ja, ar, ru, de, fr, zh, vi, tl, tr, ur, bn, th, ko, it
}

/** code -> [English name, native name]. Shown as "English (Native)" wherever
 *  a language is picked, so a viewer who reads only one of the two can still
 *  find their language in the list. */
export const LANGUAGE_NAMES: Record<string, [string, string]> = {
  en: ['English', 'English'],
  es: ['Spanish', 'Español'],
  pt: ['Portuguese', 'Português'],
  fr: ['French', 'Français'],
  de: ['German', 'Deutsch'],
  hi: ['Hindi', 'हिन्दी'],
  id: ['Indonesian', 'Bahasa Indonesia'],
  ja: ['Japanese', '日本語'],
  ru: ['Russian', 'Русский'],
  ar: ['Arabic', 'العربية'],
  zh: ['Chinese (Simplified)', '简体中文'],
  vi: ['Vietnamese', 'Tiếng Việt'],
  tl: ['Filipino', 'Filipino'],
  tr: ['Turkish', 'Türkçe'],
  ur: ['Urdu', 'اردو'],
  bn: ['Bengali', 'বাংলা'],
  th: ['Thai', 'ไทย'],
  ko: ['Korean', '한국어'],
  it: ['Italian', 'Italiano']
}

/** "Spanish (Español)" — collapsed to one name when the two are identical. */
export function languageLabel(code: string): string {
  const pair = LANGUAGE_NAMES[code]
  if (!pair) return code
  const [english, native] = pair
  return english === native ? english : `${english} (${native})`
}

/** Interface languages: English plus every locale with a dictionary. */
export const APP_LANGUAGES: [string, string][] = [
  ['system', 'System (Windows) language'],
  ['en', languageLabel('en')],
  ...Object.keys(LOCALES)
    .map((code): [string, string] => [code, languageLabel(code)])
    .sort((a, b) => a[1].localeCompare(b[1]))
]

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
