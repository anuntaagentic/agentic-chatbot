import os
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from .command_filter import CommandFilter
from .rag import TechSupportRAG
from .web_search import WebSearch


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
    rag_matches: List[object] = field(default_factory=list)
    web_results: List[object] = field(default_factory=list)
    web_query: str = ""
    web_error: str = ""
    web_count: int = 0
    blocked_commands: List[str] = field(default_factory=list)
    fix_stage: int = 1


@dataclass
class FixPlan:
    issue_type: str
    summary: str
    commands: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    success: bool
    command_results: List[CommandResult] = field(default_factory=list)
    verified: bool = False
    verification_message: str = ""


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


class OrchestratorAgent:
    def __init__(self):
        self.fix_stage = 1

    def build_plan(self, issue_text):
        issue_type = self._classify_issue(issue_text)
        install_app = self._parse_install_app(issue_text) if issue_type == "install_app" else ""
        plan_steps = self._diagnostic_plan(issue_type, install_app)
        return issue_type, install_app, plan_steps

    def _classify_issue(self, issue_text):
        text = issue_text.lower()
        if "system information" in text or "system info" in text or "system infromation" in text:
            return "system_info"
        if "install " in text or text.startswith("install") or "download " in text or "setup " in text:
            return "install_app"
        if "os" in text and ("build" in text or "version" in text):
            return "system_info"
        if "cpu" in text or "processor" in text:
            return "system_info"
        if "bluetooth" in text:
            return "bluetooth"
        if "ip" in text or "ip address" in text or "address" in text:
            return "system_info"
        if "system info" in text or "computer info" in text or "about my computer" in text or "about my system" in text or "tell me about my system" in text:
            return "system_info"
        if "details about my computer" in text or "details about my system" in text:
            return "system_info"
        if "detail" in text and ("computer" in text or "copmuter" in text or "pc" in text or "system" in text):
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
                    "Get-PnpDevice -Class Bluetooth | Select-Object Status, FriendlyName, InstanceId | Format-List",
                ),
                PlanStep(
                    "Check Bluetooth service state.",
                    "Get-Service bthserv | Select-Object Status, StartType | Format-List",
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
                    "Get-ComputerInfo | Select-Object CsName, OsName, OsVersion, OsBuildNumber, "
                    "CsSystemType, WindowsProductName | Format-List",
                ),
                PlanStep(
                    "Capture CPU details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | Format-List",
                ),
                PlanStep(
                    "Capture memory details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory | Format-List",
                ),
                PlanStep(
                    "Capture disk usage.",
                    "Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free | Format-List",
                ),
                PlanStep(
                    "Capture basic IP stack details.",
                    "ipconfig",
                ),
            ]
        return [
            PlanStep(
                "Gather OS and device info.",
                "Get-ComputerInfo | Select-Object OsName, OsVersion, OsBuildNumber, CsName | Format-List",
            )
        ]

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


class ResearchAgent:
    def __init__(self, rag, web_search):
        self.rag = rag
        self.web_search = web_search

    def fetch(self, issue_text, issue_type, keywords):
        rag_matches = []
        if self.rag and self.rag.matrix is not None:
            rag_matches = self.rag.search(issue_text, keywords=keywords)
        web_results = []
        web_query = ""
        if issue_type != "system_info":
            web_query = f"{issue_text} Windows 11 fix script"
            if self.web_search:
                web_results = self.web_search.search(web_query)
        return {
            "rag_matches": rag_matches,
            "web_results": web_results,
            "web_query": web_query,
            "web_error": getattr(self.web_search, "last_error", "") if self.web_search else "",
            "web_count": getattr(self.web_search, "last_count", 0) if self.web_search else 0,
        }


class ActionAgent:
    def __init__(self, runner):
        self.runner = runner

    def execute_plan(self, plan_steps):
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
            if not allowed:
                results.append(
                    CommandResult(step.command, False, "", reason, None)
                )
                continue
            results.append(self.runner.run(step.command))
        return validated_steps, results

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


