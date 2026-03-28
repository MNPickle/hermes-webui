/* Hermes Admin Panel - Main Application JavaScript */

const API = { base: '' };

// Token management
function getToken() {
    return localStorage.getItem('hermes_webui_token');
}

function setToken(token) {
    localStorage.setItem('hermes_webui_token', token);
}

function promptForToken() {
    const token = prompt('Enter your HERMES_WEBUI_TOKEN to access the admin panel:\n\n(Set this with: export HERMES_WEBUI_TOKEN=your-token-here)');
    if (token) {
        setToken(token);
        location.reload();
    }
}

async function api(method, path, body) {
    const token = getToken();
    const opts = { 
        method, 
        headers: { 
            'Content-Type': 'application/json',
            ...(token && { 'Authorization': 'Bearer ' + token })
        } 
    };
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    const resp = await fetch(API.base + path, opts);
    const data = await resp.json();
    if (!resp.ok) {
        // If 401 and no token or invalid token, prompt for it
        if (resp.status === 401) {
            promptForToken();
            throw new Error('Authentication required');
        }
        throw new Error(data.error || data.message || 'Request failed');
    }
    return data;
}

function toast(msg, type = 'info', dur = 4000) {
    const c = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = msg;
    c.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateX(100%)'; setTimeout(() => el.remove(), 300); }, dur);
}

function showModal(title, bodyHtml, footerHtml = '') {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-footer').innerHTML = footerHtml;
    document.getElementById('modal-overlay').classList.remove('hidden');
    document.getElementById('modal-overlay').classList.add('active');
}
function closeModal() {
    document.getElementById('modal-overlay').classList.remove('active');
    document.getElementById('modal-overlay').classList.add('hidden');
}

function escH(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }
function escA(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function fmtBytes(b) { if (b == null || b === '?') return '?'; b = Number(b); if (b >= 1e12) return (b/1e12).toFixed(1)+' TB'; if (b >= 1e9) return (b/1e9).toFixed(1)+' GB'; if (b >= 1e6) return (b/1e6).toFixed(1)+' MB'; return b+' B'; }
function fmtUptime(s) { if (!s || s === '?') return '?'; s = Number(s); const d = Math.floor(s/86400); const h = Math.floor((s%86400)/3600); const m = Math.floor((s%3600)/60); return (d ? d+'d ':'')+(h ? h+'h ':'')+(m ? m+'m':''); }
function fmtVal(v) {
    if (v === true) return '<span class="badge badge-success">Enabled</span>';
    if (v === false) return '<span class="badge badge-danger">Disabled</span>';
    if (v === null || v === undefined) return '<span class="text-muted">null</span>';
    if (typeof v === 'object') return '<pre class="font-mono text-xs" style="max-height:200px;overflow:auto">' + escH(JSON.stringify(v, null, 2)) + '</pre>';
    return escH(String(v));
}

function toggleH(name, checked, id) {
    return '<label class="toggle-switch"><input type="checkbox" id="' + (id || name) + '" ' + (checked ? 'checked' : '') + '><span class="toggle-slider"></span></label>';
}
function inputH(name, value, type = 'text', ph = '', extra = '') {
    return '<input type="' + type + '" class="form-input" id="' + name + '" value="' + escA(value || '') + '" placeholder="' + escA(ph) + '" ' + extra + '>';
}
function selectH(name, options, selected, id) {
    const opts = options.map(o => {
        const val = typeof o === 'object' ? o.value : o;
        const label = typeof o === 'object' ? o.label : o;
        return '<option value="' + escA(val) + '" ' + (String(val) === String(selected) ? 'selected' : '') + '>' + escH(label) + '</option>';
    }).join('');
    return '<select class="form-select" id="' + (id || name) + '">' + opts + '</select>';
}
function textareaH(name, value, rows = 4, mono = false) {
    return '<textarea class="form-textarea' + (mono ? ' mono' : '') + '" id="' + name + '" rows="' + rows + '">' + escH(value || '') + '</textarea>';
}

/* ═══════════════════════════════════════════════════════════════
   THEME MANAGER
   ═══════════════════════════════════════════════════════════════ */

const ThemeManager = {
    current: 'dark',

    init() {
        const saved = localStorage.getItem('hermes-theme') || 'dark';
        this.set(saved);
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            if (this.current === 'system') this.apply();
        });
    },

    set(theme) {
        this.current = theme;
        localStorage.setItem('hermes-theme', theme);
        this.apply();
        this.updateIcon();
    },

    apply() {
        document.body.setAttribute('data-theme', this.current);
    },

    cycle() {
        const themes = ['dark', 'light', 'system'];
        const next = themes[(themes.indexOf(this.current) + 1) % themes.length];
        this.set(next);
    },

    updateIcon() {
        const d = document.getElementById('theme-icon-dark');
        const l = document.getElementById('theme-icon-light');
        const s = document.getElementById('theme-icon-system');
        if (d) d.style.display = this.current === 'dark' ? '' : 'none';
        if (l) l.style.display = this.current === 'light' ? '' : 'none';
        if (s) s.style.display = this.current === 'system' ? '' : 'none';
    },

    getLabel() {
        return { dark: 'Dark', light: 'Light', system: 'System' }[this.current] || 'Dark';
    }
};

/* ═══════════════════════════════════════════════════════════════
   NAVIGATION
   ═══════════════════════════════════════════════════════════════ */

function screenTitle(s) {
    return {
        dashboard: 'Dashboard', settings: 'Settings', 'env-vars': 'Environment Variables',
        service: 'Service Controls', providers: 'Providers', models: 'Models',
        agents: 'Agents', skills: 'Skills', channels: 'Channels',
        hooks: 'Hooks / Webhooks', sessions: 'Sessions', logs: 'Logs & Diagnostics', chat: 'Chat'
    }[s] || s;
}

function navigate(screen) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector('[data-screen="' + screen + '"]');
    if (navItem) navItem.classList.add('active');
    const content = document.getElementById('content');
    content.style.padding = screen === 'chat' ? '0' : '';
    content.style.overflow = screen === 'chat' ? 'hidden' : '';
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    document.getElementById('sidebar').classList.remove('mobile-open');
    // Show/hide topbar: hide when chat is active
    document.getElementById('topbar').classList.toggle('hidden-by-chat', screen === 'chat');
    if (Screens[screen]) Screens[screen]();
    else content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u2753</div><h3>Screen not found</h3></div>';
}

let _healthCache = null, _healthTs = 0;
async function checkHealth() {
    const now = Date.now();
    if (_healthCache && (now - _healthTs) < 5000) return;
    try {
        const d = await api('GET', '/api/health');
        const dot = document.querySelector('#connection-status .status-dot');
        const txt = document.querySelector('#connection-status .status-text');
        dot.className = 'status-dot ' + (d.gateway_running ? 'online' : 'offline');
        txt.textContent = d.gateway_running ? 'Gateway Running' : 'Gateway Stopped';
        const ver = document.getElementById('sidebar-version');
        if (ver && d.version) {
            const firstLine = d.version.split('\n')[0].replace('Hermes Agent ', '');
            ver.textContent = 'v' + firstLine;
        }
    } catch (e) {
        document.querySelector('#connection-status .status-dot').className = 'status-dot error';
        document.querySelector('#connection-status .status-text').textContent = 'Error';
    }
    _healthCache = true; _healthTs = now;
}

/* ═══════════════════════════════════════════════════════════════
   SCREENS
   ═══════════════════════════════════════════════════════════════ */

const Screens = {};

