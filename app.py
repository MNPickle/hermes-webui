#!/usr/bin/env python3
"""
Hermes Admin Panel — Flask Backend
===================================
Web UI backend for managing a Hermes Agent installation.
Reads/writes ~/.hermes/config.yaml and ~/.hermes/.env
"""

import os
import re
import copy
import json
import shutil
import subprocess
import time
import logging
from datetime import datetime
from pathlib import Path
from functools import wraps

import yaml
from dotenv import dotenv_values, set_key, unset_key
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import uuid
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('hermes-webui')

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERMES_HOME = Path.home() / ".hermes"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
SKILLS_DIR = HERMES_HOME / "skills"

# Hermes executable - try current install locations with fallback
def _find_hermes_bin():
    # Prefer HERMES_HOME/.venv (the explicitly managed venv) over PATH,
    # to avoid picking up an older pipx-installed hermes
    import shutil as _shutil
    candidates = [
        HERMES_HOME / ".venv" / "bin" / "hermes",   # v0.6+ (explicitly managed)
        Path.home() / ".local" / "bin" / "hermes",  # pipx user install
        _shutil.which("hermes") or Path.home() / ".local" / "bin" / "hermes",
        HERMES_HOME / "hermes-agent" / "venv" / "bin" / "hermes",  # legacy
    ]
    for path in candidates:
        if path and path.exists():
            return path
    return candidates[0]

HERMES_BIN = _find_hermes_bin()
SESSIONS_DIR = HERMES_HOME / "sessions"
UPLOADS_DIR = Path.home() / "hermes-web-ui" / "uploads"
UPLOAD_FOLDER = Path(__file__).parent / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
BACKUP_DIR = HERMES_HOME / "backups"

# Chat session storage (persisted to disk)
CHAT_DATA_DIR = Path(__file__).parent / "chat_data"
CHAT_DATA_DIR.mkdir(exist_ok=True)

chat_sessions: dict = {}  # runtime cache: sid -> session dict


def _load_all_sessions():
    """Load all persisted chat sessions from disk into memory."""
    chat_sessions.clear()
    for f in sorted(CHAT_DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            chat_sessions[data["id"]] = data
        except Exception as exc:
            logger.warning("Failed to load session file %s: %s", f.name, exc)


def _save_session(session_id):
    """Persist a single session to disk."""
    if session_id in chat_sessions:
        path = CHAT_DATA_DIR / f"{session_id}.json"
        path.write_text(json.dumps(chat_sessions[session_id], ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_session_from_disk(session_id):
    """Remove a session from memory and disk."""
    chat_sessions.pop(session_id, None)
    path = CHAT_DATA_DIR / f"{session_id}.json"
    if path.exists():
        path.unlink()


# Load sessions on startup
_load_all_sessions()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)
# Restrict CORS to localhost by default
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
    }
})

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
HERMES_WEBUI_TOKEN = os.environ.get("HERMES_WEBUI_TOKEN")

def require_token(f):
    """Decorator to require HERMES_WEBUI_TOKEN for API endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not HERMES_WEBUI_TOKEN:
            logger.warning("Authentication not configured - rejecting API request")
            # Fail closed: if no token is configured, deny access
            return jsonify({"ok": False, "error": "API authentication not configured"}), 401
        
        # Check Authorization header for Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("API request missing Authorization header from %s", request.remote_addr)
            return jsonify({"ok": False, "error": "Missing or invalid Authorization header"}), 401
        
        provided_token = auth_header[7:]  # Remove "Bearer " prefix
        if provided_token != HERMES_WEBUI_TOKEN:
            logger.warning("API authentication failed - invalid token from %s", request.remote_addr)
            return jsonify({"ok": False, "error": "Invalid token"}), 401
        
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------
# Simple in-memory rate limiter: {ip: [(timestamp, endpoint), ...]}
_rate_limit_store = {}
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 60  # requests per window per IP

def rate_limit(f):
    """Decorator to rate limit requests per IP address."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        now = time.time()
        endpoint = request.endpoint
        
        # Clean old entries for this IP
        if client_ip in _rate_limit_store:
            _rate_limit_store[client_ip] = [
                (ts, ep) for ts, ep in _rate_limit_store[client_ip]
                if now - ts < _RATE_LIMIT_WINDOW
            ]
        else:
            _rate_limit_store[client_ip] = []
        
        # Check rate limit
        request_count = len(_rate_limit_store[client_ip])
        if request_count >= _RATE_LIMIT_MAX_REQUESTS:
            logger.warning(
                "Rate limit exceeded for %s on %s (%d requests in %ds)",
                client_ip, endpoint, request_count, _RATE_LIMIT_WINDOW
            )
            return jsonify({
                "ok": False,
                "error": "Rate limit exceeded. Please try again later."
            }), 429
        
        # Record this request
        _rate_limit_store[client_ip].append((now, endpoint))
        
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------------------------
# Secret-key patterns
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = re.compile(r"(key|token|secret|password|apikey|api_key)", re.IGNORECASE)


