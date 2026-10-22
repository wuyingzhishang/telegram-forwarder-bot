const state = { data: null, publishing: null, publishingBusy: false, targetDirty: false, loading: false, rules: [], manualRules: [], selectedManual: null, ruleKind: 'auto', manualBusy: false, filter: 'all', view: 'overview', toastTimer: null };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('visible');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}

function formatDate(dateString, options = {}) {
  if (!dateString) return '--';
  const date = new Date(dateString);
  if (Number.isNaN(date.getTime())) return '--';
  return new Intl.DateTimeFormat('zh-CN', options).format(date);
}

function formatHeartbeat(age) {
  if (age === null || age === undefined) return '从未';
  if (age < 5) return '刚刚';
  if (age < 60) return `${age} 秒前`;
  const minutes = Math.floor(age / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  return `${Math.floor(minutes / 60)} 小时前`;
}

function setStatus(online, age) {
  const text = online ? '机器人在线' : '机器人离线';
  $('#bot-state').textContent = text;
  $('#bot-detail').textContent = online ? '轮询服务正在运行' : '45 秒内没有收到运行心跳';
  $('#last-heartbeat').textContent = formatHeartbeat(age);
  $('#metric-status').textContent = online ? '运行中' : '离线';
  $('#metric-status-foot').textContent = online ? '心跳正常' : '等待恢复';
  $('#live-dot').className = `pulse-dot ${online ? 'online' : 'offline'}`;
  $('#sidebar-dot').className = `system-dot ${online ? 'online' : 'offline'}`;
  $('#metric-status-mark').className = `mini-mark ${online ? 'mark-green' : 'mark-red'}`;
  $('#sidebar-state').textContent = online ? '机器人在线' : '机器人离线';
}

function renderTargets(targets) {
  const compact = $('#overview-targets');
  const full = $('#full-targets');
  compact.replaceChildren();
  full.replaceChildren();
  $('#metric-targets').textContent = String(targets.length);
  $('#target-count-label').textContent = `${targets.length} 个目标`;
  $('#target-summary').textContent = targets.length ? `${targets.length} 个频道已配置` : '等待添加目标频道';
  $('#target-empty').hidden = targets.length > 0;
  $('#full-target-empty').hidden = targets.length > 0;
  for (const [index, target] of targets.entries()) {
    const symbol = String(index + 1).padStart(2, '0');
    const compactRow = element('div', 'target-row');
    compactRow.append(element('span', 'target-symbol', symbol));
    const compactInfo = element('div', 'target-info');
    compactInfo.append(element('strong', '', target.title), element('span', '', target.chat_id));
    compactRow.append(compactInfo, element('span', 'target-active', '已配置'));
    compact.append(compactRow);

    const card = element('article', 'target-card');
    card.append(element('span', 'target-symbol', symbol));
    const info = element('div', 'target-info');
    info.append(element('strong', '', target.title), element('span', '', target.chat_id));
    const remove = element('button', 'target-remove', '移除');
    remove.type = 'button';
    remove.dataset.removeTarget = target.chat_id;
    remove.setAttribute('aria-label', `移除目标 ${target.title}`);
    card.append(info, element('span', 'target-active', '已配置'), remove);
    full.append(card);
  }
}

const mediaLabels = { all: '全部类型', text: '文字', photo: '图片', video: '视频', document: '文件', audio: '音频', animation: '动画', voice: '语音', video_note: '视频消息', sticker: '贴纸' };
const captionLabels = { keep: '保留说明', remove: '移除说明', replace: '替换说明', prepend: '前置说明', append: '后置说明' };

function renderRules(rules) {
  state.rules = rules;
  const body = $('#rules-table');
  body.replaceChildren();
  $('#rules-empty').hidden = rules.length > 0;
  for (const rule of rules) {
    const row = document.createElement('tr');
    const nameCell = document.createElement('td');
    const name = element('div', 'rule-name');
    name.append(element('strong', '', rule.name || `规则 ${rule.id}`), element('span', '', `#${rule.id}`));
    nameCell.append(name);
    row.append(nameCell, element('td', 'rule-meta', rule.source_chat), element('td', '', rule.target_title || rule.target_chat));

    const summary = element('div', 'rule-config');
    const media = rule.media_types.map(type => mediaLabels[type] || type).join('、');
    const keywords = rule.filter_keywords.length ? `包含：${rule.filter_keywords.join('、')}` : '无包含词';
    const excluded = rule.exclude_keywords?.length ? `排除：${rule.exclude_keywords.join('、')}` : '无排除词';
    const album = rule.album_mode === 'merge' ? '合并相册' : '逐条发送';
    const sender = rule.sender_mode === 'forward' ? '保留来源' : '隐藏来源';
    const sourceMode = rule.source_mode === 'user' ? '用户账号读取' : 'Bot API 读取';
    const senderAccount = rule.sender_account === 'user' ? '个人账号发送' : 'Bot 账号发送';
    summary.append(element('span', '', media), element('span', '', sourceMode), element('span', '', senderAccount), element('span', '', keywords), element('span', '', excluded), element('span', '', `${captionLabels[rule.caption_mode] || '保留说明'} · ${album} · ${sender}`));
    const config = document.createElement('td');
    config.append(summary);
    row.append(config);

    const statusCell = document.createElement('td');
    statusCell.append(element('span', `rule-state ${rule.enabled ? 'enabled' : ''}`, rule.enabled ? '启用' : '停用'));
    row.append(statusCell);
    const actions = element('div', 'rule-actions');
    for (const [action, label, extraClass = ''] of [['edit', '编辑'], ['toggle', rule.enabled ? '停用' : '启用'], ['delete', '删除', 'danger']]) {
      const button = element('button', `row-action ${extraClass}`.trim(), label);
      button.type = 'button';
      button.dataset[`rule${action[0].toUpperCase()}${action.slice(1)}`] = String(rule.id);
      actions.append(button);
    }
    const actionCell = document.createElement('td');
    actionCell.append(actions);
    row.append(actionCell);
    body.append(row);
  }
}

async function apiRequest(path, options = {}) {
  const response = await fetch(new URL(path, location.origin), {
    cache: 'no-store',
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.method && options.method !== 'GET' ? { 'X-Relay-Request': 'dashboard' } : {}),
      ...options.headers,
    },
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.map(item => item.msg || '').join(' ') : String(body.detail || '');
    if (/numeric Telegram chat ID/i.test(detail)) throw new Error('来源请填写数字聊天 ID。');
    if (/public @username/i.test(detail)) throw new Error('目标请填写数字聊天 ID 或公开频道用户名。');
    if (/merged albums/i.test(detail)) throw new Error('合并相册时需要保留原说明文字。');
    if (/selected caption mode/i.test(detail)) throw new Error('请填写说明文字后再保存。');
    if (/loop/i.test(detail)) throw new Error('此规则会形成转发循环，请调整来源或目标。');
    if (/saved destination/i.test(detail)) throw new Error('请先保存目标频道，再创建规则。');
    if (/Remove or edit rules/i.test(detail)) throw new Error('请先修改或删除使用此目标的规则。');
    if (/already exists/i.test(detail)) throw new Error('此来源和目标的规则已存在。');
    if (/Invalid dashboard credentials/i.test(detail)) throw new Error('登录验证已失效，请重新登录仪表盘。');
    throw new Error(detail || '请求失败，请检查配置后重试。');
  }
  return body;
}