// ── DASHBOARD ──────────────────────────────────────────────
Screens.dashboard = async function () {
    const content = document.getElementById('content');
    try {
        const [health, sys, tools] = await Promise.all([
            api('GET', '/api/health').catch(() => ({ gateway_running: false, version: '?' })),
            api('GET', '/api/system').catch(() => ({ python_version: '?', os_info: '?', disk_free: '?' })),
            api('GET', '/api/tools').catch(() => ({ tools: [], total_enabled: 0, total_disabled: 0 }))
        ]);
        content.innerHTML = `
        <div class="stats-grid">
            <div class="stat-card ${health.gateway_running ? 'green' : 'red'}">
                <div class="stat-value">${health.gateway_running ? '\u25cf Running' : '\u25cb Stopped'}</div>
                <div class="stat-label">Gateway Status</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">${escH((health.version || '?').split('\n')[0])}</div>
                <div class="stat-label">Hermes Version</div>
            </div>
            <div class="stat-card green">
                <div class="stat-value">${tools.total_enabled || 0} / ${(tools.total_enabled || 0) + (tools.total_disabled || 0)}</div>
                <div class="stat-label">Tools Enabled</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">${escH(sys.python_version || '?')}</div>
                <div class="stat-label">Python</div>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>Quick Actions</span></div>
            <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
                <button class="btn btn-success" onclick="serviceAction('start')">\u25b6 Start Gateway</button>
                <button class="btn btn-danger" onclick="serviceAction('stop')">\u25a0 Stop Gateway</button>
                <button class="btn btn-primary" onclick="serviceAction('restart')">\u21bb Restart Gateway</button>
<button class="btn" onclick="serviceAction('doctor')">Run Diagnostics</button>
                <button class="btn" onclick="reloadConfig()">\u21bb Reload Config</button>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>System Info</span></div>
            <div class="card-body">
                <div class="form-row">
                    <div class="form-group"><label class="form-label">OS</label><div class="text-sm">${escH(sys.os_info || '?')}</div></div>
                    <div class="form-group"><label class="form-label">Disk Free</label><div class="text-sm">${fmtBytes(sys.disk_free)}</div></div>
                    <div class="form-group"><label class="form-label">Hermes Home</label><div class="font-mono text-sm">${escH(health.hermes_home || '?')}</div></div>
                    <div class="form-group"><label class="form-label">Gateway PID</label><div class="text-sm">${health.gateway_pid || 'N/A'}</div></div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>Enabled Tools</span><span class="badge badge-success">${tools.total_enabled || 0} enabled</span></div>
            <div class="table-container">
                <table class="table">
                    <thead><tr><th>Name</th><th>Status</th><th>Description</th></tr></thead>
                    <tbody>${(tools.tools || []).map(t => '<tr><td class="font-mono text-sm">' + escH(t.name) + '</td><td>' + (t.status === 'enabled' ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-danger">Disabled</span>') + '</td><td class="text-sm">' + escH(t.description || '') + '</td></tr>').join('')}</tbody>
                </table>
            </div>
        </div>`;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error loading dashboard</h3><p>' + escH(e.message) + '</p></div>';
    }
};

async function serviceAction(action) {
    toast('Running ' + action + '...', 'info', 2000);
    try {
        const r = await api('POST', '/api/service/' + action);
        toast(action.charAt(0).toUpperCase() + action.slice(1) + ': ' + (r.ok ? 'Success' : 'Failed'), r.ok ? 'success' : 'error');
        // Invalidate health cache so next check gets fresh gateway status
        _healthCache = null;
        checkHealth();
        if (action === 'start' || action === 'restart') {
            setTimeout(() => { _healthCache = null; checkHealth(); }, 3000);
        }
        if (action === 'doctor' && r.output) {
            showModal('Diagnostics Output', '<pre class="font-mono text-sm" style="max-height:400px;overflow:auto;white-space:pre-wrap">' + escH(r.output) + '</pre>');
        }
    } catch (e) { toast('Error: ' + e.message, 'error'); }
}

async function reloadConfig() {
    try { await api('POST', '/api/config/reload'); toast('Config reloaded', 'success'); navigate(document.querySelector('.nav-item.active')?.dataset.screen || 'dashboard'); }
    catch (e) { toast('Reload failed: ' + e.message, 'error'); }
}

// ── SETTINGS ───────────────────────────────────────────────
Screens.settings = async function () {
    const content = document.getElementById('content');
    try {
        const cfg = await api('GET', '/api/config');
        const personalities = Object.keys(cfg.personalities || {});
        const tabs = [
            { id: 'general', label: 'General', sections: ['display'] },
            { id: 'agent', label: 'Agent', sections: ['agent'] },
            { id: 'terminal', label: 'Terminal', sections: ['terminal'] },
            { id: 'browser', label: 'Browser', sections: ['browser'] },
            { id: 'memory', label: 'Memory', sections: ['memory'] },
            { id: 'security', label: 'Security', sections: ['privacy', 'security'] },
            { id: 'compression', label: 'Compression', sections: ['compression', 'checkpoints'] },
            { id: 'routing', label: 'Smart Routing', sections: ['smart_model_routing'] },
            { id: 'voice', label: 'Voice / TTS', sections: ['tts', 'stt', 'voice'] },
            { id: 'misc', label: 'Misc', sections: ['human_delay', 'approvals', 'code_execution', 'skills', 'streaming', 'delegation'] }
        ];

            let tabsHtml = '<div class="tabs">' + tabs.map((t, i) => '<button class="tab' + (i === 0 ? ' active' : '') + '" data-tab="' + t.id + '">' + t.label + '</button>').join('') + '</div>';
        let panelsHtml = tabs.map((t, i) => {
            let formHtml = '';
            t.sections.forEach(sec => {
                const data = cfg[sec];
                if (!data || typeof data !== 'object') return;
                formHtml += '<div class="form-section"><div class="form-section-title">' + escH(sec) + '</div>';
                // Theme chooser in General > display
                if (sec === 'display') {
                    formHtml += '<div class="form-row"><div class="form-group"><label class="form-label">Theme</label>';
                    formHtml += '<div class="theme-chooser">';
                    ['dark', 'light', 'system'].forEach(th => {
                        formHtml += '<button class="theme-btn' + (ThemeManager.current === th ? ' active' : '') + '" data-theme="' + th + '" onclick="ThemeManager.set(\'' + th + '\')">' + th.charAt(0).toUpperCase() + th.slice(1) + '</button>';
                    });
                    formHtml += '</div></div></div>';
                }
                for (const [key, val] of Object.entries(data)) {
                    if (key.startsWith('_') || key === 'edge' || key === 'elevenlabs' || key === 'openai' || key === 'neutts' || key === 'local') continue;
                    formHtml += '<div class="form-row"><div class="form-group"><label class="form-label">' + escH(key) + '</label>';
                    if (typeof val === 'boolean') formHtml += toggleH(sec + '.' + key, val);
                    else if (typeof val === 'number') formHtml += inputH(sec + '.' + key, val, 'number');
                    else if (key === 'personality') formHtml += selectH(sec + '.' + key, personalities.map(p => ({ value: p, label: p })), val);
                    else if (key === 'reasoning_effort' || key === 'mode' || key === 'backend') formHtml += selectH(sec + '.' + key, key === 'reasoning_effort' ? ['low', 'medium', 'high'] : key === 'mode' ? ['off', 'random', 'fixed'] : ['local', 'docker', 'ssh', 'modal', 'singularity', 'daytona', 'docker-local'], val);
                    else if (key === 'tool_progress') formHtml += selectH(sec + '.' + key, ['all', 'summary', 'none'], val);
                    else if (typeof val === 'object') formHtml += textareaH(sec + '.' + key, JSON.stringify(val, null, 2), 3, true);
                    else formHtml += inputH(sec + '.' + key, val);
                    formHtml += '</div></div>';
                }
                formHtml += '</div>';
            });
            return '<div class="tab-pane' + (i === 0 ? ' active' : '') + '" data-tab="' + t.id + '">' + formHtml + '<button class="btn btn-primary mt-16" onclick="saveSettings()">Save Settings</button></div>';
        }).join('');

        content.innerHTML = tabsHtml + panelsHtml;

        content.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                content.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                content.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                content.querySelector('[data-tab="' + tab.dataset.tab + '"].tab-pane')?.classList.add('active');
            });
        });
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.saveSettings = async function () {
    const activePane = document.querySelector('.tab-pane.active');
    if (!activePane) return;
    const updates = {};
    activePane.querySelectorAll('.form-group').forEach(g => {
        const label = g.querySelector('.form-label');
        const input = g.querySelector('input, select, textarea');
        if (!label || !input) return;
        const fullKey = input.id;
        const [sec, key] = fullKey.split('.');
        if (!sec || !key) return;
        if (!updates[sec]) updates[sec] = {};
        if (input.type === 'checkbox') updates[sec][key] = input.checked;
        else if (input.type === 'number') updates[sec][key] = parseFloat(input.value);
        else if (input.tagName === 'TEXTAREA' && input.classList.contains('mono')) {
            try { updates[sec][key] = JSON.parse(input.value); } catch { updates[sec][key] = input.value; }
        } else updates[sec][key] = input.value;
    });
    try {
        for (const [sec, data] of Object.entries(updates)) { await api('PUT', '/api/config/' + sec, data); }
        toast('Settings saved', 'success');
    } catch (e) { toast('Save failed: ' + e.message, 'error'); }
};

