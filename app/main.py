import os
import sys
from PySide6 import QtGui, QtWidgets

from .ui import MainWindow


def main():
    app = QtWidgets.QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "app_icon.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
