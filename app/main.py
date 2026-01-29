import os
import sys
from PySide6 import QtGui, QtWidgets

from .logging_utils import setup_logger
from .ui import MainWindow


def _mask_env_value(key, value):
    key_upper = key.upper()
    if any(term in key_upper for term in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
        if not value:
            return ""
        return value[:3] + "***" + value[-2:] if len(value) > 5 else "***"
    return value


def _load_env(logger=None):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.abspath(os.path.join(base_dir, "..", ".env"))
    if not os.path.exists(env_path):
        return
    try:
        loaded = []
        with open(env_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    previous = os.environ.get(key)
                    os.environ[key] = value
                    loaded.append((key, value, previous))
        if logger is not None:
            if loaded:
                logger.info("Loaded %d env vars from .env", len(loaded))
                for key, value, previous in loaded:
                    masked = _mask_env_value(key, value)
                    status = "set"
                    if previous is not None and previous != value:
                        status = "overrode"
                    elif previous is not None and previous == value:
                        status = "kept"
                    logger.info("ENV %s=%s (%s)", key, masked, status)
            else:
                logger.info("No env vars loaded from .env (already set or empty file).")
    except Exception:
        return


def main():
    logger = setup_logger()
    _load_env(logger)
    app = QtWidgets.QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "app_icon.svg")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
