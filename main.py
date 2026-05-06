import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QSurfaceFormat

# QPainter's GL paint engine requires compatibility profile (not Core).
# SwapInterval=0 disables vsync so QTimer-driven repaints actually pace
# the realtime widget instead of being clamped to monitor refresh rate.
fmt = QSurfaceFormat()
fmt.setDepthBufferSize(0)
fmt.setStencilBufferSize(8)
fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
fmt.setSwapInterval(0)
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
