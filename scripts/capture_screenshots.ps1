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
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
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

# Bring it to the front and let the compositor finish drawing.
[void][WinCap]::ShowWindow($proc.MainWindowHandle, 9)   # SW_RESTORE
[void][WinCap]::SetForegroundWindow($proc.MainWindowHandle)
Start-Sleep -Milliseconds 900

$r = New-Object WinCap+RECT
[void][WinCap]::GetWindowRect($proc.MainWindowHandle, [ref]$r)
$w = $r.R - $r.L
$h = $r.B - $r.T

if ($w -lt 1366) {
    Write-Output "WARNING: the window is ${w}px wide. The Store wants 1366 or more."
    Write-Output "Maximise Clips Kitty and run this again."
}

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)

$stamp = Get-Date -Format "HHmmss"
$path = Join-Path $outDir "$Name-$stamp.png"
$bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

$kb = [math]::Round((Get-Item $path).Length / 1KB)
Write-Output "captured ${w}x${h}  ->  $path  (${kb} KB)"
