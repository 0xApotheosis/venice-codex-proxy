import copy
import importlib.util
import os
import unittest
from pathlib import Path

# proxy.py loads API key at import time; provide a harmless test value.
os.environ.setdefault("VENICE_API_KEY", "test-key")

PROXY_PATH = Path(__file__).resolve().parents[0] / "proxy.py"
spec = importlib.util.spec_from_file_location("proxy_mod", PROXY_PATH)
proxy_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(proxy_mod)


class NormalizeInputForVeniceTests(unittest.TestCase):
    def test_converts_input_text_and_input_image_parts(self):
        payload = {
            "model": "gpt-5.1-codex",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "describe this"},
                        {"type": "input_image", "image_url": "https://example.com/cat.png"},
                    ],
                }
            ],
        }

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertTrue(changed)
        content = normalized["input"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "describe this"})
        self.assertEqual(
            content[1],
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        )

    def test_wraps_string_image_url_even_when_type_already_image_url(self):
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": "data:image/png;base64,AAAA"}],
                }
            ]
        }

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertTrue(changed)
        self.assertEqual(
            normalized["input"][0]["content"][0],
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        )

    def test_is_idempotent_for_already_normalized_content(self):
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "already normalized"},
                        {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    ],
                }
            ]
        }
        original = copy.deepcopy(payload)

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertFalse(changed)
        self.assertIs(normalized, payload)
        self.assertEqual(normalized, original)

    def test_preserves_non_message_tool_items(self):
        payload = {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "lookup_weather",
                    "arguments": "{\"city\":\"Austin\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_123",
                    "output": "sunny",
                },
                {
                    "type": "item_reference",
                    "id": "item_abc",
                },
            ]
        }
        original = copy.deepcopy(payload)

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertFalse(changed)
        self.assertIs(normalized, payload)
        self.assertEqual(normalized, original)

    def test_mixed_payload_normalizes_messages_without_touching_tool_items(self):
        payload = {
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_999",
                    "name": "do_thing",
                    "arguments": "{}",
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "caption"},
                        {"type": "input_image", "image_url": "https://example.com/img.png"},
                    ],
                },
            ]
        }

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertTrue(changed)
        self.assertEqual(normalized["input"][0], payload["input"][0])
        self.assertEqual(normalized["input"][1]["content"][0]["type"], "text")
        self.assertEqual(normalized["input"][1]["content"][1]["type"], "image_url")
        self.assertEqual(
            normalized["input"][1]["content"][1]["image_url"],
            {"url": "https://example.com/img.png"},
        )

    def test_returns_unchanged_when_no_compatible_input_list_exists(self):
        payload = {"input": "hello"}

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertFalse(changed)
        self.assertIs(normalized, payload)

    def test_returns_unchanged_when_content_is_not_list(self):
        payload = {
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": "plain-text-content",
                }
            ]
        }

        normalized, changed = proxy_mod._normalize_input_for_venice(payload)

        self.assertFalse(changed)
        self.assertIs(normalized, payload)


class ConfigAndHelpersTests(unittest.TestCase):
    def test_env_int_accepts_valid_value(self):
        os.environ["TEST_INT"] = "42"
        self.assertEqual(proxy_mod._env_int("TEST_INT", 5), 42)
        os.environ.pop("TEST_INT", None)

    def test_env_int_falls_back_on_invalid_or_small_values(self):
        import logging
        logging.disable(logging.CRITICAL)
        try:
            os.environ["TEST_INT"] = "abc"
            self.assertEqual(proxy_mod._env_int("TEST_INT", 7), 7)

            os.environ["TEST_INT"] = "0"
            self.assertEqual(proxy_mod._env_int("TEST_INT", 9, min_value=1), 9)
        finally:
            os.environ.pop("TEST_INT", None)
            logging.disable(logging.NOTSET)

    def test_mask_secret(self):
        self.assertEqual(proxy_mod._mask_secret("12345678"), "********")
        self.assertEqual(proxy_mod._mask_secret("abcdefghijkl"), "abcdef...ijkl")

    def test_placeholder_key_detection(self):
        self.assertTrue(proxy_mod._looks_like_placeholder("your-venice-api-key-here"))
        self.assertTrue(proxy_mod._looks_like_placeholder("CHANGEME"))
        self.assertFalse(proxy_mod._looks_like_placeholder("real-secret-key"))


if __name__ == "__main__":
    unittest.main()
