import logging
from typing import Any

from esprit.tools.registry import register_tool

logger = logging.getLogger(__name__)

# Track how many times finish_scan has been bounced for remediation.
# After this many bounces, allow finish_scan through with a warning.
_MAX_REMEDIATION_BOUNCES = 2
_remediation_bounce_count: int = 0


def _validate_root_agent(agent_state: Any) -> dict[str, Any] | None:
    if agent_state and hasattr(agent_state, "parent_id") and agent_state.parent_id is not None:
        return {
            "success": False,
            "error": "finish_scan_wrong_agent",
            "message": "This tool can only be used by the root/main agent",
            "suggestion": "If you are a subagent, use agent_finish from agents_graph tool instead",
        }
    return None


def _check_active_agents(agent_state: Any = None) -> dict[str, Any] | None:
    try:
        from esprit.tools.agents_graph.agents_graph_actions import _agent_graph

        if agent_state and agent_state.agent_id:
            current_agent_id = agent_state.agent_id
        else:
            return None

        active_agents = []
        stopping_agents = []

        for agent_id, node in _agent_graph["nodes"].items():
            if agent_id == current_agent_id:
                continue

            status = node.get("status", "unknown")
            if status == "running":
                active_agents.append(
                    {
                        "id": agent_id,
                        "name": node.get("name", "Unknown"),
                        "task": node.get("task", "Unknown task")[:300],
                        "status": status,
                    }
                )
            elif status == "stopping":
                stopping_agents.append(
                    {
                        "id": agent_id,
                        "name": node.get("name", "Unknown"),
                        "task": node.get("task", "Unknown task")[:300],
                        "status": status,
                    }
                )

        if active_agents or stopping_agents:
            response: dict[str, Any] = {
                "success": False,
                "error": "agents_still_active",
                "message": "Cannot finish scan: agents are still active",
            }

            if active_agents:
                response["active_agents"] = active_agents

            if stopping_agents:
                response["stopping_agents"] = stopping_agents

            response["suggestions"] = [
                "Use wait_for_message to wait for all agents to complete",
                "Use send_message_to_agent if you need agents to complete immediately",
                "Check agent_status to see current agent states",
            ]

            response["total_active"] = len(active_agents) + len(stopping_agents)

            return response

    except ImportError:
        pass
    except Exception:
        logger.exception("Error checking active agents")

    return None


def _check_remediation_completeness(agent_state: Any) -> dict[str, Any] | None:
    """In white-box mode, check that reported vulnerabilities have fixing agents."""
    global _remediation_bounce_count  # noqa: PLW0603

    if not agent_state or not getattr(agent_state, "is_whitebox", False):
        return None

    try:
        from esprit.telemetry.tracer import get_global_tracer
        from esprit.tools.agents_graph.agents_graph_actions import _agent_graph

        tracer = get_global_tracer()
        if not tracer:
            return None

        vuln_count = len(tracer.vulnerability_reports)
        if vuln_count == 0:
            return None

        # Count completed fixing agents in the agent graph
        fixing_agents = []
        for node in _agent_graph["nodes"].values():
            name = (node.get("name") or "").lower()
            status = node.get("status", "")
            if "fix" in name and status == "finished":
                fixing_agents.append(node)

        if fixing_agents:
            # At least some fixing agents ran — allow finish
            return None

        # No fixing agents found — bounce unless we've hit the limit
        _remediation_bounce_count += 1
        if _remediation_bounce_count > _MAX_REMEDIATION_BOUNCES:
            logger.info(
                "Remediation bounce limit reached (%d), allowing finish_scan",
                _remediation_bounce_count,
            )
            return None

        return {
            "success": False,
            "error": "remediation_incomplete",
            "message": (
                f"White-box scan has {vuln_count} reported vulnerabilities but no "
                f"Fixing Agents have been spawned. In white-box mode, you MUST spawn "
                f"Fixing Agents (with skills: [remediation, <vuln_type>]) to patch the "
                f"vulnerable code before finishing the scan."
            ),
            "suggestions": [
                'Spawn a Fixing Agent: create_agent(name="<Vuln> Fixing Agent", '
                'task="Fix <vuln> in <file>", skills="remediation,<vuln_type>")',
                "Each Fixing Agent should use str_replace_editor to patch the code",
                "After all Fixing Agents complete, call finish_scan again",
            ],
            "vulnerabilities_without_fixes": vuln_count,
        }

    except (ImportError, AttributeError):
        logger.debug("Could not check remediation completeness", exc_info=True)
        return None


@register_tool(sandbox_execution=False)
def finish_scan(
    executive_summary: str,
    methodology: str,
    technical_analysis: str,
    recommendations: str,
    agent_state: Any = None,
) -> dict[str, Any]:
    validation_error = _validate_root_agent(agent_state)
    if validation_error:
        return validation_error

    active_agents_error = _check_active_agents(agent_state)
    if active_agents_error:
        return active_agents_error

    remediation_error = _check_remediation_completeness(agent_state)
    if remediation_error:
        return remediation_error

    validation_errors = []

    if not executive_summary or not executive_summary.strip():
        validation_errors.append("Executive summary cannot be empty")
    if not methodology or not methodology.strip():
        validation_errors.append("Methodology cannot be empty")
    if not technical_analysis or not technical_analysis.strip():
        validation_errors.append("Technical analysis cannot be empty")
    if not recommendations or not recommendations.strip():
        validation_errors.append("Recommendations cannot be empty")

    if validation_errors:
        return {"success": False, "message": "Validation failed", "errors": validation_errors}

    try:
        from esprit.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        if tracer:
            tracer.update_scan_final_fields(
                executive_summary=executive_summary.strip(),
                methodology=methodology.strip(),
                technical_analysis=technical_analysis.strip(),
                recommendations=recommendations.strip(),
            )

            vulnerability_count = len(tracer.vulnerability_reports)

            return {
                "success": True,
                "scan_completed": True,
                "message": "Scan completed successfully",
                "vulnerabilities_found": vulnerability_count,
            }

        logger.warning("Current tracer not available - scan results not stored")

    except (ImportError, AttributeError) as e:
        return {"success": False, "message": f"Failed to complete scan: {e!s}"}
    else:
        return {
            "success": True,
            "scan_completed": True,
            "message": "Scan completed (not persisted)",
            "warning": "Results could not be persisted - tracer unavailable",
        }
