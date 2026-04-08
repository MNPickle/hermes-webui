/* Hermes Admin Panel - Main Application JavaScript */

const API = { base: '' };
const WEB_UI_VERSION = '1.1.0';
const UI_ICONS = {
    search: '&#128269;',
    books: '&#128218;',
    speechBubble: '&#128172;',
    paperclip: '&#128206;',
    image: '&#128444;&#65039;',
    audio: '&#127925;',
    pdf: '&#128213;',
    file: '&#128196;',
};

// Token management
function getToken() {
    return localStorage.getItem('hermes_webui_token');
}

function setToken(token) {
    localStorage.setItem('hermes_webui_token', token);
}

function clearToken() {
    localStorage.removeItem('hermes_webui_token');
}

// AuthRequired sentinel — thrown only when user cancels the token prompt
// (api() retries internally after prompt; this is for the cancel path only)
class AuthRequired extends Error {
    constructor() { super('Authentication required'); this.name = 'AuthRequired'; }
}

let _tokenPromptActive = false;
let _tokenPromptDeferred = null;

function promptForToken() {
    if (_tokenPromptActive) return null;
    _tokenPromptActive = true;
    // Create a deferred that waiting requests will await
    let resolveDeferred;
    _tokenPromptDeferred = new Promise(resolve => { resolveDeferred = resolve; });
    try {
        const token = prompt('Enter your HERMES_WEBUI_TOKEN to access the admin panel:\n\n(Set this with: export HERMES_WEBUI_TOKEN=your-token-here)');
        if (token && token.trim()) {
            const trimmed = token.trim();
            setToken(trimmed);
            resolveDeferred(trimmed); // resolve the deferred so all waiters retry
            return trimmed;
        }
        resolveDeferred(null); // user cancelled
        return null;
    } finally {
        _tokenPromptActive = false;
        _tokenPromptDeferred = null;
    }
}

async function authFetch(path, options = {}, signal) {
    const fetchWithToken = async (tokenValue) => {
        const headers = new Headers(options.headers || {});
        if (tokenValue && !headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + tokenValue);
        return fetch(API.base + path, { ...options, headers, signal });
    };
    const readAuthError = async (resp) => {
        let errMsg = 'Authentication failed';
        try {
            const d = await resp.clone().json();
            errMsg = d.error || d.message || errMsg;
        } catch {}
        return errMsg;
    };

    let token = getToken();
    let resp = await fetchWithToken(token);
    if (!resp.ok && resp.status === 401) {
        let errMsg = await readAuthError(resp);
        if (/not configured/i.test(errMsg)) {
            throw new Error(errMsg);
        }

        // Another request may have prompted and stored a newer token while this
        // request was still resolving its own 401. Retry once with the latest
        // saved token before prompting again.
        const latestSavedToken = getToken();
        if (latestSavedToken && latestSavedToken !== token) {
            resp = await fetchWithToken(latestSavedToken);
            if (resp.ok) return resp;
            if (resp.status !== 401) return resp;
            token = latestSavedToken;
            errMsg = await readAuthError(resp);
        }

        // Only clear the token we actually attempted. Another request may have
        // already replaced localStorage with a newer valid token.
        if (token && getToken() === token) clearToken();

        let savedToken = null;
        if (_tokenPromptActive && _tokenPromptDeferred) {
            savedToken = await _tokenPromptDeferred;
        } else {
            savedToken = promptForToken();
        }
        if (!savedToken) throw new AuthRequired();

        resp = await fetchWithToken(savedToken);
        if (resp.ok) return resp;
        if (resp.status === 401) {
            if (getToken() === savedToken) clearToken();
            errMsg = await readAuthError(resp);
            throw new Error(errMsg || 'Authentication failed: check your token');
        }
    }
    return resp;
}

async function api(method, path, body, signal) {
    const headers = { 'Content-Type': 'application/json' };
    const opts = { method, headers };
    if (body !== undefined && body !== null) opts.body = JSON.stringify(body);
    const resp = await authFetch(path, opts, signal);
    if (!resp.ok) {
        if (resp.status === 499) {
            let cancelled = false;
            let errMsg = 'Request cancelled';
            try {
                const d = await resp.json();
                cancelled = !!d.cancelled;
                errMsg = d.error || d.message || errMsg;
            } catch {}
            if (cancelled) {
                const err = new Error(errMsg);
                err.name = 'AbortError';
                throw err;
            }
        }
        let errMsg = 'Request failed';
        try {
            const d = await resp.json();
            errMsg = d.error || d.message || (Array.isArray(d.details) ? d.details.join('; ') : errMsg);
        } catch {}
        throw new Error(errMsg);
    }
    return resp.json();
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
        folders: 'Folders', service: 'Service Controls', providers: 'Providers', models: 'Models',
        agents: 'Agents', skills: 'Skills', channels: 'Integrations',
        hooks: 'Hooks / Webhooks', cron: 'Cron Jobs', sessions: 'Session Reset', logs: 'Log File Tail', chat: 'Chat'
    }[s] || s;
}

function navigate(screen) {
    if (screen === 'integrations') screen = 'channels';
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    const navItem = document.querySelector('[data-screen="' + screen + '"]');
    if (navItem) navItem.classList.add('active');
    if (screen === 'chat') {
        chatGoHome();
    }
    const content = document.getElementById('content');
    content.style.padding = screen === 'chat' ? '0' : '';
    content.style.overflow = screen === 'chat' ? 'hidden' : '';
    content.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
    document.getElementById('sidebar').classList.remove('mobile-open');
    // Show/hide topbar: hide when chat is active
    document.getElementById('topbar').classList.toggle('hidden-by-chat', screen === 'chat');
    if (Screens[screen]) Screens[screen]();
    else content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u2753</div><h3>Screen not found</h3></div>';
    setTimeout(() => { if (window.renderSidebarFoldersTree) renderSidebarFoldersTree(); }, 0);
}

