/**
 * 快捷键注册与自定义配置。
 *
 * 本文件只负责把键盘事件标准化为动作名称，并校验用户自定义键位。
 * 业务执行由 app.js 负责，设置界面由 settings_panel.js 负责。
 */

(function () {
  const actions = {
    read_full: { label: '播放', settingLabel: '读全文' },
    read_overview: { label: '概览', settingLabel: '读概览' },
    read_items: { label: '商品', settingLabel: '读商品列表' },
    previous_item: { label: '上商品', settingLabel: '上一商品' },
    next_item: { label: '下商品', settingLabel: '下一商品' },
    previous_order: { label: '上一单', settingLabel: '上一单' },
    next_order: { label: '下一单', settingLabel: '下一单' },
    complete_order: { label: '完成', settingLabel: '完成' },
    stop_speaking: { label: '停止', settingLabel: '停止朗读' },
    cycle_rate: { label: '语速', settingLabel: '语速循环' },
    undo_complete: { label: '撤销', settingLabel: '撤销完成' },
    increase_type_scale: { label: '放大', settingLabel: '字体放大' },
    decrease_type_scale: { label: '缩小', settingLabel: '字体缩小' }
  };

  const defaultShortcuts = {
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
  };

  const modifierOnlyKeys = new Set(['Control', 'Ctrl', 'Shift', 'Alt', 'Meta', 'OS']);
  let shortcutMap = { ...defaultShortcuts };
  let actionHandler = null;

  function bindShortcuts(handler) {
    actionHandler = handler;
    window.addEventListener('keydown', event => {
      if (shouldIgnore(event)) return;
      const shortcut = normalizeKeyEvent(event);
      const action = actionForShortcut(shortcut);
      if (!action) return;
      event.preventDefault();
      actionHandler(action);
    });
  }

  function setShortcutMap(nextMap) {
    const merged = { ...defaultShortcuts };
    Object.keys(actions).forEach(action => {
      const shortcut = normalizeShortcut(nextMap && nextMap[action]);
      if (shortcut) merged[action] = shortcut;
    });
    validateShortcutMap(merged);
    shortcutMap = merged;
    return getShortcutMap();
  }

  function resetShortcutMap() {
    shortcutMap = { ...defaultShortcuts };
    return getShortcutMap();
  }

  function getShortcutMap() {
    return { ...shortcutMap };
  }

  function getDefaultShortcutMap() {
    return { ...defaultShortcuts };
  }

  function actionForShortcut(shortcut) {
    if (!shortcut) return '';
    const candidates = shortcutAliases(shortcut);
    return Object.keys(shortcutMap).find(action => candidates.includes(shortcutMap[action])) || '';
  }

  function shortcutAliases(shortcut) {
    const aliases = [shortcut];
    if (shortcut === 'Ctrl+Shift+Plus') aliases.push('Ctrl+Plus');
    if (shortcut === 'Ctrl+Shift+Minus') aliases.push('Ctrl+Minus');
    return aliases;
  }

  function validateShortcutMap(map) {
    const used = new Map();
    Object.entries(map).forEach(([action, shortcut]) => {
      if (!actions[action]) throw new Error(`未知行为：${action}`);
      if (!isAllowedShortcut(shortcut)) throw new Error(`快捷键不能为空：${actions[action].settingLabel}`);
      if (used.has(shortcut)) {
        throw new Error(`${formatShortcut(shortcut)} 已绑定到 ${actions[used.get(shortcut)].settingLabel}`);
      }
      used.set(shortcut, action);
    });
  }

  function isAllowedShortcut(shortcut) {
    return Boolean(String(shortcut || '').trim());
  }

  function normalizeKeyEvent(event) {
    const baseKey = normalizeBaseKey(event.key, event.code);
    if (!baseKey || modifierOnlyKeys.has(baseKey)) return '';
    const modifiers = [];
    if (event.ctrlKey) modifiers.push('Ctrl');
    if (event.altKey) modifiers.push('Alt');
    if (event.shiftKey) modifiers.push('Shift');
    if (event.metaKey) modifiers.push('Meta');
    return [...modifiers, baseKey].join('+');
  }

  function normalizeBaseKey(key, code) {
    const rawValue = String(key || '');
    if (rawValue === ' ') return 'Space';
    const value = rawValue.trim();
    const lowerValue = value.toLowerCase();
    if (!value) return '';
    if (code === 'NumpadAdd' || value === '+' || value === '=' || lowerValue === 'plus') return 'Plus';
    if (code === 'NumpadSubtract' || value === '-' || lowerValue === 'minus') return 'Minus';
    if (lowerValue === 'space') return 'Space';
    if (lowerValue === 'esc') return 'Escape';
    if (value.length === 1) return value.toUpperCase();
    if (/^f([1-9]|1[0-9]|2[0-4])$/i.test(value)) return value.toUpperCase();
    if (lowerValue === 'control') return 'Control';
    if (lowerValue === 'ctrl') return 'Ctrl';
    if (lowerValue === 'shift') return 'Shift';
    if (lowerValue === 'alt') return 'Alt';
    if (['meta', 'cmd', 'command', 'win', 'windows'].includes(lowerValue)) return 'Meta';
    return value;
  }

  function normalizeShortcut(value) {
    const shortcut = String(value || '').trim();
    if (!shortcut) return '';
    const legacyShortcut = normalizeLegacyShortcut(shortcut);
    if (legacyShortcut) return legacyShortcut;
    const tokens = shortcut.split('+').map(token => token.trim()).filter(Boolean);
    if (tokens.length === 0) return '';
    const modifiers = [];
    const baseTokens = [];
    tokens.forEach(token => {
      const modifier = normalizeModifierToken(token);
      if (modifier) modifiers.push(modifier);
      else baseTokens.push(token);
    });
    const baseKey = normalizeBaseKey(baseTokens.join('+') || tokens[tokens.length - 1]);
    if (!baseKey || modifierOnlyKeys.has(baseKey)) return '';
    const uniqueModifiers = [];
    modifiers.forEach(modifier => {
      if (!uniqueModifiers.includes(modifier)) uniqueModifiers.push(modifier);
    });
    return [...uniqueModifiers, baseKey].join('+');
  }

  function normalizeLegacyShortcut(shortcut) {
    if (/^(ctrl|control)\+z$/i.test(shortcut)) return 'Ctrl+Z';
    if (/^(ctrl|control)\+(\+|=|plus)$/i.test(shortcut)) return 'Ctrl+Plus';
    if (/^(ctrl|control)\+(-|minus)$/i.test(shortcut)) return 'Ctrl+Minus';
    return '';
  }

  function normalizeModifierToken(token) {
    const value = String(token || '').toLowerCase();
    if (['ctrl', 'control'].includes(value)) return 'Ctrl';
    if (value === 'alt') return 'Alt';
    if (value === 'shift') return 'Shift';
    if (['meta', 'cmd', 'command', 'win', 'windows'].includes(value)) return 'Meta';
    return '';
  }

  function shouldIgnore(event) {
    const target = event.target;
    const tag = target && target.tagName ? target.tagName.toLowerCase() : '';
    if (['input', 'textarea', 'select'].includes(tag)) return true;
    return Boolean(target && target.isContentEditable);
  }

  function formatShortcut(shortcut) {
    const labels = { Plus: '+', Minus: '-', Space: 'Space' };
    return String(shortcut || '').split('+').map(part => labels[part] || part).join(' + ');
  }

  function actionList() {
    return Object.entries(actions).map(([id, meta]) => ({ id, ...meta }));
  }

  window.ReceiptShortcuts = {
    bindShortcuts,
    setShortcutMap,
    resetShortcutMap,
    getShortcutMap,
    getDefaultShortcutMap,
    normalizeKeyEvent,
    normalizeShortcut,
    isAllowedShortcut,
    validateShortcutMap,
    formatShortcut,
    actionList
  };
})();
