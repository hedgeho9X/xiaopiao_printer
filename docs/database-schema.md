# SQLite 数据库表设计 v1

本文档定义 Receipt Voice v1 的正式 SQLite 存储结构。历史 `.json` 调试文件不再作为正式存储；后续 UI、TTS、LLM 解析结果、完成状态和调试导出都以 SQLite 为准。

## 设计原则

- 当前只处理文本小票。
- 数据库服务于视障咖啡师做单，不做复杂订单系统。
- 平台 `platform` 默认由当前接入配置决定；如果小票文本中出现非常明确的美团/银豹特征，解析链路可以纠正平台，避免端口或代理打印机配置错误导致订单归类错误。
- 打印方式 `print_method` 和平台 `platform` 是两个概念：
  - `print_method`：技术入口，`network_print` 或 `usb_print`。
  - `platform`：业务平台，`yinbao` 或 `meituan`。
- 当前现场可以默认配置为：
  - `network_print -> yinbao`
  - `usb_print -> meituan`
- 商品的规格、冷热、糖度、冰量、换奶、加料等商品属性全部合并进 `order_items.item_options`，方便朗读。
- 整单备注单独保存在 `orders.order_note`。朗读商品列表时，也需要把整单备注读出来，避免漏听。
- 金额、数量、时间第一版都优先存原文文本，不做复杂数值计算。

## 表总览

第一版固定 5 张表：

| 表名 | 用途 |
| --- | --- |
| `print_jobs` | 保存每一次打印输入、原始 bytes、解析文本和转发结果 |
| `orders` | 保存咖啡师工作台使用的订单 |
| `order_items` | 保存订单商品 |
| `order_events` | 保存订单创建、完成、撤销、重复打印等事件 |
| `app_settings` | 保存打印机、TTS、UI 和 schema 配置 |

主工作流只依赖：

```text
orders + order_items
```

排查问题时再看：

```text
print_jobs + order_events
```

## 枚举值

### print_method

数据库值使用英文，UI 和日志显示中文。

| 数据库值 | 中文显示 | 说明 |
| --- | --- | --- |
| `network_print` | 网口打印 | POS 软件通过 TCP/IP 打到本机监听端口 |
| `usb_print` | USB 打印 | POS 软件选择 Windows 代理打印机，再由程序转发到真实 USB 小票机 |

### platform

| 数据库值 | 中文显示 |
| --- | --- |
| `yinbao` | 银豹 |
| `meituan` | 美团 |
| `unknown` | 未知 |

### order status

| 数据库值 | 中文显示 | 说明 |
| --- | --- | --- |
| `pending` | 待制作 | 显示在工作台队列 |
| `done` | 已完成 | 不显示在待制作队列 |

## print_jobs

`print_jobs` 记录每一次打印输入。它是调试和重放的基础，但不是咖啡师主要看的表。

```sql
CREATE TABLE IF NOT EXISTS print_jobs (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,

  print_method TEXT NOT NULL,
  platform TEXT NOT NULL,

  raw_bytes BLOB NOT NULL,
  raw_text TEXT NOT NULL,

  forwarded INTEGER NOT NULL DEFAULT 0,
  forward_error TEXT,

  order_id TEXT,

  FOREIGN KEY(order_id) REFERENCES orders(id)
);
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 本次打印输入 ID，例如 `job_20260620_153012_a1b2c3d4` |
| `created_at` | TEXT | 收到打印数据的时间，ISO 8601 字符串 |
| `print_method` | TEXT | 打印方式：`network_print` 或 `usb_print` |
| `platform` | TEXT | 业务平台：`yinbao`、`meituan` 或 `unknown` |
| `raw_bytes` | BLOB | 原始打印 bytes，用于完整重放和排查 |
| `raw_text` | TEXT | 从小票 bytes 中解析出的可读文本 |
| `forwarded` | INTEGER | 是否已转发到真实小票机，0/1 |
| `forward_error` | TEXT | 转发失败原因；成功时为空 |
| `order_id` | TEXT | 本次打印关联的订单；解析失败前可为空 |

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_print_jobs_created_at
ON print_jobs(created_at);

CREATE INDEX IF NOT EXISTS idx_print_jobs_order_id
ON print_jobs(order_id);

CREATE INDEX IF NOT EXISTS idx_print_jobs_method_platform
ON print_jobs(print_method, platform);
```