let _healthCache = null, _healthTs = 0;
async function checkHealth() {
    const now = Date.now();
    if (_healthCache && (now - _healthTs) < 5000) return;
    try {
        const d = await api('GET', '/api/health');
        const dot = document.querySelector('#connection-status .status-dot');
        const txt = document.querySelector('#connection-status .status-text');
        const running = d.gateway_running;
        dot.className = 'status-dot ' + (running ? 'online' : 'warning');
        txt.textContent = running ? 'Gateway Running' : 'Gateway Stopped';
        const ver = document.getElementById('sidebar-version');
        if (ver) {
            ver.textContent = `UI v${WEB_UI_VERSION}`;
            if (d.version) {
                const firstLine = d.version.split('\n')[0].replace('Hermes Agent ', '');
                ver.title = `Hermes Agent ${firstLine}`;
            } else {
                ver.removeAttribute('title');
            }
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
                <div class="stat-label">CLI Tool Status</div>
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
            <div class="card-header"><span>Tool Status Snapshot</span><span class="badge badge-success">${tools.total_enabled || 0} enabled</span></div>
            <div class="table-container">
                <table class="table">
                    <thead><tr><th>Name</th><th>Status</th><th>Description</th></tr></thead>
                    <tbody>${(tools.tools || []).map(t => '<tr><td class="font-mono text-sm">' + escH(t.name) + '</td><td>' + (t.status === 'enabled' ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge badge-danger">Disabled</span>') + '</td><td class="text-sm">' + escH(t.description || '') + '</td></tr>').join('')}</tbody>
                </table>
            </div>
            <div class="card-body pt-0">
                <p class="text-sm text-secondary">This list is parsed from <span class="font-mono">hermes tools list</span> output and may not reflect deeper runtime state.</p>
            </div>
        </div>`;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error loading dashboard</h3><p>' + escH(e.message) + '</p></div>';
    }
};
async function setBtnLoading(el, loading) {
    if (!el) return;
    if (loading) { el.dataset._origText = el.textContent; el.disabled = true; el.classList.add('btn-loading'); }
    else { el.textContent = el.dataset._origText || el.textContent; el.disabled = false; el.classList.remove('btn-loading'); }
}

// ── SERVICE ────────────────────────────────────────────────
async function serviceAction(action) {
    const actionLabels = { start: '\u25b6 Start', stop: '\u25a0 Stop', restart: '\u21bb Restart', doctor: 'Run Diagnostics' };
    const btns = Array.from(document.querySelectorAll('.card-body button')).filter(b => b.textContent.trim() === actionLabels[action]);
    const btn = btns[0];
    toast('Running ' + action + '...', 'info', 2000);
    try {
        setBtnLoading(btn, true);
        const r = await api('POST', '/api/service/' + action);
        toast(action.charAt(0).toUpperCase() + action.slice(1) + ': ' + (r.ok ? 'Success' : 'Failed'), r.ok ? 'success' : 'error');
        // Invalidate health cache so next check gets fresh gateway status
        _healthCache = null;
        checkHealth();
        // Re-render service card with fresh status from /api/health
        Screens.service();
        if (action === 'start' || action === 'restart') {
            setTimeout(() => { _healthCache = null; checkHealth(); Screens.service(); }, 3000);
        }
        if (action === 'doctor' && r.output) {
            showModal('Diagnostics Output', '<pre class="font-mono text-sm" style="max-height:400px;overflow:auto;white-space:pre-wrap">' + escH(r.output) + '</pre>');
        }
    } catch (e) { toast('Error: ' + e.message, 'error'); }
    finally { setBtnLoading(btn, false); }
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
            return '<div class="tab-pane' + (i === 0 ? ' active' : '') + '" data-tab="' + t.id + '">' + formHtml + '<p class="form-hint">Saves active tab only</p><button class="btn btn-primary" onclick="saveSettings(this)">Save Settings</button></div>';
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

window.saveSettings = async function (btn) {
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
        setBtnLoading(btn, true);
        for (const [sec, data] of Object.entries(updates)) { await api('PUT', '/api/config/' + sec, data); }
        toast('Settings saved', 'success');
    } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    finally { setBtnLoading(btn, false); }
};

// ── ENV VARS ───────────────────────────────────────────────
const envVarsState = {
    activeGroup: 'Provider',
    vars: {},
    groups: {},
    metadata: {},
    presets: {},
    groupHelp: {},
};

function envPresetList(group) {
    return Array.isArray(envVarsState.presets[group]) ? envVarsState.presets[group] : [];
}

function envVarMeta(key) {
    return envVarsState.metadata[key] || {
        key,
        label: key,
        description: 'Custom environment variable.',
        group: 'System',
        secret: /token|secret|key|password/i.test(String(key || '')),
        default_value: '',
    };
}

function envActiveGroupList() {
    return ['Provider', 'Channel', 'System'];
}

function envAddButtonLabel(group) {
    return '+ Add ' + (group || 'Environment') + ' Variable';
}

function envPresetDescription(meta) {
    let text = meta.description || 'Custom environment variable.';
    if (meta.default_value) {
        text += ' Default: ' + meta.default_value + '.';
    }
    return text;
}

function envMaskedDisplay(value) {
    const text = String(value || '');
    if (!text) return '(empty)';
    return '•'.repeat(Math.min(text.length, 20)) + (text.length > 20 ? '…' : '');
}

function envPresetAction(group, key) {
    if (key && Object.prototype.hasOwnProperty.call(envVarsState.vars || {}, key)) {
        editEnvVar(key);
        return;
    }
    addEnvVar(group, key);
}

Screens['env-vars'] = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/env');
        envVarsState.vars = data.vars || {};
        envVarsState.groups = data.groups || {};
        envVarsState.metadata = data.metadata || {};
        envVarsState.presets = data.presets || {};
        envVarsState.groupHelp = data.group_help || {};
        const vars = envVarsState.vars;
        const groups = envVarsState.groups;
        const groupNames = envActiveGroupList();
        if (!groupNames.includes(envVarsState.activeGroup)) envVarsState.activeGroup = groupNames[0];
        const activeGroup = envVarsState.activeGroup;
        const activePresets = envPresetList(activeGroup);

        let html = '<div class="section-header"><span></span><button class="btn btn-primary" onclick="addEnvVar(\'' + escA(activeGroup) + '\')">' + escH(envAddButtonLabel(activeGroup)) + '</button></div>';
        html += '<div class="card mb-16"><div class="card-header"><span>' + escH(activeGroup + ' Variables') + '</span></div><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">' + escH(envVarsState.groupHelp[activeGroup] || 'Environment variables let you configure Hermes without editing files by hand.') + '</p>';
        if (activePresets.length) {
            html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
            activePresets.forEach(preset => {
                html += '<button class="btn btn-sm" onclick="envPresetAction(\'' + escA(activeGroup) + '\', \'' + escA(preset.key) + '\')">' + escH(preset.label || preset.key) + '</button>';
            });
            html += '</div>';
        }
        html += '</div></div>';
        html += '<div class="tabs">';
        groupNames.forEach(g => { html += '<button class="tab' + (g === activeGroup ? ' active' : '') + '" data-group="' + g + '">' + g + '</button>'; });
        html += '</div>';

        groupNames.forEach(g => {
            const keys = (groups[g] || []).filter(k => vars.hasOwnProperty(k));
            html += '<div class="tab-pane' + (g === activeGroup ? ' active' : '') + '" data-group="' + g + '">';
            if (keys.length === 0) {
                html += '<div class="empty-state"><p>No variables in this group yet.</p></div>';
            } else {
                html += '<div class="table-container"><table class="table"><thead><tr><th>Key</th><th>What It Does</th><th>Value</th><th style="width:120px">Actions</th></tr></thead><tbody>';
                keys.forEach(k => {
                    const meta = envVarMeta(k);
                    html += '<tr><td><div class="font-mono text-sm">' + escH(k) + '</div></td><td class="text-sm">' + escH(meta.description || 'Custom environment variable.') + '</td><td class="font-mono text-sm text-muted">' + escH(vars[k]) + '</td><td class="actions"><button class="btn btn-sm" onclick="editEnvVar(\'' + escA(k) + '\')">Edit</button> <button class="btn btn-sm btn-danger" onclick="deleteEnvVar(\'' + escA(k) + '\')">Delete</button></td></tr>';
                });
                html += '</tbody></table></div>';
            }
            html += '</div>';
        });
        content.innerHTML = html;

        content.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                envVarsState.activeGroup = tab.dataset.group || 'Provider';
                Screens['env-vars']();
            });
        });
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.envVarPresetChanged = function () {
    const presetKey = document.getElementById('env-preset')?.value || '';
    const group = document.getElementById('env-group')?.value || envVarsState.activeGroup || 'Provider';
    const preset = envPresetList(group).find(item => item.key === presetKey) || null;
    const keyInput = document.getElementById('env-key');
    const valueInput = document.getElementById('env-value');
    const helper = document.getElementById('env-preset-helper');
    const footerBtn = document.getElementById('env-save-btn');
    if (!keyInput || !helper) return;
    if (preset) {
        keyInput.value = preset.key || '';
        keyInput.placeholder = preset.key || 'ENV_VAR_NAME';
        if (valueInput && !valueInput.value && preset.default_value) {
            valueInput.value = preset.default_value;
        }
        const existingValue = Object.prototype.hasOwnProperty.call(envVarsState.vars || {}, preset.key) ? envVarsState.vars[preset.key] : '';
        helper.innerHTML = '<p class="text-sm text-secondary">' + escH(envPresetDescription(preset)) + '</p>' +
            (existingValue
                ? '<p class="text-sm text-secondary">Saved already: <span class="font-mono">' + escH(envMaskedDisplay(existingValue)) + '</span>. Saving here will update the existing value.</p>'
                : '');
        if (footerBtn) footerBtn.textContent = existingValue ? 'Update' : 'Save';
    } else {
        keyInput.placeholder = 'e.g. MY_API_KEY';
        helper.innerHTML = '<p class="text-sm text-secondary">Use a custom variable name only when the Hermes docs, a provider, or a skill specifically tells you to.</p>';
        if (footerBtn) footerBtn.textContent = 'Save';
    }
};

window.addEnvVar = function (group = envVarsState.activeGroup || 'Provider', presetKey = '') {
    const presets = envPresetList(group);
    const options = [{ value: '', label: 'Custom variable' }].concat(presets.map(item => ({
        value: item.key,
        label: item.label || item.key,
    })));
    showModal('Add Environment Variable',
        '<div class="form-group"><label class="form-label">Group</label><input class="form-input" id="env-group" value="' + escA(group) + '" disabled></div>' +
        (options.length > 1
            ? '<div class="form-group"><label class="form-label">Preset</label>' + selectH('env-preset', options, presetKey || '', 'env-preset') + '</div>'
            : '') +
        '<div id="env-preset-helper" class="mb-12"></div>' +
        '<div class="form-group"><label class="form-label">Key</label>' + inputH('env-key', '', 'text', 'e.g. MY_API_KEY') + '</div>' +
        '<div class="form-group"><label class="form-label">Value</label><p class="text-sm text-secondary mb-8">Enter the actual API key or secret here.</p>' + inputH('env-value', '', 'text', 'Paste the real secret value') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" id="env-save-btn" onclick="saveNewEnvVar()">Save</button>'
    );
    const presetSelect = document.getElementById('env-preset');
    if (presetSelect) {
        presetSelect.addEventListener('change', window.envVarPresetChanged);
    }
    window.envVarPresetChanged();
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
        const metadata = data.metadata || {};
        const meta = metadata[key] || envVarMeta(key);
        const masked = val ? '•'.repeat(Math.min(val.length, 20)) + (val.length > 20 ? '…' : '') : '(empty)';
        showModal('Edit Variable: ' + key,
            '<div class="form-group"><label class="form-label">Key</label><input class="form-input" value="' + escA(key) + '" disabled></div>' +
            '<p class="text-sm text-secondary mb-12">' + escH(meta.description || 'Custom environment variable.') + '</p>' +
            (meta.default_value ? '<p class="text-sm text-secondary mb-12">Recommended default: <span class="font-mono">' + escH(meta.default_value) + '</span>. Leave this unset unless you need an override.</p>' : '') +
            '<div class="form-group"><label class="form-label">Current Value</label><div class="font-mono text-sm" style="color:var(--muted);padding:4px 0">' + escH(masked) + '</div></div>' +
            '<div class="form-group"><label class="form-label">New Value</label>' + inputH('env-edit-value', '', 'text', 'Enter new value') + '</div>',
            '<button class="btn" onclick="closeModal()">Cancel</button>' +
            (meta.default_value ? '<button class="btn" onclick="document.getElementById(\'env-edit-value\').value=\'' + escA(meta.default_value) + '\'">Use Default</button>' : '') +
            '<button class="btn btn-primary" onclick="saveEditEnvVar(\'' + escA(key) + '\')">Save</button>'
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
const PROVIDER_TYPE_OPTIONS = [
    { value: 'auto', label: 'Generic OpenAI-Compatible' },
    { value: 'openrouter', label: 'OpenRouter' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'azure', label: 'Azure OpenAI' },
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'groq', label: 'Groq' },
    { value: 'google', label: 'Google' },
    { value: 'mistral', label: 'Mistral' },
    { value: 'together', label: 'Together' },
    { value: 'fireworks', label: 'Fireworks' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'cohere', label: 'Cohere' },
];
const PROVIDER_ENV_VAR_MAP = {
    openrouter: 'OPENROUTER_API_KEY',
    openai: 'OPENAI_API_KEY',
    'openai-codex': 'OPENAI_API_KEY',
    azure: 'AZURE_OPENAI_API_KEY',
    anthropic: 'ANTHROPIC_API_KEY',
    groq: 'GROQ_API_KEY',
    google: 'GOOGLE_API_KEY',
    gemini: 'GOOGLE_API_KEY',
    mistral: 'MISTRAL_API_KEY',
    together: 'TOGETHER_API_KEY',
    fireworks: 'FIREWORKS_API_KEY',
    deepseek: 'DEEPSEEK_API_KEY',
    cohere: 'COHERE_API_KEY',
};
const MODEL_ROLE_META = {
    primary: {
        title: 'Primary Chat',
        description: 'The main provider profile and model Hermes uses for normal chat requests.',
        disableAllowed: false,
    },
    fallback: {
        title: 'Fallback Chat',
        description: 'A second provider profile and model the app retries automatically when the primary chat target has a retryable failure.',
        disableAllowed: true,
    },
    vision: {
        title: 'Vision',
        description: 'The provider profile and model used for screenshot analysis and other image-aware requests.',
        disableAllowed: true,
    },
};
const modelRoleDiscoveryCache = { models: {}, endpoints: {} };
window.modelRolesCache = null;
window.providerEnvCache = null;

function providerPresetConfig(kind) {
    const presets = {
        openrouter: {
            title: 'Add OpenRouter Profile',
            intro: 'Create a saved OpenRouter provider profile, then assign it to Primary Chat, Fallback Chat, or Vision in Model Roles.',
            name: 'openrouter',
            provider: 'openrouter',
            base_url: 'https://openrouter.ai/api/v1',
            model: '',
        },
        openai: {
            title: 'Add OpenAI Profile',
            intro: 'Create a saved OpenAI provider profile for direct model access.',
            name: 'openai',
            provider: 'openai',
            base_url: 'https://api.openai.com/v1',
            model: '',
        },
        local: {
            title: 'Add Local API Profile',
            intro: 'Create a saved OpenAI-compatible profile for a local or self-hosted server.',
            name: 'local-api',
            provider: 'auto',
            base_url: 'http://127.0.0.1:8000/v1',
            model: '',
        },
    };
    return presets[kind] || null;
}

function providerUsageBadgesH(usedBy) {
    if (!usedBy || !usedBy.length) return '<span class="text-muted">Not linked</span>';
    return usedBy.map(label => '<span class="badge badge-info">' + escH(label) + '</span>').join(' ');
}

async function loadModelRoles(force = false) {
    if (!force && window.modelRolesCache) return window.modelRolesCache;
    window.modelRolesCache = await api('GET', '/api/model-roles');
    return window.modelRolesCache;
}

async function loadEnvVarMap(force = false) {
    if (!force && window.providerEnvCache) return window.providerEnvCache;
    const data = await api('GET', '/api/env');
    window.providerEnvCache = data.vars || {};
    return window.providerEnvCache;
}

function providerTypeDefaultConfig(providerType) {
    const preset = providerPresetConfig(providerType);
    if (preset) return preset;
    return {
        name: providerType || '',
        base_url: '',
    };
}

function providerApiKeyRequirementText(providerType, envVars, hasSavedApiKey = false) {
    const envKey = PROVIDER_ENV_VAR_MAP[providerType];
    if (!envKey) return 'Optional. Leave blank if this profile does not need an API key.';
    if (hasSavedApiKey) return 'Optional here because this profile already has a saved API key. Leave it blank to keep the current secret.';
    if (envVars && envVars[envKey]) return 'Optional because ' + envKey + ' is already set in Env Vars.';
    return 'Required here or in Env Vars as ' + envKey + '.';
}

async function loadProviderDiscoveryModels(profileName, visionOnly = false, force = false) {
    const key = profileName + '|' + (visionOnly ? 'vision' : 'chat');
    if (!force && modelRoleDiscoveryCache.models[key]) return modelRoleDiscoveryCache.models[key];
    const query = visionOnly ? '?vision_only=1' : '';
    const data = await api('GET', '/api/providers/' + encodeURIComponent(profileName) + '/discovery/models' + query);
    modelRoleDiscoveryCache.models[key] = data;
    return data;
}

async function loadProviderDiscoveryEndpoints(profileName, modelId, force = false) {
    const key = profileName + '|' + modelId;
    if (!force && modelRoleDiscoveryCache.endpoints[key]) return modelRoleDiscoveryCache.endpoints[key];
    const data = await api('GET', '/api/providers/' + encodeURIComponent(profileName) + '/discovery/endpoints?model=' + encodeURIComponent(modelId));
    modelRoleDiscoveryCache.endpoints[key] = data;
    return data;
}

function modelRoleCardH(role, info) {
    const meta = MODEL_ROLE_META[role] || { title: role, description: '' };
    const enabled = role === 'primary' ? true : !!info?.enabled;
    const statusBadge = role === 'primary'
        ? '<span class="badge badge-success">Required</span>'
        : '<span class="badge ' + (enabled ? 'badge-info' : 'badge-secondary') + '">' + (enabled ? 'Enabled' : 'Disabled') + '</span>';
    const profileLabel = info?.profile || '(not linked)';
    const providerLabel = info?.provider_label || info?.provider || '(not set)';
    const modelLabel = info?.model || '(not set)';
    const routeLabel = info?.routing_provider || 'Auto';
    return '' +
        '<div class="card model-role-card">' +
            '<div class="card-header"><span>' + escH(meta.title) + '</span>' + statusBadge + '</div>' +
            '<div class="card-body">' +
                '<p class="text-sm text-secondary mb-16">' + escH(meta.description) + '</p>' +
                '<div class="model-role-meta">' +
                    '<div class="model-role-meta-item"><label class="form-label">Provider Profile</label><div class="font-mono text-sm">' + escH(profileLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">Provider Type</label><div class="text-sm">' + escH(providerLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">Model</label><div class="font-mono text-sm">' + escH(modelLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">OpenRouter Endpoint</label><div class="font-mono text-sm">' + escH(routeLabel) + '</div></div>' +
                '</div>' +
                '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">' +
                    '<button class="btn btn-primary" onclick="editModelRole(\'' + escA(role) + '\')">Edit</button>' +
                '</div>' +
            '</div>' +
        '</div>';
}

Screens.providers = async function () {
    const content = document.getElementById('content');
    try {
        const [data, chatStatus, roleData] = await Promise.all([
            api('GET', '/api/providers'),
            api('GET', '/api/chat/status').catch(() => null),
            loadModelRoles(true),
        ]);
        const def = data.default || {};
        const custom = data.custom || [];
        const readiness = chatStatus?.readiness || {};
        const screenshotReady = !!readiness.screenshots_ready;
        const screenshotReason = screenshotReady
            ? 'Pasted screenshots can be analyzed through the configured vision sidecar while Hermes CLI remains the main session.'
            : (chatStatus?.capability_reasons?.image_attachments || readiness.vision_reason || 'A vision model and reachable OpenAI-compatible API are required before screenshot paste can work.');
        const primaryRole = roleData?.roles?.primary || {};
        const visionRole = roleData?.roles?.vision || {};
        const availableProfiles = roleData?.profiles || [];
        const implicitProfiles = availableProfiles.filter(profile => !(custom || []).some(saved => saved.name === profile.name));

        let html = '<div class="card mb-16"><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">Provider Profiles store connection details like provider type, base URL, API key, and an optional suggested model. Model Roles decides which saved provider Hermes uses for Primary Chat, Fallback Chat, and Vision.</p>';
        html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
        html += '<button class="btn btn-primary" onclick="navigate(\'models\')">Open Model Roles</button>';
        html += '<button class="btn" onclick="navigate(\'env-vars\')">Open Env Vars</button>';
        html += '</div></div></div>';

        html += '<div class="section-header"><span>Quick Add</span></div>';
        html += '<div class="provider-grid mb-16">';
        ['openrouter', 'openai', 'local'].forEach(kind => {
            const preset = providerPresetConfig(kind);
            html += '<button class="provider-card" onclick="addProvider(\'' + escA(kind) + '\')">';
            html += '<div class="provider-icon">' + escH((preset?.label || kind).slice(0, 2).toUpperCase()) + '</div>';
            html += '<div class="provider-name">' + escH(preset?.label || kind) + '</div>';
            html += '</button>';
        });
        html += '</div>';

        html += '<div class="section-header"><span>Current Primary Chat Target</span></div>';
        html += '<div class="card mb-16"><div class="card-body"><div class="form-row">';
        html += '<div class="form-group"><label class="form-label">Provider Profile</label><div class="font-mono text-sm">' + escH(primaryRole.profile || def.profile || '(not linked)') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Provider Type</label><div class="text-sm">' + escH(primaryRole.provider_label || def.provider_label || def.provider || '(not set)') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Model</label><div class="font-mono text-sm">' + escH(primaryRole.model || def.model || '(not set)') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Base URL</label><div class="font-mono text-sm">' + escH(primaryRole.base_url || def.base_url || '(not set)') + '</div></div>';
        html += '</div><div style="display:flex;gap:8px;flex-wrap:wrap">';
        html += '<button class="btn btn-primary" onclick="editModelRole(\'primary\')">Edit Primary Chat</button>';
        html += '<button class="btn" onclick="editModelRole(\'fallback\')">Edit Fallback Chat</button>';
        html += '</div></div></div>';

        html += '<div class="section-header"><span>Vision Chat Readiness</span></div>';
        html += '<div class="card mb-16"><div class="card-header"><span>Screenshot Paste</span><span class="badge ' + (screenshotReady ? 'badge-success' : 'badge-danger') + '">' + (screenshotReady ? 'Ready' : 'Not Ready') + '</span></div><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">' + escH(screenshotReason) + '</p>';
        html += '<div class="form-row">';
        html += '<div class="form-group"><label class="form-label">Provider Profile</label><div class="font-mono text-sm">' + escH(visionRole.profile || '(not linked)') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Vision Provider</label><div class="text-sm">' + escH(visionRole.provider_label || visionRole.provider || 'auto') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Vision Model</label><div class="font-mono text-sm">' + escH(readiness.vision_model || visionRole.model || '(not set)') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Vision API URL</label><div class="font-mono text-sm">' + escH(readiness.vision_api_url || visionRole.base_url || chatStatus?.api_url || '(not set)') + '</div></div>';
        html += '</div>';
        html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
        html += '<button class="btn btn-primary" onclick="editModelRole(\'vision\')">Edit Vision Role</button>';
        html += '<button class="btn" onclick="chatRefreshCapabilities(); Screens.providers();">Refresh Readiness</button>';
        html += '</div></div></div>';
        if (!screenshotReady) {
            html += '<div class="card mb-16"><div class="card-header"><span>Quick Setup</span></div><div class="card-body">';
            html += '<p class="text-sm text-secondary mb-16">Start by creating a provider profile, then assign it to the Vision role in Model Roles.</p>';
            html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
            html += '<button class="btn btn-primary" onclick="startVisionWizard(\'openrouter\')">Add OpenRouter Profile</button>';
            html += '<button class="btn" onclick="startVisionWizard(\'openai\')">Add OpenAI Profile</button>';
            html += '<button class="btn" onclick="startVisionWizard(\'local\')">Add Local API Profile</button>';
            html += '<button class="btn" onclick="editModelRole(\'vision\')">Open Vision Role</button>';
            html += '</div></div></div>';
        }

        if (implicitProfiles.length) {
            html += '<div class="card mb-16"><div class="card-header"><span>Profiles From Current Hermes Config</span></div><div class="card-body">';
            html += '<p class="text-sm text-secondary mb-16">Hermes already has active provider targets that Model Roles can use immediately. They are shown here even if you have not saved them as standalone provider profiles yet.</p>';
            html += '<div class="table-container"><table class="table"><thead><tr><th>Name</th><th>Type</th><th>Base URL</th><th>Suggested Model</th><th>Used By</th></tr></thead><tbody>';
            implicitProfiles.forEach(p => {
                html += '<tr><td class="font-mono text-sm">' + escH(p.name) + '</td><td class="text-sm">' + escH(p.provider_label || p.provider || '') + '</td><td class="text-sm">' + escH(p.base_url || '') + '</td><td class="font-mono text-sm">' + escH(p.model || '') + '</td><td>' + providerUsageBadgesH(p.used_by) + '</td></tr>';
            });
            html += '</tbody></table></div></div></div>';
        }

        html += '<div class="section-header"><span>Provider Profiles</span><button class="btn btn-primary" onclick="addProvider()">+ Add Provider</button></div>';
        if (custom.length === 0) {
            html += '<div class="empty-state"><p>No provider profiles configured yet</p></div>';
        } else {
            html += '<div class="table-container"><table class="table"><thead><tr><th>Name</th><th>Type</th><th>Base URL</th><th>Suggested Model</th><th>Used By</th><th style="width:180px">Actions</th></tr></thead><tbody>';
            custom.forEach(p => {
                html += '<tr><td class="font-mono text-sm">' + escH(p.name) + '</td><td class="text-sm">' + escH(p.provider_label || p.provider || '') + '</td><td class="text-sm">' + escH(p.base_url || '') + '</td><td class="font-mono text-sm">' + escH(p.model || '') + '</td><td>' + providerUsageBadgesH(p.used_by) + '</td>';
                html += '<td class="actions"><button class="btn btn-sm" onclick="editProvider(\'' + escA(p.name) + '\')">Edit</button> <button class="btn btn-sm" onclick="testProvider(this, \'' + escA(p.name) + '\')">Test</button> <button class="btn btn-sm btn-danger" onclick="deleteProvider(\'' + escA(p.name) + '\')">Delete</button></td></tr>';
            });
            html += '</tbody></table></div>';
        }
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

async function loadProviderTypeDiscoveryModels(providerType, force = false) {
    const key = 'provider-type|' + providerType;
    if (!force && modelRoleDiscoveryCache.models[key]) return modelRoleDiscoveryCache.models[key];
    const data = await api('GET', '/api/provider-types/' + encodeURIComponent(providerType) + '/discovery/models');
    modelRoleDiscoveryCache.models[key] = data;
    return data;
}

async function validateProviderCredentials(providerType, apiKey, hasSavedApiKey = false) {
    const envKey = PROVIDER_ENV_VAR_MAP[providerType];
    if (!envKey) return;
    const envVars = await loadEnvVarMap();
    if (!apiKey && !hasSavedApiKey && !envVars[envKey]) {
        throw new Error('API key is required here or in Env Vars as ' + envKey);
    }
}

function applyProviderModalDefaults(config) {
    const typeSelect = document.getElementById('prov-type');
    const nameInput = document.getElementById('prov-name');
    const urlInput = document.getElementById('prov-url');
    if (!typeSelect || !nameInput || !urlInput) return;
    const providerType = typeSelect.value || 'auto';
    const defaults = providerTypeDefaultConfig(providerType);
    const canAutoName = !config.nameReadonly && (!nameInput.dataset.userEdited || !nameInput.value || nameInput.value === (nameInput.dataset.autoValue || ''));
    const canAutoUrl = !urlInput.dataset.userEdited || !urlInput.value || urlInput.value === (urlInput.dataset.autoValue || '');
    if (canAutoName && defaults.name) {
        nameInput.value = defaults.name;
        nameInput.dataset.autoValue = defaults.name;
    }
    if (canAutoUrl && defaults.base_url) {
        urlInput.value = defaults.base_url;
        urlInput.dataset.autoValue = defaults.base_url;
    }
}

async function refreshProviderModalState(forceDiscovery = false) {
    const state = window.providerModalState || {};
    const typeSelect = document.getElementById('prov-type');
    const modelInput = document.getElementById('prov-model');
    const modelDiscovery = document.getElementById('prov-model-discovery');
    const apiHint = document.getElementById('prov-api-key-hint');
    const apiInput = document.getElementById('prov-api-key');
    if (!typeSelect || !modelInput || !modelDiscovery || !apiHint || !apiInput) return;
    applyProviderModalDefaults(state.config || {});
    const providerType = typeSelect.value || 'auto';
    const envVars = await loadEnvVarMap().catch(() => ({}));
    apiHint.textContent = providerApiKeyRequirementText(providerType, envVars, !!state.config?.hasSavedApiKey);
    apiInput.placeholder = state.config?.hasSavedApiKey
        ? 'Leave blank to keep current secret'
        : (PROVIDER_ENV_VAR_MAP[providerType] ? 'Leave blank if ' + PROVIDER_ENV_VAR_MAP[providerType] + ' is already set' : 'Optional API key');

    if (providerType !== 'openrouter') {
        modelDiscovery.innerHTML = '';
        return;
    }

    modelDiscovery.innerHTML = '<div class="text-sm text-secondary mb-12">Loading OpenRouter models…</div>';
    try {
        const discovery = await loadProviderTypeDiscoveryModels(providerType, forceDiscovery);
        const currentModel = (modelInput.value || '').trim();
        const options = [{ value: '', label: 'Choose from OpenRouter' }];
        (discovery.models || []).forEach(item => {
            const badge = item.supports_image ? ' [image]' : '';
            options.push({ value: item.id, label: item.id + badge });
        });
        if (currentModel && !options.find(option => option.value === currentModel)) {
            options.push({ value: currentModel, label: currentModel + ' (current)' });
        }
        modelDiscovery.innerHTML = '' +
            '<div class="form-group">' +
                '<label class="form-label">OpenRouter Model List</label>' +
                selectH('prov-model-select', options, currentModel) +
                '<div class="text-xs text-secondary mt-8">Pick a live OpenRouter model, or type a custom suggested model below.</div>' +
            '</div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:-4px;margin-bottom:12px">' +
                '<button class="btn btn-sm" onclick="refreshProviderModalState(true)">Refresh Model List</button>' +
            '</div>';
        const modelSelect = document.getElementById('prov-model-select');
        if (modelSelect) {
            modelSelect.addEventListener('change', function () {
                if (this.value) modelInput.value = this.value;
            });
        }
    } catch (e) {
        modelDiscovery.innerHTML = '<div class="text-sm text-danger mb-12">Could not load OpenRouter models: ' + escH(e.message) + '</div>';
    }
}

window.refreshProviderModalState = refreshProviderModalState;

window.providerModal = function (opts) {
    const config = opts || {};
    window.providerModalState = { config };
    const body = '' +
        (config.intro ? '<p class="text-sm text-secondary mb-16">' + escH(config.intro) + '</p>' : '') +
        '<div class="form-group"><label class="form-label">Profile Name</label>' + inputH('prov-name', config.name || '', 'text', 'e.g. openrouter-prod', config.nameReadonly ? 'disabled' : '') + '</div>' +
        '<div class="form-group"><label class="form-label">Provider Type</label>' + selectH('prov-type', PROVIDER_TYPE_OPTIONS, config.provider || 'auto') + '</div>' +
        '<div class="form-group"><label class="form-label">Base URL</label>' + inputH('prov-url', config.base_url || '', 'url', 'https://api.example.com/v1') + '</div>' +
        '<div id="prov-model-discovery"></div>' +
        '<div class="form-group"><label class="form-label">Suggested Model <span class="form-label-hint">(optional)</span></label>' + inputH('prov-model', config.model || '', 'text', 'e.g. openai/gpt-4o') + '</div>' +
        '<div class="form-group"><label class="form-label">API Key <span class="form-label-hint">(optional)</span></label>' + inputH('prov-api-key', '', 'password', config.hasSavedApiKey ? 'Leave blank to keep current secret' : 'Optional API key') + '<div class="form-hint" id="prov-api-key-hint">Checking API key requirements…</div></div>';
    showModal(
        config.title || 'Provider Profile',
        body,
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="' + config.saveFn + '">Save</button>'
    );
    const nameInput = document.getElementById('prov-name');
    const urlInput = document.getElementById('prov-url');
    const typeSelect = document.getElementById('prov-type');
    if (nameInput && !config.nameReadonly) {
        nameInput.addEventListener('input', function () { this.dataset.userEdited = '1'; });
    }
    if (urlInput) {
        urlInput.addEventListener('input', function () { this.dataset.userEdited = '1'; });
    }
    if (typeSelect) {
        typeSelect.addEventListener('change', function () {
            refreshProviderModalState();
        });
    }
    refreshProviderModalState();
};
window.addProvider = function (presetKind = null) {
    const preset = presetKind ? providerPresetConfig(presetKind) : null;
    window.providerModal({
        title: preset?.title || 'Add Provider Profile',
        intro: preset?.intro || 'Create a reusable provider profile that Model Roles can reference for Primary Chat, Fallback Chat, and Vision.',
        name: preset?.name || '',
        provider: preset?.provider || 'auto',
        base_url: preset?.base_url || '',
        model: preset?.model || '',
        saveFn: 'saveNewProvider()',
    });
};
window.editProvider = async function (name) {
    try {
        const data = await api('GET', '/api/providers');
        const p = (data.custom || []).find(x => x.name === name);
        if (!p) { toast('Provider not found', 'error'); return; }
        window.providerModal({
            title: 'Edit Provider Profile: ' + name,
            intro: 'Update the saved provider profile. Linked Model Roles will keep pointing at this profile.',
            name: p.name,
            provider: p.provider || 'auto',
            base_url: p.base_url || '',
            model: p.model || '',
            hasSavedApiKey: !!p.has_api_key,
            nameReadonly: true,
            saveFn: 'saveEditProvider()',
        });
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveNewProvider = async function () {
    const name = document.getElementById('prov-name').value.trim();
    const provider = document.getElementById('prov-type').value.trim() || 'auto';
    const base_url = document.getElementById('prov-url').value.trim();
    const model = document.getElementById('prov-model').value.trim();
    const api_key = document.getElementById('prov-api-key').value;
    if (!name) { toast('Profile name is required', 'error'); return; }
    const payload = { name, provider, base_url, model };
    if (api_key) payload.api_key = api_key;
    try {
        await validateProviderCredentials(provider, api_key, false);
        await api('POST', '/api/providers', payload);
        window.modelRolesCache = null;
        toast('Provider profile added', 'success');
        closeModal();
        Screens.providers();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveEditProvider = async function () {
    const name = document.getElementById('prov-name').value.trim();
    const provider = document.getElementById('prov-type').value.trim() || 'auto';
    const base_url = document.getElementById('prov-url').value.trim();
    const model = document.getElementById('prov-model').value.trim();
    const api_key = document.getElementById('prov-api-key').value;
    const hasSavedApiKey = !!window.providerModalState?.config?.hasSavedApiKey;
    const payload = { provider, base_url, model };
    if (api_key) payload.api_key = api_key;
    try {
        await validateProviderCredentials(provider, api_key, hasSavedApiKey);
        await api('PUT', '/api/providers/' + name, payload);
        window.modelRolesCache = null;
        toast('Provider profile updated', 'success');
        closeModal();
        Screens.providers();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.deleteProvider = function (name) {
    showModal('Delete Provider Profile', '<p>Delete provider profile <strong>' + escH(name) + '</strong>?</p>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="doDeleteProvider(\'' + escA(name) + '\')">Delete</button>'
    );
};
window.doDeleteProvider = async function (name) {
    try {
        await api('DELETE', '/api/providers/' + name);
        window.modelRolesCache = null;
        toast('Provider profile deleted', 'success');
        closeModal();
        Screens.providers();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.testProvider = async function (btn, name) {
    toast('Testing ' + name + '...', 'info', 2000);
    try {
        setBtnLoading(btn, true);
        const r = await api('POST', '/api/providers/' + name + '/test');
        toast(r.ok ? 'Connection OK (' + (r.latency_ms || '?') + 'ms)' : 'Connection failed: ' + (r.error || ''), r.ok ? 'success' : 'error');
    } catch (e) { toast('Test failed: ' + e.message, 'error'); }
    finally { setBtnLoading(btn, false); }
};

window.startVisionWizard = function (kind) {
    navigate('providers');
    setTimeout(function () { addProvider(kind); }, 50);
};

window.editAuxProvider = function (purpose, presetKind = null) {
    if (purpose !== 'vision') {
        toast('Only the Vision role is editable from this shortcut right now', 'warning');
        return;
    }
    if (presetKind) {
        startVisionWizard(presetKind);
        return;
    }
    navigate('models');
    setTimeout(function () { editModelRole('vision'); }, 50);
};

// ── MODELS ─────────────────────────────────────────────────
function modelRoleProfileOptions(profiles) {
    return [{ value: '', label: profiles.length ? 'Choose a provider profile' : 'No provider profiles available yet' }]
        .concat((profiles || []).map(p => ({ value: p.name, label: p.name + ' — ' + (p.provider_label || p.provider || 'Provider') })));
}

function modelRoleSummaryH(profile) {
    if (!profile) return '<p class="text-sm text-secondary mb-16">Select a provider profile from your saved profiles or current Hermes config to choose a model.</p>';
    return '' +
        '<div class="card mb-16"><div class="card-body">' +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Provider Type</label><div class="text-sm">' + escH(profile.provider_label || profile.provider || '') + '</div></div>' +
                '<div class="form-group"><label class="form-label">Base URL</label><div class="font-mono text-sm">' + escH(profile.base_url || '(not set)') + '</div></div>' +
                '<div class="form-group"><label class="form-label">Suggested Model</label><div class="font-mono text-sm">' + escH(profile.model || '(none)') + '</div></div>' +
            '</div>' +
        '</div></div>';
}

async function renderModelRoleEditor(role, forceDiscovery = false) {
    const modalBody = document.getElementById('modal-body');
    const state = window.modelRoleEditorState || {};
    if (!modalBody || state.role !== role) return;
    const profiles = state.profiles || [];
    const currentRole = state.currentRole || {};
    const profileName = (document.getElementById('role-profile')?.value || '').trim();
    const profile = profiles.find(item => item.name === profileName) || null;
    const summary = document.getElementById('role-profile-summary');
    const modelField = document.getElementById('role-model-fields');
    const routingField = document.getElementById('role-routing-fields');
    if (summary) summary.innerHTML = modelRoleSummaryH(profile);
    if (!modelField || !routingField) return;

    const currentModel = (document.getElementById('role-model')?.value || currentRole.model || profile?.model || '').trim();
    let modelHtml = '<div class="form-group"><label class="form-label">Model ID</label>' + inputH('role-model', currentModel, 'text', role === 'vision' ? 'Enter a vision-capable model ID' : 'Enter model ID') + '</div>';
    let modelSelectMarkup = '';

    if (profile && String(profile.provider || '').toLowerCase() === 'openrouter') {
        modelHtml += '<div class="text-sm text-secondary mb-12">OpenRouter live model discovery is available for this profile.</div>';
        modelField.innerHTML = modelHtml + '<div class="text-sm text-secondary">Loading OpenRouter models…</div>';
        try {
            const discovery = await loadProviderDiscoveryModels(profile.name, role === 'vision', forceDiscovery);
            const options = [{ value: '', label: 'Choose from OpenRouter' }];
            const models = discovery.models || [];
            models.forEach(item => {
                const badge = item.supports_image ? ' [image]' : '';
                options.push({ value: item.id, label: item.id + badge });
            });
            if (currentModel && !models.find(item => item.id === currentModel)) {
                options.push({ value: currentModel, label: currentModel + ' (current)' });
            }
            modelSelectMarkup = '' +
                '<div class="form-group">' +
                    '<label class="form-label">OpenRouter Model List</label>' +
                    selectH('role-model-select', options, currentModel) +
                    '<div class="text-xs text-secondary mt-8">' + escH(role === 'vision' ? 'Showing only models that advertise image input support.' : 'Choose from OpenRouter’s live model catalog, or type a custom model ID below.') + '</div>' +
                '</div>' +
                '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:-4px;margin-bottom:12px">' +
                    '<button class="btn btn-sm" onclick="renderModelRoleEditor(\'' + escA(role) + '\', true)">Refresh Model List</button>' +
                '</div>';
        } catch (e) {
            modelSelectMarkup = '<div class="text-sm text-danger mb-12">Could not load OpenRouter models: ' + escH(e.message) + '</div>';
        }
    }
    modelField.innerHTML = modelSelectMarkup + modelHtml;

    const modelInput = document.getElementById('role-model');
    const modelSelect = document.getElementById('role-model-select');
    if (modelSelect && modelInput) {
        modelSelect.addEventListener('change', function () {
            if (this.value) modelInput.value = this.value;
            renderModelRoleRouting(role);
        });
    }
    if (modelInput) {
        modelInput.addEventListener('change', function () { renderModelRoleRouting(role); });
        modelInput.addEventListener('input', function () { renderModelRoleRouting(role); });
    }

    await renderModelRoleRouting(role, forceDiscovery);
}

async function renderModelRoleRouting(role, forceDiscovery = false) {
    const state = window.modelRoleEditorState || {};
    const routingField = document.getElementById('role-routing-fields');
    if (!routingField || state.role !== role) return;
    const profileName = (document.getElementById('role-profile')?.value || '').trim();
    const profile = (state.profiles || []).find(item => item.name === profileName) || null;
    const currentRole = state.currentRole || {};
    const modelId = (document.getElementById('role-model')?.value || '').trim();
    if (!profile || String(profile.provider || '').toLowerCase() !== 'openrouter') {
        routingField.innerHTML = '';
        return;
    }
    if (!modelId) {
        routingField.innerHTML = '<div class="text-sm text-secondary mb-12">Choose a model first to load the available OpenRouter endpoint providers.</div>';
        return;
    }
    routingField.innerHTML = '<div class="text-sm text-secondary mb-12">Loading OpenRouter endpoint providers…</div>';
    try {
        const discovery = await loadProviderDiscoveryEndpoints(profile.name, modelId, forceDiscovery);
        const endpoints = discovery.endpoints || [];
        const options = [{ value: '', label: 'Auto route within OpenRouter' }];
        endpoints.forEach(item => {
            const uptime = item.uptime_last_30m != null ? ' · ' + Number(item.uptime_last_30m).toFixed(1) + '% uptime' : '';
            const label = (item.provider_name || item.tag || 'Provider') + (item.tag ? ' (' + item.tag + ')' : '') + uptime;
            options.push({ value: item.tag || item.provider_name || '', label });
        });
        const selected = (document.getElementById('role-routing-provider')?.value || currentRole.routing_provider || '').trim();
        if (selected && !options.find(option => option.value === selected)) {
            options.push({ value: selected, label: selected + ' (current)' });
        }
        routingField.innerHTML = '' +
            '<div class="form-group">' +
                '<label class="form-label">Preferred OpenRouter Endpoint</label>' +
                selectH('role-routing-provider', options, selected) +
                '<div class="text-xs text-secondary mt-8">This biases OpenRouter toward one endpoint provider while still allowing OpenRouter routing. Leave it on Auto for normal routing.</div>' +
            '</div>' +
            '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:-4px">' +
                '<button class="btn btn-sm" onclick="renderModelRoleRouting(\'' + escA(role) + '\', true)">Refresh Endpoint List</button>' +
            '</div>';
    } catch (e) {
        routingField.innerHTML = '<div class="text-sm text-danger mb-12">Could not load OpenRouter endpoint providers: ' + escH(e.message) + '</div>';
    }
}

window.editModelRole = async function (role) {
    try {
        const data = await loadModelRoles(true);
        const profiles = data.profiles || [];
        const currentRole = data.roles?.[role];
        const meta = MODEL_ROLE_META[role];
        if (!meta) { toast('Unknown model role', 'error'); return; }
        if (!profiles.length) {
            showModal(
                'No Provider Profiles Yet',
                '<p class="text-sm text-secondary mb-16">Create at least one provider profile in Providers before assigning it to ' + escH(meta.title) + '.</p>',
                '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="closeModal(); navigate(\'providers\'); setTimeout(function(){ addProvider(); }, 50)">Add Provider Profile</button>'
            );
            return;
        }
        window.modelRoleEditorState = { role, profiles, currentRole };
        showModal(
            meta.title,
            '<p class="text-sm text-secondary mb-16">' + escH(meta.description) + '</p>' +
            '<div class="form-group"><label class="form-label">Provider Profile</label>' + selectH('role-profile', modelRoleProfileOptions(profiles), currentRole?.profile || '') + '</div>' +
            '<div id="role-profile-summary"></div>' +
            '<div id="role-model-fields"></div>' +
            '<div id="role-routing-fields"></div>',
            '<button class="btn" onclick="closeModal()">Cancel</button>' +
            (meta.disableAllowed ? '<button class="btn" onclick="disableModelRole(\'' + escA(role) + '\')">Disable</button>' : '') +
            '<button class="btn btn-primary" onclick="saveModelRole(\'' + escA(role) + '\')">Save</button>'
        );
        const profileSelect = document.getElementById('role-profile');
        if (profileSelect) {
            profileSelect.addEventListener('change', function () {
                renderModelRoleEditor(role);
            });
        }
        await renderModelRoleEditor(role);
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

window.saveModelRole = async function (role) {
    const profile = (document.getElementById('role-profile')?.value || '').trim();
    const model = (document.getElementById('role-model')?.value || '').trim();
    const routing_provider = (document.getElementById('role-routing-provider')?.value || '').trim();
    if (role === 'primary' && (!profile || !model)) {
        toast('Primary Chat requires both a provider profile and a model', 'error');
        return;
    }
    try {
        await api('PUT', '/api/model-roles/' + role, { profile, model, routing_provider });
        window.modelRolesCache = null;
        toast(MODEL_ROLE_META[role].title + ' updated', 'success');
        closeModal();
        if (role === 'vision') await chatRefreshCapabilities();
        Screens.models();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

window.disableModelRole = async function (role) {
    if (role === 'primary') return;
    try {
        await api('PUT', '/api/model-roles/' + role, { profile: '', model: '', routing_provider: '' });
        window.modelRolesCache = null;
        toast(MODEL_ROLE_META[role].title + ' disabled', 'success');
        closeModal();
        if (role === 'vision') await chatRefreshCapabilities();
        Screens.models();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

Screens.models = async function () {
    const content = document.getElementById('content');
    try {
        const data = await loadModelRoles(true);
        const roles = data.roles || {};
        const profiles = data.profiles || [];
        let html = '<div class="card mb-16"><div class="card-body">';
        html += '<p class="text-sm text-secondary">Model Roles decide which saved provider profile and model Hermes uses for primary chat, fallback chat, and vision. Provider Profiles live in the Providers screen; this screen is where you assign them.</p>';
        html += '</div></div>';
        html += '<div class="model-role-grid">';
        ['primary', 'fallback', 'vision'].forEach(role => { html += modelRoleCardH(role, roles[role] || {}); });
        html += '</div>';

        html += '<div class="card mt-16"><div class="card-header"><span>Saved Provider Profiles</span><button class="btn btn-primary" onclick="navigate(\'providers\')">Open Providers</button></div>';
        if (!profiles.length) {
            html += '<div class="card-body"><p class="text-sm text-secondary">No provider profiles are saved yet.</p></div></div>';
        } else {
            html += '<div class="table-container"><table class="table"><thead><tr><th>Name</th><th>Type</th><th>Suggested Model</th><th>Used By</th></tr></thead><tbody>';
            profiles.forEach(profile => {
                html += '<tr><td class="font-mono text-sm">' + escH(profile.name) + '</td><td class="text-sm">' + escH(profile.provider_label || profile.provider || '') + '</td><td class="font-mono text-sm">' + escH(profile.model || '') + '</td><td>' + providerUsageBadgesH(profile.used_by) + '</td></tr>';
            });
            html += '</tbody></table></div></div>';
        }
        content.innerHTML = html;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
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
function starterPackBadgeClass(status) {
    if (status === 'ready') return 'badge-success';
    if (status === 'attention') return 'badge-warning';
    return 'badge-secondary';
}

function starterPackBadgeLabel(status) {
    if (status === 'ready') return 'Ready';
    if (status === 'attention') return 'Needs Setup';
    return 'Missing';
}

const starterPackState = {
    items: {},
};

function renderStarterPackGrid(items) {
    const list = Array.isArray(items) ? items : [];
    starterPackState.items = {};
    list.forEach(item => {
        if (item && item.id) starterPackState.items[item.id] = item;
    });
    if (!list.length) {
        return '<div class="empty-state"><p>No starter-pack recommendations available right now.</p></div>';
    }
    return '<div class="starter-pack-grid">' + list.map(item =>
        '<div class="starter-pack-item">' +
            '<div class="starter-pack-item-top">' +
                '<div class="starter-pack-item-title">' + escH(item.label || item.id || 'Item') + '</div>' +
                '<span class="badge ' + starterPackBadgeClass(item.status) + '">' + escH(starterPackBadgeLabel(item.status)) + '</span>' +
            '</div>' +
            '<div class="starter-pack-item-kind">' + escH((item.kind || 'runtime').replace(/_/g, ' ')) + '</div>' +
            '<div class="starter-pack-item-detail">' + escH(item.detail || '') + '</div>' +
            '<div class="starter-pack-item-actions">' +
                ((item.setup_action && item.setup_action.type === 'env_var')
                    ? '<button class="btn btn-sm btn-primary" onclick="starterPackRunSetup(\'' + escA(item.id) + '\')">' + escH((item.setup_action || {}).label || 'Set Up') + '</button>'
                    : '') +
                ((item.supports_install && item.install_available !== false)
                    ? '<button class="btn btn-sm btn-primary" onclick="starterPackInstallPrompt(\'' + escA(item.id) + '\')">' + escH(item.install_action_label || 'Install') + '</button>'
                    : '') +
                ((Array.isArray(item.setup_notes) && item.setup_notes.length)
                    ? '<button class="btn btn-sm" onclick="starterPackShowNotes(\'' + escA(item.id) + '\')">' + (item.status === 'missing' ? 'Preview Setup' : 'Setup Notes') + '</button>'
                    : '') +
            '</div>' +
        '</div>'
    ).join('') + '</div>';
}

window.starterPackShowNotes = function (itemId) {
    const item = starterPackState.items[itemId];
    if (!item) {
        toast('Starter-pack item not found', 'error');
        return;
    }
    const notes = Array.isArray(item.setup_notes) ? item.setup_notes : [];
    const issues = Array.isArray(item.issues) ? item.issues : [];
    let html = '<p class="text-sm text-secondary mb-16">' + escH(item.label || itemId) + '</p>';
    if (issues.length) {
        html += '<div class="card mb-16"><div class="card-header"><span>Still Needed</span></div><div class="card-body"><ul class="plain-list">' +
            issues.map(issue => '<li>' + escH(issue) + '</li>').join('') +
            '</ul></div></div>';
    }
    if (notes.length) {
        html += '<div class="card"><div class="card-header"><span>Setup Notes</span></div><div class="card-body"><ul class="plain-list">' +
            notes.map(note => '<li>' + escH(note) + '</li>').join('') +
            '</ul></div></div>';
    } else {
        html += '<div class="empty-state"><p>No extra setup notes for this item.</p></div>';
    }
    showModal(
        item.label || 'Starter Pack Item',
        html,
        '<button class="btn" onclick="closeModal()">Close</button>' +
        ((item.setup_action && item.setup_action.type === 'env_var')
            ? '<button class="btn btn-primary" onclick="starterPackRunSetup(\'' + escA(item.id) + '\')">' + escH((item.setup_action || {}).label || 'Set Up') + '</button>'
            : '') +
        ((item.supports_install && item.install_available !== false)
            ? '<button class="btn btn-primary" onclick="starterPackInstallPrompt(\'' + escA(item.id) + '\')">' + escH(item.install_action_label || 'Install') + '</button>'
            : '')
    );
};

window.starterPackRunSetup = async function (itemId) {
    const item = starterPackState.items[itemId];
    if (!item) {
        toast('Starter-pack item not found', 'error');
        return;
    }
    const action = item.setup_action || {};
    if (action.type !== 'env_var' || !action.key) {
        starterPackShowNotes(itemId);
        return;
    }
    closeModal();
    if (action.key === 'OPENAI_API_KEY') {
        showModal(
            action.label || 'Set Up Memory',
            '<p class="text-sm text-secondary mb-16">Hermes already stores memory locally. Adding your OpenAI API key is optional and only turns on stronger OpenAI-backed semantic memory search.</p>' +
            '<p class="text-sm text-secondary mb-16">For standard OpenAI use, you do not need to set <span class="font-mono">OPENAI_BASE_URL</span>. Leave that unset unless you are using a custom OpenAI-compatible gateway.</p>' +
            '<div class="form-group"><label class="form-label">OPENAI_API_KEY</label>' + inputH('starter-memory-openai-key', '', 'text', 'sk-...') + '</div>',
            '<button class="btn" onclick="closeModal()">Cancel</button>' +
            '<button class="btn" onclick="closeModal(); navigate(\'env-vars\')">Advanced Env Vars</button>' +
            '<button class="btn btn-primary" onclick="saveStarterPackMemoryKey()">Save</button>'
        );
        return;
    }
    addEnvVar('Provider', action.key);
};

window.saveStarterPackMemoryKey = async function () {
    const value = document.getElementById('starter-memory-openai-key')?.value?.trim() || '';
    if (!value) {
        toast('OpenAI API key is required', 'error');
        return;
    }
    try {
        await api('PUT', '/api/env/OPENAI_API_KEY', { value });
        toast('OpenAI memory search key saved. New CLI turns can use it right away.', 'success');
        closeModal();
        Screens.skills();
    } catch (e) {
        toast('Error: ' + e.message, 'error');
    }
};

window.starterPackInstallPrompt = function (itemId) {
    const item = starterPackState.items[itemId];
    if (!item) {
        toast('Starter-pack item not found', 'error');
        return;
    }
    const candidates = Array.isArray(item.install_candidates) ? item.install_candidates : [];
    if (!candidates.length) {
        toast('No install targets available for this item', 'warning');
        return;
    }
    let html = '<p class="text-sm text-secondary mb-16">Choose the install target for ' + escH(item.label || itemId) + '.</p>';
    html += '<div class="starter-pack-candidate-list">' + candidates.map(candidate =>
        '<div class="starter-pack-candidate">' +
            '<div class="starter-pack-candidate-top">' +
                '<div>' +
                    '<div class="starter-pack-candidate-title">' + escH(candidate.label || candidate.identifier) + '</div>' +
                    '<div class="starter-pack-candidate-meta">' + escH(candidate.source || 'registry') + ' · <span class="font-mono">' + escH(candidate.identifier) + '</span></div>' +
                '</div>' +
                (candidate.recommended ? '<span class="badge badge-info">Recommended</span>' : '') +
            '</div>' +
            '<div class="starter-pack-candidate-detail">' + escH(candidate.description || '') + '</div>' +
            '<div class="starter-pack-candidate-actions"><button class="btn btn-sm btn-primary" onclick="starterPackInstall(\'' + escA(item.id) + '\', \'' + escA(candidate.identifier) + '\')">Install This</button></div>' +
        '</div>'
    ).join('') + '</div>';
    if (Array.isArray(item.setup_notes) && item.setup_notes.length) {
        html += '<div class="card mt-16"><div class="card-header"><span>After Install</span></div><div class="card-body"><ul class="plain-list">' +
            item.setup_notes.map(note => '<li>' + escH(note) + '</li>').join('') +
            '</ul></div></div>';
    }
    showModal(
        'Install ' + (item.label || itemId),
        html,
        '<button class="btn" onclick="closeModal()">Cancel</button>'
    );
};

window.starterPackInstall = async function (itemId, identifier) {
    const item = starterPackState.items[itemId];
    const label = item && item.label ? item.label : itemId;
    toast('Installing ' + label + '...', 'info', 2500);
    try {
        const resp = await api('POST', '/api/starter-pack/' + encodeURIComponent(itemId) + '/install', {
            identifier: identifier || '',
        });
        closeModal();
        let html = '<p class="text-sm text-secondary mb-16">' + escH((resp.candidate || {}).label || label) + ' installed.</p>';
        const setupNotes = Array.isArray(resp.setup_notes) ? resp.setup_notes : [];
        if (setupNotes.length) {
            html += '<div class="card mb-16"><div class="card-header"><span>Next Steps</span></div><div class="card-body"><ul class="plain-list">' +
                setupNotes.map(note => '<li>' + escH(note) + '</li>').join('') +
                '</ul></div></div>';
        }
        if (resp.output) {
            html += '<div class="card"><div class="card-header"><span>Hermes Output</span></div><div class="card-body"><pre class="font-mono text-xs" style="max-height:260px;overflow:auto">' + escH(resp.output) + '</pre></div></div>';
        }
        showModal(
            'Installed ' + label,
            html,
            '<button class="btn btn-primary" onclick="closeModal()">Close</button>'
        );
        toast(label + ' installed', 'success');
        Screens.skills();
    } catch (e) {
        toast('Install failed: ' + e.message, 'error');
    }
};

function renderRuntimeReadinessCard(runtime, policy) {
    const memory = (runtime && runtime.memory) || {};
    const integrations = (runtime && runtime.integrations) || {};
    const skills = (runtime && runtime.skills) || {};
    const hooks = (runtime && runtime.hooks) || {};
    const reasons = Array.isArray(policy?.reasons) ? policy.reasons : [];
    let html = '<div class="card mb-16"><div class="card-header"><span>Runtime Readiness</span></div><div class="card-body">';
    html += '<div class="runtime-readiness-grid">';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Transport</div><div class="runtime-readiness-value">' + escH(policy?.requires_cli ? 'CLI Required' : 'API Allowed') + '</div><div class="runtime-readiness-detail">' + escH(policy?.reason || 'Hermes API replay is available when you want it.') + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Memory</div><div class="runtime-readiness-value">' + escH(memory.enabled ? (memory.semantic_search_ready ? 'OpenAI Ready' : 'Enabled') : 'Off') + '</div><div class="runtime-readiness-detail">' + escH(memory.detail || 'Memory status unavailable.') + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Skills</div><div class="runtime-readiness-value">' + escH(String(skills.enabled_count || 0)) + ' enabled</div><div class="runtime-readiness-detail">' + escH(
        skills.tool_enabled === false
            ? 'The CLI skills tool is not enabled for chat sessions.'
            : 'Enabled skills are only available through Hermes CLI turns.'
    ) + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Integrations</div><div class="runtime-readiness-value">' + escH(String(integrations.configured_count || 0)) + ' configured</div><div class="runtime-readiness-detail">' + escH(
        integrations.configured_count
            ? 'Configured integrations should run through Hermes CLI.'
            : 'No Discord, WhatsApp, Slack, Telegram, Matrix, or webhook integrations are configured yet.'
    ) + '</div></div>';
    html += '</div>';
    if (reasons.length) {
        html += '<div class="runtime-reason-list">' + reasons.map(reason =>
            '<span class="runtime-reason-pill">' + escH(reason) + '</span>'
        ).join('') + '</div>';
    }
    if (hooks.configured) {
        html += '<p class="text-sm text-secondary mt-16">Hooks configured: ' + escH((hooks.keys || []).join(', ')) + '</p>';
    }
    html += '</div></div>';
    return html;
}

Screens.skills = async function () {
    const content = document.getElementById('content');
    try {
        const [data, status] = await Promise.all([
            api('GET', '/api/skills'),
            api('GET', '/api/chat/status').catch(() => ({})),
        ]);
        const skills = data.skills || [];
        const runtime = status.runtime || {};
        const policy = status.transport_policy || {};
        let html = '<div class="section-header"><span>' + skills.length + ' Skills</span><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn btn-primary" onclick="openSkillInstallModal()">Install Skill</button><div class="search-box"><span class="search-icon">' + UI_ICONS.search + '</span><input type="text" class="form-input" id="skill-search" placeholder="Search skills..." oninput="filterSkills()"></div></div></div>';
        html += '<div class="card mb-16"><div class="card-header"><span>Starter Pack</span><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn btn-sm" onclick="navigate(\'channels\')">Open Integrations</button><button class="btn btn-sm" onclick="navigate(\'env-vars\')">Open Env Vars</button></div></div><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">Safe-route defaults for everyday use. Missing items are shown here so you can decide what to add next without auto-installing anything behind your back.</p>';
        if (policy.reason) {
            html += '<p class="text-sm text-secondary mb-16">' + escH(policy.reason) + '</p>';
        }
        html += renderStarterPackGrid((runtime.starter_pack || {}).items || []);
        html += '</div></div>';

        const categories = {};
        skills.forEach(s => { const cat = s.category || 'general'; if (!categories[cat]) categories[cat] = []; categories[cat].push(s); });

        if (skills.length === 0) {
            html += '<div class="empty-state"><div class="empty-icon">' + UI_ICONS.books + '</div><h3>No Skills Found</h3><p>Skills directory is empty or no SKILL.md files found.</p></div>';
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

window.openSkillInstallModal = function () {
    showModal(
        'Install Skill',
        '<p class="text-sm text-secondary mb-16">Paste a Hermes skill identifier exactly the way you would use it in the CLI. GitHub repos such as <span class="font-mono">wondelai/skills</span> work here, and so do registry targets like <span class="font-mono">skills-sh/steipete/clawdis/weather</span>.</p>' +
        '<div class="form-group"><label class="form-label">Skill Identifier</label>' + inputH('skill-install-identifier', '', 'text', 'wondelai/skills') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="installSkillFromModal()">Install</button>'
    );
};

window.installSkillFromModal = async function () {
    const identifier = document.getElementById('skill-install-identifier')?.value?.trim() || '';
    if (!identifier) {
        toast('Skill identifier is required', 'error');
        return;
    }
    toast('Installing ' + identifier + '...', 'info', 2500);
    try {
        await api('POST', '/api/skills/install', { identifier });
        closeModal();
        toast('Skill installed: ' + identifier, 'success');
        Screens.skills();
    } catch (e) {
        toast('Install failed: ' + e.message, 'error');
    }
};

// ── CHANNELS ───────────────────────────────────────────────
Screens.channels = async function () {
    const content = document.getElementById('content');
    try {
        const [data, status] = await Promise.all([
            api('GET', '/api/channels'),
            api('GET', '/api/chat/status').catch(() => ({})),
        ]);
        const channels = data.integrations || data.channels || [];
        const runtime = status.runtime || {};
        const policy = status.transport_policy || {};
        let html = renderRuntimeReadinessCard(runtime, policy);
        html += '<div class="card mb-16"><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">This screen edits Hermes integration config directly. Top-level sections like <span class="font-mono">discord</span> and <span class="font-mono">whatsapp</span> appear here, while Hooks remains a separate editor for the raw <span class="font-mono">hooks</span> block.</p>';
        html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
        html += '<button class="btn" onclick="navigate(\'hooks\')">Open Hooks</button>';
        html += '<button class="btn" onclick="navigate(\'skills\')">Open Skills</button>';
        html += '<button class="btn" onclick="navigate(\'env-vars\')">Open Env Vars</button>';
        html += '</div></div></div>';
        if (channels.length === 0) {
            html += '<div class="empty-state"><div class="empty-icon">' + UI_ICONS.speechBubble + '</div><h3>No Integrations</h3><p>No Discord, WhatsApp, Slack, Telegram, Matrix, or webhook integrations were found in your Hermes config.</p></div>';
            content.innerHTML = html;
            return;
        }
        channels.forEach(ch => {
            const configured = !!ch.configured;
            const kindLabel = ch.kind === 'legacy_channel' ? 'Legacy Channel' : 'Integration';
            html += '<div class="card mb-16"><div class="card-header"><span>' + escH(ch.label || ch.name) + '</span><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="badge ' + (configured ? 'badge-success' : 'badge-secondary') + '">' + (configured ? 'Configured' : 'Empty') + '</span><span class="badge badge-info">' + escH(kindLabel) + '</span></div></div>';
            html += '<div class="card-body">';
            html += '<p class="text-sm text-secondary mb-12">' + escH(
                ch.kind === 'legacy_channel'
                    ? 'This entry comes from the legacy channels map in Hermes config.'
                    : 'This entry comes from a top-level Hermes integration section.'
            ) + '</p>';
            html += '<div class="form-group"><label class="form-label">Config</label><div>' + fmtVal(ch.config || {}) + '</div></div>';
            html += '<button class="btn btn-sm" onclick="editChannel(\'' + escA(ch.name) + '\')">Edit JSON</button>';
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
        const ch = (data.integrations || data.channels || []).find(c => c.name === name);
        if (!ch) { toast('Integration not found', 'error'); return; }
        showModal('Edit Integration: ' + (ch.label || name),
            '<p class="text-sm text-secondary mb-16">Edit the raw JSON for this Hermes integration block. Saving replaces the current block with the JSON below.</p>' +
            '<div class="form-group"><label class="form-label">Config JSON</label>' + textareaH('channel-config-json', JSON.stringify(ch.config || {}, null, 2), 12, true) + '</div>',
            '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="saveChannel(\'' + escA(name) + '\')">Save</button>'
        );
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

window.saveChannel = async function (name) {
    let updates;
    try {
        updates = JSON.parse(document.getElementById('channel-config-json').value || '{}');
    } catch (e) {
        toast('Config JSON is invalid', 'error');
        return;
    }
    if (!updates || typeof updates !== 'object' || Array.isArray(updates)) {
        toast('Integration config must be a JSON object', 'error');
        return;
    }
    try { await api('PUT', '/api/channels/' + name, updates); toast('Integration updated', 'success'); closeModal(); Screens.channels(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── HOOKS ──────────────────────────────────────────────────
Screens.hooks = async function () {
    const content = document.getElementById('content');
    try {
        const [data, status] = await Promise.all([
            api('GET', '/api/hooks'),
            api('GET', '/api/chat/status').catch(() => ({})),
        ]);
        const cfg = data.config || data;
        const runtime = status.runtime || {};
        let fields = '';
        let intro = '<p class="text-sm text-secondary mb-16">This screen edits the raw <span class="font-mono">hooks</span> block in Hermes config. The web UI does not execute hooks by itself; whether they run depends on your Hermes runtime.</p>';
        let recommended = '<div class="card mb-16"><div class="card-header"><span>Recommended Default</span></div><div class="card-body"><p class="text-sm text-secondary mb-12">Safe route: leave hooks empty unless you want a very specific approval, retry, or logging workflow. Memory, skills, and integrations do not need extra hooks to work.</p>';
        if (runtime.hooks && runtime.hooks.configured) {
            recommended += '<p class="text-sm text-secondary">Currently configured hooks: ' + escH((runtime.hooks.keys || []).join(', ')) + '</p>';
        }
        recommended += '</div></div>';
        for (const [key, val] of Object.entries(cfg)) {
            if (typeof val === 'boolean') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + toggleH('hook-' + key, val) + '</div>';
            else if (typeof val === 'number') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('hook-' + key, val, 'number') + '</div>';
            else if (typeof val === 'object') fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + textareaH('hook-' + key, JSON.stringify(val, null, 2), 4, true) + '</div>';
            else fields += '<div class="form-group"><label class="form-label">' + escH(key) + '</label>' + inputH('hook-' + key, val) + '</div>';
        }
        if (!fields) fields = '<div class="empty-state"><p>No hooks configured yet.</p></div>';
        content.innerHTML = recommended + '<div class="card"><div class="card-header"><span>Webhooks / Hooks</span></div><div class="card-body">' + intro + fields + '<button class="btn btn-primary mt-16" onclick="saveHooks()">Save Hooks</button></div></div>';
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
        content.innerHTML = '<div class="card"><div class="card-header"><span>Session Reset Configuration</span></div><div class="card-body"><p class="text-sm text-secondary mb-16">This screen edits the <span class="font-mono">session_reset</span> config only. It does not show active or historical Hermes sessions.</p>' + fields + '<button class="btn btn-primary mt-16" onclick="saveSessions()">Save Session Reset Config</button></div></div>';
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
    try { await api('PUT', '/api/sessions/config', updates); toast('Session reset config saved', 'success'); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── LOGS ───────────────────────────────────────────────────
Screens.logs = async function () {
    const content = document.getElementById('content');
    content.innerHTML = '<div class="card"><div class="card-header"><span>Hermes Log File Tail</span><div class="flex gap-8"><select class="form-select" id="log-lines" style="width:auto"><option value="100">100 lines</option><option value="500" selected>500 lines</option><option value="1000">1000 lines</option></select><button class="btn btn-sm" onclick="loadLogs()">Refresh</button><button class="btn btn-sm" onclick="copyLogs()">Copy</button></div></div><div class="card-body"><p class="text-sm text-secondary mb-16">Shows recent lines from Hermes log files when present. This is not a complete diagnostics or session activity view.</p></div><div class="card-body" style="padding:0"><div id="log-output" class="font-mono text-xs" style="padding:16px;max-height:70vh;overflow:auto;background:var(--bg-primary);white-space:pre-wrap;line-height:1.6;color:var(--text-secondary)"><div class="loading"><div class="spinner"></div></div></div></div></div>';
    document.getElementById('log-lines').addEventListener('change', loadLogs);
    loadLogs();
};

window.loadLogs = async function () {
    const lines = document.getElementById('log-lines')?.value || 500;
    try {
        const data = await api('GET', '/api/logs?lines=' + lines);
        const detail = data.source_detail ? data.source_detail + '\n\n' : '';
        document.getElementById('log-output').textContent = detail + (data.logs || 'No logs available.');
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
    currentRequestId: null,
    currentRequestCancelSupported: false,
    isThinking: false,
    pendingFiles: [],
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
    micStoppedByUser: false,  // track if user manually stopped
    recognition: null,
    speechSupported: false,
    historyOpen: false,
    localMessages: [],  // messages for the current viewed session
    folders: [],
    selectedFolderId: '',
    draftFolderId: '',
    voiceBaseText: '',
    voiceFinalTranscript: '',
    capabilities: {
        textAttachments: true,
        imageAttachments: false,
        audioAttachments: false,
    },
    capabilityReasons: {
        imageAttachments: '',
        audioAttachments: '',
    },
    apiServerEnabled: false,
    apiTransportSelectable: false,
    transportPolicy: {
        requiresCli: false,
        apiSelectable: false,
        reason: '',
        reasons: [],
    },
    runtimeStatus: null,
    transportPreference: 'auto',
    currentTransport: null,
    currentContinuity: null,
    currentTransportNotice: '',
    currentHermesSessionBacked: false,
    lastTurnUsedSidecarVision: false,
    lastTurnSidecarAssets: [],
    currentFolderId: '',
    currentFolderTitle: '',
    currentWorkspaceRoots: [],
    currentSourceDocs: [],
    currentFolderWorkspaceRoots: [],
    currentFolderSourceDocs: [],
    lastSubmission: null,
    cancelRequested: false,
};

function makeRequestId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
    }
    return 'req-' + Date.now() + '-' + Math.random().toString(16).slice(2);
}

function chatJoinTranscript(base, addition) {
    if (!addition) return base || '';
    if (!base) return addition;
    if (/\s$/.test(base) || /^[\s.,!?;:]/.test(addition)) return base + addition;
    return base + ' ' + addition;
}

function chatRenderVoiceTranscript(input, interimTranscript = '') {
    const finalText = chatJoinTranscript(chatState.voiceBaseText, chatState.voiceFinalTranscript.trim());
    input.value = chatJoinTranscript(finalText, interimTranscript.trim());
    chatAutoResize(input);
    document.getElementById('chat-send-btn').disabled = !input.value.trim() && chatState.pendingFiles.length === 0;
}

function chatIsProbablyTextFile(file) {
    const type = (file.type || '').toLowerCase();
    if (type.startsWith('text/')) return true;
    if ([
        'application/json', 'application/ld+json', 'application/xml', 'application/javascript',
        'application/x-javascript', 'application/x-sh', 'application/x-shellscript',
        'application/x-yaml', 'application/yaml', 'application/toml'
    ].includes(type)) return true;
    const name = (file.name || '').toLowerCase();
    return [
        '.txt', '.md', '.markdown', '.rst', '.log', '.csv', '.tsv', '.json', '.yaml', '.yml',
        '.xml', '.html', '.htm', '.css', '.js', '.jsx', '.ts', '.tsx', '.py', '.sh', '.bash',
        '.zsh', '.ini', '.cfg', '.conf', '.toml', '.sql', '.env', '.gitignore', '.dockerfile'
    ].some(ext => name.endsWith(ext));
}

function chatDescribeUnsupportedFile(file) {
    const type = (file.type || '').toLowerCase();
    const name = file.name || 'This file';
    if (type.startsWith('image/')) {
        return chatState.capabilities.imageAttachments
            ? ''
            : `${name} is an image, and ${chatState.capabilityReasons.imageAttachments || 'image attachments are not available in the current Hermes configuration'}.`;
    }
    if (type.startsWith('audio/')) {
        return `${name} is audio, and audio file uploads are not supported in Hermes chat.`;
    }
    if (chatIsProbablyTextFile(file)) return '';
    return `${name} is a binary file type that Hermes chat cannot read as text.`;
}

function chatOpenVisionSetup(sourceLabel = 'screenshots') {
    const reason = chatState.capabilityReasons.imageAttachments || 'A vision model and reachable OpenAI-compatible API are required before screenshots can be used in chat.';
    showModal(
        'Enable Screenshot Support',
        '<p class="text-sm text-secondary mb-16">' + escH(sourceLabel + ' are not ready yet. ' + reason) + '</p>' +
        '<p class="text-sm text-secondary">Choose a vision model in Providers, then add any needed API URL or key in Env Vars if your model endpoint requires them.</p>',
        '<button class="btn" onclick="closeModal()">Not now</button>' +
        '<button class="btn" onclick="closeModal(); navigate(\'env-vars\')">Open Env Vars</button>' +
        '<button class="btn" onclick="closeModal(); startVisionWizard(\'local\')">Local API</button>' +
        '<button class="btn" onclick="closeModal(); startVisionWizard(\'openai\')">OpenAI</button>' +
        '<button class="btn" onclick="closeModal(); startVisionWizard(\'openrouter\')">OpenRouter</button>' +
        '<button class="btn btn-primary" onclick="closeModal(); navigate(\'providers\'); setTimeout(function(){ if (window.editAuxProvider) editAuxProvider(\'vision\'); }, 50)">Manual Setup</button>'
    );
}

function chatAcceptedFileTypes() {
    const accepted = [
        'text/*', '.txt', '.md', '.markdown', '.rst', '.log', '.csv', '.tsv', '.json', '.yaml', '.yml',
        '.xml', '.html', '.htm', '.css', '.js', '.jsx', '.ts', '.tsx', '.py', '.sh', '.bash',
        '.zsh', '.ini', '.cfg', '.conf', '.toml', '.sql', '.env', '.gitignore', '.dockerfile'
    ];
    if (chatState.capabilities.imageAttachments) accepted.push('image/*');
    return accepted.join(',');
}

function chatCanUseMicButton() {
    return !!(chatState.speechSupported && chatState.recognition) || !!chatState.capabilities.audioAttachments;
}

function chatClonePendingFile(file) {
    return {
        ...file,
        preview_url: file.preview_url || null,
    };
}

function chatReleasePendingFile(file) {
    const previewUrl = file?.preview_url || '';
    if (previewUrl.startsWith('blob:')) {
        URL.revokeObjectURL(previewUrl);
    }
}

function chatReplacePendingFiles(files) {
    (chatState.pendingFiles || []).forEach(chatReleasePendingFile);
    chatState.pendingFiles = (files || []).map(chatClonePendingFile);
    chatRenderFileBar();
    chatSyncSendButton();
}

function chatResetComposerAfterRequest() {
    chatState.isThinking = false;
    chatState.currentRequestId = null;
    chatState.chatAbortController = null;
    chatState.currentRequestCancelSupported = false;
    chatState.cancelRequested = false;
    const dotsEl = document.getElementById('chat-thinking-dots');
    if (dotsEl) dotsEl.remove();
    const sendBtn = document.getElementById('chat-send-btn');
    if (sendBtn) {
        sendBtn.classList.remove('chat-stop-state');
        sendBtn.onclick = chatSend;
        const svg = sendBtn.querySelector('svg');
        if (svg) svg.innerHTML = '<path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>';
    }
    chatSyncSendButton();
}

function chatApplySessionMetadata(meta = null) {
    const session = meta || {};
    chatState.transportPreference = session.transport_preference || chatState.transportPreference || 'auto';
    chatState.currentTransport = session.transport_mode || null;
    chatState.currentContinuity = session.continuity_mode || null;
    chatState.currentTransportNotice = session.transport_notice || '';
    chatState.currentHermesSessionBacked = !!session.hermes_session_backed;
    chatState.lastTurnUsedSidecarVision = !!session.last_turn_used_sidecar_vision;
    chatState.lastTurnSidecarAssets = Array.isArray(session.last_turn_sidecar_asset_names) ? session.last_turn_sidecar_asset_names.slice() : [];
    chatState.currentFolderId = session.folder_id || '';
    chatState.currentFolderTitle = session.folder_title || session.folder_id || '';
    chatState.currentWorkspaceRoots = Array.isArray(session.workspace_roots) ? session.workspace_roots.slice() : [];
    chatState.currentSourceDocs = Array.isArray(session.source_docs) ? session.source_docs.slice() : [];
    chatState.currentFolderWorkspaceRoots = Array.isArray(session.folder_workspace_roots) ? session.folder_workspace_roots.slice() : [];
    chatState.currentFolderSourceDocs = Array.isArray(session.folder_source_docs) ? session.folder_source_docs.slice() : [];
    if (chatState.currentFolderId) {
        chatState.selectedFolderId = chatState.currentFolderId;
        chatState.draftFolderId = chatState.currentFolderId;
    } else if (!meta) {
        chatState.selectedFolderId = '';
        chatState.draftFolderId = '';
    }
    chatRenderSessionBanner();
    chatRenderContextPanel();
    chatRenderTransportControls();
}

function chatGoHome() {
    chatState.currentSessionId = null;
    chatState.localMessages = [];
    chatState.lastSubmission = null;
    chatState.selectedFolderId = '';
    chatState.draftFolderId = '';
    chatReplacePendingFiles([]);
    chatApplySessionMetadata({ transport_preference: chatState.transportPreference || 'auto' });
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = '';
        chatAutoResize(input);
        input.focus();
    }
    chatShowWelcome();
    chatLoadHistory();
    renderSidebarFoldersTree();
}

function chatFolderCollapseState() {
    try {
        return JSON.parse(localStorage.getItem('chat-folder-collapse') || '{}') || {};
    } catch (e) {
        return {};
    }
}

function chatIsFolderCollapsed(folderId) {
    return !!chatFolderCollapseState()[folderId];
}

function chatSetFolderCollapsed(folderId, collapsed) {
    const state = chatFolderCollapseState();
    if (collapsed) state[folderId] = true;
    else delete state[folderId];
    localStorage.setItem('chat-folder-collapse', JSON.stringify(state));
}

function chatFindFolder(folderId) {
    return (chatState.folders || []).find(folder => folder.id === folderId) || null;
}

function chatCurrentFolderSummary() {
    const folderId = chatState.currentFolderId || chatState.selectedFolderId || chatState.draftFolderId || '';
    const folder = folderId ? chatFindFolder(folderId) : null;
    if (folder) return folder;
    if (!folderId && !(chatState.currentFolderTitle || chatState.currentFolderSourceDocs.length || chatState.currentFolderWorkspaceRoots.length)) return null;
    return {
        id: folderId,
        title: chatState.currentFolderTitle || folderId || 'Folder',
        source_docs: chatState.currentFolderSourceDocs.slice(),
        workspace_roots: chatState.currentFolderWorkspaceRoots.slice(),
        sessions: [],
        chat_count: 0,
    };
}

function chatPathLabel(path) {
    if (!path) return '';
    const clean = String(path).replace(/[\\/]+$/, '');
    const parts = clean.split(/[\\/]/);
    return parts[parts.length - 1] || clean;
}

function chatSourceLabel(path) {
    return chatPathLabel(path) || 'Source';
}

function chatRenderContextPanel() {
    const panel = document.getElementById('chat-context-panel');
    if (!panel) return;
    const folder = chatCurrentFolderSummary();
    const showingFolderOverviewOnly = !!folder && !chatState.currentSessionId && (chatState.selectedFolderId || '') === (folder.id || '');
    if (!folder || showingFolderOverviewOnly) {
        panel.className = 'chat-context-panel hidden';
        panel.innerHTML = '';
        return;
    }
    const sourceDocs = Array.isArray(folder.source_docs) ? folder.source_docs : [];
    const workspaceRoots = Array.isArray(folder.workspace_roots) ? folder.workspace_roots : [];
    const folderChats = Array.isArray(folder.sessions) ? folder.sessions : [];
    let html = '<div class="chat-context-panel-header"><div>' +
        '<button class="chat-folder-heading-btn" onclick="chatShowFolderOverview(\'' + escA(folder.id || '') + '\')">' + escH(folder.title || 'Folder') + '</button>' +
        '<div class="chat-context-panel-subtitle">' +
        (sourceDocs.length
            ? 'Sources live at the folder level and guide every chat in this folder.'
            : 'No sources added yet. Use Add Source to attach docs to this folder.') +
        '</div></div>' +
        '<div class="chat-folder-toolbar">' +
        '<button class="btn btn-sm" onclick="chatNewSession(\'' + escA(folder.id || '') + '\')">New Chat</button>' +
        '<button class="btn btn-sm" onclick="chatAddFolderSources(\'' + escA(folder.id || '') + '\')">Add Source</button>' +
        '<button class="btn btn-sm" onclick="chatOpenFolderEditor(\'' + escA(folder.id || '') + '\')">Edit Folder</button>' +
        (chatState.currentSessionId && folder.id && chatState.currentFolderId === folder.id
            ? '<button class="btn btn-sm" onclick="chatUseCurrentChatAsSource(\'' + escA(folder.id) + '\')">Use Chat As Source</button>'
            : '') +
        '</div></div>';
    html += '<div class="chat-context-section"><div class="chat-context-label">Sources</div>';
    if (sourceDocs.length > 0) {
        html += '<div class="chat-context-chip-list">' + sourceDocs.map(doc =>
            '<span class="chat-context-chip" title="' + escA(doc) + '">' + escH(chatPathLabel(doc)) + '</span>'
        ).join('') + '</div>';
    } else {
        html += '<div class="chat-context-empty">Add files or create a chat-derived source for this folder.</div>';
    }
    html += '</div>';
    if (workspaceRoots.length > 0) {
        html += '<div class="chat-context-section"><div class="chat-context-label">Detected workspace roots</div><div class="chat-context-chip-list">' + workspaceRoots.map(root =>
            '<span class="chat-context-chip" title="' + escA(root) + '">' + escH(chatPathLabel(root)) + '</span>'
        ).join('') + '</div></div>';
    }
    if (folderChats.length > 0) {
        html += '<div class="chat-context-section"><div class="chat-context-label">Chats in this folder</div><div class="chat-folder-chatlist">' + folderChats.slice(0, 6).map(renderFolderSessionChip).join('') + '</div></div>';
    }
    panel.className = 'chat-context-panel';
    panel.innerHTML = html;
}

function chatExpectedTransport() {
    if (chatState.transportPolicy && chatState.transportPolicy.requiresCli) return 'cli';
    if (chatState.transportPreference === 'api') return 'api';
    if (chatState.transportPreference === 'cli') return 'cli';
    if (chatState.apiServerEnabled) return 'api';
    return 'cli';
}

function chatExpectedCancelSupport() {
    return chatExpectedTransport() === 'cli';
}

function chatTransportPreferenceLabel(value) {
    if (value === 'cli') return 'Hermes CLI';
    if (value === 'api') return 'API Replay';
    return 'Auto';
}

function chatTransportDescription(value) {
    if (chatState.transportPolicy && chatState.transportPolicy.requiresCli) {
        return chatState.transportPolicy.reason || 'Hermes CLI is required for the active runtime features in this chat.';
    }
    if (value === 'cli') return 'Hermes CLI keeps Hermes-side continuity and is where Hermes skills run.';
    if (value === 'api') {
        return chatState.apiServerEnabled
            ? 'API replay uses the configured API path and bypasses Hermes CLI skills.'
            : 'API replay is unavailable right now because the Hermes API server is not reachable.';
    }
    return chatState.apiServerEnabled
        ? 'Auto uses API when available and falls back to Hermes CLI when it is not.'
        : 'Auto will use Hermes CLI right now because the API server is unavailable.';
}

function chatRenderTransportControls() {
    const mount = document.getElementById('chat-transport-controls');
    if (!mount) return;
    let current = chatState.transportPreference || 'auto';
    if (current === 'api' && !chatState.apiTransportSelectable) {
        current = chatState.transportPolicy && chatState.transportPolicy.requiresCli ? 'cli' : 'auto';
        chatState.transportPreference = current;
    }
    const apiDisabled = !chatState.apiTransportSelectable;
    const options = [
        { value: 'auto', label: 'Auto', disabled: false },
        { value: 'cli', label: 'CLI', disabled: false },
        { value: 'api', label: 'API', disabled: apiDisabled },
    ];
    const buttons = options.map(option =>
        '<button class="chat-transport-btn' +
        (current === option.value ? ' active' : '') +
        (option.disabled ? ' disabled' : '') +
        '" type="button" ' +
        (option.disabled ? 'disabled ' : '') +
        'onclick="chatSetTransportPreference(\'' + escA(option.value) + '\')">' +
        escH(option.label) +
        '</button>'
    ).join('');
    let note = chatTransportDescription(current);
    if (!chatState.transportPolicy.requiresCli && current !== 'cli') {
        const activeCliFeatures = Array.isArray(chatState.runtimeStatus?.active_features)
            ? chatState.runtimeStatus.active_features.filter(Boolean)
            : [];
        if (activeCliFeatures.length) {
            const labels = activeCliFeatures.map(feature => {
                if (feature === 'memory') return 'memory';
                if (feature === 'skills') return 'skills';
                if (feature === 'integrations') return 'integrations';
                if (feature === 'hooks') return 'hooks';
                return String(feature || '').trim();
            }).filter(Boolean);
            const uniqueLabels = [...new Set(labels)];
            const labelText = uniqueLabels.length === 1
                ? uniqueLabels[0]
                : uniqueLabels.length === 2
                    ? uniqueLabels[0] + ' and ' + uniqueLabels[1]
                    : uniqueLabels.slice(0, -1).join(', ') + ', and ' + uniqueLabels[uniqueLabels.length - 1];
            note += ' Hermes ' + labelText + ' ' + (uniqueLabels.length === 1 ? 'only runs' : 'only run') + ' through CLI when you want ' + (uniqueLabels.length === 1 ? 'it' : 'them') + '.';
        } else {
            note += ' Hermes skills require CLI.';
        }
    }
    if (chatState.currentSessionId && chatState.currentTransport && current !== chatState.currentTransport) {
        note += ' Next turn preference: ' + chatTransportPreferenceLabel(current) + '.';
    }
    mount.innerHTML =
        '<div class="chat-transport-wrap">' +
            '<div class="chat-transport-label">Transport</div>' +
            '<div class="chat-transport-buttons">' + buttons + '</div>' +
        '</div>' +
        '<div class="chat-transport-note">' + escH(note) + '</div>';
}

function chatRenderSessionBanner() {
    const banner = document.getElementById('chat-session-banner');
    if (!banner) return;
    const badges = [];
    let text = chatState.currentTransportNotice || '';
    let cls = 'success';
    if (chatState.currentContinuity === 'hermes_resume') {
        badges.push('<span class="badge badge-success">Hermes session backed</span>');
        text = text || 'This chat stays attached to the Hermes CLI session.';
    } else if (chatState.currentContinuity === 'local_replay') {
        badges.push('<span class="badge badge-warning">Local replay only</span>');
        text = text || 'This chat is not attached to a resumable Hermes CLI session.';
        cls = 'warning';
    } else if (chatState.currentContinuity === 'cli_without_resume') {
        badges.push('<span class="badge badge-warning">CLI without resume</span>');
        text = text || 'Hermes did not return a resumable session id for this chat yet, so follow-up continuity may be limited.';
        cls = 'warning';
    }
    if (chatState.currentTransport === 'api') {
        badges.push('<span class="badge badge-warning">API replay transport</span>');
    } else if (chatState.currentTransport === 'cli') {
        badges.push('<span class="badge badge-info">Hermes CLI transport</span>');
    }
    if (chatState.lastTurnUsedSidecarVision) {
        badges.push('<span class="badge badge-info">Sidecar vision used</span>');
        if (!chatState.currentTransportNotice && chatState.lastTurnSidecarAssets.length) {
            text += ' Latest sidecar assets: ' + chatState.lastTurnSidecarAssets.join(', ') + '.';
        }
    }
    if (!badges.length && !text) {
        banner.className = 'chat-session-banner hidden';
        banner.innerHTML = '';
        return;
    }
    banner.className = 'chat-session-banner ' + cls;
    banner.innerHTML = '<div class="chat-session-banner-badges">' + badges.join('') + '</div>' + (text ? '<div class="chat-session-banner-copy">' + escH(text) + '</div>' : '');
}

function chatApplyComposerCapabilities() {
    const input = document.getElementById('chat-input');
    const hint = document.querySelector('.chat-composer-hint');
    const welcomeHint = document.querySelector('.chat-welcome-hint');
    const fileInput = document.getElementById('chat-file-input');
    const micBtn = document.getElementById('chat-voice-btn');
    const attachBtn = document.getElementById('chat-attach-btn');
    if (fileInput) fileInput.setAttribute('accept', chatAcceptedFileTypes());
    if (attachBtn) {
        attachBtn.title = chatState.capabilities.imageAttachments
            ? 'Attach text files or images'
            : 'Attach text files';
    }
    if (input) {
        input.placeholder = chatState.capabilities.imageAttachments
            ? 'Message Hermes... (attach text files or paste images)'
            : 'Message Hermes... (attach text files)';
    }
    if (hint) {
        hint.textContent = chatState.capabilities.imageAttachments
            ? 'Enter to send, Shift+Enter for new line, Ctrl+V to paste image'
            : 'Enter to send, Shift+Enter for new line. Configure Vision in Providers to enable screenshot paste.';
    }
    if (welcomeHint) {
        welcomeHint.textContent = chatState.capabilities.imageAttachments
            ? 'Tip: Paste screenshots directly with Ctrl+V'
            : 'Tip: Open Providers to choose a vision model before pasting screenshots';
    }
    if (micBtn) {
        const enabled = chatCanUseMicButton();
        micBtn.disabled = !enabled;
        micBtn.classList.toggle('disabled', !enabled);
        micBtn.title = enabled
            ? 'Voice input (click to start/stop)'
            : 'Voice input is unavailable here because this browser cannot transcribe speech and Hermes does not support audio uploads';
    }
    chatRenderTransportControls();
    chatRenderSessionBanner();
}

async function chatRefreshCapabilities() {
    try {
        const data = await api('GET', '/api/chat/status');
        const caps = data.capabilities || {};
        const reasons = data.capability_reasons || {};
        const policy = data.transport_policy || {};
        chatState.apiServerEnabled = !!data.api_server;
        chatState.apiTransportSelectable = !!policy.api_selectable;
        chatState.transportPolicy = {
            requiresCli: !!policy.requires_cli,
            apiSelectable: !!policy.api_selectable,
            reason: policy.reason || '',
            reasons: Array.isArray(policy.reasons) ? policy.reasons.slice() : [],
        };
        chatState.runtimeStatus = data.runtime || null;
        chatState.capabilities = {
            textAttachments: caps.text_attachments !== false,
            imageAttachments: !!caps.image_attachments,
            audioAttachments: !!caps.audio_attachments,
        };
        chatState.capabilityReasons = {
            imageAttachments: reasons.image_attachments || '',
            audioAttachments: reasons.audio_attachments || '',
        };
    } catch (e) {
        chatState.apiServerEnabled = false;
        chatState.apiTransportSelectable = false;
        chatState.transportPolicy = {
            requiresCli: false,
            apiSelectable: false,
            reason: '',
            reasons: [],
        };
        chatState.runtimeStatus = null;
        chatState.capabilities = {
            textAttachments: true,
            imageAttachments: false,
            audioAttachments: false,
        };
        chatState.capabilityReasons = {
            imageAttachments: '',
            audioAttachments: '',
        };
    }
    chatApplyComposerCapabilities();
}

// ── CLIPBOARD PASTE ─────────────────────────────────────
async function chatHandlePaste(e) {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            if (!chatState.capabilities.imageAttachments) {
                chatOpenVisionSetup('Pasted screenshots');
                continue;
            }
            const blob = item.getAsFile();
            if (!blob) continue;
            // Extract extension from mime type
            const ext = item.type.split('/')[1] || 'png';
            // Convert to base64 and upload
            const reader = new FileReader();
            reader.onload = async () => {
                try {
                    const b64data = reader.result;
                    const resp = await authFetch('/api/upload/base64', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ data: b64data, ext }),
                    });
                    if (resp.ok) {
                        const data = await resp.json();
                        data.preview_url = URL.createObjectURL(blob);
                        chatState.pendingFiles.push(data);
                        chatRenderFileBar();
                        document.getElementById('chat-send-btn').disabled = false;
                        toast('Image pasted', 'success', 2000);
                    } else {
                        const err = await resp.json();
                        const detail = Array.isArray(err.details) ? ' ' + err.details.join(' ') : '';
                        toast('Paste failed: ' + (err.error || 'Request failed') + detail, 'error');
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
                        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                    </button>
                    <button class="btn-icon" title="Toggle sidebar" onclick="chatToggleHistory()" style="width:28px;height:28px">
                        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/></svg>
                    </button>
                </div>
            </div>
            <div class="chat-history-list" id="chat-history-list">
                <div class="loading"><div class="spinner"></div></div>
            </div>
        </div>
        <div class="chat-history-overlay" id="chat-history-overlay"></div>

        <!-- Chat Main Area -->
        <div class="chat-main">
            <div class="chat-session-banner hidden" id="chat-session-banner"></div>
            <div class="chat-context-panel hidden" id="chat-context-panel"></div>
            <div class="chat-transcript" id="chat-messages" role="log" aria-live="polite"></div>

            <div class="chat-file-bar hidden" id="chat-file-bar">
                <div class="chat-file-previews" id="chat-file-previews"></div>
                <button class="clear-files" onclick="chatClearFiles()">Clear</button>
            </div>

	            <div class="chat-composer">
	                <div class="chat-composer-row">
                    <button class="chat-btn" id="chat-attach-btn" title="Attach text files" onclick="document.getElementById('chat-file-input').click()">
                        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
                    </button>
                    <input type="file" id="chat-file-input" multiple style="display:none" onchange="chatHandleFiles(event)">
                    <textarea id="chat-input" placeholder="Message Hermes... (attach text files)" rows="1" onkeydown="chatKeyDown(event)" oninput="chatAutoResize(this)"></textarea>
                    <button class="chat-btn" id="chat-voice-btn" title="Voice input (click to start/stop)" onclick="chatToggleVoice()">
                        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
                    </button>
                    <button class="chat-send-btn" id="chat-send-btn" onclick="chatSend()" disabled>
                        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
	                    </button>
	                </div>
	                <div class="chat-composer-footer">
	                    <div id="chat-transport-controls" class="chat-transport-controls"></div>
	                    <div class="chat-composer-actions">
	                        <button class="chat-action-btn" title="Export Chat" onclick="chatExport()">
                            <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        </button>
                        <button class="chat-action-btn" title="New Chat" onclick="chatNewSession()">
                            <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                        </button>
                        <button class="chat-action-btn" title="Regenerate" onclick="chatRegenerate()">
                            <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
                        </button>
                        <button class="chat-action-btn" title="Clear chat" onclick="chatClearCurrent()">
                            <svg aria-hidden="true" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
	                        </button>
	                    </div>
	                    <span class="chat-composer-hint">Enter to send, Shift+Enter for new line, text files only in this mode</span>
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
    const historyOverlay = document.getElementById('chat-history-overlay');
    if (historyOverlay) historyOverlay.addEventListener('click', () => {
        if (chatState.historyOpen) chatToggleHistory();
    });

    // Setup speech recognition
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SR) {
        chatState.speechSupported = true;
        chatState.recognition = new SR();
        chatState.recognition.continuous = true;  // stay active
        chatState.recognition.interimResults = true;
        chatState.recognition.lang = 'en-US';
        chatState.recognition.onresult = (e) => {
            let finalChunk = '';
            let interimChunk = '';
            for (let i = e.resultIndex; i < e.results.length; i++) {
                const transcript = e.results[i][0].transcript;
                if (e.results[i].isFinal) finalChunk += transcript;
                else interimChunk += transcript;
            }
            if (finalChunk) chatState.voiceFinalTranscript = chatJoinTranscript(chatState.voiceFinalTranscript, finalChunk.trim());
            chatRenderVoiceTranscript(input, interimChunk);
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

    chatRenderFileBar();
    chatRenderTransportControls();
    chatApplyComposerCapabilities();
    chatRefreshCapabilities();

    // Load sessions
    chatLoadHistory();

    // Render current session or welcome
    if (chatState.currentSessionId) {
        chatRenderMessages();
    } else if (chatState.selectedFolderId) {
        const folder = chatFindFolder(chatState.selectedFolderId);
        if (folder) chatRenderFolderOverview(folder);
        else chatShowWelcome();
    } else {
        chatShowWelcome();
    }
};

function foldersScreenCollapseState() {
    try {
        return JSON.parse(localStorage.getItem('folders-screen-collapse') || '{}') || {};
    } catch (e) {
        return {};
    }
}

function foldersScreenSetCollapsed(folderId, collapsed) {
    const state = foldersScreenCollapseState();
    state[folderId] = !!collapsed;
    localStorage.setItem('folders-screen-collapse', JSON.stringify(state));
}

window.toggleFoldersScreenFolder = function (folderId) {
    const currentCollapsed = foldersScreenCollapseState()[folderId] !== false;
    foldersScreenSetCollapsed(folderId, !currentCollapsed);
    Screens.folders();
};

Screens.folders = async function () {
    const content = document.getElementById('content');
    try {
        const [folderData, sessionData] = await Promise.all([
            api('GET', '/api/chat/folders'),
            api('GET', '/api/chat/sessions'),
        ]);
        const folders = folderData.folders || [];
        const sessions = sessionData.sessions || [];
        const ungrouped = sessions.filter(session => !(session.session?.folder_id));
        const collapsedState = foldersScreenCollapseState();
        content.innerHTML = `
        <div class="card">
            <div class="card-header">
                <span>Folders</span>
                <div class="flex gap-8">
                    <button class="btn btn-sm" onclick="chatCreateFolderPrompt()">New Folder</button>
                </div>
            </div>
            <div class="card-body">
                <p class="text-sm text-secondary">Folders hold shared sources and the chats that belong to that work area. Drag chats onto folders from Chat, or manage them here.</p>
            </div>
        </div>
        ${folders.map(folder => {
            const collapsed = collapsedState[folder.id] !== false;
            const duplicateMeta = chatFolderDuplicateMeta(folder, folders);
            return `<div class="card folder-admin-card">
                <div class="card-header folder-admin-header">
                    <button class="folder-admin-main" onclick="toggleFoldersScreenFolder('${escA(folder.id)}')" aria-expanded="${collapsed ? 'false' : 'true'}">
                        <span class="folder-admin-toggle">
                            <svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="transform:${collapsed ? 'rotate(-90deg)' : 'rotate(0deg)'}"><polyline points="6 9 12 15 18 9"/></svg>
                        </span>
                        <span class="folder-admin-title-wrap"><span class="folder-admin-title">${escH(folder.title || 'Folder')}</span>${duplicateMeta ? '<span class="folder-admin-duplicate-meta">' + escH(duplicateMeta) + '</span>' : ''}</span>
                        <span class="folder-admin-summary">
                            <span class="badge">${escH(String(folder.chat_count || 0))} chats</span>
                            <span class="badge">${escH(String((folder.source_docs || []).length))} sources</span>
                        </span>
                    </button>
                    <div class="flex gap-8 folder-admin-actions">
                        <button class="btn btn-sm" onclick="event.stopPropagation(); chatOpenFolderAddMenu('${escA(folder.id)}')">Add</button>
                        <button class="btn btn-sm" onclick="event.stopPropagation(); chatOpenFolderEditor('${escA(folder.id)}')">Edit</button>
                        <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); chatConfirmDeleteFolder('${escA(folder.id)}')">Delete</button>
                    </div>
                </div>
                <div class="card-body ${collapsed ? 'hidden' : ''}">
                    <div class="folder-admin-grid">
                        <div>
                            <div class="folder-admin-subtitle">Sources</div>
                            ${(folder.source_docs || []).length
                                ? '<div class="chat-context-chip-list">' + folder.source_docs.map(doc => `<span class="chat-context-chip" title="${escA(doc)}">${escH(chatSourceLabel(doc))}</span>`).join('') + '</div>'
                                : '<div class="text-sm text-secondary">No sources yet.</div>'}
                        </div>
                        <div>
                            <div class="folder-admin-subtitle">Chats</div>
                            ${(folder.sessions || []).length
                                ? '<div class="chat-folder-overview-chatlist">' + folder.sessions.map(renderFolderSessionChip).join('') + '</div>'
                                : '<div class="text-sm text-secondary">No chats in this folder yet.</div>'}
                        </div>
                    </div>
                </div>
            </div>`;
        }).join('')}
        <div class="card">
            <div class="card-header"><span>Ungrouped Chats</span></div>
            <div class="card-body">
                ${ungrouped.length
                    ? '<div class="chat-folder-overview-chatlist">' + ungrouped.map(renderFolderSessionChip).join('') + '</div>'
                    : '<div class="text-sm text-secondary">All current chats are already assigned to folders.</div>'}
            </div>
        </div>`;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><h3>Error loading folders</h3><p>' + escH(e.message) + '</p></div>';
    }
};

Screens.cron = async function () {
    const content = document.getElementById('content');
    try {
        const data = await api('GET', '/api/cron/jobs');
        const jobs = data.jobs || [];
        content.innerHTML = `
        <div class="card">
            <div class="card-header">
                <span>Cron Jobs</span>
                <button class="btn btn-sm" onclick="cronOpenEditor()">New Job</button>
            </div>
            <div class="card-body">
                <p class="text-sm text-secondary">Managed cron jobs write to your user crontab and preserve non-Hermes entries. Use standard five-field cron expressions.</p>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>Scheduled Jobs</span></div>
            <div class="table-container">
                <table class="table">
                    <thead><tr><th>Name</th><th>Schedule</th><th>Command</th><th>Status</th><th>Actions</th></tr></thead>
                    <tbody>
                        ${jobs.length ? jobs.map(job => `
                            <tr>
                                <td>${escH(job.name || 'Cron Job')}</td>
                                <td><span class="font-mono text-sm">${escH(job.schedule || '')}</span></td>
                                <td><span class="font-mono text-xs">${escH(job.command || '')}</span></td>
                                <td>${job.enabled ? '<span class="badge badge-success">Enabled</span>' : '<span class="badge">Disabled</span>'}</td>
                                <td>
                                    <div class="flex gap-8">
                                        <button class="btn btn-sm" onclick="cronOpenEditor('${escA(job.id)}')">Edit</button>
                                        <button class="btn btn-sm btn-danger" onclick="cronDeleteJob('${escA(job.id)}')">Delete</button>
                                    </div>
                                </td>
                            </tr>
                        `).join('') : '<tr><td colspan="5" class="text-sm text-secondary">No managed cron jobs yet.</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>`;
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠️</div><h3>Error loading cron jobs</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.cronOpenEditor = async function (jobId = '') {
    let job = null;
    if (jobId) {
        const data = await api('GET', '/api/cron/jobs');
        job = (data.jobs || []).find(item => item.id === jobId) || null;
    }
    showModal(
        job ? 'Edit Cron Job' : 'New Cron Job',
        '<div class="form-group"><label class="form-label">Name</label>' + inputH('cron-name', job?.name || '', 'text', 'Nightly report') + '</div>' +
        '<div class="form-group"><label class="form-label">Schedule</label>' + inputH('cron-schedule', job?.schedule || '0 9 * * 1-5', 'text', '0 9 * * 1-5') + '<div class="text-xs text-secondary mt-8">Standard cron format: minute hour day month weekday.</div></div>' +
        '<div class="form-group"><label class="form-label">Command</label><textarea id="cron-command" class="form-input" rows="4" placeholder="cd /home/pickle/hermes-web-ui && ./start.sh 5055">' + escH(job?.command || '') + '</textarea></div>' +
        '<div class="form-group"><label class="form-label">Enabled</label><label class="chat-modal-source-row"><input type="checkbox" id="cron-enabled" ' + ((job?.enabled ?? true) ? 'checked' : '') + '> <span>Run this schedule</span></label></div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="cronSaveJob(\'' + escA(jobId) + '\')">Save</button>'
    );
};

window.cronSaveJob = async function (jobId = '') {
    const payload = {
        name: document.getElementById('cron-name').value.trim(),
        schedule: document.getElementById('cron-schedule').value.trim(),
        command: document.getElementById('cron-command').value.trim(),
        enabled: !!document.getElementById('cron-enabled').checked,
    };
    try {
        if (jobId) await api('PUT', '/api/cron/jobs/' + jobId, payload);
        else await api('POST', '/api/cron/jobs', payload);
        closeModal();
        Screens.cron();
        toast('Cron job saved', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to save cron job', 'error');
    }
};

window.cronDeleteJob = async function (jobId) {
    try {
        await api('DELETE', '/api/cron/jobs/' + jobId);
        Screens.cron();
        toast('Cron job deleted', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to delete cron job', 'error');
    }
};

function sidebarFoldersExpanded() {
    return localStorage.getItem('sidebar-folders-open') === '1';
}

function setSidebarFoldersExpanded(open) {
    localStorage.setItem('sidebar-folders-open', open ? '1' : '0');
    const tree = document.getElementById('sidebar-folders-tree');
    if (tree) tree.classList.toggle('hidden', !open);
}

function sidebarFolderNodeCollapseState() {
    try {
        return JSON.parse(localStorage.getItem('sidebar-folder-node-collapse') || '{}') || {};
    } catch (e) {
        return {};
    }
}

function setSidebarFolderNodeCollapsed(folderId, collapsed) {
    const state = sidebarFolderNodeCollapseState();
    if (collapsed) state[folderId] = true;
    else delete state[folderId];
    localStorage.setItem('sidebar-folder-node-collapse', JSON.stringify(state));
}

window.toggleSidebarFolders = async function (forceOpen = null) {
    const next = typeof forceOpen === 'boolean' ? forceOpen : !sidebarFoldersExpanded();
    setSidebarFoldersExpanded(next);
    if (next) await renderSidebarFoldersTree();
};

window.toggleSidebarFolderNode = function (folderId) {
    const current = !!sidebarFolderNodeCollapseState()[folderId];
    setSidebarFolderNodeCollapsed(folderId, !current);
    renderSidebarFoldersTree();
};

window.sidebarOpenFolder = function (folderId) {
    setSidebarFoldersExpanded(true);
    setSidebarFolderNodeCollapsed(folderId, false);
    renderSidebarFoldersTree();
    navigate('chat');
    chatShowFolderOverview(folderId);
};

window.sidebarOpenChat = function (sessionId) {
    setSidebarFoldersExpanded(true);
    navigate('chat');
    chatLoadSession(sessionId);
};

window.sidebarOpenUngrouped = function () {
    setSidebarFoldersExpanded(true);
    navigate('chat');
    chatGoHome();
};

async function renderSidebarFoldersTree() {
    const tree = document.getElementById('sidebar-folders-tree');
    if (!tree) return;
    const sidebar = document.getElementById('sidebar');
    if (sidebar && sidebar.classList.contains('collapsed')) {
        tree.classList.add('hidden');
        tree.innerHTML = '';
        return;
    }
    if (!sidebarFoldersExpanded()) {
        tree.classList.add('hidden');
        tree.innerHTML = '';
        return;
    }
    tree.classList.remove('hidden');
    try {
        const [folderData, sessionData] = await Promise.all([
            api('GET', '/api/chat/folders'),
            api('GET', '/api/chat/sessions'),
        ]);
        const folders = folderData.folders || [];
        const sessions = sessionData.sessions || [];
        chatState.folders = folders.slice();
        const collapsed = sidebarFolderNodeCollapseState();
        const ungrouped = sessions.filter(session => !(session.session?.folder_id));
        tree.innerHTML =
            folders.map(folder => {
                const hidden = !!collapsed[folder.id];
                const chats = folder.sessions || [];
                const duplicateMeta = chatFolderDuplicateMeta(folder, folders);
                return '<div class="sidebar-folder-node">' +
                    '<div class="sidebar-folder-node-row" ondragover="chatFolderDragOver(event,\'' + escA(folder.id) + '\')" ondrop="chatDropSessionOnFolder(event,\'' + escA(folder.id) + '\')">' +
                    '<button class="sidebar-folder-toggle" onclick="event.stopPropagation(); toggleSidebarFolderNode(\'' + escA(folder.id) + '\')" title="' + (hidden ? 'Expand' : 'Collapse') + '">' +
                    '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="transform:' + (hidden ? 'rotate(-90deg)' : 'rotate(0deg)') + '"><polyline points="6 9 12 15 18 9"/></svg>' +
                    '</button>' +
                    '<button class="sidebar-folder-node-target' + (((chatState.selectedFolderId || chatState.currentFolderId) === folder.id && !chatState.currentSessionId) ? ' active' : '') + '" onclick="sidebarOpenFolder(\'' + escA(folder.id) + '\')">' +
                    '<span class="sidebar-folder-name-wrap"><span class="sidebar-folder-name">' + escH(folder.title || 'Folder') + '</span>' + (duplicateMeta ? '<span class="sidebar-folder-duplicate-meta">' + escH(duplicateMeta) + '</span>' : '') + '</span>' +
                    '<span class="sidebar-folder-count">' + escH(String(folder.chat_count || chats.length || 0)) + '</span>' +
                    '</button>' +
                    '<div class="sidebar-folder-actions">' +
                    '<button class="btn-icon" title="Add to folder" onclick="event.stopPropagation(); chatOpenFolderAddMenu(\'' + escA(folder.id) + '\')" style="width:20px;height:20px"><svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>' +
                    '</div></div>' +
                    '<div class="sidebar-folder-children' + (hidden ? ' hidden' : '') + '">' +
                    chats.map(session => renderSidebarFolderSessionItem(session)).join('') +
                    '</div></div>';
            }).join('') +
            (ungrouped.length ? '<div class="sidebar-folder-node"><div class="sidebar-folder-node-row"><button class="sidebar-folder-node-target' + (!chatState.currentSessionId && !chatState.selectedFolderId ? ' active' : '') + '" onclick="sidebarOpenUngrouped()"><span class="sidebar-folder-name">Ungrouped</span><span class="sidebar-folder-count">' + escH(String(ungrouped.length)) + '</span></button></div><div class="sidebar-folder-children">' + ungrouped.map(session => renderSidebarFolderSessionItem(session, 'sidebar-folder-ungrouped-btn')).join('') + '</div></div>' : '');
    } catch (e) {
        tree.innerHTML = '<div class="text-xs text-secondary" style="padding:8px 10px">Unable to load folders.</div>';
    }
}

window.renderSidebarFoldersTree = renderSidebarFoldersTree;

// ── CHAT HISTORY ─────────────────────────────────────────

async function chatLoadHistory() {
    try {
        const [folderData, sessionData] = await Promise.all([
            api('GET', '/api/chat/folders'),
            api('GET', '/api/chat/sessions'),
        ]);
        const list = document.getElementById('chat-history-list');
        if (!list) return;
        const folders = folderData.folders || [];
        const sessions = sessionData.sessions || [];
        chatState.folders = folders.slice();
        const ungrouped = sessions.filter(s => !(s.session && s.session.folder_id));
        if (folders.length === 0 && ungrouped.length === 0) {
            list.innerHTML = '<div class="chat-history-empty">No chats yet.<br>Click + to start one.</div>';
            chatRenderContextPanel();
            return;
        }
        const renderSessionItem = (s) => {
            const isActive = s.id === chatState.currentSessionId;
            const preview = s.last_message ? escH(s.last_message) : 'Empty';
            return '<div class="chat-history-item' + (isActive ? ' active' : '') + '" data-sid="' + escA(s.id) + '" draggable="true" ondragstart="chatDragSession(event,\'' + escA(s.id) + '\')" onclick="chatLoadSession(\'' + escA(s.id) + '\')">' +
                '<div class="chat-history-item-title">' + escH(s.title || 'Untitled') + '</div>' +
                '<div class="chat-history-item-preview">' + preview + '</div>' +
                '<div class="chat-history-item-meta">' + escH((s.message_count || 0) + ' msgs') + '</div>' +
                '<div class="chat-history-item-actions">' +
                '<button class="btn-icon" title="Rename" onclick="event.stopPropagation();chatRenameSessionPrompt(\'' + escA(s.id) + '\', \'' + escA(s.title || 'Untitled') + '\')" style="width:22px;height:22px"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg></button>' +
                '<button class="btn-icon" title="Delete" onclick="event.stopPropagation();chatDeleteSession(\'' + escA(s.id) + '\')" style="width:22px;height:22px"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>' +
                '</div></div>';
        };
        let html = '';
        if (folders.length > 0) {
            html += folders.map(folder => {
                const collapsed = chatIsFolderCollapsed(folder.id);
                const isSelected = folder.id === chatState.selectedFolderId;
                const chats = folder.sessions || [];
                const duplicateMeta = chatFolderDuplicateMeta(folder, folders);
                return '<div class="chat-folder-tree">' +
                    '<div class="chat-folder-row' + (isSelected ? ' active' : '') + '" ondragover="chatFolderDragOver(event,\'' + escA(folder.id) + '\')" ondrop="chatDropSessionOnFolder(event,\'' + escA(folder.id) + '\')">' +
                    '<button class="chat-folder-toggle" onclick="event.stopPropagation();chatToggleFolderGroup(\'' + escA(folder.id) + '\')" title="' + (collapsed ? 'Expand' : 'Collapse') + '">' +
                    '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform:' + (collapsed ? 'rotate(-90deg)' : 'rotate(0deg)') + '"><polyline points="6 9 12 15 18 9"/></svg>' +
                    '</button>' +
                    '<button class="chat-folder-main" onclick="chatShowFolderOverview(\'' + escA(folder.id) + '\')">' +
                    '<div class="chat-folder-name">' + escH(folder.title || 'Folder') + '</div>' +
                    '<div class="chat-folder-meta">' + escH((folder.chat_count || chats.length || 0) + ' chats') + (folder.source_docs && folder.source_docs.length ? ' • ' + escH(folder.source_docs.length + ' sources') : '') + (duplicateMeta ? ' • ' + escH(duplicateMeta) : '') + '</div>' +
                    '</button>' +
                    '<div class="chat-folder-actions">' +
                    '<button class="btn-icon" title="Add to folder" onclick="event.stopPropagation();chatOpenFolderAddMenu(\'' + escA(folder.id) + '\')" style="width:22px;height:22px"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg></button>' +
                    '<button class="btn-icon" title="Edit folder" onclick="event.stopPropagation();chatOpenFolderEditor(\'' + escA(folder.id) + '\')" style="width:22px;height:22px"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg></button>' +
                    '</div></div>' +
                    '<div class="chat-folder-children' + (collapsed ? ' hidden' : '') + '">' + chats.map(renderSessionItem).join('') + '</div></div>';
            }).join('');
        }
        if (ungrouped.length > 0) {
            html += '<div class="chat-history-group"><button class="chat-history-group-title-btn" onclick="chatGoHome()">Ungrouped</button>' + ungrouped.map(renderSessionItem).join('') + '</div>';
        }
        list.innerHTML = html || '<div class="chat-history-empty">No chats yet.<br>Click + to start one.</div>';
        chatRenderContextPanel();
        if (!chatState.currentSessionId && chatState.selectedFolderId) {
            const folder = chatFindFolder(chatState.selectedFolderId);
            if (folder) chatRenderFolderOverview(folder);
        }
        renderSidebarFoldersTree();
    } catch (e) {
        const list = document.getElementById('chat-history-list');
        if (list) list.innerHTML = '<div class="chat-history-empty">Error loading chats</div>';
    }
}

window.chatLoadSession = async function (sid) {
    try {
        const data = await api('GET', '/api/chat/sessions/' + sid + '/messages');
        chatState.currentSessionId = sid;
        chatState.localMessages = data.messages || [];
        chatApplySessionMetadata(data.session || null);
        chatRenderMessages();
    } catch (e) {
        toast('Failed to load session', 'error');
    }
    chatLoadHistory();  // refresh active state
};

window.chatDeleteSession = async function (sid) {
    const activeScreen = document.querySelector('.nav-item.active')?.dataset.screen || 'chat';
    try {
        await api('POST', '/api/chat/sessions/' + sid + '/delete');
        toast('Chat deleted', 'success', 2000);
        if (chatState.currentSessionId === sid) {
            chatState.currentSessionId = null;
            chatState.localMessages = [];
            chatState.lastSubmission = null;
            if (chatState.selectedFolderId) {
                const folder = chatFindFolder(chatState.selectedFolderId);
                chatApplySessionMetadata({
                    folder_id: folder ? folder.id : chatState.selectedFolderId,
                    folder_title: folder ? folder.title : chatState.currentFolderTitle,
                    folder_workspace_roots: folder ? (folder.workspace_roots || []) : [],
                    folder_source_docs: folder ? (folder.source_docs || []) : [],
                    workspace_roots: folder ? (folder.workspace_roots || []) : [],
                    source_docs: folder ? (folder.source_docs || []) : [],
                });
                if (folder) chatRenderFolderOverview(folder);
                else chatShowWelcome();
            } else {
                chatApplySessionMetadata({ transport_preference: chatState.transportPreference || 'auto' });
                chatShowWelcome();
            }
        }
        if (activeScreen === 'folders') {
            await Screens.folders();
        }
        chatLoadHistory();
        renderSidebarFoldersTree();
    } catch (e) { toast('Delete failed', 'error'); }
};

window.chatRenameSessionPrompt = async function (sid, currentTitle) {
    const newTitle = window.prompt('Rename chat:', currentTitle);
    if (newTitle === null) return;  // cancelled
    if (newTitle.trim() === '') { toast('Name cannot be empty', 'warning'); return; }
    try {
        await api('POST', '/api/chat/sessions/' + sid + '/rename', { title: newTitle.trim() });
        toast('Chat renamed', 'success', 1500);
        chatLoadHistory();
        renderSidebarFoldersTree();
    } catch (e) { toast('Rename failed', 'error'); }
};

window.chatToggleFolderGroup = function (folderId) {
    const collapsed = !chatIsFolderCollapsed(folderId);
    chatSetFolderCollapsed(folderId, collapsed);
    chatLoadHistory();
};

window.chatDragSession = function (event, sessionId) {
    event.dataTransfer.setData('text/plain', sessionId);
    event.dataTransfer.effectAllowed = 'move';
};

window.chatFolderDragOver = function (event, folderId) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
};

window.chatDropSessionOnFolder = async function (event, folderId) {
    event.preventDefault();
    const sessionId = event.dataTransfer.getData('text/plain');
    if (!sessionId || !folderId) return;
    try {
        await api('PUT', '/api/chat/sessions/' + sessionId + '/folder', { folder_id: folderId });
        if (chatState.currentSessionId === sessionId) {
            const folder = chatFindFolder(folderId);
            if (folder) {
                chatState.selectedFolderId = folderId;
                chatState.draftFolderId = folderId;
            }
        }
        await chatLoadHistory();
        renderSidebarFoldersTree();
        toast('Chat moved to folder', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to move chat', 'error');
    }
};

window.chatOpenFolderAddMenu = async function (folderId = '') {
    const folder = chatFindFolder(folderId || chatState.selectedFolderId || chatState.currentFolderId || '');
    if (!folder) {
        toast('Select a folder first', 'warning');
        return;
    }
    let sessions = [];
    try {
        const resp = await api('GET', '/api/chat/sessions');
        sessions = resp.sessions || [];
    } catch (e) {}
    const movable = sessions.filter(session => (session.session?.folder_id || '') !== folder.id);
    const currentInFolder = chatState.currentSessionId && chatState.currentFolderId === folder.id;
    showModal(
        'Add To ' + (folder.title || 'Folder'),
        '<p class="text-sm text-secondary mb-16">Add files as sources, start a new chat here, move existing chats in, or turn selected chats into reusable source docs.</p>' +
        '<div class="chat-folder-add-actions">' +
        '<button class="btn btn-primary" onclick="closeModal(); chatNewSession(\'' + escA(folder.id) + '\')">New Chat In Folder</button>' +
        '<button class="btn" onclick="closeModal(); chatAddFolderSources(\'' + escA(folder.id) + '\')">Add Source Files</button>' +
        (currentInFolder ? '<button class="btn" onclick="closeModal(); chatUseCurrentChatAsSource(\'' + escA(folder.id) + '\')">Use Current Chat As Source</button>' : '') +
        '</div>' +
        '<div class="form-group mt-16"><label class="form-label">Move existing chats into this folder</label>' +
        (movable.length
            ? '<div class="chat-modal-chip-list">' + movable.map(session =>
                '<label class="chat-modal-source-row"><input class="chat-folder-session-choice" type="checkbox" value="' + escA(session.id) + '"> <span><strong>' + escH(session.title || 'Untitled') + '</strong><br><span class="text-xs text-secondary">' + escH((session.session?.folder_title || session.session?.folder_id || 'Ungrouped')) + '</span></span></label>'
            ).join('') + '</div>'
            : '<div class="text-sm text-secondary">No other chats are available to move right now.</div>') +
        '</div>',
        '<button class="btn" onclick="closeModal()">Close</button>' +
        (movable.length ? '<button class="btn" onclick="chatAddSelectedSessionsAsSources(\'' + escA(folder.id) + '\')">Use Selected Chats As Sources</button><button class="btn btn-primary" onclick="chatMoveSelectedSessionsToFolder(\'' + escA(folder.id) + '\')">Move Selected Chats</button>' : '')
    );
};

window.chatMoveSelectedSessionsToFolder = async function (folderId) {
    const selected = Array.from(document.querySelectorAll('.chat-folder-session-choice:checked')).map(input => input.value);
    if (selected.length === 0) {
        toast('Select at least one chat', 'warning');
        return;
    }
    try {
        for (const sessionId of selected) {
            await api('PUT', '/api/chat/sessions/' + sessionId + '/folder', { folder_id: folderId });
        }
        closeModal();
        await chatLoadHistory();
        renderSidebarFoldersTree();
        if ((chatState.selectedFolderId || chatState.currentFolderId) === folderId) {
            chatShowFolderOverview(folderId);
        }
        toast('Chats moved to folder', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to move chats', 'error');
    }
};

window.chatAddSelectedSessionsAsSources = async function (folderId) {
    const selected = Array.from(document.querySelectorAll('.chat-folder-session-choice:checked')).map(input => input.value);
    if (selected.length === 0) {
        toast('Select at least one chat', 'warning');
        return;
    }
    try {
        for (const sessionId of selected) {
            await api('POST', '/api/chat/folders/' + folderId + '/sources/from-chat', { session_id: sessionId });
        }
        closeModal();
        await chatLoadHistory();
        renderSidebarFoldersTree();
        if ((chatState.selectedFolderId || chatState.currentFolderId) === folderId) {
            chatShowFolderOverview(folderId);
        }
        toast('Selected chats added as sources', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to add chat sources', 'error');
    }
};

window.chatShowFolderOverview = async function (folderId) {
    if (!folderId) return;
    let folder = chatFindFolder(folderId);
    if (!folder) {
        await chatLoadHistory();
        folder = chatFindFolder(folderId);
    }
    if (!folder) {
        toast('Folder not found', 'warning');
        return;
    }
    const activeScreen = document.querySelector('.nav-item.active')?.dataset.screen || '';
    if (activeScreen !== 'chat') {
        navigate('chat');
    }
    setSidebarFoldersExpanded(true);
    chatState.selectedFolderId = folderId;
    chatState.draftFolderId = folderId;
    chatState.currentSessionId = null;
    chatState.localMessages = [];
    chatApplySessionMetadata({
        folder_id: folder.id,
        folder_title: folder.title,
        folder_workspace_roots: folder.workspace_roots || [],
        folder_source_docs: folder.source_docs || [],
        workspace_roots: folder.workspace_roots || [],
        source_docs: folder.source_docs || [],
    });
    chatRenderFolderOverview(folder);
    await chatLoadHistory();
};

window.chatToggleHistory = function () {
    chatState.historyOpen = !chatState.historyOpen;
    const el = document.getElementById('chat-history');
    const overlay = document.getElementById('chat-history-overlay');
    if (el) el.classList.toggle('collapsed', !chatState.historyOpen);
    if (el) el.classList.toggle('mobile-open', chatState.historyOpen);
    if (overlay) overlay.classList.toggle('active', chatState.historyOpen && window.innerWidth <= 768);
};

function chatShowWelcome() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    msgs.innerHTML = `
    <div id="chat-welcome" class="chat-welcome">
        <div class="chat-welcome-icon">
            <svg aria-hidden="true" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
        </div>
        <h1>Hermes Agent</h1>
        <p>Your personal AI assistant with access to tools, files, and real-time data.</p>
        <div class="chat-quick-actions">
            <button class="chat-quick-btn" onclick="chatQuick('Help me debug a coding issue')">
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>
                <span>Coding</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Analyze the attached file')">
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                <span>File Analysis</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Research a topic for me')">
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                <span>Research</span>
            </button>
            <button class="chat-quick-btn" onclick="chatQuick('Help me write something')">
                <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                <span>Writing</span>
            </button>
        </div>
        <p class="chat-welcome-hint">Tip: You can send a text file even without typing a message</p>
    </div>`;
}

function chatRenderFolderOverview(folder) {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    const sources = folder.source_docs || [];
    const chats = folder.sessions || [];
    msgs.innerHTML = `
    <div class="chat-folder-overview">
        <div class="chat-folder-overview-title">${escH(folder.title || 'Folder')}</div>
        <div class="chat-folder-overview-subtitle">This folder groups chats and shared source docs. New chats started here will automatically inherit these sources.</div>
        <div class="chat-folder-overview-actions">
            <button class="btn btn-primary" onclick="chatNewSession('${escA(folder.id)}')">New Chat In Folder</button>
            <button class="btn" onclick="chatAddFolderSources('${escA(folder.id)}')">Add Source</button>
            <button class="btn" onclick="chatOpenFolderEditor('${escA(folder.id)}')">Edit Folder</button>
            <button class="btn btn-danger" onclick="chatConfirmDeleteFolder('${escA(folder.id)}')">Delete Folder</button>
        </div>
        <div class="chat-folder-overview-grid">
            <div class="chat-folder-overview-card">
                <div class="chat-folder-overview-card-title">Sources</div>
                ${sources.length ? '<div class="chat-context-chip-list">' + sources.map(doc => '<span class="chat-context-chip" title="' + escA(doc) + '">' + escH(chatPathLabel(doc)) + '</span>').join('') + '</div>' : '<div class="chat-context-empty">No sources yet.</div>'}
            </div>
            <div class="chat-folder-overview-card">
                <div class="chat-folder-overview-card-title">Chats</div>
                ${chats.length ? '<div class="chat-folder-overview-chatlist">' + chats.map(renderFolderSessionChip).join('') + '</div>' : '<div class="chat-context-empty">No chats in this folder yet.</div>'}
            </div>
        </div>
    </div>`;
}

function chatPrepareTranscriptForConversation() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return null;
    if (msgs.querySelector('#chat-welcome') || msgs.querySelector('.chat-folder-overview')) {
        msgs.innerHTML = '';
    }
    return msgs;
}

function chatRenderMessages() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    msgs.innerHTML = '';
    const messages = chatState.localMessages;
    if (!messages || messages.length === 0) {
        const folder = chatCurrentFolderSummary();
        if (folder && (chatState.selectedFolderId || chatState.currentFolderId)) {
            chatRenderFolderOverview(folder);
            return;
        }
        chatShowWelcome();
        return;
    }
    messages.forEach(m => {
        msgs.appendChild(chatBuildMessageNode(m));
    });
    msgs.scrollTop = msgs.scrollHeight;
    chatEnhanceCodeBlocks();
}

function chatMessageBadges(message) {
    const badges = [];
    if (message?.sidecar_vision?.used) {
        badges.push('<span class="badge badge-info">Sidecar vision</span>');
        if (message.sidecar_vision.reanalysis) {
            badges.push('<span class="badge">Re-analysis</span>');
        }
    }
    return badges.length ? '<div class="chat-msg-badges">' + badges.join('') + '</div>' : '';
}

function chatBuildMessageNode(message) {
    const role = message.role;
    const content = message.content;
    const files = message.files || [];
    const time = message.timestamp ? new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const avatarSvg = role === 'user'
        ? '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
        : '<svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>';
    let filesHtml = '';
    if (files && files.length > 0) {
        filesHtml = '<div class="chat-msg-files">' + files.map(f => '<span class="chat-file-tag"><span>' + UI_ICONS.paperclip + '</span>' + escH(f) + '</span>').join('') + '</div>';
    }
    let bubbleHtml = '';
    if (content) {
        const tmp = document.createElement('div');
        tmp.innerHTML = chatRenderMd(content);
        const plainText = tmp.textContent || tmp.innerText || content;
        const escapedPlain = escH(plainText);
        bubbleHtml = '<div class="chat-bubble"><div class="chat-bubble-content">' + chatRenderMd(content) + '</div><button class="chat-msg-copy" onclick="chatCopyMsg(this)" data-text="' + escapedPlain + '" title="Copy message"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></div>';
    }
    const div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    div.innerHTML = '<div class="chat-msg-inner"><div class="chat-msg-avatar">' + avatarSvg + '</div><div class="chat-msg-body">' + chatMessageBadges(message) + bubbleHtml + filesHtml + (time ? '<div class="chat-msg-time">' + time + '</div>' : '') + '</div></div>';
    return div;
}

function chatEnhanceCodeBlocks() {
    document.querySelectorAll('#chat-messages .code-block').forEach(function(pre) {
        if (pre.dataset.enhanced) return;
        pre.dataset.enhanced = '1';
        const code = pre.querySelector('code');
        const wrapper = document.createElement('div');
        wrapper.className = 'code-wrapper';
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);
        const btn = document.createElement('button');
        btn.className = 'code-copy-btn';
        btn.textContent = 'Copy';
        btn.title = 'Copy code';
        btn.addEventListener('click', function() {
            navigator.clipboard.writeText(code.innerText).then(function() {
                btn.textContent = 'Copied!';
                setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
            });
        });
        wrapper.appendChild(btn);
        if (window.hljs) hljs.highlightElement(code);
    });
}

function chatFolderDuplicateMeta(folder, folders = null) {
    if (!folder) return '';
    const allFolders = Array.isArray(folders) ? folders : (chatState.folders || []);
    const titleKey = String(folder.title || '').trim().toLowerCase();
    if (!titleKey) return '';
    const duplicateCount = allFolders.filter(item => String(item.title || '').trim().toLowerCase() === titleKey).length;
    if (duplicateCount < 2) return '';
    const stamp = folder.created || folder.updated || '';
    const date = stamp ? new Date(stamp) : null;
    if (date && !Number.isNaN(date.getTime())) {
        return 'Duplicate · created ' + new Intl.DateTimeFormat(undefined, {
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit',
        }).format(date);
    }
    return 'Duplicate · ref ' + String(folder.id || '').slice(0, 8);
}

function chatOpenSessionFromAnyView(sessionId) {
    const activeScreen = document.querySelector('.nav-item.active')?.dataset.screen || 'chat';
    if (activeScreen !== 'chat') navigate('chat');
    chatLoadSession(sessionId);
}

function renderFolderSessionChip(session) {
    const isActive = session.id === chatState.currentSessionId;
    return '<span class="chat-folder-chat-entry">' +
        '<button class="chat-folder-chat-pill' + (isActive ? ' active' : '') + '" onclick="chatOpenSessionFromAnyView(\'' + escA(session.id) + '\')" title="' + escA(session.title || 'Untitled') + '">' + escH(session.title || 'Untitled') + '</button>' +
        '<button class="btn-icon chat-folder-chat-delete" title="Delete chat" onclick="event.stopPropagation(); chatDeleteSession(\'' + escA(session.id) + '\')" style="width:22px;height:22px"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>' +
        '</span>';
}

function renderSidebarFolderSessionItem(session, extraClass = 'sidebar-folder-chat') {
    const active = chatState.currentSessionId === session.id ? ' active' : '';
    return '<div class="sidebar-folder-chat-row">' +
        '<button class="' + extraClass + active + '" draggable="true" ondragstart="chatDragSession(event,\'' + escA(session.id) + '\')" onclick="sidebarOpenChat(\'' + escA(session.id) + '\')" title="' + escA(session.title || 'Untitled') + '">' + escH(session.title || 'Untitled') + '</button>' +
        '<button class="btn-icon sidebar-folder-chat-delete" title="Delete chat" onclick="event.stopPropagation(); chatDeleteSession(\'' + escA(session.id) + '\')" style="width:20px;height:20px"><svg aria-hidden="true" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>' +
        '</div>';
}

window.chatNewSession = function (folderId = '') {
    const activeScreen = document.querySelector('.nav-item.active')?.dataset.screen || '';
    if (activeScreen !== 'chat') {
        navigate('chat');
    }
    chatState.currentSessionId = null;
    chatState.localMessages = [];
    chatState.lastSubmission = null;
    chatReplacePendingFiles([]);
    chatState.selectedFolderId = folderId || chatState.selectedFolderId || '';
    chatState.draftFolderId = folderId || chatState.selectedFolderId || '';
    if (chatState.selectedFolderId) {
        const folder = chatFindFolder(chatState.selectedFolderId);
        chatApplySessionMetadata({
            transport_preference: chatState.transportPreference || 'auto',
            folder_id: folder ? folder.id : chatState.selectedFolderId,
            folder_title: folder ? folder.title : chatState.currentFolderTitle,
            folder_workspace_roots: folder ? (folder.workspace_roots || []) : [],
            folder_source_docs: folder ? (folder.source_docs || []) : [],
            workspace_roots: folder ? (folder.workspace_roots || []) : [],
            source_docs: folder ? (folder.source_docs || []) : [],
        });
        if (folder) chatRenderFolderOverview(folder);
        else chatShowWelcome();
    } else {
        chatApplySessionMetadata({ transport_preference: chatState.transportPreference || 'auto' });
        chatShowWelcome();
    }
    chatLoadHistory();
    const input = document.getElementById('chat-input');
    if (input) { input.value = ''; input.focus(); }
    toast('New session', 'info', 1500);
};

async function chatEnsureSessionRecord() {
    if (chatState.currentSessionId) return chatState.currentSessionId;
    const resp = await api('POST', '/api/chat/sessions', {
        folder_id: chatState.draftFolderId || chatState.selectedFolderId || '',
        transport_preference: chatState.transportPreference || 'auto',
    });
    chatState.currentSessionId = resp.session_id;
    chatState.localMessages = [];
    chatApplySessionMetadata(resp.session || null);
    chatLoadHistory();
    return chatState.currentSessionId;
}

window.chatSetTransportPreference = async function (value) {
    const next = ['cli', 'api'].includes(value) ? value : 'auto';
    if (next === 'api' && !chatState.apiTransportSelectable) {
        toast(chatState.transportPolicy.reason || 'API transport is unavailable right now', 'warning');
        return;
    }
    try {
        if (chatState.currentSessionId) {
            const resp = await api('PUT', '/api/chat/sessions/' + chatState.currentSessionId + '/transport', {
                transport_preference: next,
            });
            chatApplySessionMetadata(resp.session || null);
        } else {
            chatState.transportPreference = next;
            chatRenderTransportControls();
            chatRenderSessionBanner();
        }
        chatApplyComposerCapabilities();
        toast('Transport preference set to ' + chatTransportPreferenceLabel(next), 'success', 1500);
    } catch (e) {
        toast('Failed to update transport: ' + e.message, 'error');
    }
};

window.chatCreateFolderPrompt = function () {
    showModal(
        'New Folder',
        '<p class="text-sm text-secondary mb-16">Create a folder to group related chats and shared sources.</p>' +
        '<div class="form-group"><label class="form-label">Folder name</label>' + inputH('chat-folder-title', '', 'text', 'Example: Hermes audit') + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="chatCreateFolder()">Create</button>'
    );
    const input = document.getElementById('chat-folder-title');
    if (input) {
        input.focus();
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            chatCreateFolder();
        });
    }
};

window.chatCreateFolder = async function () {
    const title = document.getElementById('chat-folder-title').value.trim();
    if (!title) {
        toast('Folder name is required', 'warning');
        return;
    }
    try {
        const resp = await api('POST', '/api/chat/folders', { title });
        closeModal();
        setSidebarFoldersExpanded(true);
        await chatLoadHistory();
        renderSidebarFoldersTree();
        await chatShowFolderOverview(resp.folder.id);
        toast('Folder created', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to create folder', 'error');
    }
};

window.chatOpenFolderEditor = async function (folderId = '') {
    const folder = chatFindFolder(folderId || chatState.selectedFolderId || chatState.currentFolderId || '');
    if (!folder) {
        toast('Select a folder first', 'warning');
        return;
    }
    showModal(
        'Edit Folder',
        '<p class="text-sm text-secondary mb-16">Rename the folder and choose which shared sources should stay attached to it.</p>' +
        '<div class="form-group"><label class="form-label">Folder name</label>' + inputH('chat-folder-edit-title', folder.title || '', 'text', 'Folder name') + '</div>' +
        '<div class="form-group"><label class="form-label">Current sources</label>' +
        ((folder.source_docs || []).length
            ? '<div class="chat-modal-chip-list">' + folder.source_docs.map(doc =>
                '<label class="chat-modal-source-row"><input type="checkbox" value="' + escA(doc) + '" checked> <span title="' + escA(doc) + '">' + escH(chatSourceLabel(doc)) + '</span></label>'
            ).join('') + '</div>'
            : '<div class="text-sm text-secondary">No sources yet.</div>') +
        '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn" onclick="closeModal(); chatOpenFolderAddMenu(\'' + escA(folder.id) + '\')">Add To Folder</button><button class="btn btn-danger" onclick="closeModal(); chatConfirmDeleteFolder(\'' + escA(folder.id) + '\')">Delete Folder</button><button class="btn btn-primary" onclick="chatSaveFolder(\'' + escA(folder.id) + '\')">Save</button>'
    );
};

window.chatSaveFolder = async function (folderId) {
    const selected = Array.from(document.querySelectorAll('.chat-modal-source-row input:checked')).map(input => input.value);
    const payload = {
        title: document.getElementById('chat-folder-edit-title').value.trim(),
        source_docs: Array.from(new Set(selected)),
    };
    try {
        await api('PUT', '/api/chat/folders/' + folderId, payload);
        closeModal();
        await chatLoadHistory();
        renderSidebarFoldersTree();
        if ((chatState.selectedFolderId || chatState.currentFolderId) === folderId) {
            chatShowFolderOverview(folderId);
        }
        toast('Folder updated', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to save folder', 'error');
    }
};

window.chatConfirmDeleteFolder = function (folderId = '') {
    const folder = chatFindFolder(folderId || chatState.selectedFolderId || chatState.currentFolderId || '');
    if (!folder) {
        toast('Select a folder first', 'warning');
        return;
    }
    const chatCount = (folder.sessions || []).length || folder.chat_count || 0;
    const sourceCount = (folder.source_docs || []).length;
    showModal(
        'Delete Folder',
        '<p class="text-sm text-secondary mb-16">Delete <strong>' + escH(folder.title || 'this folder') + '</strong>?</p>' +
        '<p class="text-sm text-secondary mb-16">' +
        (chatCount
            ? 'Chats in this folder will be moved back to Ungrouped. The chats themselves will not be deleted.'
            : 'This will remove the folder and its shared folder-level context.') +
        '</p>' +
        (sourceCount
            ? '<p class="text-xs text-secondary">Shared source references will be removed from the folder, but the underlying files will stay on disk.</p>'
            : ''),
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="chatDeleteFolder(\'' + escA(folder.id) + '\')">Delete Folder</button>'
    );
};

window.chatDeleteFolder = async function (folderId = '') {
    const folder = chatFindFolder(folderId || chatState.selectedFolderId || chatState.currentFolderId || '');
    if (!folder) {
        toast('Folder not found', 'warning');
        return;
    }
    const activeScreen = document.querySelector('.nav-item.active')?.dataset.screen || 'chat';
    const currentSessionId = chatState.currentSessionId || '';
    const currentChatInFolder = !!currentSessionId && chatState.currentFolderId === folder.id;
    try {
        const resp = await api('DELETE', '/api/chat/folders/' + encodeURIComponent(folder.id));
        closeModal();
        chatState.folders = (chatState.folders || []).filter(item => item.id !== folder.id);
        if (chatState.selectedFolderId === folder.id) chatState.selectedFolderId = '';
        if (chatState.draftFolderId === folder.id) chatState.draftFolderId = '';
        if (chatState.currentFolderId === folder.id) {
            chatState.currentFolderId = '';
            chatState.currentFolderTitle = '';
            chatState.currentFolderWorkspaceRoots = [];
            chatState.currentFolderSourceDocs = [];
        }
        renderSidebarFoldersTree();
        if (currentChatInFolder && currentSessionId) {
            chatState.selectedFolderId = '';
            chatState.draftFolderId = '';
            await chatLoadSession(currentSessionId);
        } else if (activeScreen === 'folders') {
            await Screens.folders();
        } else {
            chatGoHome();
        }
        toast(
            (resp.moved_session_count || 0)
                ? 'Folder deleted. Chats moved to Ungrouped.'
                : 'Folder deleted.',
            'success',
            2000,
        );
    } catch (e) {
        toast(e.message || 'Failed to delete folder', 'error');
    }
};

window.chatAddFolderSources = function (folderId = '') {
    const targetFolderId = folderId || chatState.selectedFolderId || chatState.currentFolderId || '';
    if (!targetFolderId) {
        toast('Create or select a folder first', 'warning');
        return;
    }
    const input = document.getElementById('global-folder-source-input') || document.getElementById('chat-folder-source-input');
    if (!input) return;
    input.dataset.folderId = targetFolderId;
    input.value = '';
    input.click();
};

window.chatHandleFolderSources = async function (event) {
    const input = event.target;
    const folderId = input.dataset.folderId || '';
    const files = Array.from(input.files || []);
    if (!folderId || files.length === 0) return;
    try {
        const uploads = [];
        for (const file of files) {
            const form = new FormData();
            form.append('file', file);
            const resp = await authFetch('/api/upload', { method: 'POST', body: form });
            const body = await resp.json();
            if (!resp.ok) throw new Error(body.error || 'Upload failed');
            uploads.push(body.stored_as);
        }
        await api('PUT', '/api/chat/folders/' + folderId, { source_uploads: uploads });
        closeModal();
        await chatLoadHistory();
        renderSidebarFoldersTree();
        if ((chatState.selectedFolderId || chatState.currentFolderId) === folderId) {
            chatShowFolderOverview(folderId);
        }
        toast('Sources added', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to add sources', 'error');
    } finally {
        input.value = '';
    }
};

window.chatUseCurrentChatAsSource = async function (folderId = '') {
    const targetFolderId = folderId || chatState.currentFolderId || '';
    if (!targetFolderId || !chatState.currentSessionId) {
        toast('Open a chat in a folder first', 'warning');
        return;
    }
    try {
        await api('POST', '/api/chat/folders/' + targetFolderId + '/sources/from-chat', { session_id: chatState.currentSessionId });
        await chatLoadHistory();
        renderSidebarFoldersTree();
        chatRenderContextPanel();
        toast('Chat added as a source', 'success', 1500);
    } catch (e) {
        toast(e.message || 'Failed to add chat as source', 'error');
    }
};

window.chatClearCurrent = function () {
    if (!chatState.currentSessionId) return;
    chatState.isThinking = true;
    api('POST', '/api/chat/sessions/' + chatState.currentSessionId + '/clear').then((resp) => {
        chatState.localMessages = [];
        chatState.lastSubmission = null;
        chatApplySessionMetadata(resp.session || null);
        chatRenderMessages();
        chatLoadHistory();
        chatState.isThinking = false;
        toast('Chat cleared', 'info', 1500);
        const input = document.getElementById('chat-input');
        if (input) input.focus();
    }).catch(e => { chatState.isThinking = false; toast('Error: ' + e.message, 'error'); });
};

window.chatQuick = function (text) {
    document.getElementById('chat-input').value = text;
    document.getElementById('chat-send-btn').disabled = false;
    chatSend();
};

// ── ABORT ─────────────────────────────────────────────────
window.chatAbort = async function () {
    if (!chatState.currentRequestId || !chatState.currentRequestCancelSupported || chatState.cancelRequested) return;
    const requestId = chatState.currentRequestId;
    const controller = chatState.chatAbortController;
    chatState.cancelRequested = true;
    try {
        const resp = await api('POST', '/api/chat/cancel', { request_id: requestId });
        if (!resp.cancelled) {
            chatState.cancelRequested = false;
            toast(resp.detail || 'Unable to cancel request', 'warning');
            return;
        }
        if (controller && !controller.signal.aborted) {
            controller.abort();
        }
    } catch (e) {
        chatState.cancelRequested = false;
        toast('Unable to cancel request: ' + e.message, 'warning');
    }
};

// ── REGENERATE ─────────────────────────────────────────────
window.chatRegenerate = async function () {
    if (chatState.isThinking) return;
    const last = chatState.lastSubmission;
    if (!last || (!last.message && !(last.files || []).length)) { toast('Nothing to regenerate', 'warning'); return; }
    // Clear last assistant response
    const container = document.getElementById('chat-messages');
    if (container) {
        const asst = container.querySelectorAll('.chat-msg.assistant');
        if (asst.length > 0) asst[asst.length - 1].remove();
    }
    chatState.localMessages.pop();
    // Re-send
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = last.message || '';
        chatAutoResize(input);
    }
    chatReplacePendingFiles(last.files || []);
    await chatSend();
};

window.chatSend = async function () {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message && chatState.pendingFiles.length === 0) return;
    if (chatState.isThinking) return;

    // Remove welcome if present
    const welcome = document.getElementById('chat-welcome');
    if (welcome) welcome.remove();
    chatPrepareTranscriptForConversation();

    chatState.isThinking = true;
    chatState.currentRequestCancelSupported = chatExpectedCancelSupport();
    chatState.cancelRequested = false;
    const pendingUploads = chatState.pendingFiles.map(chatClonePendingFile);
    chatState.lastSubmission = {
        message,
        files: pendingUploads.map(f => ({ ...f, preview_url: null })),
    };

    // Show thinking indicator
    const existing = document.getElementById('chat-thinking-dots');
    if (existing) existing.remove();
    const msgs = document.getElementById('chat-messages');
    const dots = document.createElement('div');
    dots.id = 'chat-thinking-dots';
    dots.className = 'chat-thinking';
    dots.innerHTML = '<div class="chat-thinking-bubble"><span class="chat-thinking-icon"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span><span class="chat-thinking-text">Hermes is thinking<span class="chat-thinking-ellipsis"></span></span>' + (chatState.currentRequestCancelSupported ? '<button class="chat-stop-btn" id="chat-stop-btn" onclick="chatAbort()" title="Stop"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' : '') + '</div>';
    if (msgs) msgs.appendChild(dots);

    // Swap send button to stop
    const sendBtn = document.getElementById('chat-send-btn');
    if (sendBtn && chatState.currentRequestCancelSupported) {
        sendBtn.classList.add('chat-stop-state');
        sendBtn.onclick = chatAbort;
        const svg = sendBtn.querySelector('svg');
        if (svg) svg.innerHTML = '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>';
    } else if (sendBtn) {
        sendBtn.disabled = true;
    }

    const files = pendingUploads.map(f => f.name);
    // Optimistically add user message to local
    const userMsg = { role: 'user', content: message, files, timestamp: new Date().toISOString() };
    chatState.localMessages.push(userMsg);
    chatAppendMsg('user', message, files);
    input.value = '';
    chatAutoResize(input);
    document.getElementById('chat-send-btn').disabled = !chatState.currentRequestCancelSupported;

    // AbortController for cancellation
    const controller = new AbortController();
    chatState.chatAbortController = controller;
    chatState.currentRequestId = makeRequestId();

    try {
        const resp = await api('POST', '/api/chat', {
            message, session_id: chatState.currentSessionId,
            folder_id: chatState.currentSessionId ? '' : (chatState.draftFolderId || chatState.selectedFolderId || ''),
            transport_preference: chatState.transportPreference || 'auto',
            request_id: chatState.currentRequestId,
            files: pendingUploads.map(f => ({ stored_as: f.stored_as, name: f.name })),
        }, controller.signal);
        chatState.currentSessionId = resp.session_id;
        chatApplySessionMetadata(resp.session || null);
        if (resp.user_message && chatState.localMessages.length > 0) {
            chatState.localMessages[chatState.localMessages.length - 1] = resp.user_message;
        }
        const assistantMsg = resp.assistant_message || { role: 'assistant', content: resp.response, timestamp: new Date().toISOString() };
        chatState.localMessages.push(assistantMsg);
        chatRenderMessages();
    } catch (e) {
        input.value = message;
        chatAutoResize(input);
        chatRenderFileBar();
        chatSyncSendButton();

        if (e.name === 'AbortError' || chatState.cancelRequested) {
            chatState.localMessages.pop();
            const container = document.getElementById('chat-messages');
            if (container) {
                const uls = container.querySelectorAll('.chat-msg.user');
                const last = uls[uls.length - 1];
                if (last) last.remove();
            }
            chatResetComposerAfterRequest();
            if (chatState.localMessages.length === 0) {
                chatRenderMessages();
            }
            chatLoadHistory();
            toast('Request cancelled', 'info', 2000);
            input.focus();
            return;
        }
        // Roll back the optimistic user message — it was never processed
        chatState.localMessages.pop(); // remove failed user msg from state
        // Now find and remove the user bubble (always the last .user in the container)
        const container = document.getElementById('chat-messages');
        if (container) {
            const userBubbles = container.querySelectorAll('.chat-msg.user');
            const lastUser = userBubbles[userBubbles.length - 1];
            if (lastUser) lastUser.remove();
        }
        chatResetComposerAfterRequest();
        if (chatState.localMessages.length === 0) {
            chatRenderMessages();
        }
        chatLoadHistory();
        toast(e.message || 'Request failed', 'error', 5000);
        input.focus();
        return;
    }

    chatReplacePendingFiles([]);
    chatResetComposerAfterRequest();
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

function chatAppendMsg(role, content, files = [], messageMeta = {}) {
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const div = chatBuildMessageNode({
        role,
        content,
        files,
        timestamp: new Date().toISOString(),
        ...messageMeta,
    });
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ── COPY MESSAGE ───────────────────────────────────────────
window.chatCopyMsg = function (btn) {
    const text = btn.getAttribute('data-text') || '';
    navigator.clipboard.writeText(text).then(function() {
        btn.classList.add('copied');
        setTimeout(function() { btn.classList.remove('copied'); }, 1500);
    }).catch(function() { toast('Copy failed', 'error'); });
};

function chatRenderMd(text) {
    if (!text) return '';
    let h = escH(text);

    // 1. Fenced code blocks and inline code are rendered first so they are
    // not touched by subsequent transforms (\n, \n\n, etc.).
    h = h.replace(/```(\w*)\n?([\s\S]*?)```/g, function(_, lang, code) {
        return '<pre class="code-block"' + (lang ? ' data-lang="' + lang + '"' : '') + '><code class="' + (lang ? 'language-' + lang : '') + '">' + code + '</code></pre>';
    });
    h = h.replace(/`([^`]+)`/g, function(_, code) {
        return '<code>' + code + '</code>';
    });

    h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
    h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // 2. Preserve list numbering/bullets instead of collapsing values like "269."
    h = h.replace(/^[-*] (.+)$/gm, '<li data-list="ul">$1</li>');
    h = h.replace(/((?:<li data-list="ul">.*?<\/li>\n*)+)/g, '<ul>$1</ul>');

    // 3. Ordered lists -> <ol>, preserving explicit numeric values.
    h = h.replace(/^(\d+)\. (.+)$/gm, '<li data-list="ol" value="$1">$2</li>');
    h = h.replace(/((?:<li data-list="ol"(?: value="[^"]+")?>.*?<\/li>\n*)+)/g, '<ol>$1</ol>');

    // 4. Consecutive blockquote lines -> single <blockquote> block.
    h = h.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
    h = h.replace(/(<blockquote>.*<\/blockquote>\n?)+/g, function(match) {
        // Collapse multiple separate blockquotes into one, preserving inner content.
        var inners = match.match(/<blockquote>(.*?)<\/blockquote>/g);
        if (!inners) return match;
        var merged = inners.map(function(bq) {
            // Strip the wrapper, keep inner HTML.
            return bq.replace(/^<blockquote>/, '').replace(/<\/blockquote>$/, '');
        }).join('\n');
        return '<blockquote>' + merged + '</blockquote>';
    });

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
    h = h.replace(/<p>(<ol>)/g, '$1');
    h = h.replace(/(<\/ol>)<\/p>/g, '$1');
    h = h.replace(/<p>(<blockquote>)/g, '$1');
    h = h.replace(/(<\/blockquote>)<\/p>/g, '$1');
    return h.replace(/<p><\/p>/g, '');
}

window.chatKeyDown = function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); chatSend(); }
};

window.chatAutoResize = function (el) {
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 300) + 'px';
};

async function chatUploadFile(file) {
    const unsupportedReason = chatDescribeUnsupportedFile(file);
    if (unsupportedReason) {
        if ((file.type || '').toLowerCase().startsWith('image/') && !chatState.capabilities.imageAttachments) {
            chatOpenVisionSetup(file.name || 'This image');
            return;
        }
        toast(unsupportedReason, 'warning', 5000);
        return;
    }
    const fd = new FormData();
    fd.append('file', file);
    try {
        const resp = await authFetch('/api/upload', {
            method: 'POST',
            body: fd,
        });
        if (resp.ok) {
            const data = await resp.json();
            data.preview_url = (file.type || '').toLowerCase().startsWith('image/') ? URL.createObjectURL(file) : null;
            chatState.pendingFiles.push(data);
            chatRenderFileBar();
            document.getElementById('chat-send-btn').disabled = false;
        } else {
            const err = await resp.json();
            const detail = Array.isArray(err.details) ? ' ' + err.details.join(' ') : '';
            toast('Upload failed: ' + (err.error || 'Request failed') + detail, 'error');
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
        '<div class="chat-file-item">' +
        ((f.preview_url && (f.type || '').toLowerCase().startsWith('image/'))
            ? '<img class="chat-file-thumb" src="' + escA(f.preview_url) + '" alt="' + escA(f.name) + '">' :
            '<span>' + chatFileIcon(f.type) + '</span>') +
        '<span>' + escH(f.name) + '</span><span style="color:var(--text-muted);font-size:11px">' + chatFmtSize(f.size) + '</span><button class="remove-file" onclick="chatRemoveFile(' + i + ')">\u2715</button></div>'
    ).join('');
}

function chatFileIcon(mime) {
    return mime?.startsWith('image/')
        ? UI_ICONS.image
        : mime?.startsWith('audio/')
            ? UI_ICONS.audio
            : mime?.includes('pdf')
                ? UI_ICONS.pdf
                : UI_ICONS.file;
}
function chatFmtSize(b) { return b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB'; }

function chatSyncSendButton() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send-btn');
    if (!sendBtn) return;
    sendBtn.disabled = !(input?.value.trim() || chatState.pendingFiles.length > 0);
}

window.chatRemoveFile = function (i) {
    chatReleasePendingFile(chatState.pendingFiles[i]);
    chatState.pendingFiles.splice(i, 1);
    chatRenderFileBar();
    chatSyncSendButton();
};
window.chatClearFiles = function () {
    chatReplacePendingFiles([]);
};

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
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('chat-voice-btn');
    const status = document.getElementById('chat-voice-status');
    if (chatState.speechSupported && chatState.recognition) {
        chatState.voiceBaseText = input?.value.trim() || '';
        chatState.voiceFinalTranscript = '';
        // Auto-resize textarea before voice starts
        if (input) { chatAutoResize(input); }
        try { chatState.recognition.start(); } catch(e) { /* already started */ }
        chatState.isRecording = true;
        chatState.micStoppedByUser = false;
        btn.classList.add('recording');
        status.textContent = 'Listening... (click mic to stop)';
        status.classList.add('active');
    } else if (chatState.capabilities.audioAttachments && navigator.mediaDevices) {
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
    } else {
        toast('Voice input is unavailable in this browser and Hermes does not support audio uploads here.', 'warning');
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
    chatState.voiceBaseText = '';
    chatState.voiceFinalTranscript = '';
}

/* ═══════════════════════════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
    ThemeManager.init();

    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            if (item.dataset.screen === 'folders') {
                const wasActive = item.classList.contains('active');
                navigate('folders');
                toggleSidebarFolders(wasActive ? !sidebarFoldersExpanded() : true);
                return;
            }
            navigate(item.dataset.screen);
        });
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
        renderSidebarFoldersTree();
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
    navigate(direct && Screens[direct] ? direct : 'chat');
    renderSidebarFoldersTree();
    // Listen for hash changes
    window.addEventListener('hashchange', () => {
        const h = window.location.hash.replace('#', '');
        if (h && Screens[h]) navigate(h);
    });
});

setInterval(checkHealth, 10000);