class GatekeeperAgent:
    def __init__(self, llm_helper):
        self.llm_helper = llm_helper
        self.blocked_terms = [
            "pc repair tool",
            "speedup",
            "trusted",
            "download",
            "ad_provider",
            "ad_domain",
            "click_metadata",
            "bing.com/aclick",
        ]

    def finalize(self, issue_type, question, candidate, sources_text):
        if not candidate:
            return candidate
        if issue_type == "system_info":
            return candidate
        lowered = candidate.lower()
        if any(term in lowered for term in self.blocked_terms):
            return "I filtered out promotional content. I can provide a neutral, technical answer instead."
        if not self.llm_helper.available():
            return candidate
        system_prompt = (
            "You are a response editor. Ensure answers are safe, non-promotional, and cite sources when available."
        )
        user_prompt = (
            f"Question: {question}\n"
            f"Draft: {candidate}\n"
            f"Sources: {sources_text}\n"
            "Return a concise final answer."
        )
        edited = self.llm_helper.generate(system_prompt, user_prompt)
        return edited or candidate


class DiagnosisAgent:
    def __init__(self, orchestrator, research, action, llm_helper):
        self.orchestrator = orchestrator
        self.research = research
        self.action = action
        self.llm_helper = llm_helper

    def run(self, issue_text):
        issue_type, install_app, plan_steps = self.orchestrator.build_plan(issue_text)
        validated_steps, results = self.action.execute_plan(plan_steps)
        research_data = self.research.fetch(
            issue_text, issue_type, self._rag_keywords(issue_text)
        )
        findings = self._summarize(issue_text, issue_type, results, research_data["rag_matches"])
        return DiagnosisResult(
            issue_type=issue_type,
            findings=findings,
            action_plan=validated_steps,
            command_results=results,
            install_app=install_app,
            rag_matches=research_data["rag_matches"],
            web_results=research_data["web_results"],
            web_query=research_data["web_query"],
            web_error=research_data["web_error"],
            web_count=research_data["web_count"],
            blocked_commands=[result.command for result in results if not result.allowed],
            fix_stage=getattr(self.orchestrator, "fix_stage", 1),
        )

    def _classify_issue(self, issue_text):
        text = issue_text.lower()
        if "blutooth" in text or "bluetooth" in text:
            return "bluetooth"
        if "install " in text or text.startswith("install") or "download " in text or "setup " in text:
            return "install_app"
        if "os" in text and ("build" in text or "version" in text):
            return "system_info"
        if "cpu" in text or "processor" in text:
            return "system_info"
        if "ip" in text or "ip address" in text or "address" in text:
            return "system_info"
        if "system info" in text or "computer info" in text or "about my computer" in text or "about my system" in text or "tell me about my system" in text:
            return "system_info"
        if "details about my computer" in text or "details about my system" in text:
            return "system_info"
        if "detail" in text and ("computer" in text or "copmuter" in text or "pc" in text or "system" in text):
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
                    "Get-PnpDevice -Class Bluetooth | Select-Object Status, FriendlyName, InstanceId | Format-List",
                ),
                PlanStep(
                    "Check Bluetooth service state.",
                    "Get-Service bthserv | Select-Object Status, StartType | Format-List",
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
                    "Get-ComputerInfo | Select-Object CsName, OsName, OsVersion, OsBuildNumber, "
                    "CsSystemType, WindowsProductName | Format-List",
                ),
                PlanStep(
                    "Capture CPU details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_Processor | Select-Object Name, NumberOfCores, NumberOfLogicalProcessors | Format-List",
                ),
                PlanStep(
                    "Capture memory details.",
                    "Import-Module CimCmdlets -ErrorAction SilentlyContinue; "
                    "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize, FreePhysicalMemory | Format-List",
                ),
                PlanStep(
                    "Capture disk usage.",
                    "Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free | Format-Table -AutoSize",
                ),
                PlanStep(
                    "Capture basic IP stack details.",
                    "ipconfig",
                ),
            ]
        return [
            PlanStep(
                "Gather OS and device info.",
                "Get-ComputerInfo | Select-Object OsName, OsVersion, OsBuildNumber, CsName | Format-List",
            )
        ]

    def _summarize(self, issue_text, issue_type, results, rag_matches):
        output_lines = []
        for result in results:
            if result.allowed and result.output:
                output_lines.append(f"{result.command}\n{result.output}")
            elif result.error:
                output_lines.append(f"{result.command}\n{result.error}")
        joined = "\n\n".join(output_lines)
        rag_hint = ""
        if rag_matches:
            top = rag_matches[0]
            rag_hint = f" Related KB: {top.issue} -> {top.response}"
        if self.llm_helper.available() and joined:
            system_prompt = (
                "You are a Windows diagnostics assistant. Summarize the issue based on command output."
            )
            user_prompt = (
                f"Issue: {issue_text}\nType: {issue_type}\nOutput:\n{joined}\n{rag_hint}"
            )
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

    def _rag_keywords(self, issue_text):
        text = issue_text.lower()
        keywords = []
        if "password" in text:
            keywords.append("password")
        if "blue screen" in text or "bluescreen" in text or "bsod" in text:
            keywords.append("blue screen")
        if "wifi" in text or "wi-fi" in text or "network" in text or "internet" in text:
            keywords.append("wi-fi")
        if "blue tooth" in text or "bluetooth" in text or "blutooth" in text:
            keywords.append("bluetooth")
        if "printer" in text:
            keywords.append("printer")
        if "install" in text or "setup" in text:
            keywords.append("install")
        if "performance" in text or "slow" in text:
            keywords.append("performance")
        return keywords


