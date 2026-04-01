import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QSurfaceFormat

# QPainter's GL paint engine requires compatibility profile (not Core).
fmt = QSurfaceFormat()
fmt.setDepthBufferSize(0)
fmt.setStencilBufferSize(8)
fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
QSurfaceFormat.setDefaultFormat(fmt)

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1280, 800)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