// ── ENV VARS ───────────────────────────────────────────────
Screens['env-vars'] = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/env');
        const vars = data.vars || {};
        const groups = data.groups || {};
        const groupNames = ['Provider', 'Channel', 'System'];

        let html = '<div class="section-header"><span></span><button class="btn btn-primary" onclick="addEnvVar()">+ Add Variable</button></div>';
        html += '<div class="tabs">';
        groupNames.forEach((g, i) => { html += '<button class="tab' + (i === 0 ? ' active' : '') + '" data-group="' + g + '">' + g + '</button>'; });
        html += '</div>';

        groupNames.forEach((g, i) => {
            const keys = (groups[g] || []).filter(k => vars.hasOwnProperty(k));
            html += '<div class="tab-pane' + (i === 0 ? ' active' : '') + '" data-group="' + g + '">';
            if (keys.length === 0) {
                html += '<div class="empty-state"><p>No variables in this group</p></div>';
            } else {
                html += '<div class="table-container"><table class="table"><thead><tr><th>Key</th><th>Value</th><th style="width:120px">Actions</th></tr></thead><tbody>';
                keys.forEach(k => {
                    html += '<tr><td class="font-mono text-sm">' + escH(k) + '</td><td class="font-mono text-sm text-muted">' + escH(vars[k]) + '</td><td class="actions"><button class="btn btn-sm" onclick="editEnvVar(\'' + escA(k) + '\')">Edit</button> <button class="btn btn-sm btn-danger" onclick="deleteEnvVar(\'' + escA(k) + '\')">Delete</button></td></tr>';
                });
                html += '</tbody></table></div>';
            }
            html += '</div>';
        });
        content.innerHTML = html;

        content.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                content.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                content.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
                tab.classList.add('active');
                content.querySelector('[data-group="' + tab.dataset.group + '"].tab-pane')?.classList.add('active');
            });
        });
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.addEnvVar = function () {
    showModal('Add Environment Variable',
        '<div class="form-group"><label class="form-label">Key</label>' + inputH('env-key', '', 'text', 'e.g. MY_API_KEY') + '</div>' +
        '<div class="form-group"><label class="form-label">Value</label>' + inputH('env-value', '', 'text', 'Secret value') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="saveNewEnvVar()">Save</button>'
    );
};
window.saveNewEnvVar = async function () {
    const key = document.getElementById('env-key').value.trim();
    const value = document.getElementById('env-value').value.trim();
    if (!key) { toast('Key is required', 'error'); return; }
    try { await api('POST', '/api/env', { key, value }); toast('Variable added', 'success'); closeModal(); Screens['env-vars'](); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.editEnvVar = async function (key) {
    try {
        const data = await api('GET', '/api/env');
        const val = data.vars[key] || '';
        showModal('Edit Variable: ' + key,
            '<div class="form-group"><label class="form-label">Key</label><input class="form-input" value="' + escA(key) + '" disabled></div>' +
            '<div class="form-group"><label class="form-label">Value</label>' + inputH('env-edit-value', '', 'text', 'Enter new value') + '</div>',
            '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="saveEditEnvVar(\'' + escA(key) + '\')">Save</button>'
        );
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveEditEnvVar = async function (key) {
    const value = document.getElementById('env-edit-value').value;
    try { await api('PUT', '/api/env/' + key, { value }); toast('Variable updated', 'success'); closeModal(); Screens['env-vars'](); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.deleteEnvVar = function (key) {
    showModal('Delete Variable', '<p>Are you sure you want to delete <strong>' + escH(key) + '</strong>?</p>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="confirmDeleteEnvVar(\'' + escA(key) + '\')">Delete</button>'
    );
};
window.confirmDeleteEnvVar = async function (key) {
    try { await api('DELETE', '/api/env/' + key); toast('Variable deleted', 'success'); closeModal(); Screens['env-vars'](); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── SERVICE ────────────────────────────────────────────────
Screens.service = async function () {
    const content = document.getElementById('content');
    try {
        const [health, sys] = await Promise.all([api('GET', '/api/health').catch(() => ({ gateway_running: false, gateway_pid: null, version: '?' })), api('GET', '/api/system').catch(() => ({ python_version: '?', os_info: '?', disk_free: '?', uptime: '?' }))]);
        content.innerHTML = `
        <div class="card">
            <div class="card-header"><span>Gateway Service</span><span class="badge ${health.gateway_running ? 'badge-success' : 'badge-danger'}">${health.gateway_running ? 'Running' : 'Stopped'}</span></div>
            <div class="card-body">
                <div class="form-row" style="margin-bottom:16px">
                    <div class="form-group"><label class="form-label">PID</label><div>${health.gateway_pid || 'N/A'}</div></div>
                    <div class="form-group"><label class="form-label">Version</label><div>${escH(health.version || '?')}</div></div>
                    <div class="form-group"><label class="form-label">Uptime</label><div>${escH(sys.uptime || '?')}</div></div>
                </div>
                <div style="display:flex;gap:8px;flex-wrap:wrap">
                    <button class="btn btn-success" onclick="serviceAction('start')">\u25b6 Start</button>
                    <button class="btn btn-danger" onclick="serviceAction('stop')">\u25a0 Stop</button>
                    <button class="btn btn-primary" onclick="serviceAction('restart')">\u21bb Restart</button>
<button class="btn" onclick="serviceAction('doctor')">Run Diagnostics</button>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>System Information</span></div>
            <div class="card-body">
                <div class="form-row">
                    <div class="form-group"><label class="form-label">Python</label><div class="font-mono text-sm">${escH(sys.python_version || '?')}</div></div>
                    <div class="form-group"><label class="form-label">OS</label><div class="text-sm">${escH(sys.os_info || '?')}</div></div>
                    <div class="form-group"><label class="form-label">Disk Free</label><div class="text-sm">${fmtBytes(sys.disk_free)}</div></div>
                    <div class="form-group"><label class="form-label">Hermes Home</label><div class="font-mono text-sm">${escH(health.hermes_home || '?')}</div></div>
                </div>
            </div>
        </div>`;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

// ── PROVIDERS ──────────────────────────────────────────────
Screens.providers = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/providers');
        const def = data.default || {};
        const custom = data.custom || [];
        const aux = data.auxiliary || {};

        let html = '<div class="section-header"><span>Default Provider</span></div>';
        html += '<div class="card mb-16"><div class="card-body"><div class="form-row">';
        html += '<div class="form-group"><label class="form-label">Provider</label><div class="font-mono text-sm">' + escH(def.provider || '?') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Model</label><div class="font-mono text-sm">' + escH(def.model || '?') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Base URL</label><div class="font-mono text-sm">' + escH(def.base_url || '?') + '</div></div></div></div></div>';

        html += '<div class="section-header"><span>Custom Providers</span><button class="btn btn-primary" onclick="addProvider()">+ Add Provider</button></div>';
        if (custom.length === 0) {
            html += '<div class="empty-state"><p>No custom providers configured</p></div>';
        } else {
            html += '<div class="table-container"><table class="table"><thead><tr><th>Name</th><th>Base URL</th><th>Model</th><th style="width:180px">Actions</th></tr></thead><tbody>';
            custom.forEach(p => {
                html += '<tr><td class="font-mono text-sm">' + escH(p.name) + '</td><td class="text-sm">' + escH(p.base_url || '') + '</td><td class="font-mono text-sm">' + escH(p.model || '') + '</td>';
                html += '<td class="actions"><button class="btn btn-sm" onclick="editProvider(\'' + escA(p.name) + '\')">Edit</button> <button class="btn btn-sm" onclick="testProvider(\'' + escA(p.name) + '\')">Test</button> <button class="btn btn-sm btn-danger" onclick="deleteProvider(\'' + escA(p.name) + '\')">Delete</button></td></tr>';
            });
            html += '</tbody></table></div>';
        }

        html += '<div class="section-header mt-16"><span>Auxiliary Models</span></div>';
        html += '<div class="table-container"><table class="table"><thead><tr><th>Purpose</th><th>Provider</th><th>Model</th></tr></thead><tbody>';
        for (const [purpose, cfg] of Object.entries(aux)) {
            if (!cfg || typeof cfg !== 'object') continue;
            html += '<tr><td>' + escH(purpose) + '</td><td class="font-mono text-sm">' + escH(cfg.provider || 'auto') + '</td><td class="font-mono text-sm">' + escH(cfg.model || 'default') + '</td></tr>';
        }
        html += '</tbody></table></div>';
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.providerModal = function (title, name, base_url, model, saveFn) {
    showModal(title,
        '<div class="form-group"><label class="form-label">Name</label>' + inputH('prov-name', name, 'text', 'e.g. my-provider', name ? 'disabled' : '') + '</div>' +
        '<div class="form-group"><label class="form-label">Base URL</label>' + inputH('prov-url', base_url, 'url', 'https://api.example.com/v1') + '</div>' +
        '<div class="form-group"><label class="form-label">Model</label>' + inputH('prov-model', model, 'text', 'e.g. gpt-4') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="' + saveFn + '">Save</button>'
    );
};
window.addProvider = function () { window.providerModal('Add Provider', '', '', '', 'saveNewProvider()'); };
window.editProvider = async function (name) {
    try {
        const data = await api('GET', '/api/providers');
        const p = (data.custom || []).find(x => x.name === name);
        if (!p) { toast('Provider not found', 'error'); return; }
        window.providerModal('Edit Provider: ' + name, p.name, p.base_url, p.model, 'saveEditProvider()');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveNewProvider = async function () {
    const name = document.getElementById('prov-name').value.trim();
    const base_url = document.getElementById('prov-url').value.trim();
    const model = document.getElementById('prov-model').value.trim();
    if (!name) { toast('Name required', 'error'); return; }
    try { await api('POST', '/api/providers', { name, base_url, model }); toast('Provider added', 'success'); closeModal(); Screens.providers(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveEditProvider = async function () {
    const name = document.getElementById('prov-name').value.trim();
    const base_url = document.getElementById('prov-url').value.trim();
    const model = document.getElementById('prov-model').value.trim();
    try { await api('PUT', '/api/providers/' + name, { base_url, model }); toast('Provider updated', 'success'); closeModal(); Screens.providers(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.deleteProvider = function (name) {
    showModal('Delete Provider', '<p>Delete provider <strong>' + escH(name) + '</strong>?</p>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="doDeleteProvider(\'' + escA(name) + '\')">Delete</button>'
    );
};
window.doDeleteProvider = async function (name) {
    try { await api('DELETE', '/api/providers/' + name); toast('Provider deleted', 'success'); closeModal(); Screens.providers(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.testProvider = async function (name) {
    toast('Testing ' + name + '...', 'info', 2000);
    try {
        const r = await api('POST', '/api/providers/' + name + '/test');
        toast(r.ok ? 'Connection OK (' + (r.latency_ms || '?') + 'ms)' : 'Connection failed: ' + (r.error || ''), r.ok ? 'success' : 'error');
    } catch (e) { toast('Test failed: ' + e.message, 'error'); }
};

// ── MODELS ─────────────────────────────────────────────────
Screens.models = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/models');
        let html = '<div class="stats-grid"><div class="stat-card blue"><div class="stat-value font-mono" style="font-size:16px">' + escH(data.default_model || '?') + '</div><div class="stat-label">Default Model</div></div>';
        html += '<div class="stat-card blue"><div class="stat-value font-mono" style="font-size:16px">' + escH(data.default_provider || '?') + '</div><div class="stat-label">Default Provider</div></div>';
        html += '<div class="stat-card green"><div class="stat-value font-mono" style="font-size:16px">' + escH(data.fallback_model || 'None') + '</div><div class="stat-label">Fallback Model</div></div></div>';

        html += '<div class="card"><div class="card-header"><span>All Models</span><button class="btn btn-primary" onclick="addModel()">+ Add Model</button></div><div class="table-container"><table class="table"><thead><tr><th>Model ID</th></tr></thead><tbody>';
        (data.all_models || []).forEach(m => { html += '<tr><td class="font-mono text-sm">' + escH(m.provider + ' / ' + m.model) + '</td></tr>'; });
        if (!data.all_models || data.all_models.length === 0) html += '<tr><td class="text-muted">No models listed</td></tr>';
        html += '</tbody></table></div></div>';
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};
window.addModel = function () {
    showModal('Set Default Model',
        '<div class="form-group"><label class="form-label">Provider</label>' + inputH('model-provider', '', 'text', 'e.g. OpenRouter') + '</div>' +
        '<div class="form-group"><label class="form-label">Model ID</label>' + inputH('model-name', '', 'text', 'e.g. openai/gpt-4o') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="saveModel()">Save</button>'
    );
};
window.saveModel = async function () {
    const provider = document.getElementById('model-provider').value.trim();
    const model = document.getElementById('model-name').value.trim();
    if (!provider || !model) { toast('Provider and Model ID are required', 'error'); return; }
    try {
        await api('PUT', '/api/config/model', { default_provider: provider, default_model: model });
        toast('Default model updated', 'success');
        closeModal();
        Screens.models();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── AGENTS ─────────────────────────────────────────────────
Screens.agents = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/agents');
        const personalities = data.personalities || {};
        const defaults = data.defaults || {};

        let html = '<div class="card mb-16"><div class="card-header"><span>Default Agent Settings</span></div><div class="card-body"><div class="form-row">';
        html += '<div class="form-group"><label class="form-label">Max Turns</label><div>' + (defaults.max_turns || '?') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Reasoning Effort</label><div>' + (defaults.reasoning_effort || '?') + '</div></div>';
        html += '</div></div></div>';

        html += '<div class="section-header"><span>Agents / Personalities</span><button class="btn btn-primary" onclick="addAgent()">+ Add Agent</button></div>';
        const entries = Object.entries(personalities);
        if (entries.length === 0) {
            html += '<div class="empty-state"><p>No agents configured</p></div>';
        } else {
            html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px">';
            entries.forEach(([name, prompt]) => {
                html += '<div class="card"><div class="card-header"><span>' + escH(name) + '</span></div><div class="card-body"><p class="text-sm text-muted" style="max-height:80px;overflow:hidden">' + escH(typeof prompt === 'string' ? prompt.substring(0, 150) + (prompt.length > 150 ? '...' : '') : JSON.stringify(prompt).substring(0, 150)) + '</p>';
                html += '<div class="mt-16" style="display:flex;gap:8px"><button class="btn btn-sm" onclick="editAgent(\'' + escA(name) + '\')">Edit</button><button class="btn btn-sm" onclick="duplicateAgent(\'' + escA(name) + '\')">Duplicate</button><button class="btn btn-sm btn-danger" onclick="deleteAgent(\'' + escA(name) + '\')">Delete</button></div></div></div>';
            });
            html += '</div>';
        }
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.agentModal = function (title, name, prompt, nameReadonly, saveFn) {
    showModal(title,
        '<div class="form-group"><label class="form-label">Name</label>' + inputH('agent-name', name, 'text', '', nameReadonly ? 'disabled' : '') + '</div>' +
        '<div class="form-group"><label class="form-label">Prompt / Instructions</label>' + textareaH('agent-prompt', prompt, 8) + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="' + saveFn + '">Save</button>'
    );
};
window.addAgent = function () { window.agentModal('Add Agent', '', '', false, 'saveNewAgent()'); };
window.editAgent = async function (name) {
    try {
        const data = await api('GET', '/api/agents');
        const prompt = (data.personalities || {})[name];
        window.agentModal('Edit Agent: ' + name, name, prompt, true, 'saveEditAgent()');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveNewAgent = async function () {
    const name = document.getElementById('agent-name').value.trim();
    const prompt = document.getElementById('agent-prompt').value;
    if (!name) { toast('Name required', 'error'); return; }
    try { await api('POST', '/api/agents', { name, prompt }); toast('Agent created', 'success'); closeModal(); Screens.agents(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveEditAgent = async function () {
    const name = document.getElementById('agent-name').value.trim();
    const prompt = document.getElementById('agent-prompt').value;
    try { await api('PUT', '/api/agents/' + name, { prompt }); toast('Agent updated', 'success'); closeModal(); Screens.agents(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.duplicateAgent = function (name) {
    showModal('Duplicate Agent', '<div class="form-group"><label class="form-label">New Name for Copy</label>' + inputH('agent-dup-name', '', 'text', name + '-copy') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="doDuplicateAgent(\'' + escA(name) + '\')">Duplicate</button>'
    );
};
window.doDuplicateAgent = async function (name) {
    const new_name = document.getElementById('agent-dup-name').value.trim();
    if (!new_name) { toast('Name required', 'error'); return; }
    try { await api('POST', '/api/agents/' + name + '/duplicate', { new_name }); toast('Agent duplicated', 'success'); closeModal(); Screens.agents(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.deleteAgent = function (name) {
    showModal('Delete Agent', '<p>Delete agent <strong>' + escH(name) + '</strong>?</p>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="doDeleteAgent(\'' + escA(name) + '\')">Delete</button>'
    );
};
window.doDeleteAgent = async function (name) {
    try { await api('DELETE', '/api/agents/' + name); toast('Agent deleted', 'success'); closeModal(); Screens.agents(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── SKILLS ─────────────────────────────────────────────────
Screens.skills = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/skills');
        const skills = data.skills || [];
        let html = '<div class="section-header"><span>' + skills.length + ' Skills</span><div class="search-box"><span class="search-icon">\U0001f50d</span><input type="text" class="form-input" id="skill-search" placeholder="Search skills..." oninput="filterSkills()"></div></div>';

        const categories = {};
        skills.forEach(s => { const cat = s.category || 'general'; if (!categories[cat]) categories[cat] = []; categories[cat].push(s); });

        if (skills.length === 0) {
            html += '<div class="empty-state"><div class="empty-icon">\U0001f4da</div><h3>No Skills Found</h3><p>Skills directory is empty or no SKILL.md files found.</p></div>';
        } else {
            for (const [cat, catSkills] of Object.entries(categories)) {
                html += '<div class="card mb-16"><div class="card-header"><span>' + escH(cat) + '</span><span class="badge badge-info">' + catSkills.length + '</span></div>';
                html += '<div class="table-container"><table class="table skill-table"><thead><tr><th>Name</th><th>Description</th><th style="width:100px">Status</th></tr></thead><tbody>';
                catSkills.forEach(s => {
                    html += '<tr data-skill-name="' + escA(s.name) + '" data-skill-search="' + escA((s.name + ' ' + s.description + ' ' + (s.category || '')).toLowerCase()) + '">';
                    html += '<td class="font-mono text-sm">' + escH(s.name) + '</td>';
                    html += '<td class="text-sm">' + escH(s.description || '') + '</td>';
                    html += '<td><span class="badge ' + (s.enabled !== false ? 'badge-success' : 'badge-danger') + '" style="cursor:pointer" onclick="toggleSkill(\'' + escA(s.name) + '\')">' + (s.enabled !== false ? 'Enabled' : 'Disabled') + '</span></td></tr>';
                });
                html += '</tbody></table></div></div>';
            }
        }
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.filterSkills = function () {
    const q = (document.getElementById('skill-search')?.value || '').toLowerCase();
    document.querySelectorAll('.skill-table tr[data-skill-search]').forEach(tr => {
        tr.style.display = tr.dataset.skillSearch.includes(q) ? '' : 'none';
    });
};

window.toggleSkill = async function (name) {
    toast('Toggling ' + name + '...', 'info', 1500);
    try {
        const r = await api('POST', '/api/skills/' + name + '/toggle');
        toast(name + ': ' + (r.enabled ? 'Enabled' : 'Disabled'), 'success');
        Screens.skills();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── CHANNELS ───────────────────────────────────────────────
Screens.channels = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/channels');
        const channels = data.channels || [];
        if (channels.length === 0) {
            content.innerHTML = '<div class="empty-state"><div class="empty-icon">\U0001f4ac</div><h3>No Channels</h3><p>No messaging channels configured.</p></div>';
            return;
        }
        let html = '';
        channels.forEach(ch => {
            html += '<div class="card mb-16"><div class="card-header"><span>' + escH(ch.name) + '</span><span class="badge ' + (ch.enabled ? 'badge-success' : 'badge-danger') + '">' + (ch.enabled ? 'Enabled' : 'Disabled') + '</span></div>';
            html += '<div class="card-body">';
            if (ch.config && typeof ch.config === 'object') {
                html += '<div class="form-section">';
                for (const [key, val] of Object.entries(ch.config)) {
                    html += '<div class="form-row"><div class="form-group"><label class="form-label">' + escH(key) + '</label><div>' + fmtVal(val) + '</div></div></div>';
                }
                html += '</div>';
            }
            html += '<button class="btn btn-sm" onclick="editChannel(\'' + escA(ch.name) + '\')">Edit Configuration</button>';
            html += '</div></div>';
        });
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.editChannel = async function (name) {
    try {
        const data = await api('GET', '/api/channels');
        const ch = (data.channels || []).find(c => c.name === name);
        if (!ch) { toast('Channel not found', 'error'); return; }
        let fields = '';
        if (ch.config && typeof ch.config === 'object') {
            for (const [key, val] of Object.entries(ch.config)) {
                if (typeof val === 'boolean') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + toggleH('ch-' + key, val) + '</div>';
                else if (typeof val === 'number') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('ch-' + key, val, 'number') + '</div>';
                else fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('ch-' + key, val) + '</div>';
            }
        }
        showModal('Edit Channel: ' + name, fields || '<p>No configurable fields.</p>',
            '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="saveChannel(\'' + escA(name) + '\')">Save</button>'
        );
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

window.saveChannel = async function (name) {
    const updates = {};
    document.querySelectorAll('#modal-body .form-group').forEach(g => {
        const label = g.querySelector('.form-label');
        const input = g.querySelector('input, select');
        if (!label || !input) return;
        const key = input.id.replace('ch-', '');
        if (input.type === 'checkbox') updates[key] = input.checked;
        else if (input.type === 'number') updates[key] = parseFloat(input.value);
        else updates[key] = input.value;
    });
    try { await api('PUT', '/api/channels/' + name, updates); toast('Channel updated', 'success'); closeModal(); Screens.channels(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── HOOKS ──────────────────────────────────────────────────
Screens.hooks = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/hooks');
        const cfg = data.config || data;
        let fields = '';
        for (const [key, val] of Object.entries(cfg)) {
            if (typeof val === 'boolean') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + toggleH('hook-' + key, val) + '</div>';
            else if (typeof val === 'number') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('hook-' + key, val, 'number') + '</div>';
            else if (typeof val === 'object') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + textareaH('hook-' + key, JSON.stringify(val, null, 2), 4, true) + '</div>';
            else fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('hook-' + key, val) + '</div>';
        }
        if (!fields) fields = '<div class="empty-state"><p>No hooks configured yet.</p></div>';
        content.innerHTML = '<div class="card"><div class="card-header"><span>Webhooks / Hooks</span></div><div class="card-body">' + fields + '<button class="btn btn-primary mt-16" onclick="saveHooks()">Save Hooks</button></div></div>';
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.saveHooks = async function () {
    const updates = {};
    document.querySelectorAll('#content .form-group').forEach(g => {
        const label = g.querySelector('.form-label');
        const input = g.querySelector('input, select, textarea');
        if (!label || !input) return;
        const key = input.id.replace('hook-', '');
        if (!key) return;
        if (input.type === 'checkbox') updates[key] = input.checked;
        else if (input.type === 'number') updates[key] = parseFloat(input.value);
        else if (input.tagName === 'TEXTAREA' && input.classList.contains('mono')) {
            try { updates[key] = JSON.parse(input.value); } catch { updates[key] = input.value; }
        } else updates[key] = input.value;
    });
    try { await api('PUT', '/api/hooks', updates); toast('Hooks saved', 'success'); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── SESSIONS ───────────────────────────────────────────────
Screens.sessions = async function () {
    const content = document.getElementById('content');
    try {
        const cfg = await api('GET', '/api/sessions/config');
        let fields = '';
        for (const [key, val] of Object.entries(cfg)) {
            if (key === '_config_version') continue;
            if (typeof val === 'boolean') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + toggleH('sess-' + key, val) + '</div>';
            else if (typeof val === 'number') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('sess-' + key, val, 'number') + '</div>';
            else if (key === 'mode') fields += '<div class="form-group"><label class="form-label">Reset Mode</label>' + selectH('sess-mode', ['both', 'session', 'idle', 'off'], val) + '</div>';
            else fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('sess-' + key, val) + '</div>';
        }
        content.innerHTML = '<div class="card"><div class="card-header"><span>Session Configuration</span></div><div class="card-body">' + fields + '<button class="btn btn-primary mt-16" onclick="saveSessions()">Save Session Config</button></div></div>';
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.saveSessions = async function () {
    const updates = {};
    document.querySelectorAll('#content .form-group').forEach(g => {
        const input = g.querySelector('input, select');
        if (!input) return;
        const key = input.id.replace('sess-', '');
        if (!key) return;
        if (input.type === 'checkbox') updates[key] = input.checked;
        else if (input.type === 'number') updates[key] = parseFloat(input.value);
        else updates[key] = input.value;
    });
    try { await api('PUT', '/api/sessions/config', updates); toast('Session config saved', 'success'); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── LOGS ───────────────────────────────────────────────────
Screens.logs = async function () {
    const content = document.getElementById('content');
    content.innerHTML = '<div class="card"><div class="card-header"><span>System Logs</span><div class="flex gap-8"><select class="form-select" id="log-lines" style="width:auto"><option value="100">100 lines</option><option value="500" selected>500 lines</option><option value="1000">1000 lines</option></select><button class="btn btn-sm" onclick="loadLogs()">Refresh</button><button class="btn btn-sm" onclick="copyLogs()">Copy</button></div></div><div class="card-body" style="padding:0"><div id="log-output" class="font-mono text-xs" style="padding:16px;max-height:70vh;overflow:auto;background:var(--bg-primary);white-space:pre-wrap;line-height:1.6;color:var(--text-secondary)"><div class="loading"><div class="spinner"></div></div></div></div></div>';
    document.getElementById('log-lines').addEventListener('change', loadLogs);
    loadLogs();
};

window.loadLogs = async function () {
    const lines = document.getElementById('log-lines')?.value || 500;
    try {
        const data = await api('GET', '/api/logs?lines=' + lines);
        document.getElementById('log-output').textContent = data.logs || 'No logs available.';
    } catch (e) {
        document.getElementById('log-output').textContent = 'Error loading logs: ' + e.message;
    }
};

window.copyLogs = function () {
    const text = document.getElementById('log-output')?.textContent || '';
    navigator.clipboard.writeText(text).then(() => toast('Logs copied', 'success')).catch(() => toast('Copy failed', 'error'));
};

/* ═══════════════════════════════════════════════════════════════
   CHAT SCREEN
   ═══════════════════════════════════════════════════════════════ */

const chatState = {
    currentSessionId: null,
    isThinking: false,
    pendingFiles: [],
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
    micStoppedByUser: false,  // track if user manually stopped
    recognition: null,
    speechSupported: false,
    historyOpen: true,
    localMessages: [],  // messages for the current viewed session
};

// ── CLIPBOARD PASTE ─────────────────────────────────────
async function chatHandlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    let hasImage = false;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            hasImage = true;
            e.preventDefault();
            const blob = item.getAsFile();
            if (!blob) continue;
            // Extract extension from mime type
            const ext = item.type.split('/')[1] || 'png';
            // Convert to base64 and upload
            const reader = new FileReader();
            reader.onload = async () => {
                try {
                    const b64data = reader.result;
                    const resp = await fetch('/api/upload/base64', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ data: b64data, ext }),
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        chatState.pendingFiles.push(data);
                        chatRenderFileBar();
                        document.getElementById('chat-send-btn').disabled = false;
                        toast('Image pasted', 'success', 2000);
                    } else {
                        const err = await resp.json();
                        toast('Paste failed: ' + err.error, 'error');
                    }
                } catch (ex) { toast('Upload error: ' + ex.message, 'error'); }
            };
            reader.readAsDataURL(blob);
        }
    }
}

Screens.chat = function () {
    const content = document.getElementById('content');
    content.style.padding = '0';
    content.style.overflow = 'hidden';
    document.getElementById('topbar').classList.add('hidden-by-chat');
    content.innerHTML = `
    <div class="chat-layout" id="chat-layout">
        <!-- Chat History Sidebar -->
        <div class="chat-history ${chatState.historyOpen ? '' : 'collapsed'}" id="chat-history">
            <div class="chat-history-header">
                <span>Chats</span>
                <div class="chat-history-actions">
                    <button class="btn-icon" title="New Chat" onclick="chatNewSession()" style="width:28px;height:28px">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    </button>
                    <button class="btn-icon" title="Toggle sidebar" onclick="chatToggleHistory()" style="width:28px;height:28px">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
                    </button>
                </div>
            </div>
            <div class="chat-history-list" id="chat-history-list">
                <div class="loading"><div class="spinner"></div></div>
            </div>
        </div>

        <!-- Chat Main Area -->
        <div class="chat-main">
            <div class="chat-header">
                <div class="chat-header-left">
                    ${!chatState.historyOpen ? '<button class="btn-icon" title="Show chats" onclick="chatToggleHistory()" style="width:32px;height:32px;margin-right:4px"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg></button>' : ''}
                    <button class="btn-icon" title="Export Chat" onclick="chatExport()" style="width:32px;height:32px">
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    </button>
                </div>
                <div class="chat-header-right">
                    <button class="btn-icon" title="New session" onclick="chatNewSession()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    </button>
                    <button class="btn-icon" title="Clear chat" onclick="chatClearCurrent()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            </div>


            <div class="chat-transcript" id="chat-messages"></div>

            <div class="chat-file-bar hidden" id="chat-file-bar">
                <div class="chat-file-previews" id="chat-file-previews"></div>
                <button class="clear-files" onclick="chatClearFiles()">Clear</button>
            </div>

            <div class="chat-composer">
                <div class="chat-composer-row">
                    <button class="chat-btn" id="chat-attach-btn" title="Attach files" onclick="document.getElementById('chat-file-input').click()">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                    </button>
                    <input type="file" id="chat-file-input" multiple style="display:none" onchange="chatHandleFiles(event)">
                    <textarea id="chat-input" placeholder="Message Hermes... (Ctrl+V to paste screenshots)" rows="1" onkeydown="chatKeyDown(event)" oninput="chatAutoResize(this)"></textarea>
                    <button class="chat-btn" id="chat-voice-btn" title="Voice input (click to start/stop)" onclick="chatToggleVoice()">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
                    </button>
                    <button class="chat-send-btn" id="chat-send-btn" onclick="chatSend()" disabled>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                    </button>
                </div>
                <div class="chat-composer-footer">
                    <span class="chat-composer-hint">Enter to send, Shift+Enter for new line, Ctrl+V to paste image</span>
                    <span id="chat-voice-status" class="chat-voice-status"></span>
                </div>
            </div>
        </div>
    </div>`;

    const input = document.getElementById('chat-input');
    input.addEventListener('input', () => {
        document.getElementById('chat-send-btn').disabled = !input.value.trim() && chatState.pendingFiles.length === 0;
    });
    // Clipboard paste handler
    input.addEventListener('paste', chatHandlePaste);
    input.focus();

    // Drag-drop on the whole chat area
    const layout = document.getElementById('chat-layout');
    layout.addEventListener('dragover', (e) => { e.preventDefault(); layout.classList.add('drag-over'); });
    layout.addEventListener('dragleave', (e) => { if (!layout.contains(e.relatedTarget)) layout.classList.remove('drag-over'); });
    layout.addEventListener('drop', (e) => { e.preventDefault(); layout.classList.remove('drag-over'); Array.from(e.dataTransfer.files).forEach(f => chatUploadFile(f)); });

    // Setup speech recognition
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) {
        chatState.speechSupported = true;
        chatState.recognition = new SR();
        chatState.recognition.continuous = true;  // stay active
        chatState.recognition.interimResults = true;
        chatState.recognition.lang = 'en-US';
        chatState.recognition.onresult = (e) => {
            let t = '';
            for (let i = e.resultIndex; i < e.results.length; i++) t += e.results[i][0].transcript;
            input.value = t;
            document.getElementById('chat-send-btn').disabled = false;
        };
        chatState.recognition.onend = () => {
            // Auto-restart if user didn't manually stop
            if (chatState.isRecording && !chatState.micStoppedByUser) {
                try { chatState.recognition.start(); } catch(e) { /* ignore */ }
            } else {
                chatStopVoiceUI();
            }
        };
        chatState.recognition.onerror = (e) => {
            if (e.error === 'no-speech' && chatState.isRecording && !chatState.micStoppedByUser) {
                try { chatState.recognition.start(); } catch(ex) { /* ignore */ }
                return;
            }
            chatStopVoiceUI();
        };
    }

    // Load sessions
    chatLoadHistory();

    // Render current session or welcome
    if (chatState.currentSessionId) {
        chatRenderMessages();
    } else {
        chatShowWelcome();
    }
};

// ── CHAT HISTORY ─────────────────────────────────────────

async function chatLoadHistory() {
    try {
        const data = await api('GET', '/api/chat/sessions');
        const list = document.getElementById('chat-history-list');
        if (!list) return;
        const sessions = data.sessions || [];
        if (sessions.length === 0) {
            list.innerHTML = '<div class="chat-history-empty">No chats yet.<br>Click + to start one.</div>';
            return;
        }
        list.innerHTML = sessions.map(s => {
            const isActive = s.id === chatState.currentSessionId;
            const msgCount = s.message_count || 0;
            const preview = s.last_message ? escH(s.last_message) : 'Empty';
            return '<div class="chat-history-item' + (isActive ? ' active' : '') + '" data-sid="' + escA(s.id) + '" onclick="chatLoadSession(\'' + escA(s.id) + '\')">' +
                '<div class="chat-history-item-title">' + escH(s.title || 'Untitled') + '</div>' +
                '<div class="chat-history-item-preview">' + preview + '</div>' +
                '<div class="chat-history-item-meta">' + msgCount + ' msgs</div>' +
                '<div class="chat-history-item-actions">' +
                '<button class="btn-icon" title="Delete" onclick="event.stopPropagation();chatDeleteSession(\'' + escA(s.id) + '\')" style="width:22px;height:22px"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>' +
                '</div></div>';
        }).join('');
    } catch (e) {
        const list = document.getElementById('chat-history-list');
        if (list) list.innerHTML = '<div class="chat-history-empty">Error loading chats</div>';
    }
}

window.chatLoadSession = async function (sid) {
    chatState.currentSessionId = sid;
    try {
        const data = await api('GET', '/api/chat/sessions/' + sid + '/messages');
        chatState.localMessages = data.messages || [];
        chatRenderMessages();
    } catch (e) {
        toast('Failed to load session', 'error');
    }
    chatLoadHistory();  // refresh active state
};

window.chatDeleteSession = async function (sid) {
    try {
        await api('POST', '/api/chat/sessions/' + sid + '/delete');
        toast('Chat deleted', 'success', 2000);
        if (chatState.currentSessionId === sid) {
            chatState.currentSessionId = null;
            chatState.localMessages = [];
            chatShowWelcome();
        }
        chatLoadHistory();
    } catch (e) { toast('Delete failed', 'error'); }
};

window.chatToggleHistory = function () {
    chatState.historyOpen = !chatState.historyOpen;
    const el = document.getElementById('chat-history');
    if (el) el.classList.toggle('collapsed', !chatState.historyOpen);
    // Re-render chat to show/hide toggle button in header
    if (document.getElementById('chat-layout')) Screens.chat();
};

function chatShowWelcome() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    msgs.innerHTML = `
    <div id="chat-welcome" class="chat-welcome">
        <div class="chat-welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        </div>
        <h1>Hermes Agent</h1>
        <p>Your personal AI assistant with access to tools, files, and real-time data.</p>
        <div class="chat-quick-actions">
            <button class="chat-quick-btn" onclick="chatQuick('Help me debug a coding issue')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                <span>Coding</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Analyze the attached file')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <span>File Analysis</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Research a topic for me')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <span>Research</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Help me write something')">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                <span>Writing</span>
            </button>
        </div>
        <p class="chat-welcome-hint">Tip: Paste screenshots directly with Ctrl+V</p>
    </div>`;
}

function chatRenderMessages() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    msgs.innerHTML = '';
    const messages = chatState.localMessages;
    if (!messages || messages.length === 0) {
        chatShowWelcome();
        return;
    }
    messages.forEach(m => {
        const role = m.role;
        const content = m.content;
        const files = m.files || [];
        const time = m.timestamp ? new Date(m.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
        const avatarSvg = role === 'user'
            ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
            : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>';
        let filesHtml = '';
        if (files && files.length > 0) {
            filesHtml = '<div class="chat-msg-files">' + files.map(f => '<span class="chat-file-tag"><span>\U0001f4ce</span>' + escH(f) + '</span>').join('') + '</div>';
        }
        const div = document.createElement('div');
        div.className = 'chat-msg ' + role;
        div.innerHTML = '<div class="chat-msg-inner"><div class="chat-msg-avatar">' + avatarSvg + '</div><div class="chat-msg-body"><div class="chat-bubble">' + chatRenderMd(content) + '</div>' + filesHtml + (time ? '<div class="chat-msg-time">' + time + '</div>' : '') + '</div></div>';
        msgs.appendChild(div);
    });
    msgs.scrollTop = msgs.scrollHeight;
}

window.chatNewSession = function () {
    chatState.currentSessionId = null;
    chatState.localMessages = [];
    chatState.pendingFiles = [];
    chatRenderFileBar();
    chatShowWelcome();
    chatLoadHistory();
    const input = document.getElementById('chat-input');
    if (input) { input.value = ''; input.focus(); }
    toast('New session', 'info', 1500);
};

window.chatClearCurrent = function () {
    if (!chatState.currentSessionId) return;
    chatState.isThinking = true;
    api('POST', '/api/chat/sessions/' + chatState.currentSessionId + '/clear').then(() => {
        chatState.localMessages = [];
        chatRenderMessages();
        chatLoadHistory();
        toast('Chat cleared', 'info', 1500);
    }).catch(e => { chatState.isThinking = false; toast('Error: ' + e.message, 'error'); });
};

window.chatQuick = function (text) {
    document.getElementById('chat-input').value = text;
    document.getElementById('chat-send-btn').disabled = false;
    chatSend();
};

window.chatSend = async function () {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message && chatState.pendingFiles.length === 0) return;
    if (chatState.isThinking) return;

    // Remove welcome if present
    const welcome = document.getElementById('chat-welcome');
    if (welcome) welcome.remove();

    chatState.isThinking = true;

    // Show thinking indicator
    const existing = document.getElementById('chat-thinking-dots');
    if (existing) existing.remove();
    const msgs = document.getElementById('chat-messages');
    const dots = document.createElement('div');
    dots.id = 'chat-thinking-dots';
    dots.className = 'chat-thinking';
    dots.innerHTML = '<div class="chat-thinking-dots"><span></span><span></span><span></span></div>';
    if (msgs) msgs.appendChild(dots);

    const files = chatState.pendingFiles.map(f => f.name);
    // Optimistically add user message to local
    const userMsg = { role: 'user', content: message, files, timestamp: new Date().toISOString() };
    chatState.localMessages.push(userMsg);
    chatAppendMsg('user', message, files);
    input.value = '';
    chatAutoResize(input);
    document.getElementById('chat-send-btn').disabled = true;

    try {
        const resp = await api('POST', '/api/chat', {
            message, session_id: chatState.currentSessionId,
            files: chatState.pendingFiles.map(f => f.stored_as),
        });
        chatState.currentSessionId = resp.session_id;
        const assistantMsg = { role: 'assistant', content: resp.response, timestamp: new Date().toISOString() };
        chatState.localMessages.push(assistantMsg);
        chatAppendMsg('assistant', resp.response);
    } catch (e) {
        const errMsg = { role: 'assistant', content: 'Connection error: ' + e.message, timestamp: new Date().toISOString() };
        chatState.localMessages.push(errMsg);
        chatAppendMsg('assistant', 'Connection error: ' + e.message);
    }

    chatClearFiles();
    chatState.isThinking = false;
    dots.remove();
    input.focus();
    // Refresh history to update title and order
    chatLoadHistory();
};

// ── EXPORT ─────────────────────────────────────────────────

window.chatExport = function () {
    const messages = chatState.localMessages;
    if (!messages || messages.length === 0) {
        toast('Nothing to export', 'error', 2000);
        return;
    }
    let text = '';
    const title = chatState.currentSessionId || 'hermes-chat';
    const now = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

    // Markdown format
    text += '# Hermes Chat Export\n\n';
    text += '**Session:** ' + title + '\n';
    text += '**Date:** ' + new Date().toLocaleString() + '\n';
    text += '**Messages:** ' + messages.length + '\n\n---\n\n';

    messages.forEach(m => {
        const time = m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : '';
        const label = m.role === 'user' ? '**You**' : '**Hermes**';
        text += label + ' (' + time + '):\n\n' + m.content + '\n\n';
        if (m.files && m.files.length > 0) {
            text += '*Attachments:* ' + m.files.join(', ') + '\n\n';
        }
        text += '---\n\n';
    });

    const blob = new Blob([text], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'hermes-chat-' + now + '.md';
    a.click();
    URL.revokeObjectURL(url);
    toast('Chat exported', 'success', 2000);
};

function chatAppendMsg(role, content, files = []) {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const avatarSvg = role === 'user'
        ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
        : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>';
    const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    let filesHtml = '';
    if (files && files.length > 0) {
        filesHtml = '<div class="chat-msg-files">' + files.map(f => '<span class="chat-file-tag"><span>\U0001f4ce</span>' + escH(f) + '</span>').join('') + '</div>';
    }
    const div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    div.innerHTML = '<div class="chat-msg-inner"><div class="chat-msg-avatar">' + avatarSvg + '</div><div class="chat-msg-body"><div class="chat-bubble">' + chatRenderMd(content) + '</div>' + filesHtml + '<div class="chat-msg-time">' + time + '</div></div></div>';
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function chatRenderMd(text) {
    if (!text) return '';
    let h = escH(text);
    h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    h = h.replace(/`([^`]+)`/g, '<code>$1</code>');
    h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
    h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    h = h.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
    h = h.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    h = h.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    h = h.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
    h = h.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(m, text, url) {
        if (/^https?:\/\//i.test(url)) return '<a href="' + escA(url) + '" target="_blank" rel="noopener">' + text + '</a>';
        return text;
    });
    h = h.replace(/\n\n/g, '</p><p>');
    h = h.replace(/\n/g, '<br>');
    if (!h.startsWith('<')) h = '<p>' + h + '</p>';
    h = h.replace(/<p>(<h[1-3]>)/g, '$1');
    h = h.replace(/(<\/h[1-3]>)<\/p>/g, '$1');
    h = h.replace(/<p>(<pre>)/g, '$1');
    h = h.replace(/(<\/pre>)<\/p>/g, '$1');
    h = h.replace(/<p>(<ul>)/g, '$1');
    h = h.replace(/(<\/ul>)<\/p>/g, '$1');
    h = h.replace(/<p>(<blockquote>)/g, '$1');
    h = h.replace(/(<\/blockquote>)<\/p>/g, '$1');
    return h.replace(/<p><\/p>/g, '');
}

window.chatKeyDown = function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); chatSend(); }
};

window.chatAutoResize = function (el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
};

async function chatUploadFile(file) {
    const fd = new FormData();
    fd.append('file', file);
    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: fd });
        if (resp.ok) {
            const data = await resp.json();
            chatState.pendingFiles.push(data);
            chatRenderFileBar();
            document.getElementById('chat-send-btn').disabled = false;
        } else {
            const err = await resp.json();
            toast('Upload failed: ' + err.error, 'error');
        }
    } catch (e) { toast('Upload error: ' + e.message, 'error'); }
}

window.chatHandleFiles = function (e) {
    Array.from(e.target.files).forEach(f => chatUploadFile(f));
    e.target.value = '';
};

function chatRenderFileBar() {
    const bar = document.getElementById('chat-file-bar');
    const previews = document.getElementById('chat-file-previews');
    if (!bar || !previews) return;
    if (chatState.pendingFiles.length === 0) { bar.classList.add('hidden'); return; }
    bar.classList.remove('hidden');
    previews.innerHTML = chatState.pendingFiles.map((f, i) =>
        '<div class="chat-file-item"><span>' + chatFileIcon(f.type) + '</span><span>' + escH(f.name) + '</span><span style="color:var(--text-muted);font-size:11px">' + chatFmtSize(f.size) + '</span><button class="remove-file" onclick="chatRemoveFile(' + i + ')">\u2715</button></div>'
    ).join('');
}

function chatFileIcon(mime) { return mime?.startsWith('image/') ? '\U0001f5bc\ufe0f' : mime?.startsWith('audio/') ? '\U0001f3b5' : mime?.includes('pdf') ? '\U0001f4d5' : '\U0001f4c4'; }
function chatFmtSize(b) { return b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB'; }

window.chatRemoveFile = function (i) { chatState.pendingFiles.splice(i, 1); chatRenderFileBar(); };
window.chatClearFiles = function () { chatState.pendingFiles = []; chatRenderFileBar(); };

// ── VOICE (continuous mode) ───────────────────────────────

window.chatToggleVoice = function () {
    if (chatState.isRecording) {
        chatState.micStoppedByUser = true;
        chatStopVoice();
    } else {
        chatState.micStoppedByUser = false;
        chatStartVoice();
    }
};

function chatStartVoice() {
    const btn = document.getElementById('chat-voice-btn');
    const status = document.getElementById('chat-voice-status');
    if (chatState.speechSupported && chatState.recognition) {
        try { chatState.recognition.start(); } catch(e) { /* already started */ }
        chatState.isRecording = true;
        chatState.micStoppedByUser = false;
        btn.classList.add('recording');
        status.textContent = 'Listening... (click mic to stop)';
        status.classList.add('active');
    } else if (navigator.mediaDevices) {
        navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
            chatState.mediaRecorder = new MediaRecorder(stream);
            chatState.audioChunks = [];
            chatState.mediaRecorder.ondataavailable = (e) => chatState.audioChunks.push(e.data);
            chatState.mediaRecorder.onstop = () => {
                const blob = new Blob(chatState.audioChunks, { type: 'audio/webm' });
                chatUploadFile(new File([blob], 'voice.webm', { type: 'audio/webm' }));
                stream.getTracks().forEach(t => t.stop());
                chatStopVoiceUI();
            };
            chatState.mediaRecorder.start();
            chatState.isRecording = true;
            chatState.micStoppedByUser = false;
            btn.classList.add('recording');
            status.textContent = 'Recording... (click mic to stop)';
            status.classList.add('active');
        }).catch(() => toast('Microphone access denied', 'error'));
    }
}

function chatStopVoice() {
    chatState.micStoppedByUser = true;
    if (chatState.speechSupported && chatState.recognition) {
        try { chatState.recognition.stop(); } catch(e) { /* ignore */ }
    }
    if (chatState.mediaRecorder && chatState.mediaRecorder.state === 'recording') chatState.mediaRecorder.stop();
    // UI update happens in onend callback
}

function chatStopVoiceUI() {
    const btn = document.getElementById('chat-voice-btn');
    const status = document.getElementById('chat-voice-status');
    if (btn) btn.classList.remove('recording');
    if (status) { status.textContent = ''; status.classList.remove('active'); }
    chatState.isRecording = false;
}

/* ═══════════════════════════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
    ThemeManager.init();

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => navigate(item.dataset.screen));
    });

    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-overlay').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeModal(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

    document.getElementById('menu-toggle').addEventListener('click', () => {
        // On desktop: toggle the sidebar collapse (same as collapse button)
        // On mobile: toggle the mobile overlay
        if (window.innerWidth > 768) {
            document.getElementById('sidebar').classList.toggle('collapsed');
            document.getElementById('main-wrapper').classList.toggle('sidebar-collapsed');
        } else {
            document.getElementById('sidebar').classList.toggle('mobile-open');
        }
    });

    document.getElementById('sidebar-collapse').addEventListener('click', () => {
        document.getElementById('sidebar').classList.toggle('collapsed');
        document.getElementById('main-wrapper').classList.toggle('sidebar-collapsed');
    });

    document.getElementById('btn-reload-config').addEventListener('click', reloadConfig);

    document.getElementById('theme-toggle').addEventListener('click', () => {
        ThemeManager.cycle();
        toast('Theme: ' + ThemeManager.getLabel(), 'info', 2000);
    });

    checkHealth();
    // Support ?chat or #chat for direct navigation
    const params = new URLSearchParams(window.location.search);
    const hash = window.location.hash.replace('#', '');
    const direct = params.get('go') || hash || '';
    navigate(direct && Screens[direct] ? direct : 'dashboard');
    // Listen for hash changes
    window.addEventListener('hashchange', () => {
        const h = window.location.hash.replace('#', '');
        if (h && Screens[h]) navigate(h);
    });
});

setInterval(checkHealth, 10000);
