"""Desktop app checks that need Qt (run with QT_QPA_PLATFORM=offscreen).

Run:  .venv\\Scripts\\python -m tests.test_app
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mazetube.app import MainWindow  # noqa: E402


def main():
    QSettings.setDefaultFormat(QSettings.IniFormat)
    app = QApplication.instance() or QApplication([])
    win = MainWindow()

    win.regenerate()
    assert win.design is not None, "the default tube builds"
    print("default tube builds  OK")

    # A printer too short for any tube: the last good design must not stay exportable.
    win.params.printer_height = 20.0
    win.regenerate()
    assert win.design is None, "a design that no longer matches the settings is still exportable"
    print("invalid settings leave nothing to export  OK")

    # A build that throws is treated the same way.
    win.params.printer_height = 250.0
    win.regenerate()
    assert win.design is not None
    import mazetube.app as app_module
    real = app_module.TubeDesign

    def broken(*args, **kwargs):
        raise RuntimeError("simulated build failure")

    app_module.TubeDesign = broken
    try:
        win.regenerate()
    finally:
        app_module.TubeDesign = real
    assert win.design is None, "a failed build left the previous design exportable"
    print("a failed build leaves nothing to export  OK")

    win.close()
    app.quit()
    print("app tests passed")


if __name__ == "__main__":
    main()
