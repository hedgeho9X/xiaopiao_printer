# Receipt Voice v1 架构

本文档描述桌面版 Receipt Voice 的模块边界。字段细节见 `docs/database-schema.md`，快捷键和朗读规则见 `docs/shortcuts.md`。

## 总体链路

```text
POS 打印数据
  -> app_core.monitor 接收 bytes
  -> app_core.receipt_parser 提取 raw_text
  -> app_core.order_store 写 print_jobs
  -> app_core.printer 原样转发 raw_bytes
  -> app_core.order_parser 生成订单草稿
  -> app_core.order_store 写 orders/order_items/order_events
  -> app_core.bridge_api 暴露给 Web UI
  -> ui/* 展示队列并触发快捷键/TTS
```

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `app_desktop.py` | 初始化 SQLite、后端服务和 pywebview 窗口 |
| `app_core.database` | 数据目录、SQLite 连接、schema 初始化 |
| `app_core.settings` | `app_settings` 默认值、打印方式到平台映射、TTS 语速 |
| `app_core.monitor` | 监听本机端口，接收打印 bytes，串联入库/转发/结构化 |
| `app_core.printer` | Windows 打印机枚举、RAW 转发、代理打印机创建 |
| `app_core.receipt_parser` | text-only 小票解码与 ESC/POS 控制码清洗 |
| `app_core.order_parser` | 美团/银豹规则解析，后续可替换或接入 LLM |
| `app_core.order_store` | 订单去重、商品重建、完成、撤销、队列查询 |
| `app_core.speech` | 按快捷键文档生成朗读文本，并调用 Windows SAPI |
| `app_core.bridge_api` | pywebview 暴露给 JS 的稳定 API |
| `ui/*` | 订单队列、当前订单详情、快捷键和设置交互 |

## 稳定边界

- UI 不直接读写 SQLite。
- UI 不直接操作打印机、文件系统、LLM 或 SAPI。
- 后端不直接操作 DOM；只通过 bridge 返回 JSON。
- 打印转发必须使用 `raw_bytes`，不能用 `raw_text` 重建。
- 订单解析失败不能阻断 `print_jobs` 入库和打印转发。

## 旧 PoC 文件

以下文件保留为 legacy 和回归参考：

```text
app_gui.py
capture_server.py
inspect_capture.py
print_raw.py
test_sender_app.py
```

新功能优先写入 `app_core/` 和 `ui/`，不要继续扩大旧 Tkinter PoC。

