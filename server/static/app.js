// ── 全局面板控制（不在 IIFE 内，确保 onclick 能调用） ──
function closePanels() {
  document.getElementById('roles-panel').style.display = 'none';
  document.getElementById('settings-panel').style.display = 'none';
  document.getElementById('panel-overlay').style.display = 'none';
}
function openPanel(id) {
  document.getElementById(id).style.display = 'flex';
  document.getElementById('panel-overlay').style.display = 'block';
}
function confirmCollab() {
  var ws = window._nexusWs;
  var plan = window._nexusPendingPlan;
  if (!ws || !plan) return;
  // 从 DOM 读取编辑后的子任务（用户可能修改了描述或删除了子任务）
  var editedSubtasks = [];
  document.querySelectorAll('#collab-plan-card .subtask-edit').forEach(function(el) {
    var desc = el.textContent.trim();
    if (desc) {
      editedSubtasks.push({
        role: el.dataset.role,
        role_zh: el.dataset.roleZh,
        emoji: el.dataset.emoji,
        description: desc
      });
    }
  });
  if (editedSubtasks.length < 1) { return; }
  ws.send(JSON.stringify({ type: 'collab_execute', task: plan._task || '', subtasks: editedSubtasks }));
  document.querySelectorAll('.collab-confirm').forEach(function(b) { b.textContent = '⏳ 执行中...'; b.disabled = true; b.style.opacity = '0.6'; });
  document.querySelectorAll('.collab-cancel,.subtask-remove,.subtask-edit').forEach(function(b) { b.disabled = true; b.style.opacity = '0.4'; });
}
function cancelCollab() {
  window._nexusPendingPlan = null;
  var plans = document.querySelectorAll('.collab-plan');
  plans.forEach(function(p) { p.parentElement.remove(); });
}
function updateTokenEstimate() {
  var count = document.querySelectorAll('#collab-plan-card .subtask-edit').length;
  var el = document.getElementById('collab-token-est');
  if (el) el.textContent = '预估消耗: ~' + (count * 3000 + 3500).toLocaleString() + ' tokens';
}
document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closePanels(); });

