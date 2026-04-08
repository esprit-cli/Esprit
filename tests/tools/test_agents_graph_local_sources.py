from __future__ import annotations

import threading
from types import SimpleNamespace

from esprit.agents.state import AgentState
from esprit.tools.agents_graph import agents_graph_actions as mod


def test_create_agent_inherits_parent_local_sources(monkeypatch) -> None:
    captured: dict[str, object] = {}
    thread_targets: list[tuple] = []

    class FakeEspritAgent:
        def __init__(self, config: dict[str, object]) -> None:
            captured["config"] = config
            self.state = config["state"]
            self.non_interactive = config.get("non_interactive", False)
            self.local_sources = config.get("local_sources", [])
            self.llm_config = config["llm_config"]

    class FakeThread:
        def __init__(self, *args, **kwargs) -> None:
            thread_targets.append((kwargs.get("target"), kwargs.get("args")))

        def start(self) -> None:
            return None

    parent_state = AgentState(agent_name="Root", is_whitebox=True)
    parent_state.add_message("user", "scan this codebase")
    parent_state.sandbox_id = "sandbox-root"
    parent_state.sandbox_token = "token-root"
    parent_state.sandbox_info = {
        "workspace_id": "sandbox-root",
        "api_url": "https://api.example.test/sandbox/sandbox-root",
    }
    parent_agent = SimpleNamespace(
        state=parent_state,
        llm_config=SimpleNamespace(timeout=123, scan_mode="standard"),
        non_interactive=True,
        local_sources=[{"source_path": "/tmp/src", "workspace_subdir": "src"}],
    )

    old_graph = {
        "nodes": mod._agent_graph["nodes"].copy(),
        "edges": mod._agent_graph["edges"][:],
    }
    old_instances = mod._agent_instances.copy()
    old_running = mod._running_agents.copy()

    try:
        mod._agent_graph["nodes"] = {
            parent_state.agent_id: {"id": parent_state.agent_id, "name": "Root", "status": "running"}
        }
        mod._agent_graph["edges"] = []
        mod._agent_instances.clear()
        mod._agent_instances[parent_state.agent_id] = parent_agent
        mod._running_agents.clear()

        monkeypatch.setattr("esprit.agents.EspritAgent", FakeEspritAgent)
        monkeypatch.setattr(threading, "Thread", FakeThread)

        result = mod.create_agent(
            parent_state,
            task="Inspect remediation target",
            name="Fixing Agent",
        )
    finally:
        mod._agent_graph["nodes"] = old_graph["nodes"]
        mod._agent_graph["edges"] = old_graph["edges"]
        mod._agent_instances.clear()
        mod._agent_instances.update(old_instances)
        mod._running_agents.clear()
        mod._running_agents.update(old_running)

    assert result["success"] is True
    child_config = captured["config"]
    assert isinstance(child_config, dict)
    assert child_config["non_interactive"] is True
    assert child_config["local_sources"] == parent_agent.local_sources
    assert child_config["local_sources"] is not parent_agent.local_sources

    child_state = child_config["state"]
    assert isinstance(child_state, AgentState)
    assert child_state.is_whitebox is True
    assert child_state.sandbox_id == "sandbox-root"
    assert child_state.sandbox_token == "token-root"
    assert child_state.sandbox_info == parent_state.sandbox_info
    assert child_state.sandbox_info is not parent_state.sandbox_info
    assert child_config["llm_config"].scan_mode == "standard"
    assert thread_targets
