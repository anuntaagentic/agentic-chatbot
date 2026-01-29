import json
import logging
from datetime import datetime
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from .command_filter import CommandFilter
from .rag import TechSupportRAG
from .logging_utils import get_log_dir
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
class DiagnosticPlanResult:
    issue_type: str
    summary: str
    plan_steps: List[PlanStep] = field(default_factory=list)
    install_app: str = ""
    rag_matches: List[object] = field(default_factory=list)
    web_results: List[object] = field(default_factory=list)
    web_query: str = ""
    web_error: str = ""
    web_count: int = 0
    sop_used: str = ""
    is_chat: bool = False


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
        model = os.environ.get("GROQ_MODEL", "").strip()
        self.model = model or "llama3-70b-8192"
        base_url = os.environ.get("GROQ_BASE_URL", "").strip()
        self.base_url = base_url or "https://api.groq.com/openai/v1"
        self.logger = logging.getLogger("agentic_chatbot")

    def available(self):
        return bool(self.api_key)

    def generate(self, system_prompt, user_prompt):
        if not self.available():
            self.logger.info("LLM unavailable: GROQ_API_KEY missing.")
            return ""
        if "groq.com" in self.base_url and self.model.startswith("openai/"):
            self.logger.info(
                "LLM model '%s' is not compatible with Groq base_url. Falling back.",
                self.model,
            )
            self.model = "llama3-70b-8192"
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
                self.logger.info("LLM returned no messages.")
                return ""
            self._log_autogen_conversation(system_prompt, user_prompt, messages)
            content = ""
            for message in reversed(messages):
                candidate = message.get("content", "")
                if candidate:
                    content = candidate
                    break
            if not content:
                self.logger.info("LLM returned empty content.")
            return content.strip()
        except Exception as exc:
            self.logger.info("LLM call failed: %s", exc)
            return ""

    def _log_autogen_conversation(self, system_prompt, user_prompt, messages):
        try:
            log_dir = get_log_dir()
            log_path = os.path.join(
                log_dir, f"autogen-{datetime.now().strftime('%Y-%m-%d')}.log"
            )
            lines = []
            lines.append(f"{datetime.now().isoformat()} SYSTEM: {system_prompt}")
            lines.append(f"{datetime.now().isoformat()} USER: {user_prompt}")
            for message in messages:
                role = message.get("role", "assistant")
                content = message.get("content", "")
                lines.append(f"{datetime.now().isoformat()} {role.upper()}: {content}")
            lines.append("-" * 80)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except Exception as exc:
            self.logger.info("Failed to write autogen log: %s", exc)


