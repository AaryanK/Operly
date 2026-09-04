import json
import unittest

from apps.api.request_safety import (
    MAX_RUNTIME_REQUEST_ID_BYTES,
    McpRequestSafetyError,
    _prepare_mcp_body,
)


class McpRequestSafetyTests(unittest.TestCase):
    def _tool_call(self, *, rpc_id=42, meta=None):
        params = {
            "name": "workflow.run.start",
            "arguments": {"workflow_id": "workflow-1"},
        }
        if meta is not None:
            params["_meta"] = meta
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "tools/call",
            "params": params,
        }

    def test_same_jsonrpc_tool_call_gets_same_bounded_request_id(self):
        raw = json.dumps(self._tool_call()).encode("utf-8")
        first = json.loads(_prepare_mcp_body(raw))
        second = json.loads(_prepare_mcp_body(raw))

        request_id = first["params"]["_meta"]["operly/requestId"]
        self.assertEqual(request_id, second["params"]["_meta"]["operly/requestId"])
        self.assertTrue(request_id.startswith("mcp-rpc:"))
        self.assertLessEqual(len(request_id.encode("utf-8")), MAX_RUNTIME_REQUEST_ID_BYTES)

    def test_jsonrpc_string_and_number_ids_do_not_collide(self):
        numeric = json.loads(
            _prepare_mcp_body(json.dumps(self._tool_call(rpc_id=42)).encode("utf-8"))
        )
        textual = json.loads(
            _prepare_mcp_body(json.dumps(self._tool_call(rpc_id="42")).encode("utf-8"))
        )
        self.assertNotEqual(
            numeric["params"]["_meta"]["operly/requestId"],
            textual["params"]["_meta"]["operly/requestId"],
        )

    def test_tools_call_without_scalar_jsonrpc_id_fails_closed(self):
        for rpc_id in (None, True, {"nested": 1}, [1]):
            with self.subTest(rpc_id=rpc_id):
                with self.assertRaises(McpRequestSafetyError):
                    _prepare_mcp_body(
                        json.dumps(self._tool_call(rpc_id=rpc_id)).encode("utf-8")
                    )

    def test_duplicate_json_fields_fail_closed_before_tool_execution(self):
        raw = (
            b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/call",'
            b'"params":{"name":"workflow.run.start","arguments":{}}}'
        )
        with self.assertRaises(McpRequestSafetyError) as raised:
            _prepare_mcp_body(raw)
        self.assertEqual(raised.exception.code, "DUPLICATE_JSON_FIELD")

    def test_overlong_explicit_operly_request_id_is_rejected_not_truncated(self):
        raw = json.dumps(
            self._tool_call(
                rpc_id=7,
                meta={"operly/requestId": "x" * (MAX_RUNTIME_REQUEST_ID_BYTES + 1)},
            )
        ).encode("utf-8")
        with self.assertRaises(McpRequestSafetyError) as raised:
            _prepare_mcp_body(raw)
        self.assertEqual(raised.exception.code, "MCP_REQUEST_ID_TOO_LONG")

    def test_valid_explicit_request_id_is_preserved_exactly(self):
        raw = json.dumps(
            self._tool_call(rpc_id=7, meta={"operly/requestId": "client-stable-7"})
        ).encode("utf-8")
        payload = json.loads(_prepare_mcp_body(raw))
        self.assertEqual(
            payload["params"]["_meta"]["operly/requestId"],
            "client-stable-7",
        )


if __name__ == "__main__":
    unittest.main()