## orders

`orders` 是咖啡师工作台的核心表。UI 队列、快捷键朗读、完成和撤销都围绕这张表。

```sql
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  dedupe_key TEXT UNIQUE,

  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',

  platform TEXT NOT NULL,
  platform_order_no TEXT,
  display_no TEXT NOT NULL,

  order_time TEXT,
  order_type TEXT,
  pickup_method TEXT,
  location TEXT,
  order_note TEXT,

  raw_text TEXT NOT NULL,

  first_job_id TEXT NOT NULL,
  latest_job_id TEXT NOT NULL,
  print_count INTEGER NOT NULL DEFAULT 1,

  FOREIGN KEY(first_job_id) REFERENCES print_jobs(id),
  FOREIGN KEY(latest_job_id) REFERENCES print_jobs(id)
);
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 内部订单 ID，例如 `ord_20260620_153013_f3e8a9c1` |
| `dedupe_key` | TEXT | 去重键；使用 `sha256(规范化 raw_text)`，平台和平台单号不参与去重 |
| `created_at` | TEXT | 订单第一次进入系统的时间 |
| `updated_at` | TEXT | 订单最近更新时间 |
| `status` | TEXT | 订单状态：`pending` 或 `done` |
| `platform` | TEXT | 业务平台：`yinbao`、`meituan` 或 `unknown` |
| `platform_order_no` | TEXT | 平台订单号；美团订单号或银豹消费流水 |
| `display_no` | TEXT | UI 和 TTS 使用的可读单号，例如 `美团 #8273`、`银豹 8888` |
| `order_time` | TEXT | 小票上的下单/打印时间原文 |
| `order_type` | TEXT | 订单类型，例如 `外卖`、`堂食`、`自取` |
| `pickup_method` | TEXT | 取件方式，例如 `骑手取餐`、`顾客自取`、`堂食` |
| `location` | TEXT | 位置类信息，例如 `1号口袋`、`大桌子 D1`、`T001` |
| `order_note` | TEXT | 整单备注，例如 `顾客需要餐具` |
| `raw_text` | TEXT | 完整可读小票文本；结构化失败时用于兜底朗读 |
| `first_job_id` | TEXT | 第一次创建该订单的打印输入 |
| `latest_job_id` | TEXT | 最近一次关联该订单的打印输入 |
| `print_count` | INTEGER | 该订单被重复打印的次数 |

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_orders_status_created_at
ON orders(status, created_at);

CREATE INDEX IF NOT EXISTS idx_orders_platform_order_no
ON orders(platform, platform_order_no);

CREATE INDEX IF NOT EXISTS idx_orders_latest_job_id
ON orders(latest_job_id);
```

去重规则：

```text
无论是否解析到 platform_order_no：
  normalized_text = 去掉空行，并对每一行 trim 后重新用换行拼接
  digest = sha256(normalized_text).hexdigest() 的前 16 位
  dedupe_key = "raw:" + digest

如果 raw_text 清洗后为空：
  dedupe_key = NULL，直接创建未解析订单
```

重复打印处理：

```text
同一个 dedupe_key 再次出现：
  不创建新订单
  orders.latest_job_id = 新 job
  orders.raw_text = 新小票文本
  orders.print_count += 1
  orders.updated_at = 当前时间
  重建 order_items
  写 order_events.reprinted

说明：

