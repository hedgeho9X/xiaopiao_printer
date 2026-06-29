/**
 * 设置抽屉交互。
 *
 * 本文件管理设置页的分类 sidebar、快捷键编辑、语速、字号、LLM 和系统设置。
 * 它通过 ReceiptBridge 保存配置，并把快捷键和字号变化通知工作台。
 */

(function () {
  const typeScales = ['normal', 'large', 'xlarge', 'xxlarge', 'max'];
  let settings = {};
  let onChanged = () => {};
  let onFeedback = () => {};
  let capturingAction = '';

  function init(options) {
    onChanged = options.onChanged || onChanged;
    onFeedback = options.onFeedback || onFeedback;
    bindDrawer();
    bindSpeech();
    bindAppearance();
    bindShortcuts();
    bindLlm();
    bindSystem();
  }

  function apply(nextSettings, printers) {
    settings = { ...(nextSettings || {}) };
    fillPrinterSelect(printers || [], settings.target_printer_name || '');
    fillLlmSettings();
    applyShortcutSettings();
    applyTypeScale(settings.ui_type_scale || 'normal');
    renderRate();
    renderTypeScale();
    renderShortcutList();
  }

  function open() {
    document.getElementById('settings-panel').hidden = false;
  }

  function close() {
    document.getElementById('settings-panel').hidden = true;
    capturingAction = '';
    renderShortcutList();
  }

  async function savePatch(patch, message) {
    const result = await ReceiptBridge.call('save_settings', patch);
    if (!result.ok) return setFeedback(result.error);
    settings = result.data.settings || settings;
    apply(settings, currentPrinterOptions());
    onChanged(settings);
    return setFeedback(message || '设置已保存。');
  }

  async function changeTypeScale(delta) {
    const current = settings.ui_type_scale || 'normal';
    const index = Math.max(0, typeScales.indexOf(current));
    const next = typeScales[Math.max(0, Math.min(typeScales.length - 1, index + delta))];
    if (next === current) return setFeedback(delta > 0 ? '字号已经是最大。' : '字号已经是标准。');
    await savePatch({ ui_type_scale: next }, `字号已切换为${typeScaleLabel(next)}。`);
  }

  function bindDrawer() {
    document.getElementById('settings-close').addEventListener('click', close);
    document.getElementById('settings-panel').addEventListener('click', event => {
      if (event.target.id === 'settings-panel') close();
    });
    document.querySelectorAll('[data-settings-tab]').forEach(button => {
      button.addEventListener('click', () => selectTab(button.dataset.settingsTab));
    });
  }

  function bindSpeech() {
    document.querySelectorAll('[data-rate]').forEach(button => {
      button.addEventListener('click', () => savePatch({ tts_rate: button.dataset.rate }, '语速已保存。'));
    });
  }

  function bindAppearance() {
    document.querySelectorAll('[data-type-scale]').forEach(button => {
      button.addEventListener('click', () => savePatch({ ui_type_scale: button.dataset.typeScale }, '字号已保存。'));
    });
  }

  function bindShortcuts() {
    document.getElementById('shortcuts-reset').addEventListener('click', () => {
      saveShortcutMap(ReceiptShortcuts.getDefaultShortcutMap(), '快捷键已恢复默认。');
    });
    window.addEventListener('keydown', event => {
      if (!capturingAction) return;
      event.preventDefault();
      event.stopPropagation();
      const shortcut = ReceiptShortcuts.normalizeKeyEvent(event);
      const nextMap = { ...ReceiptShortcuts.getShortcutMap(), [capturingAction]: shortcut };
      try {
        ReceiptShortcuts.validateShortcutMap(nextMap);
      } catch (error) {
        return setFeedback(error.message || String(error));
      }
      const action = capturingAction;
      capturingAction = '';
      saveShortcutMap(nextMap, `${actionLabel(action)} 已改为 ${ReceiptShortcuts.formatShortcut(shortcut)}。`);
    }, true);
  }

  function bindLlm() {
    document.getElementById('settings-save').addEventListener('click', saveLlmSettings);
    document.getElementById('llm-test-button').addEventListener('click', testLlmConnection);
    document.getElementById('llm-balance-button').addEventListener('click', queryLlmBalance);
  }

  function bindSystem() {
    document.getElementById('target-printer').addEventListener('change', async event => {
      await savePatch({ target_printer_name: event.target.value }, '真实小票机已保存。可先刷新诊断，再分别做 RAW 测试和普通测试。');
      await refreshPrinterDiagnostics();
    });
    document.getElementById('printer-diagnostics-button').addEventListener('click', refreshPrinterDiagnostics);
    document.getElementById('printer-test-button').addEventListener('click', testPrinter);
    document.getElementById('printer-gdi-test-button').addEventListener('click', testGdiPrinter);
    document.getElementById('proxy-button').addEventListener('click', async () => {
      const result = await autoPrepareForwarding();
      if (result.ok) setFeedback('代理打印机和监听已尝试修复。');
    });
    document.getElementById('data-folder-button').addEventListener('click', () => ReceiptBridge.call('open_data_folder'));
  }

  function selectTab(tab) {
    document.querySelectorAll('[data-settings-tab]').forEach(button => {
      button.classList.toggle('is-active', button.dataset.settingsTab === tab);
    });
    document.querySelectorAll('[data-settings-page]').forEach(page => {
      page.classList.toggle('is-active', page.dataset.settingsPage === tab);
    });
  }

  function applyShortcutSettings() {
    try {
      const stored = JSON.parse(settings.shortcuts || '{}');
      ReceiptShortcuts.setShortcutMap(stored);
    } catch {
      ReceiptShortcuts.resetShortcutMap();
    }
  }

  function renderShortcutList() {
    const list = document.getElementById('shortcut-list');
    const map = ReceiptShortcuts.getShortcutMap();
    list.innerHTML = ReceiptShortcuts.actionList().map(action => `
      <div class="shortcut-row">
        <div>
          <strong>${escapeHtml(action.settingLabel)}</strong>
          <span>${escapeHtml(action.id)}</span>
        </div>
        <button type="button" data-shortcut-action="${escapeHtml(action.id)}">
          ${capturingAction === action.id ? '按下新键' : escapeHtml(ReceiptShortcuts.formatShortcut(map[action.id]))}
        </button>
      </div>
    `).join('');
    list.querySelectorAll('[data-shortcut-action]').forEach(button => {
      button.addEventListener('click', () => {
        capturingAction = button.dataset.shortcutAction;
        setFeedback(`正在修改 ${actionLabel(capturingAction)}，请按下新键。`);
        renderShortcutList();
      });
    });
  }

  async function saveShortcutMap(map, message) {
    try {
      ReceiptShortcuts.setShortcutMap(map);
    } catch (error) {
      return setFeedback(error.message || String(error));
    }
    capturingAction = '';
    await savePatch({ shortcuts: ReceiptShortcuts.getShortcutMap() }, message);
  }

  function renderRate() {
    const current = settings.tts_rate || 'medium';
    document.querySelectorAll('[data-rate]').forEach(button => {
      button.classList.toggle('is-active', button.dataset.rate === current);
      button.setAttribute('aria-checked', button.dataset.rate === current ? 'true' : 'false');
    });
  }

  function renderTypeScale() {
    const current = settings.ui_type_scale || 'normal';
    document.querySelectorAll('[data-type-scale]').forEach(button => {
      button.classList.toggle('is-active', button.dataset.typeScale === current);
      button.setAttribute('aria-checked', button.dataset.typeScale === current ? 'true' : 'false');
    });
  }

  function applyTypeScale(scale) {
    document.documentElement.dataset.typeScale = typeScales.includes(scale) ? scale : 'normal';
  }

  function fillLlmSettings() {
    document.getElementById('llm-provider-name').value = settings.llm_provider_name || 'DeepSeek';
    document.getElementById('llm-base-url').value = settings.llm_base_url || 'https://api.deepseek.com';
    document.getElementById('llm-api-key').value = settings.llm_api_key || '';
    document.getElementById('llm-model').value = settings.llm_model || '';
  }

  function collectLlmSettings() {
    return {
      llm_enabled: '1',
      llm_provider_name: document.getElementById('llm-provider-name').value.trim(),
      llm_base_url: document.getElementById('llm-base-url').value.trim(),
      llm_api_key: document.getElementById('llm-api-key').value.trim(),
      llm_model: document.getElementById('llm-model').value.trim()
    };
  }

  async function saveLlmSettings() {
    await savePatch(collectLlmSettings(), 'LLM 设置已保存。');
  }

  async function testPrinter() {
    const button = document.getElementById('printer-test-button');
    button.disabled = true;
    renderPrinterMessage('正在发送 RAW ESC/POS 测试...');
    try {
      const result = await ReceiptBridge.call('test_printer', document.getElementById('target-printer').value);
      if (result.ok) {
        setFeedback(result.data.message);
        await refreshPrinterDiagnostics(result.data.job);
      } else {
        renderPrinterMessage(`RAW 测试失败：${result.error}`);
        setFeedback(`RAW 测试失败：${result.error}`);
      }
    } finally {
      button.disabled = false;
    }
  }

  async function testGdiPrinter() {
    const button = document.getElementById('printer-gdi-test-button');
    button.disabled = true;
    renderPrinterMessage('正在发送 Windows 普通打印测试...');
    try {
      const result = await ReceiptBridge.call('test_printer_gdi', document.getElementById('target-printer').value);
      if (result.ok) {
        setFeedback(result.data.message);
        await refreshPrinterDiagnostics(result.data.job);
      } else {
        renderPrinterMessage(`普通测试失败：${result.error}`);
        setFeedback(`普通测试失败：${result.error}`);
      }
    } finally {
      button.disabled = false;
    }
  }

  async function refreshPrinterDiagnostics(lastJob) {
    const printerName = document.getElementById('target-printer').value;
    if (!printerName) {
      renderPrinterMessage('请先选择真实小票机。');
      return;
    }
    const button = document.getElementById('printer-diagnostics-button');
    button.disabled = true;
    try {
      const result = await ReceiptBridge.call('get_printer_diagnostics', printerName);
      if (result.ok) {
        renderPrinterDiagnostics(result.data.diagnostics, lastJob);
      } else {
        renderPrinterMessage(`诊断失败：${result.error}`);
      }
    } finally {
      button.disabled = false;
    }
  }

  async function autoPrepareForwarding() {
    const proxyResult = await ReceiptBridge.call('create_proxy_printer');
    if (!proxyResult.ok) {
      setFeedback(`代理修复失败：${proxyResult.error}`);
      return proxyResult;
    }
    const monitorResult = await ReceiptBridge.call('start_monitor');
    if (!monitorResult.ok) {
      setFeedback(`监听启动失败：${monitorResult.error}`);
      return monitorResult;
    }
    setFeedback('代理打印机已修复，监听已启动。');
    return monitorResult;
  }

  async function testLlmConnection() {
    const button = document.getElementById('llm-test-button');
    button.disabled = true;
    document.getElementById('llm-test-result').textContent = '正在测试连接...';
    try {
      const result = await ReceiptBridge.call('test_llm_connection', collectLlmSettings());
      const message = result.ok ? `连接成功：${result.data.message}` : `连接失败：${result.error}`;
      document.getElementById('llm-test-result').textContent = message;
      setFeedback(message);
    } finally {
      button.disabled = false;
    }
  }

  async function queryLlmBalance() {
    const button = document.getElementById('llm-balance-button');
    button.disabled = true;
    document.getElementById('llm-balance-result').textContent = '正在查询余额...';
    try {
      const result = await ReceiptBridge.call('get_llm_balance', collectLlmSettings());
      const message = result.ok ? formatBalance(result.data.balance) : `余额查询失败：${result.error}`;
      document.getElementById('llm-balance-result').textContent = message;
      setFeedback(message);
    } finally {
      button.disabled = false;
    }
  }

  function formatBalance(balance) {
    const available = balance && balance.is_available ? '可用' : '不可用';
    const infos = balance && Array.isArray(balance.balance_infos) ? balance.balance_infos : [];
    if (!infos.length) return `账户状态：${available}。暂无余额明细。`;
    const details = infos.map(item => {
      const currency = item.currency || '未知币种';
      return `${currency} 总余额 ${item.total_balance || '0'}，赠金 ${item.granted_balance || '0'}，充值 ${item.topped_up_balance || '0'}`;
    });
    return `账户状态：${available}。${details.join('；')}`;
  }

  function fillPrinterSelect(printers, selected) {
    const select = document.getElementById('target-printer');
    select.innerHTML = '<option value="">选择真实小票机</option>';
    printers.forEach(name => {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      option.selected = name === selected;
      select.appendChild(option);
    });
  }

  function currentPrinterOptions() {
    return Array.from(document.getElementById('target-printer').options)
      .map(option => option.value)
      .filter(Boolean);
  }

  function actionLabel(actionId) {
    const action = ReceiptShortcuts.actionList().find(item => item.id === actionId);
    return action ? action.settingLabel : actionId;
  }

  function typeScaleLabel(scale) {
    return { normal: '标准', large: '大', xlarge: '更大', xxlarge: '超大', max: '最大' }[scale] || scale;
  }

  function renderPrinterDiagnostics(info, lastJob) {
    const jobs = Array.isArray(info.jobs) ? info.jobs : [];
    const jobHtml = jobs.length
      ? jobs.map(job => `
          <li>
            #${escapeHtml(job.job_id || '')}
            ${escapeHtml(job.document || '未命名任务')}：
            ${escapeHtml(job.status_text || '未知状态')}
          </li>
        `).join('')
      : '<li>当前队列为空。</li>';
    const lastJobHtml = lastJob ? `
      <div class="diagnostic-box">
        <strong>最近测试</strong>
        <span>${escapeHtml(testTypeLabel(lastJob.test_type))}</span>
        <span>任务：${escapeHtml(lastJob.job_id || '未知')}</span>
        <span>${escapeHtml(lastJob.message || '')}</span>
      </div>
    ` : '';
    document.getElementById('printer-diagnostics-result').innerHTML = `
      <div class="diagnostic-grid">
        <div class="diagnostic-box"><strong>打印机</strong><span>${escapeHtml(info.name || '')}</span></div>
        <div class="diagnostic-box"><strong>驱动</strong><span>${escapeHtml(info.driver_name || '')}</span></div>
        <div class="diagnostic-box"><strong>端口</strong><span>${escapeHtml(info.port_name || '')}</span></div>
        <div class="diagnostic-box"><strong>状态</strong><span>${escapeHtml(info.status_text || '')}</span></div>
      </div>
      ${lastJobHtml}
      <div class="diagnostic-box">
        <strong>队列 ${escapeHtml(info.job_count || 0)} 个任务</strong>
        <ul>${jobHtml}</ul>
      </div>
      <p class="settings-hint">RAW 不出、普通测试出：多半是驱动不透传 ESC/POS。两个都不出：优先检查端口、离线、队列暂停或驱动。</p>
    `;
  }

  function renderPrinterMessage(message) {
    document.getElementById('printer-diagnostics-result').textContent = message;
  }

  function testTypeLabel(type) {
    return {
      raw_escpos: 'RAW ESC/POS 测试',
      windows_gdi: 'Windows 普通测试'
    }[type] || '测试';
  }

  function setFeedback(text) {
    document.getElementById('settings-feedback').textContent = text || '';
    onFeedback(text || '');
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  window.ReceiptSettingsPanel = { init, apply, open, close, changeTypeScale };
})();
