from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ValidationError


SANDBOX_MODE = os.getenv("ESPRIT_SANDBOX_MODE", "false").lower() == "true"
if not SANDBOX_MODE:
    raise RuntimeError("Tool server should only run in sandbox mode (ESPRIT_SANDBOX_MODE=true)")

parser = argparse.ArgumentParser(description="Start Esprit tool server")
parser.add_argument("--token", required=True, help="Authentication token")
parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")  # nosec
parser.add_argument("--port", type=int, required=True, help="Port to bind to")
parser.add_argument(
    "--timeout",
    type=int,
    default=120,
    help="Hard timeout in seconds for each request execution (default: 120)",
)

args = parser.parse_args()
EXPECTED_TOKEN = args.token
REQUEST_TIMEOUT = args.timeout
MAX_WORKERS = max(4, int(os.getenv("ESPRIT_TOOL_SERVER_MAX_WORKERS", "48")))
EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="esprit-tool")

app = FastAPI()
security = HTTPBearer()
security_dependency = Depends(security)

agent_tasks: dict[str, asyncio.Task[Any]] = {}


def verify_token(credentials: HTTPAuthorizationCredentials) -> str:
    if not credentials or credentials.scheme != "Bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication scheme. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if credentials.credentials != EXPECTED_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return credentials.credentials


class ToolExecutionRequest(BaseModel):
    agent_id: str
    tool_name: str
    kwargs: dict[str, Any]


class ToolExecutionResponse(BaseModel):
    result: Any | None = None
    error: str | None = None


async def _run_tool(agent_id: str, tool_name: str, kwargs: dict[str, Any]) -> Any:
    from esprit.tools.argument_parser import convert_arguments
    from esprit.tools.context import set_current_agent_id
    from esprit.tools.registry import get_tool_by_name

    set_current_agent_id(agent_id)

    tool_func = get_tool_by_name(tool_name)
    if not tool_func:
        raise ValueError(f"Tool '{tool_name}' not found")

    converted_kwargs = convert_arguments(tool_func, kwargs)
    result = await asyncio.to_thread(tool_func, **converted_kwargs)
    return await result if inspect.isawaitable(result) else result


@app.on_event("startup")
async def _configure_executor() -> None:
    asyncio.get_running_loop().set_default_executor(EXECUTOR)


@app.on_event("shutdown")
async def _shutdown_executor() -> None:
    EXECUTOR.shutdown(wait=False, cancel_futures=True)


@app.post("/execute", response_model=ToolExecutionResponse)
async def execute_tool(
    request: ToolExecutionRequest, credentials: HTTPAuthorizationCredentials = security_dependency
) -> ToolExecutionResponse:
    verify_token(credentials)

    agent_id = request.agent_id
    active_task = agent_tasks.get(agent_id)
    if active_task is not None and not active_task.done():
        # Reject overlapping calls for the same agent instead of cancelling in-flight work.
        return ToolExecutionResponse(error="Agent has an active tool request; retry shortly")

    started_at = time.monotonic()
    task = asyncio.create_task(
        asyncio.wait_for(
            _run_tool(agent_id, request.tool_name, request.kwargs), timeout=REQUEST_TIMEOUT
        )
    )
    agent_tasks[agent_id] = task

    try:
        result = await task
        return ToolExecutionResponse(result=result)

    except asyncio.CancelledError:
        return ToolExecutionResponse(error="Tool execution cancelled")

    except TimeoutError:
        return ToolExecutionResponse(error=f"Tool timed out after {REQUEST_TIMEOUT}s")

    except ValidationError as e:
        return ToolExecutionResponse(error=f"Invalid arguments: {e}")

    except (ValueError, RuntimeError, ImportError) as e:
        return ToolExecutionResponse(error=f"Tool execution error: {e}")

    except Exception as e:  # noqa: BLE001
        return ToolExecutionResponse(error=f"Unexpected error: {e}")

    finally:
        if agent_tasks.get(agent_id) is task:
            del agent_tasks[agent_id]
        duration_s = time.monotonic() - started_at
        sys.stderr.write(
            f"[tool_server] agent={agent_id} tool={request.tool_name} duration={duration_s:.2f}s\n"
        )
        sys.stderr.flush()


@app.post("/register_agent")
async def register_agent(
    agent_id: str, credentials: HTTPAuthorizationCredentials = security_dependency
) -> dict[str, str]:
    verify_token(credentials)
    return {"status": "registered", "agent_id": agent_id}


@app.get("/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "healthy",
        "sandbox_mode": str(SANDBOX_MODE),
        "environment": "sandbox" if SANDBOX_MODE else "main",
        "auth_configured": "true" if EXPECTED_TOKEN else "false",
        "active_agents": len(agent_tasks),
        "agents": list(agent_tasks.keys()),
    }


def signal_handler(_signum: int, _frame: Any) -> None:
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    for task in agent_tasks.values():
        task.cancel()
    sys.exit(0)


if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

if __name__ == "__main__":
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
