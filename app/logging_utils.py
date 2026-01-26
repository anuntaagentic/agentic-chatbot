import logging
import os
from datetime import datetime


def get_log_dir():
    base_dir = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    log_dir = os.path.join(base_dir, "AgenticChatbot", "logs")
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
    return logger
