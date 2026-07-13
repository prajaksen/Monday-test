import unittest
from unittest.mock import patch

from app import app


class ChatbotTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_chat_requires_message(self):
        response = self.client.post("/chat", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "message is required")

    @patch("app.requests.post")
    def test_chat_returns_response_from_ollama(self, mock_post):
        class FakeResponse:
            def __init__(self, payload, status_code=200):
                self._payload = payload
                self.status_code = status_code

            def json(self):
                return self._payload

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception("boom")

        mock_post.return_value = FakeResponse({"model": "llama3.2:latest", "response": "Hello back", "done": True})

        response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["response"], "Hello back")


if __name__ == "__main__":
    unittest.main()
