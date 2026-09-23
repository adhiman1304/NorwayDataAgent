import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, model_validator


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SSB_API_BASE = "https://data.ssb.no/api/pxwebapi/v2"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_TABLES = 10
MAX_VALUES_PER_VARIABLE = 5
MAX_RESPONSE_CHARACTERS = 12_000
MIN_YEAR = 1990
MAX_YEAR = 2100

logger = logging.getLogger("ssb-mcp")
logging.basicConfig(level=logging.INFO, format="%(message)s")

mcp = FastMCP("SSB Statistics")


class FindStatisticsRequest(BaseModel):
    topic: Literal["population", "unemployment"]
    language: Literal["no", "en"] = "en"
    max_results: int = Field(default=MAX_TABLES, ge=1, le=MAX_TABLES)


class MetadataRequest(BaseModel):
    table_id: str = Field(pattern=r"^\d{5}$", description="Five-digit SSB table ID")
    language: Literal["no", "en"] = "en"


class SelectionRequest(BaseModel):
    variable_code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    value_codes: list[str] = Field(min_length=1, max_length=MAX_VALUES_PER_VARIABLE)

    @field_validator("value_codes")
    @classmethod
    def validate_value_codes(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.strip() or len(value) > 100:
                raise ValueError("value codes must be non-empty and at most 100 characters")
        return values


class SampleRequest(BaseModel):
    table_id: str = Field(pattern=r"^\d{5}$", description="Five-digit SSB table ID")
    selections: list[SelectionRequest] = Field(min_length=1, max_length=8)
    language: Literal["no", "en"] = "en"
    year_from: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    year_to: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)

    @model_validator(mode="after")
    def validate_year_range(self) -> "SampleRequest":
        if self.year_from is not None and self.year_to is not None and self.year_to < self.year_from:
            raise ValueError("year_to must be greater than or equal to year_from")
        if any(selection.variable_code.casefold() == "tid" for selection in self.selections) and (
            self.year_from is not None or self.year_to is not None
        ):
            raise ValueError("use year_from/year_to or a Tid selection, not both")
        return self


def _error(message: str, retry: str) -> dict[str, str]:
    return {"error": message, "retry": retry}


def _log_tool_event(
    tool_name: str,
    parameters: dict[str, Any],
    started: float,
    result: Any,
    error: bool,
) -> None:
    logger.info(json.dumps({
        "timestamp": datetime.now(UTC).isoformat(),
        "tool_name": tool_name,
        "parameters": parameters,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "result_size": len(json.dumps(result, ensure_ascii=True)),
        "error": error,
    }, ensure_ascii=True))


def _api_error(response: httpx.Response) -> dict[str, str]:
    if response.status_code == 400:
        retry = "Check the table ID, variable codes, and value codes using get_ssb_table_metadata."
    elif response.status_code == 404:
        retry = "Verify the table ID with find_ssb_statistics."
    elif response.status_code == 429:
        retry = "Wait before trying again; SSB limits clients to 30 requests per minute."
    else:
        retry = "Try again later, or narrow the request."
    return _error(f"SSB API returned HTTP {response.status_code}.", retry)


async def _get_json(path: str, params: dict[str, Any]) -> tuple[Any | None, dict[str, str] | None]:
    request_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{SSB_API_BASE}/{path}", params=params)
    except httpx.RequestError:
        logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "GET", "path": path, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "error": True}, ensure_ascii=True))
        return None, _error("Could not reach the SSB API.", "Check network access and try again.")

    if not response.is_success:
        logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "GET", "path": path, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "error": True}, ensure_ascii=True))
        return None, _api_error(response)
    try:
        payload = response.json()
        logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "GET", "path": path, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "result_size": len(response.content), "error": False}, ensure_ascii=True))
        return payload, None
    except ValueError:
        return None, _error("SSB returned an invalid JSON response.", "Try again later.")


async def _post_json(
    path: str,
    params: dict[str, Any],
    body: dict[str, Any],
) -> tuple[httpx.Response | None, dict[str, str] | None]:
    request_started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(f"{SSB_API_BASE}/{path}", params=params, json=body)
    except httpx.RequestError:
        logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "POST", "path": path, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "error": True}, ensure_ascii=True))
        return None, _error("Could not reach the SSB API.", "Check network access and try again.")

    if not response.is_success:
        logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "POST", "path": path, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "error": True}, ensure_ascii=True))
        return None, _api_error(response)
    logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), "operation": "POST", "path": path, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - request_started) * 1000, 2), "result_size": len(response.content), "error": False}, ensure_ascii=True))
    return response, None