class OrchestratorAgent:
    def __init__(self, llm_helper):
        self.fix_stage = 1
        self.llm_helper = llm_helper

    def build_plan(self, issue_text, sop_text, web_results):
        issue_type, install_app = self._classify_issue(issue_text)
        plan_steps, summary = self._diagnostic_plan(
            issue_text, issue_type, install_app, sop_text, web_results
        )
        return issue_type, install_app, plan_steps, summary

    def _classify_issue(self, issue_text):
        if not self.llm_helper.available():
            return "general", ""
        system_prompt = (
            "You are a Windows support classifier. Return JSON only. "
            "Use issue_type from: system_info, install_app, network, bluetooth, performance, account, app_error, general, chitchat."
        )
        user_prompt = (
            f"Issue: {issue_text}\n"
            "Return JSON with keys: issue_type, install_app (empty if not install)."
        )
        response = self.llm_helper.generate(system_prompt, user_prompt)
        payload = self._extract_json(response)
        if not payload:
            self.llm_helper.logger.info(
                "LLM classification parse failed. Raw response: %s",
                (response or "")[:800],
            )
        issue_type = (payload.get("issue_type") or "general").strip()
        install_app = (payload.get("install_app") or "").strip()
        return issue_type, install_app

    def _diagnostic_plan(self, issue_text, issue_type, install_app, sop_text, web_results):
        if not self.llm_helper.available():
            return [], "LLM unavailable; unable to generate a diagnostic script."
        web_lines = []
        for result in web_results or []:
            title = getattr(result, "title", "")
            snippet = getattr(result, "snippet", "")
            url = getattr(result, "url", "")
            line = f"{title} | {snippet} | {url}".strip(" |")
            if line:
                web_lines.append(line)
        web_text = "\n".join(web_lines[:5])
        sop_context = sop_text or "No SOP match found."
        system_prompt = (
            "You are a Windows diagnostics planner. Use the SOP if provided and the web hints. "
            "Return JSON only with keys: summary, commands. "
            "Commands must be read-only PowerShell commands for diagnostics (no changes). "
            "commands is a list of objects: {\"description\": \"...\", \"command\": \"...\"}."
        )
        user_prompt = (
            f"Issue: {issue_text}\n"
            f"Type: {issue_type}\n"
            f"Install app: {install_app}\n"
            f"SOP: {sop_context}\n"
            f"Web hints:\n{web_text}\n"
            "If the issue is just a greeting or small talk, return a friendly summary and an empty commands list."
            "Otherwise generate a diagnostic script that checks services, logs, adapters, system metrics, and app status as relevant."
        )
        response = self.llm_helper.generate(system_prompt, user_prompt)
        payload = self._extract_json(response)
        if not payload:
            self.llm_helper.logger.info(
                "LLM diagnostics plan parse failed. Raw response: %s",
                (response or "")[:1200],
            )
        summary = (payload.get("summary") or "").strip()
        if issue_type == "chitchat" and not summary:
            summary = "Hi! How can I help you with your Windows issue today?"
        plan_steps = []
        for item in payload.get("commands", []) or []:
            if isinstance(item, dict):
                description = (item.get("description") or "").strip()
                command = (item.get("command") or "").strip()
            else:
                description = ""
                command = str(item).strip()
            if command:
                plan_steps.append(PlanStep(description or "Run diagnostic command.", command))
        if not summary:
            summary = "Generated a diagnostic script based on the SOP and web references."
        return plan_steps, summary

    def _extract_json(self, text):
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}


