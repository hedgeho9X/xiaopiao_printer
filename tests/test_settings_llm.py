"""LLM 设置与 DeepSeek 余额接口测试。

本文件验证默认 DeepSeek 配置、旧数据库补齐策略和余额接口辅助逻辑。
它不真实请求 DeepSeek，避免测试依赖网络和 API 余额。
"""

from __future__ import annotations

import sqlite3
import unittest

from app_core.database import initialize
from app_core.llm_parser import _deepseek_balance_url, _normalize_balance_payload
from app_core.settings import DEFAULT_LLM_API_KEY, DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL, SettingsStore


class LlmSettingsTest(unittest.TestCase):
    """LLM 配置默认值和辅助函数测试。"""

    def setUp(self) -> None:
        """创建内存数据库并初始化默认设置。"""
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        initialize(self.conn)
        self.settings = SettingsStore(self.conn)
        self.settings.initialize_defaults()

    def test_deepseek_defaults_are_initialized(self) -> None:
        """新库会默认写入 DeepSeek API 配置。"""
        self.assertEqual(self.settings.get("llm_provider_name"), "DeepSeek")
        self.assertEqual(self.settings.get("llm_base_url"), DEFAULT_LLM_BASE_URL)
        self.assertEqual(self.settings.get("llm_api_key"), DEFAULT_LLM_API_KEY)
        self.assertEqual(self.settings.get("llm_model"), DEFAULT_LLM_MODEL)
        self.assertEqual(self.settings.get("llm_enabled"), "1")

    def test_empty_legacy_llm_settings_are_filled(self) -> None:
        """旧库里空的 LLM 配置会被补成 DeepSeek 默认值。"""
        for key in ("llm_provider_name", "llm_base_url", "llm_api_key", "llm_model"):
            self.conn.execute("UPDATE app_settings SET value = '' WHERE key = ?", (key,))
        self.conn.commit()
        self.settings.initialize_defaults()
        self.assertEqual(self.settings.get("llm_provider_name"), "DeepSeek")
        self.assertEqual(self.settings.get("llm_base_url"), DEFAULT_LLM_BASE_URL)
        self.assertEqual(self.settings.get("llm_api_key"), DEFAULT_LLM_API_KEY)
        self.assertEqual(self.settings.get("llm_model"), DEFAULT_LLM_MODEL)

    def test_custom_llm_settings_are_not_overwritten(self) -> None:
        """用户自定义过的 LLM 配置不会被默认值覆盖。"""
        self.settings.save_many({
            "llm_provider_name": "Custom",
            "llm_base_url": "https://example.com/v1",
            "llm_api_key": "custom-key",
            "llm_model": "custom-model",
        })
        self.settings.initialize_defaults()
        self.assertEqual(self.settings.get("llm_provider_name"), "Custom")
        self.assertEqual(self.settings.get("llm_base_url"), "https://example.com/v1")
        self.assertEqual(self.settings.get("llm_api_key"), "custom-key")
        self.assertEqual(self.settings.get("llm_model"), "custom-model")

    def test_deepseek_balance_helpers(self) -> None:
        """余额接口 URL 和返回结构会被规范化。"""
        self.assertEqual(
            _deepseek_balance_url("https://api.deepseek.com/v1"),
            "https://api.deepseek.com/user/balance",
        )
        payload = _normalize_balance_payload({
            "is_available": True,
            "balance_infos": [{
                "currency": "CNY",
                "total_balance": "10.50",
                "granted_balance": "1.00",
                "topped_up_balance": "9.50",
            }],
        })
        self.assertTrue(payload["is_available"])
        self.assertEqual(payload["balance_infos"][0]["currency"], "CNY")
        self.assertEqual(payload["balance_infos"][0]["total_balance"], "10.50")


if __name__ == "__main__":
    unittest.main()
