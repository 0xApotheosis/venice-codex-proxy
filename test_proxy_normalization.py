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


if __name__ == "__main__":
    unittest.main()
