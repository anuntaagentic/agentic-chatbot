import os
from PySide6 import QtCore, QtGui, QtWidgets

from .agents import build_agents
from .logging_utils import get_log_path, setup_logger


class DiagnosisWorker(QtCore.QObject):
    finished = QtCore.Signal(object, object)
    log_line = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, diagnosis_agent, fix_planner, issue_text):
        super().__init__()
        self.diagnosis_agent = diagnosis_agent
        self.fix_planner = fix_planner
        self.issue_text = issue_text

    def run(self):
        try:
            diagnosis = self.diagnosis_agent.run(self.issue_text)
            for result in diagnosis.command_results:
                if result.allowed:
                    summary = result.output or result.error or "No output."
                else:
                    summary = result.error
                self.log_line.emit(f"{result.command}\n{summary}\n")
            plan = self.fix_planner.propose(self.issue_text, diagnosis)
            self.finished.emit(diagnosis, plan)
        except Exception as exc:
            self.error.emit(str(exc))


class ExecuteWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    log_line = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, executor, fix_plan):
        super().__init__()
        self.executor = executor
        self.fix_plan = fix_plan

    def run(self):
        try:
            result = self.executor.apply(self.fix_plan)
            for command_result in result.command_results:
                if command_result.allowed:
                    summary = command_result.output or command_result.error or "No output."
                else:
                    summary = command_result.error
                self.log_line.emit(f"{command_result.command}\n{summary}\n")
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Agentic Windows Helper")
        self.resize(1100, 720)
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "app_icon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QtGui.QIcon(icon_path))

        logger = setup_logger()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        allowlist_path = os.path.join(base_dir, "..", "config", "allowlist.json")
        denylist_path = os.path.join(base_dir, "..", "config", "denylist.json")
        (
            self.diagnosis_agent,
            self.fix_planner,
            self.executor,
            self.llm_helper,
        ) = build_agents(allowlist_path, denylist_path, logger)

        self.current_fix_plan = None
        self.is_dark_theme = False
        self.background_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "assets",
            "nyc_skyline.svg",
        )

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_chat_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)
        self._apply_style()

        self._append_chat("Assistant", "Describe your Windows issue and I will run diagnostics.")
        self._append_log(f"Logs are saved to: {get_log_path()}")

    def _build_chat_panel(self):
        panel = QtWidgets.QWidget()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.chat_view = QtWidgets.QTextEdit()
        self.chat_view.setReadOnly(True)

        title = QtWidgets.QLabel("Assistant")
        title.setObjectName("sectionTitle")
        subtitle = QtWidgets.QLabel("Describe the issue and I will diagnose it step by step.")
        subtitle.setObjectName("sectionSubtitle")

        input_row = QtWidgets.QHBoxLayout()
        self.input_box = QtWidgets.QLineEdit()
        self.input_box.setPlaceholderText("Example: C drive is full or Wi-Fi is not working")
        self.input_box.returnPressed.connect(self._on_send)
        self.send_button = QtWidgets.QPushButton("Send")
        self.send_button.clicked.connect(self._on_send)
        input_row.addWidget(self.input_box)
        input_row.addWidget(self.send_button)

        self.apply_button = QtWidgets.QPushButton("Apply Fix")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)

        header_row = QtWidgets.QHBoxLayout()
        header_row.addWidget(title)
        header_row.addStretch()
        self.theme_toggle = QtWidgets.QPushButton("Dark Mode")
        self.theme_toggle.clicked.connect(self._toggle_theme)
        header_row.addWidget(self.theme_toggle)

        layout.addLayout(header_row)
        layout.addWidget(subtitle)
        layout.addWidget(self.chat_view)
        layout.addLayout(input_row)
        layout.addWidget(self.apply_button)
        return panel

    def _build_log_panel(self):
        panel = QtWidgets.QWidget()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)

        title = QtWidgets.QLabel("Diagnostics & Actions")
        title.setObjectName("sectionTitle")
        subtitle = QtWidgets.QLabel("Plan steps, command output, and outcomes.")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addWidget(self.log_view)
        return panel

    def _append_chat(self, who, message):
        self.chat_view.append(f"<b>{who}:</b> {message}")

    def _append_log(self, message):
        self.log_view.append(message)

    def _apply_style(self):
        background_url = ""
        if os.path.exists(self.background_path):
            background_url = self.background_path.replace("\\", "/")
        if self.is_dark_theme:
            style = """
                QMainWindow {{
                    background-color: #0f1216;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                    background-image: url("{background_url}");
                    background-position: bottom center;
                    background-repeat: no-repeat;
                    background-attachment: fixed;
                }}
                QWidget#panel {{
                    background-color: rgba(19, 24, 32, 0.6);
                    border: 1px solid #202735;
                    border-radius: 10px;
                }}
                QLabel {{
                    color: #e1e6eb;
                    font-weight: 600;
                }}
                QLabel#sectionSubtitle {{
                    color: #9aa4b2;
                    font-weight: 400;
                }}
                QTextEdit {{
                    background-color: rgba(22, 27, 34, 0.6);
                    color: #e1e6eb;
                    border: 1px solid #2a3038;
                    border-radius: 6px;
                    padding: 8px;
                }}
                QLineEdit {{
                    background-color: rgba(22, 27, 34, 0.6);
                    color: #e1e6eb;
                    border: 1px solid #2a3038;
                    border-radius: 6px;
                    padding: 6px 8px;
                }}
                QPushButton {{
                    background-color: #2f6fec;
                    color: #ffffff;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
                QPushButton:disabled {{
                    background-color: #3c4f7f;
                }}
                QSplitter::handle {{
                    background-color: #0f1216;
                }}
                QScrollBar:vertical {{
                    background: #11151b;
                    width: 10px;
                }}
                QScrollBar::handle:vertical {{
                    background: #2a3038;
                    border-radius: 4px;
                    min-height: 20px;
                }}
            """.format(background_url=background_url)
            self.setStyleSheet(style)
        else:
            style = """
                QMainWindow {{
                    background-color: #f4f6f8;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                    background-image: url("{background_url}");
                    background-position: bottom center;
                    background-repeat: no-repeat;
                    background-attachment: fixed;
                }}
                QWidget#panel {{
                    background-color: rgba(255, 255, 255, 0.6);
                    border: 1px solid #e1e6ec;
                    border-radius: 10px;
                }}
                QLabel {{
                    color: #2b2f33;
                    font-weight: 600;
                }}
                QLabel#sectionSubtitle {{
                    color: #5f6b7a;
                    font-weight: 400;
                }}
                QTextEdit {{
                    background-color: rgba(255, 255, 255, 0.6);
                    color: #2b2f33;
                    border: 1px solid #d7dde3;
                    border-radius: 6px;
                    padding: 8px;
                }}
                QLineEdit {{
                    background-color: rgba(255, 255, 255, 0.6);
                    color: #2b2f33;
                    border: 1px solid #d7dde3;
                    border-radius: 6px;
                    padding: 6px 8px;
                }}
                QPushButton {{
                    background-color: #2f6fec;
                    color: #ffffff;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
                QPushButton:disabled {{
                    background-color: #9fb6f2;
                }}
                QSplitter::handle {{
                    background-color: #f4f6f8;
                }}
                QScrollBar:vertical {{
                    background: #f4f6f8;
                    width: 10px;
                }}
                QScrollBar::handle:vertical {{
                    background: #c8d2de;
                    border-radius: 4px;
                    min-height: 20px;
                }}
            """.format(background_url=background_url)
            self.setStyleSheet(style)

    def _toggle_theme(self):
        self.is_dark_theme = not self.is_dark_theme
        label = "Light Mode" if self.is_dark_theme else "Dark Mode"
        self.theme_toggle.setText(label)
        self._apply_style()

    def _set_busy(self, busy):
        self.send_button.setEnabled(not busy)
        self.input_box.setEnabled(not busy)
        if busy:
            self.apply_button.setEnabled(False)

    def _on_send(self):
        issue_text = self.input_box.text().strip()
        if not issue_text:
            return
        self.input_box.clear()
        self._append_chat("You", issue_text)
        self._append_chat("Assistant", "Running diagnostics...")
        self._append_log("Starting diagnostics...")
        self._set_busy(True)

        self.diagnosis_thread = QtCore.QThread()
        self.diagnosis_worker = DiagnosisWorker(
            self.diagnosis_agent, self.fix_planner, issue_text
        )
        self.diagnosis_worker.moveToThread(self.diagnosis_thread)
        self.diagnosis_thread.started.connect(self.diagnosis_worker.run)
        self.diagnosis_worker.finished.connect(self._on_diagnosis_complete)
        self.diagnosis_worker.log_line.connect(self._append_log)
        self.diagnosis_worker.error.connect(self._on_worker_error)
        self.diagnosis_worker.finished.connect(self.diagnosis_thread.quit)
        self.diagnosis_worker.finished.connect(self.diagnosis_worker.deleteLater)
        self.diagnosis_thread.finished.connect(self.diagnosis_thread.deleteLater)
        self.diagnosis_thread.start()

    def _on_diagnosis_complete(self, diagnosis, plan):
        self._set_busy(False)
        self.current_fix_plan = plan
        if diagnosis.action_plan:
            self._append_log("Action plan:")
            for step in diagnosis.action_plan:
                self._append_log(step)
        self._append_chat("Assistant", diagnosis.findings)
        self._append_chat("Assistant", plan.summary)
        self._append_log("Diagnostics complete.")
        if plan.commands:
            self.apply_button.setEnabled(True)
        else:
            self.apply_button.setEnabled(False)

    def _on_apply(self):
        if not self.current_fix_plan or not self.current_fix_plan.commands:
            return
        confirm = QtWidgets.QMessageBox.question(
            self,
            "Apply Fix",
            "Apply the proposed fixes now?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
        )
        if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            self._append_chat("Assistant", "Fix canceled.")
            return
        self._append_chat("Assistant", "Applying fixes now...")
        self._append_log("Applying fixes...")
        self._set_busy(True)

        self.execute_thread = QtCore.QThread()
        self.execute_worker = ExecuteWorker(self.executor, self.current_fix_plan)
        self.execute_worker.moveToThread(self.execute_thread)
        self.execute_thread.started.connect(self.execute_worker.run)
        self.execute_worker.finished.connect(self._on_execute_complete)
        self.execute_worker.log_line.connect(self._append_log)
        self.execute_worker.error.connect(self._on_worker_error)
        self.execute_worker.finished.connect(self.execute_thread.quit)
        self.execute_worker.finished.connect(self.execute_worker.deleteLater)
        self.execute_thread.finished.connect(self.execute_thread.deleteLater)
        self.execute_thread.start()

    def _on_execute_complete(self, result):
        self._set_busy(False)
        if result.success:
            self._append_chat("Assistant", "Fixes applied. Please re-check the issue.")
        else:
            self._append_chat("Assistant", "Fixes applied with errors. Review the log panel.")
        self.apply_button.setEnabled(False)

    def _on_worker_error(self, message):
        self._set_busy(False)
        self._append_chat("Assistant", f"Error: {message}")
        self._append_log(f"Error: {message}")
