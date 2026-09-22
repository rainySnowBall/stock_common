import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_common.llm import (
    ChatLLMSettings,
    build_chat_completions_url,
    extract_chat_completion_content,
)
from stock_common.model_selection.defaults import build_model_profiles


class ChatLLMTests(unittest.TestCase):
    def test_build_chat_completions_url_appends_endpoint(self) -> None:
        self.assertEqual(
            build_chat_completions_url("https://api.deepseek.com"),
            "https://api.deepseek.com/chat/completions",
        )
        self.assertEqual(
            build_chat_completions_url("https://api.example.com/v1"),
            "https://api.example.com/v1/chat/completions",
        )

    def test_build_chat_completions_url_preserves_full_endpoint(self) -> None:
        self.assertEqual(
            build_chat_completions_url("https://api.example.com/v1/chat/completions"),
            "https://api.example.com/v1/chat/completions",
        )

    def test_settings_from_model_profile_uses_profile_envs_first(self) -> None:
        profile = build_model_profiles(
            (
                {
                    "model_id": "custom-chat",
                    "model_name_env": "CUSTOM_MODEL",
                    "base_url_env": "CUSTOM_BASE_URL",
                    "api_key_env": "CUSTOM_API_KEY",
                    "timeout_seconds_env": "CUSTOM_TIMEOUT",
                    "max_retries_env": "CUSTOM_RETRIES",
                    "supported_functions": ("chat",),
                    "supported_difficulties": ("easy",),
                },
            )
        )[0]

        settings = ChatLLMSettings.from_model_profile(
            profile,
            env={
                "CUSTOM_MODEL": "custom-model",
                "CUSTOM_BASE_URL": "https://custom.example/v1",
                "CUSTOM_API_KEY": "profile-key",
                "CUSTOM_TIMEOUT": "9",
                "CUSTOM_RETRIES": "0",
                "DEEPSEEK_API_KEY": "fallback-key",
            },
            load_env_files=False,
        )

        self.assertEqual(settings.model_name, "custom-model")
        self.assertEqual(settings.api_key, "profile-key")
        self.assertEqual(settings.api_url, "https://custom.example/v1/chat/completions")
        self.assertEqual(settings.timeout_seconds, 9)
        self.assertEqual(settings.max_retries, 0)

    def test_settings_from_model_profile_supports_deepseek_fallback_envs(self) -> None:
        profile = build_model_profiles(
            (
                {
                    "model_id": "fallback-chat",
                    "supported_functions": ("chat",),
                    "supported_difficulties": ("easy",),
                },
            )
        )[0]

        settings = ChatLLMSettings.from_model_profile(
            profile,
            env={
                "DEEPSEEK_MODEL": "deepseek-chat",
                "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                "DEEPSEEK_API_KEY": "deepseek-key",
            },
            load_env_files=False,
        )

        self.assertEqual(settings.model_name, "deepseek-chat")
        self.assertEqual(settings.api_key, "deepseek-key")
        self.assertEqual(settings.api_url, "https://api.deepseek.com/chat/completions")

    def test_extract_chat_completion_content(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "answer",
                    }
                }
            ]
        }

        self.assertEqual(extract_chat_completion_content(payload), "answer")


if __name__ == "__main__":
    unittest.main()
