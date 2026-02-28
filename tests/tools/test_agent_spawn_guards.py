from __future__ import annotations

from copy import deepcopy

import pytest

from esprit.tools.agents_graph import agents_graph_actions as mod


class _DummyAgentState:
    def __init__(self, agent_id: str = "parent-agent") -> None:
        self.agent_id = agent_id


@pytest.fixture(autouse=True)
def _restore_agent_graph_state() -> None:
    original_graph = deepcopy(mod._agent_graph)
    original_messages = deepcopy(mod._agent_messages)
    original_running = deepcopy(mod._running_agents)
    original_instances = deepcopy(mod._agent_instances)
    original_states = deepcopy(mod._agent_states)

    mod._agent_graph["nodes"] = {}
    mod._agent_graph["edges"] = []
    mod._agent_messages.clear()
    mod._running_agents.clear()
    mod._agent_instances.clear()
    mod._agent_states.clear()

    try:
        yield
    finally:
        mod._agent_graph["nodes"] = original_graph.get("nodes", {})
        mod._agent_graph["edges"] = original_graph.get("edges", [])
        mod._agent_messages.clear()
        mod._agent_messages.update(original_messages)
        mod._running_agents.clear()
        mod._running_agents.update(original_running)
        mod._agent_instances.clear()
        mod._agent_instances.update(original_instances)
        mod._agent_states.clear()
        mod._agent_states.update(original_states)


def _node(
    agent_id: str,
    status: str,
    parent_id: str | None = None,
    task: str = "",
) -> dict[str, str | None]:
    return {
        "id": agent_id,
        "name": agent_id,
        "status": status,
        "parent_id": parent_id,
        "task": task,
    }


def test_create_agent_blocks_total_agent_limit() -> None:
    for i in range(mod.MAX_TOTAL_AGENTS):
        agent_id = f"a{i}"
        mod._agent_graph["nodes"][agent_id] = _node(
            agent_id=agent_id,
            status="completed",
            parent_id="root",
            task=f"task-{i}",
        )

    result = mod.create_agent(
        agent_state=_DummyAgentState(),
        task="new task",
        name="child",
    )

    assert result["success"] is False
    assert "Total agent limit reached" in str(result.get("error"))


def test_create_agent_blocks_duplicate_active_subagent_task() -> None:
    parent_id = "parent-agent"
    mod._agent_graph["nodes"]["existing-child"] = _node(
        agent_id="existing-child",
        status="running",
        parent_id=parent_id,
        task="Check authentication bypass endpoints",
    )

    result = mod.create_agent(
        agent_state=_DummyAgentState(agent_id=parent_id),
        task="  check AUTHENTICATION bypass endpoints  ",
        name="duplicate-child",
    )

    assert result["success"] is False
    assert "Duplicate active subagent task detected" in str(result.get("error"))


def test_create_agent_blocks_parent_child_burst() -> None:
    parent_id = "parent-agent"
    for i in range(mod.MAX_ACTIVE_CHILDREN_PER_PARENT):
        child_id = f"child-{i}"
        mod._agent_graph["nodes"][child_id] = _node(
            agent_id=child_id,
            status="running",
            parent_id=parent_id,
            task=f"distinct-task-{i}",
        )

    result = mod.create_agent(
        agent_state=_DummyAgentState(agent_id=parent_id),
        task="another distinct task",
        name="overflow-child",
    )

    assert result["success"] is False
    assert "too many active subagents" in str(result.get("error"))
