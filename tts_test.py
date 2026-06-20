"""Windows SAPI TTS 测试脚本。

这个脚本用于排查当前电脑是否能通过 Windows 官方 SAPI 朗读中文。
它不会依赖项目里的打印逻辑，只测试系统语音、音量和朗读接口。
"""

import argparse
import time

import win32com.client


def create_speaker():
    """创建 Windows SAPI 朗读对象。"""
    return win32com.client.Dispatch("SAPI.SpVoice")


def list_voices(speaker) -> None:
    """列出当前系统安装的 SAPI 语音。"""
    for index, voice in enumerate(speaker.GetVoices()):
        print(f"{index}: {voice.GetDescription()}")


def choose_voice(speaker, keyword: str | None) -> None:
    """按关键词选择语音。

    Args:
        speaker: SAPI.SpVoice 对象。
        keyword: 语音描述中的关键词，例如 Huihui、Chinese、Zira。
    """
    if not keyword:
        return

    lowered = keyword.lower()
    for voice in speaker.GetVoices():
        desc = voice.GetDescription()
        if lowered in desc.lower():
            speaker.Voice = voice
            print(f"使用语音：{desc}")
            return

    print(f"没有找到包含关键词的语音：{keyword}")


def speak(text: str, voice: str | None, volume: int, rate: int, repeat: int) -> None:
    """朗读指定文本。"""
    speaker = create_speaker()
    choose_voice(speaker, voice)
    speaker.Volume = max(0, min(100, volume))
    speaker.Rate = max(-10, min(10, rate))

    print(f"音量：{speaker.Volume}")
    print(f"语速：{speaker.Rate}")
    print(f"文本：{text}")

    for index in range(repeat):
        print(f"朗读第 {index + 1}/{repeat} 次")
        speaker.Speak(text)
        time.sleep(0.3)


def main() -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="测试 Windows SAPI TTS。")
    parser.add_argument("--list", action="store_true", help="列出系统可用语音。")
    parser.add_argument("--voice", default="Huihui", help="语音关键词，默认 Huihui。")
    parser.add_argument("--volume", type=int, default=100, help="音量 0-100。")
    parser.add_argument("--rate", type=int, default=-1, help="语速 -10 到 10。")
    parser.add_argument("--repeat", type=int, default=1, help="重复朗读次数。")
    parser.add_argument(
        "--text",
        default="测试语音。新订单，冰美式一杯，少冰。请确认是否能听到。",
        help="要朗读的文本。",
    )
    args = parser.parse_args()

    speaker = create_speaker()
    if args.list:
        list_voices(speaker)
        return 0

    speak(args.text, args.voice, args.volume, args.rate, args.repeat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

