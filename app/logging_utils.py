import logging
import os
from datetime import datetime


def get_log_dir():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def get_log_path():
    log_dir = get_log_dir()
    file_name = datetime.now().strftime("%Y-%m-%d") + ".log"
    return os.path.join(log_dir, file_name)


def setup_logger():
    logger = logging.getLogger("agentic_chatbot")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_path = get_log_path()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False

    # Keep autogen logs in the same file, without printing to terminal.
    autogen_logger = logging.getLogger("autogen")
    autogen_logger.handlers = []
    autogen_logger.addHandler(file_handler)
    autogen_logger.setLevel(logging.INFO)
    autogen_logger.propagate = False

    oai_logger = logging.getLogger("autogen.oai.client")
    oai_logger.handlers = []
    oai_logger.addHandler(file_handler)
    oai_logger.setLevel(logging.INFO)
    oai_logger.propagate = False
    return logger
