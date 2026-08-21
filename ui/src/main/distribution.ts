// Which distribution this copy came from, and the two things that depend on it.
//
// Clips Kitty ships from two places out of one codebase: the NSIS installer
// on GitHub/Hugging Face, and an MSIX package in the Microsoft Store. The
// binaries are identical. Two behaviours are not, and both are rules rather
// than preferences:
//
//   * Updates. A Store copy is updated by the Store. Downloading and running
//     the NSIS installer over the top of a package Windows manages would at
//     best do nothing and at worst leave two installs fighting.
//   * Paying. Store policy 10.8.2 allows a third-party payment API but is
//     explicit that "users may be directed to a browser to complete
//     registration or transactions". Handing PayPal to the system browser is
//     the route the policy names, and it means a certification tester never
//     has to assess a payment page hosted inside the app.
//
// Detection is the runtime fact rather than a build flag. `process.windowsStore`
// is set by Electron itself when the app is running from an appx/MSIX package,
// so it cannot disagree with reality — a Store build mislabelled at build time
// would still behave correctly, and an NSIS build can never accidentally claim
// to be one.

/** True when this copy is running from an MSIX/appx package. */
export function isMicrosoftStore(): boolean {
  // Typed as optional: `windowsStore` exists only on Windows builds of
  // Electron, and is simply absent elsewhere.
  return (process as NodeJS.Process & { windowsStore?: boolean }).windowsStore === true
}

/** Short name for logs and the Settings screen. */
export function distributionName(): string {
  return isMicrosoftStore() ? 'Microsoft Store' : 'standalone'
}
