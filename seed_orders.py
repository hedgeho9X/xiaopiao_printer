"""测试订单注入脚本。

本脚本用于开发阶段快速生成订单数据：
1. 直接写入 SQLite，方便打开 Web UI 看订单。
2. 发送到 127.0.0.1:9100，模拟 POS 软件通过监听端口打印。

它不会操作真实打印机，除非目标监听程序自己配置了转发。
"""

from __future__ import annotations

import argparse
import socket
import sys

from app_core.database import connect, default_db_path, initialize
from app_core.order_parser import parse_order_text
from app_core.order_store import OrderStore
from app_core.receipt_parser import parse_receipt_text
from app_core.settings import SettingsStore


MEITUAN_TEXT = """**#1美团拼好饭**
*手心咖啡（南栅店品）*

【顾客到店自取】

美团客人
门店新客
顾客号码：手机尾号 2548
虚拟号码：18613141546 转 4423
备用号码1：15710730462 转 3900
备用号码2：14743662895 转 2411

预计 2026-05-21 11:48:00 到店

下单时间：2026-05-21 11:41:00

备注：顾客需要餐具；

1号口袋

经典美式
-冷
-【默认】
*1  6.5
"""

YINBAO_TEXT = """手心咖啡
收银员：1001  牌号：大桌子 D1
单据号：202606071052125990004
下单时间：2026-06-07 10:52:12

商品名称        单价    数量    小计
[填好肚子]       15     1       15
瑞士卷
原味瑞士卷￥0/

原价：15.0       总数：1.00
现价：15         支付：网店会员储值
:15
实收：15         找零：0

堂食
广东省广州市白云区沙太南路181号V
lab翌方181园区负一层UB03室

手心咖啡感谢有您，祝生活愉快～
"""

SAMPLES = {
    "meituan": {
        "text": MEITUAN_TEXT,
        "print_method": "usb_print",
        "platform": "meituan",
    },
    "yinbao": {
        "text": YINBAO_TEXT,
        "print_method": "network_print",
        "platform": "yinbao",
    },
}


def encode_receipt(text: str) -> bytes:
    """把测试小票文本编码成接近真实小票的 gbk bytes。"""
    return text.encode("gbk", errors="replace")


def seed_sqlite(sample_name: str) -> str:
    """直接把测试订单写入 SQLite，并返回订单 ID。"""
    sample = SAMPLES[sample_name]
    raw_bytes = encode_receipt(sample["text"])
    parsed = parse_receipt_text(raw_bytes)

    conn = connect()
    initialize(conn)
    settings = SettingsStore(conn)
    settings.initialize_defaults()
    store = OrderStore(conn)

    job_id = store.create_print_job(
        print_method=sample["print_method"],
        platform=sample["platform"],
        raw_bytes=raw_bytes,
        raw_text=parsed.text,
    )
    store.update_forward_result(job_id, forwarded=False, forward_error="测试脚本直接入库，未转发打印。")
    draft = parse_order_text(sample["platform"], parsed.text)
    return store.upsert_order_from_draft(
        job_id=job_id,
        platform=sample["platform"],
        raw_text=parsed.text,
        draft=draft,
    )


def send_tcp(sample_name: str, host: str, port: int) -> int:
    """把测试小票发送到监听端口，模拟真实打印输入。"""
    data = encode_receipt(SAMPLES[sample_name]["text"])
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(data)
    return len(data)


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="Seed Receipt Voice test orders.")
    parser.add_argument(
        "sample",
        choices=["meituan", "yinbao", "all"],
        help="要生成的测试订单。",
    )
    parser.add_argument(
        "--mode",
        choices=["db", "tcp"],
        default="db",
        help="db=直接写 SQLite；tcp=发送到监听端口。",
    )
    parser.add_argument("--host", default="127.0.0.1", help="TCP host。")
    parser.add_argument("--port", type=int, default=9100, help="TCP port。")
    args = parser.parse_args(argv)

    names = ["meituan", "yinbao"] if args.sample == "all" else [args.sample]
    if args.mode == "db":
        print(f"SQLite: {default_db_path()}")
        for name in names:
            order_id = seed_sqlite(name)
            print(f"已写入 {name} 测试订单：{order_id}")
        return 0

    for name in names:
        byte_count = send_tcp(name, args.host, args.port)
        print(f"已发送 {name} 测试小票到 {args.host}:{args.port}，{byte_count} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

