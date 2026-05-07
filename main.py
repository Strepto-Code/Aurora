import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QSurfaceFormat

# Compatibility profile required for QPainter GL engine. SwapInterval=0
# disables vsync so the realtime FPS cap is honoured.
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

    # Center on primary screen before show() to avoid a position snap.
    screen = app.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        frame = win.frameGeometry()
        frame.moveCenter(avail.center())
        win.move(frame.topLeft())

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
