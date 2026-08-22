# Capture the running Clips Kitty window for the Microsoft Store listing.
#
#     powershell -ExecutionPolicy Bypass -File scripts\capture_screenshots.ps1
#
# Partner Center requires at least one screenshot and recommends four or more,
# at 1366x768 or larger. This grabs the app's own window rather than the whole
# desktop, so nothing else on screen ends up in a public listing.
#
# It captures whatever page is open. Run it once per page: switch the app to
# the Queue, run it, switch to Clips, run it again. Each capture is numbered.
#
# Output: docs/store-screenshots/  (gitignored - these are large PNGs and
# Partner Center is where they belong, not the repository)

param(
    [string]$Name = "shot"
)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinCap {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  // The CLIENT rect is the app's own pixels. GetWindowRect includes the
  // invisible resize border and drop shadow Windows draws around a window,
  // and copying that region off the screen pulls in whatever is behind it —
  // the desktop, or another window. That is how wallpaper ended up down the
  // edge of a Store screenshot.
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
}
"@

$proc = Get-Process -Name "Clips Kitty" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } |
        Select-Object -First 1

if (-not $proc) {
    Write-Output "Clips Kitty is not running with a visible window."
    Write-Output "Start it, open the page you want, then run this again."
    exit 1
}

$outDir = Join-Path $PSScriptRoot "..\docs\store-screenshots"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# MAXIMISE, not restore. SW_RESTORE un-maximises an already-maximised
# window, which is how these were being shot as a small window floating on
# the desktop instead of filling the screen.
[void][WinCap]::ShowWindow($proc.MainWindowHandle, 3)   # SW_MAXIMIZE
[void][WinCap]::SetForegroundWindow($proc.MainWindowHandle)
Start-Sleep -Milliseconds 1200

# Client area only — see the note on GetClientRect above.
$c = New-Object WinCap+RECT
[void][WinCap]::GetClientRect($proc.MainWindowHandle, [ref]$c)
$origin = New-Object WinCap+POINT
[void][WinCap]::ClientToScreen($proc.MainWindowHandle, [ref]$origin)
$w = $c.R - $c.L
$h = $c.B - $c.T

if ($w -lt 1366) {
    Write-Output "WARNING: the window is ${w}px wide. The Store wants 1366 or more."
}

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($origin.X, $origin.Y, 0, 0, $bmp.Size)

$stamp = Get-Date -Format "HHmmss"
$path = Join-Path $outDir "$Name-$stamp.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

$kb = [math]::Round((Get-Item $path).Length / 1KB)
Write-Output "captured ${w}x${h}  ->  $path  (${kb} KB)"
