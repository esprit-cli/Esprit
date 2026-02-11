"""Bridge between Tracer and WebSocket clients — polls for changes, pushes deltas."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket

    from esprit.telemetry.tracer import Tracer


logger = logging.getLogger(__name__)


class TracerBridge:
    """Polls the Tracer every 500ms and pushes delta messages to WS clients."""

    POLL_INTERVAL = 0.5  # seconds

    def __init__(self, tracer: Tracer) -> None:
        self._tracer = tracer
        self._clients: list[WebSocket] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

        # Tracking counters for delta detection
        self._last_agent_count = 0
        self._last_agent_statuses: dict[str, str] = {}
        self._last_tool_count = 0
        self._last_chat_count = 0
        self._last_vuln_count = 0
        self._last_streaming: dict[str, str] = {}
        self._last_screenshot_ids: dict[str, int] = {}
        self._last_stats_hash = ""
        self._last_scan_config: dict[str, Any] | None = None
        self._sent_final_report = False

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def add_client(self, ws: WebSocket) -> None:
        if ws not in self._clients:
            self._clients.append(ws)

    def remove_client(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._stopped = False
        self._task = loop.create_task(self._poll_loop())

    def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()

    # ------------------------------------------------------------------
    # Full state snapshot (sent on new WS connect)
    # ------------------------------------------------------------------

    def get_full_state(self) -> dict[str, Any]:
        t = self._tracer
        agents = self._serialize_agents()
        tools = self._serialize_tools()
        chat = list(t.chat_messages)
        vulns = list(t.vulnerability_reports)
        streaming = dict(t.streaming_content)
        stats = self._get_stats()

        # Track which agents have screenshots
        screenshot_agents: list[str] = []
        for agent_id in t.latest_browser_screenshots:
            screenshot_agents.append(agent_id)

        return {
            "type": "full_state",
            "agents": agents,
            "tools": tools,
            "chat": chat,
            "vulnerabilities": vulns,
            "streaming": streaming,
            "screenshot_agents": screenshot_agents,
            "stats": stats,
            "scan_config": t.scan_config,
            "final_report": t.final_scan_result,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # Screenshot REST endpoint data
    # ------------------------------------------------------------------

    def get_screenshot(self, agent_id: str) -> dict[str, Any]:
        t = self._tracer
        exec_id = t.latest_browser_screenshots.get(agent_id)
        if exec_id and exec_id in t.tool_executions:
            tool_data = t.tool_executions[exec_id]
            result = tool_data.get("result")
            if isinstance(result, dict):
                screenshot = result.get("screenshot")
                if screenshot and isinstance(screenshot, str) and screenshot != "[rendered]":
                    url = result.get("url") or tool_data.get("args", {}).get("url") or ""
                    return {"screenshot": screenshot, "url": url, "agent_id": agent_id}

        # Fallback scan
        best_exec_id = -1
        best_screenshot = None
        best_url = ""
        for eid, td in list(t.tool_executions.items()):
            if td.get("tool_name") != "browser_action":
                continue
            if td.get("agent_id") != agent_id:
                continue
            r = td.get("result")
            if not isinstance(r, dict):
                continue
            ss = r.get("screenshot")
            if not ss or not isinstance(ss, str) or ss == "[rendered]":
                continue
            if eid > best_exec_id:
                best_exec_id = eid
                best_screenshot = ss
                best_url = r.get("url") or td.get("args", {}).get("url") or ""

        if best_screenshot:
            return {"screenshot": best_screenshot, "url": best_url, "agent_id": agent_id}
        return {"screenshot": None, "url": "", "agent_id": agent_id}

    # ------------------------------------------------------------------
    # Delta polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self.POLL_INTERVAL)
            if not self._clients:
                continue
            try:
                deltas = self._detect_deltas()
                if deltas:
                    await self._broadcast(deltas)
            except Exception:  # noqa: BLE001
                logger.debug("Error in bridge poll loop", exc_info=True)

    def _detect_deltas(self) -> list[dict[str, Any]]:
        t = self._tracer
        deltas: list[dict[str, Any]] = []

        # Agents
        current_agent_count = len(t.agents)
        current_statuses = {aid: a.get("status", "") for aid, a in list(t.agents.items())}
        if current_agent_count != self._last_agent_count or current_statuses != self._last_agent_statuses:
            deltas.append({"type": "agents_update", "agents": self._serialize_agents()})
            self._last_agent_count = current_agent_count
            self._last_agent_statuses = current_statuses

        # Tools
        current_tool_count = len(t.tool_executions)
        if current_tool_count != self._last_tool_count:
            new_tools = self._serialize_tools(offset=self._last_tool_count)
            if new_tools:
                deltas.append({"type": "tools_update", "tools": new_tools})
            self._last_tool_count = current_tool_count

        # Chat
        current_chat_count = len(t.chat_messages)
        if current_chat_count != self._last_chat_count:
            new_messages = t.chat_messages[self._last_chat_count:]
            deltas.append({"type": "chat_update", "messages": list(new_messages)})
            self._last_chat_count = current_chat_count

        # Vulnerabilities
        current_vuln_count = len(t.vulnerability_reports)
        if current_vuln_count != self._last_vuln_count:
            new_vulns = t.vulnerability_reports[self._last_vuln_count:]
            deltas.append({"type": "vulnerability_update", "vulnerabilities": list(new_vulns)})
            self._last_vuln_count = current_vuln_count

        # Streaming content
        current_streaming = dict(t.streaming_content)
        if current_streaming != self._last_streaming:
            deltas.append({"type": "streaming_update", "streaming": current_streaming})
            self._last_streaming = current_streaming

        # Screenshots
        current_screenshots = dict(t.latest_browser_screenshots)
        if current_screenshots != self._last_screenshot_ids:
            for agent_id, exec_id in current_screenshots.items():
                old_id = self._last_screenshot_ids.get(agent_id)
                if old_id != exec_id:
                    deltas.append({"type": "screenshot_update", "agent_id": agent_id})
            self._last_screenshot_ids = current_screenshots

        # Stats
        stats = self._get_stats()
        stats_hash = json.dumps(stats, sort_keys=True)
        if stats_hash != self._last_stats_hash:
            deltas.append({"type": "stats_update", "stats": stats})
            self._last_stats_hash = stats_hash

        # Scan config
        if t.scan_config and t.scan_config != self._last_scan_config:
            deltas.append({"type": "scan_config_update", "scan_config": t.scan_config})
            self._last_scan_config = t.scan_config

        # Final report (scan complete)
        if t.final_scan_result and not self._sent_final_report:
            deltas.append({"type": "scan_complete", "final_report": t.final_scan_result})
            self._sent_final_report = True

        return deltas

    async def _broadcast(self, messages: list[dict[str, Any]]) -> None:
        payload = json.dumps({"type": "delta_batch", "deltas": messages})
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.remove_client(ws)

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------

    def _serialize_agents(self) -> list[dict[str, Any]]:
        agents = []
        t = self._tracer
        for agent_id, data in list(t.agents.items()):
            agents.append({
                "id": agent_id,
                "name": data.get("name", ""),
                "task": data.get("task", ""),
                "status": data.get("status", ""),
                "parent_id": data.get("parent_id"),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
                "has_screenshot": agent_id in t.latest_browser_screenshots,
                "tool_count": len(t.get_agent_tools(agent_id)),
                "compacting": agent_id in t.compacting_agents,
            })
        return agents

    def _serialize_tools(self, offset: int = 0) -> list[dict[str, Any]]:
        tools = []
        for exec_id, data in list(self._tracer.tool_executions.items()):
            if exec_id <= offset and offset > 0:
                continue
            tool_entry: dict[str, Any] = {
                "execution_id": exec_id,
                "agent_id": data.get("agent_id", ""),
                "tool_name": data.get("tool_name", ""),
                "status": data.get("status", ""),
                "timestamp": data.get("timestamp", ""),
                "completed_at": data.get("completed_at"),
            }
            # Include args but strip screenshot data to keep payload small
            args = data.get("args", {})
            tool_entry["args"] = {k: v for k, v in args.items() if k != "screenshot"}

            # Include result summary (strip screenshots)
            result = data.get("result")
            if isinstance(result, dict):
                tool_entry["result_summary"] = {
                    k: v for k, v in result.items() if k != "screenshot"
                }
                tool_entry["has_screenshot"] = bool(result.get("screenshot"))
            elif isinstance(result, str):
                tool_entry["result_summary"] = result[:500] if len(result) > 500 else result
            tools.append(tool_entry)
        return tools

    def _get_stats(self) -> dict[str, Any]:
        try:
            stats = self._tracer.get_total_llm_stats()
        except Exception:  # noqa: BLE001
            stats = {"total": {}, "total_tokens": 0, "max_context_tokens": 0}

        t = self._tracer

        # Compute tokens per second
        tokens_per_second = 0.0
        output_tokens = stats.get("total", {}).get("output_tokens", 0)
        if t.start_time and output_tokens > 0:
            try:
                start = datetime.fromisoformat(t.start_time)
                end = datetime.fromisoformat(t.end_time) if t.end_time else datetime.now(UTC)
                elapsed = (end - start).total_seconds()
                if elapsed > 0:
                    tokens_per_second = round(output_tokens / elapsed, 1)
            except Exception:  # noqa: BLE001
                pass

        # Get context limit
        context_limit = 128_000
        try:
            from esprit.llm.pricing import get_pricing_db
            model = t.run_metadata.get("model", "")
            if model:
                context_limit = get_pricing_db().get_context_limit(model)
        except Exception:  # noqa: BLE001
            pass

        return {
            "llm": stats,
            "agent_count": len(t.agents),
            "tool_count": t.get_real_tool_count(),
            "vuln_count": len(t.vulnerability_reports),
            "start_time": t.start_time,
            "end_time": t.end_time,
            "status": t.run_metadata.get("status", "running"),
            "max_context_tokens": stats.get("max_context_tokens", 0),
            "context_limit": context_limit,
            "tokens_per_second": tokens_per_second,
            "run_name": t.run_name,
            "run_id": t.run_id,
        }
