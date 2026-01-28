import os
import sys
import ctypes
from PySide6 import QtCore, QtGui, QtWidgets

from .agents import build_agents
from .logging_utils import get_log_path, setup_logger


class DiagnosisWorker(QtCore.QObject):
    finished = QtCore.Signal(object, object)
    log_line = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, diagnosis_agent, fix_planner, issue_text, fix_stage):
        super().__init__()
        self.diagnosis_agent = diagnosis_agent
        self.fix_planner = fix_planner
        self.issue_text = issue_text
        self.fix_stage = fix_stage

    def run(self):
        try:
            orchestrator = getattr(self.diagnosis_agent, "orchestrator", None)
            if orchestrator is not None:
                orchestrator.fix_stage = self.fix_stage
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
        self.last_issue_text = ""
        self.fix_stage = 1
        self.retry_mode = False
        self.auto_fix_in_progress = False
        self.is_admin = self._check_admin()
        self.background_path = ""

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_chat_panel())
        splitter.addWidget(self._build_log_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.addWidget(splitter)
        self.setCentralWidget(container)
        self._apply_style()

        self._append_chat("Assistant", "Describe your Windows issue and I will run diagnostics.")
        self._append_log(f"Logs are saved to: {get_log_path()}")
        if self.is_admin:
            self.setWindowTitle("Agentic Windows Helper (Admin)")
        research = getattr(self.diagnosis_agent, "research", None)
        rag = getattr(research, "rag", None) if research else None
        rag_ready = rag and rag.matrix is not None
        if not rag_ready:
            self._append_log(
                "RAG vectors not loaded. Run: python -m app.build_vectors"
            )

    def _build_chat_panel(self):
        panel = QtWidgets.QWidget()
        panel.setObjectName("panel")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.chat_view = QtWidgets.QTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setObjectName("chatView")

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
        self.clear_chat_button = QtWidgets.QPushButton("Clear Chat")
        self.clear_chat_button.clicked.connect(self._clear_chat)
        header_row.addWidget(self.clear_chat_button)
        self.admin_status = QtWidgets.QLabel(
            "Admin: Yes" if self.is_admin else "Admin: No"
        )
        self.admin_status.setObjectName("adminStatus")
        header_row.addWidget(self.admin_status)
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

        header_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("LIVE DIAGNOSTICS")
        title.setObjectName("sectionTitle")
        badge = QtWidgets.QLabel("Active Session")
        badge.setObjectName("badge")
        header_row.addWidget(title)
        header_row.addStretch()
        header_row.addWidget(badge)

        self.log_view = QtWidgets.QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.NoWrap)
        self.log_view.setObjectName("card")

        self.sources_view = QtWidgets.QTextEdit()
        self.sources_view.setReadOnly(True)
        self.sources_view.setObjectName("card")
        self.sources_view.setPlaceholderText("Sources will appear here.")

        layout.addLayout(header_row)
        layout.addWidget(self._section_label("Command Output"))
        layout.addWidget(self.log_view)
        layout.addWidget(self._section_label("Active Context & Sources"))
        layout.addWidget(self.sources_view)
        return panel

    def _append_chat(self, who, message):
        bubble_color = "#2f6fec" if who == "You" else "#ffffff"
        text_color = "#ffffff" if who == "You" else "#1f2937"
        justify = "flex-end" if who == "You" else "flex-start"
        bubble = (
            f"<div style='display:flex; justify-content:{justify}; margin:8px 0;'>"
            f"<span style='display:inline-block; padding:10px 12px; "
            f"border-radius:14px; background:{bubble_color}; color:{text_color}; "
            f"max-width:80%;'>{message}</span></div>"
        )
        self.chat_view.append(bubble)

    def _append_log(self, message):
        self.log_view.append(message)

    def _section_label(self, text):
        label = QtWidgets.QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _apply_style(self):
        background_url = ""
        if self.background_path and os.path.exists(self.background_path):
            background_url = self.background_path.replace("\\", "/")
        if self.is_dark_theme:
            style = """
                QMainWindow {{
                    background-color: #1f1f1f;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                }}
                QLabel#adminStatus {{
                    background-color: rgba(255, 255, 255, 0.08);
                    color: #e5e7eb;
                    padding: 4px 10px;
                    border-radius: 10px;
                    font-weight: 600;
                }}
                QWidget#panel {{
                    background-color: rgba(31, 31, 31, 0.75);
                    border: 1px solid #2b2b2b;
                    border-radius: 10px;
                }}
                QLabel#sectionLabel {{
                    color: #e5e7eb;
                    font-weight: 600;
                }}
                QLabel#badge {{
                    background-color: rgba(76, 175, 80, 0.2);
                    color: #8df5a4;
                    padding: 4px 10px;
                    border-radius: 10px;
                    font-weight: 600;
                }}
                QLabel {{
                    color: #f7f7f7;
                    font-weight: 600;
                }}
                QLabel#sectionSubtitle {{
                    color: #b3b3b3;
                    font-weight: 400;
                }}
                QTextEdit {{
                    background-color: rgba(38, 38, 38, 0.7);
                    color: #f7f7f7;
                    border: 1px solid #2b2b2b;
                    border-radius: 6px;
                    padding: 8px;
                }}
                QTextEdit#card {{
                    background-color: rgba(20, 24, 35, 0.85);
                    border: 1px solid #2b2b2b;
                    border-radius: 10px;
                }}
                QTextEdit#chatView {{
                    background-color: transparent;
                    border: none;
                }}
                QLineEdit {{
                    background-color: rgba(38, 38, 38, 0.7);
                    color: #f7f7f7;
                    border: 1px solid #2b2b2b;
                    border-radius: 6px;
                    padding: 6px 8px;
                }}
                QPushButton {{
                    background-color: #f7f7f7;
                    color: #1f1f1f;
                    border: 1px solid #2b2b2b;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
                QPushButton#accent {{
                    background-color: #2f6fec;
                    color: #ffffff;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
                QPushButton:disabled {{
                    background-color: #444444;
                }}
                QSplitter::handle {{
                    background-color: #1f1f1f;
                }}
                QScrollBar:vertical {{
                    background: #1f1f1f;
                    width: 10px;
                }}
                QScrollBar::handle:vertical {{
                    background: #3a3a3a;
                    border-radius: 4px;
                    min-height: 20px;
                }}
            """.format(background_url=background_url)
            self.setStyleSheet(style)
        else:
            style = """
                QMainWindow {{
                    background-color: #ffffff;
                    font-family: "Segoe UI";
                    font-size: 10pt;
                }}
                QLabel#adminStatus {{
                    background-color: #f1f5f9;
                    color: #1f2937;
                    padding: 4px 10px;
                    border-radius: 10px;
                    font-weight: 600;
                }}
                QWidget#panel {{
                    background-color: rgba(255, 255, 255, 0.75);
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    border-radius: 10px;
                }}
                QLabel#sectionLabel {{
                    color: #1f2937;
                    font-weight: 600;
                }}
                QLabel#badge {{
                    background-color: #e9f7ef;
                    color: #1f8a4c;
                    padding: 4px 10px;
                    border-radius: 10px;
                    font-weight: 600;
                }}
                QLabel {{
                    color: #0f0f0f;
                    font-weight: 600;
                }}
                QLabel#sectionSubtitle {{
                    color: #717182;
                    font-weight: 400;
                }}
                QTextEdit {{
                    background-color: rgba(243, 243, 245, 0.75);
                    color: #0f0f0f;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    border-radius: 6px;
                    padding: 8px;
                }}
                QTextEdit#card {{
                    background-color: #0f172a;
                    color: #e5e7eb;
                    border: 1px solid rgba(15, 23, 42, 0.2);
                    border-radius: 12px;
                }}
                QTextEdit#chatView {{
                    background-color: transparent;
                    border: none;
                }}
                QLineEdit {{
                    background-color: rgba(243, 243, 245, 0.85);
                    color: #0f0f0f;
                    border: 1px solid rgba(0, 0, 0, 0.1);
                    border-radius: 6px;
                    padding: 6px 8px;
                }}
                QPushButton {{
                    background-color: #030213;
                    color: #ffffff;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-weight: 600;
                }}
                QPushButton:disabled {{
                    background-color: #cbced4;
                }}
                QSplitter::handle {{
                    background-color: #ffffff;
                }}
                QScrollBar:vertical {{
                    background: #ffffff;
                    width: 10px;
                }}
                QScrollBar::handle:vertical {{
                    background: rgba(100, 116, 139, 0.2);
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

    def _clear_chat(self):
        self.chat_view.clear()
        self._append_chat("Assistant", "Chat cleared. How can I help?")

    def _check_admin(self):
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    def _run_as_admin(self):
        exe = sys.executable
        args = " ".join([f'"{arg}"' for arg in sys.argv])
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None,
                "runas",
                exe,
                args,
                None,
                1,
            )
        except Exception:
            self._append_log("Failed to elevate. Please run the app as administrator.")

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
        self.last_issue_text = issue_text
        self.fix_stage = 1
        self.retry_mode = False
        self._start_diagnosis(issue_text)

    def _start_diagnosis(self, issue_text):
        self._append_chat("You", issue_text)
        self._append_chat("Assistant", "Running diagnostics...")
        self._append_log("Starting diagnostics...")
        if hasattr(self, "sources_view"):
            self.sources_view.clear()
        self._set_busy(True)

        self.diagnosis_thread = QtCore.QThread()
        self.diagnosis_worker = DiagnosisWorker(
            self.diagnosis_agent, self.fix_planner, issue_text, self.fix_stage
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
        self.retry_mode = False
        self.apply_button.setText("Apply Fix")
        sources_lines = []
        if diagnosis.rag_matches:
            sources_lines.append("Knowledge base sources:")
            for match in diagnosis.rag_matches[:3]:
                sources_lines.append(
                    f"- {match.conversation_id} | {match.issue} (score {match.score:.2f})"
                )
        if diagnosis.web_results:
            if sources_lines:
                sources_lines.append("")
            sources_lines.append("Web sources:")
            for result in diagnosis.web_results[:3]:
                sources_lines.append(f"- {result.title} | {result.url}")
            top_web = diagnosis.web_results[0]
            sources_lines.append(f"Final web result: {top_web.title} | {top_web.url}")
        if not sources_lines:
            sources_lines = ["Sources: none"]
        self.sources_view.setPlainText("\n".join(sources_lines))
        if diagnosis.web_query:
            self._append_log(f"Web query: {diagnosis.web_query}")
        if diagnosis.web_error:
            self._append_log(f"Web error: {diagnosis.web_error}")
        if diagnosis.web_count == 0:
            self._append_log("Web result count: 0")
        if diagnosis.issue_type == "system_info":
            self._append_chat("Assistant", plan.summary)
        else:
            cause, fix = self._extract_cause_and_fix(plan.summary)
            if cause:
                self._append_chat("Assistant", f"Problem: {cause}")
            else:
                self._append_chat("Assistant", "Problem identified from diagnostics.")
            if fix:
                self._append_chat("Assistant", f"Planned fix: {fix}")
        self._append_log("Diagnostics complete.")
        if plan.commands:
            self.apply_button.setEnabled(True)
        else:
            self.apply_button.setEnabled(False)

        if self.auto_fix_in_progress and plan.commands:
            self._auto_apply_fix()

    def _on_apply(self):
        if self.retry_mode and self.last_issue_text:
            return
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
        self.auto_fix_in_progress = True
        self._auto_apply_fix()

    def _on_execute_complete(self, result):
        self._set_busy(False)
        if result.verification_message:
            self._append_log(f"Verification: {result.verification_message}")
        if result.success:
            self._append_chat("Assistant", "Fix applied and verified.")
            self.apply_button.setEnabled(False)
            self.auto_fix_in_progress = False
        else:
            if result.verified:
                self._append_chat(
                    "Assistant", "Fix applied but there were errors. See diagnostics."
                )
                self.apply_button.setEnabled(True)
                self.apply_button.setText("Retry Fix")
                self.retry_mode = True
            else:
                if self.auto_fix_in_progress:
                    if self.fix_stage >= 4:
                        self._append_chat(
                            "Assistant",
                            "Fix attempts exhausted. Escalation required.",
                        )
                        self.apply_button.setEnabled(False)
                        self.auto_fix_in_progress = False
                    else:
                        self.fix_stage = min(self.fix_stage + 1, 4)
                        self._append_chat(
                            "Assistant",
                            "Fix attempt failed. Trying the next method...",
                        )
                        self._append_log("Auto-retrying with next fix stage...")
                        self._start_diagnosis(self.last_issue_text)
                else:
                    self._append_chat(
                        "Assistant",
                        "Fix applied but the issue persists. You can try again or escalate.",
                    )
                    self.apply_button.setEnabled(True)
                    self.apply_button.setText("Retry Fix")
                    self.retry_mode = True
            failed = []
            for command_result in result.command_results:
                if command_result.error or (command_result.return_code not in (0, None)):
                    failed.append(command_result.command)
            if failed:
                if not self.auto_fix_in_progress:
                    self._append_chat(
                        "Assistant",
                        "Some commands failed. See diagnostics for manual commands.",
                    )
                self._append_log("Manual commands (failed):")
                for cmd in failed:
                    self._append_log(cmd)

    def _auto_apply_fix(self):
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

    def _extract_cause_and_fix(self, summary):
        cause = ""
        fix = ""
        for line in summary.splitlines():
            if "Likely cause:" in line:
                cause = line.split("Likely cause:", 1)[1].strip()
            if "Proposed fix:" in line:
                fix = line.split("Proposed fix:", 1)[1].strip()
        return cause, fix

    def _on_worker_error(self, message):
        self._set_busy(False)
        self._append_chat("Assistant", f"Error: {message}")
        self._append_log(f"Error: {message}")
