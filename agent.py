import asyncio
import hashlib
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver


# if hasattr(sys.stdout, "reconfigure"):
#     sys.stdout.reconfigure(encoding="utf-8", errors="replace")
# if hasattr(sys.stderr, "reconfigure"):
#     sys.stderr.reconfigure(encoding="utf-8", errors="replace")


load_dotenv(override=True)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ssb-agent")
SESSION_THREAD_ID = "terminal-session"


def _log_agent_event(event: dict[str, Any]) -> None:
    logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), **event}, ensure_ascii=True))


@tool
def calculate_percentage_change(old_value: float, new_value: float) -> dict[str, float | str]:
    """Calculate percentage change from old_value to new_value exactly."""
    if old_value == 0:
        return {
            "error": "Cannot calculate percentage change from zero.",
            "retry": "Provide a non-zero old_value or ask for the absolute difference instead.",
        }
    difference = new_value - old_value
    percentage_change = difference / old_value * 100
    return {
        "old_value": old_value,
        "new_value": new_value,
        "difference": difference,
        "percentage_change": round(percentage_change, 4),
    }


def build_model() -> AzureChatOpenAI:
    api_version = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("OPENAI_API_VERSION")
    deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or os.getenv("AZURE_OPENAI_DEPLOYMENT")
    required = {
        "AZURE_OPENAI_API_KEY": os.getenv("AZURE_OPENAI_API_KEY"),
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "AZURE_OPENAI_API_VERSION": api_version,
        "AZURE_OPENAI_DEPLOYMENT_NAME": deployment,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    return AzureChatOpenAI(
        azure_endpoint=required["AZURE_OPENAI_ENDPOINT"],
        api_key=required["AZURE_OPENAI_API_KEY"],
        api_version=required["AZURE_OPENAI_API_VERSION"],
        azure_deployment=required["AZURE_OPENAI_DEPLOYMENT_NAME"],
        temperature=0,
    )


def _server_command() -> dict[str, Any]:
    server_environment = os.environ.copy()
    server_environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    server_template = {
        "transport": "stdio",
        "command": sys.executable,
        "env": server_environment,
        "encoding": "utf-8",
        "encoding_error_handler": "replace",
    }
    return {
        "ssb": {
            **server_template,
            "args": [str(Path(__file__).parent / "src" / "ssb_mcp.py")],
        },
        "stortinget": {
            **server_template,
            "args": [str(Path(__file__).parent / "src" / "stortinget_mcp.py")],
        },
    }


def _last_text(response: dict[str, Any]) -> str:
    messages = response.get("messages", [])
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if content and getattr(message, "type", None) == "ai":
            return str(content)
    return "The agent did not return a text response."


def _log_tool_calls(response: dict[str, Any]) -> None:
    for message in response.get("messages", []):
        if getattr(message, "type", None) == "tool":
            tool_name = getattr(message, "name", "unknown")
            logger.info("Tool called: %s", tool_name)


SYSTEM_PROMPT = """
You answer questions using Statistics Norway (SSB) and Stortinget open data through MCP tools.
For population or unemployment questions, first find relevant tables, then inspect
metadata, then retrieve a small sample. Never invent table IDs, values, or periods.
For questions combining a municipality with parliamentary representation, use at least
one tool from each server. Clearly label facts from SSB versus Stortinget. Explain the
municipality-to-county/electoral-district link and mark it uncertain unless a tool proves
the relationship. Do not present a district hint as a verified municipal mapping.
Keep answers concise and state what was retrieved, the period covered, and table/source
IDs or titles whenever tool results provide them. If a tool returns an error, follow its
retry instruction and do not hide the limitation.
Remember earlier turns in this terminal session. For arithmetic, use
calculate_percentage_change rather than estimating or pretending to calculate mentally.
""".strip()


async def answer_question(agent: Any, question: str) -> str:
    started = time.perf_counter()
    response = await agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]},
        config={"configurable": {"thread_id": SESSION_THREAD_ID}},
    )
    _log_tool_calls(response)
    tool_messages = [message for message in response.get("messages", []) if getattr(message, "type", None) == "tool"]
    tool_names = [getattr(message, "name", "unknown") for message in tool_messages]
    tool_result_sizes = [len(str(getattr(message, "content", ""))) for message in tool_messages]
    has_tool_error = any('"error"' in str(getattr(message, "content", "")) for message in tool_messages)
    answer = _last_text(response)
    _log_agent_event({
        "event": "agent_call",
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest()[:12],
        "tool_names": tool_names,
        "tool_result_sizes": tool_result_sizes,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "result_size": len(answer),
        "error": has_tool_error,
    })
    return answer


async def main() -> None:
    model = build_model()
    client = MultiServerMCPClient(_server_command())
    tools = [calculate_percentage_change, *(await client.get_tools())]
    logger.info("Loaded MCP tools: %s", ", ".join(tool.name for tool in tools))
    agent = create_agent(
        model,
        tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )

    print("SSB + Stortinget agent ready. Ask a data question; type 'exit' to quit.")
    while True:
        question = await asyncio.to_thread(input, "\nQuestion: ")
        if question.strip().lower() in {"exit", "quit"}:
            break
        if not question.strip():
            continue
        try:
            print(f"\n{await answer_question(agent, question)}")
        except Exception as exc:
            logger.error("Agent request failed: %s", exc)


if __name__ == "__main__":
    asyncio.run(main())