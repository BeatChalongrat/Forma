"""Load clients respect admission backpressure without replaying ambiguous failed writes."""

import unittest
from unittest.mock import Mock, patch

import httpx
import runtime_gate


class AdmissionTests(unittest.TestCase):
    def response(self, code, headers=None):
        return httpx.Response(
            code,
            json={"id": "ok"},
            headers=headers,
            request=httpx.Request("POST", "http://localhost/api/batches"),
        )

    def test_retries_429_with_same_payload_and_retry_after(self):
        client = Mock()
        client.request.side_effect = [
            self.response(429, {"Retry-After": "2"}),
            self.response(201),
        ]
        with patch.object(runtime_gate.time, "sleep") as sleep:
            result = runtime_gate.request_with_backpressure(
                client, "POST", "/api/batches", files=[("files", ("a.csv", b"sku\na"))]
            )
        self.assertEqual(result, {"id": "ok"})
        self.assertEqual(
            client.request.call_args_list[0], client.request.call_args_list[1]
        )
        sleep.assert_called_once_with(2)

    def test_does_not_retry_ambiguous_write_failure(self):
        client = Mock()
        client.request.return_value = self.response(503)
        with self.assertRaises(httpx.HTTPStatusError):
            runtime_gate.request_with_backpressure(client, "POST", "/api/batches")
        self.assertEqual(client.request.call_count, 1)

    def test_busy_budget_is_bounded(self):
        client = Mock()
        client.request.return_value = self.response(429)
        with (
            patch.object(runtime_gate.time, "sleep"),
            self.assertRaises(httpx.HTTPStatusError),
        ):
            runtime_gate.request_with_backpressure(client, "POST", "/api/batches")
        self.assertEqual(client.request.call_count, 13)


if __name__ == "__main__":
    unittest.main()
