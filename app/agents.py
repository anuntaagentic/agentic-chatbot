import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from .command_filter import CommandFilter


@dataclass
class CommandResult:
    command: str
    allowed: bool
    output: str
    error: str
    return_code: Optional[int]


@dataclass
class PlanStep:
    description: str
    command: str


@dataclass
class DiagnosisResult:
    issue_type: str
    findings: str
    action_plan: List[str] = field(default_factory=list)
    command_results: List[CommandResult] = field(default_factory=list)
    install_app: str = ""


@dataclass
class FixPlan:
    issue_type: str
    summary: str
    commands: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    success: bool
    command_results: List[CommandResult] = field(default_factory=list)


class CommandRunner:
    def __init__(self, command_filter, logger):
        self.command_filter = command_filter
        self.logger = logger

    def run(self, command):
        allowed, reason = self.command_filter.is_allowed(command)
        if not allowed:
            self.logger.info("BLOCKED: %s | %s", command, reason)
            return CommandResult(command, False, "", reason, None)

        self.logger.info("RUN: %s", command)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = (completed.stdout or "").strip()
            error = (completed.stderr or "").strip()
            self.logger.info("EXIT %s", completed.returncode)
            if output:
                self.logger.info("STDOUT: %s", output)
            if error:
                self.logger.info("STDERR: %s", error)
            return CommandResult(command, True, output, error, completed.returncode)
        except Exception as exc:
            self.logger.info("ERROR: %s", exc)
            return CommandResult(command, True, "", str(exc), None)


class AutoGenHelper:
    def __init__(self):
        self.api_key = os.environ.get("GROQ_API_KEY", "")
        self.model = os.environ.get("GROQ_MODEL", "llama3-70b-8192")
        self.base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

    def available(self):
        return bool(self.api_key)

    def generate(self, system_prompt, user_prompt):
        if not self.available():
            return ""
        try:
            import autogen

            config_list = [
                {
                    "model": self.model,
                    "api_key": self.api_key,
                    "base_url": self.base_url,
                }
            ]
            llm_config = {"config_list": config_list, "temperature": 0}
            assistant = autogen.AssistantAgent(
                "assistant",
                llm_config=llm_config,
                system_message=system_prompt,
            )
            user_proxy = autogen.UserProxyAgent(
                "user_proxy",
                human_input_mode="NEVER",
                max_consecutive_auto_reply=1,
                code_execution_config=False,
            )
            user_proxy.initiate_chat(assistant, message=user_prompt, clear_history=True)
            messages = user_proxy.chat_messages.get(assistant, [])
            if not messages:
                return ""
            return messages[-1].get("content", "").strip()
        except Exception:
            return ""


