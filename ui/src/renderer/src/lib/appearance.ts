/** User-adjustable appearance (font, text size, text color).
 *
 * Implemented as runtime overrides of the design tokens, so every component
 * follows automatically. Persisted in localStorage — it's a per-user visual
 * preference, not project state.
 */

export interface Appearance {
  font: string
  scale: number // percent, 100 = default
  textColor: string
}

export const FONTS = [
  { label: 'Default (Segoe UI)', value: "'Segoe UI', system-ui, sans-serif" },
  { label: 'Arial', value: 'Arial, sans-serif' },
  { label: 'Verdana (high legibility)', value: 'Verdana, sans-serif' },
  { label: 'Tahoma', value: 'Tahoma, sans-serif' },
  { label: 'Trebuchet MS', value: "'Trebuchet MS', sans-serif" },
  { label: 'Georgia (serif)', value: 'Georgia, serif' },
  { label: 'Comic Sans MS (dyslexia-friendly)', value: "'Comic Sans MS', sans-serif" }
]

export const DEFAULT_APPEARANCE: Appearance = {
  font: FONTS[0].value,
  scale: 100,
  textColor: '#f1f5f9'
}

const KEY = 'clips-studio-appearance'

export function loadAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return DEFAULT_APPEARANCE
    return { ...DEFAULT_APPEARANCE, ...(JSON.parse(raw) as Partial<Appearance>) }
  } catch {
    return DEFAULT_APPEARANCE
  }
}

export function saveAppearance(a: Appearance): void {
  localStorage.setItem(KEY, JSON.stringify(a))
  applyAppearance(a)
}

export function applyAppearance(a: Appearance): void {
  const root = document.documentElement
  root.style.fontSize = `${a.scale}%`
  root.style.setProperty('--color-ink', a.textColor)
  document.body.style.fontFamily = a.font
}
