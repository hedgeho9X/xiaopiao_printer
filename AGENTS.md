# AGENTS.md

本文件是 Receipt Voice PoC 项目的代理协作规范。任何自动化代码代理或人工维护者在修改本仓库前，都应该先阅读并遵守这里的约定。

## 项目目标

本项目面向听障/视障咖啡店场景，最终交付一个 all-in-one Windows 桌面程序：

- 监听美团、银豹等点单系统发出的本地小票打印数据。
- 保存原始打印内容，提取 text-only 小票文本。
- 将原始 bytes 原样转发到真实小票机，不能因为解析失败影响打印。
- 使用结构化 LLM 解析文本为订单对象。
- 在桌面 UI 中展示订单队列，并支持快捷键和语音播报。

当前阶段只考虑文本小票，不主动处理 bitmap、二维码、OCR 或 VLM。遇到非文本内容时保留原始文件和诊断信息即可。

## 技术路线

当前推荐路线：

```text
Python 主进程
  -> 打印监听/转发
  -> 文本解析
  -> LLM 结构化解析
  -> 本地存储
  -> Windows SAPI TTS
  -> pywebview 桌面窗口
       -> HTML + CSS + 原生 JS UI
```

原则：

- 最终交付形态是一个 exe 或安装包，店员不需要手动启动浏览器或命令行。
- Web UI 只负责展示、焦点、快捷键、交互状态。
- Python 负责打印、文件、LLM、TTS、Windows 系统能力。
- JS 与 Python 通过 pywebview bridge API 通信，不在 UI 里直接实现系统能力。
- 暂不引入 React/Vue/Svelte/Vite，除非 UI 复杂度明确超过原生三件套的合理范围。

## 目录约定

推荐逐步整理为：

```text
receipt-voice-poc/
  app_desktop.py
  app_core/
    monitor.py
    parser.py
    order_models.py
    order_store.py
    llm_parser.py
    tts.py
    settings.py
    bridge_api.py
  ui/
    index.html
    styles.css
    app.js
    bridge.js
    orders.js
    shortcuts.js
  docs/
    architecture.md
```

现有 PoC 文件可以逐步迁移，不要为了追求目录完美一次性大重构。

## 文件长度限制

- 单个源代码文件原则上不超过 500 行。
- 如果接近 500 行，应优先按职责拆分模块，而不是继续堆功能。
- 临时脚本、生成文件、第三方产物、文档文件不受此限制，但应避免无意义膨胀。
- 拆分时遵守“一个模块一个清晰职责”，不要为了行数机械拆碎。

## 注释规范

本项目使用中文注释和中文文档字符串。

### 文件头注释

每个手写源代码文件顶部都应说明：

- 这个文件负责什么。
- 它在整体链路中的位置。
- 是否直接操作打印机、文件系统、LLM、TTS 或 UI。

Python 示例：

```python
"""打印监听模块。

本模块负责监听代理打印端口，保存收到的原始小票 bytes，并将原始数据转发到真实打印机。
它位于“打印输入 -> 抓包落盘 -> 转发打印”的核心链路中。
"""
```

JavaScript 示例：

```js
/**
 * 订单工作台入口。
 *
 * 本文件负责初始化页面状态、绑定快捷键，并通过 bridge 调用 Python 后端能力。
 */
```

PowerShell 示例：

```powershell
<#
创建或修复代理打印机。

本脚本负责在 Windows 中创建指向 127.0.0.1:9100 的打印机，供美团/银豹选择。
#>
```

### 函数注释

每个公开函数、类、复杂私有函数都应有注释，说明：

- 输入是什么。
- 输出是什么。
- 是否有副作用。
- 出错时的行为。

Python 优先使用 docstring。JS 使用 JSDoc 风格注释。

简单的一两行私有 helper 可以不写长注释，但函数名必须自解释。

### 行内注释

重要代码行或不容易理解的业务判断，需要写简短中文注释。尤其包括：

- ESC/POS 控制码处理。
- Windows 打印机 API。
- pywebview bridge 边界。
- TTS 播报策略。
- LLM prompt/schema 约束。
- 快捷键与读屏软件兼容逻辑。

