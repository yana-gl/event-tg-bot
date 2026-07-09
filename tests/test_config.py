import os
import unittest
from unittest.mock import patch

from tg_event.config import Settings, parse_channels


class ConfigTest(unittest.TestCase):
    def test_parse_channels_accepts_usernames_and_links(self):
        raw = " @events_vrn, https://t.me/avanturacoffee ,t.me/vrn_guide "

        self.assertEqual(parse_channels(raw), ["events_vrn", "avanturacoffee", "vrn_guide"])

    def test_parse_channels_rejects_empty_list(self):
        with self.assertRaisesRegex(ValueError, "CHANNELS"):
            parse_channels(" , ")

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
        }

        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.telegram_api_id, 12345)
        self.assertEqual(settings.channels, ["events_vrn", "vrn_guide"])
        self.assertEqual(settings.city, "Воронеж")
        self.assertEqual(str(settings.database_path), "data/test.sqlite3")
        self.assertEqual(settings.posts_per_channel, 20)


if __name__ == "__main__":
    unittest.main()