def _compact_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    dimensions = payload.get("dimension", {})
    compact_variables = []
    for code, dimension in list(dimensions.items())[:20] if isinstance(dimensions, dict) else []:
        if not isinstance(dimension, dict):
            continue
        category = dimension.get("category", {})
        index = category.get("index", {}) if isinstance(category, dict) else {}
        labels = category.get("label", {}) if isinstance(category, dict) else {}
        values = list(index) if isinstance(index, dict) else index
        labels = labels if isinstance(labels, dict) else {}
        compact_variables.append(
            {
                "code": code,
                "label": dimension.get("label"),
                "values": [
                    {"code": value, "label": labels.get(value)}
                    for value in values[:20]
                ],
                "more_values": max(len(values) - 20, 0),
            }
        )
    return {
        "title": payload.get("label") or payload.get("title"),
        "id": payload.get("id"),
        "updated": payload.get("updated"),
        "period": {
            "first": payload.get("dimension", {}).get("Tid", {}).get("category", {}).get("label", {})
        } if isinstance(payload.get("dimension"), dict) else None,
        "variables": compact_variables,
        "note": "Value lists are capped at 20 entries; use SSB directly for larger lists.",
    }


@mcp.tool(
    name="find_ssb_statistics",
    description=(
        "Find a small list of relevant Statistics Norway tables for population or "
        "unemployment. Use this before requesting metadata when you do not know a table ID."
    ),
)
async def find_ssb_statistics(
    request: FindStatisticsRequest,
) -> dict[str, Any]:
    started = time.perf_counter()
    terms = {
        "population": {"en": "population", "no": "folkemengde"},
        "unemployment": {"en": "unemployment", "no": "arbeidsledighet"},
    }
    payload, error = await _get_json(
        "tables",
        {"lang": request.language, "query": terms[request.topic][request.language], "pagesize": request.max_results, "pagenumber": 1},
    )
    if error:
        _log_tool_event("find_ssb_statistics", request.model_dump(), started, error, True)
        return error
    if isinstance(payload, list):
        tables = payload[:MAX_TABLES]
    elif isinstance(payload, dict) and isinstance(payload.get("tables"), list):
        tables = payload["tables"][:MAX_TABLES]
    else:
        result = _error("SSB returned an unexpected table-search response.", "Try the search again later.")
        _log_tool_event("find_ssb_statistics", request.model_dump(), started, result, True)
        return result
    result = {"topic": request.topic, "tables": tables, "count_returned": len(tables)}
    _log_tool_event("find_ssb_statistics", request.model_dump(), started, result, False)
    return result


@mcp.tool(
    name="get_ssb_table_metadata",
    description=(
        "Get compact metadata for one SSB table, including variable codes and sample "
        "value codes. Use these codes to make a precise population or unemployment query."
    ),
)
async def get_ssb_table_metadata(
    request: MetadataRequest,
) -> dict[str, Any]:
    started = time.perf_counter()
    payload, error = await _get_json(f"tables/{request.table_id}/metadata", {"lang": request.language})
    if error:
        _log_tool_event("get_ssb_table_metadata", request.model_dump(), started, error, True)
        return error
    if not isinstance(payload, dict):
        result = _error("SSB returned invalid table metadata.", "Verify the table ID and try again.")
        _log_tool_event("get_ssb_table_metadata", request.model_dump(), started, result, True)
        return result
    result = _compact_metadata(payload)
    _log_tool_event("get_ssb_table_metadata", request.model_dump(), started, result, False)
    return result


@mcp.tool(
    name="query_ssb_sample",
    description=(
        "Retrieve a small JSON-stat2 sample from an SSB population or unemployment table. "
        "Pass variable codes and no more than five value codes per variable; use '*', "
        "'top(3)', or 'from(2020)' to select a compact range. Call metadata first."
    ),
)
async def query_ssb_sample(
    request: SampleRequest,
) -> dict[str, Any]:
    started = time.perf_counter()
    selections = [selection.model_dump() for selection in request.selections]
    if request.year_from is not None:
        selections.append({"variable_code": "Tid", "value_codes": [f"from({request.year_from})"]})
        if request.year_to is not None:
            selections[-1]["value_codes"] = [f"range({request.year_from},{request.year_to})"]

    response, error = await _post_json(
        f"tables/{request.table_id}/data",
        {"lang": request.language, "outputFormat": "json-stat2"},
        {
            "selection": [
                {"variableCode": selection["variable_code"], "valueCodes": selection["value_codes"]}
                for selection in selections
            ]
        },
    )
    if error:
        _log_tool_event("query_ssb_sample", request.model_dump(), started, error, True)
        return error
    try:
        result = response.json()
    except ValueError:
        result = _error("SSB returned an invalid data response.", "Try a narrower selection.")
        _log_tool_event("query_ssb_sample", request.model_dump(), started, result, True)
        return result

    if len(json.dumps(result, ensure_ascii=True)) > MAX_RESPONSE_CHARACTERS:
        result = _error(
            "The selected sample is too large for an agent context.",
            "Select fewer values, fewer dimensions, or use 'top(3)' for time.",
        )
        _log_tool_event("query_ssb_sample", request.model_dump(), started, result, True)
        return result
    _log_tool_event("query_ssb_sample", request.model_dump(), started, result, False)
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")