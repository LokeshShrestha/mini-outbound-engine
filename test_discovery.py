import unittest
from unittest.mock import patch

import requests

from app.discovery import DiscoveryError, discover_companies


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload or {"elements": []}
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"status {self.status}")

    def json(self):
        return self.payload


class DiscoveryTests(unittest.TestCase):
    @patch("app.discovery.requests.post")
    @patch("app.discovery.requests.get")
    def test_overpass_fallback_returns_companies(self, get, post):
        get.return_value = FakeResponse({"lat": "27.7"})
        get.return_value.json = lambda: [{"lat": "27.7", "lon": "85.3"}]
        post.side_effect = [
            FakeResponse(status=504),
            FakeResponse({"elements": [{"type": "node", "id": 1, "tags": {"name": "Acme"}}]}),
        ]

        result = discover_companies("software company", "Kathmandu", limit=5)

        self.assertEqual(result[0]["name"], "Acme")
        self.assertEqual(post.call_count, 2)

    @patch("app.discovery.requests.post")
    @patch("app.discovery.requests.get")
    def test_all_overpass_failures_are_friendly(self, get, post):
        get.return_value = FakeResponse()
        get.return_value.json = lambda: [{"lat": "27.7", "lon": "85.3"}]
        post.side_effect = requests.Timeout("timed out")

        with self.assertRaisesRegex(DiscoveryError, "timed out"):
            discover_companies("clinic", "Kathmandu")


if __name__ == "__main__":
    unittest.main()
