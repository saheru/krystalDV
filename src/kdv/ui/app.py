"""Application bootstrap: QApplication + qasync event loop + main window."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import sys
import traceback

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from kdv.paths import assets_dir, cache_dir
from kdv.ui.main_window import MainWindow
from kdv.ui.state import AppState
from kdv.ui.style import apply_global_style


def _configure_logging() -> None:
    """Log to console (when available) AND to a rotating file in the cache dir.

    Frozen GUI builds have no terminal, so the file log is the only way to
    diagnose crashes after the fact. Path: <user_cache>/krystaldatavision/kdv.log
    """
    log_path = cache_dir() / "kdv.log"
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Clear any existing handlers (avoid duplicates on hot-reload)
    root.handlers.clear()
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=2, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    # Keep stderr handler too — useful in dev mode
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    logging.info("=== Krystal Data Vision starting; log file: %s ===", log_path)


def _install_excepthook() -> None:
    """Log uncaught exceptions instead of letting them silently crash."""

    def hook(exc_type, exc, tb):
        logging.critical(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc, tb)),
        )
        try:
            QMessageBox.critical(
                None,
                "应用错误",
                f"发生未处理异常：\n\n{exc}\n\n详情已写入日志文件。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def _maybe_load_bundled_font() -> None:
    """Load Inter from assets/fonts if present; otherwise fall back to system."""
    fonts_dir = assets_dir() / "fonts"
    if not fonts_dir.exists():
        return
    for f in fonts_dir.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    for f in fonts_dir.glob("*.otf"):
        QFontDatabase.addApplicationFont(str(f))


def main() -> int:
    _configure_logging()
    _install_excepthook()

    import qasync  # imported here to avoid surprising the user pre-install

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Krystal Data Vision")
    app.setOrganizationName("Krystal Data Vision")

    icon_path = assets_dir() / "icons" / "kdv.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    apply_global_style(app)
    _maybe_load_bundled_font()

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    state = AppState()
    win = MainWindow(state)
    win.show()

    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