class ResearchAgent:
    def __init__(self, rag, web_search):
        self.rag = rag
        self.web_search = web_search

    def fetch(self, issue_text, keywords):
        rag_matches = []
        if self.rag and self.rag.matrix is not None:
            rag_matches = self.rag.search(issue_text, keywords=keywords)
        web_results = []
        web_query = f"{issue_text} Windows 11 troubleshooting steps"
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
        if not step.command or not step.command.strip():
            return False, "empty command"
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

    def prepare_plan(self, issue_text):
        research_data = self.research.fetch(issue_text, self._rag_keywords(issue_text))
        rag_matches = research_data["rag_matches"]
        sop_text = self._select_sop(rag_matches)
        issue_type, install_app, plan_steps, summary = self.orchestrator.build_plan(
            issue_text, sop_text, research_data["web_results"]
        )
        return DiagnosticPlanResult(
            issue_type=issue_type,
            summary=summary,
            plan_steps=plan_steps,
            install_app=install_app,
            rag_matches=rag_matches,
            web_results=research_data["web_results"],
            web_query=research_data["web_query"],
            web_error=research_data["web_error"],
            web_count=research_data["web_count"],
            sop_used=sop_text,
            is_chat=issue_type == "chitchat",
        )

    def execute(self, issue_text, plan):
        validated_steps, results = self.action.execute_plan(plan.plan_steps)
        findings = self._summarize(
            issue_text, plan.issue_type, results, plan.rag_matches, plan.web_results
        )
        return DiagnosisResult(
            issue_type=plan.issue_type,
            findings=findings,
            action_plan=validated_steps,
            command_results=results,
            install_app=plan.install_app,
            rag_matches=plan.rag_matches,
            web_results=plan.web_results,
            web_query=plan.web_query,
            web_error=plan.web_error,
            web_count=plan.web_count,
            blocked_commands=[result.command for result in results if not result.allowed],
            fix_stage=getattr(self.orchestrator, "fix_stage", 1),
        )

    def _summarize(self, issue_text, issue_type, results, rag_matches, web_results):
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
            rag_hint = f" Related SOP: {top.issue} -> {top.response}"
        web_hint = ""
        if web_results:
            top_web = web_results[0]
            web_hint = f" Web hint: {top_web.title} | {top_web.url}"
        if self.llm_helper.available() and joined:
            system_prompt = (
            "You are a Windows diagnostics assistant. Provide a short, user-friendly summary in passive voice only."
            )
            user_prompt = (
                f"Issue: {issue_text}\nType: {issue_type}\nOutput:\n{joined}\n{rag_hint}\n{web_hint}"
            )
            summary = self.llm_helper.generate(system_prompt, user_prompt)
            if summary:
                return summary
        return "Diagnostics complete."

    def _select_sop(self, rag_matches):
        if not rag_matches:
            return ""
        top = rag_matches[0]
        if getattr(top, "score", 0) >= 0.2:
            return f"{top.conversation_id}: {top.issue} -> {top.response}"
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
        commands, summary = self._fix_plan(issue_text, diagnosis)
        return FixPlan(issue_type=issue_type, summary=summary, commands=commands)

    def _fix_plan(self, issue_text, diagnosis):
        if not self.llm_helper.available():
            return [], "LLM unavailable; unable to generate a fix script."
        rag_text = ""
        if diagnosis.rag_matches:
            top = diagnosis.rag_matches[0]
            rag_text = f"SOP ({top.conversation_id}): {top.response}"
        web_text = ""
        if diagnosis.web_results:
            top_web = diagnosis.web_results[0]
            web_text = f"Web source: {top_web.title} ({top_web.url})"
        system_prompt = (
            "You are a Windows support agent. Propose a safe resolution script based on diagnostics. "
            "Return JSON only with keys: summary, commands. "
            "The summary must be short, user-friendly, and in passive voice only. "
            "Commands must be PowerShell commands for remediation; avoid destructive actions."
        )
        user_prompt = (
            f"Issue: {issue_text}\nType: {diagnosis.issue_type}\nFindings: {diagnosis.findings}\n"
            f"{rag_text}\n{web_text}\nFix stage: {diagnosis.fix_stage}\n"
            "Return JSON with summary and commands."
        )
        response = self.llm_helper.generate(system_prompt, user_prompt)
        payload = self._extract_json(response)
        if not payload:
            self.llm_helper.logger.info(
                "LLM fix plan parse failed. Raw response: %s",
                (response or "")[:1200],
            )
        summary = (payload.get("summary") or "").strip()
        commands = []
        for item in payload.get("commands", []) or []:
            if isinstance(item, dict):
                cmd = (item.get("command") or "").strip()
            else:
                cmd = str(item).strip()
            if cmd:
                commands.append(cmd)
        if not commands:
            return [], self._answer_question(issue_text, diagnosis)
        if summary:
            return commands, summary
        return commands, "Proposed fix script ready. Apply?"

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

    def _extract_json(self, text):
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            match = re.search(r"\{.*\}", text, re.S)
            if not match:
                return {}
            try:
                return json.loads(match.group(0))
            except Exception:
                return {}

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
        return True, "Verification skipped; please confirm the issue is resolved."


def build_agents(allowlist_path, denylist_path, logger):
    command_filter = CommandFilter(allowlist_path, denylist_path)
    runner = CommandRunner(command_filter, logger)
    llm_helper = AutoGenHelper()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "..", "tech_support_dataset.csv")
    cache_path = os.path.join(base_dir, "..", "tech_support_dataset.vectors.pkl")
    rag = TechSupportRAG(csv_path, cache_path=cache_path, require_cache=True)
    web_search = WebSearch()
    orchestrator = OrchestratorAgent(llm_helper)
    research = ResearchAgent(rag, web_search)
    action = ActionAgent(runner)
    gatekeeper = GatekeeperAgent(llm_helper)
    return (
        DiagnosisAgent(orchestrator, research, action, llm_helper),
        FixPlannerAgent(llm_helper, gatekeeper),
        ExecutorAgent(runner),
        llm_helper,
    )