(function() {
'use strict';

var messagesEl = document.getElementById('messages');
var welcomeEl = document.getElementById('welcome');
var inputEl = document.getElementById('input');
var sendBtn = document.getElementById('send-btn');
var roleBadge = document.getElementById('role-badge');
var versionBadge = document.getElementById('version-badge');
var statusDot = document.getElementById('status-dot');
var ws = null, isWaiting = false, hasMessages = false;

// 主题
var theme = localStorage.getItem('ox-theme') || 'dark';
document.documentElement.setAttribute('data-theme', theme);
document.querySelectorAll('.theme-toggle button').forEach(function(btn) {
  btn.classList.toggle('active', btn.dataset.theme === theme);
});
document.querySelector('.theme-toggle').addEventListener('click', function(e) {
  var btn = e.target.closest('button[data-theme]');
  if (!btn) return;
  var t = btn.dataset.theme;
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('ox-theme', t);
  document.querySelectorAll('.theme-toggle button').forEach(function(b) { b.classList.toggle('active', b.dataset.theme === t); });
});

// Markdown
marked.setOptions({ breaks: true, gfm: true });
function renderMd(text) { return DOMPurify.sanitize(marked.parse(text)); }

function addMessage(role, content, isHtml) {
  if (!hasMessages && role !== 'system') { hasMessages = true; if (welcomeEl) welcomeEl.style.display = 'none'; }
  var div = document.createElement('div');
  div.className = 'msg ' + role;
  if (isHtml) { div.innerHTML = content; } else { div.textContent = content; }
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function setWaiting(v) {
  isWaiting = v; sendBtn.disabled = v || !inputEl.value.trim(); inputEl.disabled = v;
  if (!v) { inputEl.focus(); sendBtn.disabled = !inputEl.value.trim(); }
}

// Token 预估：每个子任务约 3000 tokens，Manager + 风格统一约 3500 tokens
function estimateTokens(subtaskCount) {
  var total = subtaskCount * 3000 + 3500;
  return total.toLocaleString();
}

// WebSocket
function connectWS() {
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws/chat');
  ws.onopen = function() { statusDot.classList.add('connected'); sendSavedKeys(); };
  ws.onclose = function() { statusDot.classList.remove('connected'); setTimeout(connectWS, 3000); };
  ws.onerror = function() {};
  var thinkingEl = null;
  ws.onmessage = function(event) {
    var msg = JSON.parse(event.data);
    if (msg.type === 'role') { roleBadge.style.display = 'inline'; roleBadge.textContent = msg.emoji + ' ' + (msg.name_zh || msg.name); }
    else if (msg.type === 'thinking') { thinkingEl = addMessage('assistant', ''); thinkingEl.classList.add('thinking'); thinkingEl.textContent = '思考中'; }
    else if (msg.type === 'done') {
      if (thinkingEl) { thinkingEl.classList.remove('thinking'); thinkingEl.innerHTML = renderMd(msg.full_content); thinkingEl = null; }
      else { addMessage('assistant', renderMd(msg.full_content), true); }
      setWaiting(false);
    }
    else if (msg.type === 'error') { if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; } addMessage('system', '⚠️ ' + msg.message); setWaiting(false); }
    else if (msg.type === 'model_set') { addMessage('system', '🤖 模型已切换: ' + msg.provider + ' / ' + msg.model); }
    else if (msg.type === 'collab_status') { addMessage('system', '🤝 ' + msg.message); }
    else if (msg.type === 'collab_plan_ready') {
      pendingPlan = msg;
      pendingPlan._task = inputEl.dataset.lastTask || '';
      window._nexusPendingPlan = pendingPlan;
      window._nexusWs = ws;
      var planHtml = '<div class="collab-plan" id="collab-plan-card"><h4>🤝 协作方案: ' + msg.summary + '</h4>';
      msg.subtasks.forEach(function(st, i) {
        planHtml += '<div class="subtask" data-index="' + i + '">'
          + '<div class="subtask-left"><span class="subtask-status">' + st.emoji + '</span>'
          + '<span class="subtask-role">' + st.role_zh + '</span></div>'
          + '<div class="subtask-edit" contenteditable="true" data-role="' + st.role + '" data-role-zh="' + st.role_zh + '" data-emoji="' + st.emoji + '">' + st.description.replace(/</g, '&lt;') + '</div>'
          + '<button class="subtask-remove" onclick="this.parentElement.remove();updateTokenEstimate();" title="删除">✕</button>'
          + '</div>';
      });
      planHtml += '<div class="collab-token-estimate" id="collab-token-est">预估: ~' + estimateTokens(msg.subtasks.length) + ' tokens</div>';
      planHtml += '<div class="collab-actions">'
        + '<button class="collab-confirm" onclick="confirmCollab()">✅ 确认执行</button>'
        + '<button class="collab-cancel" onclick="cancelCollab()">取消</button>'
        + '</div>'
        + '</div>';
      addMessage('assistant', planHtml, true);
      setWaiting(false);
    }
    else if (msg.type === 'collab_progress') {
      var icon = msg.status === 'done' ? '✅' : msg.status === 'failed' ? '❌' : '⏳';
      addMessage('system', icon + ' ' + msg.emoji + ' ' + msg.role_zh + (msg.status === 'done' ? ' 完成' : msg.status === 'failed' ? ' 失败' : ' 执行中...'));
    }
    else if (msg.type === 'collab_done') {
      addMessage('assistant', renderMd(msg.full_content), true);
      addMessage('system', '🤝 协作完成，' + msg.subtask_count + ' 位专家参与');
      setWaiting(false);
    }
  };
}

var collabMode = false;
var pendingPlan = null;

function sendMessage(text) {
  text = text || inputEl.value.trim();
  if (!text || !ws || ws.readyState !== 1) return;
  addMessage('user', text);
  inputEl.dataset.lastTask = text;
  if (collabMode) {
    // 协作模式：先请求方案
    ws.send(JSON.stringify({ type: 'collab_plan', content: text }));
  } else {
    ws.send(JSON.stringify({ type: 'message', content: text }));
  }
  inputEl.value = ''; inputEl.style.height = 'auto'; setWaiting(true);
}

sendBtn.addEventListener('click', function() { sendMessage(); });
inputEl.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(); } });
inputEl.addEventListener('input', function() { inputEl.style.height = 'auto'; inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + 'px'; sendBtn.disabled = isWaiting || !inputEl.value.trim(); });

// 协作模式开关
document.getElementById('collab-btn').addEventListener('click', function() {
  collabMode = !collabMode;
  this.classList.toggle('collab-active', collabMode);
  if (collabMode) {
    addMessage('system', '🤝 协作模式已开启 — 输入任务后，多位专家将协同完成');
    inputEl.placeholder = '描述需要多专家协作的任务...';
  } else {
    addMessage('system', '💬 已切换回普通对话模式');
    inputEl.placeholder = '输入消息... (Enter 发送, Shift+Enter 换行)';
  }
});

