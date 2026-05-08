"""Application bootstrap: QApplication + qasync event loop + main window."""
from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from kdv.paths import assets_dir
from kdv.ui.main_window import MainWindow
from kdv.ui.state import AppState
from kdv.ui.style import apply_global_style


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


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