```text
platform 和 platform_order_no 仍然保存到 orders.platform / orders.platform_order_no，
用于 UI 展示、朗读和排查，但不再决定去重。

这样可以避免 LLM 解析抖动、条码/平台号误识别、平台兜底为 unknown、
银豹/美团单号形态差异把同一张小票拆成多张订单。
```
```

## order_items

`order_items` 保存商品明细。为了朗读自然，商品规格、冷热、糖度、冰量、换奶、加料等商品属性全部合并到 `item_options`。

```sql
CREATE TABLE IF NOT EXISTS order_items (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,

  sort_order INTEGER NOT NULL,
  name TEXT NOT NULL,
  quantity TEXT,
  price TEXT,
  item_options TEXT,

  raw_text TEXT,

  FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
  UNIQUE(order_id, sort_order)
);
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 商品 ID，例如 `item_20260620_153013_0001` |
| `order_id` | TEXT | 所属订单 ID |
| `sort_order` | INTEGER | 商品顺序，从 1 开始 |
| `name` | TEXT | 商品名，例如 `热拿铁` |
| `quantity` | TEXT | 份数原文，例如 `1`、`2`、`x2` |
| `price` | TEXT | 价格原文；可以为空 |
| `item_options` | TEXT | 商品属性，包含规格、冰量、糖度、换奶、加料、口味等 |
| `raw_text` | TEXT | 该商品对应的小票原文片段，方便排查 |

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_order_items_order_id
ON order_items(order_id);
```

示例：

| name | quantity | price | item_options |
| --- | --- | --- | --- |
| `热拿铁` | `1` | `18` | `大杯，半糖，换燕麦奶` |
| `冰美式` | `2` | `32` | `少冰` |

## order_events

`order_events` 保存订单生命周期事件。它不影响主 UI，但方便撤销、排查和后续导出。

```sql
CREATE TABLE IF NOT EXISTS order_events (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  created_at TEXT NOT NULL,

  event_type TEXT NOT NULL,
  detail TEXT,

  FOREIGN KEY(order_id) REFERENCES orders(id)
);
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT | 事件 ID |
| `order_id` | TEXT | 对应订单 |
| `created_at` | TEXT | 事件发生时间 |
| `event_type` | TEXT | 事件类型 |
| `detail` | TEXT | 简单说明 |

事件类型：

| event_type | 说明 |
| --- | --- |
| `created` | 创建订单 |
| `reprinted` | 同一小票全文重复打印 |
| `done` | 完成订单 |
| `undo_done` | 撤销完成 |

索引：

```sql
CREATE INDEX IF NOT EXISTS idx_order_events_order_id_created_at
ON order_events(order_id, created_at);
```

## app_settings

`app_settings` 保存程序配置。复杂值可以用 JSON 字符串。

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

建议配置：

| key | 示例 | 说明 |
| --- | --- | --- |
| `schema_version` | `1` | 数据库 schema 版本 |
| `network_print_platform` | `yinbao` | 网口打印默认平台 |
| `usb_print_platform` | `meituan` | USB 打印默认平台 |
| `target_printer_name` | `GP-5850II` | 真实小票机 |
| `tts_voice` | `Microsoft Huihui Desktop` | 默认语音 |
| `tts_rate` | `-2` | 默认语速 |
| `ui_theme` | `light` | UI 主题 |
| `ui_type_scale` | `normal` | 字号 |

## 写入流程

每次收到打印数据：

```text
1. 判断 print_method：
   网口进来的数据 -> network_print
   Windows 代理打印机进来的数据 -> usb_print

2. 根据配置得到 platform：
   network_print_platform -> 默认 yinbao
   usb_print_platform -> 默认 meituan

3. 写 print_jobs：
   raw_bytes、raw_text、print_method、platform、forwarded 等信息。

4. 原样转发 raw_bytes 到真实小票机。

5. 用 LLM 或规则解析 raw_text：
   得到 platform_order_no、display_no、order_time、order_type、pickup_method、location、order_note、items。

6. 根据规范化 raw_text 生成 dedupe_key。

7. 用 dedupe_key 查找旧订单。

8. 找到旧订单：
   更新 orders，重建 order_items，写 reprinted 事件。

9. 没找到旧订单：
   创建 orders，创建 order_items，写 created 事件。

10. 回填 print_jobs.order_id。
```

