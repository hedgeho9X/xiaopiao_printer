/**
 * Python bridge 封装。
 *
 * 本文件负责调用 window.pywebview.api，并在浏览器直接打开时提供 mock 数据。
 */

(function () {
  const pywebviewReady = new Promise(resolve => {
    if (window.pywebview && window.pywebview.api) {
      resolve(true);
      return;
    }
    window.addEventListener('pywebviewready', () => resolve(true), { once: true });
    window.setTimeout(() => resolve(false), 3000);
  });

  const mockSettings = {
    target_printer_name: '',
    proxy_printer_name: 'Receipt Voice Proxy',
    tts_rate: 'medium',
    ui_type_scale: 'normal',
    shortcuts: JSON.stringify({
      read_full: '1',
      read_overview: '2',
      read_items: '3',
      previous_item: '4',
      next_item: '5',
      previous_order: '6',
      next_order: '7',
      complete_order: '8',
      stop_speaking: '9',
      cycle_rate: '0',
      undo_complete: 'Ctrl+Z',
      increase_type_scale: 'Ctrl+Plus',
      decrease_type_scale: 'Ctrl+Minus'
    }),
    llm_enabled: '0',
    llm_provider_name: 'OpenAI Compatible',
    llm_base_url: 'https://api.openai.com/v1',
    llm_api_key: '',
    llm_model: ''
  };

  const mockOrders = [
    {
      id: 'mock_meituan_1',
      displayNo: '美团 #1',
      platform: 'meituan',
      orderType: '自取',
      pickupMethod: '顾客到店自取',
      location: '1号口袋',
      orderTime: '2026-05-21 11:41:00',
      orderNote: '顾客需要餐具',
      printCount: 1,
      rawText: '',
      items: [
        { id: 'mock_item_1', sortOrder: 1, name: '经典美式', quantity: '1', price: '6.5', itemOptions: '冷，默认' }
      ]
    },
    {
      id: 'mock_yinbao_1',
      displayNo: '银豹 大桌子 D1',
      platform: 'yinbao',
      orderType: '堂食',
      pickupMethod: '堂食',
      location: '大桌子 D1',
      orderTime: '2026-06-07 10:52:12',
      orderNote: '',
      printCount: 1,
      rawText: '',
      items: [
        { id: 'mock_item_2', sortOrder: 1, name: '填好肚子 瑞士卷', quantity: '1', price: '15', itemOptions: '原味瑞士卷' }
      ]
    }
  ];

  async function call(name, ...args) {
    if (hasNativeApi(name)) {
      return window.pywebview.api[name](...args);
    }
    await pywebviewReady;
    if (hasNativeApi(name)) {
      return window.pywebview.api[name](...args);
    }
    return fallbackCall(name, args);
  }

  function hasNativeApi(name) {
    return Boolean(
      window.pywebview &&
      window.pywebview.api &&
      typeof window.pywebview.api[name] === 'function'
    );
  }

  async function fallbackCall(name, args) {
    if (name === 'get_orders') return { ok: true, data: { orders: mockOrders } };
    if (name === 'get_history_orders') return { ok: true, data: { orders: [] } };
    if (name === 'get_current_order') return { ok: true, data: { order: mockOrders[0] } };
    if (name === 'get_settings') {
      return {
        ok: true,
        data: {
          settings: { ...mockSettings },
          printers: ['GP-5850II', 'Receipt Voice Test Printer']
        }
      };
    }
    if (name === 'save_settings') {
      Object.assign(mockSettings, args[0] || {});
      if (typeof mockSettings.shortcuts !== 'string') {
        mockSettings.shortcuts = JSON.stringify(mockSettings.shortcuts);
      }
      return { ok: true, data: { settings: { ...mockSettings } } };
    }
    if (name === 'test_llm_connection') {
      return { ok: false, error: '当前没有连接 Python 后端，请用 python app_desktop.py 打开桌面程序后再测试。' };
    }
    if (name === 'get_monitor_status') {
      return {
        ok: true,
        data: {
          status: {
            running: false,
            monitors: [
              { printMethodLabel: '网口打印', port: 9100, platform: 'yinbao' },
              { printMethodLabel: 'USB 打印', port: 9101, platform: 'meituan' }
            ]
          }
        }
      };
    }
    if (name === 'cycle_rate') {
      const order = ['slow', 'medium', 'fast', 'faster', 'fastest'];
      const index = order.indexOf(mockSettings.tts_rate);
      mockSettings.tts_rate = order[(Math.max(0, index) + 1) % order.length];
      return { ok: true, data: { spokenText: `语速已切换为${mockSettings.tts_rate}`, settings: { ...mockSettings } } };
    }
    if (name.startsWith('speak')) {
      return { ok: true, data: { spokenText: args[0] || '' } };
    }
    return { ok: true, data: {} };
  }

  window.ReceiptBridge = { call };
})();
