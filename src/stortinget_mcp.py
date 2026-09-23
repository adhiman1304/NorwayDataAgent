import json
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field


STORTING_API_BASE = "https://data.stortinget.no/eksport"
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_RESULTS = 25
MAX_RESPONSE_CHARACTERS = 12_000
MAX_PARAMETER_LENGTH = 100

logger = logging.getLogger("stortinget-mcp")
logging.basicConfig(level=logging.INFO, format="%(message)s")

ENDPOINTS = {
    "representatives": "dagensrepresentanter",
    "parties": "allepartier",
    "votes": "voteringer",
}

mcp = FastMCP("Stortinget Open Data")


class RepresentativesRequest(BaseModel):
    municipality: str | None = Field(default=None, max_length=MAX_PARAMETER_LENGTH)
    district_hint: str | None = Field(default=None, max_length=MAX_PARAMETER_LENGTH)


class VotesRequest(BaseModel):
    case_id: str = Field(pattern=r"^\d{1,20}$", description="Numeric Stortinget case ID")


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _error(message: str, retry: str) -> dict[str, str]:
    return {"error": message, "retry": retry}


def _log_event(event: dict[str, Any]) -> None:
    logger.info(json.dumps({"timestamp": datetime.now(UTC).isoformat(), **event}, ensure_ascii=True))


async def _get_json(operation: str, params: dict[str, Any] | None = None) -> tuple[Any | None, dict[str, str] | None]:
    started = time.perf_counter()
    endpoint = ENDPOINTS.get(operation)
    if endpoint is None:
        _log_event({"operation": "GET", "endpoint": operation, "duration_ms": 0, "result_size": 0, "error": True})
        return None, _error(
            "That Stortinget resource is outside this server's approved scope.",
            "Use representatives, parties, or votes.",
        )

    request_params = {"format": "json", **(params or {})}
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{STORTING_API_BASE}/{endpoint}", params=request_params)
    except httpx.RequestError:
        _log_event({"operation": "GET", "endpoint": endpoint, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": 0, "error": True})
        return None, _error(
            "The Stortinget service could not be reached.",
            "Check network access and try again.",
        )
    if not response.is_success:
        _log_event({"operation": "GET", "endpoint": endpoint, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(response.content), "error": True})
        return None, _error(
            "The Stortinget request was not successful.",
            "Check the supplied identifier and try again.",
        )
    if len(response.content) > MAX_RESPONSE_CHARACTERS:
        _log_event({"operation": "GET", "endpoint": endpoint, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(response.content), "error": True})
        return None, _error(
            "The Stortinget response is too large for this tool.",
            "Request a narrower resource or try again later.",
        )
    try:
        payload = response.json()
        _log_event({"operation": "GET", "endpoint": endpoint, "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(response.content), "error": False})
        return payload, None
    except ValueError:
        return None, _error(
            "Stortinget returned an invalid JSON response.",
            "Try again later or use a narrower request.",
        )


def _bounded_json(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key, value in list(payload.items()):
            if isinstance(value, list):
                payload[key] = value[:MAX_RESULTS]
    serialized = json.dumps(payload, ensure_ascii=True)
    if len(serialized) > MAX_RESPONSE_CHARACTERS:
        return {"truncated": True, "data": payload}
    return payload


def _valid_text(value: str | None, field_name: str) -> tuple[str | None, dict[str, str] | None]:
    if value is None:
        return None, None
    normalized = value.strip()
    if not normalized:
        return None, _error(f"{field_name} cannot be empty.", f"Provide a valid {field_name}.")
    if len(normalized) > MAX_PARAMETER_LENGTH:
        return None, _error(f"{field_name} is too long.", f"Keep {field_name} under {MAX_PARAMETER_LENGTH} characters.")
    return normalized, None


@mcp.tool(
    name="get_storting_representatives",
    description=(
        "Return current Storting representatives, optionally filtered by the Storting's "
        "district/fylke name. For a municipality such as Trondheim, use district_hint "
        "only as an explicitly uncertain mapping; municipalities and electoral districts "
        "are not guaranteed to be identical."
    ),
)
async def get_storting_representatives(
    request: RepresentativesRequest,
) -> dict[str, Any]:
    started = time.perf_counter()
    municipality = request.municipality.strip() if request.municipality else None
    district_hint = request.district_hint.strip() if request.district_hint else None
    parameters = request.model_dump()
    municipality, error = _valid_text(municipality, "municipality")
    if error:
        _log_event({"tool_name": "get_storting_representatives", "parameters": parameters, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(error)), "error": True})
        return error
    district_hint, error = _valid_text(district_hint, "district_hint")
    if error:
        return error
    payload, error = await _get_json("representatives")
    if error:
        return error
    if not isinstance(payload, dict):
        result = _error("Unexpected representatives response.", "Try again later.")
        _log_event({"tool_name": "get_storting_representatives", "parameters": parameters, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result)), "error": True})
        return result

    representatives = payload.get("dagensrepresentanter_liste", [])
    if not isinstance(representatives, list):
        return _error("Representatives list was missing.", "Try again later.")

    mapping = {
        "trondheim": {
            "district": "Sør-Trøndelag",
            "status": "uncertain",
            "reason": (
                "Trondheim is an SSB municipality. Sør-Trøndelag is a historical "
                "Stortinget district/fylke label; this is not a verified current municipal boundary mapping."
            ),
        }
    }
    normalized_municipality = municipality.strip().lower() if municipality else None
    if normalized_municipality and not district_hint:
        suggestion = mapping.get(normalized_municipality)
        if not suggestion:
            return _error(
                f"No built-in municipality mapping exists for {municipality}.",
                "Provide district_hint explicitly, and label the mapping as uncertain.",
            )
        district_hint = suggestion["district"]
    filtered = representatives
    if district_hint:
        needle = district_hint.casefold()
        filtered = [
            representative
            for representative in representatives
            if needle in str(representative.get("fylke", {}).get("navn", "")).casefold()
        ]

    compact = [
        {
            "id": representative.get("id"),
            "name": " ".join(
                part for part in [representative.get("fornavn"), representative.get("etternavn")] if part
            ),
            "party": representative.get("parti", {}).get("navn"),
            "district": representative.get("fylke", {}).get("navn"),
            "is_substitute": representative.get("vara_representant"),
        }
        for representative in filtered[:MAX_RESULTS]
    ]
    result: dict[str, Any] = {
        "source": "Stortinget open data: dagensrepresentanter",
        "municipality": municipality,
        "district_hint": district_hint,
        "representatives": compact,
        "count_returned": len(compact),
    }
    if normalized_municipality and normalized_municipality in mapping:
        result["mapping"] = mapping[normalized_municipality]
    _log_event({"tool_name": "get_storting_representatives", "parameters": parameters, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result, ensure_ascii=True)), "error": False})
    return result