## UI 查询

工作台读取待制作订单：

```sql
SELECT *
FROM orders
WHERE status = 'pending'
ORDER BY COALESCE(NULLIF(order_time, ''), created_at) DESC,
         created_at DESC;
```

读取商品：

```sql
SELECT *
FROM order_items
WHERE order_id = :order_id
ORDER BY sort_order ASC;
```

完成订单：

```sql
UPDATE orders
SET status = 'done',
    updated_at = :now
WHERE id = :order_id;
```

撤销完成：

```sql
UPDATE orders
SET status = 'pending',
    updated_at = :now
WHERE id = :order_id;
```

## TTS 播报字段

整单播报使用：

```text
orders.display_no
orders.platform
orders.order_type
orders.pickup_method
orders.location
orders.order_time
orders.order_note
order_items.name
order_items.quantity
order_items.item_options
```

单品播报使用：

```text
第 {sort_order} 项，{name}，{quantity} 份，{item_options}
```

朗读商品列表时，先逐个朗读商品，再朗读整单备注：

```text
{name}，{quantity} 份，{item_options}
整单备注：{order_note}
```

如果订单无法结构化：

```text
orders.display_no = 未解析订单 HH:mm
order_items 为空
TTS 直接朗读 orders.raw_text
```

## 真实小票案例

本节记录目前已知的两类真实小票形态，后续解析器和 LLM schema 应以这些 case 做回归测试。

### 美团小票 case

图片来源：`d2b0c7dbfc075696c2d71775a5870790.jpg`。图片存在手指遮挡，以下只记录可见内容。

小票可见原文：

```text
**#1美团拼好饭**
*下心咖啡（南栅店品）*

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
```

整理后的 `orders`：

| 字段 | 值 |
| --- | --- |
| `print_method` | `usb_print` |
| `platform` | `meituan` |
| `platform_order_no` | 图片遮挡/不可见，若解析不到则为空 |
| `display_no` | `美团 #1` |
| `order_time` | `2026-05-21 11:41:00` |
| `order_type` | `自取` |
| `pickup_method` | `顾客到店自取` |
| `location` | `1号口袋` |
| `order_note` | `顾客需要餐具` |
| `raw_text` | 小票完整可读文本 |

整理后的 `order_items`：

| sort_order | name | quantity | price | item_options |
| --- | --- | --- | --- | --- |
| `1` | `经典美式` | `1` | `6.5` | `冷，默认` |

朗读示例：

```text
美团 #1，自取，顾客到店自取，1号口袋，预计二零二六年五月二十一日十一点四十八到店。
经典美式，一份，冷，默认。
整单备注：顾客需要餐具。
```

### 银豹小票 case

图片来源：`3fb4abc1af268307fbb759ac463607e1.jpg`。

小票可见原文：

```text
手心咖啡
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
```

整理后的 `orders`：

| 字段 | 值 |
| --- | --- |
| `print_method` | `network_print` |
| `platform` | `yinbao` |
| `platform_order_no` | `202606071052125990004` |
| `display_no` | `银豹 大桌子 D1` |
| `order_time` | `2026-06-07 10:52:12` |
| `order_type` | `堂食` |
| `pickup_method` | `堂食` |
| `location` | `大桌子 D1` |
| `order_note` | 空 |
| `raw_text` | 小票完整可读文本 |

整理后的 `order_items`：

| sort_order | name | quantity | price | item_options |
| --- | --- | --- | --- | --- |
| `1` | `填好肚子 瑞士卷` | `1` | `15` | `原味瑞士卷` |

说明：

- `[填好肚子]` 是商品区的一部分，不是备注。
- `瑞士卷` 是商品名主体。
- `原味瑞士卷￥0/` 是商品属性/口味，放入 `item_options`。

朗读示例：

```text
银豹 大桌子 D1，堂食，大桌子 D1。
填好肚子瑞士卷，一份，原味瑞士卷。
```
