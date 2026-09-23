import unittest
from unittest.mock import patch

from mcp_workshop import stortinget_mcp
from pydantic import ValidationError


class FakeResponse:
    content = b"{}"
    is_success = True
    status_code = 200

    def json(self):
        return {}


class FakeClient:
    requested_url = None
    requested_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def get(self, url, params):
        self.requested_url = url
        self.requested_params = params
        return FakeResponse()


class StortingetSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_out_of_scope_operation_is_rejected_before_http(self):
        with patch("mcp_workshop.stortinget_mcp.httpx.AsyncClient") as client:
            payload, error = await stortinget_mcp._get_json("moter")

        self.assertIsNone(payload)
        self.assertEqual(error["retry"], "Use representatives, parties, or votes.")
        client.assert_not_called()

    async def test_approved_operation_uses_fixed_endpoint_mapping(self):
        fake_client = FakeClient()
        with patch(
            "mcp_workshop.stortinget_mcp.httpx.AsyncClient",
            return_value=fake_client,
        ):
            payload, error = await stortinget_mcp._get_json("representatives")

        self.assertEqual(payload, {})
        self.assertIsNone(error)
        self.assertEqual(
            fake_client.requested_url,
            "https://data.stortinget.no/eksport/dagensrepresentanter",
        )
        self.assertEqual(fake_client.requested_params, {"format": "json"})

    async def test_vote_identifier_is_validated_before_http(self):
        with self.assertRaises(ValidationError):
            stortinget_mcp.VotesRequest(case_id="travel-expenses")


class StortingetValidationTests(unittest.TestCase):
    def test_representative_text_is_bounded(self):
        with self.assertRaises(ValidationError):
            stortinget_mcp.RepresentativesRequest(municipality="x" * 101)


if __name__ == "__main__":
    unittest.main()