@mcp.tool(
    name="list_storting_parties",
    description="List political parties known to the Stortinget open data API, including currently represented parties.",
)
async def list_storting_parties() -> dict[str, Any]:
    started = time.perf_counter()
    payload, error = await _get_json("parties")
    if error:
        _log_event({"tool_name": "list_storting_parties", "parameters": {}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(error)), "error": True})
        return error
    if not isinstance(payload, dict):
        result = _error("Unexpected parties response.", "Try again later.")
        _log_event({"tool_name": "list_storting_parties", "parameters": {}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result)), "error": True})
        return result
    parties = payload.get("partier_liste", [])
    result = {
        "source": "Stortinget open data: allepartier",
        "parties": [
            {"id": party.get("id"), "name": party.get("navn"), "represented": party.get("representert_parti")}
            for party in parties[:MAX_RESULTS]
        ],
    }
    _log_event({"tool_name": "list_storting_parties", "parameters": {}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result, ensure_ascii=True)), "error": False})
    return result


@mcp.tool(
    name="get_storting_votes",
    description=(
        "Get voting records for a specific Stortinget case (sakid). A case ID is required "
        "because the API does not provide a safe generic 'latest votes' query. Keep the result small."
    ),
)
async def get_storting_votes(request: VotesRequest) -> dict[str, Any]:
    started = time.perf_counter()
    normalized_case_id = request.case_id
    payload, error = await _get_json("votes", {"sakid": normalized_case_id})
    if error:
        _log_event({"tool_name": "get_storting_votes", "parameters": {"case_id": normalized_case_id}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(error)), "error": True})
        return error
    if not isinstance(payload, dict):
        result = _error("Unexpected votes response.", "Check the case ID and try again.")
        _log_event({"tool_name": "get_storting_votes", "parameters": {"case_id": normalized_case_id}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result)), "error": True})
        return result
    result = {
        "source": "Stortinget open data: voteringer",
        "case_id": normalized_case_id,
        "votes": payload.get("voteringer_liste", [])[:MAX_RESULTS],
        "note": "Vote records are capped to keep the agent context small.",
    }
    _log_event({"tool_name": "get_storting_votes", "parameters": {"case_id": normalized_case_id}, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "result_size": len(json.dumps(result, ensure_ascii=True)), "error": False})
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")