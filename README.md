# NorwayDataAgent

A production-oriented local agent for querying Norwegian public data through safe, constrained MCP tools. The project combines a LangChain agent with secure tool servers for Statistics Norway (SSB) and Stortinget, plus a deterministic calculator for percentage-change operations.

## Why this project exists

This workspace demonstrates a practical pattern for building agentic applications that need access to external data without over-permitting the model. The design emphasizes:

- constrained tool access
- strict request validation
- small, deterministic tools for arithmetic and data transformations
- structured logging and auditable tool calls
- local evaluation with an LLM-as-judge
- separation between orchestration code and data-source adapters

## Architecture

The project is organized around a local agent that connects to multiple MCP servers over stdio:

- SSB MCP server: metadata lookup, table discovery, and safe sample data queries
- Stortinget MCP server: restricted parliamentary data access with an endpoint allowlist
- LangChain agent: orchestrates reasoning, tool selection, and memory
- evaluator: runs repeatable scenarios and scores agent responses

## Features

- Azure OpenAI-backed chat agent
- In-process memory for conversational continuity
- Strict Pydantic validation for tool parameters
- Hardcoded endpoint allowlists for external APIs
- Timeout handling and response-size limits
- Deterministic calculator for percentage change
- Regression tests for validation and security boundaries
- LLM-as-judge evaluation harness

## Prerequisites

- Python 3.12+
- uv
- Access to an Azure OpenAI deployment

## Setup

From the project root:

```powershell
uv sync
```

Create a .env file in the project root with values similar to:

```text
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_API_VERSION=2024-02-01
```

## Run the agent

```powershell
uv run python agent.py
```

This starts the MCP servers and opens an interactive chat loop. The model can call the SSB tools, Stortinget tools, and the percentage-change helper when needed.

## Project structure

```text
.
├── agent.py
├── eval_agent.py
├── pyproject.toml
├── README.md
├── src/
│   └── mcp_workshop/
│       ├── __init__.py
│       ├── ssb_mcp.py
│       └── stortinget_mcp.py
├── tests/
│   ├── test_ssb_validation.py
│   └── test_stortinget_security.py
├── uv.lock
└── .env
```

## Validation and tests

Run the unit tests:

```powershell
uv run python -m unittest discover -s tests -v
```

Run Python compile checks:

```powershell
uv run python -m compileall -q agent.py eval_agent.py src tests
```

## Evaluation

The project includes an evaluator for repeatable quality checks:

```powershell
uv run python eval_agent.py --runs 3 --output eval-results.json
```

This runs scenario-based evaluations and records pass/fail results, tool usage, and summary metrics such as pass rate and pass@k. The generated output may contain model responses and should be treated as test data rather than public documentation.

## Security notes

This project is intentionally built to be conservative about what tools the agent can access.

- the SSB MCP tools validate table identifiers and request shape
- the Stortinget MCP server restricts endpoints to a known-safe allowlist
- network calls use timeouts and response caps
- untrusted data is not used as a direct source of executable logic
- the tool layer is narrow and auditable

These patterns are useful when moving from a quick prototype to a more production-minded agent architecture.

## Typical usage examples

You can ask the agent questions such as:

- What is the trend in employment in Norway over the last decade?
- Which Stortinget representatives belong to a specific party?
- Show summary statistics from a particular SSB table and compute the percentage change between years.

## Notes

This project is meant for local experimentation and demonstration. If you want to move it toward production, the next areas to harden are:

- secret management and environment isolation
- deployment packaging and CI/CD
- logging/telemetry pipelines
- stricter human approval boundaries for sensitive tools
- more comprehensive evaluation datasets

## License

This project is provided for learning and demonstration purposes in the current workspace. See your organization or repository policy for any production licensing requirements.
