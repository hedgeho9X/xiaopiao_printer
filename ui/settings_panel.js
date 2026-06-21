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
  }

  function bindSystem() {
    document.getElementById('target-printer').addEventListener('change', event => {
      savePatch({ target_printer_name: event.target.value }, '真实小票机已保存。');
    });
    document.getElementById('proxy-button').addEventListener('click', async () => {
      const result = await ReceiptBridge.call('create_proxy_printer');
      setFeedback(result.ok ? '代理打印机已创建或修复。' : result.error);
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
    document.getElementById('llm-enabled').checked = settings.llm_enabled === '1';
    document.getElementById('llm-provider-name').value = settings.llm_provider_name || 'OpenAI Compatible';
    document.getElementById('llm-base-url').value = settings.llm_base_url || 'https://api.openai.com/v1';
    document.getElementById('llm-api-key').value = settings.llm_api_key || '';
    document.getElementById('llm-model').value = settings.llm_model || '';
  }

  function collectLlmSettings() {
    return {
      llm_enabled: document.getElementById('llm-enabled').checked ? '1' : '0',
      llm_provider_name: document.getElementById('llm-provider-name').value.trim(),
      llm_base_url: document.getElementById('llm-base-url').value.trim(),
      llm_api_key: document.getElementById('llm-api-key').value.trim(),
      llm_model: document.getElementById('llm-model').value.trim()
    };
  }

  async function saveLlmSettings() {
    await savePatch(collectLlmSettings(), 'LLM 设置已保存。');
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
