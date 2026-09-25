/** "Set up OpenRouter" from anywhere in the app.
 *
 *  Opens Settings with the AI card showing that provider's setup. It only
 *  opens the setup: nothing is switched on, and nothing is sent anywhere,
 *  until the user pastes their own key and picks a model there.
 */

const HINT = 'ai-setup'

export function openAISetup(provider = 'openrouter'): void {
  try {
    sessionStorage.setItem(HINT, provider)
  } catch {
    // storage unavailable: Settings still opens, just without the preselection
  }
  window.dispatchEvent(new Event('open-settings'))
}

/** The provider to preselect. Read without clearing, because React may run a
 *  state initializer twice (StrictMode does in development), and clearing on
 *  the first read left the second with nothing. Clear it after mounting. */
export function peekAISetupHint(): string {
  try {
    return sessionStorage.getItem(HINT) || ''
  } catch {
    return ''
  }
}

export function clearAISetupHint(): void {
  try {
    sessionStorage.removeItem(HINT)
  } catch {
    // nothing to clear
  }
}
