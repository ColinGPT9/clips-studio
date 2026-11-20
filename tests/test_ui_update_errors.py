"""Update failures stay diagnosable without interrupting someone who is offline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_update_failures_are_logged_and_retry_the_failed_operation():
    main = (ROOT / "ui/src/main/updater.ts").read_text(encoding="utf-8")
    banner = (ROOT / "ui/src/renderer/src/components/UpdateBanner.tsx").read_text(encoding="utf-8")
    types = (ROOT / "ui/src/renderer/src/env.d.ts").read_text(encoding="utf-8")

    assert "UPDATE_LOG_LIMIT = 64 * 1024" in main
    assert "writeFileSync(path, (previous + entry).slice(-UPDATE_LOG_LIMIT), 'utf8')" in main
    assert "autoUpdater.logger = {" in main
    assert "quiet: isOfflineError(err)" in main
    assert "action: downloadingUpdate ? 'download' : 'check'" in main
    assert "action: 'download'" in main
    assert "if (s.quiet) return null" in banner
    assert "Details saved to update.log." in banner
    assert "window.studio.update.download()" in banner
    assert "window.studio.update.check()" in banner
    assert "quiet?: boolean" in types
    assert "action?: 'check' | 'download'" in types
