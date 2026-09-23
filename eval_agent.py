import argparse
import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver

from agent import SYSTEM_PROMPT, _server_command, build_model, calculate_percentage_change


EVAL_CASES = [
    {
        "id": "population-source",
        "question": "Find a current SSB population statistic. State the table ID and period.",
        "rubric": (
            "Pass only if the answer gives a real or plausibly real SSB population-related statistic, "
            "includes a table ID or source reference, clearly states the period, and is grounded in official "
            "Statistics Norway data rather than generic discussion."
        ),
    },
    {
        "id": "parliament-source",
        "question": "Which political parties are represented in the Storting? Identify the source.",
        "rubric": (
            "Pass only if the answer identifies that the information comes from the Stortinget / Norwegian parliament "
            "data and names the relevant political parties or a valid representative source. It should not invent a party list."
        ),
    },
    {
        "id": "deterministic-calculation",
        "question": "What is the percentage change from 100 to 125? Use the calculation tool.",
        "rubric": (
            "Pass only if the answer correctly computes the percentage change as 25% and clearly indicates it was calculated "
            "using the percentage change tool or equivalent arithmetic."
        ),
    },
]


def _last_text(response: dict[str, Any]) -> str:
    for message in reversed(response.get("messages", [])):
        if getattr(message, "type", None) == "ai" and getattr(message, "content", None):
            return str(message.content)
    return ""


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def _judge_answer(model: Any, case: dict[str, str], answer: str) -> tuple[bool, str]:
    prompt = (
        "You are an evaluator for an LLM agent. Judge whether the answer satisfies the task.\n\n"
        f"Question: {case['question']}\n\n"
        f"Rubric: {case['rubric']}\n\n"
        f"Answer to evaluate:\n{answer}\n\n"
        "Return ONLY valid JSON in the form {\"passed\": true|false, \"reason\": \"short explanation\"}.\n"
        "Be strict but fair. Mark passed only if the answer is reasonably correct and grounded."
    )
    result = model.invoke(prompt)
    content = getattr(result, "content", str(result))
    payload = _extract_json_object(str(content))
    return bool(payload.get("passed", False)), str(payload.get("reason", "No reason provided."))


async def _build_agent() -> Any:
    client = MultiServerMCPClient(_server_command())
    tools = [calculate_percentage_change, *(await client.get_tools())]
    return create_agent(
        build_model(),
        tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
    )


async def evaluate(runs: int, output_path: Path) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    judge_model = build_model()
    records = []
    for case in EVAL_CASES:
        for run_number in range(1, runs + 1):
            agent = await _build_agent()
            started = time.perf_counter()
            response = await agent.ainvoke(
                {"messages": [{"role": "user", "content": case["question"]}]},
                config={"configurable": {"thread_id": f"eval-{case['id']}-{run_number}"}},
            )
            answer = _last_text(response)
            tool_names = [
                getattr(message, "name", "unknown")
                for message in response.get("messages", [])
                if getattr(message, "type", None) == "tool"
            ]
            judge_pass, judge_reason = _judge_answer(judge_model, case, answer)
            record = {
                "timestamp": datetime.now(UTC).isoformat(),
                "case_id": case["id"],
                "run": run_number,
                "tools": tool_names,
                "judge_pass": judge_pass,
                "judge_reason": judge_reason,
                "pass": judge_pass,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "answer": answer,
            }
            records.append(record)
            print(json.dumps(record, ensure_ascii=True))

    summary = {}
    for case in EVAL_CASES:
        results = [record["pass"] for record in records if record["case_id"] == case["id"]]
        summary[case["id"]] = {
            "runs": len(results),
            "pass_rate": sum(results) / len(results),
            "pass_at_k": any(results),
            "pass_power_k": all(results),
        }
    final = {"summary": summary, "overall_pass_rate": sum(record["pass"] for record in records) / len(records)}
    output_path.write_text(json.dumps({"records": records, **final}, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(final, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate the SSB/Stortinget agent.")
    parser.add_argument("--runs", type=int, default=3, help="Runs per case.")
    parser.add_argument("--output", type=Path, default=Path("eval-results.json"))
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    asyncio.run(evaluate(args.runs, args.output))