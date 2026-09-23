import unittest

from pydantic import ValidationError

from src.ssb_mcp import (
    FindStatisticsRequest,
    MetadataRequest,
    SampleRequest,
)


class SsbValidationTests(unittest.TestCase):
    def test_valid_sample_request(self):
        request = SampleRequest(
            table_id="05212",
            selections=[{"variable_code": "Region", "value_codes": ["*"]}],
            year_from=2020,
            year_to=2024,
        )
        self.assertEqual(request.year_to, 2024)

    def test_invalid_topic_is_rejected(self):
        with self.assertRaises(ValidationError):
            FindStatisticsRequest(topic="housing")

    def test_invalid_table_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            MetadataRequest(table_id="123")

    def test_invalid_year_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            SampleRequest(
                table_id="05212",
                selections=[{"variable_code": "Region", "value_codes": ["*"]}],
                year_from=2025,
                year_to=2024,
            )

    def test_too_many_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            SampleRequest(
                table_id="05212",
                selections=[
                    {"variable_code": "Tid", "value_codes": ["2020", "2021", "2022", "2023", "2024", "2025"]}
                ],
            )


if __name__ == "__main__":
    unittest.main()