不要写无信息量注释，例如“给变量赋值”“调用函数”。

## Python 风格

- 遵守 PEP 8 代码风格。
- 遵守 PEP 257 文档字符串风格，但文案使用中文。
- 新增核心数据结构优先使用 `dataclass` 或 Pydantic 模型。
- 函数尽量有类型标注，特别是 app_core 内的模块边界。
- I/O、打印、LLM、TTS 等副作用应集中在清晰的服务类或模块中。
- 解析逻辑必须保留原始输入，不允许为了提取文本而破坏原始 bytes。

## JavaScript / UI 风格

- 当前阶段使用原生 JavaScript 模块化组织。
- UI 状态、渲染、快捷键、bridge 调用应拆分清楚。
- 不在 JS 中直接做打印机、文件系统、LLM 或 Windows TTS。
- 快捷键不能拦截输入框、文本域、下拉框和 contenteditable 内的按键。
- 不随意占用读屏软件和浏览器常用按键，例如 Tab、Shift+Tab、Enter、方向键、Space。
- 所有按钮和关键区域需要有清晰的可访问性语义。
- 自带语音播报应通过 Python SAPI 统一处理，Web Speech API 只能作为临时 PoC 或兜底。

## Bridge API 约定

pywebview 暴露给 JS 的 API 应稳定、少而清晰。第一版建议包含：

```text
get_orders()
get_current_order()
complete_order(order_id)
undo_complete()
speak_order(order_id)
speak_item(order_id, item_index)
stop_speaking()
get_settings()
save_settings(settings)
open_captures_folder()
start_monitor()
stop_monitor()
get_monitor_status()
```

约定：

- API 返回 JSON 可序列化对象，不返回 Python 专有对象。
- 所有 API 返回值包含 `ok` 字段，失败时包含 `error`。
- JS 不直接猜测 Python 异常类型，只展示可读错误消息。
- 长任务不要阻塞 UI，Python 侧使用线程、队列或后台 worker。

## 数据模型原则

订单结构应稳定，并与 UI 解耦。推荐核心对象：

- `CaptureRecord`：一次抓包记录，包含路径、时间、解析文本、转发状态。
- `Order`：结构化订单，包含来源、订单号、履约信息、商品列表。
- `OrderItem`：商品名、数量、规格、备注。
- `ParseResult`：LLM 或规则解析结果，包含置信度、错误、是否需要人工检查。
- `SpeechPlan`：播报文本，包含整单播报、摘要播报、单品播报。

LLM 解析失败时，必须保留 raw text，并允许 UI 以“未结构化订单”方式展示和朗读。

## 可靠性原则

- 打印转发是最高优先级，不能因为 LLM、TTS、UI 失败而中断。
- LLM 解析必须异步或后台处理。
- 没网、API key 缺失、模型报错时，应退回 raw text 展示/播报。
- 所有抓包原始文件必须完整保存。
- 用户切换真实打印机、启动/停止监听、创建代理打印机等操作必须写入日志。
- 现场诊断优先保留简单可读的本地日志和 JSON 文件。

## 隐私原则

- LLM 默认只接收制作咖啡所需文本。
- 如小票含手机号、地址、顾客名等信息，应在发送给 LLM 前尽量脱敏。
- 原始小票可以本地保存用于调试，但不要自动上传。
- 导出给开发者的调试包应尽量包含 raw text、解析 JSON、日志，避免包含不必要个人信息。

## Git 与变更原则

- 不要回滚用户已有修改，除非用户明确要求。
- 不要删除抓包样本、`HBC/` 原型或现场数据，除非用户明确要求。
- 代码变更应小步提交，优先保持现有 PoC 可运行。
- 大重构前先写迁移计划或架构文档。
- 修改 UI 快捷键时，同步更新对应文档和可访问性说明。

## 验收原则

任何核心链路变更至少验证：

- 能启动程序。
- 能监听代理端口。
- 能保存原始小票数据。
- 能解析 text 小票。
- 能原样转发到真实打印机或在无打印机模式下安全跳过。
- UI 能展示订单。
- 快捷键播报、切单、完成、撤销逻辑可用。

如果某项无法在当前机器验证，最终回复中必须明确说明。
