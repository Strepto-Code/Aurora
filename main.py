import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QSurfaceFormat

# Force an OpenGL 3.3 Core context so our ModernGL shaders (#version 330) work in QOpenGLWidget
fmt = QSurfaceFormat()
fmt.setVersion(3, 3)
fmt.setProfile(QSurfaceFormat.CoreProfile)
fmt.setDepthBufferSize(24)
fmt.setStencilBufferSize(8)
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