// 页头按钮
document.getElementById('roles-btn').addEventListener('click', function() { openPanel('roles-panel'); loadRolesOnce(); });
document.getElementById('settings-btn').addEventListener('click', function() { loadSavedKeys(); loadModels(); openPanel('settings-panel'); });

// 角色加载
var rolesLoaded = false;
function loadRolesOnce() {
  if (rolesLoaded) return; rolesLoaded = true;
  var container = document.getElementById('role-groups');
  container.innerHTML = '<p style="color:var(--text-dim);text-align:center;padding:20px">加载中...</p>';
  fetch('/api/agency/roles').then(function(r) { return r.json(); }).then(function(data) {
    if (!data.available || !data.groups) { container.innerHTML = '<p style="color:var(--text-dim)">未找到角色</p>'; return; }
    container.innerHTML = '';
    data.groups.forEach(function(group, idx) {
      var g = document.createElement('div'); g.className = 'role-group';
      // 分组标题（可折叠）
      var header = document.createElement('button'); header.className = 'role-group-header';
      header.innerHTML = '<span>' + group.name + '</span><span class="role-count">' + group.count + '</span><span class="group-arrow">▾</span>';
      var grid = document.createElement('div'); grid.className = 'role-grid';
      // 默认只展开第一组
      if (idx > 0) { grid.style.display = 'none'; header.querySelector('.group-arrow').textContent = '▸'; }
      header.addEventListener('click', function() {
        var isOpen = grid.style.display !== 'none';
        grid.style.display = isOpen ? 'none' : 'flex';
        header.querySelector('.group-arrow').textContent = isOpen ? '▸' : '▾';
      });
      group.roles.forEach(function(role) {
        var card = document.createElement('button'); card.className = 'role-card';
        var zhName = role.name_zh || role.name;
        card.dataset.name = role.name;
        card.dataset.search = (role.name + ' ' + zhName + ' ' + role.description + ' ' + role.category).toLowerCase();
        card.innerHTML = '<span class="role-emoji">' + role.emoji + '</span>'
          + '<div class="role-info"><span class="role-name">' + zhName + '</span></div>';
        card.title = role.description;
        card.addEventListener('click', function() {
          if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'select_role', name: role.name }));
          // 更新角色徽章为中文名
          roleBadge.style.display = 'inline';
          roleBadge.textContent = role.emoji + ' ' + zhName;
          closePanels();
        });
        grid.appendChild(card);
      });
      g.appendChild(header); g.appendChild(grid); container.appendChild(g);
    });
  }).catch(function() { container.innerHTML = '<p style="color:var(--color-error)">加载失败</p>'; });
}

document.getElementById('auto-mode-btn').addEventListener('click', function() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'auto_mode' }));
  roleBadge.style.display = 'inline';
  roleBadge.textContent = '🤖 自动匹配';
  closePanels();
});
document.getElementById('role-search').addEventListener('input', function(e) {
  var q = e.target.value.toLowerCase();
  document.querySelectorAll('.role-card').forEach(function(c) { c.style.display = c.dataset.search.includes(q) ? '' : 'none'; });
  // 搜索时展开所有分组，隐藏空分组
  document.querySelectorAll('.role-group').forEach(function(g) {
    var visible = g.querySelectorAll('.role-card:not([style*="display: none"])').length;
    g.style.display = visible > 0 ? '' : 'none';
    if (q && visible > 0) { var grid = g.querySelector('.role-grid'); if (grid) grid.style.display = 'flex'; }
  });
});

