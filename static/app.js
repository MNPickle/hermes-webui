/* Hermes Admin Panel - Main Application JavaScript */

const API = { base: '' };
const WEB_UI_VERSION = '1.2.0';
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

function bootstrapTokenFromUrl() {
    try {
        const url = new URL(window.location.href);
        const token = (url.searchParams.get('token') || '').trim();
        if (!token) return;
        setToken(token);
        url.searchParams.delete('token');
        window.history.replaceState({}, document.title, url.pathname + (url.search || '') + url.hash);
    } catch {}
}

// AuthRequired sentinel
class AuthRequired extends Error {
    constructor() { super('Authentication required'); this.name = 'AuthRequired'; }
}

// --- Login screen flow ---
let _authed = false;

async function checkAuthSession() {
    try {
        const resp = await fetch(API.base + '/api/auth/check', { credentials: 'include' });
        return resp.ok;
    } catch { return false; }
}

function showLoginScreen() {
    document.getElementById('login-screen').style.display = 'flex';
    document.getElementById('app').style.display = 'none';
}

function showApp() {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app').style.display = '';
    _authed = true;
}

function initLoginForm() {
    const form = document.getElementById('login-form');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('login-submit');
        const errEl = document.getElementById('login-error');
        const username = document.getElementById('login-username').value.trim();
        const password = document.getElementById('login-password').value;
        errEl.style.display = 'none';
        btn.disabled = true;
        btn.textContent = 'Signing in...';
        try {
            const resp = await fetch(API.base + '/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password }),
                credentials: 'include',
            });
            if (resp.ok) {
                showApp();
                bootstrapApp();
            } else {
                const d = await resp.json().catch(() => ({}));
                errEl.textContent = d.error || 'Invalid credentials';
                errEl.style.display = 'block';
            }
        } catch (err) {
            errEl.textContent = 'Connection error';
            errEl.style.display = 'block';
        } finally {
            btn.disabled = false;
            btn.textContent = 'Sign in';
        }
    });
}

async function authFetch(path, options = {}, signal) {
    const headers = new Headers(options.headers || {});
    // Include cookies for session auth
    const fetchOpts = { ...options, headers, signal, credentials: 'include' };

    // Also send Bearer token if available (legacy compat)
    const token = getToken();
    if (token && !headers.has('Authorization')) headers.set('Authorization', 'Bearer ' + token);

    let resp = await fetch(API.base + path, fetchOpts);
    if (resp.ok) return resp;
    if (resp.status === 401) {
        // Session expired — show login screen
        _authed = false;
        showLoginScreen();
        throw new AuthRequired();
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
        let errData = null;
        try {
            const d = await resp.json();
            errData = d;
            errMsg = d.error || d.message || (Array.isArray(d.details) ? d.details.join('; ') : errMsg);
        } catch {}
        const err = new Error(errMsg);
        err.status = resp.status;
        err.responseData = errData;
        throw err;
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
        agents: 'Agents', capabilities: 'Create Capability', skills: 'Skills', channels: 'Apps & Integrations',
        hooks: 'Raw Hooks', cron: 'Cron Jobs', sessions: 'Session Reset', logs: 'Log File Tail', chat: 'Chat'
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

function currentScreenId() {
    return document.querySelector('.nav-item.active')?.dataset.screen || 'dashboard';
}

function screenIsActive(screen) {
    return currentScreenId() === screen;
}

function refreshCurrentScreen() {
    const screen = currentScreenId();
    if (Screens[screen]) Screens[screen]();
}

let _healthCache = null, _healthTs = 0;

function activeProfileBadgeH(profile, compact = false) {
    const value = String(profile || 'unknown').trim() || 'unknown';
    const label = compact ? '' : '<span class="profile-indicator-label">Profile</span>';
    return '' +
        '<div class="profile-indicator">' +
            label +
            '<span class="badge badge-accent" title="Active Hermes profile">' + escH(value) + '</span>' +
        '</div>';
}

function activeProfileSidebarH(profile) {
    const value = String(profile || 'unknown').trim() || 'unknown';
    return '' +
        '<div class="profile-indicator profile-indicator--sidebar">' +
            '<span class="profile-indicator-label">Profile</span>' +
            '<span class="badge badge-accent" title="Active Hermes profile">' + escH(value) + '</span>' +
        '</div>';
}

function runtimeProfileContextCardH(health, apiUrl, title = 'Runtime Context', description = '') {
    const resolvedApiUrl = String(apiUrl || health?.api_url || '(not set)').trim() || '(not set)';
    return '' +
        '<div class="card mb-16"><div class="card-header"><span>' + escH(title) + '</span></div><div class="card-body">' +
            (description ? '<p class="text-sm text-secondary mb-16">' + escH(description) + '</p>' : '') +
            '<div class="form-row">' +
                '<div class="form-group"><label class="form-label">Active Profile</label><div>' + activeProfileBadgeH(health?.profile) + '</div></div>' +
                '<div class="form-group"><label class="form-label">Hermes Home</label><div class="font-mono text-sm">' + escH(health?.hermes_home || '?') + '</div></div>' +
                '<div class="form-group"><label class="form-label">API Server</label><div class="font-mono text-sm">' + escH(resolvedApiUrl) + '</div></div>' +
                '<div class="form-group"><label class="form-label">Gateway Status</label><div>' + (health?.gateway_running ? '<span class="badge badge-success">Running</span>' : '<span class="badge badge-danger">Stopped</span>') + '</div></div>' +
            '</div>' +
        '</div></div>';
}

function updateActiveProfileIndicators(health) {
    const profile = health?.profile || 'unknown';
    const topbar = document.getElementById('topbar-active-profile');
    const sidebar = document.getElementById('sidebar-active-profile');
    if (topbar) topbar.innerHTML = activeProfileBadgeH(profile, true);
    if (sidebar) sidebar.innerHTML = activeProfileSidebarH(profile);
}

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
        updateActiveProfileIndicators(d);
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
        updateActiveProfileIndicators({ profile: 'unavailable' });
    }
    _healthCache = true; _healthTs = now;
}

function copyText(text, successMsg = 'Copied') {
    const value = String(text || '');
    if (!value) {
        toast('Nothing to copy', 'warning');
        return Promise.resolve(false);
    }
    return navigator.clipboard.writeText(value)
        .then(() => {
            toast(successMsg, 'success', 1800);
            return true;
        })
        .catch(() => {
            toast('Copy failed', 'error');
            return false;
        });
}

function hermesUpdateStatusMeta(status, state = null) {
    if (status === 'update_available' && state?.update_scope === 'revision') {
        return { label: 'Updates Available', badge: 'badge-warning', title: 'Hermes updates available on main' };
    }
    return {
        up_to_date: { label: 'Up to Date', badge: 'badge-success', title: 'Hermes is current' },
        checking: { label: 'Checking', badge: 'badge-info', title: 'Checking for updates' },
        update_available: { label: 'Update Available', badge: 'badge-warning', title: 'New Hermes version available' },
        update_in_progress: { label: 'Update In Progress', badge: 'badge-info', title: 'Updating Hermes' },
        update_failed: { label: 'Update Failed', badge: 'badge-danger', title: 'Hermes update failed' },
        unknown_latest: { label: 'Latest Unknown', badge: 'badge-danger', title: 'Unable to determine latest version' },
    }[status] || { label: 'Unknown', badge: 'badge-secondary', title: 'Hermes update status' };
}

function hermesUpdateVersionLabel(info, fallback = 'Unknown') {
    return info?.display || fallback;
}

function hermesUpdateSourceLabel(state) {
    return state?.official_source?.label || 'Unavailable';
}

function hermesUpdateWorktreeLabel(state) {
    const worktree = state?.worktree || {};
    const tracked = Number(worktree.tracked || 0);
    const untracked = Number(worktree.untracked || 0);
    if (!tracked && !untracked) return 'Clean worktree';
    const parts = [];
    if (tracked) parts.push(`${tracked} tracked`);
    if (untracked) parts.push(`${untracked} untracked`);
    return parts.join(' + ');
}

function hermesUpdateShouldOfferAction(state) {
    if (!state) return false;
    if (state.status === 'update_in_progress') return false;
    return !!state.can_update && state.status !== 'up_to_date';
}

function renderHermesUpdateLogPanel(state) {
    const action = state?.update_action || {};
    const logText = action.log_text || '';
    if (!logText) return '';
    const title = state?.status === 'update_in_progress'
        ? 'Live Update Log'
        : (state?.status === 'update_failed' ? 'Update Failure Log' : 'Recent Update Log');
    return `
        <div class="update-log-panel">
            <span class="update-card-item-label">${escH(title)}</span>
            <pre class="font-mono text-xs">${escH(logText)}</pre>
        </div>
    `;
}

function renderHermesUpdateCard(state) {
    if (!state) {
        return '<div class="card"><div class="card-header"><span>Hermes Updates</span><span class="badge badge-info">Checking</span></div><div class="card-body"><div class="loading"><div class="spinner"></div></div></div></div>';
    }
    const meta = hermesUpdateStatusMeta(state.status, state);
    const installed = hermesUpdateVersionLabel(state.installed_version);
    const latest = hermesUpdateVersionLabel(state.latest_version, 'Unknown');
    const canUpdate = hermesUpdateShouldOfferAction(state);
    const actionLabel = state.status === 'update_failed' ? 'Retry Update' : 'Update Hermes';
    const command = state.manual_command || '';
    const sourceLabel = hermesUpdateSourceLabel(state);
    const checkedAt = state.checked_at ? new Date(state.checked_at).toLocaleString() : 'Not checked yet';
    const behind = typeof state.behind_commits === 'number' ? state.behind_commits : null;
    const ahead = typeof state.ahead_commits === 'number' ? state.ahead_commits : null;
    const commitSummary = [
        behind !== null ? `${behind} behind` : '',
        ahead !== null && ahead > 0 ? `${ahead} ahead` : '',
    ].filter(Boolean).join(' · ') || 'Unknown';
    const scopeLabel = state.update_scope === 'revision'
        ? 'Same released version, newer commits on main'
        : (state.update_scope === 'release' ? 'Newer released version available' : 'Unknown');

    return `
        <div class="card" id="hermes-update-card">
            <div class="card-header">
                <span>Hermes Updates</span>
                <span class="badge ${meta.badge}">${escH(meta.label)}</span>
            </div>
            <div class="card-body">
                <p class="text-sm text-secondary mb-16">${escH(state.message || meta.title)}</p>
                <div class="update-card-grid">
                    <div class="update-card-item">
                        <span class="update-card-item-label">Installed Version</span>
                        <div class="update-card-item-value font-mono text-sm">${escH(installed)}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Latest Known Version</span>
                        <div class="update-card-item-value font-mono text-sm">${escH(latest)}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Update Type</span>
                        <div class="update-card-item-value text-sm">${escH(scopeLabel)}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Update Source</span>
                        <div class="update-card-item-value text-sm">${escH(sourceLabel)}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Git Status</span>
                        <div class="update-card-item-value text-sm">${escH(commitSummary)}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Managed Install</span>
                        <div class="update-card-item-value text-sm">${escH(state.managed_system || 'No')}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Local Changes</span>
                        <div class="update-card-item-value text-sm">${escH(hermesUpdateWorktreeLabel(state))}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Hermes Binary</span>
                        <div class="update-card-item-value font-mono text-sm">${escH(state.bin_path || 'Unknown')}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Project Root</span>
                        <div class="update-card-item-value font-mono text-sm">${escH(state.project_root || 'Unknown')}</div>
                    </div>
                    <div class="update-card-item">
                        <span class="update-card-item-label">Last Checked</span>
                        <div class="update-card-item-value text-sm">${escH(checkedAt)}</div>
                    </div>
                </div>
                <div class="update-card-actions">
                    <button class="btn" onclick="hermesUpdateCheckNow(this)">Check Now</button>
                    ${canUpdate ? `<button class="btn btn-primary" onclick="openHermesUpdateConfirm()">${escH(actionLabel)}</button>` : ''}
                    ${command ? `<button class="btn" onclick="copyHermesUpdateCommand()">${state.can_update ? 'Copy Manual Command' : 'Copy Update Command'}</button>` : ''}
                </div>
                ${state.selection_reason ? `<p class="text-sm text-secondary mt-16">${escH(state.selection_reason)}</p>` : ''}
                ${state.manual_reason ? `<p class="text-sm text-secondary mt-16">${escH(state.manual_reason)}</p>` : ''}
                ${command ? `<div class="update-card-command"><span class="update-card-item-label">Manual Command</span><code>${escH(command)}</code></div>` : ''}
                ${renderHermesUpdateLogPanel(state)}
            </div>
        </div>
    `;
}

function renderGlobalStatusBanner() {
    const container = document.getElementById('global-status-banner');
    if (!container) return;
    const state = HermesUpdate.state;
    const hideForRevisionOnly = state?.status === 'update_available' && state?.update_scope === 'revision';
    if (!state || state.status === 'up_to_date' || hideForRevisionOnly) {
        container.innerHTML = '';
        container.classList.add('hidden');
        return;
    }
    const meta = hermesUpdateStatusMeta(state.status, state);
    const canUpdate = hermesUpdateShouldOfferAction(state);
    const message = state.message || meta.title;
    container.classList.remove('hidden');
    container.innerHTML = `
        <div class="update-banner status-${escA(state.status || '')}">
            <!-- The Close Button -->
            <button class="card-close-btn" 
                    onclick="closeHermesUpdateCard()" 
                    title="Ocultar tarjeta de actualizaciones"
                    style="position: absolute; top: 10px; right: 10px; background: transparent; border: none; font-size: 24px; cursor: pointer; color: inherit; line-height: 1; z-index: 9999;">
            &times;
            </button>
            <div class="update-banner-copy">
                <div class="mb-8"><span class="badge ${meta.badge}">${escH(meta.label)}</span></div>
                <h3>${escH(meta.title)}</h3>
                <p class="text-sm">${escH(message)}</p>
            </div>
            <div class="update-banner-actions">
                <button class="btn btn-sm" onclick="navigate('service')">Open Service</button>
                <button class="btn btn-sm" onclick="hermesUpdateCheckNow(this)">Check Now</button>
                ${canUpdate ? '<button class="btn btn-primary btn-sm" onclick="openHermesUpdateConfirm()">Update Hermes</button>' : ''}
                ${state.manual_command ? '<button class="btn btn-sm" onclick="copyHermesUpdateCommand()">Copy Command</button>' : ''}
            </div>
        </div>
    `;
}

const HermesUpdate = {
    state: null,
    loadingPromise: null,
    pollTimer: null,

    async ensureLoaded(force = false) {
        if (force || !this.state) {
            await this.refresh(force);
        }
        return this.state;
    },

    applyState(nextState) {
        const prevStatus = this.state?.status || '';
        this.state = nextState || null;
        renderGlobalStatusBanner();
        this.syncPolling();
        if (prevStatus === 'update_in_progress' && this.state && this.state.status !== 'update_in_progress') {
            if (this.state.status === 'update_failed') {
                toast(this.state.message || 'Hermes update failed', 'error', 6000);
            } else {
                toast('Hermes update finished', 'success', 2500);
            }
            _healthCache = null;
            checkHealth();
            if (screenIsActive('dashboard') || screenIsActive('service')) {
                refreshCurrentScreen();
            }
        }
    },

    syncPolling() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
        if (this.state?.status === 'update_in_progress') {
            this.pollTimer = setInterval(() => {
                this.refresh(true, { silent: true }).catch(() => {});
            }, 3000);
        }
    },

    async refresh(force = false, { checking = false, silent = false } = {}) {
        if (checking) {
            this.applyState({
                ...(this.state || {}),
                status: 'checking',
                availability_status: this.state?.availability_status || 'checking',
                update_scope: this.state?.update_scope || 'unknown',
                message: 'Checking the installed Hermes version and official update source...',
                can_update: this.state?.can_update || false,
                manual_command: this.state?.manual_command || '',
                update_action: this.state?.update_action || {},
            });
        }
        if (this.loadingPromise && !force) {
            return this.loadingPromise;
        }
        const path = '/api/hermes/update-status' + (force ? '?refresh=1' : '');
        this.loadingPromise = api('GET', path)
            .then((data) => {
                this.applyState(data);
                return data;
            })
            .catch((e) => {
                if (!silent) {
                    toast('Hermes update status failed: ' + e.message, 'error');
                }
                this.applyState({
                    ...(this.state || {}),
                    status: 'unknown_latest',
                    availability_status: 'unknown_latest',
                    update_scope: 'unknown',
                    message: e.message || 'Unable to determine the latest Hermes version.',
                    can_update: this.state?.can_update || false,
                    manual_command: this.state?.manual_command || '',
                    update_action: this.state?.update_action || {},
                });
                throw e;
            })
            .finally(() => {
                this.loadingPromise = null;
            });
        return this.loadingPromise;
    },
};

window.hermesUpdateCheckNow = async function (btn) {
    try {
        setBtnLoading(btn, true);
        await HermesUpdate.refresh(true, { checking: true });
        if (screenIsActive('dashboard') || screenIsActive('service')) refreshCurrentScreen();
    } catch {}
    finally {
        setBtnLoading(btn, false);
    }
};

window.copyHermesUpdateCommand = function () {
    return copyText(HermesUpdate.state?.manual_command || '', 'Update command copied');
};

window.openHermesUpdateConfirm = function () {
    const state = HermesUpdate.state;
    if (!state) {
        toast('Hermes update status is still loading', 'warning');
        return;
    }
    if (!state.can_update) {
        showModal(
            'Manual Hermes Update',
            '<p class="mb-12">' + escH(state.manual_reason || 'This Hermes install cannot be updated directly from the web UI.') + '</p>' +
            (state.manual_command
                ? '<div class="update-card-command"><span class="update-card-item-label">Run this command</span><code>' + escH(state.manual_command) + '</code></div>'
                : ''),
            '<button class="btn" onclick="closeModal()">Close</button>' +
            (state.manual_command ? '<button class="btn btn-primary" onclick="copyHermesUpdateCommand()">Copy Command</button>' : '')
        );
        return;
    }
    const worktree = state.worktree || {};
    const detail = [];
    if (worktree.total) detail.push(`Local changes detected: ${hermesUpdateWorktreeLabel(state)}.`);
    detail.push('Hermes will run its built-in update command for the install currently managed by this Web UI.');
    detail.push('If local changes cannot be restored cleanly, Hermes will log the recovery steps and keep the saved stash reference.');
    showModal(
        'Update Hermes',
        '<p class="mb-12">This will update <strong>' + escH(hermesUpdateVersionLabel(state.installed_version)) + '</strong>' +
            (state.latest_version?.version ? ' toward <strong>' + escH(hermesUpdateVersionLabel(state.latest_version)) + '</strong>.' : '.') +
        '</p>' +
        '<p class="text-sm text-secondary mb-12">' + escH(detail.join(' ')) + '</p>' +
        (state.manual_command
            ? '<div class="update-card-command"><span class="update-card-item-label">Command Hermes Web UI will run</span><code>' + escH(state.manual_command) + '</code></div>'
            : ''),
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" id="confirm-hermes-update-btn" onclick="runHermesUpdate(this)">Update Hermes</button>'
    );
};

window.runHermesUpdate = async function (btn) {
    try {
        setBtnLoading(btn, true);
        const resp = await api('POST', '/api/hermes/update', { confirm: true });
        closeModal();
        if (resp.status) HermesUpdate.applyState(resp.status);
        toast(resp.message || 'Hermes update started', 'info', 2500);
        if (screenIsActive('dashboard') || screenIsActive('service')) refreshCurrentScreen();
    } catch (e) {
        toast('Hermes update could not start: ' + e.message, 'error', 5000);
    } finally {
        setBtnLoading(btn, false);
    }
};

/* ═══════════════════════════════════════════════════════════════
   SCREENS
   ═══════════════════════════════════════════════════════════════ */

const Screens = {};

