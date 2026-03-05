import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

if __name__ == "__main__":
    import logging
    from core.bootstrap import bootstrap_runtime
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    bootstrap_runtime()
    stream = sys.stdout if sys.stdout is not None else sys.stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(stream)],
    )
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
