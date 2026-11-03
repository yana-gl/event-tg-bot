import os
import unittest
from unittest.mock import patch

from tg_event.config import Settings


class ConfigTest(unittest.TestCase):
    def test_settings_from_env_parses_poc_values(self):
        env = {
            "TELEGRAM_API_ID": "12345",
            "TELEGRAM_API_HASH": "hash-value",
            "TELEGRAM_PHONE": "+79990000000",
            "OPENROUTER_API_KEY": "or-key",
            "OPENROUTER_MODEL": "qwen/free",
            "OPENROUTER_FALLBACK_MODEL": "qwen/fallback",
            "CITY": "Воронеж",
            "CHANNELS": "@events_vrn,@vrn_guide",
            "DATABASE_PATH": "data/test.sqlite3",
            "EVENT_TIMEZONE": "Europe/Moscow",
        }

        with patch.dict(os.environ, env, clear=True), patch("tg_event.config.load_dotenv"):
            settings = Settings.from_env()

        self.assertEqual(settings.telegram_api_id, 12345)
        self.assertFalse(hasattr(settings, "channels"))
        self.assertEqual(settings.city, "Воронеж")
        self.assertEqual(str(settings.database_path), "data/test.sqlite3")
        self.assertEqual(settings.posts_per_channel, 20)
        self.assertEqual(settings.event_timezone, "Europe/Moscow")
        without_channels = {k: v for k, v in env.items() if k != "CHANNELS"}
        with patch.dict(os.environ, without_channels, clear=True), patch("tg_event.config.load_dotenv"):
            self.assertFalse(hasattr(Settings.from_env(), "channels"))

        env.pop("CITY")
        env.pop("EVENT_TIMEZONE")
        with patch.dict(os.environ, env, clear=True), patch("tg_event.config.load_dotenv"):
            settings = Settings.from_env()
            self.assertEqual(settings.city, "Владивосток")
            self.assertEqual(settings.event_timezone, "Asia/Vladivostok")


if __name__ == "__main__":
    unittest.main()
