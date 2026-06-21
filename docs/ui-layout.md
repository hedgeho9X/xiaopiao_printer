# Web UI 布局设计 v1

本文档定义 Receipt Voice 桌面 Web UI 的布局和职责。快捷键行为以 `docs/shortcuts.md` 为准。

## 布局

```text
顶部状态栏：监听状态、打印方式、真实小票机、系统操作
左侧 sidebar：待制作订单队列
右侧主区域：当前订单详情
底部操作栏：四个高频动作
右侧设置抽屉：朗读、快捷键、外观、LLM、系统
```

## 左侧订单队列

每个订单卡片展示：

```text
display_no
平台 / 订单类型 / 取件方式 / location
商品摘要
```

左侧队列只负责选择订单，不展示完整原文，不承担调试职责。

## 当前订单详情

主区域展示：

```text
display_no
平台、订单类型、取件方式、location、order_time
商品列表：name、quantity、item_options
整单备注：order_note
```

如果没有结构化商品，显示“没有结构化商品，可按 1 朗读原文”。

## 滚动规则

工作台整体固定为窗口高度，不让整页滚动：

```text
.app-shell: height 100vh, overflow hidden
.sidebar: 独立滚动
.order-detail: 独立滚动
.action-bar: 位于 grid 底部，不覆盖内容
```

这样订单列表再长，也只滚动左侧队列，不会把底部操作栏推走。

## 底部操作栏

底部操作栏只展示四个最高频按钮：

```text
播放
上一单
下一单
完成
```

按钮文字需要同步当前自定义快捷键。其他行为仍通过快捷键和设置页说明保留。

## 设置抽屉

设置从右侧打开，内部使用分类 sidebar：

```text
朗读：五档语速
快捷键：自定义键位、重置默认
外观：字号档位、Ctrl + / Ctrl -
LLM：Provider、Base API、Key、Model、测试连接
系统：真实小票机、修复代理、数据目录
```

## 可访问性约束

- 当前订单详情区域可聚焦。
- 队列项使用 button 和 `aria-selected`。
- 输入框、下拉框中不拦截快捷键。
- 不占用 `Tab`、方向键、`Enter`、`Space`、`Esc`。
- 状态反馈写入 `role=status` 的 live region，并同步调用后端 SAPI。