class FixPlannerAgent:
    def __init__(self, llm_helper, gatekeeper):
        self.llm_helper = llm_helper
        self.gatekeeper = gatekeeper

    def propose(self, issue_text, diagnosis):
        issue_type = diagnosis.issue_type
        commands = self._fix_commands(
            issue_type, diagnosis.install_app, diagnosis, diagnosis.fix_stage
        )
        summary = self._summarize(issue_text, issue_type, diagnosis, commands)
        return FixPlan(issue_type=issue_type, summary=summary, commands=commands)

    def _fix_commands(self, issue_type, install_app, diagnosis, fix_stage):
        if issue_type == "disk_space":
            return [
                "Remove-Item $env:TEMP\\* -Recurse -Force -ErrorAction SilentlyContinue",
                "Clear-RecycleBin -Force",
            ]
        if issue_type == "network":
            return self._network_fix_commands(diagnosis, fix_stage)
        if issue_type == "bluetooth":
            return self._bluetooth_fix_commands(diagnosis, fix_stage)
        if issue_type == "install_app" and install_app:
            return [
                f'winget install --name "{install_app}" --accept-package-agreements --accept-source-agreements',
            ]
        return []

    def _summarize(self, issue_text, issue_type, diagnosis, commands):
        rag_text = ""
        if diagnosis.rag_matches:
            top = diagnosis.rag_matches[0]
            rag_text = f"Knowledge base ({top.conversation_id}): {top.response}"
        web_text = ""
        if diagnosis.issue_type != "system_info" and diagnosis.web_results:
            top_web = diagnosis.web_results[0]
            web_text = f"Web source: {top_web.title} ({top_web.url})"
        if not commands:
            return self._answer_question(issue_text, diagnosis)
        if self.llm_helper.available():
            system_prompt = (
                "You are a Windows support agent. Propose a concise fix plan for the user."
            )
            command_text = "\n".join(commands) if commands else "No commands."
            user_prompt = (
                f"Issue: {issue_text}\nType: {issue_type}\nFindings: {diagnosis.findings}\n"
                f"{rag_text}\n{web_text}\nCommands:\n{command_text}\n"
                "Summarize the fix plan and request confirmation."
            )
            summary = self.llm_helper.generate(system_prompt, user_prompt)
            if summary:
                return summary
        if issue_type == "disk_space":
            return "Proposed fix: clear temp files and recycle bin to free space. Apply?"
        if issue_type == "network":
            cause = self._network_root_cause(diagnosis)
            fix_hint = "Proposed fix: reset DNS and network stack, then restart Wi-Fi service. Apply?"
            if diagnosis.fix_stage == 2:
                fix_hint = "Proposed fix: enable Wi-Fi adapter and renew IP. Apply?"
            if diagnosis.fix_stage >= 3:
                fix_hint = "Proposed fix: cycle Wi-Fi adapter and restart network services. Apply?"
            parts = []
            if cause:
                parts.append(f"Likely cause: {cause}")
            parts.append(fix_hint)
            return "\n".join(parts)
        if issue_type == "bluetooth":
            diagnosis_summary = self._bluetooth_summary(diagnosis)
            root_cause = self._bluetooth_root_cause(diagnosis)
            fix_hint = (
                "Proposed fix: restart Bluetooth service, cycle the adapter, and rescan devices. Apply?"
            )
            if diagnosis.fix_stage >= 4:
                fix_hint = (
                    "Proposed fix: remove and rescan Bluetooth device (admin may be required). Apply?"
                )
            parts = []
            if diagnosis_summary:
                parts.append(diagnosis_summary)
            if root_cause:
                parts.append(f"Likely cause: {root_cause}")
            parts.append(fix_hint)
            return "\n".join(parts)
        if issue_type == "install_app":
            if not diagnosis.install_app:
                return "Please specify the app name you want to install."
            if "office" in diagnosis.install_app.lower():
                return (
                    "Proposed install: use winget to install Microsoft Office. "
                    "Note: desktop Office may require a subscription/license to activate. Apply?"
                )
            return f'Proposed install: use winget to install "{diagnosis.install_app}". Apply?'
        if diagnosis.blocked_commands:
            return self._handle_blocked(diagnosis)
        return self._answer_question(issue_text, diagnosis)

    def _handle_blocked(self, diagnosis):
        alternatives = []
        for cmd in diagnosis.blocked_commands:
            if "Get-ComputerInfo" in cmd:
                alternatives.append("Get-CimInstance Win32_OperatingSystem")
            if "Get-NetIPAddress" in cmd:
                alternatives.append("ipconfig")
        if alternatives:
            alt_text = "; ".join(alternatives)
            return f"Some diagnostics were blocked. Try safe alternatives: {alt_text}."
        return "Some diagnostics were blocked by policy. Escalate to a human for approval."

    def _answer_question(self, issue_text, diagnosis):
        normalized = issue_text.lower()
        extracted = self._extract_answer(normalized, diagnosis)
        if extracted:
            return extracted

        if self._is_system_info_query(normalized):
            return "I couldn't find that in diagnostics. Please re-run diagnostics."

        rag_text = ""
        if diagnosis.rag_matches:
            top = diagnosis.rag_matches[0]
            if top.score >= 0.2:
                rag_text = f"{top.conversation_id}: {top.response}"

        web_text = ""
        if diagnosis.issue_type != "system_info" and diagnosis.web_results:
            top_web = diagnosis.web_results[0]
            web_text = f"{top_web.title} ({top_web.url})"

        combined = ""
        if self.llm_helper.available():
            system_prompt = (
                "You synthesize answers from a knowledge base and web hints. "
                "Pick the most relevant, safest guidance and keep it concise."
            )
            user_prompt = (
                f"Question: {issue_text}\n"
                f"Diagnostics: {diagnosis.findings}\n"
                f"KB: {rag_text}\n"
                f"Web: {web_text}\n"
                "Provide the best single answer with concrete steps if applicable."
            )
            combined = self.llm_helper.generate(system_prompt, user_prompt)
        if not combined:
            if rag_text:
                combined = self._format_kb_steps(rag_text)
                if web_text:
                    combined = f"{combined}\nAdditional reference: {web_text}"
            elif web_text and diagnosis.issue_type == "system_info":
                combined = f"Suggested reference: {web_text}"
        if combined:
            sources_text = f"{rag_text} {web_text}".strip()
            return self.gatekeeper.finalize(
                diagnosis.issue_type, issue_text, combined, sources_text
            )

        if rag_text:
            formatted = self._format_kb_steps(rag_text)
            return self.gatekeeper.finalize(
                diagnosis.issue_type, issue_text, formatted, rag_text
            )
        if web_text and diagnosis.issue_type == "system_info":
            formatted = f"Suggested reference: {web_text}"
            return self.gatekeeper.finalize(
                diagnosis.issue_type, issue_text, formatted, web_text
            )
        return "I couldn't find a direct answer. Try rephrasing the question."

    def _extract_answer(self, normalized_question, diagnosis):
        outputs = []
        for result in diagnosis.command_results:
            if result.output:
                outputs.append(result.output)
        combined = "\n".join(outputs)

        if "os build" in normalized_question or "build version" in normalized_question:
            for line in combined.splitlines():
                if "osbuildnumber" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value
                if "osversion" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value
            for line in combined.splitlines():
                if "ip" in line.lower():
                    continue
                for token in line.split():
                    if token.count(".") >= 2 and all(part.isdigit() for part in token.split(".")):
                        return token

        if "ip address" in normalized_question or "my ip" in normalized_question:
            for line in combined.splitlines():
                if "ipaddress" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value
            for line in combined.splitlines():
                if line.strip().startswith(("10.", "172.", "192.168.")):
                    continue
                tokens = [token for token in line.replace(",", " ").split() if "." in token]
                for token in tokens:
                    if token.count(".") == 3 and all(part.isdigit() for part in token.split(".")):
                        return token
            return "Public IP not found in diagnostics. Ask for local IP or enable external lookup."

        if "os version" in normalized_question:
            for line in combined.splitlines():
                if "osversion" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            return value

        if "cpu" in normalized_question or "processor" in normalized_question:
            for line in combined.splitlines():
                if line.lower().startswith("name") and ":" in line:
                    parts = line.split(":", 1)
                    value = parts[1].strip()
                    if value:
                        return value
            for line in combined.splitlines():
                if "name" in line.lower():
                    parts = [part.strip() for part in line.split() if part.strip()]
                    if len(parts) > 1 and parts[0].lower() != "name":
                        return " ".join(parts)

        if "ram" in normalized_question or "memory" in normalized_question:
            total_kb = None
            free_kb = None
            for line in combined.splitlines():
                if "totalvisiblememorysize" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        total_kb = int(parts[1].strip())
                if "freephysicalmemory" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        free_kb = int(parts[1].strip())
            if free_kb is not None:
                free_gb = free_kb / 1024 / 1024
                return f"{free_gb:.1f} GB"
            if total_kb is not None:
                total_gb = total_kb / 1024 / 1024
                return f"{total_gb:.1f} GB"

        if "how much ram" in normalized_question or "ram do i have" in normalized_question:
            total_kb = None
            for line in combined.splitlines():
                if "totalvisiblememorysize" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        total_kb = int(parts[1].strip())
            if total_kb is not None:
                total_gb = total_kb / 1024 / 1024
                return f"{total_gb:.1f} GB"

        if (
            "details about my computer" in normalized_question
            or "details about my system" in normalized_question
            or "system info" in normalized_question
            or "about my system" in normalized_question
            or "tell me about my system" in normalized_question
            or "system information" in normalized_question
            or "system infromation" in normalized_question
            or "computer info" in normalized_question
            or ("detail" in normalized_question and ("computer" in normalized_question or "copmuter" in normalized_question or "pc" in normalized_question or "system" in normalized_question))
        ):
            summary = []
            os_name = self._find_value(combined, "osname")
            os_version = self._find_value(combined, "osversion")
            os_build = self._find_value(combined, "osbuildnumber")
            cpu_name = self._find_value(combined, "name")
            if os_name:
                summary.append(f"OS: {os_name}")
            if os_version:
                summary.append(f"OS Version: {os_version}")
            if os_build:
                summary.append(f"OS Build: {os_build}")
            if cpu_name:
                summary.append(f"CPU: {cpu_name}")
            total_kb = None
            free_kb = None
            for line in combined.splitlines():
                if "totalvisiblememorysize" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        total_kb = int(parts[1].strip())
                if "freephysicalmemory" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[1].strip().isdigit():
                        free_kb = int(parts[1].strip())
            if total_kb is not None:
                total_gb = total_kb / 1024 / 1024
                summary.append(f"RAM Total: {total_gb:.1f} GB")
            if free_kb is not None:
                free_gb = free_kb / 1024 / 1024
                summary.append(f"RAM Free: {free_gb:.1f} GB")
            ips = []
            for line in combined.splitlines():
                if "ipaddress" in line.lower():
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        value = parts[1].strip()
                        if value:
                            ips.append(value)
            if ips:
                summary.append(f"IP: {', '.join(ips)}")
            disks = self._parse_psdrive(combined)
            if disks:
                summary.extend(disks)
            if summary:
                return self._format_system_info(summary)

        return ""

    def _find_value(self, combined, key):
        for line in combined.splitlines():
            if line.lower().startswith(key) and ":" in line:
                parts = line.split(":", 1)
                value = parts[1].strip()
                if value:
                    return value
        return ""

    def _format_system_info(self, summary):
        rows = []
        for item in summary:
            if ":" in item:
                key, value = item.split(":", 1)
                rows.append((key.strip(), value.strip()))
        if not rows:
            return "\n".join(summary)
        html_rows = []
        for key, value in rows:
            html_rows.append(f"<tr><td><b>{key}</b></td><td>{value}</td></tr>")
        table = (
            "<table style='width:100%; border-collapse:collapse;'>"
            + "".join(html_rows)
            + "</table>"
        )
        return table

    def _parse_psdrive(self, combined):
        disks = []
        current = {}
        for line in combined.splitlines():
            if line.lower().startswith("name") and ":" in line:
                if current:
                    disk_line = self._format_disk(current)
                    if disk_line:
                        disks.append(disk_line)
                    current = {}
                current["name"] = line.split(":", 1)[1].strip()
            if line.lower().startswith("used") and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value.isdigit():
                    current["used"] = int(value)
            if line.lower().startswith("free") and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value.isdigit():
                    current["free"] = int(value)
        if current:
            disk_line = self._format_disk(current)
            if disk_line:
                disks.append(disk_line)
        return disks

    def _format_disk(self, data):
        name = data.get("name")
        used = data.get("used")
        free = data.get("free")
        if not name or used is None or free is None:
            return ""
        used_gb = used / 1024 / 1024 / 1024
        free_gb = free / 1024 / 1024 / 1024
        return f"Disk {name}: Used {used_gb:.1f} GB, Free {free_gb:.1f} GB"

    def _format_kb_steps(self, rag_text):
        if ":" in rag_text:
            kb_id, response = rag_text.split(":", 1)
            response = response.strip()
            steps = [step.strip() for step in response.replace(";", ".").split(".") if step.strip()]
            lines = [f"Recommended steps (from {kb_id.strip()}):"]
            for step in steps:
                lines.append(f"- {step}")
            return "\n".join(lines)
        return f"Recommended steps: {rag_text}"

    def _bluetooth_fix_commands(self, diagnosis, fix_stage):
        commands = []
        status, instance_id, service_status = self._parse_bluetooth_state(diagnosis)
        stage = max(1, min(fix_stage, 4))
        if stage == 1:
            if service_status and service_status.lower() != "running":
                commands.append("Start-Service bthserv")
            commands.append("Restart-Service bthserv")
        if stage == 2:
            if instance_id and status and status.lower() in ("disabled", "error", "unknown"):
                commands.append(f'Disable-PnpDevice -InstanceId "{instance_id}" -Confirm:$false')
                commands.append(f'Enable-PnpDevice -InstanceId "{instance_id}" -Confirm:$false')
            commands.append("pnputil /scan-devices")
        if stage == 3:
            commands.extend(
                [
                    "Stop-Service bthserv -Force",
                    "Start-Service bthserv",
                    "Restart-Service bthserv",
                ]
            )
        if stage == 4:
            # Stage 4: driver refresh attempt (admin likely required)
            if instance_id:
                commands.extend(
                    [
                        f'pnputil /remove-device "{instance_id}"',
                        "pnputil /scan-devices",
                    ]
                )
        return commands

    def _network_fix_commands(self, diagnosis, fix_stage):
        stage = max(1, min(fix_stage, 4))
        if stage == 1:
            return [
                "ipconfig /flushdns",
                "netsh winsock reset",
                "netsh int ip reset",
                "Restart-Service WlanSvc",
            ]
        if stage == 2:
            return [
                "Enable-NetAdapter -Name \"Wi-Fi\" -Confirm:$false",
                "ipconfig /release",
                "ipconfig /renew",
            ]
        if stage == 3:
            return [
                "Disable-NetAdapter -Name \"Wi-Fi\" -Confirm:$false",
                "Enable-NetAdapter -Name \"Wi-Fi\" -Confirm:$false",
                "Restart-Service WlanSvc",
            ]
        return [
            "netsh int ip reset",
            "netsh winsock reset",
        ]

    def _network_root_cause(self, diagnosis):
        for result in diagnosis.command_results:
            output = (result.output or "").lower()
            if "wifi" in output and "disabled" in output:
                return "Wi-Fi adapter is disabled"
            if "there is no wireless interface" in output:
                return "Wireless adapter not detected"
            if "media disconnected" in output:
                return "No active network connection"
        return ""

    def _bluetooth_summary(self, diagnosis):
        status, _, service_status = self._parse_bluetooth_state(diagnosis)
        parts = []
        if status:
            parts.append(f"Adapter status: {status}")
        if service_status:
            parts.append(f"Bluetooth service: {service_status}")
        return " | ".join(parts)

    def _bluetooth_root_cause(self, diagnosis):
        status, _, service_status = self._parse_bluetooth_state(diagnosis)
        if status and status.lower() in ("error", "disabled", "unknown"):
            return "Bluetooth adapter is not healthy"
        if service_status and service_status.lower() != "running":
            return "Bluetooth service is not running"
        return ""

    def _parse_bluetooth_state(self, diagnosis=None):
        status = ""
        instance_id = ""
        service_status = ""
        results = diagnosis.command_results if diagnosis else []
        for result in results:
            if "Get-PnpDevice -Class Bluetooth" in result.command and result.output:
                for line in result.output.splitlines():
                    if line.lower().startswith("status"):
                        status = line.split(":", 1)[1].strip()
                    if line.lower().startswith("instanceid"):
                        instance_id = line.split(":", 1)[1].strip()
            if "Get-Service bthserv" in result.command and result.output:
                for line in result.output.splitlines():
                    if line.lower().startswith("status"):
                        service_status = line.split(":", 1)[1].strip()
        return status, instance_id, service_status

    def _is_system_info_query(self, normalized_question):
        if "os" in normalized_question and ("build" in normalized_question or "version" in normalized_question):
            return True
        if "ip address" in normalized_question or "my ip" in normalized_question:
            return True
        if "system info" in normalized_question or "computer info" in normalized_question or "about my system" in normalized_question or "tell me about my system" in normalized_question:
            return True
        if "cpu" in normalized_question or "processor" in normalized_question:
            return True
        if "ram" in normalized_question or "memory" in normalized_question:
            return True
        if "details about my computer" in normalized_question or "details about my system" in normalized_question:
            return True
        if "detail" in normalized_question and (
            "computer" in normalized_question
            or "copmuter" in normalized_question
            or "pc" in normalized_question
            or "system" in normalized_question
        ):
            return True
        return False


