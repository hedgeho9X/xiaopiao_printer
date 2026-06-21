/**
 * 桌面工作台入口。
 *
 * 本文件负责初始化页面、轮询订单、执行快捷键行为，并通过 bridge 调用 Python 后端。
 * 设置抽屉的具体交互由 settings_panel.js 负责。
 */

(function () {
  let pendingCompleteOrderId = null;
  let cachedPrinters = [];
  const pollMs = 1800;

  async function init() {
    ReceiptSettingsPanel.init({ onChanged: handleSettingsChanged, onFeedback: setFeedback });
    bindButtons();
    ReceiptShortcuts.bindShortcuts(handleAction);
    await loadSettings();
    await refreshStatus();
    await refreshOrders();
    window.setInterval(refreshOrders, pollMs);
    window.setInterval(refreshStatus, 3000);
  }

  async function loadSettings() {
    const result = await ReceiptBridge.call('get_settings');
    if (!result.ok) return setFeedback(result.error);
    cachedPrinters = result.data.printers || [];
    ReceiptSettingsPanel.apply(result.data.settings || {}, cachedPrinters);
    syncActionBarShortcuts();
  }

  async function refreshOrders() {
    const result = await ReceiptBridge.call('get_orders');
    if (!result.ok) return setFeedback(result.error);
    ReceiptOrders.setOrders(result.data.orders || []);
    ReceiptOrders.render();
  }

  async function refreshStatus() {
    const result = await ReceiptBridge.call('get_monitor_status');
    if (!result.ok) return setFeedback(result.error);
    const status = result.data.status;
    const monitorText = status.monitors ? status.monitors.map(monitor => (
      `${monitor.printMethodLabel} ${monitor.port} · ${monitor.platform}`
    )).join(' / ') : `${status.printMethodLabel} · ${status.platform}`;
    document.getElementById('monitor-status').textContent = status.running
      ? `监听中：${monitorText}`
      : `监听未启动：${monitorText}`;
    document.getElementById('monitor-toggle').textContent = status.running ? '停止监听' : '开始监听';
  }

  function bindButtons() {
    document.querySelectorAll('[data-action]').forEach(button => {
      button.addEventListener('click', () => handleAction(button.dataset.action));
    });
    document.getElementById('monitor-toggle').addEventListener('click', toggleMonitor);
    document.getElementById('history-button').addEventListener('click', openHistory);
    document.getElementById('history-close').addEventListener('click', closeHistory);
    document.getElementById('settings-button').addEventListener('click', ReceiptSettingsPanel.open);
    document.getElementById('history-panel').addEventListener('click', event => {
      if (event.target.id === 'history-panel') closeHistory();
    });
  }

  async function handleAction(action) {
    const order = ReceiptOrders.currentOrder();
    const noOrderActions = [
      'stop_speaking', 'cycle_rate', 'undo_complete', 'increase_type_scale', 'decrease_type_scale'
    ];
    if (!order && !noOrderActions.includes(action)) {
      return speakFeedback('当前没有待制作订单。');
    }
    if (action !== 'complete_order') pendingCompleteOrderId = null;
    const actions = {
      read_full: () => ReceiptBridge.call('speak_full', order.id),
      read_overview: () => ReceiptBridge.call('speak_overview', order.id),
      read_items: () => ReceiptBridge.call('speak_items', order.id),
      previous_item: () => speakMovedItem(-1),
      next_item: () => speakMovedItem(1),
      previous_order: () => switchOrder(-1),
      next_order: () => switchOrder(1),
      complete_order: () => completeOrder(order),
      stop_speaking: () => ReceiptBridge.call('stop_speaking'),
      cycle_rate: cycleRate,
      undo_complete: undoComplete,
      increase_type_scale: () => ReceiptSettingsPanel.changeTypeScale(1),
      decrease_type_scale: () => ReceiptSettingsPanel.changeTypeScale(-1)
    };
    const result = await actions[action]();
    if (result && !result.ok) setFeedback(result.error);
  }

  async function speakMovedItem(delta) {
    const order = ReceiptOrders.currentOrder();
    const index = ReceiptOrders.moveItem(delta);
    return ReceiptBridge.call('speak_item', order.id, index);
  }

  async function switchOrder(delta) {
    const order = ReceiptOrders.moveOrder(delta);
    if (!order) return speakFeedback('当前没有待制作订单。');
    return ReceiptBridge.call('speak_switch_order', order.id);
  }

  async function completeOrder(order) {
    if (pendingCompleteOrderId !== order.id) {
      pendingCompleteOrderId = order.id;
      return speakFeedback(`准备完成 ${order.displayNo}。再按 ${shortcutText('complete_order')} 确认。`);
    }
    pendingCompleteOrderId = null;
    const result = await ReceiptBridge.call('complete_order', order.id);
    if (!result.ok) return result;
    ReceiptOrders.setOrders(result.data.orders || []);
    ReceiptOrders.render();
    const next = ReceiptOrders.currentOrder();
    if (next) return ReceiptBridge.call('speak_switch_order', next.id);
    return speakFeedback('所有订单已完成。');
  }

  async function undoComplete() {
    const result = await ReceiptBridge.call('undo_complete');
    if (!result.ok) return result;
    ReceiptOrders.setOrders(result.data.orders || []);
    const restored = result.data.restored;
    if (restored) ReceiptOrders.selectOrder(restored.id);
    ReceiptOrders.render();
    if (restored) return ReceiptBridge.call('speak_switch_order', restored.id);
    return speakFeedback('没有可撤销的完成记录。');
  }

  async function cycleRate() {
    const result = await ReceiptBridge.call('cycle_rate');
    if (result.ok && result.data.settings) {
      ReceiptSettingsPanel.apply(result.data.settings, cachedPrinters);
    }
    return result;
  }

  async function toggleMonitor() {
    const status = await ReceiptBridge.call('get_monitor_status');
    if (!status.ok) return setFeedback(status.error);
    const result = status.data.status.running
      ? await ReceiptBridge.call('stop_monitor')
      : await ReceiptBridge.call('start_monitor');
    if (!result.ok) return setFeedback(result.error);
    await refreshStatus();
  }

  async function openHistory() {
    document.getElementById('history-panel').hidden = false;
    await refreshHistory();
  }

  function closeHistory() {
    document.getElementById('history-panel').hidden = true;
  }

  async function refreshHistory() {
    const result = await ReceiptBridge.call('get_history_orders', 50);
    if (!result.ok) return setFeedback(result.error);
    renderHistory(result.data.orders || []);
  }

  function renderHistory(orders) {
    const list = document.getElementById('history-list');
    if (!orders.length) {
      list.innerHTML = '<p class="history-meta">暂无历史订单。</p>';
      return;
    }
    list.innerHTML = orders.map(order => `
      <div class="history-card">
        <div>
          <div class="history-title">${escapeHtml(order.displayNo)}</div>
          <div class="history-meta">${escapeHtml(historyFacts(order))}</div>
          <div class="history-items">${escapeHtml(historyItems(order))}</div>
        </div>
        <button type="button" data-restore-order="${escapeHtml(order.id)}">恢复</button>
      </div>
    `).join('');
    list.querySelectorAll('[data-restore-order]').forEach(button => {
      button.addEventListener('click', async () => {
        const result = await ReceiptBridge.call('restore_order', button.dataset.restoreOrder);
        if (!result.ok) return setFeedback(result.error);
        ReceiptOrders.setOrders(result.data.orders || []);
        if (result.data.restored) ReceiptOrders.selectOrder(result.data.restored.id);
        ReceiptOrders.render();
        await refreshHistory();
        setFeedback(result.data.restored ? '订单已恢复到待制作。' : '订单无法恢复。');
      });
    });
  }

  function handleSettingsChanged() {
    syncActionBarShortcuts();
  }

  function syncActionBarShortcuts() {
    const map = ReceiptShortcuts.getShortcutMap();
    document.querySelectorAll('.action-bar [data-action]').forEach(button => {
      const action = button.dataset.action;
      const shortcut = map[action] || '';
      const key = button.querySelector('.action-key');
      const label = button.querySelector('.action-label');
      if (key) key.textContent = ReceiptShortcuts.formatShortcut(shortcut);
      button.setAttribute('aria-keyshortcuts', shortcut);
      button.title = `${ReceiptShortcuts.formatShortcut(shortcut)} ${label ? label.textContent : ''}`.trim();
    });
  }

  function shortcutText(action) {
    return ReceiptShortcuts.formatShortcut(ReceiptShortcuts.getShortcutMap()[action] || '');
  }

  function historyFacts(order) {
    return [
      order.platform,
      order.orderType,
      order.location,
      order.orderTime,
      order.updatedAt ? `完成 ${order.updatedAt}` : ''
    ].filter(Boolean).join(' · ');
  }

  function historyItems(order) {
    if (!order.items || !order.items.length) return '未解析商品';
    return order.items.map(item => `${item.name}${item.quantity ? ' × ' + item.quantity : ''}`).join('，');
  }

  function speakFeedback(text) {
    setFeedback(text);
    return ReceiptBridge.call('speak_text', text);
  }

  function setFeedback(text) {
    document.getElementById('feedback').textContent = text || '';
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
