/**
 * 订单状态与渲染。
 *
 * 本文件维护当前订单游标和商品游标，并渲染左侧队列与右侧详情。
 */

(function () {
  const state = {
    orders: [],
    currentOrderId: null,
    currentItemIndex: 0
  };

  function setOrders(orders) {
    state.orders = Array.isArray(orders) ? orders : [];
    if (!state.orders.some(order => order.id === state.currentOrderId)) {
      state.currentOrderId = state.orders[0] ? state.orders[0].id : null;
      state.currentItemIndex = 0;
    }
  }

  function currentOrder() {
    return state.orders.find(order => order.id === state.currentOrderId) || null;
  }

  function selectOrder(orderId) {
    if (!state.orders.some(order => order.id === orderId)) return null;
    state.currentOrderId = orderId;
    state.currentItemIndex = 0;
    render();
    return currentOrder();
  }

  function moveOrder(delta) {
    if (state.orders.length === 0) return null;
    const currentIndex = Math.max(0, state.orders.findIndex(order => order.id === state.currentOrderId));
    const nextIndex = (currentIndex + delta + state.orders.length) % state.orders.length;
    return selectOrder(state.orders[nextIndex].id);
  }

  function moveItem(delta) {
    const order = currentOrder();
    if (!order || !order.items.length) return -1;
    const nextIndex = state.currentItemIndex + delta;
    if (nextIndex < 0) return -1;
    if (nextIndex >= order.items.length) return order.items.length;
    state.currentItemIndex = nextIndex;
    render();
    return state.currentItemIndex;
  }

  function render() {
    renderQueue();
    renderDetail();
  }

  function renderQueue() {
    const list = document.getElementById('order-list');
    const count = document.getElementById('queue-count');
    count.textContent = `${state.orders.length} 单`;
    list.innerHTML = '';
    state.orders.forEach(order => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'queue-card' + (order.id === state.currentOrderId ? ' is-current' : '');
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', order.id === state.currentOrderId ? 'true' : 'false');
      button.dataset.orderId = order.id;
      button.innerHTML = `
        <span class="queue-headline">
          <span class="queue-title">${escapeHtml(order.displayNo)}</span>
          <span class="queue-time">${escapeHtml(displayTime(order))}</span>
        </span>
        <span class="queue-meta">${escapeHtml(joinFacts(order))}</span>
        <span class="queue-items">${escapeHtml(itemsSummary(order))}</span>
      `;
      button.addEventListener('click', () => selectOrder(order.id));
      list.appendChild(button);
    });
  }

  function renderDetail() {
    const detail = document.getElementById('order-detail');
    const order = currentOrder();
    if (!order) {
      detail.innerHTML = '<div class="empty-state"><h2>暂无待制作订单</h2><p>监听启动后，新小票会自动进入队列。</p></div>';
      return;
    }
    const items = order.items.map((item, index) => itemHtml(item, index)).join('');
    const note = order.orderNote ? `<section><h3 class="section-title">整单备注</h3><div class="order-note">${escapeHtml(order.orderNote)}</div></section>` : '';
    detail.innerHTML = `
      <div class="detail-heading">
        <div>
          <h2>${escapeHtml(order.displayNo)}</h2>
          <div class="detail-facts">${escapeHtml(joinFacts(order))}<br>${escapeHtml(order.orderTime || '')}</div>
        </div>
        <div class="print-count">打印 ${order.printCount || 1} 次</div>
      </div>
      <section>
        <h3 class="section-title">商品</h3>
        <div class="items">${items || '<p>没有结构化商品，可按 1 朗读原文。</p>'}</div>
      </section>
      ${note}
    `;
  }

  function itemHtml(item, index) {
    const current = index === state.currentItemIndex ? ' aria-current="true"' : '';
    return `
      <div class="item-row"${current}>
        <div class="item-index">${index + 1}</div>
        <div>
          <div class="item-name">${escapeHtml(item.name)}</div>
          <div class="item-options">${escapeHtml(item.itemOptions || '')}</div>
        </div>
        <div class="item-quantity">${escapeHtml(item.quantity || '')}</div>
      </div>
    `;
  }

  function joinFacts(order) {
    return uniqueFacts([
      platformLabel(order.platform),
      normalizeType(order.orderType),
      normalizePickup(order),
      locationFact(order)
    ]).join(' · ');
  }

  function itemsSummary(order) {
    if (!order.items || !order.items.length) return '未解析商品';
    const names = order.items.slice(0, 3).map(item => `${item.name}${item.quantity ? ' × ' + item.quantity : ''}`);
    return order.items.length > 3 ? `${names.join('，')} 等 ${order.items.length} 项` : names.join('，');
  }

  function displayTime(order) {
    const value = order.orderTime || order.createdAt || '';
    const match = String(value).match(/(?:\d{4}-)?(\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?/);
    if (match) return `${match[1]} ${match[2]}`;
    const timeOnly = String(value).match(/(\d{2}:\d{2})(?::\d{2})?/);
    return timeOnly ? timeOnly[1] : value;
  }

  function platformLabel(platform) {
    return { meituan: '美团', yinbao: '银豹', unknown: '未知' }[platform] || platform;
  }

  function normalizeType(value) {
    return String(value || '').trim();
  }

  function normalizePickup(order) {
    const pickup = String(order.pickupMethod || '').trim();
    const type = normalizeType(order.orderType);
    return pickup && pickup !== type ? pickup : '';
  }

  function locationFact(order) {
    const location = String(order.location || '').trim();
    if (!location || order.platform === 'meituan') return '';
    if (/^吧台\s*P/i.test(location)) return location.replace(/^吧台\s*/i, '吧台 ');
    if (/^P[\w-]*$/i.test(location)) return `吧台 ${location.toUpperCase()}`;
    if (location.includes('桌')) return `桌号 ${location}`;
    return location;
  }

  function uniqueFacts(values) {
    const seen = new Set();
    const facts = [];
    values.forEach(value => {
      const fact = String(value || '').trim();
      if (!fact || seen.has(fact)) return;
      seen.add(fact);
      facts.push(fact);
    });
    return facts;
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  window.ReceiptOrders = { state, setOrders, currentOrder, selectOrder, moveOrder, moveItem, render };
})();