class ExecutorAgent:
    def __init__(self, runner):
        self.runner = runner

    def apply(self, fix_plan):
        results = [self.runner.run(cmd) for cmd in fix_plan.commands]
        had_errors = any(
            (result.error or (result.return_code not in (0, None))) for result in results
        )
        verified, message = self._verify(fix_plan.issue_type)
        success = (not had_errors) and verified
        return ExecutionResult(
            success=success,
            command_results=results,
            verified=verified,
            verification_message=message,
        )

    def _verify(self, issue_type):
        if issue_type == "bluetooth":
            return self._verify_bluetooth()
        return True, "No verification required."

    def _verify_bluetooth(self):
        adapter = self.runner.run(
            "Import-Module PnpDevice -ErrorAction SilentlyContinue; "
            "Get-PnpDevice -Class Bluetooth | Select-Object Status | Format-List"
        )
        service = self.runner.run(
            "Get-Service bthserv | Select-Object Status | Format-List"
        )
        adapter_ok = "ok" in (adapter.output or "").lower()
        service_ok = "running" in (service.output or "").lower()
        if adapter_ok and service_ok:
            return True, "Bluetooth verified: adapter OK and service running."
        return False, "Bluetooth still not healthy after fix attempt."


def build_agents(allowlist_path, denylist_path, logger):
    command_filter = CommandFilter(allowlist_path, denylist_path)
    runner = CommandRunner(command_filter, logger)
    llm_helper = AutoGenHelper()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "..", "tech_support_dataset.csv")
    cache_path = os.path.join(base_dir, "..", "tech_support_dataset.vectors.pkl")
    rag = TechSupportRAG(csv_path, cache_path=cache_path, require_cache=True)
    web_search = WebSearch()
    orchestrator = OrchestratorAgent()
    research = ResearchAgent(rag, web_search)
    action = ActionAgent(runner)
    gatekeeper = GatekeeperAgent(llm_helper)
    return (
        DiagnosisAgent(orchestrator, research, action, llm_helper),
        FixPlannerAgent(llm_helper, gatekeeper),
        ExecutorAgent(runner),
        llm_helper,
    )