async function loadRules(quiet = false) {
  try {
    renderRules(await apiRequest('/api/rules'));
  } catch (error) {
    if (!quiet) showToast(error.message || '无法读取转发规则');
  }
}

function makeActivityRow(item) {
  const row = document.createElement('tr');
  const statusCell = document.createElement('td');
  statusCell.append(element('span', `status-pill ${item.status}`, item.status === 'success' ? '成功' : '失败'));
  row.append(statusCell);
  row.append(element('td', '', item.target_title));
  row.append(element('td', '', String(item.item_count)));
  row.append(element('td', '', formatDate(item.created_at, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })));
  row.append(element('td', '', `#${String(item.id).padStart(6, '0')}`));
  return row;
}

function renderActivity(items) {
  const overviewBody = $('#overview-activity');
  const fullBody = $('#activity-table');
  overviewBody.replaceChildren();
  fullBody.replaceChildren();
  $('#activity-empty').hidden = items.length > 0;
  const filtered = state.filter === 'all' ? items : items.filter(item => item.status === state.filter);
  $('#full-activity-empty').hidden = filtered.length > 0;
  for (const item of items.slice(0, 6)) overviewBody.append(makeActivityRow(item));
  for (const item of filtered) fullBody.append(makeActivityRow(item));
  $('#nav-activity-count').textContent = String(items.length);
}