# ===================================================================
# ConfigManager
# ===================================================================
class ConfigManager:
    """Manages reading, writing, and merging the Hermes YAML config."""

    def __init__(self):
        self._config: dict = {}
        self.load()

    # -- loading ----------------------------------------------------------
    def load(self):
        """Read config.yaml from disk (or return empty dict)."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                    self._config = yaml.safe_load(fh) or {}
            except Exception as exc:
                self._config = {}
                print(f"[ConfigManager] Failed to load config: {exc}")
        else:
            self._config = {}

    # -- saving -----------------------------------------------------------
    def save(self):
        """Write config to disk, creating a timestamped backup first."""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = BACKUP_DIR / f"config_{ts}.yaml"
        if CONFIG_PATH.exists():
            shutil.copy2(CONFIG_PATH, backup)
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            yaml.dump(
                self._config,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

    # -- getters ----------------------------------------------------------
    def get(self, section=None):
        """Return full config or a single section (masked)."""
        if section is None:
            return self.mask_secrets(copy.deepcopy(self._config))
        data = copy.deepcopy(self._config.get(section, {}))
        if isinstance(data, dict):
            return self.mask_secrets(data)
        return data

    def get_raw(self, section=None):
        """Return config or section WITHOUT masking (internal use)."""
        if section is None:
            return copy.deepcopy(self._config)
        return copy.deepcopy(self._config.get(section, {}))

    # -- setters ----------------------------------------------------------
    def set(self, section, data):
        """Replace a section entirely and save."""
        self._config[section] = data
        self.save()

    def update(self, section, data):
        """Deep-merge *data* into *section* and save."""
        current = self._config.get(section, {})
        if isinstance(current, dict) and isinstance(data, dict):
            self._config[section] = self.deep_merge(current, data)
        else:
            self._config[section] = data
        self.save()

    def delete_section(self, section):
        """Remove a top-level key from config and save."""
        self._config.pop(section, None)
        self.save()

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def deep_merge(base: dict, update: dict) -> dict:
        """Recursively merge *update* into *base*, returning a new dict."""
        result = copy.deepcopy(base)
        for key, value in update.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = ConfigManager.deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        return result

    @staticmethod
    def mask_secrets(data, parent_key=""):
        """Recursively mask values whose key name hints at a secret."""
        if isinstance(data, dict):
            return {
                k: ConfigManager.mask_secrets(v, k) for k, v in data.items()
            }
        if isinstance(data, list):
            return [ConfigManager.mask_secrets(v, parent_key) for v in data]
        if isinstance(data, str) and _SECRET_PATTERNS.search(parent_key) and len(data) > 4:
            return "\u2022" * (len(data) - 4) + data[-4:]
        return data


# Global config manager instance
cfg = ConfigManager()


# ===================================================================
# Helper utilities
# ===================================================================

def _mask_value(key: str, value: str) -> str:
    """Mask a single env-var value if its key looks secret."""
    if not isinstance(value, str):
        return value
    if _SECRET_PATTERNS.search(key) and len(value) > 4:
        return "\u2022" * (len(value) - 4) + value[-4:]
    return value


def _classify_env_key(key: str) -> str:
    """Classify an env var into a display group."""
    k = key.lower()
    if any(p in k for p in ("anthropic", "openai", "groq", "google", "mistral", "ollama", "together", "fireworks", "deepseek", "cohere", "xai", "bedrock", "azure")):
        return "Provider"
    if any(p in k for p in ("discord", "whatsapp", "telegram", "slack", "webhook", "matrix")):
        return "Channel"
    return "System"


def _run_hermes(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a hermes CLI command and return the CompletedProcess."""
    env = {**os.environ, "NO_COLOR": "1"}
    return subprocess.run(
        [str(HERMES_BIN)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _gateway_status() -> dict:
    """Run `hermes gateway status` and parse the result.

    Returns:
        {
            "running": bool,          # True if gateway is running
            "pid": int | None,       # PID from CLI output if present
            "status_text": str,       # Raw first line of output
            "raw": str                # Full stdout
        }
    """
    try:
        r = _run_hermes("gateway", "status", timeout=10)
        lines = r.stdout.strip().split("\n")
        status_line = lines[0] if lines else ""
        raw = r.stdout + r.stderr

        running = "✓ Gateway is running" in status_line
        pid: int | None = None
        if running:
            # Extract PID from "✓ Gateway is running (PID: NNNNN)"
            import re
            m = re.search(r"PID:\s*(\d+)", status_line)
            if m:
                pid = int(m.group(1))

        return {"running": running, "pid": pid, "status_text": status_line, "raw": raw}
    except Exception as e:
        return {"running": False, "pid": None, "status_text": str(e), "raw": ""}


def _find_gateway_pid() -> int | None:
    """Try to locate the Hermes gateway process ID.

    The gateway runs as a python/hermes subprocess. We look for processes
    whose cmdline contains 'hermes' followed by 'gateway' (as separate
    arguments), or the python wrapper running hermes_cli.main with gateway.
    """
    try:
        # Try to read /proc entries directly for reliability
        proc_dir = Path("/proc")
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline_path = entry / "cmdline"
                cmdline = cmdline_path.read_text(errors="replace")
                # Normalize: split on \0 and join with spaces
                args = cmdline.replace("\x00", " ").split()
                if not args:
                    continue
                # Check patterns used by hermes_cli/gateway.py find_gateway_pids()
                combined = " ".join(args)
                patterns = [
                    "hermes_cli.main gateway",
                    "hermes_cli/main.py gateway",
                    "hermes gateway",
                    "gateway/run.py",
                ]
                if any(p in combined for p in patterns):
                    pid = int(entry.name)
                    if pid != os.getpid():
                        return pid
            except (PermissionError, FileNotFoundError, ProcessLookupError, ValueError):
                continue
    except Exception:
        pass
    return None


def _read_log_file(path: Path, lines: int = 200) -> str:
    """Read the last *lines* from a log file."""
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
        return "".join(all_lines[-lines:])
    except Exception:
        return ""


def _skill_frontmatter(skill_md: Path) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
    try:
        text = skill_md.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                return yaml.safe_load(parts[1]) or {}
    except Exception:
        pass
    return {}


def _http_error(msg: str, status: int = 500):
    logger.error("HTTP error %d: %s", status, msg)
    return jsonify({"ok": False, "error": msg}), status


# ===================================================================
# 1. Health
# ===================================================================

@app.route("/api/health")
@require_token
def api_health():
    try:
        # Primary truth: Hermes CLI status
        gs = _gateway_status()
        # Fallback PID scan only if CLI didn't return a PID (for debug/missing CLI info)
        pid = gs["pid"]
        if pid is None:
            pid = _find_gateway_pid()
        version = "unknown"
        try:
            r = _run_hermes("--version", timeout=5)
            if r.returncode == 0:
                version = r.stdout.strip() or r.stderr.strip()
        except Exception:
            pass
        return jsonify({
            "status": "running" if gs["running"] else "stopped",
            "gateway_pid": pid,
            "gateway_running": gs["running"],
            "version": version,
            "hermes_home": str(HERMES_HOME),
        })
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 2. System info
# ===================================================================

@app.route("/api/system")
@require_token
def api_system():
    try:
        import platform
        disk = shutil.disk_usage(str(Path.home()))
        uptime = 0
        try:
            with open("/proc/uptime") as fh:
                uptime = float(fh.read().split()[0])
        except Exception:
            pass
        return jsonify({
            "python_version": platform.python_version(),
            "os_info": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "disk_free": disk.free,
            "uptime": uptime,
        })
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 3–6. Config endpoints
# ===================================================================

@app.route("/api/config", methods=["GET"])
@require_token
def api_config_get():
    try:
        return jsonify(cfg.get())
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/config/<section>", methods=["GET"])
@require_token
def api_config_get_section(section):
    try:
        return jsonify(cfg.get(section))
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/config/<section>", methods=["PUT"])
@require_token
def api_config_put_section(section):
    try:
        data = request.get_json(force=True)
        cfg.update(section, data)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "errors": [str(exc)]}), 500