// API Key
var KEY_STORAGE = 'nexus-agent-keys';
function loadSavedKeys() {
  var saved = JSON.parse(localStorage.getItem(KEY_STORAGE) || '{}');
  document.querySelectorAll('.key-field input').forEach(function(input) {
    var k = input.dataset.key;
    if (!k || !saved[k]) return;
    if (input.type === 'password') {
      var v = saved[k]; input.value = v.length > 8 ? v.slice(0,3) + '***' + v.slice(-3) : '***'; input.dataset.saved = '1';
    } else {
      input.value = saved[k];
    }
  });
}
function sendSavedKeys() {
  var saved = JSON.parse(localStorage.getItem(KEY_STORAGE) || '{}');
  if (Object.keys(saved).length > 0 && ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'set_keys', keys: saved }));
}
document.getElementById('save-keys-btn').addEventListener('click', function() {
  var keys = {}, toSend = {};
  document.querySelectorAll('.key-field input').forEach(function(input) {
    var k = input.dataset.key, v = input.value.trim();
    if (!k) return;
    // password 类型的 Key：处理遮蔽值
    if (input.type === 'password') {
      if (input.dataset.saved === '1' && v.includes('***')) { var s = JSON.parse(localStorage.getItem(KEY_STORAGE) || '{}'); v = s[k] || ''; }
      if (v && !v.includes('***')) { keys[k] = v; toSend[k] = v; }
    } else {
      // text 类型（Ollama 地址/模型名）：直接保存
      if (v) { keys[k] = v; toSend[k] = v; }
    }
  });
  localStorage.setItem(KEY_STORAGE, JSON.stringify(keys));
  if (ws && ws.readyState === 1 && Object.keys(toSend).length > 0) ws.send(JSON.stringify({ type: 'set_keys', keys: toSend }));
  addMessage('system', '✅ 配置已保存');
  // 保存后延迟刷新模型列表（等后端处理完 set_keys）
  setTimeout(loadModels, 500);
  closePanels();
});
document.getElementById('clear-keys-btn').addEventListener('click', function() {
  localStorage.removeItem(KEY_STORAGE);
  document.querySelectorAll('.key-field input').forEach(function(i) { i.value = ''; delete i.dataset.saved; });
  addMessage('system', '🗑️ 所有 API Key 已清除'); closePanels();
});

// Ollama 本地模型配置
document.getElementById('test-ollama-btn').addEventListener('click', function() {
  var url = (document.getElementById('ollama-url').value.trim() || 'http://localhost:11434').replace(/\/+$/, '');
  var btn = document.getElementById('test-ollama-btn');
  btn.textContent = '测试中...'; btn.disabled = true;
  fetch(url + '/api/tags').then(function(r) { return r.json(); }).then(function(data) {
    var models = (data.models || []).map(function(m) { return m.name; });
    if (models.length > 0) {
      addMessage('system', '✅ Ollama 连接成功，可用模型: ' + models.slice(0, 5).join(', '));
      // 自动填入第一个模型
      var modelInput = document.getElementById('ollama-model');
      if (!modelInput.value.trim()) modelInput.value = models[0];
    } else {
      addMessage('system', '⚠️ Ollama 已连接但未找到模型，请先运行 ollama pull <模型名>');
    }
    btn.textContent = '✅ 连接成功'; setTimeout(function() { btn.textContent = '测试连接'; btn.disabled = false; }, 2000);
  }).catch(function() {
    addMessage('system', '❌ 无法连接 Ollama，请确认已启动: ollama serve');
    btn.textContent = '❌ 连接失败'; setTimeout(function() { btn.textContent = '测试连接'; btn.disabled = false; }, 2000);
  });
});

// 模型选择
function loadModels() {
  fetch('/api/models').then(function(r) { return r.json(); }).then(function(data) {
    var select = document.getElementById('model-select');
    var current = select.value;
    select.innerHTML = '<option value="auto">🔄 自动路由（推荐）</option>';
    var statusParts = [];
    (data.providers || []).forEach(function(p) {
      var label = p.name;
      if (p.configured) label += ' ✓';
      var opt = document.createElement('option');
      opt.value = p.name;
      opt.textContent = label + '  (' + p.model + ')';
      opt.disabled = !p.configured;
      select.appendChild(opt);
      if (p.configured) statusParts.push(p.name);
    });
    select.value = current || 'auto';
    var statusEl = document.getElementById('model-status');
    if (statusParts.length > 0) {
      statusEl.textContent = '已配置: ' + statusParts.join(', ');
      statusEl.style.color = 'var(--color-success)';
    } else {
      statusEl.textContent = '未配置任何 API Key，请先在下方填入';
      statusEl.style.color = 'var(--color-warning)';
    }
  }).catch(function() {});
}

document.getElementById('model-select').addEventListener('change', function() {
  var provider = this.value;
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'set_model', provider: provider }));
  }
});

// 初始化
fetch('/api/health').then(function(r) { return r.json(); }).then(function(d) { versionBadge.textContent = 'v' + d.version; }).catch(function() { versionBadge.textContent = 'offline'; });
connectWS();
inputEl.focus();
setInterval(function() { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'ping' })); }, 30000);

})();
