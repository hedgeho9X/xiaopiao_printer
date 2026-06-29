"""统一业务错误类型。

本模块定义后端向 UI 暴露的可读错误对象。Bridge 层会把这些错误转换成
稳定的 JSON 结构，方便前端展示，也方便现场排查时快速判断是哪一层出错。
"""

from __future__ import annotations


class ReceiptVoiceError(Exception):
    """Receipt Voice 可预期业务错误。"""

    def __init__(self, message: str, *, code: str = "receipt_voice_error", detail: str = ""):
        """保存错误码、用户可读信息和可选技术细节。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class ConfigError(ReceiptVoiceError):
    """配置缺失或配置非法错误。"""

    def __init__(self, message: str, *, detail: str = ""):
        """创建配置错误。"""
        super().__init__(message, code="config_error", detail=detail)


class PrinterError(ReceiptVoiceError):
    """打印机枚举、测试或转发错误。"""

    def __init__(self, message: str, *, detail: str = ""):
        """创建打印机错误。"""
        super().__init__(message, code="printer_error", detail=detail)


class ParseError(ReceiptVoiceError):
    """LLM 小票结构化解析错误。"""

    def __init__(self, message: str, *, detail: str = ""):
        """创建解析错误。"""
        super().__init__(message, code="parse_error", detail=detail)
