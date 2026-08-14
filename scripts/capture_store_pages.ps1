# Walk Clips Studio's sidebar and capture each page for the Store listing.
#
#     powershell -ExecutionPolicy Bypass -File scripts\capture_store_pages.ps1
#
# Partner Center wants four or more screenshots and this is the tedious way to
# get them by hand: switch page, screenshot, repeat. It only ever clicks the
# left-hand navigation, never a button that does anything - nothing here starts
# a job, deletes a clip, or changes a setting.
#
# Output: docs/store-screenshots/ (gitignored)

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Nav {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
$DOWN = 0x0002; $UP = 0x0004

# Sidebar entries, as offsets from the window's top-left. Taken from a
# 1440x900 window; the sidebar is fixed-width so these hold as it grows.
$pages = @(
  @{ n = "1-dashboard";  y = 133 },
  @{ n = "2-queue";      y = 181 },
  @{ n = "3-clipstudio"; y = 229 },
  @{ n = "4-creators";   y = 277 },
  @{ n = "5-models";     y = 325 },
  @{ n = "6-settings";   y = 373 }
)
$sidebarX = 110

$proc = Get-Process -Name "Clips Studio" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle } |
        Select-Object -First 1
if (-not $proc) { Write-Output "Clips Studio is not running."; exit 1 }

$outDir = Join-Path $PSScriptRoot "..\docs\store-screenshots"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

[void][Nav]::ShowWindow($proc.MainWindowHandle, 9)
[void][Nav]::SetForegroundWindow($proc.MainWindowHandle)
Start-Sleep -Milliseconds 800

$r = New-Object Nav+RECT
[void][Nav]::GetWindowRect($proc.MainWindowHandle, [ref]$r)
$w = $r.R - $r.L; $h = $r.B - $r.T
Write-Output "window ${w}x${h} at $($r.L),$($r.T)"
if ($w -lt 1366) { Write-Output "NOTE: under the Store 1366px minimum - maximise and re-run" }

foreach ($p in $pages) {
    [void][Nav]::SetCursorPos($r.L + $sidebarX, $r.T + $p.y)
    Start-Sleep -Milliseconds 150
    [Nav]::mouse_event($DOWN, 0, 0, 0, 0)
    [Nav]::mouse_event($UP, 0, 0, 0, 0)
    Start-Sleep -Milliseconds 1600      # let the page render and any fetch land

    [void][Nav]::GetWindowRect($proc.MainWindowHandle, [ref]$r)
    $bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
    $path = Join-Path $outDir "$($p.n).png"
    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    Write-Output "  $($p.n).png"
}

Write-Output ""
Write-Output "Captured to docs\store-screenshots\. Review them before uploading -"
Write-Output "pick the four that show the app doing something, not empty states."