@app.route("/api/config/reload", methods=["POST"])
@require_token
def api_config_reload():
    try:
        cfg.load()
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 7–9. Environment variables
# ===================================================================

@app.route("/api/env", methods=["GET"])
@require_token
def api_env_get():
    try:
        raw = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
        masked = {k: _mask_value(k, v) for k, v in raw.items() if v is not None}

        groups: dict[str, list[str]] = {}
        for k in masked:
            g = _classify_env_key(k)
            groups.setdefault(g, []).append(k)

        return jsonify({"vars": masked, "groups": groups})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/env", methods=["POST"])
@require_token
def api_env_set():
    try:
        data = request.get_json(force=True)
        key, value = data.get("key"), data.get("value")
        if not key:
            return jsonify({"ok": False, "error": "key is required"}), 400
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        set_key(str(ENV_PATH), key, value or "")
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/env/<key>", methods=["PUT"])
@require_token
def api_env_update(key):
    try:
        data = request.get_json(force=True)
        value = data.get("value", "")
        set_key(str(ENV_PATH), key, value)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/env/<key>", methods=["DELETE"])
@require_token
def api_env_delete(key):
    try:
        unset_key(str(ENV_PATH), key)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 10–14. Providers
# ===================================================================

def _get_providers_info():
    """Build a structured view of providers from config."""
    raw = cfg.get_raw()
    model_cfg = raw.get("model", {})
    custom = raw.get("custom_providers", []) or []

    default = {
        "provider": model_cfg.get("default_provider", ""),
        "model": model_cfg.get("default_model", ""),
        "base_url": model_cfg.get("base_url", ""),
    }

    auxiliary = {}
    for aux_key in ("vision", "web_extract", "compression", "session_search",
                     "summarization", "embedding", "tts", "stt"):
        val = model_cfg.get(aux_key)
        if val:
            if isinstance(val, str):
                auxiliary[aux_key] = {"model": val}
            elif isinstance(val, dict):
                auxiliary[aux_key] = val

    return default, custom, auxiliary


