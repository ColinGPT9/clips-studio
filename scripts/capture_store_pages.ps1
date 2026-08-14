# Capture Clips Studio's people-free pages for the Microsoft Store listing.
#
#     powershell -ExecutionPolicy Bypass -File scripts\capture_store_pages.ps1
#
# ONLY visits Models and Settings. Every other page shows the library: clip
# thumbnails on Clip Studio and Creators, and video titles and channel names on
# Dashboard and Queue. None of that belongs in a public listing - the footage
# is someone else's, and neither their face nor their channel name is ours to
# advertise with.
#
# Pass -All to include Dashboard and Queue, for when there is finally footage
# that can be shown.
#
# Uses PrintWindow rather than a screen grab. A screen grab copies whatever
# pixels are at the window's coordinates, so if the app loses focus for even a
# moment the image fills with the desktop behind it - file paths, other
# applications, whatever happens to be open. PrintWindow asks the window to
# draw itself into a bitmap instead, so nothing else can appear in the frame.
# PW_RENDERFULLCONTENT (0x2) is the flag that makes it work for the
# hardware-composited surfaces Chromium uses.
#
# Output: docs/store-screenshots/ (gitignored). Check every image before
# uploading anyway.

param([switch]$All)

Add-Type -AssemblyName System.Drawing
# -ReferencedAssemblies: the inline C# below uses System.Drawing.Bitmap, and
# loading the assembly into PowerShell does not put it on the compiler's path.
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public class Shot {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }

  public static Bitmap Grab(IntPtr h) {
    RECT r; GetWindowRect(h, out r);
    Bitmap bmp = new Bitmap(r.R - r.L, r.B - r.T);
    using (Graphics g = Graphics.FromImage(bmp)) {
      IntPtr dc = g.GetHdc();
      PrintWindow(h, dc, 0x2);   // PW_RENDERFULLCONTENT
      g.ReleaseHdc(dc);
    }
    return bmp;
  }
}
"@
$DOWN = 0x0002; $UP = 0x0004

# Sidebar offsets from the window's top-left, on a 1440x900 window. The
# sidebar is a fixed width, so these hold as the window grows.
$pages = @(
  @{ n = "3-models";   y = 325 },
  @{ n = "4-settings"; y = 373 }
)
if ($All) {
  $pages = @(
    @{ n = "1-dashboard"; y = 133 },
    @{ n = "2-queue";     y = 181 }
  ) + $pages
}
$sidebarX = 110

$proc = Get-Process -Name "Clips Studio" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } |
        Select-Object -First 1
if (-not $proc) { Write-Output "Clips Studio is not running."; exit 1 }
$h = $proc.MainWindowHandle

$outDir = Join-Path $PSScriptRoot "..\docs\store-screenshots"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
Get-ChildItem $outDir -Filter *.png -ErrorAction SilentlyContinue | Remove-Item -Force

[void][Shot]::ShowWindow($h, 9)
[void][Shot]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 900

$r = New-Object Shot+RECT
[void][Shot]::GetWindowRect($h, [ref]$r)
Write-Output "window $($r.R - $r.L)x$($r.B - $r.T)"
if (($r.R - $r.L) -lt 1366) { Write-Output "NOTE: under the Store 1366px minimum - maximise and re-run" }

foreach ($p in $pages) {
    # Re-assert focus before every click; a click on an unfocused window is
    # consumed activating it and never reaches the button.
    [void][Shot]::SetForegroundWindow($h)
    Start-Sleep -Milliseconds 250
    [void][Shot]::SetCursorPos($r.L + $sidebarX, $r.T + $p.y)
    Start-Sleep -Milliseconds 200
    [Shot]::mouse_event($DOWN, 0, 0, 0, 0)
    [Shot]::mouse_event($UP, 0, 0, 0, 0)
    Start-Sleep -Milliseconds 2000     # render, plus any fetch the page makes

    $bmp = [Shot]::Grab($h)
    $path = Join-Path $outDir "$($p.n).png"
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output "  $($p.n).png"
}

Write-Output ""
Write-Output "Captured to docs\store-screenshots\."
if (-not $All) {
  Write-Output "Only Models and Settings: the other pages show titles, channel"
  Write-Output "names or thumbnails. Pass -All if that is wanted."
}
