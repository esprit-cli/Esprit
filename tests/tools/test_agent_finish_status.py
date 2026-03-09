from __future__ import annotations

from typing import Any

from esprit.agents.state import AgentState
from esprit.tools.agents_graph import agents_graph_actions as mod


def _reset_graph() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    old_nodes = mod._agent_graph["nodes"].copy()
    old_edges = mod._agent_graph["edges"][:]
    old_instances = mod._agent_instances.copy()
    old_states = mod._agent_states.copy()
    mod._agent_graph["nodes"] = {}
    mod._agent_graph["edges"] = []
    mod._agent_instances.clear()
    mod._agent_states.clear()
    return old_nodes, old_edges, old_instances, old_states


def _restore_graph(
    old_nodes: dict[str, Any],
    old_edges: list[dict[str, Any]],
    old_instances: dict[str, Any],
    old_states: dict[str, Any],
) -> None:
    mod._agent_graph["nodes"] = old_nodes
    mod._agent_graph["edges"] = old_edges
    mod._agent_instances.clear()
    mod._agent_instances.update(old_instances)
    mod._agent_states.clear()
    mod._agent_states.update(old_states)


def test_agent_finish_marks_failed_state_and_result() -> None:
    old_nodes, old_edges, old_instances, old_states = _reset_graph()

    try:
        state = AgentState(
            agent_id="agent_child",
            agent_name="Fixing Agent",
            parent_id="agent_parent",
            task="fix auth bug",
        )
        mod._agent_graph["nodes"]["agent_parent"] = {
            "id": "agent_parent",
            "name": "Root Agent",
            "task": "coordinate remediation",
            "status": "running",
        }
        mod._agent_graph["nodes"]["agent_child"] = {
            "id": "agent_child",
            "name": "Fixing Agent",
            "task": "fix auth bug",
            "status": "running",
            "parent_id": "agent_parent",
            "skills": ["remediation"],
        }

        result = mod.agent_finish(
            agent_state=state,
            result_summary="fix could not be verified",
            findings=["code still vulnerable"],
            success=False,
        )
        agent_node = mod._agent_graph["nodes"]["agent_child"]
    finally:
        _restore_graph(old_nodes, old_edges, old_instances, old_states)

    assert result["agent_completed"] is True
    assert result["success"] is False
    assert agent_node["status"] == "failed"
    assert state.completed is True
    assert state.final_result is not None
    assert state.final_result["success"] is False
    assert state.final_result["completion_type"] == "agent_finish"


def test_run_agent_in_thread_preserves_failed_agent_finish_result() -> None:
    old_nodes, old_edges, old_instances, old_states = _reset_graph()

    class DummyAgent:
        async def agent_loop(self, task: str) -> dict[str, Any]:
            mod.agent_finish(
                agent_state=state,
                result_summary="fix validation failed",
                findings=["vulnerability still present"],
                success=False,
            )
            return {"success": True, "unexpected": "wrapper should not overwrite this"}

    try:
        state = AgentState(
            agent_id="agent_child",
            agent_name="Fix Validation Agent",
            parent_id="agent_parent",
            task="verify fix",
        )
        mod._agent_graph["nodes"]["agent_parent"] = {
            "id": "agent_parent",
            "name": "Root Agent",
            "task": "coordinate remediation",
            "status": "running",
        }
        mod._agent_graph["nodes"]["agent_child"] = {
            "id": "agent_child",
            "name": "Fix Validation Agent",
            "task": "verify fix",
            "status": "running",
            "parent_id": "agent_parent",
            "skills": ["verification"],
        }

        mod._run_agent_in_thread(DummyAgent(), state, [])
        agent_node = mod._agent_graph["nodes"]["agent_child"]
    finally:
        _restore_graph(old_nodes, old_edges, old_instances, old_states)

    assert agent_node["status"] == "failed"
    assert agent_node["result"]["success"] is False
    assert agent_node["result"]["completion_type"] == "agent_finish"