class DiagnosisAgent:
    def __init__(self, runner, llm_helper):
        self.runner = runner
        self.llm_helper = llm_helper

    def run(self, issue_text):
        issue_type = self._classify_issue(issue_text)
        install_app = self._parse_install_app(issue_text) if issue_type == "install_app" else ""
        plan_steps = self._diagnostic_plan(issue_type, install_app)
        results = []
        validated_steps = []
        for step in plan_steps:
            preflight_ok, preflight_reason = self._preflight(step)
            if not preflight_ok:
                validated_steps.append(
                    f"{len(validated_steps) + 1}. {step.description} [SKIPPED ({preflight_reason})]"
                )
                results.append(
                    CommandResult(
                        step.command,
                        False,
                        "",
                        f"Skipped: {preflight_reason}",
                        None,
                    )
                )
                continue

            allowed, reason = self.runner.command_filter.is_allowed(step.command)
            status = "ALLOWED" if allowed else f"BLOCKED ({reason})"
            validated_steps.append(f"{len(validated_steps) + 1}. {step.description} [{status}]")
            results.append(self.runner.run(step.command))
        findings = self._summarize(issue_text, issue_type, results)
        return DiagnosisResult(
            issue_type=issue_type,
            findings=findings,
            action_plan=validated_steps,
            command_results=results,
            install_app=install_app,
        )

    def _classify_issue(self, issue_text):
        text = issue_text.lower()
        if "install " in text or text.startswith("install") or "download " in text or "setup " in text:
            return "install_app"
        if "bluetooth" in text:
            return "bluetooth"
        if "ip" in text or "ip address" in text or "address" in text:
            return "system_info"
        if "system info" in text or "computer info" in text or "about my computer" in text:
            return "system_info"
        if "wifi" in text or "wi-fi" in text or "network" in text or "internet" in text:
            return "network"
        if "disk" in text or "space" in text or "c drive" in text:
            return "disk_space"
        return "general"

    def _diagnostic_plan(self, issue_type, install_app):
        if issue_type == "install_app":
            app_label = install_app or "requested app"
            return [
                PlanStep(
                    "Check winget availability.",
                    "Get-Command winget -ErrorAction SilentlyContinue",
                ),
                PlanStep(
                    f"Search winget for {app_label}.",
                    f'winget search --name "{app_label}"',
                ),
            ]
        if issue_type == "disk_space":
            return [
                PlanStep(
                    "Check file system drive usage.",
                    "Get-PSDrive -PSProvider FileSystem",
                ),
                PlanStep(
                    "Inspect volume sizes and free space.",
                    "Get-Volume | Select-Object DriveLetter, FileSystemLabel, SizeRemaining, Size",
                ),
                PlanStep(
                    "Estimate temp folder size.",
                    "Get-ChildItem $env:TEMP -Recurse -Force -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum",
                ),
            ]
        if issue_type == "network":
            return [
                PlanStep(
                    "Check network adapters and link status.",
                    "Import-Module NetAdapter -ErrorAction SilentlyContinue; "
                    "Get-NetAdapter | Select-Object Name, Status, LinkSpeed",
                ),
                PlanStep(
                    "Review IP configuration.",
                    "Import-Module NetTCPIP -ErrorAction SilentlyContinue; Get-NetIPConfiguration",
                ),
                PlanStep(
                    "Test internet connectivity.",
                    "Test-NetConnection 8.8.8.8 -InformationLevel Detailed",
                ),
                PlanStep(
                    "Gather full IP stack details.",
                    "ipconfig /all",
                ),
                PlanStep(
                    "Inspect Wi-Fi interface status.",
                    "netsh wlan show interfaces",
                ),
            ]
        if issue_type == "bluetooth":
            return [
                PlanStep(
                    "Check Bluetooth adapter status.",
                    "Import-Module PnpDevice -ErrorAction SilentlyContinue; "
                    "Get-PnpDevice -Class Bluetooth | Select-Object Status, FriendlyName, InstanceId",
                ),
                PlanStep(
                    "Check Bluetooth service state.",
                    "Get-Service bthserv | Select-Object Status, StartType",
                ),
                PlanStep(
                    "Check radio status (if available).",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance -Namespace root\\wmi -ClassName BthRadio | Select-Object InstanceName, SoftwareRadioState",
                ),
            ]
        if issue_type == "system_info":
            return [
                PlanStep(
                    "Gather IP address information.",
                    "Import-Module NetTCPIP -ErrorAction SilentlyContinue; "
                    "Get-NetIPAddress | Select-Object IPAddress, InterfaceAlias, AddressFamily",
                ),
                PlanStep(
                    "Capture OS and system info.",
                    "Get-ComputerInfo | Select-Object CsName, OsName, OsVersion, CsSystemType, WindowsProductName",
                ),
                PlanStep(
                    "Capture CPU details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors",
                ),
                PlanStep(
                    "Capture memory details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory",
                ),
                PlanStep(
                    "Capture basic IP stack details.",
                    "ipconfig",
                ),
            ]
        return [
            PlanStep(
                "Gather OS and device info.",
                "Get-ComputerInfo | Select-Object OsName, OsVersion, CsName",
            )
        ]

    def _summarize(self, issue_text, issue_type, results):
        output_lines = []
        for result in results:
            if result.allowed and result.output:
                output_lines.append(f"{result.command}\n{result.output}")
            elif result.error:
                output_lines.append(f"{result.command}\n{result.error}")
        joined = "\n\n".join(output_lines)
        if self.llm_helper.available() and joined:
            system_prompt = (
                "You are a Windows diagnostics assistant. Summarize the issue based on command output."
            )
            user_prompt = f"Issue: {issue_text}\nType: {issue_type}\nOutput:\n{joined}"
            summary = self.llm_helper.generate(system_prompt, user_prompt)
            if summary:
                return summary
        if issue_type == "disk_space":
            return "Disk diagnostics complete. I gathered drive usage and temp folder size."
        if issue_type == "network":
            return "Network diagnostics complete. I checked adapters, IP configuration, and connectivity."
        if issue_type == "system_info":
            return "System info diagnostics complete. I gathered IP address details."
        if issue_type == "bluetooth":
            return "Bluetooth diagnostics complete. I checked the adapter, service, and radio state."
        if issue_type == "install_app":
            return "Install diagnostics complete. I checked winget and searched for the app."
        return "Diagnostics complete."

    def _preflight(self, step):
        if "winget search" in step.command and ("requested app" in step.command or '""' in step.command):
            return False, "missing app name"
        if "Get-CimInstance -Namespace root\\wmi -ClassName BthRadio" in step.command:
            check_cmd = (
                "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                "Get-CimClass -Namespace root\\wmi -ClassName BthRadio -ErrorAction SilentlyContinue"
            )
            result = self.runner.run(check_cmd)
            if not result.allowed:
                return False, "preflight blocked"
            if not result.output:
                return False, "BthRadio class not available"
        return True, ""

    def _parse_install_app(self, issue_text):
        text = issue_text.strip()
        lowered = text.lower()
        keywords = ["install", "download", "setup", "get"]
        for keyword in keywords:
            if keyword in lowered:
                parts = text.split(keyword, 1)
                if len(parts) == 2:
                    app = parts[1].strip(" .")
                    if app:
                        return app
        return ""


