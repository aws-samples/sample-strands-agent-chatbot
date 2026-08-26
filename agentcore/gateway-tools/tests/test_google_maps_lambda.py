"""Tests for the Google Maps Lambda function."""

import json
from unittest.mock import MagicMock, patch

from conftest import load_lambda


lf = load_lambda("google-maps")
get_place_details = lf.get_place_details


class TestGetPlaceDetails:
    """Regression tests for Google Places detail requests."""

    @patch.object(lf, "get_google_maps_client")
    def test_calls_supported_googlemaps_place_signature(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.place.return_value = {
            "result": {
                "name": "Test Place",
                "formatted_address": "123 Test Street",
                "reviews": [],
            }
        }
        mock_get_client.return_value = mock_client

        result = get_place_details({
            "place_id": "test-place-id",
            "language": "ko",
            # Older callers may still send this until the Gateway schema update
            # propagates. The Lambda must ignore it instead of forwarding it to
            # googlemaps.Client.place(), which does not support the argument.
            "reviews_sort": "newest",
        })

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        content = json.loads(body["content"][0]["text"])
        assert content["name"] == "Test Place"

        call_kwargs = mock_client.place.call_args.kwargs
        assert call_kwargs["place_id"] == "test-place-id"
        assert call_kwargs["language"] == "ko"
        assert "reviews_sort" not in call_kwargs
        assert "review" in call_kwargs["fields"]
        assert "reviews" not in call_kwargs["fields"]
        assert set(call_kwargs["fields"]) <= lf.googlemaps.places.PLACES_DETAIL_FIELDS

    @patch.object(lf, "get_google_maps_client", return_value=MagicMock())
    def test_requires_place_id(self, _mock_get_client):
        result = get_place_details({})

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "place_id" in body["error"]
