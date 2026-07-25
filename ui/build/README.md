# Build resources

electron-builder picks files up from this folder by name. Nothing here is
code — it's the artwork and metadata that make the packaged app look like a
real product rather than a generic Electron shell.

## icon.ico — the one thing still missing

Drop a Windows icon here as `icon.ico` and the build uses it automatically:
no config change needed. Without it the build logs

    • default Electron icon is used  reason=application icon is not set

and the installer, the desktop shortcut, the Start Menu entry and the
taskbar all show the generic Electron atom. It is the first thing a creator
sees.

Requirements:

- **`icon.ico`**, containing at least a **256×256** image. electron-builder
  rejects anything smaller as the largest size.
- Multi-resolution is best (16, 24, 32, 48, 64, 128, 256) so it stays sharp
  everywhere from the taskbar to the desktop.
- Square, with a transparent background.

The app's own palette, if the icon should match the interface: navy
`#0A1628` on the dark side, sky blue `#38BDF8` as the accent.

## A note on the .exe's embedded icon

`signAndEditExecutable: false` is set in `electron-builder.yml`, because the
tool electron-builder uses for signing and for embedding icon/version
metadata ships with macOS symlinks that an ordinary Windows account cannot
extract. While that setting stands, the installer and shortcuts get the icon
but the app `.exe` itself does not carry it internally.

To get that too: turn on Windows Developer Mode (Settings → System → For
developers), then remove the `signAndEditExecutable` line.