// ── DASHBOARD ──────────────────────────────────────────────
Screens.dashboard = async function () {
    const content = document.getElementById('content');
    try {
        const [health, sys, tools, updateState] = await Promise.all([
            api('GET', '/api/health').catch(() => ({ gateway_running: false, version: '?' })),
            api('GET', '/api/system').catch(() => ({ python_version: '?', os_info: '?', disk_free: '?' })),
            api('GET', '/api/tools').catch(() => ({ tools: [], total_enabled: 0, total_disabled: 0 })),
            HermesUpdate.ensureLoaded().catch(() => null),
        ]);
        const updateMeta = hermesUpdateStatusMeta(updateState?.status || 'unknown_latest');
        const installedVersion = hermesUpdateVersionLabel(updateState?.installed_version, (health.version || '?').split('\n')[0]);
        content.innerHTML = `
        <div class="stats-grid">
            <div class="stat-card ${health.gateway_running ? 'green' : 'red'}">
                <div class="stat-value">${health.gateway_running ? '\u25cf Running' : '\u25cb Stopped'}</div>
                <div class="stat-label">Gateway Status</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">${escH(installedVersion)}</div>
                <div class="stat-label">Hermes Version</div>
            </div>
            <div class="stat-card ${updateState?.status === 'update_available' ? 'amber' : (updateState?.status === 'update_failed' || updateState?.status === 'unknown_latest' ? 'red' : 'green')}">
                <div class="stat-value">${escH(updateMeta.label)}</div>
                <div class="stat-label">Hermes Update Status</div>
            </div>
            <div class="stat-card blue">
                <div class="stat-value">${escH(sys.python_version || '?')}</div>
                <div class="stat-label">Python</div>
            </div>
        </div>
        ${renderHermesUpdateCard(updateState)}
        <div class="card">
            <div class="card-header"><span>Quick Actions</span></div>
            <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
                <button class="btn btn-success" onclick="serviceAction('start')">\u25b6 Start Gateway</button>
                <button class="btn btn-danger" onclick="serviceAction('stop')">\u25a0 Stop Gateway</button>
                <button class="btn btn-primary" onclick="serviceAction('restart')">\u21bb Restart Gateway</button>
<button class="btn" onclick="serviceAction('doctor')">Run Diagnostics</button>
                <button class="btn" onclick="reloadConfig()">\u21bb Reload Config</button>
                <button class="btn" onclick="navigate('service')">Open Service</button>
            </div>
        </div>
        <div class="card">
            <div class="card-header"><span>System Info</span></div>
            <div class="card-body">
                <div class="form-row">
                    <div class="form-group"><label class="form-label">Active Profile</label><div>${activeProfileBadgeH(health.profile)}</div></div>
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
function closeHermesUpdateCard() {
  const banner = document.getElementById('global-status-banner');
  if (banner) {
    // Hide the banner
    banner.style.display = 'none';
    
    // Optional: Save the state in localStorage so it stays hidden on refresh
    // localStorage.setItem('hermes_banner_hidden', 'true');
  }
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

async function loadRuntimeProfiles() {
    return api('GET', '/api/runtime/profiles');
}

async function loadRuntimeProfileApiToken(profileName) {
    return api('GET', '/api/runtime/profiles/' + encodeURIComponent(profileName) + '/api-token');
}

const runtimeProfileSettingsState = {
    profileData: null,
    tokenMeta: null,
};

function renderRuntimeProfileCard(profileData, status) {
    const selected = profileData?.selected || 'default';
    const profiles = Array.isArray(profileData?.profiles) ? profileData.profiles : [];
    const options = profiles.map(profile => ({
        value: profile.name,
        label: profile.name + (profile.is_root_active ? ' - backend active' : ''),
    }));
    const currentApiUrl = status?.api_url || profileData?.paths?.env || '(unknown)';
    return '' +
        '<div class="card mb-16"><div class="card-header"><span>Hermes Profile</span></div><div class="card-body">' +
            '<p class="text-sm text-secondary mb-16">Choose which Hermes profile this portal should read for config, env vars, CLI chats, and gateway actions. This does not modify the backend\'s own active profile file.</p>' +
            '<div style="display:grid;grid-template-columns:repeat(2, minmax(0, 1fr));gap:16px;align-items:start;margin-bottom:16px">' +
                '<div style="display:flex;flex-direction:column;gap:12px">' +
                    '<div class="form-group"><label class="form-label">Profile</label>' + selectH('runtime-profile-select', options, selected) + '</div>' +
                    '<div class="form-group"><label class="form-label">API Server</label><div class="font-mono text-sm">' + escH(currentApiUrl) + '</div></div>' +
                    '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
                        '<button class="btn btn-primary" onclick="saveRuntimeProfile(this)">Use Profile</button>' +
                        '<button class="btn" onclick="Screens.settings()">Refresh</button>' +
                    '</div>' +
                '</div>' +
                '<div style="display:flex;flex-direction:column;gap:12px">' +
                    '<div class="form-group"><label class="form-label">Hermes Home</label><div id="runtime-profile-home" class="font-mono text-sm">' + escH(profileData?.paths?.home || '') + '</div></div>' +
                    '<div class="card" style="border-style:dashed"><div class="card-body">' +
                        '<div style="display:grid;grid-template-columns:minmax(0, 1fr);gap:12px">' +
                            '<div class="form-group" style="flex:2 1 320px"><label class="form-label">API Server Token</label>' + inputH('runtime-profile-api-token', '', 'password', 'Set or replace the token for this gateway port') + '<div id="runtime-profile-api-token-status" class="form-hint" style="margin-top:8px"></div></div>' +
                            '<div class="form-group" style="flex:1 1 220px"><label class="form-label">Stored In</label><div id="runtime-profile-api-token-path" class="font-mono text-sm text-muted">Loading...</div></div>' +
                        '</div>' +
                        '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
                            '<button class="btn btn-primary" onclick="saveRuntimeProfileApiToken(this)">Save API Token</button>' +
                            '<button class="btn" onclick="clearRuntimeProfileApiToken(this)">Clear Token</button>' +
                        '</div>' +
                    '</div></div>' +
                '</div>' +
            '</div>' +
        '</div></div>';
}

function runtimeProfileSelectionMeta(profileName) {
    const profiles = Array.isArray(runtimeProfileSettingsState.profileData?.profiles) ? runtimeProfileSettingsState.profileData.profiles : [];
    return profiles.find(profile => profile.name === profileName) || null;
}

async function refreshRuntimeProfileTokenCard(profileName) {
    const normalized = profileName || document.getElementById('runtime-profile-select')?.value || 'default';
    const homeEl = document.getElementById('runtime-profile-home');
    const pathEl = document.getElementById('runtime-profile-api-token-path');
    const statusEl = document.getElementById('runtime-profile-api-token-status');
    const input = document.getElementById('runtime-profile-api-token');
    const profileMeta = runtimeProfileSelectionMeta(normalized);
    if (homeEl && profileMeta?.home) homeEl.textContent = profileMeta.home;
    if (pathEl) pathEl.textContent = 'Loading...';
    if (statusEl) statusEl.textContent = 'Loading token status...';
    if (input) input.value = '';
    try {
        const tokenMeta = await loadRuntimeProfileApiToken(normalized);
        runtimeProfileSettingsState.tokenMeta = tokenMeta;
        if (pathEl) pathEl.textContent = tokenMeta.env_path || '(unknown)';
        if (statusEl) {
            statusEl.textContent = tokenMeta.has_token
                ? ('Gateway port ' + (tokenMeta.api_port || '?') + ' saved as ' + (tokenMeta.token_key || 'HERMES_API_TOKEN') + ': ' + (tokenMeta.masked_token || 'hidden'))
                : ('No API token saved for gateway port ' + (tokenMeta.api_port || '?') + '.');
        }
    } catch (e) {
        runtimeProfileSettingsState.tokenMeta = null;
        if (pathEl) pathEl.textContent = '(unavailable)';
        if (statusEl) statusEl.textContent = 'Token status unavailable: ' + e.message;
    }
}

window.saveRuntimeProfileApiToken = async function (btn) {
    const select = document.getElementById('runtime-profile-select');
    const input = document.getElementById('runtime-profile-api-token');
    if (!select || !input) return;
    const profileName = select.value || 'default';
    const token = input.value || '';
    if (!token.trim()) {
        toast('Enter a token first, or use Clear Token.', 'warning');
        return;
    }
    try {
        setBtnLoading(btn, true);
        await api('PUT', '/api/runtime/profiles/' + encodeURIComponent(profileName) + '/api-token', { token: token.trim() });
        input.value = '';
        toast('API token saved for profile ' + profileName, 'success');
        await refreshRuntimeProfileTokenCard(profileName);
    } catch (e) {
        toast('API token save failed: ' + e.message, 'error');
    } finally {
        setBtnLoading(btn, false);
    }
};

window.clearRuntimeProfileApiToken = async function (btn) {
    const select = document.getElementById('runtime-profile-select');
    const input = document.getElementById('runtime-profile-api-token');
    if (!select) return;
    const profileName = select.value || 'default';
    try {
        setBtnLoading(btn, true);
        await api('PUT', '/api/runtime/profiles/' + encodeURIComponent(profileName) + '/api-token', { token: '' });
        if (input) input.value = '';
        toast('API token cleared for profile ' + profileName, 'success');
        await refreshRuntimeProfileTokenCard(profileName);
    } catch (e) {
        toast('API token clear failed: ' + e.message, 'error');
    } finally {
        setBtnLoading(btn, false);
    }
};

window.saveRuntimeProfile = async function (btn) {
    const select = document.getElementById('runtime-profile-select');
    if (!select) return;
    try {
        setBtnLoading(btn, true);
        const selectedProfile = select.value || 'default';
        const shouldSwitchCurrentChat = !!chatState.currentSessionId && chatVisibleProfile() !== selectedProfile;
        await api('PUT', '/api/runtime/profiles', { profile: selectedProfile });
        chatState.activeProfile = selectedProfile;
        if (shouldSwitchCurrentChat) {
            const resp = await api('PUT', '/api/chat/sessions/' + chatState.currentSessionId + '/profile', { profile: selectedProfile });
            chatApplySessionMetadata(resp.session || null);
        } else if (!chatState.currentSessionId) {
            chatState.currentSessionProfile = selectedProfile;
            if (currentScreenId() === 'chat') chatRenderSessionBanner();
        }
        updateChatHistoryActiveProfileBadge();
        _healthCache = null;
        checkHealth();
        window.modelRolesCache = null;
        window.providerEnvCache = null;
        toast('Hermes profile updated', 'success');
        await Screens.settings();
    } catch (e) {
        toast('Profile update failed: ' + e.message, 'error');
    } finally {
        setBtnLoading(btn, false);
    }
};

// ── SETTINGS ───────────────────────────────────────────────
Screens.settings = async function () {
    const content = document.getElementById('content');
    try {
        const [cfg, status, profileData] = await Promise.all([
            api('GET', '/api/config'),
            api('GET', '/api/chat/status').catch(() => ({})),
            loadRuntimeProfiles(),
        ]);
        runtimeProfileSettingsState.profileData = profileData;
        runtimeProfileSettingsState.tokenMeta = null;
        const personalities = Object.keys(cfg.personalities || {});
        const runtime = status.runtime || {};
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

            let tabsHtml = renderRuntimeProfileCard(profileData, status) + '<div class="tabs">' + tabs.map((t, i) => '<button class="tab' + (i === 0 ? ' active' : '') + '" data-tab="' + t.id + '">' + t.label + '</button>').join('') + '</div>';
        let panelsHtml = tabs.map((t, i) => {
            let formHtml = '';
            t.sections.forEach(sec => {
                const data = cfg[sec];
                if (!data || typeof data !== 'object') return;
                if (t.id === 'memory' && sec === 'memory') {
                    formHtml += renderSettingsMemoryStatusCard(runtime.memory || {});
                }
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
        const runtimeSelect = document.getElementById('runtime-profile-select');
        if (runtimeSelect) {
            runtimeSelect.addEventListener('change', () => refreshRuntimeProfileTokenCard(runtimeSelect.value || 'default'));
            await refreshRuntimeProfileTokenCard(runtimeSelect.value || 'default');
        }
    } catch (e) {
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

function runtimeEnvSourceLabel(source) {
    if (source === 'process_env') return 'Process environment';
    if (source === 'repo_env') return 'Repo .env';
    if (source === 'hermes_env') return 'Hermes env vars';
    return 'Not found';
}

function renderSettingsMemoryStatusCard(memory) {
    const enabled = !!memory.enabled;
    const cliTool = !!memory.cli_tool_enabled;
    const ready = !!memory.semantic_search_ready;
    const keyPresent = !!memory.openai_api_key_present;
    const keySource = runtimeEnvSourceLabel(memory.openai_api_key_source || '');
    const actionLabel = keyPresent ? 'Edit OpenAI Key' : 'Set OpenAI Key';
    const actionClass = ready ? 'btn' : 'btn btn-primary';
    let html = '<div class="card mb-16"><div class="card-header"><span>Memory Status</span>' +
        '<span class="badge ' + (ready ? 'badge-success' : (enabled ? 'badge-warning' : 'badge-secondary')) + '">' +
        escH(ready ? 'OpenAI Ready' : (enabled ? 'Needs OpenAI Key' : 'Local Only')) +
        '</span></div><div class="card-body">';
    html += '<div class="runtime-readiness-grid">';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Local Memory</div><div class="runtime-readiness-value">' + escH(enabled ? 'Enabled' : 'Disabled') + '</div><div class="runtime-readiness-detail">' + escH(enabled ? 'Hermes will keep using its built-in local memory.' : 'Turn memory on below if you want Hermes to retain memory locally.') + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">OpenAI Recall</div><div class="runtime-readiness-value">' + escH(ready ? 'Ready' : (keyPresent ? 'Waiting on CLI' : 'Key Missing')) + '</div><div class="runtime-readiness-detail">' + escH(memory.detail || 'Memory status unavailable.') + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">CLI Memory Tool</div><div class="runtime-readiness-value">' + escH(cliTool ? 'Active' : 'Inactive') + '</div><div class="runtime-readiness-detail">' + escH(cliTool ? 'CLI chats can use Hermes memory.' : 'Enable the CLI memory tool if you want memory available during Hermes CLI turns.') + '</div></div>';
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">OpenAI Key</div><div class="runtime-readiness-value">' + escH(keyPresent ? 'Saved' : 'Missing') + '</div><div class="runtime-readiness-detail">' + escH(keyPresent ? ('Found in ' + keySource + '.') : 'Add OPENAI_API_KEY to enable OpenAI-backed semantic recall.') + '</div></div>';
    html += '</div>';
    html += '<p class="text-sm text-secondary mt-16">Saving your OpenAI key here does not replace Hermes local memory. It only adds stronger OpenAI-backed semantic recall. You do not need <span class="font-mono">OPENAI_BASE_URL</span> for normal OpenAI use.</p>';
    html += '<div class="starter-pack-item-actions">';
    html += '<button class="' + actionClass + '" onclick="openEnvVarSetup(\'OPENAI_API_KEY\', \'Provider\')">' + escH(actionLabel) + '</button>';
    html += '<button class="btn" onclick="navigate(\'env-vars\')">Open Env Vars</button>';
    html += '</div></div></div>';
    return html;
}

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
    try { await api('POST', '/api/env', { key, value }); toast('Variable added', 'success'); closeModal(); refreshCurrentScreen(); }
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
    try { await api('PUT', '/api/env/' + key, { value }); toast('Variable updated', 'success'); closeModal(); refreshCurrentScreen(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.deleteEnvVar = function (key) {
    showModal('Delete Variable', '<p>Are you sure you want to delete <strong>' + escH(key) + '</strong>?</p>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-danger" onclick="confirmDeleteEnvVar(\'' + escA(key) + '\')">Delete</button>'
    );
};
window.confirmDeleteEnvVar = async function (key) {
    try { await api('DELETE', '/api/env/' + key); toast('Variable deleted', 'success'); closeModal(); refreshCurrentScreen(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};

// ── SERVICE ────────────────────────────────────────────────
Screens.service = async function () {
    const content = document.getElementById('content');
    try {
        const [health, sys, updateState] = await Promise.all([
            api('GET', '/api/health').catch(() => ({ gateway_running: false, gateway_pid: null, version: '?' })),
            api('GET', '/api/system').catch(() => ({ python_version: '?', os_info: '?', disk_free: '?', uptime: '?' })),
            HermesUpdate.ensureLoaded().catch(() => null),
        ]);
        content.innerHTML = `
        ${renderHermesUpdateCard(updateState)}
        <div class="card">
            <div class="card-header"><span>Gateway Service</span><span class="badge ${health.gateway_running ? 'badge-success' : 'badge-danger'}">${health.gateway_running ? 'Running' : 'Stopped'}</span></div>
            <div class="card-body">
                <div class="form-row" style="margin-bottom:16px">
                    <div class="form-group"><label class="form-label">Active Profile</label><div>${activeProfileBadgeH(health.profile)}</div></div>
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
                    <div class="form-group"><label class="form-label">Active Profile</label><div>${activeProfileBadgeH(health.profile)}</div></div>
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

function modelRoleCardH(role, info, status = null) {
    const meta = MODEL_ROLE_META[role] || { title: role, description: '' };
    const enabled = role === 'primary' ? true : !!info?.enabled;
    let statusBadge = role === 'primary'
        ? '<span class="badge badge-success">Required</span>'
        : '<span class="badge ' + (enabled ? 'badge-info' : 'badge-secondary') + '">' + (enabled ? 'Enabled' : 'Disabled') + '</span>';
    if (status && status.label) {
        statusBadge = '<span class="badge ' + escH(status.tone || 'badge-secondary') + '">' + escH(status.label) + '</span>';
    }
    const profileLabel = info?.profile || '(not linked)';
    const providerType = String(info?.provider || '').trim().toLowerCase();
    const providerLabel = info?.provider_label || info?.provider || '(not set)';
    const modelLabel = info?.model || '(not set)';
    const routeTitle = providerType === 'openrouter' ? 'OpenRouter Endpoint' : 'Routing';
    const routeLabel = providerType === 'openrouter'
        ? (info?.routing_provider || 'Auto')
        : 'Direct';
    return '' +
        '<div class="card model-role-card">' +
            '<div class="card-header"><span>' + escH(meta.title) + '</span>' + statusBadge + '</div>' +
            '<div class="card-body">' +
                '<p class="text-sm text-secondary mb-16">' + escH(meta.description) + '</p>' +
                '<div class="model-role-meta">' +
                    '<div class="model-role-meta-item"><label class="form-label">Provider Profile</label><div class="font-mono text-sm">' + escH(profileLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">Provider Type</label><div class="text-sm">' + escH(providerLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">Model</label><div class="font-mono text-sm">' + escH(modelLabel) + '</div></div>' +
                    '<div class="model-role-meta-item"><label class="form-label">' + escH(routeTitle) + '</label><div class="font-mono text-sm">' + escH(routeLabel) + '</div></div>' +
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
        const [data, chatStatus, roleData, health] = await Promise.all([
            api('GET', '/api/providers'),
            api('GET', '/api/chat/status').catch(() => null),
            loadModelRoles(true),
            api('GET', '/api/health').catch(() => ({ gateway_running: false, profile: 'unknown', hermes_home: '?' })),
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

        let html = runtimeProfileContextCardH(
            health,
            primaryRole.base_url || chatStatus?.api_url || def.base_url || '',
            'Providers Runtime Context',
            'Provider Profiles and Model Roles are profile-sensitive. The active portal profile determines which Hermes home, env, gateway, and API server runtime you are inspecting here.'
        );
        html += '<div class="card mb-16"><div class="card-body">';
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
        const [data, chatStatus, health] = await Promise.all([
            loadModelRoles(true),
            api('GET', '/api/chat/status').catch(() => null),
            api('GET', '/api/health').catch(() => ({ gateway_running: false, profile: 'unknown', hermes_home: '?' })),
        ]);
        const roles = data.roles || {};
        const profiles = data.profiles || [];
        const primaryRole = roles.primary || {};
        const readiness = chatStatus?.readiness || {};
        const screenshotReady = !!readiness.screenshots_ready;
        let html = runtimeProfileContextCardH(
            health,
            chatStatus?.api_url || primaryRole.base_url || '',
            'Models Runtime Context',
            'Model Roles resolve within the active portal profile. This profile determines the Hermes home, gateway runtime, and API server context behind the roles shown below.'
        );
        html += '<div class="card mb-16"><div class="card-body">';
        html += '<p class="text-sm text-secondary">Model Roles decide which saved provider profile and model Hermes uses for primary chat, fallback chat, and vision. Provider Profiles live in the Providers screen; this screen is where you assign them.</p>';
        html += '</div></div>';
        html += '<div class="model-role-grid">';
        ['primary', 'fallback', 'vision'].forEach(role => {
            const status = role === 'vision'
                ? { label: screenshotReady ? 'Ready' : 'Not Ready', tone: screenshotReady ? 'badge-success' : 'badge-danger' }
                : null;
            html += modelRoleCardH(role, roles[role] || {}, status);
        });
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
        if (!screenIsActive('agents')) return;
        const personalities = data.personalities || {};
        const defaults = data.defaults || {};
        const entries = Array.isArray(data.entries)
            ? data.entries
            : Object.entries(personalities).map(([name, value]) => ({
                name,
                kind: 'personality',
                description: '',
                system_prompt: typeof value === 'string' ? value : JSON.stringify(value),
                value,
            }));
        const focusPresetName = agentCatalogState.focusNameOnRender || '';
        const orderedEntries = entries.slice().sort((a, b) => {
            if (focusPresetName && a.name === focusPresetName && b.name !== focusPresetName) return -1;
            if (focusPresetName && b.name === focusPresetName && a.name !== focusPresetName) return 1;
            return 0;
        });

        let html = '<div class="card mb-16"><div class="card-header"><span>Default Agent Settings</span></div><div class="card-body"><div class="form-row">';
        html += '<div class="form-group"><label class="form-label">Max Turns</label><div>' + (defaults.max_turns || '?') + '</div></div>';
        html += '<div class="form-group"><label class="form-label">Reasoning Effort</label><div>' + (defaults.reasoning_effort || '?') + '</div></div>';
        html += '</div></div></div>';

        html += '<div class="section-header"><span>Agents / Personalities</span><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" onclick="openCreateCapability(\'agent_preset\')">Create Agent Preset</button><button class="btn btn-primary" onclick="addAgent()">+ Add Agent</button></div></div>';
        if (orderedEntries.length === 0) {
            html += '<div class="empty-state"><p>No agents configured</p></div>';
        } else {
            html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px">';
            orderedEntries.forEach(entry => {
                const preview = (entry.description || entry.system_prompt || '').trim();
                const recent = !!agentCatalogState.recentNames[entry.name || ''];
                html += '<div class="card" id="' + escA(agentElementId(entry.name || '')) + '"><div class="card-header"><span>' + escH(entry.name || '') + '</span><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="badge ' + (entry.kind === 'agent_preset' ? 'badge-info' : 'badge-secondary') + '">' + escH(entry.kind === 'agent_preset' ? 'Preset' : 'Personality') + '</span>' + (recent ? '<span class="badge badge-success">New</span>' : '') + '</div></div><div class="card-body">';
                html += '<p class="text-sm text-muted" style="max-height:80px;overflow:hidden">' + escH(preview.substring(0, 150) + (preview.length > 150 ? '...' : '')) + '</p>';
                html += '<div class="mt-16" style="display:flex;gap:8px"><button class="btn btn-sm" onclick="editAgent(\'' + escA(entry.name || '') + '\')">Edit</button><button class="btn btn-sm" onclick="duplicateAgent(\'' + escA(entry.name || '') + '\')">Duplicate</button><button class="btn btn-sm btn-danger" onclick="deleteAgent(\'' + escA(entry.name || '') + '\')">Delete</button></div></div></div>';
            });
            html += '</div>';
        }
        content.innerHTML = html;
        if (focusPresetName) {
            agentCatalogState.focusNameOnRender = '';
            requestAnimationFrame(() => focusInventoryCardById(agentElementId(focusPresetName)));
        }
    } catch (e) {
        if (!screenIsActive('agents')) return;
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.agentModal = function (title, name, prompt, nameReadonly, saveFn) {
    showModal(title,
        '<div class="form-group"><label class="form-label">Name</label>' + inputH('agent-name', name, 'text', '', nameReadonly ? 'disabled' : '') + '</div>' +
        '<div class="form-group"><label class="form-label">System Prompt / Instructions</label>' + textareaH('agent-prompt', prompt, 8) + '</div>',
        '<button class="btn" onclick="closeModal()">Cancel</button><button class="btn btn-primary" onclick="' + saveFn + '">Save</button>'
    );
};
window.addAgent = function () { window.agentModal('Add Agent', '', '', false, 'saveNewAgent()'); };
window.editAgent = async function (name) {
    try {
        const data = await api('GET', '/api/agents');
        const entries = Array.isArray(data.entries) ? data.entries : [];
        const entry = entries.find(item => item.name === name) || null;
        const prompt = entry ? (entry.system_prompt || '') : '';
        window.agentModal('Edit Agent: ' + name, name, prompt, true, 'saveEditAgent()');
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveNewAgent = async function () {
    const name = document.getElementById('agent-name').value.trim();
    const prompt = document.getElementById('agent-prompt').value;
    if (!name) { toast('Name required', 'error'); return; }
    try { await api('POST', '/api/agents', { name, system_prompt: prompt }); toast('Agent created', 'success'); closeModal(); Screens.agents(); }
    catch (e) { toast('Error: ' + e.message, 'error'); }
};
window.saveEditAgent = async function () {
    const name = document.getElementById('agent-name').value.trim();
    const prompt = document.getElementById('agent-prompt').value;
    try { await api('PUT', '/api/agents/' + name, { system_prompt: prompt }); toast('Agent updated', 'success'); closeModal(); Screens.agents(); }
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

const capabilityBuilderState = {
    catalog: null,
    selectedType: 'skill',
    drafts: {},
    previews: {},
    lastCreated: null,
    focusBuilderOnRender: false,
    focusPreviewOnRender: false,
};

function capabilityContext() {
    return (capabilityBuilderState.catalog || {}).context || {};
}

function capabilityClone(value) {
    if (value === null || value === undefined) return value;
    return JSON.parse(JSON.stringify(value));
}

function defaultSkillCapabilityDraft() {
    return {
        name: '',
        slug: '',
        category: '',
        description: '',
        instructions: '',
        env_vars: [],
        credential_files: [],
        required_commands: [],
        include_scripts: false,
        include_references: false,
    };
}

function capabilityIntegrationOptions() {
    return Array.isArray(capabilityContext().integration_options) ? capabilityContext().integration_options : [];
}

function capabilityProfileList() {
    return Array.isArray(capabilityContext().provider_profiles) ? capabilityContext().provider_profiles : [];
}

function capabilitySkillList() {
    return Array.isArray(capabilityContext().skills) ? capabilityContext().skills : [];
}

function capabilityDefaultIntegrationOption(kind = '') {
    const options = capabilityIntegrationOptions();
    return options.find(item => item && item.name === kind) || options[0] || {
        name: kind || 'discord',
        label: kind || 'discord',
        config_template: {},
        suggested_env_vars: [],
    };
}

function defaultIntegrationCapabilityDraft(kind = '') {
    const option = capabilityDefaultIntegrationOption(kind);
    const template = capabilityClone(option.config_template || option.config || {}) || {};
    return {
        kind: option.name || kind || 'discord',
        config: JSON.stringify(template, null, 2),
        env_vars: (option.suggested_env_vars || []).map(item => ({
            key: item.key || '',
            label: item.label || '',
            group: item.group || 'Channel',
            description: item.description || '',
            value: '',
        })),
    };
}

function defaultAgentPresetDraft() {
    const context = capabilityContext();
    const roles = context.model_roles || {};
    const defaults = context.agent_defaults || {};
    return {
        name: '',
        description: '',
        system_prompt: '',
        roles: {
            primary: {
                enabled: true,
                profile: ((roles.primary || {}).profile) || '',
                model: ((roles.primary || {}).model) || '',
                routing_provider: ((roles.primary || {}).routing_provider) || '',
            },
            fallback: {
                enabled: !!((roles.fallback || {}).enabled),
                profile: ((roles.fallback || {}).profile) || '',
                model: ((roles.fallback || {}).model) || '',
                routing_provider: ((roles.fallback || {}).routing_provider) || '',
            },
            vision: {
                enabled: !!((roles.vision || {}).enabled),
                profile: ((roles.vision || {}).profile) || '',
                model: ((roles.vision || {}).model) || '',
                routing_provider: ((roles.vision || {}).routing_provider) || '',
            },
        },
        skills: [],
        integrations: [],
        reasoning_effort: (defaults.reasoning_effort || ''),
        max_turns: defaults.max_turns || '',
    };
}

function capabilitySlugify(value) {
    return String(value || '')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '');
}

function capabilityCatalogType(type) {
    return ((capabilityBuilderState.catalog || {}).types || []).find(item => item && item.id === type) || {
        id: type,
        label: type,
        status: 'planned',
        phase: '',
        summary: '',
        data_model: [],
        ui_flow: [],
        layout: [],
        mvp_scope: [],
    };
}

function capabilityDraft(type = capabilityBuilderState.selectedType) {
    if (!capabilityBuilderState.drafts[type]) {
        if (type === 'skill') capabilityBuilderState.drafts[type] = defaultSkillCapabilityDraft();
        else if (type === 'integration') capabilityBuilderState.drafts[type] = defaultIntegrationCapabilityDraft();
        else if (type === 'agent_preset') capabilityBuilderState.drafts[type] = defaultAgentPresetDraft();
        else capabilityBuilderState.drafts[type] = {};
    }
    return capabilityBuilderState.drafts[type];
}

function capabilityPreview(type = capabilityBuilderState.selectedType) {
    return capabilityBuilderState.previews[type] || null;
}

function clearCapabilityPreview(type = capabilityBuilderState.selectedType) {
    delete capabilityBuilderState.previews[type];
    if (capabilityBuilderState.lastCreated && capabilityBuilderState.lastCreated.type === type) {
        capabilityBuilderState.lastCreated = null;
    }
}

function capabilityStatusBadgeClass(status) {
    if (status === 'active') return 'badge-success';
    if (status === 'planned') return 'badge-info';
    return 'badge-secondary';
}

function capabilityStatusLabel(statusLabel, status) {
    return statusLabel || (status === 'active' ? 'Active' : (status === 'planned' ? 'Planned Next' : 'Preview'));
}

function capabilitySelectButtonClass(type) {
    return capabilityBuilderState.selectedType === type ? 'btn btn-primary' : 'btn';
}

function captureCapabilityEditorState() {
    const content = document.getElementById('content');
    const active = document.activeElement;
    if (!active || !active.id) {
        return {
            scrollX: window.scrollX || 0,
            scrollY: window.scrollY || 0,
            contentScrollTop: content ? content.scrollTop : 0,
            contentScrollLeft: content ? content.scrollLeft : 0,
        };
    }
    const canRestoreSelection = typeof active.selectionStart === 'number' && typeof active.selectionEnd === 'number';
    return {
        id: active.id,
        selectionStart: canRestoreSelection ? active.selectionStart : null,
        selectionEnd: canRestoreSelection ? active.selectionEnd : null,
        scrollX: window.scrollX || 0,
        scrollY: window.scrollY || 0,
        contentScrollTop: content ? content.scrollTop : 0,
        contentScrollLeft: content ? content.scrollLeft : 0,
    };
}

function restoreCapabilityEditorState(state) {
    if (!state) return;
    const scrollX = Number.isFinite(state.scrollX) ? state.scrollX : 0;
    const scrollY = Number.isFinite(state.scrollY) ? state.scrollY : 0;
    const contentScrollTop = Number.isFinite(state.contentScrollTop) ? state.contentScrollTop : 0;
    const contentScrollLeft = Number.isFinite(state.contentScrollLeft) ? state.contentScrollLeft : 0;
    const restore = () => {
        const content = document.getElementById('content');
        if (content) {
            content.scrollTop = contentScrollTop;
            content.scrollLeft = contentScrollLeft;
        }
        const el = state.id ? document.getElementById(state.id) : null;
        if (el && typeof el.focus === 'function') {
            try { el.focus({ preventScroll: true }); }
            catch (e) { el.focus(); }
            if (typeof state.selectionStart === 'number' && typeof state.selectionEnd === 'number' && typeof el.setSelectionRange === 'function') {
                try { el.setSelectionRange(state.selectionStart, state.selectionEnd); } catch (e) { /* ignore */ }
            }
        }
        if (content) {
            content.scrollTop = contentScrollTop;
            content.scrollLeft = contentScrollLeft;
        }
        window.scrollTo(scrollX, scrollY);
    };
    requestAnimationFrame(restore);
}

function focusCapabilityBuilder() {
    const target = document.getElementById('capability-builder-anchor');
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function focusCapabilityPreview() {
    const target = document.getElementById('capability-preview-actions') || document.getElementById('capability-preview-anchor');
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const approveButton = document.getElementById('capability-approve-button');
    if (approveButton && typeof approveButton.focus === 'function') {
        try { approveButton.focus({ preventScroll: true }); }
        catch (e) { approveButton.focus(); }
    }
}

function resetCapabilityDraftState(type = capabilityBuilderState.selectedType) {
    if (type === 'skill') capabilityBuilderState.drafts.skill = defaultSkillCapabilityDraft();
    else if (type === 'integration') capabilityBuilderState.drafts.integration = defaultIntegrationCapabilityDraft();
    else if (type === 'agent_preset') capabilityBuilderState.drafts.agent_preset = defaultAgentPresetDraft();
    clearCapabilityPreview(type);
}

function renderCapabilityBulletCard(title, items) {
    const list = Array.isArray(items) ? items.filter(Boolean) : [];
    if (!list.length) return '';
    return '<div class="card"><div class="card-header"><span>' + escH(title) + '</span></div><div class="card-body"><ul class="plain-list">' +
        list.map(item => '<li>' + escH(item) + '</li>').join('') +
        '</ul></div></div>';
}

function renderCapabilityLayoutCard(items) {
    const list = Array.isArray(items) ? items : [];
    if (!list.length) return '';
    return '<div class="card"><div class="card-header"><span>File / Config Layout</span></div><div class="card-body"><div class="capability-layout-list">' +
        list.map(item =>
            '<div class="capability-layout-item">' +
                '<div class="capability-layout-kind">' + escH((item.kind || 'item').toUpperCase()) + '</div>' +
                '<div class="font-mono text-sm">' + escH(item.path || '') + '</div>' +
                '<div class="capability-layout-detail">' + escH(item.purpose || '') + '</div>' +
            '</div>'
        ).join('') +
        '</div></div></div>';
}

function renderCapabilityOverview(catalog) {
    let html = '<div class="capability-overview-grid">';
    html += renderCapabilityBulletCard('Architecture Rules', catalog.architecture_rules || []);
    html += renderCapabilityBulletCard('MVP Scope', catalog.mvp_scope || []);
    html += renderCapabilityBulletCard('Implementation Order', catalog.recommended_order || []);
    html += '</div>';
    return html;
}

function renderCapabilityTypeCards() {
    const types = ((capabilityBuilderState.catalog || {}).types || []);
    return '<div class="capability-type-grid">' + types.map(type =>
        '<button class="capability-type-card' + (capabilityBuilderState.selectedType === type.id ? ' active' : '') + '" onclick="selectCapabilityType(\'' + escA(type.id) + '\')">' +
            '<div class="capability-type-card-top"><div class="capability-type-title">' + escH(type.label || type.id) + '</div>' +
            '<span class="badge ' + capabilityStatusBadgeClass(type.status) + '">' + escH(capabilityStatusLabel(type.status_label, type.status)) + '</span></div>' +
            '<div class="capability-type-phase">' + escH(type.phase || '') + '</div>' +
            '<div class="capability-type-summary">' + escH(type.summary || '') + '</div>' +
        '</button>'
    ).join('') + '</div>';
}

function renderCapabilityCreatedCard(type) {
    const created = capabilityBuilderState.lastCreated;
    if (!created || created.type !== type) return '';
    const title = type === 'integration' ? 'Integration Created' : type === 'agent_preset' ? 'Agent Preset Created' : 'Skill Created';
    const target = created.kind || created.slug || created.name || '';
    const openAction = type === 'skill'
        ? 'openCreatedSkillInventory(\'' + escA(created.slug || created.path || '') + '\')'
        : (type === 'integration'
            ? 'openCreatedIntegrationInventory(\'' + escA(created.kind || created.name || '') + '\')'
            : 'openCreatedAgentInventory(\'' + escA(created.name || '') + '\')');
    const openLabel = type === 'integration' ? 'Apps & Integrations' : type === 'agent_preset' ? 'Agents' : 'Skills';
    return '<div class="card mb-16 capability-created-card"><div class="card-header"><span>' + escH(title) + '</span><span class="badge badge-success">Saved</span></div><div class="card-body">' +
        '<p class="text-sm text-secondary mb-12"><span class="font-mono">' + escH(target) + '</span> was written to <span class="font-mono">' + escH(created.target_dir || '') + '</span>.</p>' +
        '<div class="capability-builder-actions"><button class="btn btn-primary" onclick="' + openAction + '">Open ' + escH(openLabel) + '</button><button class="btn" onclick="clearCreatedCapabilityNotice()">Create Another</button></div>' +
        '</div></div>';
}

function capabilityEnvVarRowH(entry, index) {
    const groupOptions = Object.keys(envVarsState.groupHelp || { Provider: true, Channel: true, System: true });
    return '<div class="capability-list-row">' +
        '<div class="form-group"><label class="form-label">Key</label>' + inputH('capability-env-key-' + index, entry.key || '', 'text', 'OPENAI_API_KEY', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'key\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Label</label>' + inputH('capability-env-label-' + index, entry.label || '', 'text', 'OpenAI API Key', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'label\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Group</label>' + selectH('capability-env-group-' + index, groupOptions, entry.group || 'Provider', 'capability-env-group-' + index).replace('<select', '<select onchange="updateCapabilityListItem(\'env_vars\', ' + index + ', \'group\', this.value)"') + '</div>' +
        '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + inputH('capability-env-description-' + index, entry.description || '', 'text', 'What this variable is used for', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'description\', this.value)"') + '</div>' +
        '<div class="form-group capability-inline-action"><label class="form-label">Remove</label><button class="btn btn-sm btn-danger" onclick="removeCapabilityListItem(\'env_vars\', ' + index + ')">Remove</button></div>' +
    '</div>';
}

function capabilityCredentialRowH(entry, index) {
    return '<div class="capability-list-row">' +
        '<div class="form-group"><label class="form-label">Relative Path</label>' + inputH('capability-file-path-' + index, entry.path || '', 'text', 'credentials/client.json', 'oninput="updateCapabilityListItem(\'credential_files\', ' + index + ', \'path\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Label</label>' + inputH('capability-file-label-' + index, entry.label || '', 'text', 'Google OAuth Client', 'oninput="updateCapabilityListItem(\'credential_files\', ' + index + ', \'label\', this.value)"') + '</div>' +
        '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + inputH('capability-file-description-' + index, entry.description || '', 'text', 'Expected credential file', 'oninput="updateCapabilityListItem(\'credential_files\', ' + index + ', \'description\', this.value)"') + '</div>' +
        '<div class="form-group capability-inline-action"><label class="form-label">Remove</label><button class="btn btn-sm btn-danger" onclick="removeCapabilityListItem(\'credential_files\', ' + index + ')">Remove</button></div>' +
    '</div>';
}

function capabilityCommandRowH(entry, index) {
    return '<div class="capability-list-row">' +
        '<div class="form-group"><label class="form-label">Command</label>' + inputH('capability-command-name-' + index, entry.name || '', 'text', 'uv', 'oninput="updateCapabilityListItem(\'required_commands\', ' + index + ', \'name\', this.value)"') + '</div>' +
        '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + inputH('capability-command-description-' + index, entry.description || '', 'text', 'Why Hermes needs this command', 'oninput="updateCapabilityListItem(\'required_commands\', ' + index + ', \'description\', this.value)"') + '</div>' +
        '<div class="form-group capability-inline-action"><label class="form-label">Remove</label><button class="btn btn-sm btn-danger" onclick="removeCapabilityListItem(\'required_commands\', ' + index + ')">Remove</button></div>' +
    '</div>';
}

function capabilityIntegrationEnvVarRowH(entry, index) {
    const groupOptions = Object.keys(envVarsState.groupHelp || { Provider: true, Channel: true, System: true });
    return '<div class="capability-list-row">' +
        '<div class="form-group"><label class="form-label">Key</label>' + inputH('capability-int-env-key-' + index, entry.key || '', 'text', 'DISCORD_TOKEN', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'key\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Label</label>' + inputH('capability-int-env-label-' + index, entry.label || '', 'text', 'Discord Token', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'label\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Group</label>' + selectH('capability-int-env-group-' + index, groupOptions, entry.group || 'Channel', 'capability-int-env-group-' + index).replace('<select', '<select onchange="updateCapabilityListItem(\'env_vars\', ' + index + ', \'group\', this.value)"') + '</div>' +
        '<div class="form-group"><label class="form-label">Value</label>' + inputH('capability-int-env-value-' + index, entry.value || '', 'text', 'Paste the token or secret', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'value\', this.value)"') + '</div>' +
        '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + inputH('capability-int-env-description-' + index, entry.description || '', 'text', 'What this variable is used for', 'oninput="updateCapabilityListItem(\'env_vars\', ' + index + ', \'description\', this.value)"') + '</div>' +
        '<div class="form-group capability-inline-action"><label class="form-label">Remove</label><button class="btn btn-sm btn-danger" onclick="removeCapabilityListItem(\'env_vars\', ' + index + ')">Remove</button></div>' +
    '</div>';
}

function renderCapabilityListSection(title, key, rows, emptyText, addLabel) {
    const list = rows || [];
    let html = '<div class="card"><div class="card-header"><span>' + escH(title) + '</span><button class="btn btn-sm" onclick="addCapabilityListItem(\'' + escA(key) + '\')">' + escH(addLabel) + '</button></div><div class="card-body">';
    if (!list.length) {
        html += '<div class="empty-state capability-empty-inline"><p>' + escH(emptyText) + '</p></div>';
    } else {
        html += list.join('');
    }
    html += '</div></div>';
    return html;
}

function renderCapabilitySelectionCard(title, key, items, selectedValues, emptyText) {
    const list = Array.isArray(items) ? items : [];
    const selected = new Set(Array.isArray(selectedValues) ? selectedValues : []);
    let html = '<div class="card"><div class="card-header"><span>' + escH(title) + '</span></div><div class="card-body">';
    if (!list.length) {
        html += '<div class="empty-state capability-empty-inline"><p>' + escH(emptyText) + '</p></div>';
    } else {
        html += '<div class="capability-layout-list">';
        list.forEach(item => {
            const value = item.path || item.name || '';
            const label = item.name || item.label || value;
            const detailParts = [];
            if (item.description) detailParts.push(item.description);
            if (item.provider_label) detailParts.push(item.provider_label);
            if (item.configured === false) detailParts.push('Not configured yet');
            if (item.enabled === false) detailParts.push('Disabled');
            if (item.ready === false) detailParts.push('Needs setup');
            html += '<label class="capability-layout-item" style="display:block;cursor:pointer">' +
                '<div style="display:flex;gap:12px;align-items:flex-start">' +
                    '<input type="checkbox" ' + (selected.has(value) ? 'checked ' : '') + 'onchange="toggleCapabilitySelection(\'' + escA(key) + '\', \'' + escA(value) + '\', this.checked)">' +
                    '<div style="flex:1 1 auto">' +
                        '<div class="capability-layout-kind">' + escH(label) + '</div>' +
                        '<div class="font-mono text-sm">' + escH(value) + '</div>' +
                        (detailParts.length ? '<div class="capability-layout-detail">' + escH(detailParts.join(' • ')) + '</div>' : '') +
                    '</div>' +
                '</div>' +
            '</label>';
        });
        html += '</div>';
    }
    html += '</div></div>';
    return html;
}

function renderAgentRoleCard(role, entry, profiles) {
    const meta = (MODEL_ROLE_META && MODEL_ROLE_META[role]) || { title: role, description: '' };
    const options = [{ value: '', label: 'Choose profile' }].concat((profiles || []).map(profile => ({
        value: profile.name || '',
        label: (profile.name || '') + ((profile.provider_label || profile.provider) ? ' (' + (profile.provider_label || profile.provider) + ')' : ''),
    })));
    let html = '<div class="card mb-16"><div class="card-header"><span>' + escH(meta.title || role) + '</span>';
    if (role !== 'primary') {
        html += '<label class="skill-select-toggle"><input type="checkbox" ' + (entry.enabled ? 'checked ' : '') + 'onchange="updateCapabilityRoleField(\'' + escA(role) + '\', \'enabled\', this.checked)"><span>Enabled</span></label>';
    } else {
        html += '<span class="badge badge-success">Required</span>';
    }
    html += '</div><div class="card-body">';
    if (meta.description) {
        html += '<p class="text-sm text-secondary mb-12">' + escH(meta.description) + '</p>';
    }
    html += '<div class="capability-form-grid">';
    html += '<div class="form-group"><label class="form-label">Provider Profile</label>' + selectH('capability-role-profile-' + role, options, entry.profile || '', 'capability-role-profile-' + role).replace('<select', '<select onchange="updateCapabilityRoleField(\'' + escA(role) + '\', \'profile\', this.value)"') + '</div>';
    html += '<div class="form-group"><label class="form-label">Model</label>' + inputH('capability-role-model-' + role, entry.model || '', 'text', 'openai/gpt-5.4-mini', 'oninput="updateCapabilityRoleField(\'' + escA(role) + '\', \'model\', this.value)"') + '</div>';
    html += '<div class="form-group"><label class="form-label">Routing Provider</label>' + inputH('capability-role-routing-' + role, entry.routing_provider || '', 'text', 'Optional OpenRouter endpoint provider', 'oninput="updateCapabilityRoleField(\'' + escA(role) + '\', \'routing_provider\', this.value)"') + '</div>';
    html += '</div></div></div>';
    return html;
}

function renderSkillCapabilityBuilder(typeMeta) {
    const draft = capabilityDraft('skill');
    const preview = capabilityPreview('skill');
    const skillPreview = ((preview || {}).manifest || {}).skill || {};
    const setup = skillPreview.setup || {};
    const blockers = Array.isArray(setup.blockers) ? setup.blockers : [];
    const writes = Array.isArray((preview || {}).writes) ? preview.writes : [];
    const markdownWrite = writes.find(item => item && item.kind === 'file' && item.content);
    const suggestedSlug = capabilitySlugify(draft.name || '');

    let html = '<div class="card mb-16"><div class="card-header"><span>' + escH(typeMeta.label || 'Create Skill') + '</span><span class="badge badge-success">' + escH(typeMeta.phase || 'Phase 1') + '</span></div><div class="card-body">';
    html += '<p class="text-sm text-secondary mb-16">Phase 1 is live for skills. The draft stays local until you preview it, and nothing is written until you approve the exact generated output.</p>';
    html += '</div></div>';
    html += '<div class="capability-builder-grid">';
    html += '<div class="capability-builder-column">';
    html += '<div class="card mb-16"><div class="card-header"><span>Skill Draft</span></div><div class="card-body">';
    html += '<div class="capability-form-grid">';
    html += '<div class="form-group"><label class="form-label">Name</label>' + inputH('capability-skill-name', draft.name || '', 'text', 'Google Workspace Helper', 'oninput="updateCapabilityDraftField(\'name\', this.value)"') + '</div>';
    html += '<div class="form-group"><label class="form-label">Slug</label>' + inputH('capability-skill-slug', draft.slug || '', 'text', suggestedSlug || 'google-workspace-helper', 'oninput="updateCapabilityDraftField(\'slug\', this.value)"') + '<div class="capability-field-note">Stored under <span class="font-mono">~/.hermes/skills/' + escH(draft.slug || suggestedSlug || '<slug>') + '/</span></div></div>';
    html += '<div class="form-group"><label class="form-label">Category</label>' + inputH('capability-skill-category', draft.category || '', 'text', 'productivity', 'oninput="updateCapabilityDraftField(\'category\', this.value)"') + '</div>';
    html += '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + textareaH('capability-skill-description', draft.description || '', 3, false).replace('<textarea', '<textarea oninput="updateCapabilityDraftField(\'description\', this.value)"') + '</div>';
    html += '<div class="form-group capability-list-wide"><label class="form-label">Instructions</label>' + textareaH('capability-skill-instructions', draft.instructions || '', 10, false).replace('<textarea', '<textarea oninput="updateCapabilityDraftField(\'instructions\', this.value)"') + '<div class="capability-field-note">This becomes the main body of <span class="font-mono">SKILL.md</span>.</div></div>';
    html += '</div>';
    html += '<div class="capability-toggle-row">';
    html += '<label class="skill-select-toggle"><input type="checkbox" ' + (draft.include_scripts ? 'checked ' : '') + 'onchange="updateCapabilityDraftField(\'include_scripts\', this.checked)"><span>Include <span class="font-mono">scripts/</span></span></label>';
    html += '<label class="skill-select-toggle"><input type="checkbox" ' + (draft.include_references ? 'checked ' : '') + 'onchange="updateCapabilityDraftField(\'include_references\', this.checked)"><span>Include <span class="font-mono">references/</span></span></label>';
    html += '</div>';
    html += '<div class="capability-builder-actions"><button class="btn btn-primary" onclick="previewCapabilityDraft()">Preview Draft</button><button class="btn" onclick="resetCapabilitySkillDraft()">Reset</button></div>';
    html += '</div></div>';
    html += renderCapabilityListSection(
        'Environment Variables',
        'env_vars',
        (draft.env_vars || []).map((entry, index) => capabilityEnvVarRowH(entry, index)),
        'Add the env vars this skill expects Hermes users to set later.',
        '+ Add Env Var'
    );
    html += renderCapabilityListSection(
        'Credential Files',
        'credential_files',
        (draft.credential_files || []).map((entry, index) => capabilityCredentialRowH(entry, index)),
        'Declare credential files the user should place inside this skill folder.',
        '+ Add Credential File'
    );
    html += renderCapabilityListSection(
        'Required Commands',
        'required_commands',
        (draft.required_commands || []).map((entry, index) => capabilityCommandRowH(entry, index)),
        'List commands Hermes should check before this skill is considered ready.',
        '+ Add Command'
    );
    html += '</div>';

    html += '<div class="capability-builder-column">';
    html += renderCapabilityCreatedCard('skill');
    html += '<div id="capability-preview-anchor"></div><div class="card mb-16"><div class="card-header"><span>Preview & Approval</span></div><div class="card-body">';
    if (!preview) {
        html += '<div class="empty-state"><p>Preview the draft to inspect generated files, setup blockers, and the exact <span class="font-mono">SKILL.md</span> content before approval.</p></div>';
    } else {
        const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];
        html += '<div class="capability-preview-summary">';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Target</div><div class="runtime-readiness-value">' + escH((preview.summary || {}).slug || '') + '</div><div class="runtime-readiness-detail">' + escH((preview.summary || {}).target_dir || '') + '</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Setup</div><div class="runtime-readiness-value">' + escH(String((preview.summary || {}).env_var_count || 0)) + ' env vars</div><div class="runtime-readiness-detail">' + escH(String((preview.summary || {}).credential_file_count || 0)) + ' credential files, ' + escH(String((preview.summary || {}).required_command_count || 0)) + ' commands</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Approval</div><div class="runtime-readiness-value">' + escH(preview.can_apply ? 'Ready' : 'Blocked') + '</div><div class="runtime-readiness-detail">' + escH(preview.can_apply ? 'Approve to write files.' : 'Resolve the slug conflict before approval.') + '</div></div>';
        html += '</div>';
        if (warnings.length) {
            html += '<div class="card mt-16"><div class="card-header"><span>Warnings</span></div><div class="card-body"><ul class="plain-list">' + warnings.map(warning => '<li>' + escH(warning) + '</li>').join('') + '</ul></div></div>';
        }
        html += '<div id="capability-preview-actions" class="capability-builder-actions mt-16"><button id="capability-approve-button" class="btn btn-primary" ' + (preview.can_apply ? '' : 'disabled ') + 'onclick="applyCapabilityDraft()">Approve & Create Skill</button><button class="btn" onclick="previewCapabilityDraft()">Refresh Preview</button></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Will Write</span></div><div class="card-body"><div class="capability-layout-list">' +
            writes.map(item =>
                '<div class="capability-layout-item"><div class="capability-layout-kind">' + escH((item.kind || 'item').toUpperCase()) + '</div><div class="font-mono text-sm">' + escH(item.path || '') + '</div><div class="capability-layout-detail">' + escH(item.label || item.action || '') + '</div></div>'
            ).join('') +
            '</div></div></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Readiness After Create</span></div><div class="card-body">';
        if (!blockers.length) {
            html += '<p class="text-sm text-secondary">This draft will be immediately ready based on the metadata it declares.</p>';
        } else {
            html += '<div class="skill-setup-blocker-list">' + blockers.map(blocker =>
                '<div class="skill-setup-blocker"><div class="skill-setup-blocker-title">' + escH(blocker.message || 'Setup needed') + '</div>' +
                (blocker.kind === 'env_var'
                    ? '<div class="skill-setup-blocker-detail">Group: ' + escH(blocker.group || 'System') + '</div>'
                    : blocker.kind === 'credential_file'
                        ? '<div class="skill-setup-blocker-detail">Expected at <span class="font-mono">' + escH(blocker.absolute_path || blocker.path || '') + '</span></div>'
                        : '<div class="skill-setup-blocker-detail">Install or expose <span class="font-mono">' + escH(blocker.name || '') + '</span> in your PATH before using this skill.</div>') +
                '</div>'
            ).join('') + '</div>';
        }
        html += '</div></div>';
        if (markdownWrite) {
            html += '<div class="card mt-16"><div class="card-header"><span>Generated SKILL.md</span></div><div class="card-body"><pre class="font-mono text-xs capability-code-preview">' + escH(markdownWrite.content || '') + '</pre></div></div>';
        }
    }
    html += '</div></div>';
    html += renderCapabilityBulletCard('Proposed Data Model', typeMeta.data_model || []);
    html += renderCapabilityBulletCard('UI Flow', typeMeta.ui_flow || []);
    html += renderCapabilityLayoutCard(typeMeta.layout || []);
    html += renderCapabilityBulletCard('Phase 1 Scope', typeMeta.mvp_scope || []);
    html += '</div></div>';
    return html;
}

function renderIntegrationCapabilityBuilder(typeMeta) {
    const draft = capabilityDraft('integration');
    const preview = capabilityPreview('integration');
    const options = capabilityIntegrationOptions();
    const currentOption = options.find(item => item && item.name === draft.kind) || capabilityDefaultIntegrationOption(draft.kind);
    const manifest = ((preview || {}).manifest || {}).integration || {};
    const readiness = manifest.readiness || {};
    const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
    const writes = Array.isArray((preview || {}).writes) ? preview.writes : [];
    const configWrite = writes.find(item => item && item.path && item.path.endsWith('config.yaml'));

    let html = '<div class="card mb-16"><div class="card-header"><span>' + escH(typeMeta.label || 'Create Integration') + '</span><span class="badge badge-success">' + escH(typeMeta.phase || 'Phase 2') + '</span></div><div class="card-body">';
    html += '<p class="text-sm text-secondary mb-16">Integrations write to the same Hermes config and env files the existing Apps & Integrations screen already manages. Preview first, then approve the exact config and env changes.</p>';
    html += '</div></div>';
    html += '<div class="capability-builder-grid">';
    html += '<div class="capability-builder-column">';
    html += '<div class="card mb-16"><div class="card-header"><span>Integration Draft</span></div><div class="card-body">';
    html += '<div class="capability-form-grid">';
    html += '<div class="form-group"><label class="form-label">Integration Kind</label>' + selectH('capability-integration-kind', options.map(item => ({ value: item.name, label: item.label })), draft.kind || (currentOption.name || 'discord'), 'capability-integration-kind').replace('<select', '<select onchange="updateCapabilityDraftField(\'kind\', this.value)"') + '</div>';
    html += '<div class="form-group capability-list-wide"><label class="form-label">Config JSON</label>' + textareaH('capability-integration-config', draft.config || '{}', 12, true).replace('<textarea', '<textarea oninput="updateCapabilityDraftField(\'config\', this.value)"') + '<div class="capability-field-note">This writes the top-level <span class="font-mono">' + escH((draft.kind || currentOption.name || 'integration')) + '</span> integration block in <span class="font-mono">~/.hermes/config.yaml</span>.</div></div>';
    html += '</div>';
    html += '<div class="capability-builder-actions"><button class="btn btn-primary" onclick="previewCapabilityDraft()">Preview Draft</button><button class="btn" onclick="resetCapabilityDraft(\'integration\')">Reset</button></div>';
    html += '</div></div>';
    html += renderCapabilityListSection(
        'Environment Variables',
        'env_vars',
        (draft.env_vars || []).map((entry, index) => capabilityIntegrationEnvVarRowH(entry, index)),
        'Add tokens or secrets this integration should save into ~/.hermes/.env.',
        '+ Add Env Var'
    );
    html += '</div>';

    html += '<div class="capability-builder-column">';
    html += renderCapabilityCreatedCard('integration');
    html += '<div id="capability-preview-anchor"></div><div class="card mb-16"><div class="card-header"><span>Preview & Approval</span></div><div class="card-body">';
    if (!preview) {
        html += '<div class="empty-state"><p>Preview the draft to inspect the config block, any env-var writes, and readiness blockers before approval.</p></div>';
    } else {
        const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];
        html += '<div class="capability-preview-summary">';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Target</div><div class="runtime-readiness-value">' + escH((preview.summary || {}).kind || '') + '</div><div class="runtime-readiness-detail">' + escH((currentOption.label || (preview.summary || {}).kind || '').toString()) + '</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Env Vars</div><div class="runtime-readiness-value">' + escH(String((preview.summary || {}).env_var_count || 0)) + '</div><div class="runtime-readiness-detail">' + escH(String((preview.summary || {}).env_write_count || 0)) + ' will be written now</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Approval</div><div class="runtime-readiness-value">' + escH(preview.can_apply ? 'Ready' : 'Blocked') + '</div><div class="runtime-readiness-detail">' + escH(preview.can_apply ? 'Approve to write config and env changes.' : 'Resolve the existing configured integration first.') + '</div></div>';
        html += '</div>';
        if (warnings.length) {
            html += '<div class="card mt-16"><div class="card-header"><span>Warnings</span></div><div class="card-body"><ul class="plain-list">' + warnings.map(warning => '<li>' + escH(warning) + '</li>').join('') + '</ul></div></div>';
        }
        html += '<div id="capability-preview-actions" class="capability-builder-actions mt-16"><button id="capability-approve-button" class="btn btn-primary" ' + (preview.can_apply ? '' : 'disabled ') + 'onclick="applyCapabilityDraft()">Approve & Create Integration</button><button class="btn" onclick="previewCapabilityDraft()">Refresh Preview</button></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Will Write</span></div><div class="card-body"><div class="capability-layout-list">' +
            writes.map(item =>
                '<div class="capability-layout-item"><div class="capability-layout-kind">' + escH((item.kind || 'item').toUpperCase()) + '</div><div class="font-mono text-sm">' + escH(item.path || '') + '</div><div class="capability-layout-detail">' + escH(item.label || item.action || '') + (item.key ? ' · ' + escH(item.key) : '') + '</div></div>'
            ).join('') +
            '</div></div></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Readiness After Create</span></div><div class="card-body">';
        if (!blockers.length) {
            html += '<p class="text-sm text-secondary">This integration draft will be ready immediately after the write.</p>';
        } else {
            html += '<div class="skill-setup-blocker-list">' + blockers.map(blocker =>
                '<div class="skill-setup-blocker"><div class="skill-setup-blocker-title">' + escH(blocker.message || 'Setup needed') + '</div>' +
                (blocker.kind === 'env_var'
                    ? '<div class="skill-setup-blocker-detail">Group: ' + escH(blocker.group || 'System') + '</div>'
                    : '<div class="skill-setup-blocker-detail">Add at least one meaningful config value or save the missing env vars.</div>') +
                '</div>'
            ).join('') + '</div>';
        }
        html += '</div></div>';
        if (configWrite) {
            html += '<div class="card mt-16"><div class="card-header"><span>Generated Integration Config</span></div><div class="card-body"><pre class="font-mono text-xs capability-code-preview">' + escH(configWrite.content || '') + '</pre></div></div>';
        }
    }
    html += '</div></div>';
    html += renderCapabilityBulletCard('Proposed Data Model', typeMeta.data_model || []);
    html += renderCapabilityBulletCard('UI Flow', typeMeta.ui_flow || []);
    html += renderCapabilityLayoutCard(typeMeta.layout || []);
    html += renderCapabilityBulletCard('MVP Scope', typeMeta.mvp_scope || []);
    html += '</div></div>';
    return html;
}

function renderAgentPresetCapabilityBuilder(typeMeta) {
    const draft = capabilityDraft('agent_preset');
    const preview = capabilityPreview('agent_preset');
    const profiles = capabilityProfileList();
    const skills = capabilitySkillList();
    const integrations = capabilityIntegrationOptions();
    const writes = Array.isArray((preview || {}).writes) ? preview.writes : [];
    const yamlWrite = writes.find(item => item && item.content);
    const manifest = (preview || {}).manifest || {};
    const selectedSkills = Array.isArray(manifest.skills) ? manifest.skills : [];
    const selectedIntegrations = Array.isArray(manifest.integrations) ? manifest.integrations : [];

    let html = '<div class="card mb-16"><div class="card-header"><span>' + escH(typeMeta.label || 'Create Agent Preset') + '</span><span class="badge badge-success">' + escH(typeMeta.phase || 'Phase 3') + '</span></div><div class="card-body">';
    html += '<p class="text-sm text-secondary mb-16">Agent presets store a reusable personality overlay plus the model roles, skills, integrations, and agent defaults that preset expects around it.</p>';
    html += '</div></div>';
    html += '<div class="capability-builder-grid">';
    html += '<div class="capability-builder-column">';
    html += '<div class="card mb-16"><div class="card-header"><span>Preset Draft</span></div><div class="card-body">';
    html += '<div class="capability-form-grid">';
    html += '<div class="form-group"><label class="form-label">Name</label>' + inputH('capability-preset-name', draft.name || '', 'text', 'code-reviewer', 'oninput="updateCapabilityDraftField(\'name\', this.value)"') + '</div>';
    html += '<div class="form-group"><label class="form-label">Reasoning Effort</label>' + selectH('capability-preset-reasoning', ['', 'none', 'low', 'medium', 'high', 'xhigh', 'minimal'], draft.reasoning_effort || '', 'capability-preset-reasoning').replace('<select', '<select onchange="updateCapabilityDraftField(\'reasoning_effort\', this.value)"') + '</div>';
    html += '<div class="form-group"><label class="form-label">Max Turns</label>' + inputH('capability-preset-max-turns', draft.max_turns || '', 'number', '90', 'oninput="updateCapabilityDraftField(\'max_turns\', this.value)" min="1"') + '</div>';
    html += '<div class="form-group capability-list-wide"><label class="form-label">Description</label>' + textareaH('capability-preset-description', draft.description || '', 3, false).replace('<textarea', '<textarea oninput="updateCapabilityDraftField(\'description\', this.value)"') + '</div>';
    html += '<div class="form-group capability-list-wide"><label class="form-label">System Prompt</label>' + textareaH('capability-preset-system-prompt', draft.system_prompt || '', 8, false).replace('<textarea', '<textarea oninput="updateCapabilityDraftField(\'system_prompt\', this.value)"') + '<div class="capability-field-note">This becomes the preset personality overlay stored in Hermes config.</div></div>';
    html += '</div>';
    html += '<div class="capability-builder-actions"><button class="btn btn-primary" onclick="previewCapabilityDraft()">Preview Draft</button><button class="btn" onclick="resetCapabilityDraft(\'agent_preset\')">Reset</button></div>';
    html += '</div></div>';
    ['primary', 'fallback', 'vision'].forEach(role => {
        html += renderAgentRoleCard(role, (((draft.roles || {})[role]) || {}), profiles);
    });
    html += renderCapabilitySelectionCard(
        'Skills',
        'skills',
        skills.map(item => ({
            path: item.path,
            name: item.name || item.path,
            description: item.description || '',
            enabled: item.enabled,
            ready: item.ready,
        })),
        draft.skills || [],
        'Install or create skills first, then reference the ones this preset expects.'
    );
    html += renderCapabilitySelectionCard(
        'Integrations',
        'integrations',
        integrations.map(item => ({
            name: item.name,
            label: item.label || item.name,
            description: item.exists ? 'Available in current config' : 'Known Hermes integration',
            configured: item.configured,
        })),
        draft.integrations || [],
        'No integrations were discovered in capability context.'
    );
    html += '</div>';

    html += '<div class="capability-builder-column">';
    html += renderCapabilityCreatedCard('agent_preset');
    html += '<div id="capability-preview-anchor"></div><div class="card mb-16"><div class="card-header"><span>Preview & Approval</span></div><div class="card-body">';
    if (!preview) {
        html += '<div class="empty-state"><p>Preview the preset to inspect the stored personality payload, referenced skills, integrations, and model-role bindings before approval.</p></div>';
    } else {
        const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];
        html += '<div class="capability-preview-summary">';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Preset</div><div class="runtime-readiness-value">' + escH((preview.summary || {}).name || '') + '</div><div class="runtime-readiness-detail">' + escH((manifest.storage_path || 'agent.personalities')) + '</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Composition</div><div class="runtime-readiness-value">' + escH(String((preview.summary || {}).enabled_role_count || 0)) + ' roles</div><div class="runtime-readiness-detail">' + escH(String((preview.summary || {}).skill_count || 0)) + ' skills, ' + escH(String((preview.summary || {}).integration_count || 0)) + ' integrations</div></div>';
        html += '<div class="capability-preview-item"><div class="runtime-readiness-label">Approval</div><div class="runtime-readiness-value">' + escH(preview.can_apply ? 'Ready' : 'Blocked') + '</div><div class="runtime-readiness-detail">' + escH(preview.can_apply ? 'Approve to save the preset.' : 'Resolve the existing preset name conflict first.') + '</div></div>';
        html += '</div>';
        if (warnings.length) {
            html += '<div class="card mt-16"><div class="card-header"><span>Warnings</span></div><div class="card-body"><ul class="plain-list">' + warnings.map(warning => '<li>' + escH(warning) + '</li>').join('') + '</ul></div></div>';
        }
        html += '<div id="capability-preview-actions" class="capability-builder-actions mt-16"><button id="capability-approve-button" class="btn btn-primary" ' + (preview.can_apply ? '' : 'disabled ') + 'onclick="applyCapabilityDraft()">Approve & Create Agent Preset</button><button class="btn" onclick="previewCapabilityDraft()">Refresh Preview</button></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Will Write</span></div><div class="card-body"><div class="capability-layout-list">' +
            writes.map(item =>
                '<div class="capability-layout-item"><div class="capability-layout-kind">' + escH((item.kind || 'item').toUpperCase()) + '</div><div class="font-mono text-sm">' + escH(item.path || '') + '</div><div class="capability-layout-detail">' + escH(item.label || item.action || '') + '</div></div>'
            ).join('') +
            '</div></div></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Referenced Skills</span></div><div class="card-body">';
        if (!selectedSkills.length) html += '<p class="text-sm text-secondary">No skills were selected for this preset.</p>';
        else html += '<ul class="plain-list">' + selectedSkills.map(item => '<li><span class="font-mono">' + escH(item.path || '') + '</span>' + (item.ready === false ? ' · needs setup' : item.enabled === false ? ' · disabled' : '') + '</li>').join('') + '</ul>';
        html += '</div></div>';
        html += '<div class="card mt-16"><div class="card-header"><span>Referenced Integrations</span></div><div class="card-body">';
        if (!selectedIntegrations.length) html += '<p class="text-sm text-secondary">No integrations were selected for this preset.</p>';
        else html += '<ul class="plain-list">' + selectedIntegrations.map(item => '<li><span class="font-mono">' + escH(item.name || '') + '</span>' + (item.configured ? '' : ' · not configured yet') + '</li>').join('') + '</ul>';
        html += '</div></div>';
        if (yamlWrite) {
            html += '<div class="card mt-16"><div class="card-header"><span>Generated Preset Fragment</span></div><div class="card-body"><pre class="font-mono text-xs capability-code-preview">' + escH(yamlWrite.content || '') + '</pre></div></div>';
        }
    }
    html += '</div></div>';
    html += renderCapabilityBulletCard('Proposed Data Model', typeMeta.data_model || []);
    html += renderCapabilityBulletCard('UI Flow', typeMeta.ui_flow || []);
    html += renderCapabilityLayoutCard(typeMeta.layout || []);
    html += renderCapabilityBulletCard('MVP Scope', typeMeta.mvp_scope || []);
    html += '</div></div>';
    return html;
}

function renderCapabilityBuilder() {
    if (!screenIsActive('capabilities')) return;
    const editorState = (capabilityBuilderState.focusBuilderOnRender || capabilityBuilderState.focusPreviewOnRender) ? null : captureCapabilityEditorState();
    const content = document.getElementById('content');
    if (!content) return;
    const catalog = capabilityBuilderState.catalog || { types: [] };
    const typeMeta = capabilityCatalogType(capabilityBuilderState.selectedType);
    let html = '<div class="section-header skill-page-header"><span>Create Capability</span><div class="skill-page-actions">';
    html += '<button class="' + capabilitySelectButtonClass('skill') + '" onclick="selectCapabilityType(\'skill\')">Create Skill</button>';
    html += '<button class="' + capabilitySelectButtonClass('integration') + '" onclick="selectCapabilityType(\'integration\')">Create Integration</button>';
    html += '<button class="' + capabilitySelectButtonClass('agent_preset') + '" onclick="selectCapabilityType(\'agent_preset\')">Create Agent Preset</button>';
    html += '</div></div>';
    html += '<div class="card mb-16"><div class="card-body"><p class="text-sm text-secondary">The capability system is phased on purpose: skills are the primary extension mechanism, integrations capture connection state, and agent presets compose models plus capabilities. Every type uses the same draft preview and approval pattern before writing files or config.</p></div></div>';
    html += renderCapabilityOverview(catalog);
    html += '<div class="card mb-16"><div class="card-header"><span>Capability Types</span></div><div class="card-body">' + renderCapabilityTypeCards() + '</div></div>';
    html += '<div id="capability-builder-anchor"></div>';
    if (capabilityBuilderState.selectedType === 'skill') html += renderSkillCapabilityBuilder(typeMeta);
    else if (capabilityBuilderState.selectedType === 'integration') html += renderIntegrationCapabilityBuilder(typeMeta);
    else if (capabilityBuilderState.selectedType === 'agent_preset') html += renderAgentPresetCapabilityBuilder(typeMeta);
    content.innerHTML = html;
    if (capabilityBuilderState.focusBuilderOnRender) {
        capabilityBuilderState.focusBuilderOnRender = false;
        requestAnimationFrame(() => focusCapabilityBuilder());
    } else if (capabilityBuilderState.focusPreviewOnRender) {
        capabilityBuilderState.focusPreviewOnRender = false;
        requestAnimationFrame(() => focusCapabilityPreview());
    } else {
        restoreCapabilityEditorState(editorState);
    }
}

Screens.capabilities = async function () {
    const content = document.getElementById('content');
    try {
        capabilityBuilderState.catalog = await api('GET', '/api/capabilities');
        if (!screenIsActive('capabilities')) return;
        renderCapabilityBuilder();
    } catch (e) {
        if (!screenIsActive('capabilities')) return;
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.openCreateCapability = function (type = 'skill') {
    capabilityBuilderState.selectedType = type || 'skill';
    capabilityBuilderState.focusBuilderOnRender = true;
    capabilityBuilderState.focusPreviewOnRender = false;
    navigate('capabilities');
};

window.selectCapabilityType = function (type) {
    capabilityBuilderState.selectedType = type || 'skill';
    capabilityBuilderState.focusBuilderOnRender = true;
    capabilityBuilderState.focusPreviewOnRender = false;
    renderCapabilityBuilder();
};

window.updateCapabilityDraftField = function (field, value) {
    const type = capabilityBuilderState.selectedType || 'skill';
    const draft = capabilityDraft(type);
    if (type === 'skill') {
        if (field === 'name') {
            const previousAuto = capabilitySlugify(draft.name || '');
            draft.name = value;
            if (!draft.slug || draft.slug === previousAuto) {
                draft.slug = capabilitySlugify(value);
            }
        } else {
            draft[field] = value;
        }
    } else if (type === 'integration') {
        if (field === 'kind') {
            capabilityBuilderState.drafts.integration = defaultIntegrationCapabilityDraft(value);
        } else {
            draft[field] = value;
        }
    } else {
        draft[field] = value;
    }
    clearCapabilityPreview(type);
    if (currentScreenId() === 'capabilities') renderCapabilityBuilder();
};

window.addCapabilityListItem = function (key) {
    const type = capabilityBuilderState.selectedType || 'skill';
    const draft = capabilityDraft(type);
    if (!Array.isArray(draft[key])) draft[key] = [];
    if (key === 'env_vars' && type === 'skill') draft[key].push({ key: '', label: '', group: 'Provider', description: '' });
    if (key === 'env_vars' && type === 'integration') draft[key].push({ key: '', label: '', group: 'Channel', description: '', value: '' });
    if (key === 'credential_files') draft[key].push({ path: '', label: '', description: '' });
    if (key === 'required_commands') draft[key].push({ name: '', description: '' });
    clearCapabilityPreview(type);
    renderCapabilityBuilder();
};

window.updateCapabilityListItem = function (key, index, field, value) {
    const type = capabilityBuilderState.selectedType || 'skill';
    const draft = capabilityDraft(type);
    if (!Array.isArray(draft[key]) || !draft[key][index]) return;
    draft[key][index][field] = value;
    clearCapabilityPreview(type);
    renderCapabilityBuilder();
};

window.removeCapabilityListItem = function (key, index) {
    const type = capabilityBuilderState.selectedType || 'skill';
    const draft = capabilityDraft(type);
    if (!Array.isArray(draft[key])) return;
    draft[key].splice(index, 1);
    clearCapabilityPreview(type);
    renderCapabilityBuilder();
};

window.resetCapabilitySkillDraft = function () {
    resetCapabilityDraftState('skill');
    renderCapabilityBuilder();
};

window.resetCapabilityDraft = function (type) {
    resetCapabilityDraftState(type || capabilityBuilderState.selectedType || 'skill');
    renderCapabilityBuilder();
};

window.clearCreatedCapabilityNotice = function () {
    capabilityBuilderState.lastCreated = null;
    renderCapabilityBuilder();
};

window.updateCapabilityRoleField = function (role, field, value) {
    const draft = capabilityDraft('agent_preset');
    if (!draft.roles) draft.roles = {};
    if (!draft.roles[role]) draft.roles[role] = { enabled: role === 'primary', profile: '', model: '', routing_provider: '' };
    draft.roles[role][field] = value;
    if (field === 'profile' && value && !draft.roles[role].model) {
        const profile = capabilityProfileList().find(item => item.name === value);
        if (profile && profile.model) draft.roles[role].model = profile.model;
    }
    clearCapabilityPreview('agent_preset');
    renderCapabilityBuilder();
};

window.toggleCapabilitySelection = function (key, value, checked) {
    const draft = capabilityDraft('agent_preset');
    if (!Array.isArray(draft[key])) draft[key] = [];
    const list = draft[key].slice();
    const next = checked
        ? Array.from(new Set(list.concat([value])))
        : list.filter(item => item !== value);
    draft[key] = next;
    clearCapabilityPreview('agent_preset');
    renderCapabilityBuilder();
};

window.previewCapabilityDraft = async function () {
    const type = capabilityBuilderState.selectedType || 'skill';
    toast('Building draft preview...', 'info', 1500);
    try {
        const resp = await api('POST', '/api/capabilities/preview', {
            type,
            draft: capabilityDraft(type),
        });
        capabilityBuilderState.previews[type] = resp;
        capabilityBuilderState.lastCreated = null;
        capabilityBuilderState.focusPreviewOnRender = true;
        renderCapabilityBuilder();
        toast('Draft preview ready', 'success');
    } catch (e) {
        toast('Preview failed: ' + e.message, 'error');
    }
};

window.applyCapabilityDraft = async function () {
    const type = capabilityBuilderState.selectedType || 'skill';
    const preview = capabilityPreview(type);
    if (!preview || !preview.preview_token) {
        toast('Preview the draft before approval', 'warning');
        return;
    }
    if (preview.can_apply === false) {
        toast('Resolve the draft warnings before approval', 'error');
        return;
    }
    toast('Writing capability...', 'info', 1500);
    try {
        const resp = await api('POST', '/api/capabilities/apply', {
            type,
            draft: capabilityDraft(type),
            preview_token: preview.preview_token,
        });
        capabilityBuilderState.lastCreated = {
            type,
            ...(resp.created || {}),
        };
        if (type === 'skill' && resp.created && (resp.created.slug || resp.created.path)) {
            const skillPath = resp.created.slug || resp.created.path;
            skillCatalogState.recentPaths[skillPath] = true;
            skillCatalogState.focusPathOnRender = skillPath;
        }
        if (type === 'integration' && resp.created && (resp.created.kind || resp.created.name)) {
            const integrationName = resp.created.kind || resp.created.name;
            integrationCatalogState.recentNames[integrationName] = true;
            integrationCatalogState.focusNameOnRender = integrationName;
        }
        if (type === 'agent_preset' && resp.created && resp.created.name) {
            const presetName = resp.created.name;
            agentCatalogState.recentNames[presetName] = true;
            agentCatalogState.focusNameOnRender = presetName;
        }
        if (type === 'skill') capabilityBuilderState.drafts.skill = defaultSkillCapabilityDraft();
        else if (type === 'integration') capabilityBuilderState.drafts.integration = defaultIntegrationCapabilityDraft();
        else if (type === 'agent_preset') capabilityBuilderState.drafts.agent_preset = defaultAgentPresetDraft();
        capabilityBuilderState.previews[type] = null;
        renderCapabilityBuilder();
        toast((resp.created || {}).name ? ((resp.created || {}).name + ' created') : 'Capability created', 'success');
    } catch (e) {
        toast('Create failed: ' + e.message, 'error');
    }
};

const starterPackState = {
    items: {},
};
const skillCatalogState = {
    items: {},
    list: [],
    runtime: {},
    policy: {},
    searchQuery: '',
    statusFilter: 'all',
    sourceFilter: 'all',
    categoryFilter: 'all',
    collapsedSources: {},
    selectedPaths: {},
    selectedSourceKey: '',
    recentPaths: {},
    focusPathOnRender: '',
};

const integrationCatalogState = {
    recentNames: {},
    focusNameOnRender: '',
};

const agentCatalogState = {
    recentNames: {},
    focusNameOnRender: '',
};

const UNCATEGORIZED_SKILL_FILTER = '__uncategorized__';

function rememberSkills(skills) {
    const nextList = Array.isArray(skills) ? skills : [];
    skillCatalogState.list = nextList;
    skillCatalogState.items = {};
    nextList.forEach(skill => {
        if (skill && skill.path) skillCatalogState.items[skill.path] = skill;
    });

    Object.keys(skillCatalogState.selectedPaths || {}).forEach(path => {
        if (!skillCatalogState.items[path]) delete skillCatalogState.selectedPaths[path];
    });
    if (!selectedSkillPaths().length) {
        skillCatalogState.selectedSourceKey = '';
    } else if (
        skillCatalogState.selectedSourceKey &&
        skillCatalogState.selectedSourceKey !== '__starter_pack__' &&
        !selectedSkillPaths().some(path => skillSourceKey(skillCatalogState.items[path] || {}) === skillCatalogState.selectedSourceKey)
    ) {
        skillCatalogState.selectedSourceKey = skillSourceKey(skillCatalogState.items[selectedSkillPaths()[0]] || {}) || '';
    }
    Object.keys(skillCatalogState.recentPaths || {}).forEach(path => {
        if (!skillCatalogState.items[path]) delete skillCatalogState.recentPaths[path];
    });

    const validSourceKeys = new Set(nextList.map(skill => skillSourceKey(skill)));
    if (skillCatalogState.sourceFilter !== 'all' && !validSourceKeys.has(skillCatalogState.sourceFilter)) {
        skillCatalogState.sourceFilter = 'all';
    }
    const validCategoryKeys = new Set(nextList.map(skill => skillCategoryKey(skill)));
    if (skillCatalogState.categoryFilter !== 'all' && !validCategoryKeys.has(skillCatalogState.categoryFilter)) {
        skillCatalogState.categoryFilter = 'all';
    }
}

function skillSourceLabel(skill) {
    const source = (skill && skill.source) || {};
    return source.display || source.identifier || source.source_repo || 'Local / Unknown';
}

function skillSourceKey(skill) {
    const source = (skill && skill.source) || {};
    return source.identifier || source.source_repo || source.display || 'Local / Unknown';
}

function skillSourceMeta(skill) {
    const source = (skill && skill.source) || {};
    if (!source.identifier && !source.source_repo && !source.catalog_source) {
        return 'Manual or older skills without recorded repo metadata.';
    }
    if (source.install_mode === 'webui_create') {
        return 'Created in Hermes Web UI.';
    }
    if (source.install_mode === 'github_repo' && source.source_repo) {
        return 'Imported from GitHub repo.';
    }
    if (source.catalog_source) {
        return 'Installed via ' + source.catalog_source + '.';
    }
    if (source.install_mode === 'hermes') {
        return 'Installed through the Hermes CLI.';
    }
    return 'Recorded source metadata is available for this skill.';
}

function skillCategoryKey(skill) {
    const category = String((skill && skill.category) || '').trim();
    return category || UNCATEGORIZED_SKILL_FILTER;
}

function skillCategoryLabel(valueOrSkill) {
    const key = typeof valueOrSkill === 'string'
        ? String(valueOrSkill || '').trim()
        : skillCategoryKey(valueOrSkill || {});
    if (!key || key === UNCATEGORIZED_SKILL_FILTER) {
        return 'Uncategorized';
    }
    return key;
}

function skillSetupStatus(skill) {
    const setup = (skill && skill.setup) || {};
    return {
        ready: setup.ready !== false,
        issues: Array.isArray(setup.issues) ? setup.issues : [],
        blockers: Array.isArray(setup.blockers) ? setup.blockers : [],
        actions: Array.isArray(setup.actions) ? setup.actions : [],
    };
}

function renderSkillInstallPathList(paths) {
    const list = Array.isArray(paths) ? paths.filter(Boolean) : [];
    if (!list.length) {
        return '<div class="empty-state"><p>No matching skill paths reported.</p></div>';
    }
    return '<ul class="plain-list">' + list.map(path => '<li><span class="font-mono">' + escH(path) + '</span></li>').join('') + '</ul>';
}

function skillSortKey(skill) {
    return String((skill && (skill.name || skill.path)) || '').toLowerCase();
}

function sortSkillsStable(skills) {
    return (Array.isArray(skills) ? skills.slice() : []).sort((a, b) => {
        const aKey = skillSortKey(a);
        const bKey = skillSortKey(b);
        if (aKey < bKey) return -1;
        if (aKey > bKey) return 1;
        return String((a && a.path) || '').localeCompare(String((b && b.path) || ''));
    });
}

function skillElementId(path) {
    return 'skill-card-' + String(path || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
}

function integrationElementId(name) {
    return 'integration-card-' + String(name || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
}

function agentElementId(name) {
    return 'agent-card-' + String(name || '').toLowerCase().replace(/[^a-z0-9_-]+/g, '-');
}

function focusInventoryCardById(elementId) {
    const target = elementId ? document.getElementById(elementId) : null;
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const focusTarget = target.querySelector('button, input, [tabindex]');
    if (focusTarget && typeof focusTarget.focus === 'function') {
        try { focusTarget.focus({ preventScroll: true }); }
        catch (e) { focusTarget.focus(); }
    }
}

function captureSkillsInventoryState() {
    const content = document.getElementById('content');
    const active = document.activeElement;
    if (!active || !active.id) {
        return {
            scrollX: window.scrollX || 0,
            scrollY: window.scrollY || 0,
            contentScrollTop: content ? content.scrollTop : 0,
            contentScrollLeft: content ? content.scrollLeft : 0,
        };
    }
    const canRestoreSelection = typeof active.selectionStart === 'number' && typeof active.selectionEnd === 'number';
    return {
        id: active.id,
        selectionStart: canRestoreSelection ? active.selectionStart : null,
        selectionEnd: canRestoreSelection ? active.selectionEnd : null,
        scrollX: window.scrollX || 0,
        scrollY: window.scrollY || 0,
        contentScrollTop: content ? content.scrollTop : 0,
        contentScrollLeft: content ? content.scrollLeft : 0,
    };
}

function restoreSkillsInventoryState(state) {
    if (!state) return;
    const scrollX = Number.isFinite(state.scrollX) ? state.scrollX : 0;
    const scrollY = Number.isFinite(state.scrollY) ? state.scrollY : 0;
    const contentScrollTop = Number.isFinite(state.contentScrollTop) ? state.contentScrollTop : 0;
    const contentScrollLeft = Number.isFinite(state.contentScrollLeft) ? state.contentScrollLeft : 0;
    const restore = () => {
        const content = document.getElementById('content');
        if (content) {
            content.scrollTop = contentScrollTop;
            content.scrollLeft = contentScrollLeft;
        }
        const el = state.id ? document.getElementById(state.id) : null;
        if (el && typeof el.focus === 'function') {
            try { el.focus({ preventScroll: true }); }
            catch (e) { el.focus(); }
            if (typeof state.selectionStart === 'number' && typeof state.selectionEnd === 'number' && typeof el.setSelectionRange === 'function') {
                try { el.setSelectionRange(state.selectionStart, state.selectionEnd); } catch (e) { /* ignore */ }
            }
        }
        if (content) {
            content.scrollTop = contentScrollTop;
            content.scrollLeft = contentScrollLeft;
        }
        window.scrollTo(scrollX, scrollY);
    };
    requestAnimationFrame(restore);
}

function focusSkillInventoryPath(path) {
    const target = path ? document.getElementById(skillElementId(path)) : null;
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const focusTarget = target.querySelector('button, input, [tabindex]');
    if (focusTarget && typeof focusTarget.focus === 'function') {
        try { focusTarget.focus({ preventScroll: true }); }
        catch (e) { focusTarget.focus(); }
    }
}

function finishSkillsInventoryRender(content, html, viewState) {
    if (!content) return;
    content.innerHTML = html;
    if (skillCatalogState.focusPathOnRender) {
        const focusPath = skillCatalogState.focusPathOnRender;
        skillCatalogState.focusPathOnRender = '';
        requestAnimationFrame(() => focusSkillInventoryPath(focusPath));
        return;
    }
    restoreSkillsInventoryState(viewState);
}

function skillSearchText(skill) {
    const source = skill.source || {};
    const setup = skillSetupStatus(skill);
    return [
        skill.name,
        skill.path,
        skill.description,
        skill.category || '',
        skillSourceLabel(skill),
        source.identifier || '',
        source.source_repo || '',
        ...setup.issues,
    ].join(' ').toLowerCase();
}

function availableSkillSourceKeys() {
    return Array.from(new Set(skillCatalogState.list.map(skill => skillSourceKey(skill)))).sort((a, b) => {
        if (a === 'Local / Unknown') return -1;
        if (b === 'Local / Unknown') return 1;
        return String(a).localeCompare(String(b));
    });
}

function availableSkillCategoryKeys() {
    return Array.from(new Set(skillCatalogState.list.map(skill => skillCategoryKey(skill)))).sort((a, b) => {
        if (a === UNCATEGORIZED_SKILL_FILTER) return 1;
        if (b === UNCATEGORIZED_SKILL_FILTER) return -1;
        return String(a).localeCompare(String(b));
    });
}

function isSkillSelected(path) {
    return !!skillCatalogState.selectedPaths[path];
}

function selectedSkillPaths() {
    return Object.keys(skillCatalogState.selectedPaths || {}).filter(path => skillCatalogState.selectedPaths[path]);
}

function selectedSkillPathsForSource(sourceKey) {
    if (sourceKey === '__starter_pack__') {
        return skillCatalogState.selectedSourceKey === '__starter_pack__' ? selectedSkillPaths() : [];
    }
    return selectedSkillPaths().filter(path => skillSourceKey(skillCatalogState.items[path] || {}) === sourceKey);
}

function clearSelectedSkillsState() {
    skillCatalogState.selectedPaths = {};
    skillCatalogState.selectedSourceKey = '';
}

function activateSelectionSource(sourceKey) {
    const nextKey = String(sourceKey || '');
    if (!nextKey) {
        clearSelectedSkillsState();
        return;
    }
    if (skillCatalogState.selectedSourceKey && skillCatalogState.selectedSourceKey !== nextKey) {
        skillCatalogState.selectedPaths = {};
    }
    skillCatalogState.selectedSourceKey = nextKey;
}

function skillMatchesFilters(skill) {
    const searchNeedle = String(skillCatalogState.searchQuery || '').trim().toLowerCase();
    const statusFilter = skillCatalogState.statusFilter || 'all';
    const sourceFilter = skillCatalogState.sourceFilter || 'all';
    const categoryFilter = skillCatalogState.categoryFilter || 'all';
    const setup = skillSetupStatus(skill);
    const enabled = skill.enabled !== false;

    if (sourceFilter !== 'all' && skillSourceKey(skill) !== sourceFilter) {
        return false;
    }
    if (categoryFilter !== 'all' && skillCategoryKey(skill) !== categoryFilter) {
        return false;
    }
    if (statusFilter === 'enabled' && !enabled) {
        return false;
    }
    if (statusFilter === 'disabled' && enabled) {
        return false;
    }
    if (statusFilter === 'needs_setup' && setup.ready) {
        return false;
    }
    if (statusFilter === 'ready' && !setup.ready) {
        return false;
    }
    if (searchNeedle && !skillSearchText(skill).includes(searchNeedle)) {
        return false;
    }
    return true;
}

function visibleSkills() {
    return skillCatalogState.list.filter(skillMatchesFilters);
}

function visibleSkillsForSource(sourceKey, section = 'all') {
    return visibleSkills().filter(skill => {
        if (skillSourceKey(skill) !== sourceKey) return false;
        if (section === 'enabled') return skill.enabled !== false;
        if (section === 'disabled') return skill.enabled === false;
        return true;
    });
}

function skillsInventoryIsDefaultView() {
    return !String(skillCatalogState.searchQuery || '').trim()
        && (skillCatalogState.statusFilter || 'all') === 'all'
        && (skillCatalogState.sourceFilter || 'all') === 'all'
        && (skillCatalogState.categoryFilter || 'all') === 'all';
}

function summarizeSkillGroup(skills) {
    const list = Array.isArray(skills) ? skills : [];
    return {
        total: list.length,
        enabled: list.filter(skill => skill.enabled !== false).length,
        disabled: list.filter(skill => skill.enabled === false).length,
        needsSetup: list.filter(skill => skillSetupStatus(skill).ready === false).length,
    };
}

function starterPackCollapsed(defaultCollapsed = true) {
    if (!Object.prototype.hasOwnProperty.call(skillCatalogState.collapsedSources, '__starter_pack__')) {
        skillCatalogState.collapsedSources.__starter_pack__ = !!defaultCollapsed;
    }
    return skillCatalogState.collapsedSources.__starter_pack__ === true;
}

function skillSourceBlocks(skills) {
    const grouped = {};
    (Array.isArray(skills) ? skills : []).forEach(skill => {
        const key = skillSourceKey(skill);
        if (!grouped[key]) {
            grouped[key] = {
                key,
                title: skillSourceLabel(skill),
                meta: skillSourceMeta(skill),
                representativePath: skill.path,
                skills: [],
            };
        }
        grouped[key].skills.push(skill);
    });
    return Object.values(grouped).sort((a, b) => {
        if (a.key === 'Local / Unknown') return -1;
        if (b.key === 'Local / Unknown') return 1;
        return a.title.localeCompare(b.title);
    });
}

function renderSkillFilterBar(totalSkills) {
    const sourceOptions = availableSkillSourceKeys();
    const categoryOptions = availableSkillCategoryKeys();
    const sourceSelect = '<select class="form-select skill-filter-select" id="skill-source-filter" onchange="setSkillSourceFilter(this.value)">' +
        '<option value="all"' + (skillCatalogState.sourceFilter === 'all' ? ' selected' : '') + '>All sources</option>' +
        sourceOptions.map(key => '<option value="' + escA(key) + '"' + (skillCatalogState.sourceFilter === key ? ' selected' : '') + '>' + escH(key) + '</option>').join('') +
        '</select>';
    const categorySelect = '<select class="form-select skill-filter-select" id="skill-category-filter" onchange="setSkillCategoryFilter(this.value)">' +
        '<option value="all"' + (skillCatalogState.categoryFilter === 'all' ? ' selected' : '') + '>All categories</option>' +
        categoryOptions.map(key => '<option value="' + escA(key) + '"' + (skillCatalogState.categoryFilter === key ? ' selected' : '') + '>' + escH(skillCategoryLabel(key)) + '</option>').join('') +
        '</select>';
    const filters = [
        ['all', 'All'],
        ['enabled', 'Enabled'],
        ['disabled', 'Disabled'],
        ['needs_setup', 'Needs Setup'],
        ['ready', 'Ready'],
    ];
    let html = '<div class="section-header skill-page-header"><span>' + totalSkills + ' Skills</span><div class="skill-page-actions">';
    html += '<button class="btn btn-primary" onclick="openCreateCapability(\'skill\')">Create Skill</button>';
    html += '<button class="btn" onclick="openSkillInstallModal()">Install Skill</button>';
    html += '<button class="btn" onclick="navigate(\'channels\')">Open Apps & Integrations</button>';
    html += '<button class="btn" onclick="navigate(\'env-vars\')">Open Env Vars</button>';
    html += '</div></div>';
    html += '<div class="card mb-16"><div class="card-body">';
    html += '<div class="skill-filter-bar">';
    html += '<div class="skill-filter-group">' + filters.map(([value, label]) =>
        '<button class="btn btn-sm ' + (skillCatalogState.statusFilter === value ? 'btn-primary' : '') + '" onclick="setSkillStatusFilter(\'' + escA(value) + '\')">' + escH(label) + '</button>'
    ).join('') + '</div>';
    html += '<div class="skill-filter-controls">';
    html += '<label class="skill-filter-label">Source / Origin</label>' + sourceSelect;
    html += '<label class="skill-filter-label">Category</label>' + categorySelect;
    html += '<div class="search-box skill-search-box"><span class="search-icon">' + UI_ICONS.search + '</span><input type="text" class="form-input" id="skill-search" placeholder="Search skills..." value="' + escA(skillCatalogState.searchQuery) + '" oninput="setSkillSearch(this.value)"></div>';
    if (skillCatalogState.searchQuery || skillCatalogState.statusFilter !== 'all' || skillCatalogState.sourceFilter !== 'all' || skillCatalogState.categoryFilter !== 'all') {
        html += '<button class="btn btn-sm" onclick="clearSkillFilters()">Clear Filters</button>';
    }
    html += '</div></div></div></div>';
    return html;
}

function renderSkillSourceSelectionBar(sourceKey, scopeLabel) {
    const paths = selectedSkillPathsForSource(sourceKey);
    if (!paths.length) {
        return '';
    }
    const detail = paths.length + ' selected';
    return '<div class="card skill-source-selection-bar"><div class="card-body skill-source-selection-body">' +
        '<div><div class="skill-bulk-title">' + escH(detail) + '</div><div class="text-sm text-secondary">Selection stays inside this block so bulk actions are tied to the repo you are working in.</div></div>' +
        '<div class="skill-bulk-actions">' +
            '<button class="btn btn-sm btn-selection" onclick="clearSkillSourceSelection(\'' + escA(sourceKey) + '\')">Clear Selected</button>' +
            '<button class="btn btn-sm btn-success" onclick="runSelectedSkillAction(\'enable\', \'' + escA(sourceKey) + '\', \'' + escA(scopeLabel || sourceKey) + '\')">Enable Selected</button>' +
            '<button class="btn btn-sm btn-warning" onclick="runSelectedSkillAction(\'disable\', \'' + escA(sourceKey) + '\', \'' + escA(scopeLabel || sourceKey) + '\')">Disable Selected</button>' +
            '<button class="btn btn-sm btn-danger" onclick="runSelectedSkillAction(\'remove\', \'' + escA(sourceKey) + '\', \'' + escA(scopeLabel || sourceKey) + '\')">Remove Selected</button>' +
        '</div>' +
    '</div></div>';
}

function collapseToggleLabel(expanded) {
    return (expanded ? '&#9662; Collapse' : '&#9656; Expand');
}

function starterPackSelectablePaths(item) {
    const paths = Array.isArray(item?.matched_skill_paths) ? item.matched_skill_paths : [];
    return Array.from(new Set(paths.filter(path => path && skillCatalogState.items[path])));
}

function renderStarterPackBlock(items) {
    const list = Array.isArray(items) ? items : [];
    starterPackState.items = {};
    list.forEach(item => {
        if (item && item.id) starterPackState.items[item.id] = item;
    });
    const collapsed = starterPackCollapsed(list.length === 0);
    const summary = {
        total: list.length,
        needsSetup: list.filter(item => item && item.status === 'attention').length,
        missing: list.filter(item => item && item.status === 'missing').length,
    };

    let html = '<div class="card mb-16 skill-source-block skill-source-block-wide starter-pack-block"><div class="card-header skill-source-block-header">';
    html += '<div><div class="skill-source-block-title">Starter Pack</div><div class="skill-source-block-meta">Recommended starter skills that still need attention. Ready installs move down into their real source blocks.</div></div>';
    html += '<div class="skill-source-block-summary">';
    html += '<span class="badge badge-info">' + summary.total + ' items</span>';
    if (summary.needsSetup) {
        html += '<span class="badge badge-warning">' + summary.needsSetup + ' need setup</span>';
    }
    if (summary.missing) {
        html += '<span class="badge badge-secondary">' + summary.missing + ' missing</span>';
    }
    html += '</div></div><div class="card-body">';
    html += '<div class="skill-source-block-actions">';
    html += '<button class="btn btn-sm" onclick="toggleStarterPackCollapsed()">' + collapseToggleLabel(!collapsed) + '</button>';
    html += '<button class="btn btn-sm" onclick="showStarterPackDetails()">Block Details</button>';
    html += '</div>';
    if (!collapsed) {
        html += renderStarterPackGrid(list);
    }
    html += '</div></div>';
    return html;
}

function renderSkillSourceBlock(group) {
    const visibleGroupSkills = sortSkillsStable(group.skills);
    const summary = summarizeSkillGroup(visibleGroupSkills);
    const expanded = skillCatalogState.collapsedSources[group.key] !== true;
    const selectedPaths = selectedSkillPathsForSource(group.key);
    const allSelected = visibleGroupSkills.length > 0 && visibleGroupSkills.every(skill => isSkillSelected(skill.path));
    const enabledSkills = visibleGroupSkills.filter(skill => skill.enabled !== false);
    const disabledSkills = visibleGroupSkills.filter(skill => skill.enabled === false);

    let html = '<div class="card mb-16 skill-source-block"><div class="card-header skill-source-block-header">';
    html += '<div><div class="skill-source-block-title">Source: ' + escH(group.title) + '</div><div class="skill-source-block-meta">' + escH(group.meta) + '</div></div>';
    html += '<div class="skill-source-block-summary">';
    html += '<span class="badge badge-info">' + summary.total + ' total</span>';
    html += '<span class="badge badge-success">' + summary.enabled + ' enabled</span>';
    if (summary.disabled) {
        html += '<span class="badge badge-danger">' + summary.disabled + ' disabled</span>';
    }
    if (summary.needsSetup) {
        html += '<span class="badge badge-warning">' + summary.needsSetup + ' need setup</span>';
    }
    html += '</div></div>';
    html += '<div class="card-body">';
    html += '<div class="skill-source-block-actions">';
    html += '<button class="btn btn-sm" onclick="toggleSkillSourceCollapsed(\'' + escA(group.key) + '\')">' + collapseToggleLabel(expanded) + '</button>';
    html += '<button class="btn btn-sm" onclick="showSkillSourceDetails(\'' + escA(group.representativePath) + '\')">Block Details</button>';
    html += '<button class="btn btn-sm btn-selection" onclick="toggleSkillSourceSelection(\'' + escA(group.key) + '\', ' + (allSelected ? 'false' : 'true') + ')">' + (allSelected ? 'Clear Block' : 'Select Block') + '</button>';
    html += '</div>';
    html += renderSkillSourceSelectionBar(group.key, group.title);
    if (expanded) {
        html += renderSkillTileSection('Enabled', enabledSkills);
        html += renderSkillTileSection('Disabled', disabledSkills);
    }
    html += '</div></div>';
    return html;
}

function renderSkillTileSection(title, skills) {
    const list = sortSkillsStable(skills);
    if (!list.length) {
        return '';
    }
    let html = '<div class="skill-subsection">';
    html += '<div class="skill-subsection-header"><div class="skill-subsection-title">' + escH(title) + '</div><div class="skill-subsection-actions"><span class="badge badge-info">' + list.length + '</span></div></div>';
    html += '<div class="starter-pack-grid skill-card-grid">';
    list.forEach(skill => {
        html += renderSkillTile(skill);
    });
    html += '</div></div>';
    return html;
}

function renderSkillTile(skill) {
    const setup = skillSetupStatus(skill);
    const recent = skillCatalogState.recentPaths[skill.path];
    const checked = isSkillSelected(skill.path);
    let html = '<div class="starter-pack-item skill-item-card" id="' + escA(skillElementId(skill.path)) + '" data-skill-name="' + escA(skill.name) + '" data-skill-path="' + escA(skill.path) + '" data-skill-search="' + escA(skillSearchText(skill)) + '">';
    html += '<div class="starter-pack-item-top"><div><div class="skill-name-line"><span class="starter-pack-item-title">' + escH(skill.name) + '</span>' +
        (recent ? '<span class="badge badge-info">New</span>' : '') + '</div>';
    html += '<div class="starter-pack-item-kind">Category: ' + escH(skillCategoryLabel(skill)) + '</div>';
    html += '</div><div class="skill-card-badges">';
    html += '<span class="badge ' + (skill.enabled !== false ? 'badge-success' : 'badge-danger') + '">' + (skill.enabled !== false ? 'Enabled' : 'Disabled') + '</span>';
    if (setup.ready) {
        html += '<span class="badge badge-success">Ready</span>';
    } else {
        html += '<span class="badge badge-warning">Needs Setup</span>';
    }
    html += '</div></div>';
    html += '<div class="starter-pack-item-detail">' + escH(skill.description || 'No description provided.') + '</div>';
    html += '<div class="starter-pack-item-actions skill-card-actions">';
    html += '<label class="skill-select-toggle"><input type="checkbox" ' + (checked ? 'checked ' : '') + 'onchange="toggleSkillSelection(\'' + escA(skill.path) + '\', this.checked)"><span>Select</span></label>';
    if (!setup.ready) {
        html += '<button class="btn btn-sm btn-warning" onclick="showSkillSetupDetails(\'' + escA(skill.path) + '\')">Needs Setup</button>';
    }
    html += '<button class="btn btn-sm" onclick="toggleSkill(\'' + escA(skill.path) + '\', \'' + escA(skill.name) + '\')">' + (skill.enabled !== false ? 'Disable' : 'Enable') + '</button>';
    html += '<button class="btn btn-sm" onclick="showSkillSourceDetails(\'' + escA(skill.path) + '\')">View Source</button>';
    html += '</div></div>';
    return html;
}

function renderSkillsInventory() {
    if (!screenIsActive('skills')) return;
    const content = document.getElementById('content');
    if (!content) return;
    const viewState = skillCatalogState.focusPathOnRender ? null : captureSkillsInventoryState();

    const skills = skillCatalogState.list || [];
    const runtime = skillCatalogState.runtime || {};
    const policy = skillCatalogState.policy || {};
    const visible = visibleSkills();
    const focusedSkill = skillCatalogState.focusPathOnRender ? skillCatalogState.items[skillCatalogState.focusPathOnRender] : null;
    if (focusedSkill) {
        skillCatalogState.collapsedSources[skillSourceKey(focusedSkill)] = false;
    }
    const blocks = skillSourceBlocks(visible);
    const starterPackItems = (runtime.starter_pack || {}).items || [];
    const showStarterPack = skillsInventoryIsDefaultView();

    let html = renderSkillFilterBar(skills.length);
    html += '<div class="card mb-16"><div class="card-body"><p class="text-sm text-secondary">Skills are grouped into source blocks by where they came from, like <span class="font-mono">Hermes Web UI</span> or a repo import. The category you type, like <span class="font-mono">test</span>, appears on each skill card and in the category filter. <span class="font-mono">Enabled</span> means Hermes can consider a skill on CLI turns. <span class="font-mono">Needs Setup</span> opens exact blockers plus shortcuts to the right setup flow.</p></div></div>';
    html += '<div class="skills-source-grid">';
    if (showStarterPack) {
        html += renderStarterPackBlock(starterPackItems);
    }

    if (!skills.length) {
        html += '<div class="empty-state"><div class="empty-icon">' + UI_ICONS.books + '</div><h3>No Skills Found</h3><p>Skills directory is empty or no <span class="font-mono">SKILL.md</span> files were discovered.</p></div>';
        html += '</div>';
        finishSkillsInventoryRender(content, html, viewState);
        return;
    }

    if (!visible.length) {
        html += '<div class="empty-state"><div class="empty-icon">' + UI_ICONS.books + '</div><h3>No Matching Skills</h3><p>Try clearing filters or searching for a different skill name.</p></div>';
        html += '</div>';
        finishSkillsInventoryRender(content, html, viewState);
        return;
    }

    html += blocks.map(renderSkillSourceBlock).join('') + '</div>';
    finishSkillsInventoryRender(content, html, viewState);
}

window.setSkillSearch = function (value) {
    skillCatalogState.searchQuery = String(value || '');
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.setSkillStatusFilter = function (value) {
    skillCatalogState.statusFilter = value || 'all';
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.setSkillSourceFilter = function (value) {
    skillCatalogState.sourceFilter = value || 'all';
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.setSkillCategoryFilter = function (value) {
    skillCatalogState.categoryFilter = value || 'all';
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.clearSkillFilters = function () {
    skillCatalogState.searchQuery = '';
    skillCatalogState.statusFilter = 'all';
    skillCatalogState.sourceFilter = 'all';
    skillCatalogState.categoryFilter = 'all';
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.clearSelectedSkills = function () {
    clearSelectedSkillsState();
    renderSkillsInventory();
};

window.openCreatedSkillInventory = function (path) {
    const skillPath = String(path || '').trim();
    if (skillPath) {
        skillCatalogState.recentPaths[skillPath] = true;
        skillCatalogState.focusPathOnRender = skillPath;
        const existing = skillCatalogState.items[skillPath];
        if (existing) {
            skillCatalogState.collapsedSources[skillSourceKey(existing)] = false;
        }
    }
    skillCatalogState.searchQuery = '';
    skillCatalogState.statusFilter = 'all';
    skillCatalogState.sourceFilter = 'all';
    skillCatalogState.categoryFilter = 'all';
    clearSelectedSkillsState();
    navigate('skills');
};

window.openCreatedIntegrationInventory = function (name) {
    const integrationName = String(name || '').trim();
    if (integrationName) {
        integrationCatalogState.recentNames[integrationName] = true;
        integrationCatalogState.focusNameOnRender = integrationName;
    }
    navigate('channels');
};

window.openCreatedAgentInventory = function (name) {
    const presetName = String(name || '').trim();
    if (presetName) {
        agentCatalogState.recentNames[presetName] = true;
        agentCatalogState.focusNameOnRender = presetName;
    }
    navigate('agents');
};

window.toggleSkillSelection = function (path, checked) {
    if (!path) return;
    const skill = skillCatalogState.items[path];
    const sourceKey = skillSourceKey(skill || {});
    if (checked) {
        activateSelectionSource(sourceKey);
        skillCatalogState.selectedPaths[path] = true;
    } else {
        delete skillCatalogState.selectedPaths[path];
        if (!selectedSkillPaths().length) {
            skillCatalogState.selectedSourceKey = '';
        }
    }
    renderSkillsInventory();
};

window.toggleSkillSourceSelection = function (sourceKey, checked) {
    const paths = visibleSkillsForSource(sourceKey).map(skill => skill.path);
    if (checked) {
        activateSelectionSource(sourceKey);
        paths.forEach(path => {
            skillCatalogState.selectedPaths[path] = true;
        });
    } else {
        paths.forEach(path => {
            delete skillCatalogState.selectedPaths[path];
        });
        if (!selectedSkillPaths().length) {
            skillCatalogState.selectedSourceKey = '';
        }
    }
    renderSkillsInventory();
};

window.clearSkillSourceSelection = function (sourceKey) {
    selectedSkillPathsForSource(sourceKey).forEach(path => {
        delete skillCatalogState.selectedPaths[path];
    });
    if (!selectedSkillPaths().length) {
        skillCatalogState.selectedSourceKey = '';
    }
    renderSkillsInventory();
};

window.toggleSkillSourceCollapsed = function (sourceKey) {
    skillCatalogState.collapsedSources[sourceKey] = !(skillCatalogState.collapsedSources[sourceKey] === true);
    renderSkillsInventory();
};

window.toggleStarterPackCollapsed = function () {
    skillCatalogState.collapsedSources.__starter_pack__ = !starterPackCollapsed();
    renderSkillsInventory();
};

window.showSkillSourceDetails = function (path) {
    const skill = skillCatalogState.items[path];
    if (!skill) {
        toast('Skill details not found', 'error');
        return;
    }
    const source = skill.source || {};
    let html = '<div class="card mb-16"><div class="card-header"><span>Installed From</span></div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">Display</label><div class="font-mono text-sm">' + escH(skillSourceLabel(skill)) + '</div></div>';
    html += '<div class="form-group"><label class="form-label">Category</label><div class="text-sm">' + escH(skillCategoryLabel(skill)) + '</div></div>';
    if (source.identifier) {
        html += '<div class="form-group"><label class="form-label">Identifier</label><div class="font-mono text-sm">' + escH(source.identifier) + '</div></div>';
    }
    if (source.source_repo) {
        html += '<div class="form-group"><label class="form-label">Repo</label><div class="font-mono text-sm">' + escH(source.source_repo) + '</div></div>';
    }
    if (source.source_path) {
        html += '<div class="form-group"><label class="form-label">Repo Path</label><div class="font-mono text-sm">' + escH(source.source_path) + '</div></div>';
    }
    if (source.catalog_source) {
        html += '<div class="form-group"><label class="form-label">Catalog</label><div class="text-sm">' + escH(source.catalog_source) + '</div></div>';
    }
    html += '<div class="form-group"><label class="form-label">Install Mode</label><div class="text-sm">' + escH(source.install_mode || 'manual/local') + '</div></div>';
    if (source.recorded_at) {
        html += '<div class="form-group"><label class="form-label">Recorded</label><div class="text-sm">' + escH(source.recorded_at) + '</div></div>';
    }
    html += '</div></div>';
    showModal(
        'Skill Source: ' + (skill.name || skill.path),
        html,
        '<button class="btn" onclick="closeModal()">Close</button>'
    );
};

async function openEnvVarSetup(key, groupHint = '') {
    const data = await api('GET', '/api/env');
    envVarsState.vars = data.vars || {};
    envVarsState.groups = data.groups || {};
    envVarsState.metadata = data.metadata || {};
    envVarsState.presets = data.presets || {};
    envVarsState.groupHelp = data.group_help || {};
    const meta = envVarMeta(key);
    envVarsState.activeGroup = groupHint || meta.group || 'Provider';
    closeModal();
    if (Object.prototype.hasOwnProperty.call(envVarsState.vars || {}, key)) {
        editEnvVar(key);
    } else {
        addEnvVar(envVarsState.activeGroup, key);
    }
}

window.runSkillSetupAction = async function (path, index) {
    const skill = skillCatalogState.items[path];
    const setup = skillSetupStatus(skill);
    const action = setup.actions[index];
    if (!action) {
        toast('Setup action not found', 'error');
        return;
    }
    try {
        if (action.type === 'env_var') {
            await openEnvVarSetup(action.key, action.group || '');
            return;
        }
        if (action.type === 'screen') {
            closeModal();
            navigate(action.screen);
            return;
        }
        toast('Unsupported setup action', 'warning');
    } catch (e) {
        toast('Setup action failed: ' + e.message, 'error');
    }
};

window.showSkillSetupDetails = function (path) {
    const skill = skillCatalogState.items[path];
    if (!skill) {
        toast('Skill details not found', 'error');
        return;
    }
    const setup = skillSetupStatus(skill);
    let html = '<p class="text-sm text-secondary mb-16">' + escH(skill.name || skill.path || 'Skill') + '</p>';
    if (setup.blockers.length) {
        html += '<div class="card mb-16"><div class="card-header"><span>Still Needed</span></div><div class="card-body"><div class="skill-setup-blocker-list">';
        setup.blockers.forEach(blocker => {
            html += '<div class="skill-setup-blocker">';
            html += '<div class="skill-setup-blocker-title">' + escH(blocker.message || 'Setup needed') + '</div>';
            if (blocker.kind === 'env_var') {
                html += '<div class="skill-setup-blocker-detail">Group: ' + escH(blocker.group || 'System') + '</div>';
            } else if (blocker.kind === 'credential_file') {
                html += '<div class="skill-setup-blocker-detail">Expected at <span class="font-mono">' + escH(blocker.absolute_path || blocker.path || '') + '</span></div>';
            } else if (blocker.kind === 'command') {
                html += '<div class="skill-setup-blocker-detail">Install or expose <span class="font-mono">' + escH(blocker.name || '') + '</span> in your PATH before using this skill.</div>';
            }
            html += '</div>';
        });
        html += '</div></div></div>';
    } else {
        html += '<div class="empty-state"><p>No declared setup blockers were detected for this skill.</p></div>';
    }

    let footer = '<button class="btn" onclick="closeModal()">Close</button>';
    setup.actions.forEach((action, index) => {
        footer += '<button class="btn btn-primary" onclick="runSkillSetupAction(\'' + escA(path) + '\', ' + index + ')">' + escH(action.label || 'Set Up') + '</button>';
    });
    footer += '<button class="btn" onclick="showSkillSourceDetails(\'' + escA(path) + '\')">View Source</button>';
    footer += '<button class="btn" onclick="closeModal(); navigate(\'env-vars\')">Open Env Vars</button>';
    footer += '<button class="btn" onclick="closeModal(); navigate(\'channels\')">Open Apps & Integrations</button>';

    showModal(
        'Skill Setup: ' + (skill.name || skill.path),
        html,
        footer
    );
};

function showSkillInstallResult(identifier, result) {
    const installed = Array.isArray(result.installed_skill_paths) ? result.installed_skill_paths : [];
    const alreadyPresent = Array.isArray(result.already_present_paths) ? result.already_present_paths : [];
    const total = installed.length + alreadyPresent.length;
    if (!total && result.install_mode !== 'github_repo') {
        return false;
    }

    skillCatalogState.recentPaths = {};
    installed.forEach(path => {
        skillCatalogState.recentPaths[path] = true;
    });

    let html = '<p class="text-sm text-secondary mb-16">Install target: <span class="font-mono">' + escH(identifier) + '</span></p>';
    if (result.install_mode === 'github_repo' && result.fallback && result.fallback.source) {
        html += '<div class="card mb-16"><div class="card-header"><span>Repo Import</span></div><div class="card-body">';
        html += '<p class="text-sm text-secondary">Imported every <span class="font-mono">SKILL.md</span> folder discovered in <span class="font-mono">' + escH(result.fallback.source) + '</span>.</p>';
        html += '</div></div>';
    }
    if (installed.length) {
        html += '<div class="card mb-16"><div class="card-header"><span>Installed Now</span><span class="badge badge-success">' + installed.length + '</span></div><div class="card-body">' +
            renderSkillInstallPathList(installed) + '</div></div>';
    }
    if (alreadyPresent.length) {
        html += '<div class="card mb-16"><div class="card-header"><span>Already Present</span><span class="badge badge-info">' + alreadyPresent.length + '</span></div><div class="card-body">' +
            renderSkillInstallPathList(alreadyPresent) + '</div></div>';
    }
    if (result.output) {
        html += '<div class="card"><div class="card-header"><span>Installer Output</span></div><div class="card-body"><pre class="font-mono text-xs" style="max-height:260px;overflow:auto">' + escH(result.output) + '</pre></div></div>';
    }
    showModal(
        'Installed Skills',
        html,
        '<button class="btn btn-primary" onclick="closeModal()">Close</button>'
    );
    return true;
}

function describeSkillActionPaths(paths) {
    const list = Array.isArray(paths) ? paths.filter(Boolean) : [];
    const sources = Array.from(new Set(list.map(path => skillSourceLabel(skillCatalogState.items[path] || {})))).filter(Boolean);
    return {
        count: list.length,
        sources,
    };
}

function confirmSkillBulkAction(action, paths, scopeLabel) {
    const summary = describeSkillActionPaths(paths);
    let html = '<p class="text-sm text-secondary mb-16">This will remove ' + summary.count + ' skill' + (summary.count === 1 ? '' : 's') + ' from Hermes.</p>';
    if (scopeLabel) {
        html += '<p class="text-sm text-secondary mb-16">Scope: ' + escH(scopeLabel) + '</p>';
    }
    if (summary.sources.length) {
        html += '<div class="card"><div class="card-header"><span>Sources Affected</span></div><div class="card-body"><ul class="plain-list">' +
            summary.sources.map(source => '<li>' + escH(source) + '</li>').join('') +
            '</ul></div></div>';
    }
    showModal(
        'Remove Skills',
        html,
        '<button class="btn" onclick="closeModal()">Cancel</button>' +
        '<button class="btn btn-danger" onclick="confirmSkillBulkActionRun(\'' + escA(action) + '\')">Remove from Hermes</button>'
    );
    window.__pendingSkillBulkAction = { action, paths };
}

window.confirmSkillBulkActionRun = async function () {
    const pending = window.__pendingSkillBulkAction || {};
    if (!pending.action || !Array.isArray(pending.paths)) {
        toast('No pending skill action', 'error');
        return;
    }
    closeModal();
    await runSkillBulkAction(pending.action, pending.paths, '', true);
    window.__pendingSkillBulkAction = null;
};

async function runSkillBulkAction(action, paths, scopeLabel = '', skipConfirm = false) {
    const uniquePaths = Array.from(new Set((Array.isArray(paths) ? paths : []).filter(Boolean)));
    if (!uniquePaths.length) {
        toast('No skills selected', 'warning');
        return;
    }
    if (action === 'remove' && !skipConfirm) {
        confirmSkillBulkAction(action, uniquePaths, scopeLabel);
        return;
    }
    toast((action.charAt(0).toUpperCase() + action.slice(1)) + ' ' + uniquePaths.length + ' skill' + (uniquePaths.length === 1 ? '' : 's') + '...', 'info', 2000);
    try {
        const resp = await api('POST', '/api/skills/bulk', { action, paths: uniquePaths });
        rememberSkills(resp.skills || []);
        clearSelectedSkillsState();
        renderSkillsInventory();
        toast((resp.changed_count || 0) + ' skill' + ((resp.changed_count || 0) === 1 ? '' : 's') + ' updated', 'success');
    } catch (e) {
        toast('Bulk action failed: ' + e.message, 'error');
    }
}

window.runSelectedSkillAction = async function (action, sourceKey = '', scopeLabel = '') {
    const paths = sourceKey ? selectedSkillPathsForSource(sourceKey) : selectedSkillPaths();
    await runSkillBulkAction(action, paths, scopeLabel || 'Selected skills');
};

function renderStarterPackGrid(items) {
    const list = Array.isArray(items) ? items : [];
    starterPackState.items = {};
    list.forEach(item => {
        if (item && item.id) starterPackState.items[item.id] = item;
    });
    if (!list.length) {
        return '<div class="empty-state"><p>All recommended starter skills are ready right now. Manage the installed ones in their source blocks below.</p></div>';
    }
    return '<div class="starter-pack-grid">' + list.map(item =>
        '<div class="starter-pack-item">' +
            '<div class="starter-pack-item-top">' +
                '<div class="starter-pack-item-title">' + escH(item.label || item.id || 'Item') + '</div>' +
                '<span class="badge ' + starterPackBadgeClass(item.status) + '">' + escH(starterPackBadgeLabel(item.status)) + '</span>' +
            '</div>' +
            '<div class="starter-pack-item-kind">' + escH((item.kind || 'runtime').replace(/_/g, ' ')) + '</div>' +
            '<div class="starter-pack-item-detail">' + escH(item.detail || '') + '</div>' +
            '<div class="starter-pack-item-actions skill-card-actions">' +
                (starterPackSelectablePaths(item).length
                    ? '<button class="btn btn-sm" onclick="starterPackShowSource(\'' + escA(item.id) + '\')">' + escH(starterPackSelectablePaths(item).length === 1 ? 'View Source' : 'View Sources') + '</button>'
                    : '') +
                (starterPackPrimaryAction(item)) +
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

function starterPackPrimaryAction(item) {
    if (!item) return '';
    const setupAction = item.setup_action || {};
    const matchedSkillPaths = Array.isArray(item.matched_skill_paths) ? item.matched_skill_paths : [];
    const hasSetupFlow = !!(matchedSkillPaths.length || (setupAction && setupAction.type) || (Array.isArray(item.setup_actions) && item.setup_actions.length));
    if (!hasSetupFlow) {
        return '';
    }
    if (item.kind === 'skill' && item.status === 'ready') {
        return '';
    }
    const label = item.status === 'attention'
        ? 'Needs Setup'
        : ((setupAction || {}).label || (item.status === 'missing' ? 'Preview Setup' : 'Set Up'));
    const btnClass = item.status === 'attention' ? 'btn btn-sm btn-warning' : 'btn btn-sm btn-primary';
    return '<button class="' + btnClass + '" onclick="starterPackRunSetup(\'' + escA(item.id) + '\')">' + escH(label) + '</button>';
}

window.showStarterPackDetails = function () {
    const items = Object.values(starterPackState.items || {});
    let html = '<p class="text-sm text-secondary mb-16">Starter Pack keeps recommended starter skills that are still missing or still need setup in one place.</p>';
    html += '<div class="card mb-16"><div class="card-header"><span>How This Block Works</span></div><div class="card-body"><ul class="plain-list">';
    html += '<li>Starter Pack only shows starter skills that are missing or still need setup.</li>';
    html += '<li>Ready starter skills disappear from this block and stay manageable in their source blocks below.</li>';
    html += '<li>Runtime memory and chat apps live outside this block so it stays about real Hermes skills only.</li>';
    html += '</ul></div></div>';
    html += '<div class="card"><div class="card-header"><span>Summary</span></div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">Items</label><div class="text-sm">' + escH(String(items.length)) + '</div></div>';
    html += '<div class="form-group"><label class="form-label">Needs Setup</label><div class="text-sm">' + escH(String(items.filter(item => item && item.status === 'attention').length)) + '</div></div>';
    html += '<div class="form-group"><label class="form-label">Missing</label><div class="text-sm">' + escH(String(items.filter(item => item && item.status === 'missing').length)) + '</div></div>';
    html += '</div></div>';
    showModal('Starter Pack Details', html, '<button class="btn btn-primary" onclick="closeModal()">Close</button>');
};

window.starterPackShowSource = function (itemId) {
    const item = starterPackState.items[itemId];
    const paths = starterPackSelectablePaths(item);
    if (!paths.length) {
        toast('No starter-pack skill sources found', 'warning');
        return;
    }
    if (paths.length === 1) {
        showSkillSourceDetails(paths[0]);
        return;
    }
    let html = '<p class="text-sm text-secondary mb-16">This starter-pack item is backed by multiple installed skills.</p>';
    html += '<div class="card"><div class="card-header"><span>Matched Skills</span></div><div class="card-body"><div class="starter-pack-candidate-list">';
    paths.forEach(path => {
        const skill = skillCatalogState.items[path] || {};
        html += '<div class="starter-pack-candidate"><div class="starter-pack-candidate-top"><div><div class="font-mono text-sm">' + escH(path) + '</div><div class="text-xs text-secondary mt-8">' + escH(skill.description || 'Installed skill') + '</div></div></div><div class="starter-pack-candidate-actions"><button class="btn btn-sm" onclick="closeModal(); showSkillSourceDetails(\'' + escA(path) + '\')">View Source</button></div></div>';
    });
    html += '</div></div></div>';
    showModal(item?.label || 'Starter Pack Sources', html, '<button class="btn btn-primary" onclick="closeModal()">Close</button>');
};

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
        (starterPackPrimaryAction(item) ? starterPackPrimaryAction(item).replace('btn btn-sm', 'btn') : '') +
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
    const matchedSkillPaths = Array.isArray(item.matched_skill_paths) ? item.matched_skill_paths : [];
    if (matchedSkillPaths.length && skillCatalogState.items[matchedSkillPaths[0]]) {
        closeModal();
        showSkillSetupDetails(matchedSkillPaths[0]);
        return;
    }
    const setupActions = Array.isArray(item.setup_actions) ? item.setup_actions : [];
    const skillSetupAction = setupActions.find(action => action && action.type === 'skill_setup' && action.path && skillCatalogState.items[action.path]);
    if (skillSetupAction) {
        closeModal();
        showSkillSetupDetails(skillSetupAction.path);
        return;
    }
    const action = item.setup_action || {};
    if (action.type === 'screen' && action.screen) {
        closeModal();
        navigate(action.screen);
        return;
    }
    if (action.type !== 'env_var' || !action.key) {
        starterPackShowNotes(itemId);
        return;
    }
    closeModal();
    if (action.key === 'OPENAI_API_KEY') {
        if (action.mode === 'edit' || item.status === 'ready') {
            editEnvVar('OPENAI_API_KEY');
            return;
        }
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
        const installed = Array.isArray(resp.installed_skill_paths) ? resp.installed_skill_paths : [];
        const alreadyPresent = Array.isArray(resp.already_present_paths) ? resp.already_present_paths : [];
        if (installed.length) {
            html += '<div class="card mb-16"><div class="card-header"><span>Installed Now</span><span class="badge badge-success">' + installed.length + '</span></div><div class="card-body">' +
                renderSkillInstallPathList(installed) + '</div></div>';
        }
        if (alreadyPresent.length) {
            html += '<div class="card mb-16"><div class="card-header"><span>Already Present</span><span class="badge badge-info">' + alreadyPresent.length + '</span></div><div class="card-body">' +
                renderSkillInstallPathList(alreadyPresent) + '</div></div>';
        }
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
    html += '<div class="runtime-readiness-item"><div class="runtime-readiness-label">Apps & Integrations</div><div class="runtime-readiness-value">' + escH(String(integrations.configured_count || 0)) + ' configured</div><div class="runtime-readiness-detail">' + escH(
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
        rememberSkills(data.skills || []);
        skillCatalogState.runtime = status.runtime || {};
        skillCatalogState.policy = status.transport_policy || {};
        if (!screenIsActive('skills')) return;
        renderSkillsInventory();
    } catch (e) {
        if (!screenIsActive('skills')) return;
        content.innerHTML = '<div class="empty-state"><div class="empty-icon">\u26a0\ufe0f</div><h3>Error</h3><p>' + escH(e.message) + '</p></div>';
    }
};

window.toggleSkill = async function (path, label) {
    const skillLabel = label || path;
    toast('Toggling ' + skillLabel + '...', 'info', 1500);
    try {
        const encodedPath = String(path || '').split('/').map(part => encodeURIComponent(part)).join('/');
        const r = await api('POST', '/api/skills/' + encodedPath + '/toggle');
        toast(skillLabel + ': ' + (r.enabled ? 'Enabled' : 'Disabled'), 'success');
        await Screens.skills();
    } catch (e) { toast('Error: ' + e.message, 'error'); }
};

window.openSkillInstallModal = function () {
    showModal(
        'Install Skill',
        '<p class="text-sm text-secondary mb-12">Paste a Hermes skill identifier exactly the way you would use it in the CLI.</p>' +
        '<p class="text-sm text-secondary mb-16">Use <span class="font-mono">skills-sh/steipete/clawdis/weather</span> for a single registry skill, or <span class="font-mono">wondelai/skills</span> to import every skill folder discovered in that GitHub repo.</p>' +
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
        const result = await api('POST', '/api/skills/install', { identifier });
        closeModal();
        const openedResult = showSkillInstallResult(identifier, result);
        const installedCount = (result.installed_skill_paths || []).length;
        const skippedCount = (result.already_present_paths || []).length;
        if (result.install_mode === 'github_repo' && result.fallback) {
            const summary = [];
            if (installedCount) summary.push(installedCount + ' installed');
            if (skippedCount) summary.push(skippedCount + ' already present');
            toast('Imported from GitHub: ' + identifier + (summary.length ? ' (' + summary.join(', ') + ')' : ''), 'success', 5000);
        } else if (!openedResult) {
            toast('Skill installed: ' + identifier, 'success');
        }
        await Screens.skills();
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
        if (!screenIsActive('channels')) return;
        const channels = data.integrations || data.channels || [];
        const focusIntegrationName = integrationCatalogState.focusNameOnRender || '';
        const orderedChannels = channels.slice().sort((a, b) => {
            if (focusIntegrationName && a.name === focusIntegrationName && b.name !== focusIntegrationName) return -1;
            if (focusIntegrationName && b.name === focusIntegrationName && a.name !== focusIntegrationName) return 1;
            return 0;
        });
        const configuredNames = orderedChannels.filter(item => item && item.configured).map(item => item.label || item.name).filter(Boolean);
        const runtime = status.runtime || {};
        const policy = status.transport_policy || {};
        let html = renderRuntimeReadinessCard(runtime, policy);
        html += '<div class="card mb-16"><div class="card-body">';
        html += '<p class="text-sm text-secondary mb-16">This screen edits Hermes app and integration config directly. Top-level sections like <span class="font-mono">discord</span>, <span class="font-mono">whatsapp</span>, and <span class="font-mono">webhook</span> appear here. The separate <span class="font-mono">Raw Hooks</span> screen only edits the low-level <span class="font-mono">hooks</span> block.</p>';
        if (configuredNames.length) {
            html += '<div class="text-sm text-secondary mb-16">Configured now: ' + configuredNames.map(name => '<span class="badge badge-success">' + escH(name) + '</span>').join(' ') + '</div>';
        }
        html += '<div style="display:flex;gap:8px;flex-wrap:wrap">';
        html += '<button class="btn btn-primary" onclick="openCreateCapability(\'integration\')">Create Integration</button>';
        html += '<button class="btn" onclick="navigate(\'hooks\')">Open Raw Hooks</button>';
        html += '<button class="btn" onclick="navigate(\'skills\')">Open Skills</button>';
        html += '<button class="btn" onclick="navigate(\'env-vars\')">Open Env Vars</button>';
        html += '</div></div></div>';
        if (channels.length === 0) {
            html += '<div class="empty-state"><div class="empty-icon">' + UI_ICONS.speechBubble + '</div><h3>No Apps or Integrations</h3><p>No Discord, WhatsApp, Slack, Telegram, Matrix, or webhook integrations were found in your Hermes config.</p></div>';
            content.innerHTML = html;
            return;
        }
        orderedChannels.forEach(ch => {
            const configured = !!ch.configured;
            const kindLabel = ch.kind === 'legacy_channel' ? 'Legacy Channel' : 'Integration';
            const recent = !!integrationCatalogState.recentNames[ch.name || ''];
            html += '<div class="card mb-16" id="' + escA(integrationElementId(ch.name || '')) + '"><div class="card-header"><span>' + escH(ch.label || ch.name) + '</span><div style="display:flex;gap:8px;flex-wrap:wrap"><span class="badge ' + (configured ? 'badge-success' : 'badge-secondary') + '">' + (configured ? 'Configured' : 'Empty') + '</span><span class="badge badge-info">' + escH(kindLabel) + '</span>' + (recent ? '<span class="badge badge-success">New</span>' : '') + '</div></div>';
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
        if (focusIntegrationName) {
            integrationCatalogState.focusNameOnRender = '';
            requestAnimationFrame(() => focusInventoryCardById(integrationElementId(focusIntegrationName)));
        }
    } catch (e) {
        if (!screenIsActive('channels')) return;
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
        let intro = '<p class="text-sm text-secondary mb-16">This screen edits the raw <span class="font-mono">hooks</span> block in Hermes config. It is separate from the <span class="font-mono">Webhook</span> integration card on <span class="font-mono">Apps & Integrations</span>. The web UI does not execute hooks by itself; whether they run depends on your Hermes runtime.</p>';
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
        content.innerHTML = recommended + '<div class="card"><div class="card-header"><span>Raw Hooks</span></div><div class="card-body">' + intro + fields + '<button class="btn btn-primary mt-16" onclick="saveHooks()">Save Hooks</button></div></div>';
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
    activeProfile: '',
    currentSessionProfile: '',
    draftProfile: '',
    availableProfiles: [],
    currentSegments: [],
    currentActiveSegmentId: '',
    currentActiveSegmentIndex: 1,
    historyProfileFilter: 'all',
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
    transportPreference: localStorage.getItem('hermes-transport-preference') || 'auto',
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
    lastRequestErrorNotice: '',
    requestProgressPoll: null,
    persistDebugTrace: false,
    lastProgressLines: [],
    lastProgressTransport: '',
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
    if (chatState.requestProgressPoll) {
        clearInterval(chatState.requestProgressPoll);
        chatState.requestProgressPoll = null;
    }
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

function chatBindMessagesScroll(container) {
    if (!container || container.dataset.scrollBound === 'true') return;
    container.dataset.scrollBound = 'true';
    container.dataset.autoScroll = 'true';
    container.addEventListener('scroll', () => {
        const isNearBottom = (container.scrollHeight - container.clientHeight - container.scrollTop) < 8;
        container.dataset.autoScroll = isNearBottom ? 'true' : 'false';
    });
}

function chatShouldAutoScroll(container) {
    if (!container) return false;
    chatBindMessagesScroll(container);
    return container.dataset.autoScroll !== 'false';
}

function chatBindProgressLog(panel) {
    if (!panel || panel.dataset.scrollBound === 'true') return;
    panel.dataset.scrollBound = 'true';
    panel.dataset.autoScroll = 'true';
    panel.addEventListener('scroll', () => {
        const isNearBottom = (panel.scrollHeight - panel.clientHeight - panel.scrollTop) < 8;
        panel.dataset.autoScroll = isNearBottom ? 'true' : 'false';
    });
}

function chatRenderLogPanel(panel, lines = [], transport = '') {
    if (!panel) return;
    chatBindProgressLog(panel);
    const shouldAutoScroll = panel.dataset.autoScroll !== 'false';
    const rows = Array.isArray(lines) ? lines.filter(Boolean) : [];
    if (!rows.length) {
        panel.innerHTML = '<div class="chat-progress-empty">' + escH(
            transport === 'cli'
                ? 'Waiting for Hermes CLI activity...'
                : 'Live tool activity is only available for Hermes CLI transport.'
        ) + '</div>';
        return;
    }
    panel.innerHTML = rows.map(line => '<div class="chat-progress-line">' + escH(line) + '</div>').join('');
    if (shouldAutoScroll) {
        panel.scrollTop = panel.scrollHeight;
    }
}

function chatSetDebugTraceStatus(label) {
    chatState.lastProgressStatus = label || 'Idle';
}

function chatRenderProgressLines(lines = [], transport = '') {
    const panel = document.getElementById('chat-progress-log');
    chatState.lastProgressLines = Array.isArray(lines) ? lines.filter(Boolean) : [];
    chatState.lastProgressTransport = transport || '';
    chatRenderLogPanel(panel, lines, transport);
    chatSetDebugTraceStatus(chatState.isThinking ? 'Running' : 'Updated');
}

function chatRenderProgressError(message) {
    const panel = document.getElementById('chat-progress-log');
    if (panel) {
        chatBindProgressLog(panel);
        panel.innerHTML = '<div class="chat-progress-error">' + escH(message || 'Live progress is temporarily unavailable.') + '</div>';
    }
    chatState.lastProgressLines = [message || 'Live progress is temporarily unavailable.'];
    chatState.lastProgressTransport = 'cli';
    chatSetDebugTraceStatus('Error');
}

function chatRenderPersistentProgressBubble(statusLabel) {
    if (!chatState.persistDebugTrace) return;
    const container = document.getElementById('chat-messages');
    if (!container) return;
    const existing = document.getElementById('chat-persistent-progress');
    if (existing) existing.remove();
    const bubble = document.createElement('div');
    bubble.id = 'chat-persistent-progress';
    bubble.className = 'chat-thinking chat-persistent-progress';
    bubble.innerHTML = '<div class="chat-thinking-bubble"><div class="chat-thinking-header"><span class="chat-thinking-icon"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span><span class="chat-thinking-text">Debug trace (' + escH(statusLabel || chatState.lastProgressStatus || 'Completed') + ')</span></div><div class="chat-progress-log" id="chat-persistent-progress-log"></div></div>';
    container.appendChild(bubble);
    chatRenderLogPanel(document.getElementById('chat-persistent-progress-log'), chatState.lastProgressLines || [], chatState.lastProgressTransport || '');
}

async function chatFetchRequestProgress(requestId) {
    if (!requestId || chatState.currentRequestId !== requestId) return;
    try {
        const resp = await authFetch('/api/chat/status?request_id=' + encodeURIComponent(requestId) + '&_ts=' + Date.now(), {
            method: 'GET',
            cache: 'no-store',
            headers: {
                'Cache-Control': 'no-store, no-cache, max-age=0',
                'Pragma': 'no-cache',
            },
        });
        const progress = await resp.json();
        if (chatState.currentRequestId !== requestId) return;
        chatRenderProgressLines(progress.progress_lines || [], progress.transport || '');
        if (progress.status && progress.status !== 'running' && progress.status !== 'cancel_requested') {
            if (chatState.requestProgressPoll) {
                clearInterval(chatState.requestProgressPoll);
                chatState.requestProgressPoll = null;
            }
        }
    } catch (e) {
        if (chatState.currentRequestId === requestId) {
            chatRenderProgressError('Live CLI activity could not be loaded: ' + (e.message || 'request failed'));
        }
        if (chatState.requestProgressPoll) {
            clearInterval(chatState.requestProgressPoll);
            chatState.requestProgressPoll = null;
        }
    }
}

function chatStartRequestProgress(requestId, expectedTransport) {
    if (chatState.requestProgressPoll) {
        clearInterval(chatState.requestProgressPoll);
        chatState.requestProgressPoll = null;
    }
    chatState.lastProgressLines = [];
    chatState.lastProgressTransport = expectedTransport || '';
    chatSetDebugTraceStatus('Running');
    const persisted = document.getElementById('chat-persistent-progress');
    if (persisted) persisted.remove();
    chatRenderProgressLines([], expectedTransport || '');
    chatFetchRequestProgress(requestId);
    chatState.requestProgressPoll = window.setInterval(() => {
        chatFetchRequestProgress(requestId);
    }, 900);
}

function chatFormatNoticeTimestamp(date = new Date()) {
    try {
        return date.toLocaleTimeString();
    } catch (e) {
        return date.toISOString();
    }
}

function chatSetRequestErrorNotice(message) {
    chatState.lastRequestErrorNotice = String(message || '').trim();
    chatRenderSessionBanner();
}

function chatClearRequestErrorNotice() {
    if (!chatState.lastRequestErrorNotice) return;
    chatState.lastRequestErrorNotice = '';
    chatRenderSessionBanner();
}

function chatApplySessionMetadata(meta = null) {
    chatState.lastRequestErrorNotice = '';
    const session = meta || {};
    const sessionSegments = Array.isArray(session.segments) ? session.segments.slice() : [];
    const activeSegmentId = session.active_segment_id || '';
    const activeSegment = sessionSegments.find(segment => segment.id === activeSegmentId)
        || sessionSegments[sessionSegments.length - 1]
        || null;
    const resolvedSessionProfile = activeSegment?.profile || session.profile || '';
    if (resolvedSessionProfile) {
        chatState.currentSessionProfile = resolvedSessionProfile;
        chatState.draftProfile = '';
    } else if (chatState.currentSessionId) {
        chatState.currentSessionProfile = chatState.activeProfile || '';
        chatState.draftProfile = '';
    } else {
        chatState.currentSessionProfile = '';
    }
    chatState.currentSegments = sessionSegments;
    chatState.currentActiveSegmentId = activeSegmentId;
    chatState.currentActiveSegmentIndex = Number(session.active_segment_index || 1) || 1;
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

function chatVisibleProfile() {
    return chatState.currentSessionProfile || chatState.draftProfile || chatState.activeProfile || 'default';
}

function chatCurrentSegment() {
    return (chatState.currentSegments || []).find(segment => segment.id === chatState.currentActiveSegmentId)
        || chatState.currentSegments[chatState.currentSegments.length - 1]
        || null;
}

async function chatLoadAvailableProfiles() {
    try {
        const data = await loadRuntimeProfiles();
        chatState.availableProfiles = Array.isArray(data?.profiles) ? data.profiles.slice() : [];
    } catch (e) {
        chatState.availableProfiles = [];
    }
    chatRenderTransportControls();
}

function chatTransportLabel(value) {
    if (value === 'cli') return 'CLI';
    if (value === 'api') return 'API';
    return 'Auto';
}

function chatSegmentToneClass(profile) {
    const normalized = String(profile || '').trim().toLowerCase();
    if (!normalized || normalized === 'default') return 'chat-segment-tone-blue';
    if (normalized === 'leire') return 'chat-segment-tone-green';
    return 'chat-segment-tone-green';
}

function chatMessageToneClass(profile) {
    const normalized = String(profile || '').trim().toLowerCase();
    if (!normalized || normalized === 'default') return 'chat-msg-tone-blue';
    if (normalized === 'leire') return 'chat-msg-tone-green';
    return 'chat-msg-tone-green';
}

function chatProfileTextToneClass(profiles) {
    const names = Array.isArray(profiles) ? profiles.map(value => String(value || '').trim().toLowerCase()).filter(Boolean) : [];
    if (!names.length) return 'sidebar-chat-profile-badge-blue';
    if (names.length === 1 && names[0] === 'default') return 'sidebar-chat-profile-badge-blue';
    return 'sidebar-chat-profile-badge-green';
}

function chatBuildSegmentNode(message, segment) {
    const div = document.createElement('div');
    const profile = message?.profile || segment?.profile || chatVisibleProfile();
    const transport = message?.transport || segment?.transport || '';
    const continuityReady = transport === 'cli' && !!segment?.hermes_session_id;
    div.className = 'chat-segment-marker ' + chatSegmentToneClass(profile);
    const chips = [
        '<span class="badge badge-info">Profile: ' + escH(profile || 'default') + '</span>',
    ];
    if (transport) {
        chips.push('<span class="badge">Transport: ' + escH(chatTransportLabel(transport)) + '</span>');
    }
    if (continuityReady) {
        chips.push('<span class="badge badge-success">CLI continuity ready</span>');
    }
    div.innerHTML = '<div class="chat-segment-marker-line"></div><div class="chat-segment-marker-body"><div class="chat-segment-marker-title">Profile changed</div><div class="chat-segment-marker-badges">' + chips.join('') + '</div>' + (transport === 'cli' ? '<div class="chat-segment-marker-detail">' + escH(continuityReady ? 'This profile can resume its own Hermes CLI session when you return to it.' : 'This profile is starting a fresh Hermes CLI continuity path.') + '</div>' : '') + '</div><div class="chat-segment-marker-line"></div>';
    return div;
}

function updateChatHistoryActiveProfileBadge() {
    const badge = document.getElementById('chat-history-active-profile');
    if (!badge) return;
    badge.textContent = 'Portal: ' + (chatState.activeProfile || 'default');
}

function chatSessionProfile(session) {
    return String((session?.session || {}).profile || session?.profile || '').trim();
}

function chatSessionProfiles(session) {
    const names = [];
    const addName = (value) => {
        const name = String(value || '').trim();
        if (name && !names.includes(name)) names.push(name);
    };
    const segments = Array.isArray((session?.session || {}).segments) ? (session.session.segments || []) : [];
    segments.forEach(segment => addName(segment?.profile));
    addName(chatSessionProfile(session));
    return names;
}

function chatSessionProfilesLabel(session) {
    const profiles = chatSessionProfiles(session);
    if (!profiles.length) return '';
    if (profiles.length === 1) return 'Profile: ' + profiles[0];
    return 'Profiles: ' + profiles.join(', ');
}

function chatProfileNameToneClass(profile) {
    const normalized = String(profile || '').trim().toLowerCase();
    if (!normalized || normalized === 'default') return 'sidebar-chat-profile-name-blue';
    if (normalized === 'leire') return 'sidebar-chat-profile-name-green';
    return 'sidebar-chat-profile-name-green';
}

function chatSessionProfilesBadgeHtml(session) {
    const profiles = chatSessionProfiles(session);
    if (!profiles.length) return '';
    const prefix = profiles.length === 1 ? 'Profile:' : 'Profiles:';
    const namesHtml = profiles.map((profile, index) => {
        const separator = index ? '<span class="sidebar-chat-profile-separator">, </span>' : '';
        return separator + '<span class="sidebar-chat-profile-name ' + chatProfileNameToneClass(profile) + '">' + escH(profile) + '</span>';
    }).join('');
    const label = profiles.length === 1 ? 'Profile: ' + profiles[0] : 'Profiles: ' + profiles.join(', ');
    return '<span class="sidebar-chat-profile-badge" title="' + escA(label) + '"><span class="sidebar-chat-profile-prefix">' + escH(prefix) + '</span> ' + namesHtml + '</span>';
}

function chatFilteredProfileOptions(sessions = []) {
    const names = new Set();
    (chatState.availableProfiles || []).forEach(profile => {
        const name = String(profile?.name || '').trim();
        if (name) names.add(name);
    });
    sessions.forEach(session => {
        chatSessionProfiles(session).forEach(name => names.add(name));
    });
    if (chatState.activeProfile) names.add(chatState.activeProfile);
    return ['all', ...Array.from(names).sort()];
}

function chatSessionMatchesProfileFilter(session) {
    const filter = String(chatState.historyProfileFilter || 'all');
    if (!filter || filter === 'all') return true;
    return chatSessionProfiles(session).includes(filter);
}

function chatRenderHistoryProfileFilter(sessions = []) {
    const mounts = [
        document.getElementById('chat-history-filter-slot'),
        document.getElementById('sidebar-history-profile-filter-slot'),
    ].filter(Boolean);
    if (!mounts.length) return;
    const options = chatFilteredProfileOptions(sessions);
    if (!options.length || options.length === 1) {
        mounts.forEach(mount => { mount.innerHTML = ''; });
        return;
    }
    if (!options.includes(chatState.historyProfileFilter)) {
        chatState.historyProfileFilter = 'all';
    }
    const html =
        '<label class="chat-history-filter-label" for="chat-history-profile-filter">Filter</label>' +
        '<select id="chat-history-profile-filter" class="chat-history-filter-select" onchange="chatSetHistoryProfileFilter(this.value)">' +
        options.map(value => '<option value="' + escA(value) + '"' + (value === chatState.historyProfileFilter ? ' selected' : '') + '>' + escH(value === 'all' ? 'All profiles' : value) + '</option>').join('') +
        '</select>';
    mounts.forEach((mount, index) => {
        mount.innerHTML = html.replace(/chat-history-profile-filter/g, index === 0 ? 'chat-history-profile-filter' : 'sidebar-history-profile-filter');
    });
}

window.chatSetHistoryProfileFilter = function (value) {
    chatState.historyProfileFilter = value || 'all';
    chatLoadHistory();
};

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
    const availableProfiles = Array.isArray(chatState.availableProfiles) ? chatState.availableProfiles : [];
    const activeSegment = chatCurrentSegment();
    const profileOptions = availableProfiles.map(profile => {
        const name = profile?.name || 'default';
        return '<option value="' + escA(name) + '"' + (name === chatVisibleProfile() ? ' selected' : '') + '>' + escH(name) + '</option>';
    }).join('');
    const switchSummary = chatState.currentSessionId
        ? 'The next messages in this chat will use the selected profile after you apply it.'
        : 'Choose the profile you want to use before the next new chat turn.';
    mount.innerHTML =
        '<div class="chat-runtime-controls">' +
            '<div class="chat-transport-wrap">' +
                '<div class="chat-transport-label">Transport</div>' +
                '<div class="chat-transport-buttons">' + buttons + '</div>' +
            '</div>' +
            '<div class="chat-profile-wrap">' +
                '<div class="chat-transport-label">Profile</div>' +
                '<div class="chat-profile-controls">' +
                    '<select id="chat-profile-select" class="chat-profile-select" onchange="chatHandleProfileDraftChange()">' + profileOptions + '</select>' +
                    '<button class="chat-runtime-apply-btn" type="button" onclick="chatApplyRuntimeProfile()"' + (profileOptions ? '' : ' disabled') + '>Use Profile</button>' +
                '</div>' +
            '</div>' +
        '</div>' +
        '<div class="chat-transport-note">' + escH(note) + '</div>' +
        '<div class="chat-runtime-note">' +
            (activeSegment ? '<span class="badge">Current profile: ' + escH(activeSegment.profile || chatVisibleProfile()) + '</span> ' : '') +
            escH(switchSummary) +
        '</div>';
    chatHandleProfileDraftChange();
}

window.chatHandleProfileDraftChange = function () {
    const select = document.getElementById('chat-profile-select');
    const button = document.querySelector('.chat-runtime-apply-btn');
    if (!select || !button) return;
    button.disabled = !select.value || select.value === chatVisibleProfile();
};

window.chatApplyRuntimeProfile = async function () {
    const select = document.getElementById('chat-profile-select');
    if (!select || !select.value) return;
    const nextProfile = select.value;
    if (nextProfile === chatVisibleProfile()) {
        toast('This chat is already using that profile', 'info', 1500);
        chatHandleProfileDraftChange();
        return;
    }
    const button = document.querySelector('.chat-runtime-apply-btn');
    try {
        if (button) button.disabled = true;
        if (chatState.currentSessionId) {
            const resp = await api('PUT', '/api/chat/sessions/' + chatState.currentSessionId + '/profile', { profile: nextProfile });
            chatApplySessionMetadata(resp.session || null);
            toast('Chat profile changed to ' + nextProfile, 'success', 1500);
        } else {
            chatState.draftProfile = nextProfile;
            chatState.currentSessionProfile = '';
            chatState.currentSegments = [{ id: 'segment-1', index: 1, profile: nextProfile, transport: '', start_message_index: 0 }];
            chatState.currentActiveSegmentId = 'segment-1';
            chatState.currentActiveSegmentIndex = 1;
            chatRenderSessionBanner();
            toast('Next chat will use profile ' + nextProfile, 'success', 1500);
        }
        chatRenderTransportControls();
    } catch (e) {
        toast('Profile change failed: ' + e.message, 'error');
        chatRenderTransportControls();
    }
};

function chatRenderSessionBanner() {
    const banner = document.getElementById('chat-session-banner');
    if (!banner) return;
    const badges = [];
    const profile = chatVisibleProfile();
    let text = chatState.currentTransportNotice || '';
    let cls = 'success';
    if (chatState.lastRequestErrorNotice) {
        badges.push('<span class="badge badge-warning">Send failed</span>');
        text = text ? (chatState.lastRequestErrorNotice + ' ' + text) : chatState.lastRequestErrorNotice;
        cls = 'warning';
    }
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
    if (profile) {
        badges.push('<span class="badge badge-accent">Profile: ' + escH(profile) + '</span>');
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
        const debug = data.debug || {};
        chatState.apiServerEnabled = !!data.api_server;
        chatState.activeProfile = data.profile || chatState.activeProfile || '';
        updateChatHistoryActiveProfileBadge();
        chatState.persistDebugTrace = !!debug.persist_trace;
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
        chatState.persistDebugTrace = false;
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
    if (!(chatState.availableProfiles || []).length) {
        chatLoadAvailableProfiles();
    }
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
                <div class="chat-history-header-main">
                    <span>Chats</span>
                    <span class="badge badge-accent chat-history-active-profile" id="chat-history-active-profile">Portal: ${escH(chatState.activeProfile || 'default')}</span>
                </div>
                <div class="chat-history-header-filters" id="chat-history-filter-slot"></div>
                <div class="chat-history-actions">
                    <button class="btn-icon" title="New Chat" onclick="chatNewSession()" style="width:28px;height:28px">
                        <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
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
    chatLoadAvailableProfiles();

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
                    <button class="btn btn-sm" id="folders-screen-create" onclick="chatCreateFolderPrompt()">New Folder</button>
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
        const visibleSessions = sessions.filter(chatSessionMatchesProfileFilter);
        chatState.folders = folders.slice();
        const collapsed = sidebarFolderNodeCollapseState();
        const ungrouped = visibleSessions.filter(session => !(session.session?.folder_id));
        tree.innerHTML =
            folders.map(folder => {
                const hidden = !!collapsed[folder.id];
                const chats = (folder.sessions || []).filter(chatSessionMatchesProfileFilter);
                if (!chats.length) return '';
                const duplicateMeta = chatFolderDuplicateMeta(folder, folders);
                return '<div class="sidebar-folder-node">' +
                    '<div class="sidebar-folder-node-row" ondragover="chatFolderDragOver(event,\'' + escA(folder.id) + '\')" ondrop="chatDropSessionOnFolder(event,\'' + escA(folder.id) + '\')">' +
                    '<button class="sidebar-folder-toggle" onclick="event.stopPropagation(); toggleSidebarFolderNode(\'' + escA(folder.id) + '\')" title="' + (hidden ? 'Expand' : 'Collapse') + '">' +
                    '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="transform:' + (hidden ? 'rotate(-90deg)' : 'rotate(0deg)') + '"><polyline points="6 9 12 15 18 9"/></svg>' +
                    '</button>' +
                    '<button class="sidebar-folder-node-target' + (((chatState.selectedFolderId || chatState.currentFolderId) === folder.id && !chatState.currentSessionId) ? ' active' : '') + '" onclick="sidebarOpenFolder(\'' + escA(folder.id) + '\')">' +
                    '<span class="sidebar-folder-name-wrap"><span class="sidebar-folder-name">' + escH(folder.title || 'Folder') + '</span>' + (duplicateMeta ? '<span class="sidebar-folder-duplicate-meta">' + escH(duplicateMeta) + '</span>' : '') + '</span>' +
                    '<span class="sidebar-folder-count">' + escH(String(chats.length || 0)) + '</span>' +
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
        const folders = folderData.folders || [];
        const sessions = sessionData.sessions || [];
        const visibleSessions = sessions.filter(chatSessionMatchesProfileFilter);
        chatState.folders = folders.slice();
        chatRenderHistoryProfileFilter(sessions);
        const list = document.getElementById('chat-history-list');
        if (!list) return;
        const ungrouped = visibleSessions.filter(s => !(s.session && s.session.folder_id));
        if (folders.length === 0 && ungrouped.length === 0) {
            list.innerHTML = '<div class="chat-history-empty">No chats yet.<br>Click + to start one.</div>';
            chatRenderContextPanel();
            return;
        }
        const renderSessionItem = (s) => {
            const isActive = s.id === chatState.currentSessionId;
            const preview = s.last_message ? escH(s.last_message) : 'Empty';
            const profiles = chatSessionProfiles(s);
            const profileLabel = chatSessionProfilesLabel(s);
            const profileBadge = profileLabel
                ? ' <span class="badge ' + ((profiles.length === 1 && profiles[0] === (chatState.activeProfile || '')) ? 'badge-accent' : 'badge-warning') + '" title="Profiles used by this chat session">' + escH(profileLabel) + '</span>'
                : '';
            return '<div class="chat-history-item' + (isActive ? ' active' : '') + '" data-sid="' + escA(s.id) + '" draggable="true" ondragstart="chatDragSession(event,\'' + escA(s.id) + '\')" onclick="chatLoadSession(\'' + escA(s.id) + '\')">' +
                '<div class="chat-history-item-title">' + escH(s.title || 'Untitled') + '</div>' +
                '<div class="chat-history-item-preview">' + preview + '</div>' +
                '<div class="chat-history-item-meta">' + escH((s.message_count || 0) + ' msgs') + profileBadge + '</div>' +
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
                const chats = (folder.sessions || []).filter(chatSessionMatchesProfileFilter);
                if (!chats.length) return '';
                const duplicateMeta = chatFolderDuplicateMeta(folder, folders);
                return '<div class="chat-folder-tree">' +
                    '<div class="chat-folder-row' + (isSelected ? ' active' : '') + '" ondragover="chatFolderDragOver(event,\'' + escA(folder.id) + '\')" ondrop="chatDropSessionOnFolder(event,\'' + escA(folder.id) + '\')">' +
                    '<button class="chat-folder-toggle" onclick="event.stopPropagation();chatToggleFolderGroup(\'' + escA(folder.id) + '\')" title="' + (collapsed ? 'Expand' : 'Collapse') + '">' +
                    '<svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transform:' + (collapsed ? 'rotate(-90deg)' : 'rotate(0deg)') + '"><polyline points="6 9 12 15 18 9"/></svg>' +
                    '</button>' +
                    '<button class="chat-folder-main" onclick="chatShowFolderOverview(\'' + escA(folder.id) + '\')">' +
                    '<div class="chat-folder-name">' + escH(folder.title || 'Folder') + '</div>' +
                    '<div class="chat-folder-meta">' + escH((chats.length || 0) + ' chats') + (folder.source_docs && folder.source_docs.length ? ' • ' + escH(folder.source_docs.length + ' sources') : '') + (duplicateMeta ? ' • ' + escH(duplicateMeta) : '') + '</div>' +
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

async function chatRestoreSessionAfterFailure(sessionId) {
    if (!sessionId) return false;
    try {
        const data = await api('GET', '/api/chat/sessions/' + sessionId + '/messages');
        chatState.currentSessionId = sessionId;
        chatState.localMessages = data.messages || [];
        chatApplySessionMetadata(data.session || null);
        chatRenderMessages();
        return true;
    } catch {
        return false;
    }
}

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
    chatBindMessagesScroll(msgs);
    if (msgs.querySelector('#chat-welcome') || msgs.querySelector('.chat-folder-overview')) {
        msgs.innerHTML = '';
    }
    return msgs;
}

function chatRenderMessages() {
    const msgs = document.getElementById('chat-messages');
    if (!msgs) return;
    const shouldAutoScroll = chatShouldAutoScroll(msgs);
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
    const segmentMap = new Map((chatState.currentSegments || []).map(segment => [segment.id, segment]));
    let lastSegmentKey = '';
    messages.forEach(m => {
        const segmentId = m.segment_id || '';
        const segment = segmentMap.get(segmentId) || null;
        const segmentIndex = Number(m.segment_index || segment?.index || 0) || 0;
        const segmentKey = segmentId || (segmentIndex ? String(segmentIndex) : '');
        if (segmentKey && segmentKey !== lastSegmentKey) {
            msgs.appendChild(chatBuildSegmentNode(m, segment));
            lastSegmentKey = segmentKey;
        }
        msgs.appendChild(chatBuildMessageNode(m));
    });
    if (shouldAutoScroll) {
        msgs.scrollTop = msgs.scrollHeight;
    }
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
    const profile = message?.profile || '';
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
    div.className = 'chat-msg ' + role + ' ' + chatMessageToneClass(profile);
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
    const profileHtml = chatSessionProfilesBadgeHtml(session);
    return '<div class="sidebar-folder-chat-row">' +
        '<button class="' + extraClass + active + '" draggable="true" ondragstart="chatDragSession(event,\'' + escA(session.id) + '\')" onclick="sidebarOpenChat(\'' + escA(session.id) + '\')" title="' + escA(session.title || 'Untitled') + '"><span class="sidebar-chat-title">' + escH(session.title || 'Untitled') + '</span>' + profileHtml + '</button>' +
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
    chatState.currentSessionProfile = '';
    chatState.draftProfile = '';
    chatState.currentSegments = [];
    chatState.currentActiveSegmentId = '';
    chatState.currentActiveSegmentIndex = 1;
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
        profile: chatVisibleProfile(),
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
    localStorage.setItem('hermes-transport-preference', next);
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
    const previousSessionId = chatState.currentSessionId || '';
    const previousMessages = Array.isArray(chatState.localMessages)
        ? chatState.localMessages.map(m => ({ ...m }))
        : [];

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
    const persisted = document.getElementById('chat-persistent-progress');
    if (persisted) persisted.remove();
    const msgs = document.getElementById('chat-messages');
    const dots = document.createElement('div');
    dots.id = 'chat-thinking-dots';
    dots.className = 'chat-thinking';
    dots.innerHTML = '<div class="chat-thinking-bubble"><div class="chat-thinking-header"><span class="chat-thinking-icon"><svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg></span><span class="chat-thinking-text">Hermes (' + escH(chatVisibleProfile()) + ') is thinking<span class="chat-thinking-ellipsis"></span></span>' + (chatState.currentRequestCancelSupported ? '<button class="chat-stop-btn" id="chat-stop-btn" onclick="chatAbort()" title="Stop"><svg aria-hidden="true" width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg></button>' : '') + '</div><div class="chat-progress-log" id="chat-progress-log"></div></div>';
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
    const activeSegment = chatCurrentSegment();
    // Optimistically add user message to local
    const userMsg = {
        role: 'user',
        content: message,
        files,
        timestamp: new Date().toISOString(),
        segment_id: activeSegment?.id || chatState.currentActiveSegmentId || '',
        segment_index: activeSegment?.index || chatState.currentActiveSegmentIndex || 1,
        profile: chatVisibleProfile(),
        transport: chatExpectedTransport(),
    };
    chatState.localMessages.push(userMsg);
    chatAppendMsg('user', message, files, {
        segment_id: userMsg.segment_id,
        segment_index: userMsg.segment_index,
        profile: userMsg.profile,
        transport: userMsg.transport,
    });
    input.value = '';
    chatAutoResize(input);
    document.getElementById('chat-send-btn').disabled = !chatState.currentRequestCancelSupported;

    // AbortController for cancellation
    const controller = new AbortController();
    chatState.chatAbortController = controller;
    chatState.currentRequestId = makeRequestId();
    chatStartRequestProgress(chatState.currentRequestId, chatExpectedTransport());

    try {
        const resp = await api('POST', '/api/chat', {
            message, session_id: chatState.currentSessionId,
            profile: chatVisibleProfile(),
            folder_id: chatState.currentSessionId ? '' : (chatState.draftFolderId || chatState.selectedFolderId || ''),
            transport_preference: chatState.transportPreference || 'auto',
            request_id: chatState.currentRequestId,
            files: pendingUploads.map(f => ({ stored_as: f.stored_as, name: f.name })),
        }, controller.signal);
        chatClearRequestErrorNotice();
        chatState.currentSessionId = resp.session_id;
        chatApplySessionMetadata(resp.session || null);
        if (resp.user_message && chatState.localMessages.length > 0) {
            chatState.localMessages[chatState.localMessages.length - 1] = resp.user_message;
        }
        const assistantMsg = resp.assistant_message || { role: 'assistant', content: resp.response, timestamp: new Date().toISOString() };
        chatState.localMessages.push(assistantMsg);
        chatRenderMessages();
        chatSetDebugTraceStatus('Completed');
        chatRenderPersistentProgressBubble('Completed');
    } catch (e) {
        input.value = message;
        chatAutoResize(input);
        chatRenderFileBar();
        chatSyncSendButton();
        const failedSessionId = e?.responseData?.session_id || chatState.currentSessionId || '';

        if (e.name === 'AbortError' || chatState.cancelRequested) {
            chatSetDebugTraceStatus('Cancelled');
            chatState.localMessages.pop();
            const container = document.getElementById('chat-messages');
            if (container) {
                const uls = container.querySelectorAll('.chat-msg.user');
                const last = uls[uls.length - 1];
                if (last) last.remove();
            }
            chatRenderPersistentProgressBubble('Cancelled');
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
        chatSetDebugTraceStatus('Failed');
        chatState.localMessages.pop(); // remove failed user msg from state
        // Now find and remove the user bubble (always the last .user in the container)
        const container = document.getElementById('chat-messages');
        if (container) {
            const userBubbles = container.querySelectorAll('.chat-msg.user');
            const lastUser = userBubbles[userBubbles.length - 1];
            if (lastUser) lastUser.remove();
        }
        chatRenderPersistentProgressBubble('Failed');
        chatResetComposerAfterRequest();
        const restored = await chatRestoreSessionAfterFailure(failedSessionId);
        if (!restored) {
            chatState.currentSessionId = previousSessionId;
            chatState.localMessages = previousMessages;
            chatRenderMessages();
        }
        chatLoadHistory();
        const errorAt = chatFormatNoticeTimestamp(new Date());
        chatSetRequestErrorNotice('Send failed at ' + errorAt + '. Message restored as unsent draft.');
        toast((e.message || 'Request failed') + ' (' + errorAt + ')', 'error', 5000);
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
    const shouldAutoScroll = chatShouldAutoScroll(container);
    const div = chatBuildMessageNode({
        role,
        content,
        files,
        timestamp: new Date().toISOString(),
        ...messageMeta,
    });
    container.appendChild(div);
    if (shouldAutoScroll) {
        container.scrollTop = container.scrollHeight;
    }
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

document.addEventListener('DOMContentLoaded', async () => {
    bootstrapTokenFromUrl();
    initLoginForm();

    // Always require explicit login — never auto-enter dashboard from a
    // leftover session cookie.  Show the login screen on every page load.
    showLoginScreen();
});

function bootstrapApp() {
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

    updateActiveProfileIndicators({ profile: 'loading' });
    checkHealth();
    HermesUpdate.ensureLoaded().catch(() => {});
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
}

setInterval(() => { if (_authed) checkHealth(); }, 10000);
setInterval(() => { if (_authed) HermesUpdate.refresh(false, { silent: true }).catch(() => {}); }, 300000);