function renderChart(daily) {
  const chart = $('#bar-chart');
  chart.replaceChildren();
  const max = Math.max(1, ...daily.map(day => Number(day.success) + Number(day.failed)));
  let total = 0;
  for (const day of daily) {
    const success = Number(day.success) || 0;
    const failed = Number(day.failed) || 0;
    total += success + failed;
    const column = element('div', 'chart-day');
    column.title = `${day.date} · 成功 ${success} · 失败 ${failed}`;
    const pair = element('div', 'bar-pair');
    for (const [kind, value] of [['success', success], ['failed', failed]]) {
      const bar = element('span', `bar ${kind}`);
      bar.style.height = `${Math.max(value ? 5 : 2, (value / max) * 100)}%`;
      bar.setAttribute('aria-hidden', 'true');
      pair.append(bar);
    }
    column.append(pair, element('span', 'chart-day-label', formatDate(`${day.date}T00:00:00`, { weekday: 'short' })));
    chart.append(column);
  }
  $('#week-total').textContent = total.toLocaleString('zh-CN');
}

function showView(name) {
  state.view = name;
  for (const view of $$('.view')) {
    const active = view.dataset.view === name;
    view.hidden = !active;
    view.classList.toggle('active', active);
  }
  for (const link of $$('[data-view-link]')) link.classList.toggle('active', link.dataset.viewLink === name && link.classList.contains('nav-item'));
  const titles = { overview: '开始转发', manual: '普通转发规则', activity: '活动记录', targets: '频道目标', rules: '自动监听规则' };
  $('#breadcrumb-title').textContent = titles[name];
  history.replaceState(null, '', `#${name}`);
  if (name === 'rules') loadRules(true);
  if (name === 'manual') loadManualRules();
}