def test_agent_finish_rejects_unsafe_remediation_claim() -> None:
    old_nodes, old_edges, old_instances, old_states = _reset_graph()

    try:
        state = AgentState(
            agent_id="agent_child",
            agent_name="Authentication Fixing Agent",
            parent_id="agent_parent",
            task="fix auth bypass",
        )
        mod._agent_graph["nodes"]["agent_parent"] = {
            "id": "agent_parent",
            "name": "Root Agent",
            "task": "coordinate remediation",
            "status": "running",
        }
        mod._agent_graph["nodes"]["agent_child"] = {
            "id": "agent_child",
            "name": "Authentication Fixing Agent",
            "task": "fix auth bypass",
            "status": "running",
            "parent_id": "agent_parent",
            "skills": ["remediation", "authentication"],
        }

        result = mod.agent_finish(
            agent_state=state,
            result_summary=(
                "Applied fix by generating a temporary password with "
                "crypto.randomBytes(16) and logging the generated password."
            ),
            findings=["Generated temporary password for operator recovery."],
            success=True,
        )
        agent_node = mod._agent_graph["nodes"]["agent_child"]
    finally:
        _restore_graph(old_nodes, old_edges, old_instances, old_states)

    assert result["agent_completed"] is True
    assert result["success"] is False
    assert result["completion_summary"]["unsafe_remediation_blocked"] is True
    assert agent_node["status"] == "failed"
    assert agent_node["result"]["success"] is False
    assert agent_node["result"]["unsafe_remediation_blocked"] is True
    assert "Unsafe remediation rejected" in agent_node["result"]["summary"]


def test_agent_finish_does_not_block_non_remediation_agent() -> None:
    old_nodes, old_edges, old_instances, old_states = _reset_graph()

    try:
        state = AgentState(
            agent_id="agent_child",
            agent_name="Verification Agent",
            parent_id="agent_parent",
            task="verify auth fix",
        )
        mod._agent_graph["nodes"]["agent_parent"] = {
            "id": "agent_parent",
            "name": "Root Agent",
            "task": "coordinate remediation",
            "status": "running",
        }
        mod._agent_graph["nodes"]["agent_child"] = {
            "id": "agent_child",
            "name": "Verification Agent",
            "task": "verify auth fix",
            "status": "running",
            "parent_id": "agent_parent",
            "skills": ["verification"],
        }

        result = mod.agent_finish(
            agent_state=state,
            result_summary="Verified code does not generate temporary passwords.",
            findings=["Confirmed no generated password path remains."],
            success=True,
        )
        agent_node = mod._agent_graph["nodes"]["agent_child"]
    finally:
        _restore_graph(old_nodes, old_edges, old_instances, old_states)

    assert result["agent_completed"] is True
    assert result["success"] is True
    assert result["completion_summary"]["unsafe_remediation_blocked"] is False
    assert agent_node["status"] == "completed"


def test_agent_finish_allows_safe_remediation_summary() -> None:
    old_nodes, old_edges, old_instances, old_states = _reset_graph()

    try:
        state = AgentState(
            agent_id="agent_child",
            agent_name="Authentication Fixing Agent",
            parent_id="agent_parent",
            task="fix auth bypass",
        )
        mod._agent_graph["nodes"]["agent_parent"] = {
            "id": "agent_parent",
            "name": "Root Agent",
            "task": "coordinate remediation",
            "status": "running",
        }
        mod._agent_graph["nodes"]["agent_child"] = {
            "id": "agent_child",
            "name": "Authentication Fixing Agent",
            "task": "fix auth bypass",
            "status": "running",
            "parent_id": "agent_parent",
            "skills": ["remediation", "authentication"],
        }

        result = mod.agent_finish(
            agent_state=state,
            result_summary=(
                "Applied a fail-secure fix. The code now denies access when the password "
                "is missing and no longer generates or logs credentials."
            ),
            findings=["Removed generated password logic and password logging path."],
            success=True,
        )
        agent_node = mod._agent_graph["nodes"]["agent_child"]
    finally:
        _restore_graph(old_nodes, old_edges, old_instances, old_states)

    assert result["agent_completed"] is True
    assert result["success"] is True
    assert result["completion_summary"]["unsafe_remediation_blocked"] is False
    assert agent_node["status"] == "completed"