@app.route("/api/providers", methods=["GET"])
@require_token
def api_providers_get():
    try:
        default, custom, auxiliary = _get_providers_info()
        # Mask secrets in custom providers
        safe_custom = [cfg.mask_secrets(c) for c in custom]
        return jsonify({
            "default": default,
            "custom": safe_custom,
            "auxiliary": cfg.mask_secrets(auxiliary),
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers", methods=["POST"])
@require_token
def api_providers_add():
    try:
        data = request.get_json(force=True)
        name = data.get("name")
        if not name:
            return jsonify({"ok": False, "error": "name is required"}), 400

        raw = cfg.get_raw()
        custom = raw.get("custom_providers", []) or []
        # Check for duplicate name
        for p in custom:
            if p.get("name") == name:
                return jsonify({"ok": False, "error": f"Provider '{name}' already exists"}), 409

        custom.append(data)
        cfg.set("custom_providers", custom)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>", methods=["PUT"])
@require_token
def api_providers_update(name):
    try:
        data = request.get_json(force=True)
        raw = cfg.get_raw()
        custom = raw.get("custom_providers", []) or []
        found = False
        for i, p in enumerate(custom):
            if p.get("name") == name:
                custom[i] = ConfigManager.deep_merge(p, data)
                custom[i]["name"] = name  # preserve name if not in payload
                found = True
                break
        if not found:
            return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
        cfg.set("custom_providers", custom)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>", methods=["DELETE"])
@require_token
def api_providers_delete(name):
    try:
        raw = cfg.get_raw()
        custom = raw.get("custom_providers", []) or []
        new_custom = [p for p in custom if p.get("name") != name]
        if len(new_custom) == len(custom):
            return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
        cfg.set("custom_providers", new_custom)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>/test", methods=["POST"])
@require_token
@rate_limit
def api_providers_test(name):
    try:
        raw = cfg.get_raw()
        custom = raw.get("custom_providers", []) or []
        provider_cfg = None
        for p in custom:
            if p.get("name") == name:
                provider_cfg = p
                break

        if not provider_cfg:
            # Maybe it's the default provider
            model_cfg = raw.get("model", {})
            if model_cfg.get("default_provider") == name:
                provider_cfg = {
                    "name": name,
                    "base_url": model_cfg.get("base_url", ""),
                    "model": model_cfg.get("default_model", ""),
                    "api_key": model_cfg.get("api_key", ""),
                }
            else:
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404

        # Try a simple chat completion request
        import urllib.request
        import urllib.error

        base_url = (provider_cfg.get("base_url") or "").rstrip("/")
        api_key = provider_cfg.get("api_key", "")
        model = provider_cfg.get("model", "gpt-3.5-turbo")

        # Determine the chat completions endpoint
        if "/v1" in base_url:
            url = f"{base_url}/chat/completions"
        elif "/chat" in base_url:
            url = base_url
        else:
            url = f"{base_url}/v1/chat/completions"

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                latency = int((time.time() - start) * 1000)
                return jsonify({"ok": True, "latency_ms": latency, "response": body[:200]})
        except urllib.error.HTTPError as e:
            latency = int((time.time() - start) * 1000)
            body = e.read().decode("utf-8", errors="replace")[:300]
            return jsonify({"ok": False, "error": f"HTTP {e.code}: {body}", "latency_ms": latency}), 200
        except urllib.error.URLError as e:
            latency = int((time.time() - start) * 1000)
            return jsonify({"ok": False, "error": str(e.reason), "latency_ms": latency}), 200

    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ===================================================================
# 15. Models
# ===================================================================

@app.route("/api/models", methods=["GET"])
@require_token
def api_models_get():
    try:
        raw = cfg.get_raw()
        model_cfg = raw.get("model", {})
        custom = raw.get("custom_providers", []) or []

        all_models = []
        seen = set()

        def _add(provider_name, model_name):
            if model_name and model_name not in seen:
                seen.add(model_name)
                all_models.append({"provider": provider_name, "model": model_name})

        _add(model_cfg.get("default_provider", "default"), model_cfg.get("default_model", ""))
        _add(model_cfg.get("default_provider", "default"), model_cfg.get("fallback_model", ""))

        for cp in custom:
            _add(cp.get("name", ""), cp.get("model", ""))

        for aux_key in ("vision", "web_extract", "compression", "session_search",
                         "summarization", "embedding", "tts", "stt"):
            val = model_cfg.get(aux_key)
            if isinstance(val, str):
                _add(aux_key, val)
            elif isinstance(val, dict):
                _add(aux_key, val.get("model", ""))

        return jsonify({
            "default_model": model_cfg.get("default_model", ""),
            "default_provider": model_cfg.get("default_provider", ""),
            "fallback_model": model_cfg.get("fallback_model", ""),
            "all_models": all_models,
        })
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 16–20. Agents / Personalities
# ===================================================================

@app.route("/api/agents", methods=["GET"])
@require_token
def api_agents_get():
    try:
        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        personalities = agent_cfg.get("personalities", {})
        # Return agent defaults plus personalities (masked)
        result = {
            "defaults": cfg.mask_secrets({k: v for k, v in agent_cfg.items() if k != "personalities"}),
            "personalities": cfg.mask_secrets(personalities),
        }
        return jsonify(result)
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/agents", methods=["POST"])
@require_token
def api_agents_add():
    try:
        data = request.get_json(force=True)
        name = data.get("name")
        if not name:
            return jsonify({"ok": False, "error": "name is required"}), 400

        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        personalities = agent_cfg.get("personalities", {})
        if name in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' already exists"}), 409

        personalities[name] = {k: v for k, v in data.items() if k != "name"}
        agent_cfg["personalities"] = personalities
        cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/agents/<name>", methods=["PUT"])
@require_token
def api_agents_update(name):
    try:
        data = request.get_json(force=True)
        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        personalities = agent_cfg.get("personalities", {})
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

        personalities[name] = ConfigManager.deep_merge(personalities[name], data)
        agent_cfg["personalities"] = personalities
        cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/agents/<name>", methods=["DELETE"])
@require_token
def api_agents_delete(name):
    try:
        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        personalities = agent_cfg.get("personalities", {})
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

        del personalities[name]
        agent_cfg["personalities"] = personalities
        cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/agents/<name>/duplicate", methods=["POST"])
@require_token
def api_agents_duplicate(name):
    try:
        data = request.get_json(force=True)
        new_name = data.get("new_name")
        if not new_name:
            return jsonify({"ok": False, "error": "new_name is required"}), 400

        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        personalities = agent_cfg.get("personalities", {})
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

        personalities[new_name] = copy.deepcopy(personalities[name])
        agent_cfg["personalities"] = personalities
        cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 21–22. Skills
# ===================================================================

@app.route("/api/skills", methods=["GET"])
@require_token
def api_skills_get():
    try:
        skills = []
        if not SKILLS_DIR.exists():
            return jsonify({"skills": skills})

        for root, dirs, files in os.walk(str(SKILLS_DIR)):
            dirs[:] = [d for d in dirs if not d.startswith(".")]  # skip hidden
            if "SKILL.md" in files:
                skill_md = Path(root) / "SKILL.md"
                fm = _skill_frontmatter(skill_md)
                rel_path = Path(root).relative_to(SKILLS_DIR)
                dir_name = str(rel_path)
                enabled = not dir_name.endswith(".disabled")
                skills.append({
                    "name": fm.get("name", rel_path.name),
                    "category": fm.get("category", ""),
                    "description": fm.get("description", ""),
                    "path": str(rel_path),
                    "enabled": enabled,
                    "frontmatter": fm,
                })
        return jsonify({"skills": skills})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/skills/<path:name>/toggle", methods=["POST"])
@require_token
def api_skill_toggle(name):
    try:
        skill_path = SKILLS_DIR / name
        disabled_path = SKILLS_DIR / (name + ".disabled")

        if skill_path.exists():
            # Disable: rename to .disabled
            shutil.move(str(skill_path), str(disabled_path))
            return jsonify({"ok": True, "enabled": False})
        elif disabled_path.exists():
            # Enable: remove .disabled suffix
            shutil.move(str(disabled_path), str(skill_path))
            return jsonify({"ok": True, "enabled": True})
        else:
            return jsonify({"ok": False, "error": f"Skill '{name}' not found"}), 404
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 23–24. Channels
# ===================================================================

@app.route("/api/channels", methods=["GET"])
@require_token
def api_channels_get():
    try:
        raw = cfg.get_raw()
        channels_cfg = raw.get("channels", {})
        toolsets = raw.get("platform_toolsets", {})

        channels = []
        for ch_name, ch_config in channels_cfg.items():
            if not isinstance(ch_config, dict):
                continue
            # Derive enabled from platform_toolsets
            enabled = bool(toolsets.get(ch_name))
            channels.append({
                "name": ch_name,
                "config": cfg.mask_secrets(ch_config),
                "enabled": enabled,
            })

        return jsonify({"channels": channels})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/channels/<name>", methods=["PUT"])
@require_token
def api_channels_update(name):
    try:
        data = request.get_json(force=True)
        raw = cfg.get_raw()
        channels_cfg = raw.get("channels", {})
        if name not in channels_cfg:
            return jsonify({"ok": False, "error": f"Channel '{name}' not found"}), 404

        channels_cfg[name] = ConfigManager.deep_merge(channels_cfg[name], data)
        cfg.set("channels", channels_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 25–26. Sessions
# ===================================================================

@app.route("/api/sessions", methods=["GET"])
@require_token
def api_sessions_get():
    """Return a list of recent chat sessions for the session selector."""
    try:
        import glob
        sessions = []
        if SESSIONS_DIR.is_dir():
            files = sorted(glob.glob(str(SESSIONS_DIR / "*.json")), key=os.path.getmtime, reverse=True)[:50]
            for f in files:
                name = os.path.splitext(os.path.basename(f))[0]
                sessions.append({"id": name, "title": name})
        return jsonify({"sessions": sessions})
    except Exception as exc:
        return jsonify({"sessions": []})


@app.route("/api/sessions/config", methods=["GET"])
@require_token
def api_sessions_config_get():
    try:
        return jsonify(cfg.get("session_reset") or {})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/sessions/config", methods=["PUT"])
@require_token
def api_sessions_config_put():
    try:
        data = request.get_json(force=True)
        cfg.set("session_reset", data)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 27–28. Hooks / Webhooks
# ===================================================================

@app.route("/api/hooks", methods=["GET"])
@require_token
def api_hooks_get():
    try:
        hooks = cfg.get("hooks")
        if not hooks:
            return jsonify({"hooks": {}, "webhook": {}})
        return jsonify(hooks)
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/hooks", methods=["PUT"])
@require_token
def api_hooks_put():
    try:
        data = request.get_json(force=True)
        cfg.set("hooks", data)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 29. Logs
# ===================================================================

@app.route("/api/logs")
@require_token
def api_logs_get():
    try:
        lines = request.args.get("lines", 200, type=int)
        lines = max(10, min(lines, 2000))

        # hermes logs is not a valid CLI command - skip CLI entirely
        # and read log files directly (hermes sessions are in SQLite, not text logs)
        log_text = ""

        # Fallback: read from common log locations
        if not log_text:
            log_candidates = [
                HERMES_HOME / "logs" / "hermes.log",
                HERMES_HOME / "hermes.log",
                HERMES_HOME / "gateway.log",
            ]
            for lc in log_candidates:
                content = _read_log_file(lc, lines)
                if content:
                    log_text = content
                    break

        return jsonify({"logs": log_text})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 30. Tools
# ===================================================================

@app.route("/api/tools", methods=["GET"])
@require_token
def api_tools_get():
    try:
        tools = []
        total_enabled = 0
        total_disabled = 0

        try:
            r = _run_hermes("tools", "list", timeout=15)
            output = r.stdout if r.returncode == 0 else r.stderr
        except Exception:
            output = ""

        if output:
            for line in output.strip().splitlines():
                line = line.strip()
                if not line or line.startswith("-") or line.startswith("="):
                    continue
                # Try to parse "NAME  STATUS  Description" patterns
                parts = line.split(None, 2)
                if len(parts) >= 2:
                    tool_name = parts[0]
                    status = parts[1].lower()
                    desc = parts[2] if len(parts) > 2 else ""
                    is_enabled = status in ("enabled", "active", "on", "✓", "yes")
                    tools.append({
                        "name": tool_name,
                        "status": "enabled" if is_enabled else "disabled",
                        "description": desc,
                    })
                    if is_enabled:
                        total_enabled += 1
                    else:
                        total_disabled += 1

        return jsonify({
            "tools": tools,
            "total_enabled": total_enabled,
            "total_disabled": total_disabled,
        })
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 31. Service control
# ===================================================================

@app.route("/api/service/<action>", methods=["POST"])
@require_token
def api_service_action(action):
    try:
        action = action.lower()
        cmd_map = {
            "start": ["gateway", "start"],
            "stop": ["gateway", "stop"],
            "restart": ["gateway", "restart"],
            "doctor": ["doctor"],
        }
        if action not in cmd_map:
            return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400

        r = _run_hermes(*cmd_map[action], timeout=30)
        output = (r.stdout + "\n" + r.stderr).strip()

        # Determine actual running state after the action using Hermes CLI status
        running_after = _gateway_status()["running"]

        if action == "start":
            # start returns failure if systemd service not installed, even when
            # gateway is already running manually. Treat already-running as success.
            ok = running_after or r.returncode == 0
        elif action == "stop":
            # stop returns success even when no systemd service exists (falls back
            # to kill_gateway_processes). Treat already-stopped as success.
            ok = (not running_after) or r.returncode == 0
        elif action == "restart":
            # restart succeeds if the gateway is running after the command
            ok = running_after
        else:
            ok = r.returncode == 0

        return jsonify({"ok": ok, "output": output, "returncode": r.returncode, "gateway_running": running_after})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Command timed out", "gateway_running": _gateway_status()["running"]}), 500
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 33. Onboarding check
# ===================================================================

@app.route("/api/onboarding", methods=["GET"])
@require_token
def api_onboarding_get():
    try:
        raw = cfg.get_raw()
        env_vars = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
        missing = []

        # Check for API key in env
        has_api_key = any(
            v for k, v in env_vars.items() if v and _SECRET_PATTERNS.search(k)
        )
        if not has_api_key:
            # Also check config model section
            model_cfg = raw.get("model", {})
            if not model_cfg.get("api_key"):
                missing.append("api_key")

        # Check default provider is set
        model_cfg = raw.get("model", {})
        if not model_cfg.get("default_provider"):
            missing.append("default_provider")

        # Check default model is set
        if not model_cfg.get("default_model"):
            missing.append("default_model")

        # Check at least one channel is configured
        channels = raw.get("channels", {})
        if not channels:
            missing.append("channel")

        return jsonify({
            "complete": len(missing) == 0,
            "missing": missing,
        })
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# Chat endpoints (from V1 chat UI)
# ===================================================================

def _clean_cli_output(output: str) -> str:
    """Strip CLI banner, box-drawing, tool list, metadata from hermes -q output."""
    lines = output.split('\n')
    clean = []
    skip = False
    for line in lines:
        if 'Hermes Agent v' in line or 'Available Tools' in line or 'Available Skills' in line:
            skip = True; continue
        if 'Query:' in line:
            skip = False; continue
        if 'Resume this session' in line or line.strip().startswith('hermes --resume'):
            skip = True; continue
        if skip:
            continue
        # Skip separator lines (box drawing chars including corners)
        if re.match(r'^[\u2500-\u257f\u2550-\u256f\u2800-\u28ff\u2580-\u259f\u2591-\u2593\u2b1b\u2b1c ]+$', line.strip()):
            continue
        # Skip lines that are box-drawing borders with text (e.g. ╭─── Health ───╮)
        stripped = line.strip()
        if any(c in stripped for c in '\u2500\u2501\u2502\u2503\u2504\u2505\u2506\u2507\u2508\u2509\u250a\u250b\u250c\u250d\u250e\u250f\u2510\u2511\u2512\u2513\u2514\u2515\u2516\u2517\u2518\u2519\u251a\u251b\u251c\u251d\u251e\u251f\u2520\u2521\u2522\u2523\u2524\u2525\u2526\u2527\u2528\u2529\u252a\u252b\u252c\u252d\u252e\u252f\u2530\u2531\u2532\u2533\u2534\u2535\u2536\u2537\u2538\u2539\u253a\u253b\u253c\u253d\u253e\u253f\u2540\u2541\u2542\u2543\u2544\u2545\u2546\u2547\u2548\u2549\u254a\u254b\u254c\u254d\u254e\u254f\u2550\u2551\u2552\u2553\u2554\u2555\u2556\u2557\u2558\u2559\u255a\u255b\u255c\u255d\u255e\u255f\u2560\u2561\u2562\u2563\u2564\u2565\u2566\u2567\u2568\u2569\u256a\u256b\u256c\u256d\u256e\u256f\u2570\u2571\u2572\u2573\u2574\u2575\u2576\u2577\u2578\u2579\u257a\u257b\u257c\u257d\u257e\u257f'):
            box_char_count = sum(1 for c in stripped if ord(c) in range(0x2500, 0x2580) or ord(c) in range(0x2550, 0x2570))
            if box_char_count > len(stripped) * 0.6:
                continue
        if re.match(r'^\s*(Session|Duration|Messages):', line):
            continue
        # Skip braille-heavy lines (tool checkboxes)
        braille = sum(1 for c in line if '\u2800' <= c <= '\u28ff')
        if braille > len(line) * 0.4:
            continue
        stripped = line.strip()
        if stripped:
            if '\u2502' in stripped:
                parts = stripped.split('\u2502')
                content = [p.strip() for p in parts if p.strip() and not re.match(r'^[\u2800-\u28ff\s]+$', p.strip())]
                if content:
                    text = ' '.join(content)
                    if any(s in text for s in ['Available Tools','Available Skills','/help','hermes --resume','Session:','Duration:','Messages:','Hermes Agent v','Nous Research','/home/']):
                        continue
                    clean.append(text)
            else:
                clean.append(stripped)
    result = '\n'.join(l for l in clean if len(l) > 1)
    return result or "(Empty response)"


def _call_hermes_direct(message: str, files: list = None) -> str:
    """Call Hermes via CLI subprocess (fallback when API server is unavailable)."""
    prompt = message
    if files:
        file_info = []
        image_files = []
        for f in files:
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                # Images can't be processed in CLI mode — track them separately
                image_files.append(f.name)
            else:
                try:
                    content = f.read_text(errors="replace")
                    file_info.append(f"File: {f.name}\n```\n{content[:5000]}\n```\n")
                except Exception:
                    file_info.append(f"File: {f.name} (could not read)\n")
        if image_files:
            prompt += ("\n\nNote: the following image file(s) cannot be processed in CLI mode: "
                       + ", ".join(image_files) + ". Please re-send with the gateway running for image support.")
        if file_info:
            prompt = "\n\n".join(file_info) + "\n\nUser message: " + message
        else:
            prompt = message
    try:
        result = subprocess.run(
            [str(HERMES_BIN), "chat", "-q", prompt],
            capture_output=True, text=True, timeout=300,
            cwd=str(Path.home()),
            env={**os.environ, "NO_COLOR": "1"},
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip() or "(No response)"
        import re as _re
        output = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        output = _re.sub(r'\x1b\].*?\x07', '', output)
        return _clean_cli_output(output)
    except subprocess.TimeoutExpired:
        return "(Response timed out after 5 minutes)"
    except Exception as e:
        return f"(Error calling Hermes: {e})"


def _call_api_server(messages: list, session_id: str) -> str:
    """Call Hermes via its OpenAI-compatible API server."""
    import urllib.request, urllib.error
    payload = {"model": "hermes-agent", "messages": messages, "stream": False}
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("HERMES_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        f"{HERMES_API_URL}/v1/chat/completions",
        data=json.dumps(payload).encode(), headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode())
            return result["choices"][0]["message"]["content"]
    except Exception as e:
        raise Exception(f"API server error: {e}")


def _check_api_server() -> bool:
    """Check if Hermes API server is reachable."""
    try:
        import urllib.request
        req = urllib.request.Request(f"{HERMES_API_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_or_create_chat_session(session_id=None):
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    if session_id not in chat_sessions:
        chat_sessions[session_id] = {
            "id": session_id, "messages": [],
            "created": datetime.now().isoformat(),
            "title": "New Chat",
            "updated": datetime.now().isoformat(),
        }
    return chat_sessions[session_id]


@app.route("/api/chat", methods=["POST"])
@require_token
@rate_limit
def api_chat():
    data = request.get_json()
    message = data.get("message", "").strip()
    session_id = data.get("session_id")
    if not message:
        return jsonify({"error": "Message is required"}), 400
    sess = _get_or_create_chat_session(session_id)
    sid = sess["id"]
    files = []
    for ref in (data.get("files") or []):
        fpath = UPLOAD_FOLDER / ref
        if fpath.exists():
            files.append(fpath)
    user_msg = {"role": "user", "content": message,
                "files": [f.name for f in files], "timestamp": datetime.now().isoformat()}
    sess["messages"].append(user_msg)
    # Auto-title from first user message
    if len(sess["messages"]) == 1 and sess.get("title") == "New Chat":
        sess["title"] = message[:60] + ("..." if len(message) > 60 else "")
    sess["updated"] = datetime.now().isoformat()
    try:
        if _check_api_server():
            api_msgs = []
            for m in sess["messages"]:
                api_msgs.append({"role": m["role"], "content": m["content"]})
            response_text = _call_api_server(api_msgs, sid)
        else:
            response_text = _call_hermes_direct(message, files)
    except Exception as e:
        response_text = f"Error: {e}"
    assistant_msg = {"role": "assistant", "content": response_text,
                     "timestamp": datetime.now().isoformat()}
    sess["messages"].append(assistant_msg)
    sess["updated"] = datetime.now().isoformat()
    _save_session(sid)
    return jsonify({"session_id": sid, "response": response_text,
                     "message_count": len(sess["messages"]), "title": sess.get("title", "")})


@app.route("/api/upload", methods=["POST"])
@require_token
@rate_limit
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No filename"}), 400
    f.seek(0, 2); size = f.tell(); f.seek(0)
    if size > MAX_UPLOAD_SIZE:
        return jsonify({"error": f"Too large (max {MAX_UPLOAD_SIZE//(1024*1024)}MB)"}), 400
    safe = secure_filename(f.filename) or "file"
    unique = f"{uuid.uuid4().hex[:8]}_{safe}"
    (UPLOAD_FOLDER / unique).write_bytes(f.read())
    return jsonify({"name": safe, "stored_as": unique, "size": size,
                     "type": f.content_type, "url": f"/uploads/{unique}"})


@app.route("/api/upload/base64", methods=["POST"])
@require_token
@rate_limit
def api_upload_base64():
    """Accept a base64-encoded image (from clipboard paste) and save it."""
    data = request.get_json() or {}
    b64 = data.get("data", "")
    if not b64:
        return jsonify({"error": "No data"}), 400
    # Strip data URL prefix if present
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        import base64
        img_bytes = base64.b64decode(b64)
    except Exception:
        return jsonify({"error": "Invalid base64"}), 400
    if len(img_bytes) > MAX_UPLOAD_SIZE:
        return jsonify({"error": f"Too large (max {MAX_UPLOAD_SIZE//(1024*1024)}MB)"}), 400
    ext = data.get("ext", "png")
    unique = f"{uuid.uuid4().hex[:8]}_clipboard.{ext}"
    (UPLOAD_FOLDER / unique).write_bytes(img_bytes)
    return jsonify({"name": f"clipboard.{ext}", "stored_as": unique,
                     "size": len(img_bytes), "type": f"image/{ext}",
                     "url": f"/uploads/{unique}"})


@app.route("/uploads/<path:filename>")
@require_token
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_FOLDER), filename)


@app.route("/api/chat/sessions", methods=["GET"])
@require_token
def api_chat_sessions():
    sessions = []
    for sid, s in chat_sessions.items():
        sessions.append({
            "id": s["id"],
            "title": s.get("title", "Untitled"),
            "message_count": len(s["messages"]),
            "created": s["created"],
            "updated": s.get("updated", s["created"]),
            "last_message": s["messages"][-1]["content"][:100] if s["messages"] else "",
        })
    # Sort by updated desc
    sessions.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return jsonify({"sessions": sessions})


@app.route("/api/chat/sessions/<session_id>/messages", methods=["GET"])
@require_token
def api_chat_messages(session_id):
    if session_id not in chat_sessions:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"messages": chat_sessions[session_id]["messages"],
                     "title": chat_sessions[session_id].get("title", "")})


@app.route("/api/chat/sessions/<session_id>/rename", methods=["POST"])
@require_token
def api_chat_rename(session_id):
    if session_id not in chat_sessions:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    new_title = data.get("title", "").strip()
    if new_title:
        chat_sessions[session_id]["title"] = new_title
        _save_session(session_id)
    return jsonify({"ok": True, "title": chat_sessions[session_id].get("title", "")})


@app.route("/api/chat/sessions/<session_id>/delete", methods=["POST"])
@require_token
def api_chat_delete(session_id):
    if session_id not in chat_sessions:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    _delete_session_from_disk(session_id)
    return jsonify({"ok": True})


@app.route("/api/chat/sessions/<session_id>/clear", methods=["POST"])
@require_token
def api_chat_clear(session_id):
    if session_id not in chat_sessions:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    chat_sessions[session_id]["messages"] = []
    _save_session(session_id)
    return jsonify({"ok": True})


@app.route("/api/chat/status", methods=["GET"])
@require_token
def api_chat_status():
    return jsonify({
        "api_server": _check_api_server(),
        "api_url": HERMES_API_URL,
    })


# ===================================================================
# Static catch-all (serve index.html for SPA routing)
# ===================================================================

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def catch_all(path):
    """Serve the React SPA index.html for non-API routes."""
    if path.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    index = Path(__file__).parent / "templates" / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return jsonify({"error": "Frontend not built yet"}), 404


# ===================================================================
# Main
# ===================================================================

if __name__ == "__main__":
    print("[Hermes Admin Panel] ERROR: do not run this file directly.")
    print("[Hermes Admin Panel] Use start.sh to run in production, or DEV=1 ./start.sh for development.")
    exit(1)