function render(data) {
  state.data = data;
  setStatus(data.bot.online, data.bot.last_seen_seconds);
  $('#metric-success').textContent = Number(data.metrics.success).toLocaleString('zh-CN');
  $('#metric-failed').textContent = Number(data.metrics.failed).toLocaleString('zh-CN');
  renderTargets(data.targets);
  renderActivity(data.activity);
  renderChart(data.daily);
  const now = new Date(data.generated_at);
  const timeLabel = `更新于 ${formatDate(data.generated_at, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
  $('#updated-label').textContent = timeLabel;
  for (const selector of ['#heading-date', '#activity-date', '#target-date']) {
    $(selector).textContent = formatDate(now.toISOString(), { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  }
}

async function loadData() {
  if (state.loading) return;
  state.loading = true;
  const button = $('#refresh-button');
  button.classList.add('loading');
  try {
    const response = await fetch(new URL('/api/overview', location.origin), { cache: 'no-store', credentials: 'same-origin' });
    if (!response.ok) throw new Error(response.status === 401 ? '登录验证失败，请刷新页面重新登录。' : '无法读取仪表盘数据。');
    render(await response.json());
    feedback('#connection-error', '');
    await loadPublishing();
    await loadManualRules();
    await loadRules(true);
  } catch (error) {
    feedback('#connection-error', error.message + ' 请点击刷新重试；已显示的数据可能过期。');
    if (!state.data) {
      $('#bot-state').textContent = '数据暂不可用';
      $('#bot-detail').textContent = '请检查服务连接后重试';
      $('#sidebar-state').textContent = '连接中断';
      $('#sidebar-dot').className = 'system-dot offline';
    }
    showToast(error.message || '连接失败');
  } finally {
    state.loading = false;
    button.classList.remove('loading');
  }
}

function fillRuleForm(rule = null, kind = 'auto') {
  state.ruleKind = kind;
  feedback('#rule-feedback', '');
  $('.advanced-settings').open = Boolean(rule);
  const form = $('#rule-form');
  for (const name of ['source_chat', 'source_mode']) {
    form.elements[name].closest('label').hidden = kind === 'manual';
    form.elements[name].disabled = kind === 'manual';
  }
  form.elements.source_chat.required = kind !== 'manual';
  $('small', form.elements.sender_account.closest('label')).textContent = kind === 'manual'
    ? '普通规则使用此发布账号；个人账号须与提交消息的账号一致，并具有目标发布权限。'
    : '个人账号发送需与用户账号来源配合，并有目标发布权限。';
  const targets = state.data?.targets || [];
  const targetSelect = $('#rule-target');
  targetSelect.replaceChildren();
  if (!targets.length) {
    const option = new Option('请先添加目标频道', '');
    option.disabled = true;
    option.selected = true;
    targetSelect.append(option);
  } else {
    for (const target of targets) targetSelect.append(new Option(`${target.title} (${target.chat_id})`, target.chat_id));
  }
  form.elements.rule_id.value = rule ? String(rule.id) : '';
  form.elements.name.value = rule?.name || '';
  form.elements.source_chat.value = rule?.source_chat || '';
  const account = state.publishing?.sender_account || 'bot';
  form.elements.source_mode.value = rule?.source_mode || (account === 'user' ? 'user' : 'bot');
  form.elements.sender_account.value = rule?.sender_account || account;
  form.elements.target_chat.value = rule?.target_chat || state.publishing?.default_target || targets[0]?.chat_id || '';
  form.elements.enabled.checked = rule?.enabled ?? true;
  form.elements.filter_keywords.value = rule?.filter_keywords?.join('\n') || '';
  form.elements.exclude_keywords.value = rule?.exclude_keywords?.join('\n') || '';
  form.elements.filter_scope.value = rule?.filter_scope || 'message';
  form.elements.sender_mode.value = rule?.sender_mode || 'copy';
  form.elements.caption_mode.value = rule?.caption_mode || 'keep';
  form.elements.caption_text.value = rule?.caption_text || '';
  form.elements.album_mode.value = rule?.album_mode || 'individual';
  form.elements.remove_keywords.value = rule?.remove_keywords?.join('\n') || '';
  form.elements.replacements.value = rule?.replacements?.map(item => `${item.find} => ${item.replace}`).join('\n') || '';
  form.elements.prefix_text.value = rule?.prefix_text || '';
  form.elements.suffix_text.value = rule?.suffix_text || '';
  form.elements.remove_links.checked = rule?.remove_links ?? false;
  form.elements.button_text.value = rule?.button_text || '';
  form.elements.button_url.value = rule?.button_url || '';
  const selectedMedia = rule?.media_types || ['all'];
  for (const input of $$('input[name="media_types"]', form)) input.checked = selectedMedia.includes(input.value);
  $('#rule-dialog-title').textContent = (rule ? '编辑' : '新建') + (kind === 'manual' ? '普通转发规则' : '自动监听规则');
  updateRuleFormControls();
  updateSourceModeHelp();
  $('#rule-dialog').showModal();
}

function updateSourceModeHelp() {
  const form = $('#rule-form');
  const userMode = form.elements.source_mode.value === 'user';
  $('#source-mode-help').textContent = userMode
    ? '填写数字聊天 ID 或公开 @username；私密来源需登录账号已加入。'
    : '来源需填写数字 ID，Bot 必须能收到此频道或群组的新消息。';
  form.elements.source_chat.placeholder = userMode ? '-1001234567890 或 @channel' : '-1001234567890';
}

function updateRuleFormControls() {
  const form = $('#rule-form');
  const preserveSource = form.elements.sender_mode.value === 'forward';
  const userSender = form.elements.sender_account.value === 'user';
  if (preserveSource) form.elements.caption_mode.value = 'keep';
  form.elements.caption_mode.disabled = preserveSource;
  form.elements.caption_text.disabled = preserveSource || ['keep', 'remove'].includes(form.elements.caption_mode.value);
  for (const name of ['remove_keywords', 'replacements', 'prefix_text', 'suffix_text', 'remove_links', 'button_text', 'button_url']) {
    form.elements[name].disabled = preserveSource || (userSender && ['button_text', 'button_url'].includes(name));
  }
}

function splitList(value) {
  return value.split(/[\r\n,]/).map(item => item.trim()).filter(Boolean);
}

function rulePayload(form) {
  const preserveSource = form.elements.sender_mode.value === 'forward';
  const media_types = $$('input[name="media_types"]:checked', form).map(input => input.value);
  const replacements = preserveSource ? [] : splitList(form.elements.replacements.value).map(line => {
    const separator = line.indexOf('=>');
    if (separator < 1) throw new Error('替换内容请按“查找文字 => 替换文字”逐行填写。');
    return { find: line.slice(0, separator).trim(), replace: line.slice(separator + 2).trim() };
  });
  return {
    name: form.elements.name.value.trim(),
    source_chat: form.elements.source_chat.value.trim(),
    source_mode: form.elements.source_mode.value,
    sender_account: form.elements.sender_account.value,
    target_chat: form.elements.target_chat.value,
    enabled: form.elements.enabled.checked,
    media_types: media_types.length ? media_types : ['all'],
    filter_keywords: splitList(form.elements.filter_keywords.value),
    exclude_keywords: splitList(form.elements.exclude_keywords.value),
    filter_scope: form.elements.filter_scope.value,
    sender_mode: form.elements.sender_mode.value,
    caption_mode: preserveSource ? 'keep' : form.elements.caption_mode.value,
    caption_text: preserveSource ? '' : form.elements.caption_text.value,
    album_mode: form.elements.album_mode.value,
    remove_keywords: preserveSource ? [] : splitList(form.elements.remove_keywords.value),
    replacements,
    prefix_text: preserveSource ? '' : form.elements.prefix_text.value,
    suffix_text: preserveSource ? '' : form.elements.suffix_text.value,
    remove_links: preserveSource ? false : form.elements.remove_links.checked,
    button_text: preserveSource || form.elements.sender_account.value === 'user' ? '' : form.elements.button_text.value,
    button_url: preserveSource || form.elements.sender_account.value === 'user' ? '' : form.elements.button_url.value,
  };
}

document.addEventListener('click', async event => {
  const link = event.target.closest('[data-view-link]');
  if (link) showView(link.dataset.viewLink);
  if (event.target.closest('#refresh-button')) loadData();
  if (event.target.closest('#new-rule-button')) fillRuleForm();
  if (event.target.closest('#close-rule-dialog, #cancel-rule-dialog')) $('#rule-dialog').close();

  const editButton = event.target.closest('[data-rule-edit]');
  if (editButton) fillRuleForm(state.rules.find(rule => rule.id === Number(editButton.dataset.ruleEdit)));

  const toggleButton = event.target.closest('[data-rule-toggle]');
  if (toggleButton) {
    const rule = state.rules.find(item => item.id === Number(toggleButton.dataset.ruleToggle));
    if (rule) {
      try {
        await apiRequest(`/api/rules/${rule.id}`, { method: 'PUT', body: JSON.stringify({ ...rule, enabled: !rule.enabled }) });
        await loadRules(true);
        showToast(`规则已${rule.enabled ? '停用' : '启用'}`);
      } catch (error) { showToast(error.message); }
    }
  }

  const deleteButton = event.target.closest('[data-rule-delete]');
  if (deleteButton) {
    const rule = state.rules.find(item => item.id === Number(deleteButton.dataset.ruleDelete));
    if (rule && window.confirm(`删除规则“${rule.name}”？`)) {
      try {
        await apiRequest(`/api/rules/${rule.id}`, { method: 'DELETE' });
        await loadRules(true);
        showToast('规则已删除');
      } catch (error) { showToast(error.message); }
    }
  }

  const removeTarget = event.target.closest('[data-remove-target]');
  if (removeTarget && window.confirm(`移除目标“${removeTarget.getAttribute('aria-label').replace('移除目标 ', '')}”？`)) {
    try {
      await apiRequest(`/api/targets/${encodeURIComponent(removeTarget.dataset.removeTarget)}`, { method: 'DELETE' });
      await loadData();
      showToast('目标频道已移除');
    } catch (error) { showToast(error.message); }
  }

  const filter = event.target.closest('[data-filter]');
  if (filter) {
    state.filter = filter.dataset.filter;
    for (const option of $$('[data-filter]')) option.classList.toggle('active', option === filter);
    if (state.data) renderActivity(state.data.activity);
  }
});

$('#rule-form').addEventListener('change', event => {
  const form = event.currentTarget;
  if (event.target.name === 'media_types') {
    const all = $('input[name="media_types"][value="all"]', form);
    if (event.target.value === 'all' && event.target.checked) {
      for (const input of $$('input[name="media_types"]', form)) if (input !== all) input.checked = false;
    } else if (event.target.value !== 'all' && event.target.checked) all.checked = false;
    if (!$('input[name="media_types"]:checked', form)) all.checked = true;
  }
  if (event.target.name === 'caption_mode' || event.target.name === 'sender_mode' || event.target.name === 'sender_account') updateRuleFormControls();
  if (event.target.name === 'sender_account' && event.target.value === 'user') {
    $('#rule-form').elements.source_mode.value = 'user';
    updateSourceModeHelp();
  }
  if (event.target.name === 'source_mode') {
    const form = $('#rule-form');
    if (event.target.value === 'bot') form.elements.sender_account.value = 'bot';
    updateSourceModeHelp();
    updateRuleFormControls();
  }
});

$('#rule-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const ruleId = form.elements.rule_id.value;
  const submit = $('button[type="submit"]', form);
  if (submit.disabled) return;
  submit.disabled = true;
  feedback('#rule-feedback', '');
  try {
    const base = state.ruleKind === 'manual' ? '/api/manual-rules' : '/api/rules';
    const payload = rulePayload(form);
    if (state.ruleKind === 'manual') { delete payload.source_chat; delete payload.source_mode; }
    await apiRequest(ruleId ? `${base}/${ruleId}` : base, {
      method: ruleId ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    });
    $('#rule-dialog').close();
    await loadRules(true);
    await loadManualRules();
    showToast(ruleId ? '规则已更新' : '规则已创建');
  } catch (error) { feedback('#rule-feedback', error.message); }
  finally { submit.disabled = false; }
});

$('#target-form').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const submit = $('button[type="submit"]', form);
  if (submit.disabled) return;
  submit.disabled = true;
  feedback('#target-feedback', '');
  try {
    await apiRequest('/api/targets', {
      method: 'POST',
      body: JSON.stringify({ chat_id: form.elements.chat_id.value.trim(), title: form.elements.title.value.trim() }),
    });
    form.reset();
    await loadData();
    showToast('目标频道已保存');
  } catch (error) { feedback('#target-feedback', error.message); }
  finally { submit.disabled = false; }
});

function feedback(selector, message) {
  $(selector).textContent = message;
  $(selector).hidden = !message;
}

function renderPublishing(settings) {
  state.publishing = settings;
  const labels = { ready: '已连接，可用此身份发布', mismatch: '登录账号与当前管理员不一致', offline: '个人账号离线，请先连接', disabled: '尚未启用个人账号，请先完成登录配置' };
  $('#personal-status').textContent = labels[settings.personal_status];
  for (const button of $$('[data-account]')) {
    const selected = button.dataset.account === settings.sender_account;
    button.setAttribute('aria-pressed', String(selected));
    button.disabled = state.publishingBusy || (button.dataset.account === 'user' && !settings.personal_available);
  }
  $('#clear-account').disabled = state.publishingBusy || !settings.expires_at;
  $('#account-expiry').textContent = settings.sender_account
    ? '已设为默认 · 有效至 ' + formatDate(new Date(settings.expires_at * 1000).toISOString(), {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}) + '。再次点击账号可续期30天。'
    : settings.expired ? '默认账号已到期，请重新选择。' : '点击账号即保存为默认，有效30天；随时可以更换或清除。';
  $('#settings-owner').textContent = '设置对应 Telegram 管理员 ' + settings.owner_id + '，仅同步此账号的机器人菜单。';
  const select = $('#publishing-target');
  const selected = state.targetDirty ? select.value : settings.default_target || '';
  select.replaceChildren(new Option('每次选择目标', ''));
  for (const target of state.data?.targets || []) select.append(new Option(target.title + ' (' + target.chat_id + ')', target.chat_id));
  select.value = [...select.options].some(option => option.value === selected) ? selected : '';
  select.disabled = state.publishingBusy;
  $('#publishing-target-form button').disabled = state.publishingBusy;
  $('#publish-ready').textContent = settings.sender_account === 'user' && !settings.personal_available
    ? '默认个人账号当前不可用。请恢复连接或主动切换为机器人账号。'
    : settings.sender_account ? '默认账号已就绪。新消息将使用此身份发布；已有自动规则保持各自设置。' : '先选一个发布账号；机器人也会在开始时提示选择。';
}

async function loadPublishing() {
  if (state.publishingBusy) return;
  try {
    const settings = await apiRequest('/api/publishing');
    if (!state.publishingBusy) renderPublishing(settings);
    feedback('#publishing-error', '');
  } catch (error) {
    feedback('#publishing-error', '发布设置读取失败：' + error.message + ' 点击顶部刷新重试。');
  }
}

async function savePublishing(path, payload) {
  if (state.publishingBusy) return;
  state.publishingBusy = true;
  if (state.publishing) renderPublishing(state.publishing);
  try {
    const settings = await apiRequest(path, { method: 'PUT', body: JSON.stringify(payload) });
    if (path.endsWith('/target')) state.targetDirty = false;
    renderPublishing(settings);
    feedback('#publishing-error', '');
    showToast('发布设置已保存，并同步到机器人');
  } catch (error) { feedback('#publishing-error', error.message); }
  finally {
    state.publishingBusy = false;
    if (state.publishing) renderPublishing(state.publishing);
  }
}

for (const button of $$('[data-account]')) button.addEventListener('click', () => savePublishing('/api/publishing/account', {sender_account: button.dataset.account}));
$('#clear-account').addEventListener('click', () => savePublishing('/api/publishing/account', {sender_account: null}));
$('#publishing-target').addEventListener('change', () => { state.targetDirty = true; });
$('#publishing-target-form').addEventListener('submit', event => {
  event.preventDefault();
  savePublishing('/api/publishing/target', {target_chat: $('#publishing-target').value || null});
});

async function loadManualRules() {
  try {
    const data = await apiRequest('/api/manual-rules');
    state.manualRules = data.rules;
    state.selectedManual = data.selected_id;
    const current = data.rules.find(rule => rule.id === data.selected_id);
    const label = data.selected_id === null ? '快捷转发（不套用规则）' : current?.enabled ? `普通规则：${current.name}` : '当前规则已停用或删除，请重新选择';
    $('#manual-current').textContent = label;
    $('#manual-mode-title').textContent = label;
    $('#manual-mode-detail').textContent = data.selected_id === null ? '下方账号与默认目标用于快捷转发。需要筛选或改写消息时，创建并选择普通转发规则。' : '当前使用规则中的发布账号、目标和处理设置。下方30天默认账号仅用于快捷转发，不会覆盖规则。停用或删除当前规则会停止普通转发，需重新选择模式。' ;
    const list = $('#manual-list');
    list.replaceChildren();
    $('#manual-empty').hidden = data.rules.length > 0;
    const targets = Object.fromEntries((state.data?.targets || []).map(t => [t.chat_id, t.title]));
    for (const rule of data.rules) {
      const card = element('article', 'panel manual-card');
      card.append(element('h2', '', rule.name));
      card.append(element('span', 'rule-state ' + (rule.enabled ? 'enabled' : ''), rule.id === data.selected_id ? (rule.enabled ? '当前使用' : '当前已停用') : rule.enabled ? '可使用' : '已停用'));
      card.append(element('p', '', `${rule.sender_account === 'user' ? '个人账号' : '机器人账号'} → ${targets[rule.target_chat] || rule.target_chat}`));
      const summary = [rule.media_types.map(t => mediaLabels[t]).join('、'), rule.album_mode === 'merge' ? '合并相册' : '逐条发送'];
      if (rule.filter_keywords.length) summary.push('包含：' + rule.filter_keywords.join('、'));
      if (rule.exclude_keywords.length) summary.push('排除：' + rule.exclude_keywords.join('、'));
      if (rule.remove_links) summary.push('移除链接');
      if (rule.remove_keywords.length || rule.replacements.length || rule.prefix_text || rule.suffix_text || rule.caption_mode !== 'keep') summary.push('已配置内容处理');
      card.append(element('p', 'setting-detail', summary.join(' · ')));
      const actions = element('div', 'rule-actions');
      for (const [action, label] of [['select', '使用此规则'], ['edit', '编辑'], ['toggle', rule.enabled ? '停用' : '启用'], ['delete', '删除']]) {
        const button = element('button', 'row-action', label);
        button.type = 'button'; button.dataset.manualAction = action; button.dataset.id = rule.id;
        button.disabled = action === 'select' && (!rule.enabled || rule.id === data.selected_id);
        actions.append(button);
      }
      card.append(actions); list.append(card);
    }
    feedback('#manual-error', '');
  } catch (error) {
    feedback('#manual-error', error.message);
    $('#manual-mode-title').textContent = '普通转发规则读取失败，请刷新重试';
  }
}

document.addEventListener('click', async event => {
  if (event.target.closest('#new-manual-button')) fillRuleForm(null, 'manual');
  const button = event.target.closest('[data-manual-action], #manual-clear');
  if (!button || state.manualBusy) return;
  const rule = state.manualRules.find(item => item.id === Number(button.dataset.id));
  const action = button.dataset.manualAction;
  if (action === 'edit' && rule) { fillRuleForm(rule, 'manual'); return; }
  if (action === 'delete' && !window.confirm(`删除普通规则“${rule.name}”？当前选中的规则删除后会停止处理，需重新选择。`)) return;
  state.manualBusy = true;
  button.disabled = true;
  try {
    if (button.id === 'manual-clear' || action === 'select') {
      await apiRequest('/api/manual-selection', {method: 'PUT', body: JSON.stringify({rule_id: rule?.id || null})});
    } else if (action === 'toggle') {
      await apiRequest(`/api/manual-rules/${rule.id}`, {method: 'PUT', body: JSON.stringify({...rule, enabled: !rule.enabled})});
    } else if (action === 'delete') {
      await apiRequest(`/api/manual-rules/${rule.id}`, {method: 'DELETE'});
    }
    await loadManualRules();
    showToast('普通转发设置已同步到机器人');
  } catch (error) { feedback('#manual-error', error.message); }
  finally { state.manualBusy = false; button.disabled = false; }
});

const initialView = location.hash.slice(1);
showView(['overview', 'manual', 'activity', 'targets', 'rules'].includes(initialView) ? initialView : 'overview');
loadData();
setInterval(loadData, 15000);