class FixPlannerAgent:
    def __init__(self, llm_helper):
        self.llm_helper = llm_helper

    def propose(self, issue_text, diagnosis):
        issue_type = diagnosis.issue_type
        commands = self._fix_commands(issue_type, diagnosis.install_app)
        summary = self._summarize(issue_text, issue_type, diagnosis, commands)
        return FixPlan(issue_type=issue_type, summary=summary, commands=commands)

    def _fix_commands(self, issue_type, install_app):
        if issue_type == "disk_space":
            return [
                "Remove-Item $env:TEMP\\* -Recurse -Force -ErrorAction SilentlyContinue",
                "Clear-RecycleBin -Force",
            ]
        if issue_type == "network":
            return [
                "ipconfig /flushdns",
                "netsh winsock reset",
                "netsh int ip reset",
                "Restart-Service WlanSvc",
            ]
        if issue_type == "install_app" and install_app:
            return [
                f'winget install --name "{install_app}" --accept-package-agreements --accept-source-agreements',
            ]
        return []

    def _summarize(self, issue_text, issue_type, diagnosis, commands):
        if not commands:
            return self._answer_question(issue_text, diagnosis)
        if self.llm_helper.available():
            system_prompt = (
                "You are a Windows support agent. Propose a concise fix plan for the user."
            )
            command_text = "\n".join(commands) if commands else "No commands."
            user_prompt = (
                f"Issue: {issue_text}\nType: {issue_type}\nFindings: {diagnosis.findings}\n"
                f"Commands:\n{command_text}\n"
                "Summarize the fix plan and request confirmation."
            )
            summary = self.llm_helper.generate(system_prompt, user_prompt)
            if summary:
                return summary
        if issue_type == "disk_space":
            return "Proposed fix: clear temp files and recycle bin to free space. Apply?"
        if issue_type == "network":
            return "Proposed fix: reset DNS and network stack, then restart Wi-Fi service. Apply?"
        if issue_type == "install_app":
            if not diagnosis.install_app:
                return "Please specify the app name you want to install."
            if "office" in diagnosis.install_app.lower():
                return (
                    "Proposed install: use winget to install Microsoft Office. "
                    "Note: desktop Office may require a subscription/license to activate. Apply?"
                )
            return f'Proposed install: use winget to install "{diagnosis.install_app}". Apply?'
        return self._answer_question(issue_text, diagnosis)

    def _answer_question(self, issue_text, diagnosis):
        normalized = issue_text.lower()
        extracted = self._extract_answer(normalized, diagnosis)
        if extracted:
            return extracted
        if self.llm_helper.available():
            system_prompt = "You answer Windows system questions using the provided diagnostics."
            user_prompt = (
                f"Question: {issue_text}\n"
                f"Diagnostics: {diagnosis.findings}\n"
                "Provide a concise, direct answer."
            )
            answer = self.llm_helper.generate(system_prompt, user_prompt)
            if answer:
                return answer
        return "No fix actions proposed. Diagnostics are shown in the log panel."

    def _extract_answer(self, normalized_question, diagnosis):
        outputs = []
        for result in diagnosis.command_results:
            if result.output:
                outputs.append(result.output)
        combined = "\n".join(outputs)

        if "os build" in normalized_question or "build version" in normalized_question:
            for line in combined.splitlines():
                if "osversion" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value
                if "buildnumber" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value

        if "ip address" in normalized_question or "my ip" in normalized_question:
            for line in combined.splitlines():
                if "ipaddress" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value

        if "os version" in normalized_question:
            for line in combined.splitlines():
                if "osversion" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value

        return ""


class ExecutorAgent:
    def __init__(self, runner):
        self.runner = runner

    def apply(self, fix_plan):
        results = [self.runner.run(cmd) for cmd in fix_plan.commands]
        success = all(result.allowed and result.return_code in (0, None) for result in results)
        return ExecutionResult(success=success, command_results=results)


def build_agents(allowlist_path, denylist_path, logger):
    command_filter = CommandFilter(allowlist_path, denylist_path)
    runner = CommandRunner(command_filter, logger)
    llm_helper = AutoGenHelper()
    return (
        DiagnosisAgent(runner, llm_helper),
        FixPlannerAgent(llm_helper),
        ExecutorAgent(runner),
        llm_helper,
    )
