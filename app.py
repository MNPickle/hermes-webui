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
import hashlib
import shlex
import shutil
import mimetypes
import subprocess
import signal
import time
import logging
import tempfile
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from functools import wraps
from contextlib import contextmanager
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values, load_dotenv, set_key, unset_key
from flask import Flask, g, has_request_context, jsonify, request, send_from_directory
from flask_cors import CORS
import uuid
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
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
APP_ROOT = Path(__file__).resolve().parent
PROFILE_OVERRIDE = ContextVar("PROFILE_OVERRIDE", default="")


def _load_runtime_env() -> Path:
    """Load repo-local runtime env without overriding real process env."""
    repo_env_path = APP_ROOT / ".env"
    if repo_env_path.exists():
        load_dotenv(repo_env_path, override=False)
        logger.info("Loaded runtime env from %s", repo_env_path)
    else:
        logger.info("No runtime .env found at %s", repo_env_path)
    return repo_env_path


REPO_ENV_PATH = _load_runtime_env()


def _repo_env_values() -> dict:
    if REPO_ENV_PATH.exists():
        return {
            key: value for key, value in dotenv_values(str(REPO_ENV_PATH)).items()
            if value is not None
        }
    return {}


def _hermes_env_values() -> dict:
    env_path = _selected_env_path() if "_selected_env_path" in globals() else ENV_PATH
    if env_path.exists():
        return {
            key: value for key, value in dotenv_values(str(env_path)).items()
            if value is not None
        }
    return {}


def _runtime_env_value(key: str, default: str = "", allow_repo_env: bool = True) -> str:
    value = os.environ.get(key)
    if value not in (None, ""):
        return value
    if allow_repo_env:
        repo_value = _repo_env_values().get(key)
        if repo_value not in (None, ""):
            return str(repo_value)
    hermes_env_path = globals().get("ENV_PATH")
    if hermes_env_path:
        hermes_value = _hermes_env_values().get(key)
        if hermes_value not in (None, ""):
            return str(hermes_value)
    return default


def _runtime_env_source(key: str, allow_repo_env: bool = True) -> str:
    value = os.environ.get(key)
    if value not in (None, ""):
        return "process_env"
    if allow_repo_env:
        repo_value = _repo_env_values().get(key)
        if repo_value not in (None, ""):
            return "repo_env"
    hermes_env_path = globals().get("ENV_PATH")
    if hermes_env_path:
        hermes_value = _hermes_env_values().get(key)
        if hermes_value not in (None, ""):
            return "hermes_env"
    return ""


def _resolve_runtime_template(value: str) -> str:
    raw_value = str(value or "")
    if not raw_value:
        return ""

    def replace_env(match: re.Match[str]) -> str:
        env_key = str(match.group(1) or "").strip()
        return _runtime_env_value(env_key, "") if env_key else ""

    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace_env, raw_value)


def _effective_hermes_api_url(default: str = "http://127.0.0.1:8642") -> str:
    explicit = _runtime_env_value("HERMES_API_URL", "").strip()
    if explicit:
        return explicit

    host = _runtime_env_value("API_SERVER_HOST", "").strip() or "127.0.0.1"
    port = _runtime_env_value("API_SERVER_PORT", "").strip()
    if not port:
        return default
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"

HERMES_HOME = Path.home() / ".hermes"
HERMES_REPO_DIR = HERMES_HOME / "hermes-agent"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
SKILLS_DIR = HERMES_HOME / "skills"
HERMES_PROFILES_DIR = HERMES_HOME / "profiles"
WEBUI_PROFILE_STATE_PATH = APP_ROOT / "run" / "webui_profile"


def _normalize_hermes_profile_name(name: str | None) -> str:
    normalized = str(name or "").strip()
    return normalized or "default"


def _root_active_profile_name() -> str:
    active_profile_path = HERMES_HOME / "active_profile"
    try:
        return _normalize_hermes_profile_name(active_profile_path.read_text(encoding="utf-8").strip())
    except Exception:
        return "default"


def _available_hermes_profile_names() -> list[str]:
    names = ["default"]
    try:
        if HERMES_PROFILES_DIR.exists():
            names.extend(
                sorted(
                    entry.name
                    for entry in HERMES_PROFILES_DIR.iterdir()
                    if entry.is_dir() and entry.name.strip()
                )
            )
    except Exception:
        pass
    deduped = []
    for name in names:
        normalized = _normalize_hermes_profile_name(name)
        if normalized not in deduped:
            deduped.append(normalized)
    return deduped


def _profile_home(profile_name: str | None = None) -> Path:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized == "default":
        return HERMES_HOME
    return HERMES_PROFILES_DIR / normalized


def _selected_hermes_profile_name() -> str:
    override_raw = str(PROFILE_OVERRIDE.get() or "").strip()
    if override_raw:
        override = _normalize_hermes_profile_name(override_raw)
        if override in _available_hermes_profile_names():
            return override
    try:
        if WEBUI_PROFILE_STATE_PATH.exists():
            stored = _normalize_hermes_profile_name(WEBUI_PROFILE_STATE_PATH.read_text(encoding="utf-8").strip())
            if stored in _available_hermes_profile_names():
                return stored
    except Exception:
        pass
    fallback = _root_active_profile_name()
    if fallback in _available_hermes_profile_names():
        return fallback
    return "default"


def _set_selected_hermes_profile_name(profile_name: str) -> str:
    normalized = _normalize_hermes_profile_name(profile_name)
    available = _available_hermes_profile_names()
    if normalized not in available:
        raise ValueError(f"Unknown Hermes profile: {normalized}")
    WEBUI_PROFILE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEBUI_PROFILE_STATE_PATH.write_text(normalized, encoding="utf-8")
    return normalized


@contextmanager
def _scoped_profile_override(profile_name: str | None):
    raw_profile = str(profile_name or "").strip()
    normalized = _normalize_hermes_profile_name(raw_profile) if raw_profile else ""
    if normalized and normalized not in _available_hermes_profile_names():
        normalized = ""
    token = PROFILE_OVERRIDE.set(normalized)
    try:
        yield normalized or _selected_hermes_profile_name()
    finally:
        PROFILE_OVERRIDE.reset(token)


def _selected_hermes_home() -> Path:
    if _selected_hermes_profile_name() == "default":
        return HERMES_HOME
    return _profile_home(_selected_hermes_profile_name())


def _selected_config_path() -> Path:
    if _selected_hermes_profile_name() == "default":
        return CONFIG_PATH
    return _selected_hermes_home() / "config.yaml"


def _selected_env_path() -> Path:
    if _selected_hermes_profile_name() == "default":
        return ENV_PATH
    return _selected_hermes_home() / ".env"


def _env_path_for_profile(profile_name: str | None = None) -> Path:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized == "default":
        return ENV_PATH
    return _profile_home(normalized) / ".env"


def _api_url_port(api_url: str | None) -> str:
    raw = str(api_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    try:
        if parsed.port:
            return str(parsed.port)
    except ValueError:
        return ""
    if parsed.scheme == "https":
        return "443"
    if parsed.scheme == "http":
        return "80"
    return ""


def _api_token_repo_keys_for_port(port: str | None) -> list[str]:
    normalized = str(port or "").strip()
    if not normalized:
        return ["HERMES_API_TOKEN", "HERMES_API_KEY", "API_SERVER_TOKEN", "API_SERVER_KEY"]
    return [
        f"HERMES_API_TOKEN_PORT_{normalized}",
        f"HERMES_API_KEY_PORT_{normalized}",
        f"API_SERVER_TOKEN_PORT_{normalized}",
        f"API_SERVER_KEY_PORT_{normalized}",
        "HERMES_API_TOKEN",
        "HERMES_API_KEY",
        "API_SERVER_TOKEN",
        "API_SERVER_KEY",
    ]


def _profile_api_gateway_url(profile_name: str | None = None) -> str:
    with _scoped_profile_override(profile_name):
        hermes_env = _hermes_env_values()
        explicit = str(hermes_env.get("HERMES_API_URL") or "").strip()
        if explicit:
            return explicit
        host = str(hermes_env.get("API_SERVER_HOST") or "").strip() or "127.0.0.1"
        port = str(hermes_env.get("API_SERVER_PORT") or "").strip()
        if port:
            if host in ("0.0.0.0", "::", "[::]"):
                host = "127.0.0.1"
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            return f"http://{host}:{port}"
    return _effective_hermes_api_url(DEFAULT_HERMES_API_URL)


def _profile_primary_api_url(profile_name: str | None = None) -> str:
    with _scoped_profile_override(profile_name):
        target = _resolve_api_target(prefer_vision=False) or {}
        target_url = str(target.get("base_url") or "").strip()
        if target_url:
            return target_url
    return _profile_api_gateway_url(profile_name)


def _selected_skills_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return SKILLS_DIR
    return _selected_hermes_home() / "skills"


def _selected_sessions_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return SESSIONS_DIR
    return _selected_hermes_home() / "sessions"


def _selected_backup_dir() -> Path:
    if _selected_hermes_profile_name() == "default":
        return BACKUP_DIR
    return _selected_hermes_home() / "backups"


def _selected_gateway_pid_path() -> Path:
    return _selected_hermes_home() / "gateway.pid"


def _selected_gateway_log_path() -> Path:
    return _selected_hermes_home() / "logs" / "gateway.log"


def _selected_hermes_profile_payload() -> dict:
    selected = _selected_hermes_profile_name()
    root_active = _root_active_profile_name()
    return {
        "selected": selected,
        "profiles": [
            {
                "name": name,
                "home": str(_profile_home(name)),
                "is_default": name == "default",
                "is_root_active": name == root_active,
            }
            for name in _available_hermes_profile_names()
        ],
        "paths": {
            "home": str(_selected_hermes_home()),
            "config": str(_selected_config_path()),
            "env": str(_selected_env_path()),
            "skills": str(_selected_skills_dir()),
            "sessions": str(_selected_sessions_dir()),
        },
    }


def _profile_api_token_metadata(profile_name: str | None = None) -> dict:
    normalized = _normalize_hermes_profile_name(profile_name)
    if normalized not in _available_hermes_profile_names():
        raise ValueError(f"Unknown Hermes profile: {normalized}")
    api_url = _profile_api_gateway_url(normalized)
    port = _api_url_port(api_url)
    env_path = REPO_ENV_PATH
    raw = _repo_env_values()
    token_key = ""
    token_value = ""
    for key in _api_token_repo_keys_for_port(port):
        value = str(raw.get(key) or "").strip()
        if value:
            token_key = key
            token_value = value
            break
    return {
        "profile": normalized,
        "api_url": api_url,
        "api_port": port,
        "env_path": str(env_path),
        "token_key": token_key,
        "has_token": bool(token_value),
        "masked_token": _mask_value(token_key or "HERMES_API_TOKEN", token_value) if token_value else "",
    }

# Hermes executable - try current install locations with fallback
def _find_hermes_bin():
    # Align with the current Hermes installer layout:
    # ~/.hermes/hermes-agent/venv/bin/hermes (plus ~/.local/bin/hermes on PATH).
    # Keep the legacy ~/.hermes/.venv layout as a last-resort fallback.
    import shutil as _shutil
    candidates = [
        os.environ.get("HERMES_WEBUI_HERMES_BIN"),
        os.environ.get("HERMES_BIN"),
        HERMES_REPO_DIR / "venv" / "bin" / "hermes",
        Path.home() / ".local" / "bin" / "hermes",
        _shutil.which("hermes"),
        HERMES_HOME / ".venv" / "bin" / "hermes",
    ]
    for path in candidates:
        candidate = Path(path).expanduser() if path else None
        if candidate and candidate.exists():
            return candidate
    return HERMES_REPO_DIR / "venv" / "bin" / "hermes"

HERMES_BIN = _find_hermes_bin()
SESSIONS_DIR = HERMES_HOME / "sessions"
UPLOADS_DIR = Path.home() / "hermes-web-ui" / "uploads"
UPLOAD_FOLDER = APP_ROOT / "uploads"
UPLOAD_FOLDER.mkdir(exist_ok=True)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
DEFAULT_MAX_REQUEST_BODY_SIZE = (MAX_UPLOAD_SIZE * 3) // 2 + (2 * 1024 * 1024)
MAX_REQUEST_BODY_SIZE = max(
    MAX_UPLOAD_SIZE,
    int(_runtime_env_value("HERMES_WEBUI_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BODY_SIZE))),
)
REQUEST_LOG_SLOW_MS = max(250, int(_runtime_env_value("HERMES_WEBUI_SLOW_REQUEST_MS", "1500")))
UPLOAD_STREAM_CHUNK_SIZE = 1024 * 1024
DEFAULT_HERMES_API_URL = "http://127.0.0.1:8642"
HERMES_API_URL = _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
BACKUP_DIR = HERMES_HOME / "backups"

# Chat session storage (persisted to disk)
CHAT_DATA_DIR = APP_ROOT / "chat_data"
CHAT_DATA_DIR.mkdir(exist_ok=True)
CHAT_DATA_LOCK = CHAT_DATA_DIR / ".lock"
CHAT_FOLDERS_PATH = CHAT_DATA_DIR / ".folders.json"
CHAT_REQUEST_DIR = APP_ROOT / "run" / "chat_requests"
CHAT_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
CHAT_REQUEST_TIMEOUT = int(_runtime_env_value("HERMES_CHAT_TIMEOUT", "300"))
CHAT_PERSIST_DEBUG_TRACE = _runtime_env_value("HERMES_WEBUI_PERSIST_DEBUG_TRACE", "0").strip().lower() not in {"", "0", "false", "no", "off"}
CHAT_SERVER_TIMEOUT = int(
    _runtime_env_value(
        "GUNICORN_TIMEOUT",
        str(CHAT_REQUEST_TIMEOUT + int(_runtime_env_value("GUNICORN_TIMEOUT_HEADROOM", "90"))),
    )
)
CHAT_CANCEL_POLL_INTERVAL = 0.25
CHAT_CANCEL_GRACE_SECONDS = 5.0
CHAT_CONTEXT_SOURCE_DOC_LIMIT = 64 * 1024
CHAT_CONTEXT_SOURCE_DOC_TOTAL_LIMIT = 128 * 1024
CHAT_FOLDER_SOURCE_DIR = CHAT_DATA_DIR / "sources"
CHAT_FOLDER_SOURCE_DIR.mkdir(exist_ok=True)
CRON_JOBS_PATH = APP_ROOT / "run" / "cron_jobs.json"
CRON_JOB_MARKER = "hermes-web-ui-job"
HERMES_UPDATE_CACHE_SECONDS = max(
    60,
    int(_runtime_env_value("HERMES_WEBUI_UPDATE_CACHE_SECONDS", "600")),
)
HERMES_UPDATE_LOG_LINE_LIMIT = max(
    80,
    int(_runtime_env_value("HERMES_WEBUI_UPDATE_LOG_LINES", "400")),
)

chat_sessions: dict = {}  # runtime cache: sid -> session dict
chat_folders: dict = {}  # runtime cache: folder_id -> folder dict
hermes_update_cache: dict = {}  # cache_key -> {"ts": float, "payload": dict}
hermes_update_cache_lock = threading.Lock()
hermes_update_runtime_lock = threading.Lock()
hermes_update_runtime = {
    "status": "",
    "started_at": "",
    "finished_at": "",
    "returncode": None,
    "error": "",
    "summary": "",
    "logs": [],
    "install_key": "",
    "installed_version_before": "",
    "installed_version_after": "",
}

CHAT_TRANSPORT_CLI = "cli"
CHAT_TRANSPORT_API = "api"
CHAT_TRANSPORT_AUTO = "auto"
CHAT_CONTINUITY_HERMES = "hermes_resume"
CHAT_CONTINUITY_LOCAL = "local_replay"
CHAT_CONTINUITY_LIMITED = "cli_without_resume"
AUXILIARY_MODEL_KEYS = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "summarization",
    "embedding",
    "tts",
    "stt",
)
MODEL_ROLE_LABELS = {
    "primary": "Primary Chat",
    "fallback": "Fallback Chat",
    "vision": "Vision",
}
PROVIDER_TYPE_LABELS = {
    "auto": "Generic OpenAI-Compatible",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "openai-codex": "OpenAI",
    "azure": "Azure OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "google": "Google",
    "gemini": "Google",
    "mistral": "Mistral",
    "together": "Together",
    "fireworks": "Fireworks",
    "deepseek": "DeepSeek",
    "cohere": "Cohere",
}
PROVIDER_DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "openai-codex": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "deepseek": "https://api.deepseek.com/v1",
}
PROVIDER_ENV_KEY_MAP = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
}
PROVIDER_PRESETS = [
    {
        "id": "openrouter",
        "label": "OpenRouter",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "name": "openrouter",
        "intro": "Use OpenRouter as an OpenAI-compatible provider profile.",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "name": "openai",
        "intro": "Use OpenAI directly as a provider profile.",
    },
    {
        "id": "local",
        "label": "Local API",
        "provider": "auto",
        "base_url": "http://127.0.0.1:8000/v1",
        "name": "local-api",
        "intro": "Use a local OpenAI-compatible server as a provider profile.",
    },
]
SKILL_INSTALL_ERROR_MARKERS = (
    "error:",
    "could not fetch",
    "failed to fetch",
    "failed to install",
    "not a valid skill",
    "no skill found",
)
SKILL_SOURCE_METADATA_FILENAME = ".hermes-webui-source.json"
INTEGRATION_SECTION_LABELS = {
    "discord": "Discord",
    "whatsapp": "WhatsApp",
    "telegram": "Telegram",
    "slack": "Slack",
    "matrix": "Matrix",
    "webhook": "Webhook",
}
INTEGRATION_SECTION_ORDER = tuple(INTEGRATION_SECTION_LABELS.keys())
INTEGRATION_CONFIG_TEMPLATES = {
    "discord": {
        "require_mention": True,
        "free_response_channels": "",
        "auto_thread": True,
    },
    "whatsapp": {},
    "telegram": {},
    "slack": {},
    "matrix": {},
    "webhook": {
        "url": "",
    },
}
INTEGRATION_ENV_TEMPLATES = {
    "discord": (
        {
            "key": "DISCORD_TOKEN",
            "group": "Channel",
            "label": "Discord Token",
            "description": "Bot token Hermes uses to connect to Discord.",
            "secret": True,
        },
    ),
    "telegram": (
        {
            "key": "TELEGRAM_BOT_TOKEN",
            "group": "Channel",
            "label": "Telegram Bot Token",
            "description": "Bot token Hermes uses to connect to Telegram.",
            "secret": True,
        },
    ),
    "slack": (
        {
            "key": "SLACK_BOT_TOKEN",
            "group": "Channel",
            "label": "Slack Bot Token",
            "description": "Bot token Hermes uses to connect to Slack.",
            "secret": True,
        },
    ),
    "matrix": (
        {
            "key": "MATRIX_ACCESS_TOKEN",
            "group": "Channel",
            "label": "Matrix Access Token",
            "description": "Access token Hermes uses to connect to Matrix.",
            "secret": True,
        },
    ),
}
AGENT_REASONING_EFFORT_OPTIONS = (
    "",
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "minimal",
)
ENV_GROUP_HELP = {
    "Provider": "Provider variables are API keys and optional endpoint overrides used by model providers and memory search. For standard OpenAI use, you usually only need OPENAI_API_KEY.",
    "Channel": "Channel variables are only needed when an integration or skill specifically asks for them. If you are not setting up Discord, WhatsApp, Slack, or another messaging bridge, you can usually leave this group alone.",
    "System": "System variables tune how Hermes itself behaves. Only change these when you are intentionally customizing the runtime.",
}
ENV_VAR_PRESETS = {
    "OPENAI_API_KEY": {
        "group": "Provider",
        "label": "OpenAI API Key",
        "description": "Used for OpenAI provider profiles and optional OpenAI-backed Hermes memory search.",
        "secret": True,
        "recommended": True,
        "starter_pack_item": "memory",
    },
    "OPENAI_BASE_URL": {
        "group": "Provider",
        "label": "OpenAI Base URL",
        "description": "Optional override for custom OpenAI-compatible gateways. Leave this unset for normal OpenAI API use.",
        "default_value": "https://api.openai.com/v1",
    },
    "OPENROUTER_API_KEY": {
        "group": "Provider",
        "label": "OpenRouter API Key",
        "description": "Lets Hermes and provider profiles call OpenRouter.",
        "secret": True,
        "recommended": True,
    },
    "ANTHROPIC_API_KEY": {
        "group": "Provider",
        "label": "Anthropic API Key",
        "description": "Lets Hermes call Anthropic models directly.",
        "secret": True,
    },
    "GOOGLE_API_KEY": {
        "group": "Provider",
        "label": "Google API Key",
        "description": "Used by some Google and Gemini provider flows. Google Workspace skills may still need OAuth files separately.",
        "secret": True,
    },
    "GROQ_API_KEY": {
        "group": "Provider",
        "label": "Groq API Key",
        "description": "Lets Hermes call Groq-hosted models.",
        "secret": True,
    },
    "DISCORD_TOKEN": {
        "group": "Channel",
        "label": "Discord Token",
        "description": "Only needed if your Discord integration or skill specifically asks for it.",
        "secret": True,
    },
    "SLACK_BOT_TOKEN": {
        "group": "Channel",
        "label": "Slack Bot Token",
        "description": "Only needed if you connect Hermes to Slack via a bot token.",
        "secret": True,
    },
    "TELEGRAM_BOT_TOKEN": {
        "group": "Channel",
        "label": "Telegram Bot Token",
        "description": "Only needed if you connect Hermes to Telegram.",
        "secret": True,
    },
    "HERMES_API_URL": {
        "group": "System",
        "label": "Hermes API URL",
        "description": "Overrides the URL the web UI uses for API replay.",
        "default_value": "http://127.0.0.1:8642",
    },
    "HERMES_CHAT_TIMEOUT": {
        "group": "System",
        "label": "Chat Timeout",
        "description": "How long the web UI waits for a Hermes chat turn before timing out.",
        "default_value": "300",
    },
}
STARTER_PACK_SKILL_GROUPS = (
    {
        "id": "google_workspace",
        "label": "Google Workspace",
        "terms": ("google-workspace", "gog"),
        "description": "Gmail, Calendar, Drive, Docs, and Sheets helpers.",
        "query": "gog",
        "setup_notes": [
            "Google Workspace usually needs OAuth setup after install.",
            "Expect to add Google client credentials and authorize the account before Gmail, Calendar, Drive, Sheets, or Docs actions will work.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/gog",
                "label": "Gog",
                "source": "skills.sh",
                "description": "Google Workspace helpers from the Clawdis skill set.",
                "recommended": True,
            },
        ),
    },
    {
        "id": "summarize",
        "label": "Summarize",
        "terms": ("summarize",),
        "description": "Recommended summaries and transcripts for URLs, podcasts, videos, and local files.",
        "query": "summarize",
        "setup_notes": [
            "The summarize skill may need the local summarize CLI after the skill files are installed.",
            "If it still is not ready, install the tool with `brew install steipete/tap/summarize`.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/summarize",
                "label": "Summarize",
                "source": "skills.sh",
                "description": "Summaries and transcripts for URLs, videos, and local files.",
                "recommended": True,
            },
        ),
    },
    {
        "id": "weather",
        "label": "Weather",
        "terms": ("weather",),
        "description": "Quick forecast and current weather lookups.",
        "query": "weather",
        "setup_notes": [
            "The recommended weather skill works without an API key.",
        ],
        "install_candidates": (
            {
                "identifier": "skills-sh/steipete/clawdis/weather",
                "label": "weather",
                "source": "skills.sh",
                "description": "Weather helpers from the Clawdis skill set.",
                "recommended": True,
            },
        ),
    },
)
CAPABILITY_RECOMMENDED_ORDER = (
    "skill",
    "integration",
    "agent_preset",
)
CAPABILITY_ARCHITECTURE_RULES = [
    "Skills are the primary Hermes extension mechanism.",
    "Integrations store reusable connection and config for external systems.",
    "Agent Presets compose model roles, skills, and integrations into a reusable working mode.",
    "Every capability type must support a draft preview before any files or config are written.",
]
CAPABILITY_MVP_SCOPE = [
    "Phase 1 ships Create Skill end-to-end first.",
    "Phase 2 adds Create Integration on top of the same preview-and-approve flow.",
    "Phase 3 adds Create Agent Preset that composes existing models, skills, and integrations.",
]
CAPABILITY_IMPLEMENTATION_ORDER = [
    "Create Skill",
    "Create Integration",
    "Create Agent Preset",
]
CAPABILITY_TYPE_DEFINITIONS = {
    "skill": {
        "id": "skill",
        "label": "Skill",
        "phase": "Phase 1",
        "status": "active",
        "summary": "Primary Hermes extension mechanism stored as a skill folder with SKILL.md and optional helper assets.",
        "data_model": [
            "identity: name, slug, category, description",
            "instructions: markdown body for when and how the skill should be used",
            "setup: env vars, credential files, and required commands",
            "assets: optional scripts/ and references/ folders",
        ],
        "ui_flow": [
            "Choose Skill",
            "Fill draft fields",
            "Preview generated files and readiness blockers",
            "Approve and write the skill folder",
        ],
        "layout": [
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/", "purpose": "Skill root directory"},
            {"kind": "file", "path": "~/.hermes/skills/<slug>/SKILL.md", "purpose": "Skill instructions and metadata"},
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/scripts/", "purpose": "Optional helper scripts"},
            {"kind": "folder", "path": "~/.hermes/skills/<slug>/references/", "purpose": "Optional reference material"},
        ],
        "mvp_scope": [
            "Create a new skill from the UI",
            "Preview generated SKILL.md before write",
            "Feed setup metadata into the existing readiness and env-var flows",
        ],
    },
    "integration": {
        "id": "integration",
        "label": "Integration",
        "phase": "Phase 2",
        "status": "active",
        "summary": "Reusable connection and config blocks for Discord, Slack, webhooks, and other external systems.",
        "data_model": [
            "identity: name, slug, provider, label",
            "config: structured JSON config block",
            "secrets: env vars and credential references stored outside the visible config",
            "readiness: configuration completeness and transport/runtime notes",
        ],
        "ui_flow": [
            "Choose integration kind",
            "Fill config and secret references",
            "Preview config diff and readiness",
            "Approve and write Hermes config",
        ],
        "layout": [
            {"kind": "file", "path": "~/.hermes/config.yaml", "purpose": "Top-level integration sections and legacy channels config"},
            {"kind": "file", "path": "~/.hermes/.env", "purpose": "Secrets and token storage when env vars are used"},
        ],
        "mvp_scope": [
            "Create top-level integration blocks that match the current Apps & Integrations UI",
            "Reuse existing env-var editing for secret setup",
            "Preview config changes before save",
        ],
    },
    "agent_preset": {
        "id": "agent_preset",
        "label": "Agent Preset",
        "phase": "Phase 3",
        "status": "active",
        "summary": "Reusable agent working modes that compose model roles, enabled skills, and connected integrations.",
        "data_model": [
            "identity: name, slug, description",
            "model composition: primary, fallback, and vision role bindings",
            "capability composition: selected skills and integrations",
            "agent settings: personality and execution defaults",
        ],
        "ui_flow": [
            "Pick model-role targets",
            "Select skills and integrations",
            "Preview the composed preset",
            "Approve and write the preset config",
        ],
        "layout": [
            {"kind": "file", "path": "~/.hermes/config.yaml", "purpose": "Future agent preset storage alongside Hermes agent config"},
        ],
        "mvp_scope": [
            "Compose existing model roles, skills, and integrations into reusable presets",
            "Preview final role and capability bindings before save",
        ],
    },
}
VISION_REFERENCE_HINT_RE = re.compile(
    r"\b("
    r"screenshot|screen|image|photo|picture|diagram|ui"
    r")\b.*\b("
    r"earlier|previous|prior|before|above|same|that|those|attached|last"
    r")\b"
    r"|"
    r"\b("
    r"earlier|previous|prior|before|above|same|that|those|attached|last"
    r")\b.*\b("
    r"screenshot|screen|image|photo|picture|diagram|ui"
    r")\b",
    re.IGNORECASE,
)


@contextmanager
def _chat_data_lock(shared: bool = False):
    """Serialize chat session file access across gunicorn workers."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    CHAT_DATA_LOCK.touch(exist_ok=True)
    with CHAT_DATA_LOCK.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _chat_session_path(session_id: str) -> Path:
    return CHAT_DATA_DIR / f"{secure_filename(session_id)}.json"


def _normalize_transport_preference(value) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in ("", CHAT_TRANSPORT_AUTO):
        return None
    if normalized in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        return normalized
    return None


def _transport_preference_label(value) -> str:
    normalized = _normalize_transport_preference(value)
    if normalized == CHAT_TRANSPORT_CLI:
        return "Hermes CLI"
    if normalized == CHAT_TRANSPORT_API:
        return "API Replay"
    return "Auto"


def _normalize_chat_session(session: dict) -> dict:
    transport_mode = session.get("transport_mode")
    transport_preference = session.get("transport_preference")
    if "transport_preference" not in session and transport_mode in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        # Preserve legacy session behavior by treating existing explicit mode
        # as the preferred transport when no separate preference exists yet.
        transport_preference = transport_mode
    transport_preference = _normalize_transport_preference(transport_preference)
    continuity_mode = session.get("continuity_mode")
    if transport_mode == CHAT_TRANSPORT_CLI and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_HERMES if session.get("hermes_session_id") else CHAT_CONTINUITY_LIMITED
    elif transport_mode == CHAT_TRANSPORT_API and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_LOCAL
    folder_id = session.get("folder_id")
    if not isinstance(folder_id, str):
        folder_id = ""
    profile_name = _normalize_hermes_profile_name(session.get("profile"))
    if not profile_name:
        profile_name = _selected_hermes_profile_name()
    session["transport_mode"] = transport_mode
    session["transport_preference"] = transport_preference
    session["continuity_mode"] = continuity_mode
    session.setdefault("transport_notice", "")
    session["messages"] = _normalize_chat_messages(session.get("messages"))
    session["vision_assets"] = _normalize_vision_assets(session.get("vision_assets"))
    session["segments"] = _normalize_chat_segments(session, profile_name)
    active_segment = _active_chat_segment(session)
    session["profile"] = (active_segment or {}).get("profile") or profile_name
    session["hermes_session_id"] = _segment_hermes_session_id(active_segment)
    session["folder_id"] = folder_id.strip()
    session["workspace_roots"] = _clean_string_list(session.get("workspace_roots"))
    session["source_docs"] = _clean_string_list(session.get("source_docs"))
    segment_map = {segment["id"]: segment for segment in session.get("segments") or []}
    default_segment = active_segment or ((session.get("segments") or [None])[0])
    for index, entry in enumerate(session["messages"]):
        segment = segment_map.get(str(entry.get("segment_id") or "").strip())
        if not segment:
            segment = default_segment
            for candidate in session.get("segments") or []:
                if index >= int(candidate.get("start_message_index") or 0):
                    segment = candidate
            if segment:
                entry["segment_id"] = segment["id"]
        if segment:
            entry["segment_index"] = int(segment.get("index") or 1)
            entry["profile"] = _normalize_hermes_profile_name(entry.get("profile")) or segment.get("profile") or session["profile"]
            transport = str(entry.get("transport") or "").strip().lower()
            if transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                transport = str(segment.get("transport") or session.get("transport_mode") or "").strip().lower()
            if transport in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                entry["transport"] = transport
    return session


def _clean_hermes_session_id(value) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_chat_segments(session: dict, fallback_profile: str | None = None) -> list[dict]:
    raw_segments = session.get("segments")
    normalized = []
    base_profile = _normalize_hermes_profile_name(fallback_profile or session.get("profile")) or _selected_hermes_profile_name()
    base_transport = str(session.get("transport_mode") or "").strip().lower()
    legacy_hermes_session_id = _clean_hermes_session_id(session.get("hermes_session_id"))
    raw_active_segment_id = str(session.get("active_segment_id") or "").strip()
    if base_transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        base_transport = ""
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            profile = _normalize_hermes_profile_name(item.get("profile")) or base_profile
            segment_id = str(item.get("id") or f"segment-{len(normalized) + 1}").strip() or f"segment-{len(normalized) + 1}"
            transport = str(item.get("transport") or "").strip().lower()
            if transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
                transport = ""
            start_message_index = item.get("start_message_index")
            if not isinstance(start_message_index, int) or start_message_index < 0:
                start_message_index = 0
            hermes_session_id = _clean_hermes_session_id(item.get("hermes_session_id"))
            if not hermes_session_id and segment_id == raw_active_segment_id:
                hermes_session_id = legacy_hermes_session_id
            normalized.append({
                "id": segment_id,
                "index": len(normalized) + 1,
                "profile": profile,
                "transport": transport,
                "hermes_session_id": hermes_session_id,
                "started_at": str(item.get("started_at") or session.get("created") or "").strip(),
                "start_message_index": start_message_index,
            })
    if not normalized:
        normalized = [{
            "id": "segment-1",
            "index": 1,
            "profile": base_profile,
            "transport": base_transport,
            "hermes_session_id": legacy_hermes_session_id,
            "started_at": str(session.get("created") or "").strip(),
            "start_message_index": 0,
        }]
    active_segment_id = str(session.get("active_segment_id") or "").strip()
    if not any(segment["id"] == active_segment_id for segment in normalized):
        active_segment_id = normalized[-1]["id"]
    session["active_segment_id"] = active_segment_id
    return normalized


def _trim_trailing_empty_chat_segments(session: dict) -> bool:
    segments = session.get("segments") or []
    if len(segments) <= 1:
        return False
    message_count = len(session.get("messages") or [])
    trimmed = False
    while len(segments) > 1:
        last_segment = segments[-1]
        start_message_index = int(last_segment.get("start_message_index") or 0)
        if start_message_index < message_count:
            break
        segments.pop()
        trimmed = True
    if not trimmed:
        return False
    for index, segment in enumerate(segments, start=1):
        segment["index"] = index
    session["segments"] = segments
    session["active_segment_id"] = segments[-1].get("id") or ""
    session["profile"] = segments[-1].get("profile") or session.get("profile")
    session["hermes_session_id"] = _segment_hermes_session_id(segments[-1])
    return True


def _active_chat_segment(session: dict) -> dict | None:
    segments = session.get("segments") or []
    active_segment_id = str(session.get("active_segment_id") or "").strip()
    for segment in segments:
        if segment.get("id") == active_segment_id:
            return segment
    return segments[-1] if segments else None


def _segment_hermes_session_id(segment: dict | None) -> str | None:
    if not isinstance(segment, dict):
        return None
    return _clean_hermes_session_id(segment.get("hermes_session_id"))


def _latest_chat_segment_for_profile(session: dict, profile_name: str) -> dict | None:
    normalized_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    for segment in reversed(session.get("segments") or []):
        if _normalize_hermes_profile_name(segment.get("profile")) == normalized_profile:
            return segment
    return None


def _append_chat_segment(session: dict, profile_name: str, *, transport: str = "") -> dict:
    normalized_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    if "segments" not in session:
        session["segments"] = _normalize_chat_segments(session, normalized_profile)
    active = _active_chat_segment(session)
    clean_transport = str(transport or "").strip().lower()
    if clean_transport not in (CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        clean_transport = ""
    if active and active.get("profile") == normalized_profile:
        if clean_transport and not active.get("transport"):
            active["transport"] = clean_transport
        session["active_segment_id"] = active.get("id")
        session["profile"] = normalized_profile
        session["hermes_session_id"] = _segment_hermes_session_id(active)
        return active
    previous = _latest_chat_segment_for_profile(session, normalized_profile)
    next_index = len(session.get("segments") or []) + 1
    segment = {
        "id": f"segment-{next_index}",
        "index": next_index,
        "profile": normalized_profile,
        "transport": clean_transport,
        "hermes_session_id": _segment_hermes_session_id(previous),
        "started_at": datetime.now().isoformat(),
        "start_message_index": len(session.get("messages") or []),
    }
    session.setdefault("segments", []).append(segment)
    session["active_segment_id"] = segment["id"]
    session["profile"] = normalized_profile
    session["hermes_session_id"] = _segment_hermes_session_id(segment)
    return segment


def _normalize_chat_messages(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []
    normalized = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        entry = copy.deepcopy(message)
        entry["role"] = str(entry.get("role") or "").strip() or "user"
        entry["content"] = str(entry.get("content") or "")
        entry["files"] = _clean_string_list(entry.get("files"))
        entry["attachment_refs"] = _normalize_attachment_refs(entry.get("attachment_refs"))
        sidecar_state = entry.get("sidecar_vision")
        if isinstance(sidecar_state, dict):
            entry["sidecar_vision"] = {
                "used": bool(sidecar_state.get("used")),
                "status": str(sidecar_state.get("status") or "").strip() or ("ok" if sidecar_state.get("used") else ""),
                "asset_ids": _clean_string_list(sidecar_state.get("asset_ids")),
                "summary": str(sidecar_state.get("summary") or "").strip(),
                "analysis_mode": str(sidecar_state.get("analysis_mode") or "").strip(),
                "reanalysis": bool(sidecar_state.get("reanalysis")),
            }
        elif "sidecar_vision" in entry:
            entry.pop("sidecar_vision", None)
        normalized.append(entry)
    return normalized


def _normalize_attachment_refs(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    refs = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        stored_as = secure_filename(str(item.get("stored_as") or "").strip())
        if not stored_as or stored_as in seen:
            continue
        seen.add(stored_as)
        display_name = str(
            item.get("display_name")
            or item.get("name")
            or stored_as
        ).strip() or stored_as
        ref = {
            "stored_as": stored_as,
            "display_name": display_name,
        }
        kind = str(item.get("kind") or "").strip().lower()
        mime_type = str(item.get("mime_type") or "").strip()
        if kind:
            ref["kind"] = kind
        if mime_type:
            ref["mime_type"] = mime_type
        refs.append(ref)
    return refs


def _normalize_vision_assets(values) -> list[dict]:
    if not isinstance(values, list):
        return []
    assets = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "").strip()
        stored_as = secure_filename(str(item.get("stored_as") or "").strip())
        if not asset_id or not stored_as or asset_id in seen:
            continue
        seen.add(asset_id)
        asset = {
            "id": asset_id,
            "stored_as": stored_as,
            "display_name": str(item.get("display_name") or stored_as).strip() or stored_as,
            "mime_type": str(item.get("mime_type") or "").strip(),
            "created_at": str(item.get("created_at") or "").strip(),
            "source_message_timestamp": str(item.get("source_message_timestamp") or "").strip(),
        }
        source_index = item.get("source_message_index")
        if isinstance(source_index, int):
            asset["source_message_index"] = source_index
        last_analysis = item.get("last_analysis")
        if isinstance(last_analysis, dict):
            asset["last_analysis"] = {
                "summary": str(last_analysis.get("summary") or "").strip(),
                "raw_text": str(last_analysis.get("raw_text") or "").strip(),
                "focus": str(last_analysis.get("focus") or "").strip(),
                "analyzed_at": str(last_analysis.get("analyzed_at") or "").strip(),
                "model": str(last_analysis.get("model") or "").strip(),
                "provider": str(last_analysis.get("provider") or "").strip(),
            }
        assets.append(asset)
    return assets


def _clean_string_list(values) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return []
    cleaned = []
    seen = set()
    for item in values:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _normalize_context_path(raw_path: str) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = (APP_ROOT / path)
    try:
        return path.resolve(strict=False)
    except Exception:
        return path.absolute()


def _validated_context_paths(values, *, expect: str) -> tuple[list[str], list[str]]:
    normalized = []
    errors = []
    seen = set()
    for raw_value in _clean_string_list(values):
        path = _normalize_context_path(raw_value)
        path_str = str(path)
        if path_str in seen:
            continue
        if not path.exists():
            errors.append(f"{raw_value} does not exist")
            continue
        if expect == "dir" and not path.is_dir():
            errors.append(f"{raw_value} is not a directory")
            continue
        if expect == "file" and not path.is_file():
            errors.append(f"{raw_value} is not a file")
            continue
        seen.add(path_str)
        normalized.append(path_str)
    return normalized, errors


def _parse_chat_context_update(data: dict) -> tuple[dict, list[str]]:
    folder_id = str(data.get("folder_id") or "").strip()
    if len(folder_id) > 120:
        folder_id = folder_id[:120].rstrip()
    workspace_roots, root_errors = _validated_context_paths(data.get("workspace_roots"), expect="dir")
    source_docs, doc_errors = _validated_context_paths(data.get("source_docs"), expect="file")
    errors = root_errors + doc_errors
    return {
        "folder_id": folder_id,
        "workspace_roots": workspace_roots,
        "source_docs": source_docs,
    }, errors


def _merge_unique_strings(*groups) -> list[str]:
    merged = []
    seen = set()
    for group in groups:
        for item in _clean_string_list(group):
            if item in seen:
                continue
            seen.add(item)
            merged.append(item)
    return merged


def _folder_workspace_roots_for_docs(source_docs) -> list[str]:
    roots = []
    seen = set()
    for source_doc in _clean_string_list(source_docs):
        path = _normalize_context_path(source_doc)
        parent = str(path.parent)
        if parent in seen:
            continue
        seen.add(parent)
        roots.append(parent)
    return roots


def _parse_folder_update(data: dict, existing: dict | None = None) -> tuple[dict, list[str]]:
    existing = existing or {}
    title = str(data.get("title") if "title" in data else existing.get("title") or "").strip()
    if not title:
        title = "Untitled Folder"
    if len(title) > 120:
        title = title[:120].rstrip()

    source_values = data.get("source_docs") if "source_docs" in data else existing.get("source_docs", [])
    source_docs, doc_errors = _validated_context_paths(source_values, expect="file")
    upload_refs = data.get("source_uploads") or []
    for upload_ref in _clean_string_list(upload_refs):
        upload_path = UPLOAD_FOLDER / upload_ref
        if not upload_path.exists() or not upload_path.is_file():
            doc_errors.append(f"{upload_ref} is not an uploaded source file")
            continue
        source_docs.append(str(upload_path.resolve()))
    source_docs = _merge_unique_strings(source_docs)

    workspace_values = data.get("workspace_roots") if "workspace_roots" in data else existing.get("workspace_roots", [])
    workspace_roots, root_errors = _validated_context_paths(workspace_values, expect="dir")
    workspace_roots = _merge_unique_strings(workspace_roots, _folder_workspace_roots_for_docs(source_docs))
    return {
        "title": title,
        "source_docs": source_docs,
        "workspace_roots": workspace_roots,
    }, doc_errors + root_errors


def _folder_title_key(title: str) -> str:
    return str(title or "").strip().casefold()


def _folders_matching_title(title: str, folders: dict | None = None) -> list[dict]:
    folders = folders if folders is not None else _load_all_folders()
    title_key = _folder_title_key(title)
    if not title_key:
        return []
    matches = []
    for folder in folders.values():
        normalized = _normalize_chat_folder(folder)
        if _folder_title_key(normalized.get("title")) == title_key:
            matches.append(normalized)
    matches.sort(key=lambda folder: (
        folder.get("created") or "",
        folder.get("id") or "",
    ))
    return matches


def _unique_folder_for_title(title: str, folders: dict | None = None) -> dict | None:
    matches = _folders_matching_title(title, folders=folders)
    return matches[0] if len(matches) == 1 else None


def _folder_title_conflict(title: str, *, exclude_folder_id: str = "", folders: dict | None = None) -> dict | None:
    folders = folders if folders is not None else _load_all_folders()
    title_key = _folder_title_key(title)
    if not title_key:
        return None
    exclude_folder_id = str(exclude_folder_id or "").strip()
    for folder_id, folder in folders.items():
        if folder_id == exclude_folder_id:
            continue
        normalized = _normalize_chat_folder(folder)
        if _folder_title_key(normalized.get("title")) == title_key:
            return normalized
    return None


def _legacy_folder_from_sessions(folder_id: str, sessions: dict) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    grouped = [session for session in sessions.values() if (session.get("folder_id") or "").strip() == folder_id]
    if not grouped:
        return None
    newest = max(grouped, key=lambda session: session.get("updated") or session.get("created") or "")
    source_docs = _merge_unique_strings(*(session.get("source_docs") or [] for session in grouped))
    workspace_roots = _merge_unique_strings(*(session.get("workspace_roots") or [] for session in grouped))
    return _normalize_chat_folder({
        "id": folder_id,
        "title": folder_id,
        "created": newest.get("created"),
        "updated": newest.get("updated") or newest.get("created"),
        "source_docs": source_docs,
        "workspace_roots": workspace_roots,
    })


def _resolve_folder_reference(folder_id: str, sessions: dict | None = None, folders: dict | None = None, include_legacy: bool = True) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    folders = folders if folders is not None else _load_all_folders()
    direct = folders.get(folder_id)
    if direct:
        return _normalize_chat_folder(direct)
    by_title = _unique_folder_for_title(folder_id, folders=folders)
    if by_title:
        return by_title
    if not include_legacy:
        return None
    sessions = sessions if sessions is not None else _load_all_sessions()
    return _legacy_folder_from_sessions(folder_id, sessions)


def _folder_with_fallback(folder_id: str, sessions: dict | None = None) -> dict | None:
    return _resolve_folder_reference(folder_id, sessions=sessions)


def _ensure_folder_exists(folder_id: str) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    existing = _folder_with_fallback(folder_id)
    if existing:
        return existing
    conflict = _folder_title_conflict(folder_id)
    if conflict:
        return conflict
    now = datetime.now().isoformat()
    return _write_folder({
        "id": folder_id,
        "title": folder_id,
        "created": now,
        "updated": now,
        "workspace_roots": [],
        "source_docs": [],
    })


def _dedupe_legacy_folder_titles() -> dict:
    folders = _load_all_folders()
    sessions = _load_all_sessions()
    groups: dict[str, list[dict]] = {}
    for folder in folders.values():
        normalized = _normalize_chat_folder(folder)
        title_key = _folder_title_key(normalized.get("title"))
        if not title_key:
            continue
        groups.setdefault(title_key, []).append(normalized)

    changed_sessions: list[str] = []
    changed_session_ids = set()
    merged_groups = []
    changed = False

    for duplicates in groups.values():
        if len(duplicates) < 2:
            continue
        duplicates.sort(key=lambda folder: (
            folder.get("created") or "",
            folder.get("id") or "",
        ))
        canonical = dict(duplicates[0])
        merged_workspace_roots = _merge_unique_strings(*(folder.get("workspace_roots") or [] for folder in duplicates))
        merged_source_docs = _merge_unique_strings(*(folder.get("source_docs") or [] for folder in duplicates))
        merged_updated = max(
            [canonical.get("updated") or canonical.get("created")] +
            [folder.get("updated") or folder.get("created") for folder in duplicates[1:]]
        )
        if canonical.get("workspace_roots") != merged_workspace_roots or canonical.get("source_docs") != merged_source_docs or canonical.get("updated") != merged_updated:
            canonical["workspace_roots"] = merged_workspace_roots
            canonical["source_docs"] = merged_source_docs
            canonical["updated"] = merged_updated
            folders[canonical["id"]] = canonical
            changed = True

        removed_ids = []
        for duplicate in duplicates[1:]:
            duplicate_id = duplicate["id"]
            removed_ids.append(duplicate_id)
            if duplicate_id in folders:
                folders.pop(duplicate_id, None)
                changed = True
            for session in sessions.values():
                if (session.get("folder_id") or "").strip() != duplicate_id:
                    continue
                session["folder_id"] = canonical["id"]
                session["updated"] = datetime.now().isoformat()
                if session["id"] not in changed_session_ids:
                    changed_session_ids.add(session["id"])
                    changed_sessions.append(session["id"])
                changed = True

        merged_groups.append({
            "title": canonical["title"],
            "kept_id": canonical["id"],
            "removed_ids": removed_ids,
            "source_count": len(canonical.get("source_docs") or []),
            "workspace_root_count": len(canonical.get("workspace_roots") or []),
        })

    if changed:
        _write_all_folders(folders)
        for session_id in changed_sessions:
            session = sessions.get(session_id)
            if session:
                _write_session(session)

    return {
        "changed": changed,
        "merged_group_count": len(merged_groups),
        "merged_groups": merged_groups,
        "updated_session_ids": changed_sessions,
        "folders": _load_all_folders() if changed else folders,
    }


def _folder_summaries(sessions: dict | None = None) -> list[dict]:
    sessions = sessions if sessions is not None else _load_all_sessions()
    folders = _load_all_folders()
    folder_map = {folder_id: _normalize_chat_folder(folder) for folder_id, folder in folders.items()}
    grouped_sessions: dict[str, list[dict]] = {folder_id: [] for folder_id in folder_map}
    legacy_refs = set()

    for session in sessions.values():
        folder_ref = (session.get("folder_id") or "").strip()
        if not folder_ref:
            continue
        resolved = _resolve_folder_reference(folder_ref, sessions=sessions, folders=folder_map, include_legacy=False)
        if resolved and resolved["id"] in folder_map:
            grouped_sessions.setdefault(resolved["id"], []).append(session)
            continue
        legacy_refs.add(folder_ref)

    for folder_ref in sorted(legacy_refs, key=lambda value: value.casefold()):
        legacy = _legacy_folder_from_sessions(folder_ref, sessions)
        if legacy:
            folder_map[folder_ref] = legacy
            grouped_sessions[folder_ref] = [
                session for session in sessions.values()
                if (session.get("folder_id") or "").strip() == folder_ref
            ]

    summaries = []
    for folder_id, folder in folder_map.items():
        related_sessions = list(grouped_sessions.get(folder_id) or [])
        related_sessions.sort(key=lambda session: session.get("updated") or session.get("created") or "", reverse=True)
        summaries.append({
            "id": folder["id"],
            "title": folder["title"],
            "created": folder["created"],
            "updated": max([folder.get("updated")] + [s.get("updated") or s.get("created") for s in related_sessions if isinstance(s, dict)]),
            "source_docs": folder.get("source_docs") or [],
            "workspace_roots": folder.get("workspace_roots") or [],
            "chat_count": len(related_sessions),
            "sessions": [{
                "id": s["id"],
                "title": s.get("title", "Untitled"),
                "message_count": len(s.get("messages", [])),
                "updated": s.get("updated", s.get("created")),
                "last_message": s["messages"][-1]["content"][:100] if s.get("messages") else "",
            } for s in related_sessions],
        })
    summaries.sort(key=lambda folder: (
        (folder.get("title") or "").casefold(),
        folder.get("created") or "",
        folder.get("id") or "",
    ))
    return summaries


def _effective_session_context(session: dict) -> dict:
    normalized = _normalize_chat_session(copy.deepcopy(session))
    folder_ref = normalized.get("folder_id") or ""
    folder = _folder_with_fallback(folder_ref) if folder_ref else None
    folder_id = folder.get("id") if folder else folder_ref
    folder_title = folder.get("title") if folder else ""
    workspace_roots = _merge_unique_strings(
        (folder or {}).get("workspace_roots"),
        normalized.get("workspace_roots"),
    )
    source_docs = _merge_unique_strings(
        (folder or {}).get("source_docs"),
        normalized.get("source_docs"),
    )
    return {
        "folder_id": folder_id,
        "folder_title": folder_title or folder_id,
        "folder_source_docs": (folder or {}).get("source_docs") or [],
        "folder_workspace_roots": (folder or {}).get("workspace_roots") or [],
        "workspace_roots": workspace_roots,
        "source_docs": source_docs,
    }


def _session_from_file(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and data.get("id"):
        return _normalize_chat_session(data)
    raise ValueError(f"Invalid session payload in {path.name}")


def _load_all_sessions():
    """Load all persisted chat sessions from disk into memory."""
    loaded_sessions = {}
    with _chat_data_lock(shared=True):
        for f in sorted(CHAT_DATA_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f == CHAT_FOLDERS_PATH:
                continue
            try:
                data = _session_from_file(f)
                loaded_sessions[data["id"]] = data
            except Exception as exc:
                logger.warning("Failed to load session file %s: %s", f.name, exc)
    chat_sessions.clear()
    chat_sessions.update(loaded_sessions)
    return copy.deepcopy(loaded_sessions)


def _load_session(session_id):
    """Load a single persisted session from disk into the runtime cache."""
    path = _chat_session_path(session_id)
    with _chat_data_lock(shared=True):
        if not path.exists():
            chat_sessions.pop(session_id, None)
            return None
        try:
            data = _session_from_file(path)
        except Exception as exc:
            logger.warning("Failed to load session file %s: %s", path.name, exc)
            chat_sessions.pop(session_id, None)
            return None
    chat_sessions[session_id] = data
    return copy.deepcopy(data)


def _write_session(session):
    """Persist a single session atomically and refresh the runtime cache."""
    session = _normalize_chat_session(session)
    session_id = session["id"]
    path = _chat_session_path(session_id)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(session, ensure_ascii=False, indent=2)
    with _chat_data_lock():
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    chat_sessions[session_id] = copy.deepcopy(session)


def _delete_session_from_disk(session_id):
    """Remove a session from memory and disk."""
    chat_sessions.pop(session_id, None)
    path = _chat_session_path(session_id)
    with _chat_data_lock():
        if path.exists():
            path.unlink()


def _normalize_chat_folder(folder: dict) -> dict:
    normalized = {
        "id": str(folder.get("id") or "").strip(),
        "title": str(folder.get("title") or "").strip() or "Untitled Folder",
        "created": folder.get("created") or datetime.now().isoformat(),
        "updated": folder.get("updated") or datetime.now().isoformat(),
        "workspace_roots": _clean_string_list(folder.get("workspace_roots")),
        "source_docs": _clean_string_list(folder.get("source_docs")),
    }
    return normalized


def _folders_from_file() -> dict:
    if not CHAT_FOLDERS_PATH.exists():
        return {}
    data = json.loads(CHAT_FOLDERS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    folders = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        value = dict(value)
        value.setdefault("id", key)
        normalized = _normalize_chat_folder(value)
        if normalized["id"]:
            folders[normalized["id"]] = normalized
    return folders


def _write_all_folders(folders: dict) -> dict:
    serializable = {}
    for folder_id, folder in (folders or {}).items():
        normalized = _normalize_chat_folder(folder)
        if normalized["id"]:
            serializable[folder_id] = normalized
    payload = json.dumps(serializable, ensure_ascii=False, indent=2)
    tmp_path = CHAT_FOLDERS_PATH.with_suffix(CHAT_FOLDERS_PATH.suffix + ".tmp")
    with _chat_data_lock():
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, CHAT_FOLDERS_PATH)
    chat_folders.clear()
    chat_folders.update(copy.deepcopy(serializable))
    return copy.deepcopy(serializable)


def _load_all_folders() -> dict:
    with _chat_data_lock(shared=True):
        folders = _folders_from_file()
    chat_folders.clear()
    chat_folders.update(copy.deepcopy(folders))
    return copy.deepcopy(folders)


def _load_folder(folder_id: str) -> dict | None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return None
    folders = _load_all_folders()
    return folders.get(folder_id)


def _write_folder(folder: dict) -> dict:
    folder = _normalize_chat_folder(folder)
    folders = _load_all_folders()
    folders[folder["id"]] = folder
    return _write_all_folders(folders)[folder["id"]]


def _delete_folder(folder_id: str) -> None:
    folder_id = str(folder_id or "").strip()
    if not folder_id:
        return
    folders = _load_all_folders()
    if folder_id in folders:
        folders.pop(folder_id, None)
        _write_all_folders(folders)


def _crontab_available() -> bool:
    return shutil.which("crontab") is not None


def _load_cron_jobs() -> dict:
    if not CRON_JOBS_PATH.exists():
        return {}
    try:
        data = json.loads(CRON_JOBS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    jobs = {}
    for job_id, job in data.items():
        if not isinstance(job, dict):
            continue
        jobs[job_id] = {
            "id": str(job.get("id") or job_id).strip(),
            "name": str(job.get("name") or "").strip() or "Cron Job",
            "schedule": str(job.get("schedule") or "").strip(),
            "command": str(job.get("command") or "").strip(),
            "enabled": bool(job.get("enabled", True)),
            "created": job.get("created") or datetime.now().isoformat(),
            "updated": job.get("updated") or datetime.now().isoformat(),
        }
    return jobs


def _write_cron_jobs(jobs: dict) -> dict:
    payload = json.dumps(jobs or {}, ensure_ascii=False, indent=2)
    tmp_path = CRON_JOBS_PATH.with_suffix(CRON_JOBS_PATH.suffix + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, CRON_JOBS_PATH)
    return copy.deepcopy(jobs)


def _validate_cron_job_payload(data: dict) -> tuple[dict, list[str]]:
    name = str(data.get("name") or "").strip() or "Cron Job"
    schedule = str(data.get("schedule") or "").strip()
    command = str(data.get("command") or "").strip()
    enabled = bool(data.get("enabled", True))
    errors = []
    if len(schedule.split()) != 5:
        errors.append("Cron schedule must have exactly five fields")
    if not command:
        errors.append("Command is required")
    return {
        "name": name[:120].rstrip(),
        "schedule": schedule,
        "command": command,
        "enabled": enabled,
    }, errors


def _read_crontab_lines() -> list[str]:
    if not _crontab_available():
        raise ChatBackendError("crontab is not installed", status_code=501)
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip().lower()
        if "no crontab for" in stderr:
            return []
        raise ChatBackendError(proc.stderr.strip() or "Unable to read crontab", status_code=500)
    return [line.rstrip("\n") for line in proc.stdout.splitlines()]


def _write_crontab_lines(lines: list[str]) -> None:
    if not _crontab_available():
        raise ChatBackendError("crontab is not installed", status_code=501)
    text = "\n".join(lines).rstrip()
    if text:
        text += "\n"
    proc = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ChatBackendError(proc.stderr.strip() or "Unable to update crontab", status_code=500)


def _cron_job_line(job: dict) -> str:
    return f'{job["schedule"]} {job["command"]} # {CRON_JOB_MARKER}:{job["id"]}'


def _sync_cron_jobs_to_system(jobs: dict | None = None) -> None:
    jobs = jobs if jobs is not None else _load_cron_jobs()
    current_lines = _read_crontab_lines()
    preserved = [line for line in current_lines if CRON_JOB_MARKER not in line]
    managed = [_cron_job_line(job) for job in jobs.values() if job.get("enabled") and job.get("schedule") and job.get("command")]
    _write_crontab_lines(preserved + managed)


class ChatRequestCancelled(Exception):
    """Raised when an in-flight chat request is cancelled by the client."""


class ChatBackendError(Exception):
    """Raised when Hermes cannot produce a usable response for the request."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class ChatRequestTimeout(ChatBackendError):
    """Raised when a chat request exceeds the configured timeout."""

    def __init__(self, message: str):
        super().__init__(message, status_code=504)


def _request_control_path(request_id: str) -> Path:
    return CHAT_REQUEST_DIR / f"{secure_filename(request_id)}.json"


def _read_request_control(request_id: str) -> dict | None:
    path = _request_control_path(request_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read chat request control file %s: %s", path.name, exc)
        return None


def _write_request_control(request_id: str, payload: dict) -> None:
    path = _request_control_path(request_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _request_output_path(request_id: str) -> Path:
    return CHAT_REQUEST_DIR / f"{secure_filename(request_id)}.log"


def _truncate_recent_lines(lines: list[str], limit: int = 24) -> list[str]:
    cleaned = [str(line).rstrip("\n") for line in (lines or []) if str(line).strip()]
    if limit <= 0 or len(cleaned) <= limit:
        return cleaned
    return cleaned[-limit:]


def _request_progress_lines(request_id: str, limit: int = 0) -> list[str]:
    path = _request_output_path(request_id)
    if not path.exists():
        return []
    try:
        return _truncate_recent_lines(path.read_text(encoding="utf-8", errors="replace").splitlines(), limit=limit)
    except Exception:
        return []


def _register_chat_request(
    request_id: str,
    session_id: str | None,
    *,
    transport: str,
    cancel_supported: bool,
) -> None:
    _write_request_control(request_id, {
        "request_id": request_id,
        "session_id": session_id,
        "status": "running",
        "transport": transport,
        "cancel_supported": cancel_supported,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "pid": None,
        "pgid": None,
        "cancel_requested_at": None,
        "output_path": str(_request_output_path(request_id)),
    })


def _update_chat_request(request_id: str, **fields) -> dict | None:
    payload = _read_request_control(request_id)
    if payload is None:
        return None
    payload.update(fields)
    payload["updated_at"] = datetime.now().isoformat()
    _write_request_control(request_id, payload)
    return payload


def _remove_chat_request(request_id: str) -> None:
    path = _request_control_path(request_id)
    if path.exists():
        path.unlink()


def _is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _terminate_chat_process(pid: int | None, pgid: int | None, sig: int) -> bool:
    try:
        if pgid:
            os.killpg(pgid, sig)
        elif pid:
            os.kill(pid, sig)
        else:
            return False
        return True
    except ProcessLookupError:
        return True
    except Exception as exc:
        logger.warning("Failed to signal chat process pid=%s pgid=%s: %s", pid, pgid, exc)
        return False


def _cancel_chat_request(request_id: str) -> tuple[bool, str]:
    payload = _read_request_control(request_id)
    if payload is None:
        return False, "Request not found"
    if not payload.get("cancel_supported", False):
        return False, "This request is using the API/vision path and cannot be cancelled server-side"
    if payload.get("status") == "completed":
        return False, "Request already completed"
    if payload.get("status") == "cancelled":
        return True, "Request already cancelled"

    pid = payload.get("pid")
    pgid = payload.get("pgid")
    _update_chat_request(
        request_id,
        status="cancel_requested",
        cancel_requested_at=datetime.now().isoformat(),
    )

    if not pid:
        return True, "Cancellation queued before subprocess start"

    if not _terminate_chat_process(pid, pgid, signal.SIGTERM):
        return False, "Failed to terminate Hermes process"

    deadline = time.time() + CHAT_CANCEL_GRACE_SECONDS
    while time.time() < deadline:
        if not _is_process_alive(pid):
            _update_chat_request(request_id, status="cancelled")
            return True, "Request cancelled"
        time.sleep(CHAT_CANCEL_POLL_INTERVAL)

    _terminate_chat_process(pid, pgid, signal.SIGKILL)
    for _ in range(int(1 / CHAT_CANCEL_POLL_INTERVAL)):
        if not _is_process_alive(pid):
            _update_chat_request(request_id, status="cancelled")
            return True, "Request cancelled"
        time.sleep(CHAT_CANCEL_POLL_INTERVAL)

    return False, "Hermes process did not exit after cancellation"


# Load sessions on startup
_load_all_sessions()
_load_all_folders()

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(
    __name__,
    template_folder=str(APP_ROOT / "templates"),
    static_folder=str(APP_ROOT / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BODY_SIZE
# Restrict CORS to localhost by default
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:*",
            "http://127.0.0.1:*",
        ]
    }
})


def _request_id_or_dash() -> str:
    if has_request_context():
        return getattr(g, "request_id", "-")
    return "-"


def _should_log_request_summary(path: str, status_code: int, duration_ms: int) -> bool:
    if status_code >= 400:
        return True
    if duration_ms >= REQUEST_LOG_SLOW_MS:
        return True
    return path.startswith("/api/chat") or path.startswith("/api/upload")


@app.before_request
def _start_request_tracking():
    raw_request_id = (request.headers.get("X-Request-ID") or "").strip()
    if raw_request_id:
        sanitized = re.sub(r"[^A-Za-z0-9._:-]", "", raw_request_id)[:64]
        g.request_id = sanitized or uuid.uuid4().hex[:12]
    else:
        g.request_id = uuid.uuid4().hex[:12]
    g.request_started_at = time.monotonic()


@app.after_request
def _finish_request_tracking(response):
    request_id = _request_id_or_dash()
    response.headers.setdefault("X-Request-ID", request_id)
    started_at = getattr(g, "request_started_at", None)
    if started_at is None:
        return response
    duration_ms = int((time.monotonic() - started_at) * 1000)
    if _should_log_request_summary(request.path, response.status_code, duration_ms):
        logger.info(
            "HTTP %s %s status=%s duration_ms=%s request_id=%s content_length=%s remote=%s",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            request_id,
            request.content_length,
            request.remote_addr,
        )
    return response


@app.errorhandler(RequestEntityTooLarge)
def _handle_request_too_large(exc):
    logger.warning(
        "Rejected oversized request path=%s request_id=%s content_length=%s remote=%s limit=%s",
        request.path,
        _request_id_or_dash(),
        request.content_length,
        request.remote_addr,
        MAX_REQUEST_BODY_SIZE,
    )
    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": f"Request too large (max upload {MAX_UPLOAD_SIZE // (1024 * 1024)}MB)",
            "request_id": _request_id_or_dash(),
            "max_upload_mb": MAX_UPLOAD_SIZE // (1024 * 1024),
        }), 413
    return "Request too large", 413


@app.errorhandler(BadRequest)
def _handle_bad_request(exc):
    if not request.path.startswith("/api/"):
        return exc
    logger.warning(
        "Rejected bad request path=%s request_id=%s remote=%s detail=%s",
        request.path,
        _request_id_or_dash(),
        request.remote_addr,
        exc.description,
    )
    return jsonify({
        "ok": False,
        "error": "Invalid request body",
        "detail": exc.description,
        "request_id": _request_id_or_dash(),
    }), 400

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def _current_webui_token() -> str:
    return _runtime_env_value("HERMES_WEBUI_TOKEN", "")


HERMES_WEBUI_TOKEN = _current_webui_token()

# --- Cookie-based session auth (login screen) ---
import secrets as _secrets

_DASHBOARD_USER = os.environ.get("HERMES_DASHBOARD_USER", "admin")
_DASHBOARD_PASS = os.environ.get("HERMES_DASHBOARD_PASS", "Unaitxo@13")
_SESSION_TOKEN_TTL = 86400  # 24 hours
_SESSION_TOKEN_STORE = Path(os.environ.get(
    "HERMES_WEBUI_TOKEN_STORE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session_tokens.json"),
))


def _load_session_tokens() -> dict[str, float]:
    """Load valid session tokens from disk (shared across workers)."""
    if not _SESSION_TOKEN_STORE.exists():
        return {}
    try:
        data = json.loads(_SESSION_TOKEN_STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    return {str(k): float(v) for k, v in data.items()
            if isinstance(v, (int, float)) and float(v) > now}


def _save_session_tokens(tokens: dict[str, float]) -> None:
    """Persist session tokens to disk."""
    try:
        _SESSION_TOKEN_STORE.write_text(json.dumps(tokens), encoding="utf-8")
    except Exception:
        pass


def _register_session_token(token: str, expiry: float) -> None:
    tokens = _load_session_tokens()
    tokens[token] = expiry
    _save_session_tokens(tokens)


def _remove_session_token(token: str) -> None:
    tokens = _load_session_tokens()
    tokens.pop(token, None)
    _save_session_tokens(tokens)


def _verify_session_cookie() -> bool:
    token = request.cookies.get("hermes_webui")
    if not token:
        return False
    tokens = _load_session_tokens()
    expiry = tokens.get(token)
    if expiry is None or time.time() > expiry:
        if expiry is not None:
            _remove_session_token(token)
        return False
    return True


@app.route("/api/login", methods=["POST"])
def webui_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if username == _DASHBOARD_USER and password == _DASHBOARD_PASS:
        token = _secrets.token_urlsafe(32)
        _register_session_token(token, time.time() + _SESSION_TOKEN_TTL)
        resp = jsonify({"ok": True})
        resp.set_cookie(
            "hermes_webui", token,
            httponly=True, samesite="Lax", secure=True, max_age=_SESSION_TOKEN_TTL,
        )
        return resp
    return jsonify({"ok": False, "error": "Invalid credentials"}), 401


@app.route("/api/auth/check", methods=["GET"])
def webui_auth_check():
    if _verify_session_cookie():
        return jsonify({"authenticated": True})
    return jsonify({"authenticated": False}), 401


@app.route("/api/logout", methods=["POST"])
def webui_logout():
    token = request.cookies.get("hermes_webui")
    if token:
        _remove_session_token(token)
    resp = jsonify({"ok": True})
    resp.delete_cookie("hermes_webui")
    return resp


def require_token(f):
    """Decorator to require authentication for API endpoints.
    Accepts either a session cookie (login screen) or Bearer token (legacy)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check session cookie first (login screen flow)
        if _verify_session_cookie():
            return f(*args, **kwargs)

        # 2. Fall back to Bearer token (legacy/API flow)
        expected_token = _current_webui_token()
        if not expected_token:
            logger.warning("Authentication not configured - rejecting API request")
            return jsonify({"ok": False, "error": "API authentication not configured"}), 401
        
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("API request missing Authorization header from %s", request.remote_addr)
            return jsonify({"ok": False, "error": "Missing or invalid Authorization header"}), 401
        
        provided_token = auth_header[7:]
        if provided_token != expected_token:
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
        object.__setattr__(self, "_config", {})
        object.__setattr__(self, "_config_mtime_ns", None)
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_setting_from_disk", False)
        self.load()

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if name == "_config" and not getattr(self, "_setting_from_disk", False):
            object.__setattr__(self, "_manual_override", True)
            object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    def _config_file_mtime_ns(self):
        config_path = _selected_config_path()
        try:
            return config_path.stat().st_mtime_ns
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _replace_config_from_disk(self, data):
        object.__setattr__(self, "_setting_from_disk", True)
        try:
            object.__setattr__(self, "_config", data)
        finally:
            object.__setattr__(self, "_setting_from_disk", False)
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    def load_if_changed(self):
        """Reload config.yaml only when the on-disk file changed."""
        if self._manual_override:
            return
        current_mtime_ns = self._config_file_mtime_ns()
        if current_mtime_ns != self._config_mtime_ns:
            self.load()

    # -- loading ----------------------------------------------------------
    def load(self):
        """Read config.yaml from disk (or return empty dict)."""
        config_path = _selected_config_path()
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as fh:
                    self._replace_config_from_disk(yaml.safe_load(fh) or {})
            except Exception as exc:
                self._replace_config_from_disk({})
                print(f"[ConfigManager] Failed to load config: {exc}")
        else:
            self._replace_config_from_disk({})

    # -- saving -----------------------------------------------------------
    def save(self):
        """Write config to disk, creating a timestamped backup first."""
        config_path = _selected_config_path()
        backup_dir = _selected_backup_dir()
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"config_{ts}.yaml"
        if config_path.exists():
            shutil.copy2(config_path, backup)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.dump(
                self._config,
                fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        object.__setattr__(self, "_manual_override", False)
        object.__setattr__(self, "_config_mtime_ns", self._config_file_mtime_ns())

    # -- getters ----------------------------------------------------------
    def get(self, section=None):
        """Return full config or a single section (masked)."""
        self.load_if_changed()
        if section is None:
            return self.mask_secrets(copy.deepcopy(self._config))
        data = copy.deepcopy(self._config.get(section, {}))
        if isinstance(data, dict):
            return self.mask_secrets(data)
        return data

    def get_raw(self, section=None):
        """Return config or section WITHOUT masking (internal use)."""
        self.load_if_changed()
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


_KEEP_EXISTING_SECRET = object()


def _preserve_masked_secret_updates(current, update, parent_key: str = ""):
    """Drop masked secret placeholders from updates so existing secrets survive."""
    if isinstance(update, dict):
        current_dict = current if isinstance(current, dict) else {}
        sanitized = {}
        for key, value in update.items():
            result = _preserve_masked_secret_updates(current_dict.get(key), value, key)
            if result is _KEEP_EXISTING_SECRET:
                continue
            sanitized[key] = result
        return sanitized
    if isinstance(update, list):
        current_list = current if isinstance(current, list) else []
        sanitized = []
        for index, value in enumerate(update):
            current_value = current_list[index] if index < len(current_list) else None
            result = _preserve_masked_secret_updates(current_value, value, parent_key)
            if result is _KEEP_EXISTING_SECRET:
                sanitized.append(copy.deepcopy(current_value))
            else:
                sanitized.append(result)
        return sanitized
    if (
        isinstance(update, str)
        and isinstance(current, str)
        and current
        and _SECRET_PATTERNS.search(parent_key)
        and update == _mask_value(parent_key, current)
    ):
        return _KEEP_EXISTING_SECRET
    return copy.deepcopy(update)


def _normalized_model_config() -> dict:
    raw = cfg.get_raw()
    model_cfg = raw.get("model", {}) or {}
    auxiliary_cfg = raw.get("auxiliary", {}) or {}
    normalized = copy.deepcopy(model_cfg)
    if "default_model" not in normalized and model_cfg.get("default"):
        normalized["default_model"] = model_cfg.get("default")
    if "default_provider" not in normalized and model_cfg.get("provider"):
        normalized["default_provider"] = model_cfg.get("provider")
    for aux_key in AUXILIARY_MODEL_KEYS:
        if aux_key not in normalized and aux_key in auxiliary_cfg:
            normalized[aux_key] = copy.deepcopy(auxiliary_cfg.get(aux_key))
    return normalized


def _provider_display_name(provider_type: str) -> str:
    normalized = str(provider_type or "").strip().lower()
    return PROVIDER_TYPE_LABELS.get(normalized, normalized or "Custom")


def _infer_provider_type(name: str = "", base_url: str = "") -> str:
    haystack = f"{name} {base_url}".lower()
    if "openrouter" in haystack:
        return "openrouter"
    if "api.openai.com" in haystack or re.search(r"\bopenai\b", haystack):
        return "openai"
    if "azure" in haystack:
        return "azure"
    if "anthropic" in haystack or "claude" in haystack:
        return "anthropic"
    if "groq" in haystack:
        return "groq"
    if "google" in haystack or "gemini" in haystack:
        return "google"
    if "mistral" in haystack:
        return "mistral"
    if "together" in haystack:
        return "together"
    if "fireworks" in haystack:
        return "fireworks"
    if "deepseek" in haystack:
        return "deepseek"
    if "cohere" in haystack:
        return "cohere"
    return "auto"


def _normalize_provider_type(value: str = "", *, name: str = "", base_url: str = "") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in ("", "default", "custom", "generic", "local", "openai-compatible"):
        return _infer_provider_type(name=name, base_url=base_url)
    return normalized


def _provider_default_base_url(provider: str = "") -> str:
    normalized = _normalize_provider_type(provider)
    return PROVIDER_DEFAULT_BASE_URLS.get(normalized, "")


def _normalize_provider_profile(entry: dict) -> dict:
    normalized = copy.deepcopy(entry or {})
    normalized["name"] = str(normalized.get("name") or "").strip()
    normalized["base_url"] = str(normalized.get("base_url") or "").strip()
    normalized["model"] = str(normalized.get("model") or "").strip()
    normalized["provider"] = _normalize_provider_type(
        normalized.get("provider", ""),
        name=normalized.get("name", ""),
        base_url=normalized.get("base_url", ""),
    )
    if not normalized["base_url"]:
        normalized["base_url"] = _provider_default_base_url(normalized.get("provider", ""))
    api_key = normalized.get("api_key")
    normalized["api_key"] = str(api_key or "").strip() if api_key is not None else ""
    normalized["implicit"] = bool(normalized.get("implicit"))
    return normalized


def _custom_provider_profiles(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    return [_normalize_provider_profile(item) for item in (raw.get("custom_providers", []) or [])]


def _raw_role_profile_candidate(role: str, *, model_cfg: dict | None = None, raw: dict | None = None) -> dict | None:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    if role == "primary":
        explicit_profile = str(model_cfg.get("default_profile") or "").strip()
        declared_provider = str(model_cfg.get("default_provider") or "").strip()
        provider = _normalize_provider_type(declared_provider)
        model = str(model_cfg.get("default_model") or "").strip()
        base_url = str(model_cfg.get("base_url") or _provider_default_base_url(provider) or "").strip()
        api_key = str(model_cfg.get("api_key") or "").strip()
        routing_provider = _role_routing_provider("primary", model_cfg=model_cfg)
    elif role == "fallback":
        explicit_profile = str(model_cfg.get("fallback_profile") or "").strip()
        declared_provider = str(model_cfg.get("fallback_provider") or "").strip()
        provider = _normalize_provider_type(declared_provider)
        model = str(model_cfg.get("fallback_model") or "").strip()
        base_url = str(model_cfg.get("fallback_base_url") or _provider_default_base_url(provider) or "").strip()
        api_key = str(model_cfg.get("fallback_api_key") or "").strip()
        routing_provider = _role_routing_provider("fallback", model_cfg=model_cfg)
    elif role == "vision":
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, str):
            explicit_profile = ""
            declared_provider = str(model_cfg.get("default_provider") or "").strip()
            provider = _normalize_provider_type(declared_provider)
            model = vision_cfg.strip()
            base_url = str(model_cfg.get("base_url") or _provider_default_base_url(provider) or "").strip()
            api_key = str(model_cfg.get("api_key") or "").strip()
            routing_provider = ""
        elif isinstance(vision_cfg, dict):
            explicit_profile = str(vision_cfg.get("profile") or "").strip()
            declared_provider = str(vision_cfg.get("provider") or "").strip()
            provider = _normalize_provider_type(declared_provider, base_url=vision_cfg.get("base_url", ""))
            model = str(vision_cfg.get("model") or "").strip()
            base_url = str(vision_cfg.get("base_url") or _provider_default_base_url(provider) or "").strip()
            api_key = str(vision_cfg.get("api_key") or "").strip()
            routing_provider = _role_routing_provider("vision", model_cfg=model_cfg)
        else:
            return None
    else:
        return None

    if not any((explicit_profile, provider, model, base_url, api_key)):
        return None
    if not explicit_profile and provider in ("", "auto") and not any((model, base_url, api_key)):
        return None
    name = explicit_profile or declared_provider or provider or role
    return _normalize_provider_profile({
        "name": name,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "routing_provider": routing_provider,
        "implicit": True,
        "source_role": role,
    })


def _available_provider_profiles(raw: dict | None = None, model_cfg: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    profiles = []
    by_name: dict[str, dict] = {}

    def add_profile(profile: dict | None):
        if not profile:
            return
        normalized = _normalize_provider_profile(profile)
        name = normalized.get("name", "")
        if not name:
            return
        existing = by_name.get(name)
        if existing:
            same_target = (
                existing.get("provider") == normalized.get("provider")
                and existing.get("base_url") == normalized.get("base_url")
            )
            if same_target:
                if not existing.get("model") and normalized.get("model"):
                    existing["model"] = normalized["model"]
                return
            suffix_name = f"{name}-{normalized.get('source_role') or 'profile'}"
            normalized["name"] = suffix_name
            name = suffix_name
            if name in by_name:
                return
        by_name[name] = normalized
        profiles.append(normalized)

    for profile in _custom_provider_profiles(raw):
        add_profile(profile)
    for role in MODEL_ROLE_LABELS:
        candidate = _raw_role_profile_candidate(role, model_cfg=model_cfg, raw=raw)
        explicit_name = candidate.get("name", "") if candidate else ""
        if explicit_name and explicit_name in by_name:
            continue
        add_profile(candidate)
    return profiles


def _get_provider_profile(name: str, raw: dict | None = None) -> dict | None:
    name = str(name or "").strip()
    if not name:
        return None
    for profile in _available_provider_profiles(raw):
        if profile.get("name") == name:
            return profile
    return None


def _role_linked_profile_name(role: str, *, model_cfg: dict | None = None, raw: dict | None = None) -> str:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    profile_names = {item.get("name") for item in _custom_provider_profiles(raw)}

    if role == "primary":
        explicit = str(model_cfg.get("default_profile") or "").strip()
        fallback = str(model_cfg.get("default_provider") or "").strip()
    elif role == "fallback":
        explicit = str(model_cfg.get("fallback_profile") or "").strip()
        fallback = str(model_cfg.get("fallback_provider") or "").strip()
    elif role == "vision":
        vision_cfg = model_cfg.get("vision")
        explicit = str(vision_cfg.get("profile") or "").strip() if isinstance(vision_cfg, dict) else ""
        fallback = str(vision_cfg.get("provider") or "").strip() if isinstance(vision_cfg, dict) else ""
    else:
        return ""

    if explicit:
        return explicit
    if fallback in profile_names:
        return fallback
    candidate = _raw_role_profile_candidate(role, model_cfg=model_cfg, raw=raw)
    if candidate:
        return candidate.get("name", "")
    return ""


def _provider_usage_map(raw: dict | None = None, model_cfg: dict | None = None) -> dict[str, list[str]]:
    raw = raw if raw is not None else cfg.get_raw()
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    usage: dict[str, list[str]] = {}
    for role, label in MODEL_ROLE_LABELS.items():
        profile_name = _role_linked_profile_name(role, model_cfg=model_cfg, raw=raw)
        if not profile_name:
            continue
        usage.setdefault(profile_name, []).append(label)
    return usage


def _role_routing_provider(role: str, *, model_cfg: dict | None = None) -> str:
    model_cfg = model_cfg if model_cfg is not None else _normalized_model_config()
    if role == "primary":
        return str(model_cfg.get("routing_provider") or "").strip()
    if role == "fallback":
        return str(model_cfg.get("fallback_routing_provider") or "").strip()
    if role == "vision":
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, dict):
            return str(vision_cfg.get("routing_provider") or "").strip()
    return ""


def _resolve_role_target(role: str) -> dict:
    raw = cfg.get_raw()
    model_cfg = _normalized_model_config()
    default_provider = _normalize_provider_type(
        model_cfg.get("default_provider", ""),
        base_url=model_cfg.get("base_url", ""),
    )
    # The web UI is a gateway *client* — resolve the gateway URL from the
    # active profile's API_SERVER_PORT (handles multi-gateway setups like
    # default:8642 / leire:8644).  Falls back to model.base_url only when
    # no gateway port is configured for the profile.
    _gateway_url = _profile_api_gateway_url()
    default_target = {
        "base_url": (_gateway_url or model_cfg.get("base_url") or _provider_default_base_url(default_provider) or _effective_hermes_api_url("") or DEFAULT_HERMES_API_URL).strip(),
        "api_key": str(model_cfg.get("api_key") or "").strip(),
        "model": str(model_cfg.get("default_model") or "").strip(),
        "provider": default_provider,
        "profile": _role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw),
        "routing_provider": _role_routing_provider("primary", model_cfg=model_cfg),
    }

    primary_profile = _get_provider_profile(default_target.get("profile"), raw)
    if primary_profile:
        default_target["provider"] = primary_profile.get("provider") or default_target["provider"]
        # Only apply primary_profile base_url when no gateway URL was resolved
        # — the web UI must route through the local gateway, not the upstream LLM.
        if not _gateway_url:
            default_target["base_url"] = primary_profile.get("base_url") or default_target["base_url"]
        if primary_profile.get("api_key"):
            default_target["api_key"] = primary_profile.get("api_key")
        if not default_target["model"]:
            default_target["model"] = primary_profile.get("model") or default_target["model"]
    default_target["base_url"] = _resolve_runtime_template(default_target.get("base_url") or "").strip()
    default_target["api_key"] = _resolved_target_api_key(default_target)

    if role == "primary":
        return default_target

    if role == "fallback":
        fallback_provider = _normalize_provider_type(
            model_cfg.get("fallback_provider", ""),
            base_url=model_cfg.get("fallback_base_url", ""),
        )
        fallback_target = {
            "base_url": str(model_cfg.get("fallback_base_url") or _provider_default_base_url(fallback_provider) or "").strip(),
            "api_key": str(model_cfg.get("fallback_api_key") or "").strip(),
            "model": str(model_cfg.get("fallback_model") or "").strip(),
            "provider": fallback_provider,
            "profile": _role_linked_profile_name("fallback", model_cfg=model_cfg, raw=raw),
            "routing_provider": _role_routing_provider("fallback", model_cfg=model_cfg),
        }
        fallback_profile = _get_provider_profile(fallback_target.get("profile"), raw)
        if fallback_profile:
            fallback_target["provider"] = fallback_profile.get("provider") or fallback_target["provider"]
            fallback_target["base_url"] = fallback_profile.get("base_url") or fallback_target["base_url"]
            if fallback_profile.get("api_key"):
                fallback_target["api_key"] = fallback_profile.get("api_key")
            if not fallback_target["model"]:
                fallback_target["model"] = fallback_profile.get("model") or fallback_target["model"]
        fallback_target["base_url"] = _resolve_runtime_template(fallback_target.get("base_url") or "").strip()
        fallback_target["api_key"] = _resolved_target_api_key(fallback_target)
        return fallback_target

    if role == "vision":
        merged = dict(default_target)
        vision_cfg = model_cfg.get("vision")
        if isinstance(vision_cfg, str) and vision_cfg.strip():
            merged["model"] = vision_cfg.strip()
            merged["profile"] = ""
            merged["routing_provider"] = ""
            merged["api_key"] = _resolved_target_api_key(merged)
            return merged
        if isinstance(vision_cfg, dict):
            merged["profile"] = _role_linked_profile_name("vision", model_cfg=model_cfg, raw=raw)
            merged["routing_provider"] = _role_routing_provider("vision", model_cfg=model_cfg)
            vision_profile = _get_provider_profile(merged.get("profile"), raw)
            if vision_profile:
                merged["provider"] = vision_profile.get("provider") or merged["provider"]
                merged["base_url"] = vision_profile.get("base_url") or merged["base_url"]
                if vision_profile.get("api_key"):
                    merged["api_key"] = vision_profile.get("api_key")
            for key in ("base_url", "api_key", "model", "provider"):
                if isinstance(vision_cfg.get(key), str) and vision_cfg.get(key).strip():
                    merged[key] = vision_cfg.get(key).strip()
            merged["base_url"] = _resolve_runtime_template(merged.get("base_url") or "").strip()
            merged["provider"] = _normalize_provider_type(
                merged.get("provider", ""),
                base_url=merged.get("base_url", ""),
            )
            merged["api_key"] = _resolved_target_api_key(merged)
        return merged

    raise ValueError(f"Unknown model role: {role}")


def _model_role_enabled(role: str, target: dict | None = None) -> bool:
    if role == "primary":
        return True
    target = target if target is not None else _resolve_role_target(role)
    return bool(str(target.get("model") or "").strip())


def _model_role_info(role: str) -> dict:
    target = _resolve_role_target(role)
    linked_profile = str(target.get("profile") or "").strip()
    return {
        "role": role,
        "label": MODEL_ROLE_LABELS.get(role, role.title()),
        "profile": linked_profile,
        "provider": str(target.get("provider") or "").strip(),
        "provider_label": _provider_display_name(target.get("provider", "")),
        "model": str(target.get("model") or "").strip(),
        "base_url": str(target.get("base_url") or "").strip(),
        "routing_provider": str(target.get("routing_provider") or "").strip(),
        "enabled": _model_role_enabled(role, target=target),
        "supports_live_discovery": str(target.get("provider") or "").strip().lower() == "openrouter",
    }


def _profile_payload_for_role(profile_name: str, model_name: str, routing_provider: str = "") -> dict:
    profile = _get_provider_profile(profile_name)
    if not profile:
        raise ChatBackendError(f"Provider profile '{profile_name}' was not found", status_code=404)
    return {
        "profile": profile.get("name", ""),
        "provider": profile.get("provider", ""),
        "base_url": profile.get("base_url", ""),
        "api_key": profile.get("api_key", ""),
        "model": str(model_name or "").strip(),
        "routing_provider": str(routing_provider or "").strip(),
    }


def _sync_linked_provider_roles(profile_name: str, profile: dict) -> None:
    raw = cfg.get_raw()
    model_cfg = _normalized_model_config()
    model_updates = {}
    if _role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw) == profile_name:
        model_updates.update({
            "default_profile": profile_name,
            "default_provider": profile.get("provider", ""),
            "base_url": profile.get("base_url", ""),
            "api_key": profile.get("api_key", ""),
        })
    if _role_linked_profile_name("fallback", model_cfg=model_cfg, raw=raw) == profile_name:
        model_updates.update({
            "fallback_profile": profile_name,
            "fallback_provider": profile.get("provider", ""),
            "fallback_base_url": profile.get("base_url", ""),
            "fallback_api_key": profile.get("api_key", ""),
        })
    if model_updates:
        cfg.update("model", model_updates)

    vision_cfg = model_cfg.get("vision")
    if _role_linked_profile_name("vision", model_cfg=model_cfg, raw=raw) == profile_name:
        cfg.update("auxiliary", {
            "vision": {
                "profile": profile_name,
                "provider": profile.get("provider", ""),
                "base_url": profile.get("base_url", ""),
                "api_key": profile.get("api_key", ""),
                "model": str((vision_cfg or {}).get("model") or "").strip() if isinstance(vision_cfg, dict) else "",
                "routing_provider": str((vision_cfg or {}).get("routing_provider") or "").strip() if isinstance(vision_cfg, dict) else "",
            }
        })


def _classify_env_key(key: str) -> str:
    """Classify an env var into a display group."""
    k = key.lower()
    if any(p in k for p in ("anthropic", "openai", "groq", "google", "mistral", "ollama", "together", "fireworks", "deepseek", "cohere", "xai", "bedrock", "azure")):
        return "Provider"
    if any(p in k for p in ("discord", "whatsapp", "telegram", "slack", "webhook", "matrix")):
        return "Channel"
    return "System"


def _env_var_metadata(key: str) -> dict:
    preset = ENV_VAR_PRESETS.get(str(key or "").strip(), {})
    return {
        "key": str(key or "").strip(),
        "group": preset.get("group") or _classify_env_key(key),
        "label": preset.get("label") or str(key or "").strip(),
        "description": preset.get("description") or "",
        "secret": bool(preset.get("secret")),
        "recommended": bool(preset.get("recommended")),
        "default_value": str(preset.get("default_value") or ""),
        "starter_pack_item": str(preset.get("starter_pack_item") or ""),
    }


def _env_presets_by_group() -> dict[str, list[dict]]:
    grouped = {group: [] for group in ENV_GROUP_HELP}
    for key in sorted(ENV_VAR_PRESETS.keys()):
        meta = _env_var_metadata(key)
        grouped.setdefault(meta["group"], []).append(meta)
    return grouped


def _slugify_capability(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    return text.strip("-")


def _capability_preview_token(capability_type: str, payload: dict) -> str:
    def _canonicalize(value):
        if isinstance(value, dict):
            return {
                str(key): _canonicalize(item)
                for key, item in value.items()
                if str(key) not in {"recorded_at"}
            }
        if isinstance(value, list):
            return [_canonicalize(item) for item in value]
        return value

    encoded = json.dumps({
        "type": str(capability_type or "").strip(),
        "payload": _canonicalize(payload),
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _capability_status_badge(status: str) -> str:
    if status == "active":
        return "Active"
    if status == "planned":
        return "Planned Next"
    return "Preview"


def _capability_integration_options(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    existing = {entry.get("name"): entry for entry in _integration_entries(raw)}
    options = []
    for key in INTEGRATION_SECTION_ORDER:
        entry = existing.get(key) or {}
        config_value = raw.get(key)
        if not isinstance(config_value, dict):
            config_value = copy.deepcopy(INTEGRATION_CONFIG_TEMPLATES.get(key) or {})
        options.append({
            "name": key,
            "label": INTEGRATION_SECTION_LABELS.get(key, key.title()),
            "kind": "integration",
            "configured": bool(entry.get("configured")),
            "exists": isinstance(raw.get(key), dict),
            "config": cfg.mask_secrets(copy.deepcopy(config_value)),
            "config_template": copy.deepcopy(INTEGRATION_CONFIG_TEMPLATES.get(key) or {}),
            "suggested_env_vars": [
                _normalize_capability_env_var(item)
                for item in (INTEGRATION_ENV_TEMPLATES.get(key) or ())
                if _normalize_capability_env_var(item)
            ],
        })
    return options


def _agent_defaults(raw: dict | None = None) -> dict:
    raw = raw if raw is not None else cfg.get_raw()
    agent_cfg = raw.get("agent", {})
    return copy.deepcopy(agent_cfg) if isinstance(agent_cfg, dict) else {}


def _agent_personality_sections(raw: dict | None = None) -> tuple[dict, dict]:
    raw = raw if raw is not None else cfg.get_raw()
    agent_cfg = raw.get("agent", {})
    nested = agent_cfg.get("personalities", {}) if isinstance(agent_cfg, dict) else {}
    legacy = raw.get("personalities", {})
    nested = copy.deepcopy(nested) if isinstance(nested, dict) else {}
    legacy = copy.deepcopy(legacy) if isinstance(legacy, dict) else {}
    return nested, legacy


def _agent_personality_entries(raw: dict | None = None) -> tuple[dict[str, object], dict[str, str]]:
    nested, legacy = _agent_personality_sections(raw)
    merged = {}
    storage = {}
    for name, value in legacy.items():
        merged[str(name)] = copy.deepcopy(value)
        storage[str(name)] = "legacy"
    for name, value in nested.items():
        merged[str(name)] = copy.deepcopy(value)
        storage[str(name)] = "agent"
    return merged, storage


def _normalize_personality_value(value) -> object:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return str(value or "")
    system_prompt = str(value.get("system_prompt") or value.get("prompt") or "").strip()
    description = str(value.get("description") or "").strip()
    tone = str(value.get("tone") or "").strip()
    style = str(value.get("style") or "").strip()
    metadata = copy.deepcopy(value.get("metadata")) if isinstance(value.get("metadata"), dict) else None
    extra = {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"prompt", "system_prompt", "description", "tone", "style", "metadata"}
    }
    if not any((description, tone, style, metadata, extra)):
        return system_prompt
    normalized = {}
    if description:
        normalized["description"] = description
    if system_prompt:
        normalized["system_prompt"] = system_prompt
    if tone:
        normalized["tone"] = tone
    if style:
        normalized["style"] = style
    if metadata:
        normalized["metadata"] = metadata
    normalized.update(extra)
    return normalized


def _personality_system_prompt(value) -> str:
    if isinstance(value, dict):
        parts = [str(value.get("system_prompt") or value.get("prompt") or "").strip()]
        if value.get("tone"):
            parts.append(f"Tone: {value['tone']}")
        if value.get("style"):
            parts.append(f"Style: {value['style']}")
        return "\n".join(part for part in parts if part)
    return str(value or "")


def _personality_entry_for_api(name: str, value) -> dict:
    normalized = _normalize_personality_value(value)
    metadata = normalized.get("metadata") if isinstance(normalized, dict) and isinstance(normalized.get("metadata"), dict) else {}
    hermes_meta = metadata.get("hermes_web_ui") if isinstance(metadata, dict) else {}
    return {
        "name": name,
        "kind": str((hermes_meta or {}).get("capability_type") or "personality"),
        "description": str((normalized.get("description") if isinstance(normalized, dict) else "") or "").strip(),
        "system_prompt": _personality_system_prompt(normalized),
        "value": cfg.mask_secrets(copy.deepcopy(normalized)),
        "metadata": cfg.mask_secrets(copy.deepcopy(metadata)) if metadata else {},
    }


def _capability_catalog() -> dict:
    raw = cfg.get_raw()
    integrations = _integration_entries(raw)
    skills = _discover_skill_entries()
    configured_integrations = [entry for entry in integrations if entry.get("configured")]
    model_roles = {
        role: _model_role_info(role)
        for role in MODEL_ROLE_LABELS
    }
    profiles = []
    usage_map = _provider_usage_map(raw=raw)
    for profile in _available_provider_profiles(raw):
        safe = cfg.mask_secrets(profile)
        safe["used_by"] = usage_map.get(profile.get("name", ""), [])
        safe["has_api_key"] = bool(profile.get("api_key") or _provider_env_api_key(profile.get("provider")))
        safe["provider_label"] = _provider_display_name(profile.get("provider", ""))
        profiles.append(safe)
    personalities, storage = _agent_personality_entries(raw)
    return {
        "recommended_order": list(CAPABILITY_IMPLEMENTATION_ORDER),
        "architecture_rules": list(CAPABILITY_ARCHITECTURE_RULES),
        "mvp_scope": list(CAPABILITY_MVP_SCOPE),
        "types": [
            {
                **definition,
                "status_label": _capability_status_badge(definition.get("status", "")),
            }
            for definition in (CAPABILITY_TYPE_DEFINITIONS[key] for key in CAPABILITY_RECOMMENDED_ORDER)
        ],
        "context": {
            "skills_dir": str(SKILLS_DIR),
            "integrations_total": len(integrations),
            "integrations_configured": len(configured_integrations),
            "integration_names": [entry.get("name") for entry in integrations if entry.get("name")],
            "integration_options": _capability_integration_options(raw),
            "model_roles": model_roles,
            "provider_profiles": profiles,
            "skills": [
                {
                    "path": skill.get("path"),
                    "name": skill.get("name"),
                    "description": skill.get("description"),
                    "enabled": bool(skill.get("enabled")),
                    "ready": bool(((skill.get("setup") or {}).get("ready"))),
                }
                for skill in skills
            ],
            "agent_defaults": {
                key: value
                for key, value in _agent_defaults(raw).items()
                if key != "personalities"
            },
            "personality_names": sorted(personalities.keys(), key=str.casefold),
            "personality_storage": storage,
        },
    }


def _normalize_capability_env_var(entry) -> dict:
    if isinstance(entry, str):
        entry = {"key": entry}
    if not isinstance(entry, dict):
        return {}
    raw_key = str(entry.get("key") or "").strip().upper()
    key = re.sub(r"[^A-Z0-9_]", "_", raw_key).strip("_")
    if not key:
        return {}
    base = _env_var_metadata(key)
    group = str(entry.get("group") or base.get("group") or _classify_env_key(key)).strip()
    if group not in ENV_GROUP_HELP:
        group = _classify_env_key(key)
    label = str(entry.get("label") or base.get("label") or key).strip() or key
    description = str(entry.get("description") or base.get("description") or "").strip()
    default_value = str(entry.get("default_value") or base.get("default_value") or "").strip()
    secret = bool(entry.get("secret")) if "secret" in entry else bool(base.get("secret"))
    recommended = bool(entry.get("recommended")) if "recommended" in entry else bool(base.get("recommended"))
    return {
        "key": key,
        "group": group,
        "label": label,
        "description": description,
        "default_value": default_value,
        "secret": secret,
        "recommended": recommended,
    }


def _normalize_capability_credential_file(entry) -> dict:
    if isinstance(entry, str):
        entry = {"path": entry}
    if not isinstance(entry, dict):
        return {}
    rel_path = _safe_skill_rel_path(entry.get("path") or "")
    if not rel_path:
        return {}
    label = str(entry.get("label") or Path(rel_path).name).strip() or Path(rel_path).name
    description = str(entry.get("description") or "").strip()
    return {
        "path": rel_path,
        "label": label,
        "description": description,
    }


def _normalize_capability_required_command(entry) -> dict:
    if isinstance(entry, str):
        entry = {"name": entry}
    if not isinstance(entry, dict):
        return {}
    name = str(entry.get("name") or "").strip()
    if not name:
        return {}
    description = str(entry.get("description") or "").strip()
    return {
        "name": name,
        "description": description,
    }


def _normalize_capability_env_assignment(entry) -> dict:
    if isinstance(entry, str):
        entry = {"key": entry}
    if not isinstance(entry, dict):
        return {}
    normalized = _normalize_capability_env_var(entry)
    if not normalized:
        return {}
    value = entry.get("value")
    normalized["value"] = str(value) if value is not None else ""
    return normalized


def _restore_text_file(path: Path, previous_text: str | None) -> None:
    if previous_text is None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(previous_text, encoding="utf-8")


def _normalize_integration_capability_draft(data: dict | None) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    kind = str(payload.get("kind") or payload.get("name") or "").strip().lower()
    config = payload.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except Exception:
            config = {"__invalid_json__": True}
    if config is None:
        config = copy.deepcopy(INTEGRATION_CONFIG_TEMPLATES.get(kind) or {})
    env_vars = []
    seen_env_keys = set()
    for entry in payload.get("env_vars") if isinstance(payload.get("env_vars"), list) else []:
        normalized = _normalize_capability_env_assignment(entry)
        key = normalized.get("key")
        if not key or key in seen_env_keys:
            continue
        seen_env_keys.add(key)
        env_vars.append(normalized)

    normalized = {
        "kind": kind,
        "label": INTEGRATION_SECTION_LABELS.get(kind, kind.title()),
        "config": copy.deepcopy(config) if isinstance(config, dict) else config,
        "env_vars": env_vars,
    }
    errors = []
    if kind not in INTEGRATION_SECTION_LABELS:
        errors.append("Integration kind is required")
    if not isinstance(config, dict) or config.get("__invalid_json__"):
        errors.append("Integration config must be a JSON object")
    return normalized, errors


def _integration_capability_conflicts(kind: str, raw: dict | None = None) -> list[str]:
    raw = raw if raw is not None else cfg.get_raw()
    current = raw.get(kind)
    if isinstance(current, dict) and _integration_config_is_configured(current):
        return [str(CONFIG_PATH)]
    return []


def _integration_capability_readiness(draft: dict, env_values: dict[str, str] | None = None) -> dict:
    env_values = env_values or {}
    blockers = []
    issues = []
    for entry in draft.get("env_vars") or []:
        key = entry.get("key") or ""
        value = str(entry.get("value") or env_values.get(key) or "").strip()
        if value:
            continue
        blockers.append({
            "kind": "env_var",
            "key": key,
            "group": entry.get("group") or _classify_env_key(key),
            "message": f"missing env var {key}",
        })
        issues.append(f"missing env var {key}")
    if not _integration_config_is_configured(draft.get("config") or {}):
        blockers.append({
            "kind": "config",
            "message": "integration config is still empty",
        })
        issues.append("integration config is still empty")
    return {
        "ready": not blockers,
        "issues": issues,
        "blockers": blockers,
    }


def _preview_integration_capability(data: dict | None) -> tuple[dict, int]:
    draft, errors = _normalize_integration_capability_draft(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    raw = cfg.get_raw()
    env_values = dotenv_values(str(ENV_PATH)) if ENV_PATH.exists() else {}
    current = raw.get(draft["kind"])
    exists = isinstance(current, dict)
    conflicts = _integration_capability_conflicts(draft["kind"], raw=raw)
    readiness = _integration_capability_readiness(draft, env_values=env_values)

    next_raw = copy.deepcopy(raw)
    next_raw[draft["kind"]] = copy.deepcopy(draft["config"])
    integration_entry = next(
        (entry for entry in _integration_entries(next_raw) if entry.get("name") == draft["kind"]),
        {
            "name": draft["kind"],
            "label": draft["label"],
            "kind": "integration",
            "configured": _integration_config_is_configured(draft["config"]),
            "config": cfg.mask_secrets(copy.deepcopy(draft["config"])),
            "source": "top_level",
        },
    )
    integration_entry["readiness"] = readiness

    writes = [{
        "kind": "file",
        "path": str(CONFIG_PATH),
        "action": "update" if exists else "create",
        "label": f"Set {draft['label']} integration block",
        "content": json.dumps(draft.get("config") or {}, indent=2, sort_keys=True),
    }]
    env_overrides = []
    for entry in draft.get("env_vars") or []:
        key = entry.get("key") or ""
        value = str(entry.get("value") or "")
        current_value = str(env_values.get(key) or "")
        if value:
            if current_value and current_value != value:
                env_overrides.append(key)
            writes.append({
                "kind": "file",
                "path": str(ENV_PATH),
                "action": "update" if ENV_PATH.exists() else "create",
                "label": f"Set env var {key}",
                "key": key,
                "content": _mask_value(key, value),
            })

    warnings = []
    if conflicts:
        warnings.append("This integration is already configured. Edit it from Apps & Integrations instead of creating it again.")
    if not _integration_config_is_configured(draft.get("config") or {}):
        warnings.append("The config block is still empty, so Apps & Integrations may continue to show it as Empty.")
    for key in env_overrides:
        warnings.append(f"{key} already exists in ~/.hermes/.env and will be overwritten.")
    if exists and not conflicts:
        warnings.append("This will fill an existing empty integration section in config.yaml.")

    preview_payload = {
        "draft": draft,
        "integration": {
            **integration_entry,
            "config_raw": copy.deepcopy(draft["config"]),
            "env_vars": [
                {
                    key: value
                    for key, value in entry.items()
                    if key != "value"
                }
                for entry in (draft.get("env_vars") or [])
            ],
            "readiness": readiness,
        },
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = _capability_preview_token("integration", preview_payload)
    return {
        "ok": True,
        "type": "integration",
        "phase": "Phase 2",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("label") or draft.get("kind") or "Integration",
            "kind": draft.get("kind") or "",
            "target_dir": str(CONFIG_PATH),
            "env_var_count": len(draft.get("env_vars") or []),
            "env_write_count": len([entry for entry in (draft.get("env_vars") or []) if str(entry.get("value") or "").strip()]),
            "configured": bool(integration_entry.get("configured")),
            "conflict_count": len(conflicts),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "integration": {
                key: value
                for key, value in preview_payload["integration"].items()
                if key != "config_raw"
            },
            "integration_config": copy.deepcopy(draft["config"]),
        },
    }, 200


def _apply_integration_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    preview, status = _preview_integration_capability(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "This integration is already configured. Edit it from Apps & Integrations instead."}, 409

    draft = preview.get("draft") or {}
    config_before = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else None
    env_before = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else None
    try:
        for entry in draft.get("env_vars") or []:
            value = str(entry.get("value") or "")
            if not value:
                continue
            ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
            _set_env_value(ENV_PATH, entry.get("key") or "", value)
        cfg.set(str(draft.get("kind") or ""), copy.deepcopy(draft.get("config") or {}))
    except Exception:
        _restore_text_file(ENV_PATH, env_before)
        _restore_text_file(CONFIG_PATH, config_before)
        cfg.load()
        raise

    cfg.load()
    created = next(
        (entry for entry in _integration_entries() if entry.get("name") == draft.get("kind")),
        None,
    )
    return {
        "ok": True,
        "type": "integration",
        "created": {
            "name": draft.get("label") or draft.get("kind") or "Integration",
            "kind": draft.get("kind") or "",
            "target_dir": str(CONFIG_PATH),
            "files": [str(CONFIG_PATH)] + ([str(ENV_PATH)] if any(str(item.get("value") or "").strip() for item in (draft.get("env_vars") or [])) else []),
            "integration": created,
        },
    }, 200


def _normalize_agent_preset_role(role: str, payload, profile_names: set[str]) -> tuple[dict, list[str]]:
    data = payload if isinstance(payload, dict) else {}
    profile = str(data.get("profile") or "").strip()
    model = str(data.get("model") or "").strip()
    routing_provider = str(data.get("routing_provider") or "").strip()
    enabled = role == "primary" or bool(data.get("enabled")) or bool(profile or model or routing_provider)
    normalized = {
        "enabled": enabled,
        "profile": profile,
        "model": model,
        "routing_provider": routing_provider,
    }
    errors = []
    if enabled and not profile:
        errors.append(f"{MODEL_ROLE_LABELS.get(role, role.title())} requires a provider profile")
    if enabled and not model:
        errors.append(f"{MODEL_ROLE_LABELS.get(role, role.title())} requires a model")
    if profile and profile not in profile_names:
        errors.append(f"{MODEL_ROLE_LABELS.get(role, role.title())} profile '{profile}' was not found")
    return normalized, errors


def _render_agent_preset_fragment(name: str, personality: dict) -> str:
    fragment = {
        "agent": {
            "personalities": {
                name: personality,
            }
        }
    }
    return yaml.safe_dump(
        fragment,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip() + "\n"


def _normalize_agent_preset_draft(data: dict | None) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    raw = cfg.get_raw()
    profile_names = {
        str(profile.get("name") or "").strip()
        for profile in _available_provider_profiles(raw)
        if str(profile.get("name") or "").strip()
    }
    skill_map = {
        str(skill.get("path") or "").strip(): skill
        for skill in _discover_skill_entries()
        if str(skill.get("path") or "").strip()
    }
    integration_names = {
        str(item.get("name") or "").strip()
        for item in _capability_integration_options(raw)
        if str(item.get("name") or "").strip()
    }

    name = str(payload.get("name") or "").strip()
    description = str(payload.get("description") or "").strip()
    system_prompt = str(payload.get("system_prompt") or payload.get("prompt") or "").strip()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
    max_turns_raw = payload.get("max_turns")
    max_turns = None
    if max_turns_raw not in (None, "", False):
        try:
            max_turns = int(max_turns_raw)
        except (TypeError, ValueError):
            max_turns = "invalid"

    roles = {}
    errors = []
    role_payload = payload.get("roles") if isinstance(payload.get("roles"), dict) else {}
    for role in MODEL_ROLE_LABELS:
        normalized_role, role_errors = _normalize_agent_preset_role(role, role_payload.get(role), profile_names)
        roles[role] = normalized_role
        errors.extend(role_errors)

    skills = []
    seen_skills = set()
    for value in payload.get("skills") if isinstance(payload.get("skills"), list) else []:
        path = str(value or "").strip()
        if not path or path in seen_skills:
            continue
        seen_skills.add(path)
        if path not in skill_map:
            errors.append(f"Selected skill '{path}' was not found")
            continue
        skills.append(path)

    integrations = []
    seen_integrations = set()
    for value in payload.get("integrations") if isinstance(payload.get("integrations"), list) else []:
        name_value = str(value or "").strip()
        if not name_value or name_value in seen_integrations:
            continue
        seen_integrations.add(name_value)
        if name_value not in integration_names:
            errors.append(f"Selected integration '{name_value}' was not found")
            continue
        integrations.append(name_value)

    normalized = {
        "name": name[:120].rstrip(),
        "description": description[:400].rstrip(),
        "system_prompt": system_prompt[:12000].rstrip(),
        "roles": roles,
        "skills": skills,
        "integrations": integrations,
        "reasoning_effort": reasoning_effort,
        "max_turns": max_turns,
    }
    if not normalized["name"]:
        errors.append("Preset name is required")
    if normalized["max_turns"] == "invalid" or (isinstance(normalized["max_turns"], int) and normalized["max_turns"] <= 0):
        errors.append("Max turns must be a positive integer")
    if normalized["reasoning_effort"] and normalized["reasoning_effort"] not in AGENT_REASONING_EFFORT_OPTIONS:
        errors.append("Reasoning effort must be one of none, low, medium, high, xhigh, or minimal")
    return normalized, errors


def _agent_preset_conflicts(name: str, raw: dict | None = None) -> list[str]:
    personalities, _ = _agent_personality_entries(raw)
    if str(name or "").strip() in personalities:
        return [str(CONFIG_PATH)]
    return []


def _agent_preset_personality_manifest(draft: dict) -> dict:
    metadata = {
        "hermes_web_ui": {
            "capability_type": "agent_preset",
            "schema_version": 1,
            "created_via": "hermes-web-ui",
            "model_roles": copy.deepcopy(draft.get("roles") or {}),
            "skills": list(draft.get("skills") or []),
            "integrations": list(draft.get("integrations") or []),
            "agent_defaults": {},
        }
    }
    if draft.get("reasoning_effort"):
        metadata["hermes_web_ui"]["agent_defaults"]["reasoning_effort"] = draft["reasoning_effort"]
    if isinstance(draft.get("max_turns"), int):
        metadata["hermes_web_ui"]["agent_defaults"]["max_turns"] = draft["max_turns"]
    if not metadata["hermes_web_ui"]["agent_defaults"]:
        metadata["hermes_web_ui"].pop("agent_defaults", None)
    personality = {
        "description": draft.get("description") or f"{draft.get('name') or 'Preset'} created in Hermes Web UI.",
        "system_prompt": draft.get("system_prompt") or draft.get("description") or f"You are the {draft.get('name') or 'agent preset'} preset.",
        "metadata": metadata,
    }
    return personality


def _preview_agent_preset_capability(data: dict | None) -> tuple[dict, int]:
    draft, errors = _normalize_agent_preset_draft(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    raw = cfg.get_raw()
    personalities, storage = _agent_personality_entries(raw)
    skill_map = {
        str(skill.get("path") or "").strip(): skill
        for skill in _discover_skill_entries()
        if str(skill.get("path") or "").strip()
    }
    integration_map = {
        str(item.get("name") or "").strip(): item
        for item in _capability_integration_options(raw)
        if str(item.get("name") or "").strip()
    }

    conflicts = _agent_preset_conflicts(draft["name"], raw=raw)
    warnings = []
    if conflicts:
        existing_source = storage.get(draft["name"], "agent")
        warnings.append(f"A preset or personality already exists with this name in {existing_source}.")

    skill_details = []
    for path in draft.get("skills") or []:
        skill = skill_map.get(path) or {}
        if not skill.get("enabled"):
            warnings.append(f"Skill '{path}' is currently disabled.")
        elif not ((skill.get("setup") or {}).get("ready", True)):
            warnings.append(f"Skill '{path}' still needs setup.")
        skill_details.append({
            "path": path,
            "name": skill.get("name") or path,
            "enabled": bool(skill.get("enabled")),
            "ready": bool((skill.get("setup") or {}).get("ready", True)),
        })

    integration_details = []
    for name in draft.get("integrations") or []:
        item = integration_map.get(name) or {}
        if not item.get("configured"):
            warnings.append(f"Integration '{name}' is not configured yet.")
        integration_details.append({
            "name": name,
            "label": item.get("label") or name,
            "configured": bool(item.get("configured")),
        })

    personality = _agent_preset_personality_manifest(draft)
    writes = [{
        "kind": "file",
        "path": str(CONFIG_PATH),
        "action": "update",
        "label": f"Save preset {draft['name']} under agent.personalities",
        "content": _render_agent_preset_fragment(draft["name"], personality),
    }]
    preview_payload = {
        "draft": draft,
        "personality": personality,
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = _capability_preview_token("agent_preset", preview_payload)
    return {
        "ok": True,
        "type": "agent_preset",
        "phase": "Phase 3",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("name") or "Agent Preset",
            "target_dir": str(CONFIG_PATH),
            "skill_count": len(draft.get("skills") or []),
            "integration_count": len(draft.get("integrations") or []),
            "enabled_role_count": len([role for role, entry in (draft.get("roles") or {}).items() if entry.get("enabled")]),
            "conflict_count": len(conflicts),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "personality": personality,
            "skills": skill_details,
            "integrations": integration_details,
            "storage_path": f"agent.personalities.{draft['name']}",
            "existing_personality": copy.deepcopy(personalities.get(draft["name"])) if draft["name"] in personalities else None,
        },
    }, 200


def _apply_agent_preset_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    preview, status = _preview_agent_preset_capability(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "A preset or personality already exists with this name."}, 409

    draft = preview.get("draft") or {}
    personality = copy.deepcopy(((preview.get("manifest") or {}).get("personality")) or {})
    config_before = CONFIG_PATH.read_text(encoding="utf-8") if CONFIG_PATH.exists() else None
    try:
        raw = cfg.get_raw()
        agent_cfg = raw.get("agent", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        personalities = agent_cfg.get("personalities", {})
        if not isinstance(personalities, dict):
            personalities = {}
        personalities[str(draft.get("name") or "")] = personality
        agent_cfg["personalities"] = personalities
        cfg.set("agent", agent_cfg)
    except Exception:
        _restore_text_file(CONFIG_PATH, config_before)
        cfg.load()
        raise

    cfg.load()
    merged, _ = _agent_personality_entries()
    return {
        "ok": True,
        "type": "agent_preset",
        "created": {
            "name": draft.get("name") or "Agent Preset",
            "target_dir": str(CONFIG_PATH),
            "files": [str(CONFIG_PATH)],
            "personality": copy.deepcopy(merged.get(str(draft.get("name") or ""))),
        },
    }, 200


def _normalize_skill_capability_draft(data: dict | None) -> tuple[dict, list[str]]:
    payload = data if isinstance(data, dict) else {}
    name = str(payload.get("name") or "").strip()
    slug = _slugify_capability(str(payload.get("slug") or "").strip() or name)
    category = str(payload.get("category") or "").strip()
    description = str(payload.get("description") or "").strip()
    instructions = str(payload.get("instructions") or "").strip()
    include_scripts = bool(payload.get("include_scripts"))
    include_references = bool(payload.get("include_references"))

    env_vars = []
    seen_env_keys = set()
    for entry in payload.get("env_vars") if isinstance(payload.get("env_vars"), list) else []:
        normalized = _normalize_capability_env_var(entry)
        key = normalized.get("key")
        if not key or key in seen_env_keys:
            continue
        seen_env_keys.add(key)
        env_vars.append(normalized)

    credential_files = []
    seen_paths = set()
    for entry in payload.get("credential_files") if isinstance(payload.get("credential_files"), list) else []:
        normalized = _normalize_capability_credential_file(entry)
        rel_path = normalized.get("path")
        if not rel_path or rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        credential_files.append(normalized)

    required_commands = []
    seen_commands = set()
    for entry in payload.get("required_commands") if isinstance(payload.get("required_commands"), list) else []:
        normalized = _normalize_capability_required_command(entry)
        command_name = normalized.get("name")
        if not command_name or command_name in seen_commands:
            continue
        seen_commands.add(command_name)
        required_commands.append(normalized)

    normalized = {
        "name": name[:120].rstrip(),
        "slug": slug,
        "category": category[:120].rstrip(),
        "description": description[:400].rstrip(),
        "instructions": instructions[:12000].rstrip(),
        "env_vars": env_vars,
        "credential_files": credential_files,
        "required_commands": required_commands,
        "include_scripts": include_scripts,
        "include_references": include_references,
    }
    errors = []
    if not normalized["name"]:
        errors.append("Skill name is required")
    if not normalized["slug"]:
        errors.append("Skill slug is required")
    return normalized, errors


def _render_skill_capability_frontmatter(draft: dict) -> dict:
    frontmatter = {
        "name": draft.get("name") or "",
        "description": draft.get("description") or f"{draft.get('name') or 'Skill'} created in Hermes Web UI.",
    }
    if draft.get("category"):
        frontmatter["category"] = draft["category"]
    if draft.get("env_vars"):
        frontmatter["prerequisites"] = {
            "env_vars": [entry.get("key") for entry in draft["env_vars"] if entry.get("key")],
        }
    if draft.get("credential_files"):
        frontmatter["required_credential_files"] = [
            {
                key: value
                for key, value in entry.items()
                if value not in (None, "", [])
            }
            for entry in draft["credential_files"]
        ]

    metadata = {
        "hermes_web_ui": {
            "capability_type": "skill",
            "schema_version": 1,
            "created_via": "hermes-web-ui",
            "setup": {},
        }
    }
    if draft.get("required_commands"):
        metadata["openclaw"] = {
            "requires": {
                "bins": [entry.get("name") for entry in draft["required_commands"] if entry.get("name")],
            }
        }
    setup = metadata["hermes_web_ui"]["setup"]
    if draft.get("env_vars"):
        setup["env_vars"] = draft["env_vars"]
    if draft.get("credential_files"):
        setup["credential_files"] = draft["credential_files"]
    if draft.get("required_commands"):
        setup["required_commands"] = draft["required_commands"]
    if draft.get("include_scripts") or draft.get("include_references"):
        setup["folders"] = {
            "scripts": bool(draft.get("include_scripts")),
            "references": bool(draft.get("include_references")),
        }
    if setup:
        frontmatter["metadata"] = metadata
    return frontmatter


def _render_skill_capability_markdown(draft: dict, frontmatter: dict) -> str:
    frontmatter_yaml = yaml.safe_dump(
        frontmatter,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    instructions = draft.get("instructions") or (
        f"Describe when to use the {draft.get('name') or 'skill'} capability, what steps it should follow, "
        "and any constraints or output expectations."
    )
    lines = [
        "---",
        frontmatter_yaml,
        "---",
        "",
        f"# {draft.get('name') or 'New Skill'}",
        "",
        draft.get("description") or "Reusable Hermes skill created in Hermes Web UI.",
        "",
        "## Instructions",
        instructions,
    ]

    env_vars = draft.get("env_vars") or []
    credential_files = draft.get("credential_files") or []
    required_commands = draft.get("required_commands") or []
    if env_vars or credential_files or required_commands:
        lines.extend(["", "## Setup Requirements"])
        if env_vars:
            lines.append("")
            lines.append("### Environment Variables")
            for entry in env_vars:
                detail = str(entry.get("description") or "").strip()
                label = str(entry.get("label") or entry.get("key") or "").strip()
                line = f"- `{entry.get('key')}`"
                if label and label != entry.get("key"):
                    line += f" - {label}"
                if detail:
                    line += f": {detail}"
                lines.append(line)
        if credential_files:
            lines.append("")
            lines.append("### Credential Files")
            for entry in credential_files:
                detail = str(entry.get("description") or "").strip()
                line = f"- `{entry.get('path')}`"
                if detail:
                    line += f": {detail}"
                lines.append(line)
        if required_commands:
            lines.append("")
            lines.append("### Required Commands")
            for entry in required_commands:
                detail = str(entry.get("description") or "").strip()
                line = f"- `{entry.get('name')}`"
                if detail:
                    line += f": {detail}"
                lines.append(line)

    included_folders = []
    if draft.get("include_scripts"):
        included_folders.append("`scripts/` for helper automation")
    if draft.get("include_references"):
        included_folders.append("`references/` for docs and examples")
    if included_folders:
        lines.extend(["", "## Included Folders"])
        lines.extend([f"- {item}" for item in included_folders])

    return "\n".join(lines).rstrip() + "\n"


def _capability_skill_source_metadata() -> dict:
    return _build_skill_source_record(
        "hermes-web-ui/create-skill",
        install_mode="webui_create",
        display="Hermes Web UI",
        catalog_source="create-capability",
    )


def _capability_skill_conflicts(slug: str) -> list[str]:
    info = _skill_request_paths(slug)
    if not info:
        return []
    return [
        str(path)
        for path in info.get("variants") or []
        if isinstance(path, Path) and path.exists()
    ]


def _preview_skill_capability(data: dict | None) -> tuple[dict, int]:
    draft, errors = _normalize_skill_capability_draft(data)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}, 400

    frontmatter = _render_skill_capability_frontmatter(draft)
    skill_md = _render_skill_capability_markdown(draft, frontmatter)
    source = _capability_skill_source_metadata()
    skill = {
        "name": frontmatter.get("name") or draft.get("name") or draft.get("slug") or "Skill",
        "category": frontmatter.get("category") or "",
        "description": frontmatter.get("description") or "",
        "path": draft.get("slug") or "",
        "enabled": True,
        "frontmatter": frontmatter,
        "source": source,
    }
    skill["setup"] = _skill_setup_readiness(skill)

    target_dir = SKILLS_DIR / draft["slug"]
    writes = [{
        "kind": "directory",
        "path": str(target_dir),
        "action": "create",
        "label": "Skill folder",
    }, {
        "kind": "file",
        "path": str(target_dir / "SKILL.md"),
        "action": "create",
        "label": "Skill instructions",
        "content": skill_md,
    }]
    if draft.get("include_scripts"):
        writes.append({
            "kind": "directory",
            "path": str(target_dir / "scripts"),
            "action": "create",
            "label": "Optional scripts folder",
        })
    if draft.get("include_references"):
        writes.append({
            "kind": "directory",
            "path": str(target_dir / "references"),
            "action": "create",
            "label": "Optional references folder",
        })

    conflicts = _capability_skill_conflicts(draft["slug"])
    warnings = []
    if conflicts:
        warnings.append("A skill already exists for this slug. Change the slug before approval.")

    preview_payload = {
        "draft": draft,
        "skill": {
            **skill,
            "setup": skill.get("setup") or {},
        },
        "writes": writes,
        "conflicts": conflicts,
    }
    preview_token = _capability_preview_token("skill", preview_payload)
    return {
        "ok": True,
        "type": "skill",
        "phase": "Phase 1",
        "preview_token": preview_token,
        "can_apply": not conflicts,
        "draft": draft,
        "summary": {
            "name": draft.get("name") or draft.get("slug") or "Skill",
            "slug": draft.get("slug") or "",
            "target_dir": str(target_dir),
            "description": frontmatter.get("description") or "",
            "conflict_count": len(conflicts),
            "env_var_count": len(draft.get("env_vars") or []),
            "credential_file_count": len(draft.get("credential_files") or []),
            "required_command_count": len(draft.get("required_commands") or []),
        },
        "warnings": warnings,
        "conflicts": conflicts,
        "writes": writes,
        "manifest": {
            "skill": skill,
        },
    }, 200


def _apply_skill_capability(data: dict | None, preview_token: str) -> tuple[dict, int]:
    preview, status = _preview_skill_capability(data)
    if status != 200:
        return preview, status
    if not preview_token or preview_token != preview.get("preview_token"):
        return {"ok": False, "error": "Preview has changed. Refresh the draft preview before approval."}, 409
    if not preview.get("can_apply"):
        return {"ok": False, "error": "This skill slug is already taken. Change the slug and preview again."}, 409

    draft = preview.get("draft") or {}
    target_dir = SKILLS_DIR / str(draft.get("slug") or "")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        return {"ok": False, "error": "This skill already exists on disk."}, 409
    tmp_dir = target_dir.parent / f".{target_dir.name}.tmp-{uuid.uuid4().hex[:8]}"
    skill_md_content = next(
        (entry.get("content") for entry in (preview.get("writes") or []) if entry.get("path") == str(target_dir / "SKILL.md")),
        "",
    )
    try:
        tmp_dir.mkdir(parents=False, exist_ok=False)
        (tmp_dir / "SKILL.md").write_text(
            skill_md_content,
            encoding="utf-8",
        )
        if draft.get("include_scripts"):
            (tmp_dir / "scripts").mkdir(exist_ok=True)
        if draft.get("include_references"):
            (tmp_dir / "references").mkdir(exist_ok=True)
        _write_skill_source_metadata(tmp_dir, _capability_skill_source_metadata())
        tmp_dir.rename(target_dir)
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    try:
        created_skill = next(
            (entry for entry in _discover_skill_entries() if entry.get("path") == draft.get("slug")),
            None,
        )
    except Exception:
        created_skill = copy.deepcopy(((preview.get("manifest") or {}).get("skill") if isinstance(preview.get("manifest"), dict) else {}) or None)
    return {
        "ok": True,
        "type": "skill",
        "created": {
            "name": draft.get("name") or draft.get("slug") or "Skill",
            "slug": draft.get("slug") or "",
            "target_dir": str(target_dir),
            "files": [entry.get("path") for entry in (preview.get("writes") or []) if entry.get("path")],
            "skill": created_skill,
        },
    }, 200


OFFICIAL_HERMES_REPO_URLS = {
    "https://github.com/NousResearch/hermes-agent.git",
    "https://github.com/NousResearch/hermes-agent",
    "git@github.com:NousResearch/hermes-agent.git",
    "git@github.com:NousResearch/hermes-agent",
}
OFFICIAL_HERMES_REPO_URL = "https://github.com/NousResearch/hermes-agent.git"
HERMES_REINSTALL_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash"
)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_gateway_pid_record() -> dict:
    pid_file = _selected_gateway_pid_path()
    if not pid_file.exists():
        return {}
    try:
        raw = pid_file.read_text(encoding="utf-8").strip()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        return payload
    if raw.isdigit():
        return {"pid": int(raw)}
    return {"raw": raw}


def _gateway_pid_record_is_live(record: dict) -> bool:
    pid = record.get("pid")
    return isinstance(pid, int) and _is_process_alive(pid)


def _candidate_hermes_bins() -> list[dict]:
    candidates = []
    seen = set()

    def _add(raw_path, source: str):
        if not raw_path:
            return
        try:
            path = Path(raw_path).expanduser()
        except Exception:
            return
        if not path.exists():
            return
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "path": path,
            "resolved_path": resolved,
            "source": source,
        })

    gateway_record = _read_gateway_pid_record()
    argv = gateway_record.get("argv") if _gateway_pid_record_is_live(gateway_record) else None

    _add(os.environ.get("HERMES_WEBUI_HERMES_BIN"), "env_override")
    _add(os.environ.get("HERMES_BIN"), "env_hint")
    _add(HERMES_REPO_DIR / "venv" / "bin" / "hermes", "managed_repo")
    _add(Path.home() / ".local" / "bin" / "hermes", "user_local_bin")
    _add(shutil.which("hermes"), "path_lookup")
    _add(HERMES_BIN, "webui_default")
    if isinstance(argv, list) and argv:
        _add(argv[0], "active_gateway")
    _add(HERMES_HOME / ".venv" / "bin" / "hermes", "legacy_home_venv")
    return candidates


def _selected_hermes_candidate() -> dict:
    candidates = _candidate_hermes_bins()
    if candidates:
        return candidates[0]
    fallback = Path(HERMES_BIN).expanduser()
    try:
        resolved = fallback.resolve(strict=False)
    except Exception:
        resolved = fallback
    return {
        "path": fallback,
        "resolved_path": resolved,
        "source": "fallback",
    }


def _selected_hermes_bin() -> Path:
    return _selected_hermes_candidate()["path"]


def _selection_reason_for_candidate(source: str) -> str:
    if source == "active_gateway":
        return f"Using the live gateway binary recorded in {_selected_gateway_pid_path()}."
    if source == "env_override":
        return "Using the Hermes binary path from HERMES_WEBUI_HERMES_BIN."
    if source == "env_hint":
        return "Using the Hermes binary path from HERMES_BIN."
    if source == "managed_repo":
        return f"Using the repo-managed Hermes install under {HERMES_REPO_DIR}."
    if source == "legacy_home_venv":
        return f"Using the Hermes venv rooted at {_selected_hermes_home()}."
    if source == "user_local_bin":
        return "Using the Hermes launcher from ~/.local/bin."
    if source == "path_lookup":
        return "Using the Hermes binary found on PATH."
    return "Using the default Hermes binary configured for Hermes Web UI."


def _run_hermes_with_bin(bin_path: Path, *args, timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "NO_COLOR": "1", "HERMES_HOME": str(_selected_hermes_home())}
    return subprocess.run(
        [str(bin_path)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
    )


def _run_hermes(*args, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a hermes CLI command with the currently managed Hermes binary."""
    return _run_hermes_with_bin(_selected_hermes_bin(), *args, timeout=timeout)


def _combined_process_output(result: subprocess.CompletedProcess) -> str:
    return "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part and part.strip()
    ).strip()


def _first_output_line(*parts) -> str:
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        return text.splitlines()[0].strip()
    return ""


def _build_version_display(version: str = "", release_date: str = "") -> str:
    version = str(version or "").strip()
    release_date = str(release_date or "").strip()
    if version and release_date:
        return f"Hermes Agent v{version} ({release_date})"
    if version:
        return f"Hermes Agent v{version}"
    return "Unknown"


def _parse_hermes_version_output(raw_output: str) -> dict:
    lines = [line.strip() for line in str(raw_output or "").splitlines() if line.strip()]
    first_line = lines[0] if lines else ""
    match = re.search(r"Hermes Agent v?([^\s]+)(?: \(([^)]+)\))?", first_line)
    project_root = ""
    python_version = ""
    openai_sdk = ""
    update_hint = ""
    for line in lines[1:]:
        if line.startswith("Project:"):
            project_root = line.split(":", 1)[1].strip()
        elif line.startswith("Python:"):
            python_version = line.split(":", 1)[1].strip()
        elif line.startswith("OpenAI SDK:"):
            openai_sdk = line.split(":", 1)[1].strip()
        elif line.lower() == "up to date" or line.lower().startswith("update available:"):
            update_hint = line
    version = match.group(1).strip() if match else ""
    release_date = match.group(2).strip() if match and match.group(2) else ""
    return {
        "raw": str(raw_output or "").strip(),
        "display": _build_version_display(version, release_date) if match else first_line or "Unknown",
        "version": version,
        "release_date": release_date,
        "project_root": project_root,
        "python_version": python_version,
        "openai_sdk": openai_sdk,
        "update_hint": update_hint,
        "first_line": first_line,
    }


def _extract_version_from_git_init(raw_text: str) -> dict:
    version_match = re.search(r'__version__\s*=\s*"([^"]+)"', str(raw_text or ""))
    release_match = re.search(r'__release_date__\s*=\s*"([^"]+)"', str(raw_text or ""))
    version = version_match.group(1).strip() if version_match else ""
    release_date = release_match.group(1).strip() if release_match else ""
    return {
        "version": version,
        "release_date": release_date,
        "display": _build_version_display(version, release_date),
    }


def _classify_update_scope(availability_status: str, installed: dict, repo_state: dict) -> str:
    if availability_status == "up_to_date":
        return "current"
    if availability_status != "update_available":
        return "unknown"

    behind_commits = (repo_state or {}).get("behind_commits")
    if not isinstance(behind_commits, int) or behind_commits <= 0:
        return "unknown"

    latest = (repo_state or {}).get("latest_version") or {}
    installed_version = str((installed or {}).get("version") or "").strip()
    installed_release_date = str((installed or {}).get("release_date") or "").strip()
    latest_version = str(latest.get("version") or "").strip()
    latest_release_date = str(latest.get("release_date") or "").strip()

    same_version = bool(installed_version and latest_version and installed_version == latest_version)
    same_release_date = bool(
        installed_release_date and latest_release_date and installed_release_date == latest_release_date
    )
    if same_version and (not latest_release_date or not installed_release_date or same_release_date):
        return "revision"
    if same_release_date and not latest_version:
        return "revision"
    if (
        (latest_version and installed_version and latest_version != installed_version)
        or (latest_release_date and installed_release_date and latest_release_date != installed_release_date)
    ):
        return "release"
    if latest_version or latest_release_date:
        return "release"
    return "revision"


def _normalized_git_url(url: str) -> str:
    normalized = str(url or "").strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def _is_official_hermes_remote(url: str) -> bool:
    normalized = _normalized_git_url(url)
    return normalized in {_normalized_git_url(value) for value in OFFICIAL_HERMES_REPO_URLS}


def _detect_managed_install() -> tuple[str, str]:
    raw = str(os.environ.get("HERMES_MANAGED") or "").strip()
    marker = HERMES_HOME / ".managed"
    value = raw.lower()
    if value in {"homebrew", "brew"}:
        return "Homebrew", "brew upgrade hermes-agent"
    if value in {"true", "1", "yes", "nixos", "nix"} or marker.exists():
        return "NixOS", "sudo nixos-rebuild switch"
    if raw:
        return raw, ""
    return "", ""


def _guess_repo_root(bin_path: Path, project_root: str = "") -> Path | None:
    guesses = []
    if project_root:
        guesses.append(Path(project_root).expanduser())
    try:
        resolved = Path(bin_path).expanduser().resolve(strict=False)
        guesses.append(resolved.parent.parent.parent)
    except Exception:
        pass
    try:
        guesses.append(Path(bin_path).expanduser().parent.parent.parent)
    except Exception:
        pass
    guesses.extend([HERMES_HOME / "hermes-agent", HERMES_HOME])
    seen = set()
    for guess in guesses:
        guess_str = str(guess)
        if guess_str in seen:
            continue
        seen.add(guess_str)
        if (guess / ".git").exists():
            return guess
    return None


def _run_git(repo_dir: Path, *args, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(repo_dir),
    )


def _summarize_git_worktree(repo_dir: Path) -> dict:
    tracked = 0
    untracked = 0
    sample = []
    try:
        result = _run_git(repo_dir, "status", "--porcelain", timeout=10)
    except Exception as exc:
        return {
            "tracked": 0,
            "untracked": 0,
            "total": 0,
            "sample": [],
            "error": str(exc),
        }
    if result.returncode != 0:
        return {
            "tracked": 0,
            "untracked": 0,
            "total": 0,
            "sample": [],
            "error": _first_output_line(result.stderr, result.stdout),
        }
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("??"):
            untracked += 1
        else:
            tracked += 1
        if len(sample) < 10:
            sample.append(line)
    return {
        "tracked": tracked,
        "untracked": untracked,
        "total": tracked + untracked,
        "sample": sample,
        "error": "",
    }


def _select_update_remote(repo_dir: Path) -> dict:
    remotes = []
    for name in ("origin", "upstream"):
        try:
            result = _run_git(repo_dir, "remote", "get-url", name, timeout=5)
        except Exception:
            continue
        if result.returncode != 0:
            continue
        url = str(result.stdout or "").strip()
        if not url:
            continue
        remotes.append({
            "remote": name,
            "url": url,
            "official": _is_official_hermes_remote(url),
        })
    for remote in remotes:
        if remote["official"] and remote["remote"] == "origin":
            return remote
    for remote in remotes:
        if remote["official"]:
            return remote
    if remotes:
        return remotes[0]
    return {"remote": "", "url": "", "official": False}


def _cache_key_for_repo(repo_dir: Path) -> str:
    return str(repo_dir.resolve())


def _invalidate_hermes_update_cache(repo_dir: Path | None = None) -> None:
    with hermes_update_cache_lock:
        if repo_dir is None:
            hermes_update_cache.clear()
            return
        hermes_update_cache.pop(_cache_key_for_repo(repo_dir), None)


def _probe_repo_update_state(repo_dir: Path) -> dict:
    remote = _select_update_remote(repo_dir)
    remote_name = remote.get("remote") or ""
    remote_url = remote.get("url") or ""
    remote_branch = "main"
    remote_ref = f"{remote_name}/{remote_branch}" if remote_name else ""
    fetch_error = ""
    fetched = False

    if remote_name:
        try:
            fetch_result = _run_git(repo_dir, "fetch", remote_name, "--quiet", timeout=20)
            fetched = fetch_result.returncode == 0
            if not fetched:
                fetch_error = _first_output_line(fetch_result.stderr, fetch_result.stdout)
        except Exception as exc:
            fetch_error = str(exc)

    local_commit = ""
    latest_commit = ""
    behind_commits = None
    ahead_commits = None
    latest_version = {"version": "", "release_date": "", "display": "Unknown"}

    try:
        local_result = _run_git(repo_dir, "rev-parse", "--short", "HEAD", timeout=5)
        if local_result.returncode == 0:
            local_commit = str(local_result.stdout or "").strip()
    except Exception:
        pass

    ref_exists = False
    if remote_ref:
        try:
            ref_check = _run_git(repo_dir, "rev-parse", "--verify", remote_ref, timeout=5)
            ref_exists = ref_check.returncode == 0
            if ref_exists:
                latest_commit = str(ref_check.stdout or "").strip()[:8]
        except Exception:
            ref_exists = False

    if ref_exists:
        try:
            behind_result = _run_git(repo_dir, "rev-list", "--count", f"HEAD..{remote_ref}", timeout=10)
            if behind_result.returncode == 0:
                behind_commits = int(str(behind_result.stdout or "0").strip() or "0")
        except Exception:
            behind_commits = None
        try:
            ahead_result = _run_git(repo_dir, "rev-list", "--count", f"{remote_ref}..HEAD", timeout=10)
            if ahead_result.returncode == 0:
                ahead_commits = int(str(ahead_result.stdout or "0").strip() or "0")
        except Exception:
            ahead_commits = None
        try:
            init_result = _run_git(repo_dir, "show", f"{remote_ref}:hermes_cli/__init__.py", timeout=10)
            if init_result.returncode == 0:
                latest_version = _extract_version_from_git_init(init_result.stdout)
        except Exception:
            pass

    availability_status = "unknown_latest"
    if behind_commits is not None:
        availability_status = "update_available" if behind_commits > 0 else "up_to_date"

    return {
        "availability_status": availability_status,
        "checked_at": _utc_now_z(),
        "source": {
            "remote": remote_name,
            "branch": remote_branch,
            "ref": remote_ref,
            "url": remote_url,
            "official": bool(remote.get("official")),
            "label": (
                f"GitHub {remote_name}/{remote_branch}"
                if remote_name and remote.get("official")
                else (f"{remote_name}/{remote_branch}" if remote_name else "Unavailable")
            ),
        },
        "fetched": fetched,
        "fetch_error": fetch_error,
        "behind_commits": behind_commits,
        "ahead_commits": ahead_commits,
        "local_commit": local_commit,
        "latest_commit": latest_commit,
        "latest_version": latest_version,
        "worktree": _summarize_git_worktree(repo_dir),
    }


def _get_repo_update_state(repo_dir: Path, *, force_refresh: bool = False) -> dict:
    cache_key = _cache_key_for_repo(repo_dir)
    now = time.time()
    with hermes_update_cache_lock:
        cached = hermes_update_cache.get(cache_key)
        if (
            cached
            and not force_refresh
            and (now - float(cached.get("ts") or 0)) < HERMES_UPDATE_CACHE_SECONDS
        ):
            return copy.deepcopy(cached["payload"])
    payload = _probe_repo_update_state(repo_dir)
    with hermes_update_cache_lock:
        hermes_update_cache[cache_key] = {
            "ts": now,
            "payload": copy.deepcopy(payload),
        }
    return payload


def _manual_update_command(bin_path: Path, repo_dir: Path | None, managed_system: str, managed_command: str) -> str:
    if managed_command:
        return managed_command
    if repo_dir and (repo_dir / ".git").exists():
        return f"cd {shlex.quote(str(repo_dir))} && {shlex.quote(str(bin_path))} update"
    return HERMES_REINSTALL_COMMAND


def _manual_update_reason(repo_dir: Path | None, managed_system: str, managed_command: str) -> str:
    if managed_command:
        label = managed_system or "your package manager"
        return f"This Hermes install is managed by {label}. Run the manual upgrade command instead."
    if repo_dir and (repo_dir / ".git").exists():
        return ""
    return "This Hermes install is not a git checkout that Hermes can safely update in place."


def _base_update_message(availability_status: str, update_scope: str, installed: dict, repo_state: dict) -> str:
    source_label = (((repo_state or {}).get("source") or {}).get("label") or "the configured update source").strip()
    installed_display = installed.get("display") or "Installed Hermes"
    latest = (repo_state or {}).get("latest_version") or {}
    latest_display = latest.get("display") or ""
    behind_commits = (repo_state or {}).get("behind_commits")

    if availability_status == "up_to_date":
        return f"{installed_display} is current with {source_label}."
    if availability_status == "update_available":
        if update_scope == "revision":
            if behind_commits:
                word = "commit" if behind_commits == 1 else "commits"
                return (
                    f"{installed_display} matches the latest released Hermes version, "
                    f"but {source_label} is {behind_commits} {word} ahead."
                )
            return f"{installed_display} matches the latest released Hermes version, but newer commits are available on {source_label}."
        if latest_display and latest.get("version"):
            return f"{latest_display} is available on {source_label}."
        if behind_commits:
            word = "commit" if behind_commits == 1 else "commits"
            return f"A newer Hermes revision is available on {source_label} ({behind_commits} {word} ahead)."
        return f"A newer Hermes revision is available on {source_label}."
    return f"Couldn't determine the latest Hermes version from {source_label}."


def _runtime_snapshot() -> dict:
    with hermes_update_runtime_lock:
        snapshot = copy.deepcopy(hermes_update_runtime)
    snapshot["log_text"] = "\n".join(snapshot.get("logs") or [])
    return snapshot


def _set_update_runtime(**updates) -> None:
    with hermes_update_runtime_lock:
        hermes_update_runtime.update(updates)


def _append_update_log(line: str) -> None:
    text = str(line or "").rstrip()
    if not text:
        return
    with hermes_update_runtime_lock:
        logs = list(hermes_update_runtime.get("logs") or [])
        logs.append(text)
        if len(logs) > HERMES_UPDATE_LOG_LINE_LIMIT:
            logs = logs[-HERMES_UPDATE_LOG_LINE_LIMIT:]
        hermes_update_runtime["logs"] = logs
        hermes_update_runtime["summary"] = text


def _build_hermes_update_payload(*, force_refresh: bool = False) -> dict:
    selected = _selected_hermes_candidate()
    bin_path = selected["path"]
    resolved_path = selected["resolved_path"]
    version_info = {
        "raw": "",
        "display": "Unknown",
        "version": "",
        "release_date": "",
        "project_root": "",
        "python_version": "",
        "openai_sdk": "",
        "update_hint": "",
        "first_line": "",
    }
    version_error = ""
    try:
        version_result = _run_hermes_with_bin(bin_path, "--version", timeout=20)
        raw_output = (version_result.stdout or "") + ("\n" + version_result.stderr if version_result.stderr else "")
        version_info = _parse_hermes_version_output(raw_output)
    except Exception as exc:
        version_error = str(exc)

    repo_dir = _guess_repo_root(resolved_path, version_info.get("project_root") or "")
    managed_system, managed_command = _detect_managed_install()
    repo_state = {
        "availability_status": "unknown_latest",
        "checked_at": "",
        "source": {"remote": "", "branch": "", "ref": "", "url": "", "official": False, "label": "Unavailable"},
        "fetched": False,
        "fetch_error": "",
        "behind_commits": None,
        "ahead_commits": None,
        "local_commit": "",
        "latest_commit": "",
        "latest_version": {"version": "", "release_date": "", "display": "Unknown"},
        "worktree": {"tracked": 0, "untracked": 0, "total": 0, "sample": [], "error": ""},
    }
    if repo_dir:
        repo_state = _get_repo_update_state(repo_dir, force_refresh=force_refresh)

    availability_status = repo_state.get("availability_status") or "unknown_latest"
    update_scope = _classify_update_scope(availability_status, version_info, repo_state)
    can_update = bool(bin_path.exists() and repo_dir and (repo_dir / ".git").exists() and not managed_command)
    install_method = "binary_only"
    if managed_command:
        install_method = "managed"
    elif repo_dir and repo_dir == HERMES_HOME and ".venv" in str(resolved_path):
        install_method = "git_home_venv"
    elif repo_dir and repo_dir.name == "hermes-agent":
        install_method = "git_repo_venv"
    elif repo_dir:
        install_method = "git_checkout"

    runtime = _runtime_snapshot()
    status = availability_status
    message = _base_update_message(availability_status, update_scope, version_info, repo_state)
    if runtime.get("status") == "update_in_progress":
        status = "update_in_progress"
        message = runtime.get("summary") or f"Updating Hermes from {version_info.get('display') or 'the installed version'}..."
    elif runtime.get("status") == "update_failed":
        status = "update_failed"
        message = runtime.get("error") or runtime.get("summary") or "Hermes update failed."

    other_candidates = [
        str(candidate["path"])
        for candidate in _candidate_hermes_bins()[1:]
    ]

    return {
        "status": status,
        "availability_status": availability_status,
        "update_scope": update_scope,
        "message": message,
        "checked_at": repo_state.get("checked_at") or "",
        "bin_path": str(bin_path),
        "resolved_bin_path": str(resolved_path),
        "project_root": str(repo_dir) if repo_dir else (version_info.get("project_root") or ""),
        "install_method": install_method,
        "selection_reason": _selection_reason_for_candidate(selected.get("source") or ""),
        "other_detected_bins": other_candidates,
        "installed_version": version_info,
        "latest_version": repo_state.get("latest_version") or {"version": "", "release_date": "", "display": "Unknown"},
        "official_source": repo_state.get("source") or {},
        "behind_commits": repo_state.get("behind_commits"),
        "ahead_commits": repo_state.get("ahead_commits"),
        "local_commit": repo_state.get("local_commit") or "",
        "latest_commit": repo_state.get("latest_commit") or "",
        "fetch_error": repo_state.get("fetch_error") or "",
        "fetched": bool(repo_state.get("fetched")),
        "version_error": version_error,
        "can_update": can_update,
        "managed_system": managed_system,
        "manual_command": _manual_update_command(bin_path, repo_dir, managed_system, managed_command),
        "manual_reason": _manual_update_reason(repo_dir, managed_system, managed_command),
        "worktree": repo_state.get("worktree") or {"tracked": 0, "untracked": 0, "total": 0, "sample": [], "error": ""},
        "update_action": runtime,
        "install_key": str(repo_dir or resolved_path or bin_path),
    }


def _run_hermes_update_worker(install_snapshot: dict) -> None:
    bin_path = Path(install_snapshot.get("bin_path") or "")
    repo_dir = Path(install_snapshot.get("project_root") or "").expanduser() if install_snapshot.get("project_root") else None
    install_key = str(install_snapshot.get("install_key") or "")

    try:
        _invalidate_hermes_update_cache(repo_dir)
        proc = subprocess.Popen(
            [str(bin_path), "update"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "NO_COLOR": "1"},
            cwd=str(repo_dir) if repo_dir else None,
            bufsize=1,
        )
        _append_update_log(f"$ {bin_path} update")
        if proc.stdout is not None:
            for line in proc.stdout:
                _append_update_log(line)
        returncode = proc.wait()
        runtime = _runtime_snapshot()
        combined_log = runtime.get("log_text") or ""
        lowered = combined_log.lower()
        restore_conflict = (
            "restoring local changes hit conflicts" in lowered
            or "working tree reset to clean state" in lowered
        )
        _invalidate_hermes_update_cache(repo_dir)
        post_update = _build_hermes_update_payload(force_refresh=True)

        error_message = ""
        if returncode != 0:
            error_message = _first_output_line(combined_log, "Hermes update failed.")
        elif restore_conflict:
            error_message = (
                "Hermes updated, but restoring local changes hit conflicts. Review the update log and reapply the saved git stash if needed."
            )
        elif post_update.get("availability_status") == "update_available":
            behind = post_update.get("behind_commits")
            if isinstance(behind, int) and behind > 0:
                word = "commit" if behind == 1 else "commits"
                error_message = f"Update finished, but Hermes is still {behind} {word} behind the selected update source."

        if error_message:
            _set_update_runtime(
                status="update_failed",
                finished_at=_utc_now_z(),
                returncode=returncode,
                error=error_message,
                summary=error_message,
                install_key=install_key,
                installed_version_after=(post_update.get("installed_version") or {}).get("display") or "",
            )
            return

        _set_update_runtime(
            status="",
            finished_at=_utc_now_z(),
            returncode=returncode,
            error="",
            summary="Hermes update completed successfully.",
            install_key=install_key,
            installed_version_after=(post_update.get("installed_version") or {}).get("display") or "",
        )
    except Exception as exc:
        _set_update_runtime(
            status="update_failed",
            finished_at=_utc_now_z(),
            returncode=-1,
            error=str(exc),
            summary=str(exc),
            install_key=install_key,
        )


def _hermes_skill_install_failed(result: subprocess.CompletedProcess, combined_output: str) -> bool:
    lowered_output = str(combined_output or "").lower()
    if "already installed" in lowered_output:
        return False
    if result.returncode != 0:
        return True
    return any(marker in lowered_output for marker in SKILL_INSTALL_ERROR_MARKERS)


def _normalize_skill_rel_path(value: str | Path) -> str:
    text = str(value or "").strip().replace("\\", "/").strip("/")
    return text


def _skill_source_metadata_path(skill_dir: Path) -> Path:
    return skill_dir / SKILL_SOURCE_METADATA_FILENAME


def _parse_skill_source_reference(identifier: str) -> dict:
    text = str(identifier or "").strip()
    if not text:
        return {
            "identifier": "",
            "source_repo": "",
            "source_path": "",
        }

    url_match = re.match(
        r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>[^?#]+))?)?/?(?:[?#].*)?$",
        text,
        re.IGNORECASE,
    )
    if url_match:
        owner = str(url_match.group("owner") or "").strip()
        repo = str(url_match.group("repo") or "").strip()
        source_path = _normalize_skill_rel_path(url_match.group("path") or "")
        return {
            "identifier": text,
            "source_repo": f"{owner}/{repo}" if owner and repo else "",
            "source_path": source_path,
        }

    parts = [part for part in text.replace("\\", "/").split("/") if part]
    if len(parts) >= 2:
        return {
            "identifier": text,
            "source_repo": "/".join(parts[:2]),
            "source_path": "/".join(parts[2:]),
        }
    return {
        "identifier": text,
        "source_repo": "",
        "source_path": "",
    }


def _build_skill_source_record(
    identifier: str,
    *,
    install_mode: str,
    display: str = "",
    catalog_source: str = "",
) -> dict:
    parsed = _parse_skill_source_reference(identifier)
    source_repo = parsed.get("source_repo") or ""
    source_path = parsed.get("source_path") or ""
    identifier_text = parsed.get("identifier") or str(identifier or "").strip()
    if display:
        display_text = str(display).strip()
    elif install_mode == "github_repo" and source_repo:
        display_text = source_repo
    else:
        display_text = identifier_text or source_repo or "Local / Unknown"
    return {
        "display": display_text,
        "identifier": identifier_text,
        "source_repo": source_repo,
        "source_path": source_path,
        "catalog_source": str(catalog_source or "").strip(),
        "install_mode": str(install_mode or "").strip(),
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _read_skill_source_metadata(skill_dir: Path) -> dict:
    meta_path = _skill_source_metadata_path(skill_dir)
    if not meta_path.exists():
        return {}
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    source_repo = _normalize_skill_rel_path(payload.get("source_repo") or "")
    source_path = _normalize_skill_rel_path(payload.get("source_path") or "")
    identifier = str(payload.get("identifier") or "").strip()
    display = str(payload.get("display") or "").strip() or identifier or source_repo or "Local / Unknown"
    return {
        "display": display,
        "identifier": identifier,
        "source_repo": source_repo,
        "source_path": source_path,
        "catalog_source": str(payload.get("catalog_source") or "").strip(),
        "install_mode": str(payload.get("install_mode") or "").strip(),
        "recorded_at": str(payload.get("recorded_at") or "").strip(),
        "tracked": True,
    }


def _write_skill_source_metadata(skill_dir: Path, metadata: dict) -> dict:
    skill_dir.mkdir(parents=True, exist_ok=True)
    normalized = _read_skill_source_metadata(skill_dir)
    normalized.update({
        "display": str(metadata.get("display") or normalized.get("display") or "").strip(),
        "identifier": str(metadata.get("identifier") or normalized.get("identifier") or "").strip(),
        "source_repo": _normalize_skill_rel_path(metadata.get("source_repo") or normalized.get("source_repo") or ""),
        "source_path": _normalize_skill_rel_path(metadata.get("source_path") or normalized.get("source_path") or ""),
        "catalog_source": str(metadata.get("catalog_source") or normalized.get("catalog_source") or "").strip(),
        "install_mode": str(metadata.get("install_mode") or normalized.get("install_mode") or "").strip(),
        "recorded_at": str(metadata.get("recorded_at") or normalized.get("recorded_at") or "").strip()
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    })
    if not normalized.get("display"):
        normalized["display"] = normalized.get("identifier") or normalized.get("source_repo") or "Local / Unknown"
    _skill_source_metadata_path(skill_dir).write_text(
        json.dumps(normalized, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    normalized["tracked"] = True
    return normalized


def _record_skill_install_source(
    rel_paths: list[str],
    *,
    identifier: str,
    install_mode: str,
    display: str = "",
    catalog_source: str = "",
) -> list[str]:
    record = _build_skill_source_record(
        identifier,
        install_mode=install_mode,
        display=display,
        catalog_source=catalog_source,
    )
    updated = []
    seen = set()
    for rel_path in rel_paths or []:
        normalized_rel = _normalize_skill_rel_path(rel_path)
        if not normalized_rel or normalized_rel in seen:
            continue
        seen.add(normalized_rel)
        skill_dir = SKILLS_DIR / normalized_rel
        if not (skill_dir / "SKILL.md").exists():
            continue
        _write_skill_source_metadata(skill_dir, record)
        updated.append(normalized_rel)
    return updated


def _match_skill_paths_for_identifier(identifier: str, skills: list[dict]) -> list[str]:
    parsed = _parse_skill_source_reference(identifier)
    terms = []
    identifier_text = str(parsed.get("identifier") or "").strip()
    source_path = str(parsed.get("source_path") or "").strip()
    if identifier_text:
        terms.append(identifier_text.lower())
    if source_path:
        terms.append(source_path.lower())
        terms.append(Path(source_path).name.lower())
    terms = [term for term in dict.fromkeys(terms) if term]
    if not terms:
        return []
    matches = []
    for skill in skills or []:
        if _skill_matches_terms(skill, tuple(terms)):
            rel_path = _normalize_skill_rel_path(skill.get("path") or "")
            if rel_path:
                matches.append(rel_path)
    return list(dict.fromkeys(matches))


def _parse_github_skill_install_identifier(identifier: str) -> dict | None:
    text = str(identifier or "").strip()
    if not text:
        return None

    url_match = re.match(
        r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?(?:/tree/(?P<ref>[^/]+)(?:/(?P<path>[^?#]+))?)?/?(?:[?#].*)?$",
        text,
        re.IGNORECASE,
    )
    if url_match:
        owner = str(url_match.group("owner") or "").strip()
        repo = str(url_match.group("repo") or "").strip()
        if owner and repo:
            return {
                "owner": owner,
                "repo": repo,
                "ref": str(url_match.group("ref") or "").strip(),
                "path": str(url_match.group("path") or "").strip("/"),
                "clone_url": f"https://github.com/{owner}/{repo}.git",
            }
        return None

    short_match = re.match(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$", text)
    if not short_match:
        return None
    owner = str(short_match.group("owner") or "").strip()
    repo = str(short_match.group("repo") or "").strip()
    if not owner or not repo:
        return None
    return {
        "owner": owner,
        "repo": repo,
        "ref": "",
        "path": "",
        "clone_url": f"https://github.com/{owner}/{repo}.git",
    }


def _discover_skill_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []

    discovered = []
    for current_root, dirs, files in os.walk(str(root)):
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        if "SKILL.md" not in files:
            continue
        discovered.append(Path(current_root))
    return sorted(discovered)


def _install_skills_from_github_repo(identifier: str) -> dict | None:
    spec = _parse_github_skill_install_identifier(identifier)
    if not spec:
        return None
    if not shutil.which("git"):
        raise RuntimeError("git is required to install skills from GitHub repos")

    clone_cmd = ["git", "clone", "--depth", "1", "--quiet"]
    if spec.get("ref"):
        clone_cmd.extend(["--branch", str(spec["ref"]), "--single-branch"])
    clone_cmd.append(str(spec["clone_url"]))

    with tempfile.TemporaryDirectory(prefix="hermes-skill-import-") as tmpdir:
        clone_cmd.append(tmpdir)
        clone_result = subprocess.run(
            clone_cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if clone_result.returncode != 0:
            message = _combined_process_output(clone_result) or f"git clone exited with status {clone_result.returncode}"
            raise RuntimeError(message)

        repo_root = Path(tmpdir)
        search_root = repo_root / str(spec.get("path") or "") if spec.get("path") else repo_root
        if not search_root.exists():
            raise RuntimeError(f"GitHub path not found inside repo: {spec.get('path')}")

        skill_dirs = _discover_skill_dirs(search_root)
        if not skill_dirs:
            label = str(spec.get("path") or "").strip() or f"{spec['owner']}/{spec['repo']}"
            raise RuntimeError(f"No SKILL.md directories found in {label}")

        installed_paths = []
        skipped_paths = []
        source_display = f"{spec['owner']}/{spec['repo']}"
        for skill_dir in skill_dirs:
            rel_repo_path = skill_dir.relative_to(repo_root)
            dest_rel_path = rel_repo_path if rel_repo_path != Path(".") else Path(str(spec["repo"]))
            normalized_rel_path = _normalize_skill_rel_path(dest_rel_path)
            destination = SKILLS_DIR / normalized_rel_path
            if destination.exists():
                _record_skill_install_source(
                    [normalized_rel_path],
                    identifier=identifier,
                    install_mode="github_repo",
                    display=source_display,
                )
                skipped_paths.append(normalized_rel_path)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(skill_dir, destination)
            _record_skill_install_source(
                [normalized_rel_path],
                identifier=identifier,
                install_mode="github_repo",
                display=source_display,
            )
            installed_paths.append(normalized_rel_path)

    return {
        "mode": "github_repo",
        "source": f"{spec['owner']}/{spec['repo']}",
        "requested_identifier": identifier,
        "installed_paths": installed_paths,
        "skipped_paths": skipped_paths,
    }


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

        normalized_status = raw.casefold()
        running = (
            "gateway is running" in normalized_status
            or "gateway service is running" in normalized_status
            or "user gateway service is running" in normalized_status
        ) and "not running" not in normalized_status
        pid: int | None = None
        if running:
            # Extract PID from either Hermes CLI or systemd output.
            import re
            m = re.search(r"PID:\s*(\d+)", raw)
            if not m:
                m = re.search(r"Main PID:\s*(\d+)", raw)
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
    request_id = _request_id_or_dash()
    logger.error("HTTP error %d request_id=%s: %s", status, request_id, msg)
    payload = {"ok": False, "error": msg}
    if request_id != "-":
        payload["request_id"] = request_id
    return jsonify(payload), status


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
            "hermes_home": str(_selected_hermes_home()),
            "hermes_bin": str(_selected_hermes_bin()),
            "profile": _selected_hermes_profile_name(),
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


@app.route("/api/hermes/update-status")
@require_token
def api_hermes_update_status():
    try:
        refresh = str(request.args.get("refresh") or "").strip().lower() in {"1", "true", "yes", "on"}
        return jsonify(_build_hermes_update_payload(force_refresh=refresh))
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/hermes/update-check", methods=["POST"])
@require_token
def api_hermes_update_check():
    try:
        return jsonify(_build_hermes_update_payload(force_refresh=True))
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/hermes/update", methods=["POST"])
@require_token
def api_hermes_update():
    try:
        data = request.get_json(silent=True) or {}
        if not data.get("confirm"):
            return jsonify({"ok": False, "error": "Update confirmation required."}), 400

        current = _build_hermes_update_payload(force_refresh=True)
        if not current.get("can_update"):
            return jsonify({
                "ok": False,
                "error": current.get("manual_reason") or "In-app Hermes updating is not supported for this install.",
                "manual_command": current.get("manual_command") or "",
                "status": current,
            }), 409

        runtime = _runtime_snapshot()
        if runtime.get("status") == "update_in_progress":
            return jsonify({
                "ok": True,
                "message": runtime.get("summary") or "Hermes update already in progress.",
                "status": _build_hermes_update_payload(force_refresh=False),
            }), 202

        _invalidate_hermes_update_cache(Path(current["project_root"]) if current.get("project_root") else None)
        _set_update_runtime(
            status="update_in_progress",
            started_at=_utc_now_z(),
            finished_at="",
            returncode=None,
            error="",
            summary=f"Starting Hermes update for {current.get('installed_version', {}).get('display') or 'the installed version'}...",
            logs=[],
            install_key=current.get("install_key") or "",
            installed_version_before=(current.get("installed_version") or {}).get("display") or "",
            installed_version_after="",
        )
        worker = threading.Thread(
            target=_run_hermes_update_worker,
            args=(current,),
            daemon=True,
            name="hermes-update-worker",
        )
        worker.start()
        return jsonify({
            "ok": True,
            "message": "Hermes update started.",
            "status": _build_hermes_update_payload(force_refresh=False),
        }), 202
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
        current = cfg.get_raw(section)
        data = _preserve_masked_secret_updates(current, data)
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


@app.route("/api/runtime/profiles", methods=["GET"])
@require_token
def api_runtime_profiles_get():
    try:
        cfg.load()
        return jsonify(_selected_hermes_profile_payload())
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/runtime/profiles", methods=["PUT"])
@require_token
def api_runtime_profiles_put():
    try:
        data = request.get_json(force=True) or {}
        selected = _set_selected_hermes_profile_name(data.get("profile") or "default")
        cfg.load()
        return jsonify({"ok": True, **_selected_hermes_profile_payload(), "selected": selected})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/runtime/profiles/<profile_name>/api-token", methods=["GET"])
@require_token
def api_runtime_profile_api_token_get(profile_name):
    try:
        return jsonify({"ok": True, **_profile_api_token_metadata(profile_name)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/runtime/profiles/<profile_name>/api-token", methods=["PUT"])
@require_token
def api_runtime_profile_api_token_put(profile_name):
    try:
        normalized = _normalize_hermes_profile_name(profile_name)
        if normalized not in _available_hermes_profile_names():
            raise ValueError(f"Unknown Hermes profile: {normalized}")
        data = request.get_json(force=True) or {}
        token = str(data.get("token") or "").strip()
        api_url = _profile_api_gateway_url(normalized)
        port = _api_url_port(api_url)
        env_path = REPO_ENV_PATH
        env_path.parent.mkdir(parents=True, exist_ok=True)
        raw = dotenv_values(str(env_path)) if env_path.exists() else {}
        for key in _api_token_repo_keys_for_port(port):
            if raw.get(key) is not None:
                unset_key(str(env_path), key)
        if token:
            key_name = f"HERMES_API_TOKEN_PORT_{port}" if port else "HERMES_API_TOKEN"
            _set_env_value(env_path, key_name, token)
        return jsonify({"ok": True, **_profile_api_token_metadata(normalized)})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 7–9. Environment variables
# ===================================================================

@app.route("/api/env", methods=["GET"])
@require_token
def api_env_get():
    try:
        env_path = _selected_env_path()
        raw = dotenv_values(str(env_path)) if env_path.exists() else {}
        masked = {k: _mask_value(k, v) for k, v in raw.items() if v is not None}
        skills = _discover_skill_entries()
        dynamic_presets = _skill_env_var_presets(skills)

        groups: dict[str, list[str]] = {}
        for k in masked:
            g = _classify_env_key(k)
            groups.setdefault(g, []).append(k)

        metadata = {
            k: dynamic_presets.get(k) or _env_var_metadata(k)
            for k in masked
        }
        presets = _env_presets_by_group()
        for key, meta in dynamic_presets.items():
            group = meta.get("group") or _classify_env_key(key)
            bucket = presets.setdefault(group, [])
            if any(str(item.get("key") or "").strip() == key for item in bucket if isinstance(item, dict)):
                continue
            bucket.append(meta)
        for values in presets.values():
            values.sort(key=lambda item: str(item.get("label") or item.get("key") or "").casefold())
        return jsonify({
            "vars": masked,
            "groups": groups,
            "metadata": metadata,
            "group_help": ENV_GROUP_HELP,
            "presets": presets,
        })
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
        env_path = _selected_env_path()
        env_path.parent.mkdir(parents=True, exist_ok=True)
        _set_env_value(env_path, key, value or "")
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/env/<key>", methods=["PUT"])
@require_token
def api_env_update(key):
    try:
        data = request.get_json(force=True)
        value = data.get("value", "")
        env_path = _selected_env_path()
        current = dotenv_values(str(env_path)).get(key) if env_path.exists() else None
        if (
            isinstance(value, str)
            and isinstance(current, str)
            and current
            and value == _mask_value(key, current)
        ):
            return jsonify({"ok": True})
        _set_env_value(env_path, key, value)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/env/<key>", methods=["DELETE"])
@require_token
def api_env_delete(key):
    try:
        unset_key(str(_selected_env_path()), key)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 10–14. Providers
# ===================================================================

def _get_providers_info():
    """Build a structured view of providers from config."""
    raw = cfg.get_raw()
    model_cfg = _normalized_model_config()
    custom = _custom_provider_profiles(raw)

    default = {
        "profile": _role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw),
        "provider": model_cfg.get("default_provider", ""),
        "model": model_cfg.get("default_model", ""),
        "base_url": model_cfg.get("base_url", ""),
        "routing_provider": model_cfg.get("routing_provider", ""),
    }

    auxiliary = {}
    for aux_key in AUXILIARY_MODEL_KEYS:
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
        usage_map = _provider_usage_map()
        safe_custom = []
        for profile in custom:
            safe = cfg.mask_secrets(profile)
            safe["used_by"] = usage_map.get(profile.get("name", ""), [])
            safe["has_api_key"] = bool(profile.get("api_key"))
            safe["provider_label"] = _provider_display_name(profile.get("provider", ""))
            safe_custom.append(safe)
        safe_aux = cfg.mask_secrets(auxiliary)
        for cfg_value in safe_aux.values():
            if isinstance(cfg_value, dict):
                cfg_value["provider_label"] = _provider_display_name(cfg_value.get("provider", ""))
        return jsonify({
            "default": {
                **default,
                "provider_label": _provider_display_name(default.get("provider", "")),
            },
            "custom": safe_custom,
            "auxiliary": safe_aux,
            "presets": PROVIDER_PRESETS,
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers", methods=["POST"])
@require_token
def api_providers_add():
    try:
        data = _normalize_provider_profile(request.get_json(force=True))
        name = data.get("name")
        if not name:
            return jsonify({"ok": False, "error": "name is required"}), 400

        raw = cfg.get_raw()
        custom = _custom_provider_profiles(raw)
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
        custom = _custom_provider_profiles(raw)
        found = False
        for i, p in enumerate(custom):
            if p.get("name") == name:
                sanitized = _preserve_masked_secret_updates(p, data)
                merged = ConfigManager.deep_merge(p, sanitized)
                merged["name"] = name
                custom[i] = _normalize_provider_profile(merged)
                found = True
                break
        if not found:
            return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
        cfg.set("custom_providers", custom)
        _sync_linked_provider_roles(name, custom[i])
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>", methods=["DELETE"])
@require_token
def api_providers_delete(name):
    try:
        raw = cfg.get_raw()
        usage_map = _provider_usage_map(raw=raw)
        if usage_map.get(name):
            used_by = ", ".join(usage_map.get(name, []))
            return jsonify({"ok": False, "error": f"Provider '{name}' is still used by {used_by}"}), 409
        custom = _custom_provider_profiles(raw)
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
        model_cfg = _normalized_model_config()
        provider_cfg = _get_provider_profile(name, raw)

        if not provider_cfg:
            # Maybe it's the default provider
            if _role_linked_profile_name("primary", model_cfg=model_cfg, raw=raw) == name:
                provider_cfg = _resolve_role_target("primary")
            else:
                return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404

        # Try a simple chat completion request
        import urllib.request
        import urllib.error

        base_url = (provider_cfg.get("base_url") or "").rstrip("/")
        model = provider_cfg.get("model", "gpt-3.5-turbo")
        provider_type = provider_cfg.get("provider", "")
        if not base_url:
            return jsonify({"ok": False, "error": "Base URL is required to test this provider"}), 200
        if not model:
            return jsonify({"ok": False, "error": "Suggested model is required to test this provider"}), 200

        url = _build_openai_api_url(base_url, "chat/completions")

        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5,
        }).encode("utf-8")

        headers = _api_server_headers(provider_cfg.get("api_key"), provider_type)
        headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                latency = int((time.time() - start) * 1000)
                return jsonify({"ok": True, "latency_ms": latency, "response": body[:200]})
        except urllib.error.HTTPError as e:
            latency = int((time.time() - start) * 1000)
            body = e.read().decode("utf-8", errors="replace")
            detail = _summarize_upstream_error_detail(body, str(e.reason))[:300]
            return jsonify({"ok": False, "error": f"HTTP {e.code}: {detail}", "latency_ms": latency}), 200
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
        model_cfg = _normalized_model_config()
        custom = _custom_provider_profiles(raw)

        all_models = []
        seen = set()

        def _add(provider_name, model_name):
            key = (provider_name, model_name)
            if model_name and key not in seen:
                seen.add(key)
                all_models.append({"provider": provider_name, "model": model_name})

        _add(model_cfg.get("default_provider", "default"), model_cfg.get("default_model", ""))
        _add(model_cfg.get("fallback_provider") or model_cfg.get("default_provider", "default"), model_cfg.get("fallback_model", ""))

        for cp in custom:
            _add(cp.get("name", ""), cp.get("model", ""))

        for aux_key in AUXILIARY_MODEL_KEYS:
            val = model_cfg.get(aux_key)
            if isinstance(val, str):
                _add(aux_key, val)
            elif isinstance(val, dict):
                _add(aux_key, val.get("model", ""))

        return jsonify({
            "default_model": model_cfg.get("default_model", ""),
            "default_provider": model_cfg.get("default_provider", ""),
            "default_profile": model_cfg.get("default_profile", ""),
            "fallback_model": model_cfg.get("fallback_model", ""),
            "fallback_provider": model_cfg.get("fallback_provider", ""),
            "fallback_profile": model_cfg.get("fallback_profile", ""),
            "all_models": all_models,
            "roles": {
                "primary": _model_role_info("primary"),
                "fallback": _model_role_info("fallback"),
                "vision": _model_role_info("vision"),
            },
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/model-roles", methods=["GET"])
@require_token
def api_model_roles_get():
    try:
        profiles = []
        usage_map = _provider_usage_map()
        for profile in _available_provider_profiles():
            safe = cfg.mask_secrets(profile)
            safe["used_by"] = usage_map.get(profile.get("name", ""), [])
            safe["has_api_key"] = bool(profile.get("api_key") or _provider_env_api_key(profile.get("provider")))
            safe["provider_label"] = _provider_display_name(profile.get("provider", ""))
            profiles.append(safe)
        return jsonify({
            "profiles": profiles,
            "roles": {
                "primary": _model_role_info("primary"),
                "fallback": _model_role_info("fallback"),
                "vision": _model_role_info("vision"),
            },
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/model-roles/<role>", methods=["PUT"])
@require_token
def api_model_roles_update(role):
    try:
        role = str(role or "").strip().lower()
        if role not in MODEL_ROLE_LABELS:
            return jsonify({"ok": False, "error": f"Unknown role '{role}'"}), 404

        data = request.get_json(force=True) or {}
        profile_name = str(data.get("profile") or "").strip()
        model_name = str(data.get("model") or "").strip()
        routing_provider = str(data.get("routing_provider") or "").strip()

        if role == "primary":
            if not profile_name or not model_name:
                return jsonify({"ok": False, "error": "Primary Chat requires both a provider profile and a model"}), 400
            profile_payload = _profile_payload_for_role(profile_name, model_name, routing_provider)
            cfg.update("model", {
                "default_profile": profile_payload["profile"],
                "default_provider": profile_payload["provider"],
                "default_model": profile_payload["model"],
                "base_url": profile_payload["base_url"],
                "api_key": profile_payload["api_key"],
                "routing_provider": profile_payload["routing_provider"],
            })
            return jsonify({"ok": True})

        if not profile_name or not model_name:
            if role == "fallback":
                cfg.update("model", {
                    "fallback_profile": "",
                    "fallback_provider": "",
                    "fallback_model": "",
                    "fallback_base_url": "",
                    "fallback_api_key": "",
                    "fallback_routing_provider": "",
                })
                return jsonify({"ok": True})
            if role == "vision":
                cfg.update("auxiliary", {
                    "vision": {
                        "profile": "",
                        "provider": "auto",
                        "model": "",
                        "base_url": "",
                        "api_key": "",
                        "routing_provider": "",
                    }
                })
                return jsonify({"ok": True})

        profile_payload = _profile_payload_for_role(profile_name, model_name, routing_provider)
        if role == "fallback":
            cfg.update("model", {
                "fallback_profile": profile_payload["profile"],
                "fallback_provider": profile_payload["provider"],
                "fallback_model": profile_payload["model"],
                "fallback_base_url": profile_payload["base_url"],
                "fallback_api_key": profile_payload["api_key"],
                "fallback_routing_provider": profile_payload["routing_provider"],
            })
            return jsonify({"ok": True})

        cfg.update("auxiliary", {
            "vision": {
                "profile": profile_payload["profile"],
                "provider": profile_payload["provider"],
                "model": profile_payload["model"],
                "base_url": profile_payload["base_url"],
                "api_key": profile_payload["api_key"],
                "routing_provider": profile_payload["routing_provider"],
            }
        })
        return jsonify({"ok": True})
    except ChatBackendError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status_code
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>/discovery/models", methods=["GET"])
@require_token
def api_provider_discovery_models(name):
    try:
        profile = _get_provider_profile(name)
        if not profile:
            return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
        vision_only = request.args.get("vision_only", "").lower() in ("1", "true", "yes")
        if str(profile.get("provider") or "").strip().lower() != "openrouter":
            return jsonify({
                "supported": False,
                "provider": profile.get("provider", ""),
                "models": [],
                "reason": "Live model discovery is only available for OpenRouter profiles right now",
            })
        return jsonify({
            "supported": True,
            "provider": profile.get("provider", ""),
            "models": _openrouter_discovery_models(vision_only=vision_only),
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/provider-types/<provider>/discovery/models", methods=["GET"])
@require_token
def api_provider_type_discovery_models(provider):
    try:
        provider = _normalize_provider_type(provider or "")
        vision_only = request.args.get("vision_only", "").lower() in ("1", "true", "yes")
        if provider != "openrouter":
            return jsonify({
                "supported": False,
                "provider": provider,
                "models": [],
                "reason": "Live model discovery is only available for OpenRouter right now",
            })
        return jsonify({
            "supported": True,
            "provider": provider,
            "models": _openrouter_discovery_models(vision_only=vision_only),
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/providers/<name>/discovery/endpoints", methods=["GET"])
@require_token
def api_provider_discovery_endpoints(name):
    try:
        profile = _get_provider_profile(name)
        if not profile:
            return jsonify({"ok": False, "error": f"Provider '{name}' not found"}), 404
        model_id = str(request.args.get("model") or "").strip()
        if not model_id:
            return jsonify({"supported": False, "endpoints": [], "reason": "model is required"}), 400
        if str(profile.get("provider") or "").strip().lower() != "openrouter":
            return jsonify({
                "supported": False,
                "provider": profile.get("provider", ""),
                "endpoints": [],
                "reason": "Live endpoint discovery is only available for OpenRouter profiles right now",
            })
        return jsonify({
            "supported": True,
            "provider": profile.get("provider", ""),
            "endpoints": _openrouter_discovery_endpoints(model_id),
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
        agent_cfg = _agent_defaults(raw)
        personalities, _ = _agent_personality_entries(raw)
        entries = [
            _personality_entry_for_api(name, value)
            for name, value in sorted(personalities.items(), key=lambda item: str(item[0]).casefold())
        ]
        # Return agent defaults plus personalities (masked)
        result = {
            "defaults": cfg.mask_secrets({k: v for k, v in agent_cfg.items() if k != "personalities"}),
            "personalities": cfg.mask_secrets(personalities),
            "entries": entries,
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
        personalities, _ = _agent_personality_entries(raw)
        if name in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' already exists"}), 409

        agent_cfg = raw.get("agent", {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
        nested = agent_cfg.get("personalities", {})
        if not isinstance(nested, dict):
            nested = {}
        nested[name] = _normalize_personality_value({k: v for k, v in data.items() if k != "name"})
        agent_cfg["personalities"] = nested
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
        personalities, storage = _agent_personality_entries(raw)
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

        if storage.get(name) == "legacy":
            legacy = raw.get("personalities", {})
            if not isinstance(legacy, dict):
                legacy = {}
            current = legacy.get(name, "")
            merged = ConfigManager.deep_merge(current, data) if isinstance(current, dict) else {**data, "system_prompt": str(data.get("system_prompt") or data.get("prompt") or current or "")}
            legacy[name] = _normalize_personality_value(merged)
            cfg.set("personalities", legacy)
        else:
            agent_cfg = raw.get("agent", {})
            if not isinstance(agent_cfg, dict):
                agent_cfg = {}
            nested = agent_cfg.get("personalities", {})
            if not isinstance(nested, dict):
                nested = {}
            current = nested.get(name, "")
            merged = ConfigManager.deep_merge(current, data) if isinstance(current, dict) else {**data, "system_prompt": str(data.get("system_prompt") or data.get("prompt") or current or "")}
            nested[name] = _normalize_personality_value(merged)
            agent_cfg["personalities"] = nested
            cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/agents/<name>", methods=["DELETE"])
@require_token
def api_agents_delete(name):
    try:
        raw = cfg.get_raw()
        personalities, storage = _agent_personality_entries(raw)
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404

        if storage.get(name) == "legacy":
            legacy = raw.get("personalities", {})
            if not isinstance(legacy, dict):
                legacy = {}
            legacy.pop(name, None)
            cfg.set("personalities", legacy)
        else:
            agent_cfg = raw.get("agent", {})
            if not isinstance(agent_cfg, dict):
                agent_cfg = {}
            nested = agent_cfg.get("personalities", {})
            if not isinstance(nested, dict):
                nested = {}
            nested.pop(name, None)
            agent_cfg["personalities"] = nested
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
        personalities, storage = _agent_personality_entries(raw)
        if name not in personalities:
            return jsonify({"ok": False, "error": f"Agent '{name}' not found"}), 404
        if new_name in personalities:
            return jsonify({"ok": False, "error": f"Agent '{new_name}' already exists"}), 409

        if storage.get(name) == "legacy":
            legacy = raw.get("personalities", {})
            if not isinstance(legacy, dict):
                legacy = {}
            legacy[new_name] = copy.deepcopy(legacy.get(name))
            cfg.set("personalities", legacy)
        else:
            agent_cfg = raw.get("agent", {})
            if not isinstance(agent_cfg, dict):
                agent_cfg = {}
            nested = agent_cfg.get("personalities", {})
            if not isinstance(nested, dict):
                nested = {}
            nested[new_name] = copy.deepcopy(nested.get(name))
            agent_cfg["personalities"] = nested
            cfg.set("agent", agent_cfg)
        return jsonify({"ok": True})
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 21–23. Capability Builder
# ===================================================================

@app.route("/api/capabilities", methods=["GET"])
@require_token
def api_capabilities_get():
    try:
        return jsonify(_capability_catalog())
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/capabilities/preview", methods=["POST"])
@require_token
def api_capabilities_preview():
    try:
        data = request.get_json(force=True) or {}
        capability_type = str(data.get("type") or "").strip().lower()
        draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
        if capability_type == "skill":
            payload, status = _preview_skill_capability(draft)
            return jsonify(payload), status
        if capability_type == "integration":
            payload, status = _preview_integration_capability(draft)
            return jsonify(payload), status
        if capability_type == "agent_preset":
            payload, status = _preview_agent_preset_capability(draft)
            return jsonify(payload), status
        return jsonify({"ok": False, "error": "Capability type is required"}), 400
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/capabilities/apply", methods=["POST"])
@require_token
def api_capabilities_apply():
    try:
        data = request.get_json(force=True) or {}
        capability_type = str(data.get("type") or "").strip().lower()
        draft = data.get("draft") if isinstance(data.get("draft"), dict) else {}
        preview_token = str(data.get("preview_token") or "").strip()
        if capability_type == "skill":
            payload, status = _apply_skill_capability(draft, preview_token)
            return jsonify(payload), status
        if capability_type == "integration":
            payload, status = _apply_integration_capability(draft, preview_token)
            return jsonify(payload), status
        if capability_type == "agent_preset":
            payload, status = _apply_agent_preset_capability(draft, preview_token)
            return jsonify(payload), status
        return jsonify({"ok": False, "error": "Capability type is required"}), 400
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 24–25. Skills
# ===================================================================

@app.route("/api/skills", methods=["GET"])
@require_token
def api_skills_get():
    try:
        return jsonify({"skills": _discover_skill_entries()})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/skills/<path:name>/toggle", methods=["POST"])
@require_token
def api_skill_toggle(name):
    try:
        info = _skill_request_paths(name)
        if not info:
            return jsonify({"ok": False, "error": "Skill path is required"}), 400
        action = "disable" if info["base_path"].exists() else "enable"
        result = _skill_apply_action(name, action)
        if not result.get("found"):
            return jsonify({"ok": False, "error": result.get("error") or f"Skill '{name}' not found"}), 404
        return jsonify({
            "ok": True,
            "enabled": bool(result.get("enabled")),
            "changed": bool(result.get("changed")),
            "path": result.get("path") or info.get("requested_rel"),
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/skills/bulk", methods=["POST"])
@require_token
def api_skill_bulk():
    try:
        data = request.get_json(force=True) or {}
        action = str(data.get("action") or "").strip().lower()
        if action not in {"enable", "disable", "remove"}:
            return jsonify({"ok": False, "error": "Unsupported bulk action"}), 400

        raw_paths = data.get("paths") if isinstance(data.get("paths"), list) else []
        paths = []
        seen = set()
        for entry in raw_paths:
            rel_path = _safe_skill_rel_path(entry)
            if rel_path and rel_path not in seen:
                paths.append(rel_path)
                seen.add(rel_path)
        if not paths:
            return jsonify({"ok": False, "error": "At least one skill path is required"}), 400

        results = []
        changed_paths = []
        missing_paths = []
        removed_paths = []
        for rel_path in paths:
            result = _skill_apply_action(rel_path, action)
            result["requested"] = rel_path
            results.append(result)
            if not result.get("found"):
                missing_paths.append(rel_path)
                continue
            if result.get("changed"):
                changed_paths.append(result.get("path") or rel_path)
            if result.get("removed"):
                removed_paths.append(result.get("path") or rel_path)

        return jsonify({
            "ok": True,
            "action": action,
            "results": results,
            "changed_count": len(changed_paths),
            "changed_paths": changed_paths,
            "removed_paths": removed_paths,
            "missing_paths": missing_paths,
            "skills": _discover_skill_entries(),
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/skills/install", methods=["POST"])
@require_token
def api_skill_install():
    try:
        data = request.get_json(force=True) or {}
        identifier = str(data.get("identifier") or "").strip()
        if not identifier:
            return jsonify({"ok": False, "error": "identifier is required"}), 400

        before_paths = {
            _normalize_skill_rel_path(skill.get("path") or "")
            for skill in _discover_skill_entries()
            if _normalize_skill_rel_path(skill.get("path") or "")
        }
        result = _run_hermes("skills", "install", identifier, "--yes", timeout=300)
        combined_output = _combined_process_output(result)
        fallback = None
        if _hermes_skill_install_failed(result, combined_output):
            fallback = _install_skills_from_github_repo(identifier)
            if not fallback:
                message = combined_output or f"Hermes skills install exited with status {result.returncode}"
                return jsonify({"ok": False, "error": message}), 502

        skills = _discover_skill_entries()
        after_paths = {
            _normalize_skill_rel_path(skill.get("path") or "")
            for skill in skills
            if _normalize_skill_rel_path(skill.get("path") or "")
        }
        installed_skill_paths = sorted(after_paths - before_paths)
        already_present_paths = []
        annotated_skill_paths = []

        if fallback:
            installed_skill_paths = list(fallback.get("installed_paths") or [])
            already_present_paths = list(fallback.get("skipped_paths") or [])
            annotated_skill_paths = list(dict.fromkeys(installed_skill_paths + already_present_paths))
        else:
            if installed_skill_paths:
                annotated_skill_paths = _record_skill_install_source(
                    installed_skill_paths,
                    identifier=identifier,
                    install_mode="hermes",
                )
            else:
                already_present_paths = _match_skill_paths_for_identifier(identifier, skills)
                if already_present_paths:
                    annotated_skill_paths = _record_skill_install_source(
                        already_present_paths,
                        identifier=identifier,
                        install_mode="hermes",
                    )

        return jsonify({
            "ok": True,
            "identifier": identifier,
            "output": combined_output,
            "install_mode": (fallback or {}).get("mode", "hermes"),
            "fallback": fallback,
            "installed_skill_paths": installed_skill_paths,
            "already_present_paths": already_present_paths,
            "annotated_skill_paths": annotated_skill_paths,
            "skills": skills,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Hermes skills install timed out"}), 504
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/starter-pack/<item_id>/install", methods=["POST"])
@require_token
def api_starter_pack_install(item_id):
    try:
        group = _starter_pack_skill_group(item_id)
        if not group:
            return jsonify({"ok": False, "error": "Starter-pack item not found"}), 404

        candidates = _starter_pack_install_candidates(group)
        if not candidates:
            return jsonify({"ok": False, "error": "This starter-pack item does not support installation"}), 400

        requested_identifier = str((request.get_json(silent=True) or {}).get("identifier") or "").strip()
        candidate_map = {candidate["identifier"]: candidate for candidate in candidates}
        if requested_identifier:
            if requested_identifier not in candidate_map:
                return jsonify({"ok": False, "error": "Unsupported starter-pack install target"}), 400
            chosen = candidate_map[requested_identifier]
        else:
            chosen = next((candidate for candidate in candidates if candidate.get("recommended")), candidates[0])

        before_paths = {
            _normalize_skill_rel_path(skill.get("path") or "")
            for skill in _discover_skill_entries()
            if _normalize_skill_rel_path(skill.get("path") or "")
        }
        result = _run_hermes("skills", "install", chosen["identifier"], "--yes", timeout=300)
        combined_output = _combined_process_output(result)
        if _hermes_skill_install_failed(result, combined_output):
            message = combined_output or f"Hermes skills install exited with status {result.returncode}"
            return jsonify({"ok": False, "error": message}), 502

        after_skills = _discover_skill_entries()
        after_paths = {
            _normalize_skill_rel_path(skill.get("path") or "")
            for skill in after_skills
            if _normalize_skill_rel_path(skill.get("path") or "")
        }
        installed_skill_paths = sorted(after_paths - before_paths)
        already_present_paths = []
        annotated_skill_paths = []
        if installed_skill_paths:
            annotated_skill_paths = _record_skill_install_source(
                installed_skill_paths,
                identifier=chosen["identifier"],
                install_mode="hermes",
                catalog_source=chosen.get("source") or "",
            )
        else:
            already_present_paths = _match_skill_paths_for_identifier(chosen["identifier"], after_skills)
            if already_present_paths:
                annotated_skill_paths = _record_skill_install_source(
                    already_present_paths,
                    identifier=chosen["identifier"],
                    install_mode="hermes",
                    catalog_source=chosen.get("source") or "",
                )

        runtime = _chat_runtime_status()
        item = next(
            (entry for entry in (runtime.get("starter_pack", {}).get("items") or []) if entry.get("id") == group.get("id")),
            None,
        )
        return jsonify({
            "ok": True,
            "installed": True,
            "candidate": chosen,
            "item": item,
            "output": combined_output,
            "installed_skill_paths": installed_skill_paths,
            "already_present_paths": already_present_paths,
            "annotated_skill_paths": annotated_skill_paths,
            "setup_notes": [str(note).strip() for note in (group.get("setup_notes") or []) if str(note).strip()],
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Hermes skills install timed out"}), 504
    except Exception as exc:
        return _http_error(str(exc))


# ===================================================================
# 23–24. Channels
# ===================================================================

@app.route("/api/channels", methods=["GET"])
@require_token
def api_channels_get():
    try:
        integrations = _integration_entries()
        return jsonify({"channels": integrations, "integrations": integrations})
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/channels/<name>", methods=["PUT"])
@require_token
def api_channels_update(name):
    try:
        data = request.get_json(force=True)
        raw = cfg.get_raw()
        channels_cfg = raw.get("channels", {})
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Integration config must be a JSON object"}), 400

        if name in channels_cfg and isinstance(channels_cfg.get(name), dict):
            current = channels_cfg.get(name, {})
            channels_cfg[name] = _preserve_masked_secret_updates(current, data)
            cfg.set("channels", channels_cfg)
            return jsonify({"ok": True})

        if name in INTEGRATION_SECTION_LABELS and isinstance(raw.get(name), dict):
            current = raw.get(name, {})
            cfg.set(name, _preserve_masked_secret_updates(current, data))
            return jsonify({"ok": True})

        return jsonify({"ok": False, "error": f"Integration '{name}' not found"}), 404
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
        return jsonify({"config": hooks if isinstance(hooks, dict) else {}})
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
                _selected_hermes_home() / "logs" / "hermes.log",
                _selected_hermes_home() / "logs" / "gateway.log",
                _selected_hermes_home() / "logs" / "errors.log",
                _selected_hermes_home() / "hermes.log",
                _selected_hermes_home() / "gateway.log",
            ]
            for lc in log_candidates:
                content = _read_log_file(lc, lines)
                if content:
                    log_text = content
                    break

        return jsonify({
            "logs": log_text,
            "source": "log_files",
            "source_detail": "Tail of Hermes log files under ~/.hermes/logs when present.",
        })
    except Exception as exc:
        return _http_error(str(exc))


@app.route("/api/cron/jobs", methods=["GET"])
@require_token
def api_cron_jobs():
    try:
        if not _crontab_available():
            return jsonify({"available": False, "jobs": [], "error": "crontab is not installed"}), 501
        jobs = sorted(_load_cron_jobs().values(), key=lambda job: job.get("updated", ""), reverse=True)
        return jsonify({"available": True, "jobs": jobs})
    except ChatBackendError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@app.route("/api/cron/jobs", methods=["POST"])
@require_token
def api_cron_jobs_create():
    try:
        payload, errors = _validate_cron_job_payload(request.get_json() or {})
        if errors:
            return jsonify({"error": "Invalid cron job", "details": errors}), 400
        jobs = _load_cron_jobs()
        now = datetime.now().isoformat()
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {
            "id": job_id,
            "name": payload["name"],
            "schedule": payload["schedule"],
            "command": payload["command"],
            "enabled": payload["enabled"],
            "created": now,
            "updated": now,
        }
        _write_cron_jobs(jobs)
        _sync_cron_jobs_to_system(jobs)
        return jsonify({"ok": True, "job": jobs[job_id]})
    except ChatBackendError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@app.route("/api/cron/jobs/<job_id>", methods=["PUT"])
@require_token
def api_cron_job_update(job_id):
    try:
        jobs = _load_cron_jobs()
        job = jobs.get(job_id)
        if not job:
            return jsonify({"error": "Cron job not found"}), 404
        payload, errors = _validate_cron_job_payload(request.get_json() or {})
        if errors:
            return jsonify({"error": "Invalid cron job", "details": errors}), 400
        job.update(payload)
        job["updated"] = datetime.now().isoformat()
        jobs[job_id] = job
        _write_cron_jobs(jobs)
        _sync_cron_jobs_to_system(jobs)
        return jsonify({"ok": True, "job": job})
    except ChatBackendError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


@app.route("/api/cron/jobs/<job_id>", methods=["DELETE"])
@require_token
def api_cron_job_delete(job_id):
    try:
        jobs = _load_cron_jobs()
        if job_id not in jobs:
            return jsonify({"error": "Cron job not found"}), 404
        jobs.pop(job_id, None)
        _write_cron_jobs(jobs)
        _sync_cron_jobs_to_system(jobs)
        return jsonify({"ok": True})
    except ChatBackendError as exc:
        return jsonify({"error": str(exc)}), exc.status_code


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
            "source": "parsed_cli_output",
            "source_detail": "Parsed from `hermes tools list` text output.",
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

        # start uses `hermes gateway run &` (background) instead of `gateway start`
        # because `gateway start` requires systemd which may not be set up in WSL
        if action == "start":
            import subprocess
            bin_path = _selected_hermes_bin()
            env = {**os.environ, "HERMES_HOME": str(_selected_hermes_home())}
            log_path = _selected_gateway_log_path()
            log_path.parent.mkdir(exist_ok=True)
            # Kill any existing gateway process first
            _run_hermes("gateway", "stop", timeout=10)
            time.sleep(1)
            with open(log_path, "a") as lf:
                proc = subprocess.Popen(
                    [str(bin_path), "gateway", "run"],
                    env=env, stdout=lf, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            time.sleep(3)
            running = _gateway_status()["running"]
            return jsonify({"ok": running, "output": "Gateway started", "gateway_running": running})

        cmd_map = {
            "stop": ["gateway", "stop"],
            "restart": ["gateway", "stop"],
            "doctor": ["doctor"],
        }
        if action not in cmd_map:
            return jsonify({"ok": False, "error": f"Unknown action: {action}"}), 400

        r = _run_hermes(*cmd_map[action], timeout=30)
        output = (r.stdout + "\n" + r.stderr).strip()
        running_after = _gateway_status()["running"]
        ok = r.returncode == 0

        if action == "restart":
            # After stop, start in background
            import subprocess
            bin_path = _selected_hermes_bin()
            env = {**os.environ, "HERMES_HOME": str(_selected_hermes_home())}
            log_path = _selected_gateway_log_path()
            log_path.parent.mkdir(exist_ok=True)
            time.sleep(1)
            with open(log_path, "a") as lf:
                subprocess.Popen(
                    [str(bin_path), "gateway", "run"],
                    env=env, stdout=lf, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            time.sleep(3)
            running_after = _gateway_status()["running"]
            output = "Restarted (running: " + str(running_after) + ")"
            ok = running_after
        elif action == "stop":
            ok = not running_after
        elif action == "doctor":
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
            model_cfg = _normalized_model_config()
            if not model_cfg.get("api_key"):
                missing.append("api_key")

        # Check default provider is set
        model_cfg = _normalized_model_config()
        if not model_cfg.get("default_provider"):
            missing.append("default_provider")

        # Check default model is set
        if not model_cfg.get("default_model"):
            missing.append("default_model")

        # Check at least one messaging integration is configured
        integrations = _integration_entries(raw)
        if not any(item.get("configured") for item in integrations):
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
    """Strip CLI banner, box-drawing, tool list, metadata, and think blocks from hermes -q output."""
    # Strip think blocks: matches <think>...</think> tags and everything between them
    output = re.sub(r'<think>.*?</think>', '', output, flags=re.DOTALL)
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


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".webm", ".m4a", ".aac", ".ogg", ".flac"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".xml", ".html", ".htm", ".css", ".js", ".jsx", ".ts", ".tsx", ".py", ".sh", ".bash",
    ".zsh", ".ini", ".cfg", ".conf", ".toml", ".sql", ".env", ".gitignore", ".dockerfile",
}
TEXT_MIME_TYPES = {
    "application/json", "application/ld+json", "application/xml", "application/javascript",
    "application/x-javascript", "application/x-sh", "application/x-shellscript",
    "application/x-yaml", "application/yaml", "application/toml",
}


def _file_mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return (mime or "").lower()


def _is_text_attachment(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    mime = _file_mime_type(path)
    if mime.startswith("text/") or mime in TEXT_MIME_TYPES:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except Exception:
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _validate_attachment_selection(files: list[Path], image_support: bool) -> list[str]:
    errors = []
    for f in files or []:
        suffix = f.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            if not image_support:
                errors.append(f"{f.name} is an image, but the configured Hermes vision sidecar is not ready")
            continue
        if suffix in AUDIO_EXTENSIONS:
            errors.append(f"{f.name} is audio, and audio uploads are not supported in Hermes chat")
            continue
        if _is_text_attachment(f):
            continue
        errors.append(f"{f.name} is a binary file type Hermes chat cannot read")
    return errors


def _attachment_display_name(path: Path, display_names: dict | None = None) -> str:
    if display_names:
        display_name = display_names.get(path.name)
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
    return path.name


def _summarize_attachments(files: list[Path], image_support: bool, display_names: dict | None = None) -> dict:
    text_blocks = []
    image_files = []
    unsupported = []
    for f in files or []:
        display_name = _attachment_display_name(f, display_names)
        suffix = f.suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            if image_support:
                image_files.append(f)
            else:
                unsupported.append(f"{display_name} (image attachments require a ready Hermes vision sidecar)")
            continue
        if suffix in AUDIO_EXTENSIONS:
            unsupported.append(f"{display_name} (audio attachments are not supported in Hermes chat)")
            continue
        if _is_text_attachment(f):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                text_blocks.append(f"File: {display_name}\n```\n{content}\n```")
            except Exception:
                unsupported.append(f"{display_name} (could not read text content)")
            continue
        unsupported.append(f"{display_name} (binary attachments cannot be read as text in this chat mode)")
    return {
        "text_blocks": text_blocks,
        "image_files": image_files,
        "unsupported": unsupported,
    }


def _compose_message_with_attachments(
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
) -> tuple[str, list[Path]]:
    summary = _summarize_attachments(files, image_support=image_support, display_names=display_names)
    sections = list(summary["text_blocks"])
    if message:
        sections.append(f"User message: {message}")
    if summary["unsupported"]:
        notes = "\n".join(f"- {note}" for note in summary["unsupported"])
        sections.append(f"Attachment notes:\n{notes}")
    if not sections:
        sections.append(message or "User attached files without an additional text message.")
    return "\n\n".join(sections), summary["image_files"]


def _session_has_image_history(session: dict) -> bool:
    for message in session.get("messages", []):
        for file_name in message.get("files", []) or []:
            if Path(file_name).suffix.lower() in IMAGE_EXTENSIONS:
                return True
    return False


def _messages_for_active_segment(session: dict) -> list[dict]:
    messages = session.get("messages") or []
    active_segment = _active_chat_segment(session) or {}
    start_message_index = int(active_segment.get("start_message_index") or 0)
    if start_message_index <= 0:
        return messages
    return messages[start_message_index:]


def _active_segment_has_image_history(session: dict) -> bool:
    for message in _messages_for_active_segment(session):
        for file_name in message.get("files", []) or []:
            if Path(file_name).suffix.lower() in IMAGE_EXTENSIONS:
                return True
    return False


def _build_attachment_refs(files: list[Path], display_names: dict | None = None) -> list[dict]:
    refs = []
    for path in files or []:
        suffix = path.suffix.lower()
        kind = "image" if suffix in IMAGE_EXTENSIONS else "audio" if suffix in AUDIO_EXTENSIONS else "text" if _is_text_attachment(path) else "binary"
        refs.append({
            "stored_as": path.name,
            "display_name": _attachment_display_name(path, display_names),
            "kind": kind,
            "mime_type": _file_mime_type(path),
        })
    return refs


def _latest_user_turn(session: dict) -> dict | None:
    for message in reversed(session.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == "user":
            return message
    return None


def _latest_sidecar_asset_group(session: dict) -> list[dict]:
    asset_ids = set()
    for message in reversed(session.get("messages", [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        sidecar = message.get("sidecar_vision") or {}
        asset_ids = set(_clean_string_list(sidecar.get("asset_ids")))
        if asset_ids:
            break
    if not asset_ids:
        return []
    assets = []
    for asset in session.get("vision_assets", []) or []:
        if isinstance(asset, dict) and asset.get("id") in asset_ids:
            assets.append(copy.deepcopy(asset))
    return assets


def _latest_turn_used_sidecar_vision(session: dict) -> bool:
    latest_user = _latest_user_turn(session)
    return bool((latest_user or {}).get("sidecar_vision", {}).get("used"))


def _latest_turn_sidecar_asset_names(session: dict) -> list[str]:
    latest_user = _latest_user_turn(session)
    if not latest_user:
        return []
    sidecar = latest_user.get("sidecar_vision") or {}
    asset_names = []
    asset_map = {
        asset.get("id"): asset for asset in session.get("vision_assets", []) or []
        if isinstance(asset, dict) and asset.get("id")
    }
    for asset_id in _clean_string_list(sidecar.get("asset_ids")):
        asset = asset_map.get(asset_id) or {}
        label = str(asset.get("display_name") or "").strip()
        if label:
            asset_names.append(label)
    return asset_names


def _chat_session_meta(session: dict) -> dict:
    normalized = _normalize_chat_session(copy.deepcopy(session))
    context = _effective_session_context(normalized)
    active_segment = _active_chat_segment(normalized) or {}
    return {
        "profile": normalized.get("profile") or _selected_hermes_profile_name(),
        "active_segment_id": active_segment.get("id") or "",
        "active_segment_index": active_segment.get("index") or 1,
        "segments": copy.deepcopy(normalized.get("segments") or []),
        "transport_mode": normalized.get("transport_mode"),
        "transport_preference": normalized.get("transport_preference") or CHAT_TRANSPORT_AUTO,
        "transport_preference_label": _transport_preference_label(normalized.get("transport_preference")),
        "continuity_mode": normalized.get("continuity_mode"),
        "transport_notice": normalized.get("transport_notice") or "",
        "hermes_session_backed": normalized.get("continuity_mode") == CHAT_CONTINUITY_HERMES,
        "last_turn_used_sidecar_vision": _latest_turn_used_sidecar_vision(normalized),
        "last_turn_sidecar_asset_names": _latest_turn_sidecar_asset_names(normalized),
        "vision_asset_count": len(normalized.get("vision_assets") or []),
        "folder_id": context.get("folder_id") or "",
        "folder_title": context.get("folder_title") or "",
        "workspace_roots": context.get("workspace_roots") or [],
        "source_docs": context.get("source_docs") or [],
        "folder_workspace_roots": context.get("folder_workspace_roots") or [],
        "folder_source_docs": context.get("folder_source_docs") or [],
    }


def _format_chat_context_block(session: dict) -> str:
    context = _effective_session_context(session)
    folder_title = context.get("folder_title") or ""
    workspace_roots = context.get("workspace_roots") or []
    source_docs = context.get("source_docs") or []
    if not any((folder_title, workspace_roots, source_docs)):
        return ""

    sections = ["Chat workspace context:"]
    if folder_title:
        sections.append(f"Folder: {folder_title}")
    if workspace_roots:
        sections.append("Workspace roots:\n" + "\n".join(f"- {root}" for root in workspace_roots))
    if source_docs:
        sections.append("Pinned source docs:\n" + "\n".join(f"- {doc}" for doc in source_docs))

    doc_sections = []
    notes = []
    total_bytes = 0
    for source_doc in source_docs:
        path = Path(source_doc)
        if not path.exists():
            notes.append(f"{source_doc} (missing)")
            continue
        if not _is_text_attachment(path):
            notes.append(f"{source_doc} (not a readable text file)")
            continue
        try:
            raw_bytes = path.read_bytes()
        except Exception as exc:
            notes.append(f"{source_doc} (could not be read: {exc})")
            continue
        remaining = CHAT_CONTEXT_SOURCE_DOC_TOTAL_LIMIT - total_bytes
        if remaining <= 0:
            notes.append(f"{source_doc} (skipped because the source-doc context limit was reached)")
            continue
        snippet = raw_bytes[:min(CHAT_CONTEXT_SOURCE_DOC_LIMIT, remaining)]
        total_bytes += len(snippet)
        text = snippet.decode("utf-8", errors="replace")
        if len(snippet) < len(raw_bytes):
            notes.append(f"{source_doc} (truncated to {len(snippet)} bytes for chat context)")
        doc_sections.append(f"Source doc: {source_doc}\n```\n{text}\n```")

    sections.extend(doc_sections)
    if notes:
        sections.append("Source doc notes:\n" + "\n".join(f"- {note}" for note in notes))
    return "\n\n".join(sections)


def _compose_chat_turn_payload(
    session: dict,
    message: str,
    files: list[Path],
    image_support: bool,
    display_names: dict | None = None,
) -> tuple[str, list[Path]]:
    attachment_text, image_files = _compose_message_with_attachments(
        message,
        files,
        image_support=image_support,
        display_names=display_names,
    )
    context_block = _format_chat_context_block(session)
    sections = [section for section in (context_block, attachment_text) if section]
    return "\n\n".join(sections), image_files


def _integration_config_is_configured(value) -> bool:
    if isinstance(value, dict):
        if not value:
            return False
        return any(_integration_config_is_configured(item) for item in value.values())
    if isinstance(value, list):
        return any(_integration_config_is_configured(item) for item in value)
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return True


def _integration_entries(raw: dict | None = None) -> list[dict]:
    raw = raw if raw is not None else cfg.get_raw()
    integrations = []

    for key in INTEGRATION_SECTION_ORDER:
        if key not in raw:
            continue
        value = raw.get(key)
        if not isinstance(value, dict):
            continue
        integrations.append({
            "name": key,
            "label": INTEGRATION_SECTION_LABELS.get(key, key.title()),
            "kind": "integration",
            "config": cfg.mask_secrets(copy.deepcopy(value)),
            "configured": _integration_config_is_configured(value),
            "source": "top_level",
        })

    channels_cfg = raw.get("channels", {})
    toolsets = raw.get("platform_toolsets", {})
    if isinstance(channels_cfg, dict):
        for name, config_value in channels_cfg.items():
            if not isinstance(config_value, dict):
                continue
            integrations.append({
                "name": name,
                "label": name,
                "kind": "legacy_channel",
                "config": cfg.mask_secrets(copy.deepcopy(config_value)),
                "configured": _integration_config_is_configured(config_value),
                "enabled": bool(toolsets.get(name)),
                "source": "channels",
            })

    return integrations


def _discover_skill_entries() -> list[dict]:
    skills = []
    if not SKILLS_DIR.exists():
        return skills

    for root, dirs, files in os.walk(str(SKILLS_DIR)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if "SKILL.md" not in files:
            continue
        skill_md = Path(root) / "SKILL.md"
        fm = _skill_frontmatter(skill_md)
        rel_path = Path(root).relative_to(SKILLS_DIR)
        dir_name = str(rel_path)
        skill = {
            "name": fm.get("name", rel_path.name),
            "category": fm.get("category", ""),
            "description": fm.get("description", ""),
            "path": str(rel_path),
            "enabled": not dir_name.endswith(".disabled"),
            "frontmatter": fm,
        }
        skill["source"] = _read_skill_source_metadata(Path(root))
        skill["setup"] = _skill_setup_readiness(skill)
        skills.append(skill)
    return skills


def _configured_hook_keys(raw: dict | None = None) -> list[str]:
    raw = raw if raw is not None else cfg.get_raw()
    hooks_cfg = raw.get("hooks")
    if not isinstance(hooks_cfg, dict):
        return []
    return [
        str(key)
        for key, value in hooks_cfg.items()
        if _integration_config_is_configured(value)
    ]


def _skill_matches_terms(skill: dict, terms: tuple[str, ...]) -> bool:
    needles = {
        str(term or "").strip().lower()
        for term in terms or ()
        if str(term or "").strip()
    }
    if not needles:
        return False

    haystack = set()
    for value in (
        skill.get("name"),
        skill.get("path"),
        ((skill.get("frontmatter") or {}).get("name") if isinstance(skill.get("frontmatter"), dict) else ""),
    ):
        text = str(value or "").strip().lower().replace("\\", "/")
        if not text:
            continue
        haystack.add(text)
        haystack.update(part for part in text.split("/") if part)
    return bool(haystack & needles)


def _joined_labels(values: list[str]) -> str:
    items = [str(value).strip() for value in values if str(value).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _skill_absolute_path(skill: dict) -> Path | None:
    rel_path = _safe_skill_rel_path(skill.get("path") or "")
    if not rel_path:
        return None
    return SKILLS_DIR / rel_path


def _safe_skill_rel_path(value: str | Path) -> str:
    text = _normalize_skill_rel_path(value)
    if not text:
        return ""
    parts = []
    for part in PurePosixPath(text).parts:
        if part in ("", "."):
            continue
        if part == "..":
            return ""
        parts.append(part)
    return "/".join(parts)


def _skill_request_paths(requested: str | Path) -> dict:
    requested_rel = _safe_skill_rel_path(requested)
    if not requested_rel:
        return {}

    requested_parts = requested_rel.split("/")
    base_name = requested_parts[-1]
    while base_name.endswith(".disabled"):
        base_name = base_name[:-9]
    if not base_name:
        return {}

    base_rel = "/".join(requested_parts[:-1] + [base_name])
    base_path = SKILLS_DIR / base_rel
    disabled_rel = base_rel + ".disabled"
    disabled_path = SKILLS_DIR / disabled_rel

    variants = []
    seen = set()
    for rel in (requested_rel, base_rel, disabled_rel):
        if rel and rel not in seen:
            variants.append(SKILLS_DIR / rel)
            seen.add(rel)

    parent_dir = base_path.parent
    if parent_dir.exists():
        disabled_prefix = base_path.name + ".disabled"
        for sibling in sorted(parent_dir.iterdir(), key=lambda item: item.name):
            if not sibling.is_dir() or not sibling.name.startswith(disabled_prefix):
                continue
            sibling_rel = _safe_skill_rel_path(sibling.relative_to(SKILLS_DIR))
            if sibling_rel and sibling_rel not in seen:
                variants.append(sibling)
                seen.add(sibling_rel)

    return {
        "requested_rel": requested_rel,
        "base_rel": base_rel,
        "disabled_rel": disabled_rel,
        "base_path": base_path,
        "disabled_path": disabled_path,
        "variants": variants,
    }


def _replace_skill_dir(src: Path, dst: Path) -> None:
    if dst.exists() and dst != src:
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))


def _skill_apply_action(requested: str | Path, action: str) -> dict:
    info = _skill_request_paths(requested)
    if not info:
        return {"found": False, "error": "Skill path is required"}

    base_path = info["base_path"]
    disabled_path = info["disabled_path"]
    existing_variants = [path for path in info["variants"] if path.exists()]
    action_name = str(action or "").strip().lower()

    if action_name == "enable":
        if base_path.exists():
            return {"found": True, "changed": False, "enabled": True, "path": info["base_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if not candidate:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        _replace_skill_dir(candidate, base_path)
        return {"found": True, "changed": True, "enabled": True, "path": info["base_rel"]}

    if action_name == "disable":
        if base_path.exists():
            _replace_skill_dir(base_path, disabled_path)
            return {"found": True, "changed": True, "enabled": False, "path": info["disabled_rel"]}
        candidate = next((path for path in existing_variants if path != base_path), None)
        if candidate:
            candidate_rel = _safe_skill_rel_path(candidate.relative_to(SKILLS_DIR))
            return {"found": True, "changed": False, "enabled": False, "path": candidate_rel}
        return {"found": False, "error": f"Skill '{requested}' not found"}

    if action_name == "remove":
        target = base_path if base_path.exists() else next(iter(existing_variants), None)
        if not target:
            return {"found": False, "error": f"Skill '{requested}' not found"}
        removed_rel = _safe_skill_rel_path(target.relative_to(SKILLS_DIR))
        shutil.rmtree(target)
        return {"found": True, "changed": True, "removed": True, "path": removed_rel}

    return {"found": False, "error": f"Unsupported action '{action}'"}


def _skill_wants_integration_setup(skill: dict, env_blockers: list[dict]) -> bool:
    if any(str(blocker.get("group") or "").strip() == "Channel" for blocker in env_blockers):
        return True
    haystack = " ".join(
        str(value or "").strip().lower()
        for value in (
            skill.get("name"),
            skill.get("path"),
            skill.get("category"),
            ((skill.get("source") or {}).get("source_repo") if isinstance(skill.get("source"), dict) else ""),
        )
    )
    return any(hint in haystack for hint in ("discord", "whatsapp", "slack", "telegram", "matrix", "webhook"))


def _skill_setup_details(skill: dict) -> dict:
    frontmatter = skill.get("frontmatter") if isinstance(skill.get("frontmatter"), dict) else {}
    metadata = frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
    ui_setup = {}
    if isinstance(metadata.get("hermes_web_ui"), dict):
        ui_setup = metadata["hermes_web_ui"].get("setup") if isinstance(metadata["hermes_web_ui"].get("setup"), dict) else {}

    env_vars = []
    seen_env = set()
    for entry in ui_setup.get("env_vars") if isinstance(ui_setup.get("env_vars"), list) else []:
        normalized = _normalize_capability_env_var(entry)
        key = normalized.get("key")
        if not key or key in seen_env:
            continue
        seen_env.add(key)
        env_vars.append(normalized)
    prerequisites = frontmatter.get("prerequisites")
    legacy_env_vars = _clean_string_list(prerequisites.get("env_vars")) if isinstance(prerequisites, dict) else []
    for env_key in legacy_env_vars:
        normalized = _normalize_capability_env_var(env_key)
        key = normalized.get("key")
        if not key or key in seen_env:
            continue
        seen_env.add(key)
        env_vars.append(normalized)

    credential_files = []
    seen_paths = set()
    for entry in ui_setup.get("credential_files") if isinstance(ui_setup.get("credential_files"), list) else []:
        normalized = _normalize_capability_credential_file(entry)
        rel_path = normalized.get("path")
        if not rel_path or rel_path in seen_paths:
            continue
        seen_paths.add(rel_path)
        credential_files.append(normalized)
    required_files = frontmatter.get("required_credential_files")
    if isinstance(required_files, list):
        for entry in required_files:
            normalized = _normalize_capability_credential_file(entry)
            rel_path = normalized.get("path")
            if not rel_path or rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)
            credential_files.append(normalized)

    required_commands = []
    seen_commands = set()
    for entry in ui_setup.get("required_commands") if isinstance(ui_setup.get("required_commands"), list) else []:
        normalized = _normalize_capability_required_command(entry)
        name = normalized.get("name")
        if not name or name in seen_commands:
            continue
        seen_commands.add(name)
        required_commands.append(normalized)
    openclaw_meta = metadata.get("openclaw") if isinstance(metadata.get("openclaw"), dict) else {}
    legacy_bins = _clean_string_list(((openclaw_meta.get("requires") or {}).get("bins"))) if isinstance(openclaw_meta, dict) else []
    for binary in legacy_bins:
        normalized = _normalize_capability_required_command(binary)
        name = normalized.get("name")
        if not name or name in seen_commands:
            continue
        seen_commands.add(name)
        required_commands.append(normalized)

    return {
        "env_vars": env_vars,
        "credential_files": credential_files,
        "required_commands": required_commands,
    }


def _skill_setup_readiness(skill: dict) -> dict:
    skill_dir = _skill_absolute_path(skill)
    issues = []
    blockers = []
    actions = []
    details = _skill_setup_details(skill)

    for entry in details.get("credential_files") or []:
        rel_path = str(entry.get("path") or "").strip()
        if not rel_path or not skill_dir:
            continue
        target = (skill_dir / rel_path).resolve()
        if not target.exists():
            message = f"missing credential file {rel_path}"
            issues.append(message)
            blockers.append({
                "kind": "credential_file",
                "path": rel_path,
                "label": str(entry.get("label") or Path(rel_path).name).strip(),
                "description": str(entry.get("description") or "").strip(),
                "absolute_path": str(target),
                "message": message,
            })

    env_blockers = []
    for env_entry in details.get("env_vars") or []:
        env_key = str(env_entry.get("key") or "").strip()
        if not env_key:
            continue
        if not _runtime_env_value(env_key, ""):
            message = f"missing env var {env_key}"
            issues.append(message)
            blocker = {
                "kind": "env_var",
                "key": env_key,
                "group": env_entry.get("group") or _classify_env_key(env_key),
                "label": env_entry.get("label") or env_key,
                "description": env_entry.get("description") or "",
                "default_value": str(env_entry.get("default_value") or "").strip(),
                "secret": bool(env_entry.get("secret")),
                "message": message,
            }
            blockers.append(blocker)
            env_blockers.append(blocker)

    for command_entry in details.get("required_commands") or []:
        binary = str(command_entry.get("name") or "").strip()
        if not binary:
            continue
        if shutil.which(binary) is None:
            message = f"missing command {binary}"
            issues.append(message)
            blockers.append({
                "kind": "command",
                "name": binary,
                "description": str(command_entry.get("description") or "").strip(),
                "message": message,
            })

    for blocker in env_blockers:
        actions.append({
            "type": "env_var",
            "key": blocker.get("key"),
            "group": blocker.get("group"),
            "description": blocker.get("description"),
            "default_value": blocker.get("default_value"),
            "secret": blocker.get("secret"),
            "label": f"Set {blocker.get('label') or blocker.get('key')}",
        })

    if _skill_wants_integration_setup(skill, env_blockers):
        actions.append({
            "type": "screen",
            "screen": "channels",
            "label": "Open Apps & Integrations",
        })

    unique_actions = []
    seen_actions = set()
    for action in actions:
        token = json.dumps(action, sort_keys=True)
        if token in seen_actions:
            continue
        seen_actions.add(token)
        unique_actions.append(action)

    return {
        "ready": not issues,
        "issues": issues,
        "blockers": blockers,
        "actions": unique_actions,
        "requirements": details,
    }


def _skill_env_var_presets(skills: list[dict] | None = None) -> dict[str, dict]:
    catalog = {}
    for skill in skills if skills is not None else _discover_skill_entries():
        requirements = ((skill.get("setup") or {}).get("requirements") if isinstance(skill.get("setup"), dict) else {}) or {}
        for entry in requirements.get("env_vars") if isinstance(requirements.get("env_vars"), list) else []:
            normalized = _normalize_capability_env_var(entry)
            key = normalized.get("key")
            if not key:
                continue
            existing = catalog.get(key, {})
            merged = {
                **_env_var_metadata(key),
                **existing,
                **normalized,
            }
            catalog[key] = merged
    return catalog


def _starter_pack_skill_group(item_id: str) -> dict | None:
    needle = str(item_id or "").strip()
    if not needle:
        return None
    for group in STARTER_PACK_SKILL_GROUPS:
        if group.get("id") == needle:
            return group
    return None


def _starter_pack_install_candidates(group: dict) -> list[dict]:
    candidates = []
    for candidate in group.get("install_candidates") or ():
        if not isinstance(candidate, dict):
            continue
        candidates.append({
            "identifier": str(candidate.get("identifier") or "").strip(),
            "label": str(candidate.get("label") or candidate.get("identifier") or "").strip(),
            "source": str(candidate.get("source") or "").strip(),
            "description": str(candidate.get("description") or "").strip(),
            "recommended": bool(candidate.get("recommended")),
        })
    return [candidate for candidate in candidates if candidate.get("identifier")]


def _starter_pack_candidate_matches_enabled_skill(candidate: dict, enabled_skills: list[dict]) -> bool:
    terms = set()
    for value in (candidate.get("identifier"), candidate.get("label")):
        text = str(value or "").strip().lower().replace("\\", "/")
        if not text:
            continue
        terms.add(text)
        terms.update(part for part in text.split("/") if part)
    if not terms:
        return False
    return any(_skill_matches_terms(skill, tuple(terms)) for skill in enabled_skills)


def _starter_pack_item_from_group(group: dict, enabled_skills: list[dict]) -> dict:
    terms = tuple(str(term).lower() for term in group.get("terms") or ())
    matches = [
        skill for skill in enabled_skills
        if _skill_matches_terms(skill, terms)
    ]
    install_candidates = _starter_pack_install_candidates(group)
    installed_candidates = [
        candidate for candidate in install_candidates
        if _starter_pack_candidate_matches_enabled_skill(candidate, enabled_skills)
    ]
    install_available = bool(install_candidates) and not bool(installed_candidates)
    install_action_label = "Install" if not matches else "Install Recommended"
    match_names = [str(skill.get("name") or skill.get("path") or "").strip() for skill in matches]
    match_paths = [_safe_skill_rel_path(skill.get("path") or "") for skill in matches if _safe_skill_rel_path(skill.get("path") or "")]
    readiness_checks = [_skill_setup_readiness(skill) for skill in matches]
    readiness_issues = []
    readiness_actions = []
    for check in readiness_checks:
        readiness_issues.extend(check.get("issues") or [])
        readiness_actions.extend(check.get("actions") or [])
    readiness_issues = list(dict.fromkeys(readiness_issues))
    deduped_actions = []
    seen_actions = set()
    for action in readiness_actions:
        token = json.dumps(action, sort_keys=True)
        if token in seen_actions:
            continue
        seen_actions.add(token)
        deduped_actions.append(action)
    if matches and readiness_issues and not deduped_actions and match_paths:
        deduped_actions.append({
            "type": "skill_setup",
            "path": match_paths[0],
            "label": "Open Setup",
        })

    if matches and not readiness_issues:
        status = "ready"
        detail = f"Installed via {_joined_labels(match_names)}."
        ready = True
    elif matches:
        status = "attention"
        detail = (
            f"Installed via {_joined_labels(match_names)}, but setup is still needed: "
            f"{_joined_labels(readiness_issues)}."
        )
        ready = False
    else:
        status = "missing"
        detail = group.get("description", "").strip() + " Not installed yet."
        ready = False

    if matches and install_available:
        preferred_candidate = next((candidate for candidate in install_candidates if candidate.get("recommended")), None)
        preferred_label = str((preferred_candidate or install_candidates[0]).get("label") or "").strip()
        if preferred_label:
            detail = detail.rstrip(".") + f". The recommended {preferred_label} starter-pack install is still available."

    return {
        "id": group.get("id"),
        "label": group.get("label"),
        "kind": "skill",
        "status": status,
        "ready": ready,
        "detail": detail,
        "matches": match_names,
        "matched_skill_paths": match_paths,
        "query": str(group.get("query") or "").strip(),
        "install_candidates": install_candidates,
        "installed_candidates": installed_candidates,
        "install_available": install_available,
        "install_action_label": install_action_label,
        "setup_notes": [str(note).strip() for note in (group.get("setup_notes") or []) if str(note).strip()],
        "supports_install": bool(install_candidates),
        "issues": readiness_issues,
        "setup_actions": deduped_actions,
    }


def _memory_runtime_status(raw: dict | None = None) -> dict:
    raw = raw if raw is not None else cfg.get_raw()
    memory_cfg = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    cli_toolsets = set(_clean_string_list(((raw.get("platform_toolsets") or {}).get("cli"))))
    openai_key_source = _runtime_env_source("OPENAI_API_KEY")
    memory_enabled = bool(memory_cfg.get("memory_enabled"))
    user_profile_enabled = bool(memory_cfg.get("user_profile_enabled"))
    cli_tool_enabled = "memory" in cli_toolsets
    openai_api_key_present = bool(openai_key_source)
    semantic_search_ready = memory_enabled and cli_tool_enabled and openai_api_key_present

    if not memory_enabled:
        detail = "Hermes memory is disabled."
    elif not cli_tool_enabled:
        detail = "Memory is enabled in config, but the CLI memory tool is not active for chats."
    elif not openai_api_key_present:
        detail = "Add OPENAI_API_KEY to the Hermes environment to enable OpenAI-backed memory search."
    else:
        detail = "Hermes memory is enabled and can use your OpenAI API key for semantic recall."

    return {
        "enabled": memory_enabled,
        "user_profile_enabled": user_profile_enabled,
        "cli_tool_enabled": cli_tool_enabled,
        "openai_api_key_present": openai_api_key_present,
        "openai_api_key_source": openai_key_source,
        "semantic_search_ready": semantic_search_ready,
        "detail": detail,
    }


def _chat_runtime_status(raw: dict | None = None, *, skills: list[dict] | None = None) -> dict:
    raw = raw if raw is not None else cfg.get_raw()
    skills = copy.deepcopy(skills) if skills is not None else _discover_skill_entries()
    enabled_skills = [skill for skill in skills if skill.get("enabled") is not False]
    integrations = _integration_entries(raw)
    configured_integrations = [item for item in integrations if item.get("configured")]
    hook_keys = _configured_hook_keys(raw)
    cli_toolsets = set(_clean_string_list(((raw.get("platform_toolsets") or {}).get("cli"))))
    memory = _memory_runtime_status(raw)

    active_features = []
    reasons = []
    blocking_features = []
    if memory.get("enabled") and memory.get("cli_tool_enabled"):
        active_features.append("memory")
        reasons.append("Hermes memory is enabled for chat sessions.")
    if enabled_skills and "skills" in cli_toolsets:
        active_features.append("skills")
        reasons.append(f"{len(enabled_skills)} Hermes skill{'s are' if len(enabled_skills) != 1 else ' is'} enabled.")
    if configured_integrations:
        active_features.append("integrations")
        reasons.append(
            f"{len(configured_integrations)} integration{'s are' if len(configured_integrations) != 1 else ' is'} configured."
        )
    if hook_keys:
        active_features.append("hooks")
        reasons.append(f"Hooks are configured: {_joined_labels(hook_keys)}.")
        blocking_features.append("hooks")

    requires_cli = bool(blocking_features)
    if requires_cli:
        cli_reason = "Hermes CLI is required because " + _joined_labels(blocking_features) + " " + (
            "is active."
            if len(blocking_features) == 1
            else "are active."
        )
    else:
        cli_reason = ""

    starter_items = []
    for group in STARTER_PACK_SKILL_GROUPS:
        item = _starter_pack_item_from_group(group, enabled_skills)
        if item.get("status") == "ready":
            continue
        starter_items.append(item)

    return {
        "requires_cli": requires_cli,
        "cli_reason": cli_reason,
        "reasons": reasons,
        "active_features": active_features,
        "blocking_features": blocking_features,
        "memory": memory,
        "skills": {
            "detected_count": len(skills),
            "enabled_count": len(enabled_skills),
            "tool_enabled": "skills" in cli_toolsets,
        },
        "integrations": {
            "configured_count": len(configured_integrations),
            "configured_names": [item.get("label") or item.get("name") for item in configured_integrations],
        },
        "hooks": {
            "configured": bool(hook_keys),
            "keys": hook_keys,
        },
        "starter_pack": {
            "items": starter_items,
        },
    }


def _validated_transport_preference(value) -> tuple[str | None, str]:
    normalized = _normalize_transport_preference(value)
    if normalized != CHAT_TRANSPORT_API:
        return normalized, ""

    runtime = _chat_runtime_status()
    if runtime.get("requires_cli"):
        return CHAT_TRANSPORT_CLI, runtime.get("cli_reason") or "Hermes CLI is required right now."
    if not _check_api_server():
        return CHAT_TRANSPORT_CLI, "API replay is unavailable right now, so Hermes CLI will be used."
    return normalized, ""


def _vision_reanalysis_requested(message: str, session: dict) -> bool:
    if not message or not _latest_sidecar_asset_group(session):
        return False
    return bool(VISION_REFERENCE_HINT_RE.search(message))


def _vision_asset_disk_path(asset: dict) -> Path | None:
    stored_as = secure_filename(str(asset.get("stored_as") or "").strip())
    if not stored_as:
        return None
    path = UPLOAD_FOLDER / stored_as
    return path if path.exists() else None


def _strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


def _coerce_sidecar_string_list(value) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                items = decoded
            else:
                items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
        else:
            items = [part.strip() for part in re.split(r"(?:\r?\n|;\s*)", text) if part.strip()]
            if not items:
                items = [text]
    else:
        return []
    normalized = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        text = re.sub(r"^[\-\*\u2022]+\s*", "", text)
        text = re.sub(r"^\d+[\.\)]\s*", "", text)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _find_json_object_candidates(text: str) -> list[str]:
    raw = str(text or "")
    candidates = []
    seen = set()

    def add(candidate: str):
        snippet = str(candidate or "").strip()
        if not snippet or snippet in seen:
            return
        seen.add(snippet)
        candidates.append(snippet)

    add(_strip_json_fence(raw))
    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
        add(match.group(1))

    depth = 0
    start = None
    in_string = False
    escape = False
    for idx, ch in enumerate(raw):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                add(raw[start:idx + 1])
                start = None

    candidates.sort(key=len, reverse=True)
    return candidates


def _looks_like_sidecar_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = set(payload.keys())
    return bool({"overall_summary", "images", "follow_up_hints", "visible_text", "details"} & keys)


def _extract_sidecar_json_payload(raw_text: str) -> dict | None:
    for candidate in _find_json_object_candidates(raw_text):
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if _looks_like_sidecar_payload(payload):
            return payload
        if isinstance(payload, dict):
            for value in payload.values():
                if _looks_like_sidecar_payload(value):
                    return value
            return payload
    return None


def _parse_sidecar_payload(raw_text: str, image_labels: list[str]) -> dict:
    raw_text = str(raw_text or "").strip()
    fallback = {
        "overall_summary": raw_text,
        "images": [],
        "follow_up_hints": [],
        "raw_text": raw_text,
    }
    if not raw_text:
        return fallback
    payload = _extract_sidecar_json_payload(raw_text)
    if not payload:
        return fallback
    images = payload.get("images")
    normalized_images = []
    overall_summary = str(payload.get("overall_summary") or "").strip()
    if isinstance(images, list):
        for idx, item in enumerate(images):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                label = image_labels[idx] if idx < len(image_labels) else f"Image {idx + 1}"
            summary = str(item.get("summary") or "").strip()
            if not summary:
                summary = overall_summary
            normalized_images.append({
                "label": label,
                "summary": summary,
                "visible_text": _coerce_sidecar_string_list(item.get("visible_text")),
                "details": _coerce_sidecar_string_list(item.get("details")),
                "follow_up_hints": _coerce_sidecar_string_list(item.get("follow_up_hints")),
            })
    if not overall_summary and normalized_images:
        overall_summary = normalized_images[0].get("summary") or ""
    if image_labels and not normalized_images:
        top_level_visible_text = _coerce_sidecar_string_list(payload.get("visible_text"))
        top_level_details = _coerce_sidecar_string_list(payload.get("details"))
        top_level_hints = _coerce_sidecar_string_list(payload.get("follow_up_hints"))
        for idx, label in enumerate(image_labels):
            normalized_images.append({
                "label": label,
                "summary": overall_summary if idx == 0 else "",
                "visible_text": top_level_visible_text if idx == 0 else [],
                "details": top_level_details if idx == 0 else [],
                "follow_up_hints": top_level_hints if idx == 0 else [],
            })
    return {
        "overall_summary": overall_summary,
        "images": normalized_images,
        "follow_up_hints": _coerce_sidecar_string_list(payload.get("follow_up_hints")),
        "raw_text": raw_text,
    }


def _format_sidecar_context_block(sidecar_result: dict) -> str:
    if not sidecar_result:
        return ""
    lines = ["Vision sidecar analysis:"]
    if sidecar_result.get("reanalysis"):
        lines.append("This analysis refreshes an earlier screenshot because the user referred back to it.")
    overall_summary = str(sidecar_result.get("overall_summary") or "").strip()
    if overall_summary:
        lines.append(f"Overall summary: {overall_summary}")
    image_results = sidecar_result.get("images") or []
    for idx, item in enumerate(image_results, start=1):
        label = str(item.get("label") or f"Image {idx}").strip()
        asset_id = str(item.get("asset_id") or "").strip()
        lines.append(f"{label}{f' [{asset_id}]' if asset_id else ''}:")
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(f"- Summary: {summary}")
        visible_text = [str(value).strip() for value in (item.get("visible_text") or []) if str(value).strip()]
        if visible_text:
            lines.append("- Visible text:")
            lines.extend(f"  - {value}" for value in visible_text)
        details = [str(value).strip() for value in (item.get("details") or []) if str(value).strip()]
        if details:
            lines.append("- Details:")
            lines.extend(f"  - {value}" for value in details)
        follow_up_hints = [str(value).strip() for value in (item.get("follow_up_hints") or []) if str(value).strip()]
        if follow_up_hints:
            lines.append("- Follow-up hints:")
            lines.extend(f"  - {value}" for value in follow_up_hints)
    return "\n".join(lines)


def _update_session_vision_assets(
    session: dict,
    image_files: list[Path],
    parsed_payload: dict,
    *,
    source_message_index: int,
    source_message_timestamp: str,
    focus_message: str,
    target: dict,
) -> list[str]:
    assets = session.setdefault("vision_assets", [])
    now = datetime.now().isoformat()
    asset_ids = []
    existing_by_stored_as = {
        asset.get("stored_as"): asset for asset in assets
        if isinstance(asset, dict) and asset.get("stored_as")
    }
    per_image_results = parsed_payload.get("images") or []
    for idx, image_file in enumerate(image_files):
        stored_as = image_file.name
        asset = existing_by_stored_as.get(stored_as)
        if not asset:
            asset = {
                "id": f"vis-{uuid.uuid4().hex[:10]}",
                "stored_as": stored_as,
                "display_name": image_file.name,
                "mime_type": _file_mime_type(image_file),
                "created_at": now,
                "source_message_index": source_message_index,
                "source_message_timestamp": source_message_timestamp,
            }
            assets.append(asset)
            existing_by_stored_as[stored_as] = asset
        image_result = per_image_results[idx] if idx < len(per_image_results) else {}
        asset["display_name"] = str(image_result.get("label") or asset.get("display_name") or image_file.name).strip() or image_file.name
        asset["mime_type"] = _file_mime_type(image_file)
        asset["source_message_index"] = source_message_index
        asset["source_message_timestamp"] = source_message_timestamp
        asset["last_analysis"] = {
            "summary": str(image_result.get("summary") or parsed_payload.get("overall_summary") or "").strip(),
            "raw_text": str(parsed_payload.get("raw_text") or "").strip(),
            "focus": focus_message.strip(),
            "analyzed_at": now,
            "model": str(target.get("model") or "").strip(),
            "provider": str(target.get("provider") or "").strip(),
        }
        asset_ids.append(asset["id"])
        if idx < len(per_image_results):
            per_image_results[idx]["asset_id"] = asset["id"]
    return asset_ids


def _chat_backend_error_detail(exc: Exception) -> str:
    message = str(exc or "").strip()
    message = re.sub(r"^API server returned HTTP \d+:\s*", "", message)
    message = re.sub(r"^API server error:\s*", "", message)
    return message.strip()


def _chat_backend_error_is_rate_limited(exc: Exception) -> bool:
    detail = _chat_backend_error_detail(exc).lower()
    return (
        "rate limit" in detail
        or "rate-limit" in detail
        or "rate limited" in detail
        or "too many requests" in detail
        or "http 429" in str(exc).lower()
    )


def _chat_backend_error_is_retryable(exc: Exception) -> bool:
    status_code = int(getattr(exc, "status_code", 502) or 502)
    if status_code >= 500 or _chat_backend_error_is_rate_limited(exc):
        return True
    detail = _chat_backend_error_detail(exc).lower()
    retryable_terms = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "unreachable",
        "overloaded",
        "connection refused",
        "connection reset",
        "upstream request failed",
    )
    return any(term in detail for term in retryable_terms)


def _targets_equivalent(left: dict | None, right: dict | None) -> bool:
    left = left or {}
    right = right or {}
    return (
        str(left.get("provider") or "").strip().lower(),
        str(left.get("base_url") or "").strip().rstrip("/"),
        str(left.get("model") or "").strip(),
        str(left.get("routing_provider") or "").strip(),
    ) == (
        str(right.get("provider") or "").strip().lower(),
        str(right.get("base_url") or "").strip().rstrip("/"),
        str(right.get("model") or "").strip(),
        str(right.get("routing_provider") or "").strip(),
    )


def _chat_completion_request(target: dict, messages: list[dict]) -> str:
    import socket
    import urllib.error
    import urllib.request

    payload = {"model": target["model"] or "hermes-agent", "messages": messages, "stream": False}
    api_url = str(target.get("base_url") or "").strip() or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
    provider_type = str(target.get("provider") or "").strip().lower()
    routing_provider = str(target.get("routing_provider") or "").strip()
    if provider_type == "openrouter" and routing_provider:
        payload["provider"] = {
            "order": [routing_provider],
            "allow_fallbacks": True,
        }
    headers = _api_server_headers(target.get("api_key"), target.get("provider"), target)
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        _build_openai_api_url(api_url, "chat/completions"),
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=CHAT_REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
            if isinstance(result, dict) and result.get("error"):
                error_payload = result.get("error") or {}
                if isinstance(error_payload, dict):
                    error_message = error_payload.get("message") or error_payload.get("code") or "API server returned an error"
                else:
                    error_message = str(error_payload)
                raise ChatBackendError(f"API server error: {error_message}")
            choices = result.get("choices") or []
            if not choices:
                raise ChatBackendError("API server returned no choices")
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    item.get("text", "") for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ).strip()
            if not isinstance(content, str) or not content.strip():
                raise ChatBackendError("API server returned an empty message")
            return content
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        detail = _summarize_upstream_error_detail(body, str(exc.reason or "upstream request failed"))
        raise ChatBackendError(f"API server returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        reason_text = str(reason)
        if isinstance(reason, TimeoutError) or "timed out" in reason_text.lower():
            raise ChatRequestTimeout(f"API server did not respond within {CHAT_REQUEST_TIMEOUT} seconds") from exc
        raise ChatBackendError(f"API server is unreachable: {reason_text}") from exc
    except socket.timeout as exc:
        raise ChatRequestTimeout(f"API server did not respond within {CHAT_REQUEST_TIMEOUT} seconds") from exc


def _run_sidecar_vision_analysis(
    session: dict,
    message: str,
    files: list[Path],
    *,
    user_message: dict,
    file_display_names: dict | None = None,
) -> dict:
    image_files = [path for path in files or [] if path.suffix.lower() in IMAGE_EXTENSIONS]
    reanalysis = False
    if not image_files and _vision_reanalysis_requested(message, session):
        image_files = [path for path in (
            _vision_asset_disk_path(asset) for asset in _latest_sidecar_asset_group(session)
        ) if path is not None]
        reanalysis = bool(image_files)
    if not image_files:
        return {}

    import base64

    image_labels = [
        _attachment_display_name(path, file_display_names) if path in (files or []) else (path.name if path else "Image")
        for path in image_files
    ]
    user_text = message.strip() or "User attached screenshots without extra text."
    if reanalysis:
        analysis_goal = (
            "The user is asking a follow-up about an earlier screenshot. Refresh the visual analysis with extra focus on "
            f"this question: {user_text}"
        )
    else:
        analysis_goal = f"The user attached new screenshots. Focus on what matters for this request: {user_text}"
    content = [{
        "type": "text",
        "text": (
            "You are a sidecar vision interpreter for Hermes CLI. Analyze the images and return JSON only with this schema: "
            "{\"overall_summary\":\"...\",\"follow_up_hints\":[\"...\"],\"images\":["
            "{\"label\":\"...\",\"summary\":\"...\",\"visible_text\":[\"...\"],\"details\":[\"...\"],\"follow_up_hints\":[\"...\"]}"
            "]}. Keep each list concise and preserve exact visible text when it matters.\n"
            f"{analysis_goal}"
        ),
    }]
    for idx, image_file in enumerate(image_files, start=1):
        label = image_labels[idx - 1] if idx - 1 < len(image_labels) else image_file.name
        try:
            with image_file.open("rb") as handle:
                b64 = base64.b64encode(handle.read()).decode("utf-8")
        except Exception as exc:
            raise ChatBackendError(f"Vision sidecar could not read {image_file.name}: {exc}") from exc
        ext = image_file.suffix.lower().replace(".", "") or "png"
        content.append({"type": "text", "text": f"Image {idx}: {label}"})
        content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})

    target = _resolve_api_target(prefer_vision=True)
    try:
        raw_text = _chat_completion_request(target, [{"role": "user", "content": content}])
    except ChatBackendError as exc:
        model_id = str(target.get("model") or "the configured vision model").strip()
        detail = _chat_backend_error_detail(exc) or "upstream request failed"
        if _chat_backend_error_is_rate_limited(exc):
            raise ChatBackendError(
                f"Vision sidecar is temporarily rate-limited for {model_id}. Retry shortly or switch the vision model/provider in Providers. Details: {detail}",
                status_code=503,
            ) from exc
        raise ChatBackendError(
            f"Vision sidecar failed for {model_id}. Details: {detail}",
            status_code=getattr(exc, "status_code", 502),
        ) from exc
    parsed_payload = _parse_sidecar_payload(raw_text, image_labels)
    asset_ids = _update_session_vision_assets(
        session,
        image_files,
        parsed_payload,
        source_message_index=len(session.get("messages", [])) - 1,
        source_message_timestamp=str(user_message.get("timestamp") or ""),
        focus_message=user_text,
        target=target,
    )
    summary = parsed_payload.get("overall_summary") or parsed_payload.get("raw_text") or ""
    user_message["sidecar_vision"] = {
        "used": True,
        "status": "ok",
        "asset_ids": asset_ids,
        "summary": str(summary).strip(),
        "analysis_mode": "reanalysis" if reanalysis else "sidecar",
        "reanalysis": reanalysis,
    }
    return {
        "overall_summary": parsed_payload.get("overall_summary") or parsed_payload.get("raw_text") or "",
        "images": parsed_payload.get("images") or [],
        "follow_up_hints": parsed_payload.get("follow_up_hints") or [],
        "raw_text": parsed_payload.get("raw_text") or raw_text.strip(),
        "asset_ids": asset_ids,
        "reanalysis": reanalysis,
        "analysis_mode": "reanalysis" if reanalysis else "sidecar",
    }


def _compose_cli_prompt_with_sidecar(
    session: dict,
    message: str,
    files: list[Path],
    *,
    sidecar_result: dict | None = None,
    file_display_names: dict | None = None,
) -> str:
    non_image_files = [path for path in files or [] if path.suffix.lower() not in IMAGE_EXTENSIONS]
    prompt, _ = _compose_chat_turn_payload(
        session,
        message,
        non_image_files,
        image_support=False,
        display_names=file_display_names,
    )
    sidecar_block = _format_sidecar_context_block(sidecar_result or {})
    sections = [section for section in (prompt, sidecar_block) if section]
    return "\n\n".join(sections)


def _plan_chat_request(session: dict, files: list[Path]) -> dict:
    normalized = _normalize_chat_session(copy.deepcopy(session))
    transport_preference = _normalize_transport_preference(normalized.get("transport_preference"))
    has_image_files = any(f.suffix.lower() in IMAGE_EXTENSIONS for f in files or [])
    image_support, image_reason = _image_attachment_support_status()
    api_server_enabled = _check_api_server()
    runtime = _chat_runtime_status()
    notice = normalized.get("transport_notice") or ""

    if runtime.get("requires_cli"):
        transport = CHAT_TRANSPORT_CLI
        notice = runtime.get("cli_reason") or notice
    elif transport_preference == CHAT_TRANSPORT_API and api_server_enabled:
        transport = CHAT_TRANSPORT_API
    elif transport_preference == CHAT_TRANSPORT_API:
        transport = CHAT_TRANSPORT_CLI
        notice = "API replay is unavailable right now, so Hermes CLI will be used."
    elif transport_preference == CHAT_TRANSPORT_CLI:
        transport = CHAT_TRANSPORT_CLI
    else:
        transport = CHAT_TRANSPORT_API if api_server_enabled else CHAT_TRANSPORT_CLI
        if transport == CHAT_TRANSPORT_CLI and transport_preference is None and runtime.get("cli_reason"):
            notice = runtime.get("cli_reason")

    return {
        "transport": transport,
        "cancel_supported": transport == CHAT_TRANSPORT_CLI,
        "image_support": image_support,
        "image_reason": image_reason,
        "api_server_enabled": api_server_enabled,
        "transport_notice": notice,
        "use_sidecar_vision": transport == CHAT_TRANSPORT_CLI and has_image_files,
        "runtime": runtime,
    }


def _parse_hermes_chat_result(output: str) -> tuple[str, str | None]:
    session_match = re.search(r"(?mi)^session_id:\s*(\S+)\s*$", output)
    hermes_session_id = session_match.group(1) if session_match else None
    cleaned = re.sub(r"(?mi)^session_id:\s*\S+\s*$", "", output)
    cleaned = re.sub(r"(?m)^↻ Resumed session .*$", "", cleaned)
    cleaned = re.sub(r"(?m)^Resumed session .*$", "", cleaned)
    return _clean_cli_output(cleaned), hermes_session_id


def _call_hermes_prompt(
    session: dict,
    prompt: str,
    *,
    request_id: str | None = None,
) -> tuple[str, str | None]:
    """Call Hermes CLI subprocess with an already-assembled text prompt."""
    if request_id:
        state = _read_request_control(request_id)
        if state and state.get("cancel_requested_at"):
            _update_chat_request(request_id, status="cancelled")
            raise ChatRequestCancelled("Request cancelled before Hermes started")
    cmd = [str(_selected_hermes_bin()), "chat", "-Q"]
    if session.get("hermes_session_id"):
        cmd.extend(["--resume", session["hermes_session_id"]])
    cmd.extend(["-q", prompt])
    try:
        output_path = _request_output_path(request_id) if request_id else None
        if output_path:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass
        output_handle = output_path.open("w", encoding="utf-8") if output_path else None
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path.home()),
            env={**os.environ, "NO_COLOR": "1", "HERMES_HOME": str(_selected_hermes_home())},
            stdout=output_handle if output_handle is not None else subprocess.PIPE,
            stderr=subprocess.STDOUT if output_handle is not None else subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if request_id:
            _update_chat_request(request_id, pid=proc.pid, pgid=os.getpgid(proc.pid))

        last_activity_at = time.time()
        last_output_size = 0
        while True:
            if request_id:
                state = _read_request_control(request_id)
                if state and state.get("cancel_requested_at"):
                    pgid = os.getpgid(proc.pid)
                    _terminate_chat_process(proc.pid, pgid, signal.SIGTERM)
                    try:
                        stdout, stderr = proc.communicate(timeout=CHAT_CANCEL_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        _terminate_chat_process(proc.pid, pgid, signal.SIGKILL)
                        stdout, stderr = proc.communicate()
                    if output_handle is not None:
                        output_handle.flush()
                    _update_chat_request(request_id, status="cancelled")
                    raise ChatRequestCancelled("Request cancelled")
            if output_path and output_path.exists():
                try:
                    current_size = output_path.stat().st_size
                    if current_size > last_output_size:
                        last_output_size = current_size
                        last_activity_at = time.time()
                except OSError:
                    pass
            remaining = CHAT_REQUEST_TIMEOUT - (time.time() - last_activity_at)
            if remaining <= 0:
                _terminate_chat_process(proc.pid, os.getpgid(proc.pid), signal.SIGKILL)
                proc.communicate()
                raise subprocess.TimeoutExpired(proc.args, CHAT_REQUEST_TIMEOUT)
            try:
                stdout, stderr = proc.communicate(timeout=min(CHAT_CANCEL_POLL_INTERVAL, remaining))
                break
            except subprocess.TimeoutExpired:
                if output_handle is not None:
                    output_handle.flush()
                if output_path and output_path.exists():
                    try:
                        current_size = output_path.stat().st_size
                        if current_size > last_output_size:
                            last_output_size = current_size
                            last_activity_at = time.time()
                    except OSError:
                        pass
                continue

        if output_handle is not None:
            output_handle.flush()

        if request_id:
            state = _read_request_control(request_id)
            if state and state.get("cancel_requested_at"):
                _update_chat_request(request_id, status="cancelled")
                raise ChatRequestCancelled("Request cancelled")

        if proc.returncode != 0:
            if output_path and output_path.exists():
                error_output = output_path.read_text(encoding="utf-8", errors="replace").strip()
            else:
                error_output = (stderr or "").strip() or (stdout or "").strip()
            error_output = error_output or f"Hermes CLI exited with status {proc.returncode}"
            raise ChatBackendError(error_output)

        if output_path and output_path.exists():
            output = output_path.read_text(encoding="utf-8", errors="replace").strip()
        else:
            output = (stdout or "").strip()
        if not output:
            raise ChatBackendError(((stderr or "").strip()) or "Hermes returned an empty response")
        import re as _re
        output = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        output = _re.sub(r'\x1b\].*?\x07', '', output)
        return _parse_hermes_chat_result(output)
    except ChatRequestCancelled:
        raise
    except subprocess.TimeoutExpired:
        raise ChatRequestTimeout(f"Hermes did not produce activity within {CHAT_REQUEST_TIMEOUT} seconds")
    except ChatBackendError:
        raise
    except Exception as e:
        raise ChatBackendError(f"Error calling Hermes: {e}") from e
    finally:
        if 'output_handle' in locals() and output_handle is not None:
            output_handle.close()


def _call_hermes_direct(
    session: dict,
    message: str,
    files: list = None,
    request_id: str | None = None,
    file_display_names: dict | None = None,
) -> tuple[str, str | None]:
    """Call Hermes via CLI subprocess (fallback when API server is unavailable)."""
    prompt, _ = _compose_chat_turn_payload(
        session,
        message,
        files or [],
        image_support=False,
        display_names=file_display_names,
    )
    return _call_hermes_prompt(session, prompt, request_id=request_id)


def _call_api_server(
    session: dict,
    messages: list,
    session_id: str,
    files: list = None,
    prefer_vision: bool = False,
    file_display_names: dict | None = None,
) -> str:
    """Call Hermes via its OpenAI-compatible API server. Handles image files as base64."""
    import base64

    msgs = list(messages)
    use_vision_target = prefer_vision
    if files and msgs and msgs[-1].get("role") == "user":
        text_content, image_files = _compose_chat_turn_payload(
            session,
            msgs[-1].get("content", "") or "",
            files,
            image_support=True,
            display_names=file_display_names,
        )
        if image_files:
            use_vision_target = True
            img_content = []
            if text_content:
                img_content.append({"type": "text", "text": text_content})
            for img in image_files:
                try:
                    with open(img, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    ext = img.suffix.lower().replace(".", "")
                    img_content.append({"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}})
                except Exception:
                    pass
            if img_content:
                msgs[-1] = {"role": "user", "content": img_content}
        else:
            msgs[-1] = {"role": "user", "content": text_content}
    elif msgs and msgs[-1].get("role") == "user":
        text_content, _ = _compose_chat_turn_payload(
            session,
            msgs[-1].get("content", "") or "",
            [],
            image_support=False,
            display_names=file_display_names,
        )
        msgs[-1] = {"role": "user", "content": text_content}
    target = _resolve_api_target(prefer_vision=use_vision_target)
    try:
        return _chat_completion_request(target, msgs)
    except ChatBackendError as exc:
        if use_vision_target or not _chat_backend_error_is_retryable(exc):
            raise
        fallback_target = _resolve_fallback_api_target()
        if (
            not _model_role_enabled("fallback", target=fallback_target)
            or _targets_equivalent(target, fallback_target)
        ):
            raise
        try:
            return _chat_completion_request(fallback_target, msgs)
        except ChatBackendError as fallback_exc:
            primary_detail = _chat_backend_error_detail(exc) or str(exc)
            fallback_detail = _chat_backend_error_detail(fallback_exc) or str(fallback_exc)
            raise ChatBackendError(
                "Primary chat model failed"
                f" ({target.get('model') or 'primary'}): {primary_detail}. "
                "Fallback chat model also failed"
                f" ({fallback_target.get('model') or 'fallback'}): {fallback_detail}",
                status_code=max(
                    int(getattr(exc, "status_code", 502) or 502),
                    int(getattr(fallback_exc, "status_code", 502) or 502),
                ),
            ) from fallback_exc
    except Exception as exc:
        raise ChatBackendError(f"API server error: {exc}") from exc


def _provider_env_api_key(provider: str | None) -> str:
    provider_name = _normalize_provider_type(provider or "")
    env_key = PROVIDER_ENV_KEY_MAP.get(provider_name)
    return _runtime_env_value(env_key, "") if env_key else ""


def _resolved_target_api_key(target: dict | None) -> str:
    target = target or {}
    explicit_api_key = _resolve_runtime_template((target.get("api_key") or "").strip()).strip()
    if explicit_api_key:
        return explicit_api_key
    provider_api_key = _provider_env_api_key(target.get("provider"))
    if provider_api_key:
        return provider_api_key
    target_port = _api_url_port(target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL))
    repo_env = _repo_env_values()
    return (
        next((str(repo_env.get(key) or "").strip() for key in _api_token_repo_keys_for_port(target_port) if str(repo_env.get(key) or "").strip()), "")
        or str(os.environ.get("HERMES_API_KEY") or "").strip()
        or str(os.environ.get("HERMES_API_TOKEN") or "").strip()
        or str(os.environ.get("API_SERVER_KEY") or "").strip()
        or str(os.environ.get("API_SERVER_TOKEN") or "").strip()
    )


def _api_server_headers(api_key: str | None = None, provider: str | None = None, target: dict | None = None) -> dict:
    headers = {}
    resolved_api_key = (api_key or "").strip() if api_key is not None else ""
    if not resolved_api_key and provider:
        resolved_api_key = _provider_env_api_key(provider)
    if not resolved_api_key:
        resolved_api_key = _resolved_target_api_key(target)
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    return headers


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Write dotenv entries without forcing single-quoted values."""
    set_key(str(path), key, value, quote_mode="never")


def _resolve_api_target(prefer_vision: bool = False) -> dict:
    return _resolve_role_target("vision" if prefer_vision else "primary")


def _resolve_fallback_api_target() -> dict:
    return _resolve_role_target("fallback")


def _api_target_missing_credentials(target: dict | None) -> bool:
    import urllib.parse

    target = target or {}
    base_url = str(target.get("base_url") or "").strip()
    if not base_url:
        return False
    parsed = urllib.parse.urlparse(base_url)
    hostname = (parsed.hostname or "").strip().lower()
    if hostname in {"", "localhost", "127.0.0.1", "::1"}:
        return False
    return not bool(_resolved_target_api_key(target))


def _openrouter_model_supports_images(target: dict, timeout: int = 3) -> tuple[bool | None, str]:
    import urllib.request

    if (target.get("provider") or "").strip().lower() != "openrouter":
        return None, ""
    model_id = (target.get("model") or "").strip()
    if not model_id:
        return None, ""

    req = urllib.request.Request(
        _build_openai_api_url(target.get("base_url") or "", "models"),
        headers=_api_server_headers(target.get("api_key"), target.get("provider")),
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:
        return None, f"Could not verify image support for {model_id}: {exc}"

    for item in payload.get("data", []) or []:
        if item.get("id") != model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        if "image" in input_modalities:
            return True, ""
        modality = str(architecture.get("modality") or "")
        if "image" in modality.lower():
            return True, ""
        return False, f"The configured OpenRouter model {model_id} does not advertise image input support"

    return None, f"Could not find model metadata for {model_id} on OpenRouter"


def _openrouter_fetch_json(path: str, timeout: int = 10) -> dict:
    import urllib.request

    req = urllib.request.Request(
        _build_openai_api_url("https://openrouter.ai/api/v1", path),
        headers={"User-Agent": "hermes-web-ui"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _openrouter_discovery_models(*, vision_only: bool = False, timeout: int = 10) -> list[dict]:
    payload = _openrouter_fetch_json("models", timeout=timeout)
    models = []
    for item in payload.get("data", []) or []:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        architecture = item.get("architecture") or {}
        input_modalities = architecture.get("input_modalities") or []
        modality = str(architecture.get("modality") or "")
        supports_image = "image" in input_modalities or "image" in modality.lower()
        if vision_only and not supports_image:
            continue
        models.append({
            "id": model_id,
            "name": str(item.get("name") or model_id).strip(),
            "description": str(item.get("description") or "").strip(),
            "supports_image": supports_image,
            "input_modalities": input_modalities,
        })
    return sorted(models, key=lambda item: item.get("id", ""))


def _openrouter_discovery_endpoints(model_id: str, timeout: int = 10) -> list[dict]:
    import urllib.parse

    # OpenRouter expects slash-delimited model ids in the path here.
    encoded_model = urllib.parse.quote(str(model_id or "").strip(), safe="/")
    payload = _openrouter_fetch_json(f"models/{encoded_model}/endpoints", timeout=timeout)
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    endpoints = data.get("endpoints", []) if isinstance(data, dict) else []
    normalized = []
    for endpoint in endpoints or []:
        normalized.append({
            "provider_name": str(endpoint.get("provider_name") or endpoint.get("name") or "").strip(),
            "tag": str(endpoint.get("tag") or "").strip(),
            "status": endpoint.get("status"),
            "uptime_last_30m": endpoint.get("uptime_last_30m"),
            "context_length": endpoint.get("context_length"),
        })
    return normalized


def _summarize_upstream_error_detail(raw_body: str, fallback: str = "") -> str:
    detail = (raw_body or "").strip()
    if not detail:
        return fallback or "upstream request failed"
    try:
        payload = json.loads(detail)
    except Exception:
        return detail

    if isinstance(payload, dict):
        error_payload = payload.get("error", payload)
        if isinstance(error_payload, dict):
            metadata = error_payload.get("metadata") if isinstance(error_payload.get("metadata"), dict) else {}
            metadata_raw = str(metadata.get("raw") or "").strip()
            message = str(error_payload.get("message") or "").strip()
            code = str(error_payload.get("code") or "").strip()
            if metadata_raw:
                return metadata_raw
            if message and code and message.lower() != code.lower():
                return f"{message} ({code})"
            if message:
                return message
            if code:
                return code
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return detail


def _estimate_base64_decoded_size(payload: str) -> int:
    cleaned = "".join(str(payload).split())
    if not cleaned:
        return 0
    if len(cleaned) % 4 != 0:
        raise ValueError("Invalid base64 length")
    padding = len(cleaned) - len(cleaned.rstrip("="))
    return (len(cleaned) * 3) // 4 - padding


def _save_upload_stream(file_storage, destination: Path) -> int:
    temp_path = destination.with_name(f".{destination.name}.part")
    total = 0
    stream = getattr(file_storage, "stream", file_storage)
    if hasattr(stream, "seek"):
        stream.seek(0)
    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = stream.read(UPLOAD_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_SIZE:
                    raise RequestEntityTooLarge()
                handle.write(chunk)
        temp_path.replace(destination)
        return total
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _build_openai_api_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    clean_path = path.lstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{clean_path}"
    return f"{base}/v1/{clean_path}"


def _api_server_probe(timeout: int = 3, prefer_vision: bool = False) -> tuple[bool, str, dict | None]:
    import urllib.request
    import urllib.error

    target = _resolve_api_target(prefer_vision=prefer_vision)
    base_url = (target.get("base_url") or "").strip()
    if not base_url:
        return False, "API base URL is not configured", None

    headers = _api_server_headers(target.get("api_key"), target.get("provider"))
    probes = [
        ("health", f"{base_url.rstrip('/')}/health"),
        ("models", _build_openai_api_url(base_url, "models")),
    ]
    last_error = "API endpoint did not respond"

    for probe_name, url in probes:
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True, "", {"probe": probe_name, "url": url, "status": resp.status}
                last_error = f"{probe_name} probe returned HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            if probe_name == "health" and exc.code == 404:
                last_error = "health probe returned HTTP 404"
                continue
            last_error = f"{probe_name} probe returned HTTP {exc.code}"
        except Exception as exc:
            last_error = f"{probe_name} probe failed: {exc}"

    return False, last_error, None


def _check_api_server() -> bool:
    """Check if the configured API target is reachable."""
    try:
        return _api_server_healthcheck()
    except Exception:
        return False


def _api_server_healthcheck(timeout: int = 3) -> bool:
    ok, _, _ = _api_server_probe(timeout=timeout)
    return ok


def _vision_configured() -> tuple[bool, str]:
    model_cfg = _normalized_model_config()
    vision_cfg = model_cfg.get("vision")
    if isinstance(vision_cfg, str):
        if vision_cfg.strip():
            return True, ""
    elif isinstance(vision_cfg, dict):
        provider = (vision_cfg.get("provider") or "").strip()
        model = (vision_cfg.get("model") or "").strip()
        base_url = (vision_cfg.get("base_url") or "").strip()
        if model or base_url or (provider and provider.lower() != "auto"):
            return True, ""
    return False, "Hermes vision is not configured"


def _image_attachment_support_status() -> tuple[bool, str]:
    vision_ready, vision_reason = _vision_configured()
    if not vision_ready:
        return False, vision_reason
    target = _resolve_api_target(prefer_vision=True)
    if _api_target_missing_credentials(target):
        api_url = target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
        return False, f"Vision API key is missing for remote endpoint {api_url}"
    api_ok, api_reason, _ = _api_server_probe(timeout=2, prefer_vision=True)
    if api_ok:
        image_model_ok, image_model_reason = _openrouter_model_supports_images(target, timeout=3)
        if image_model_ok is False:
            return False, image_model_reason
        return True, ""
    api_url = target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL)
    return False, f"OpenAI-compatible vision sidecar API is not reachable at {api_url} ({api_reason})"


def _get_or_create_chat_session(session_id=None, profile_name=None):
    existing = _load_session(session_id) if session_id else None
    if existing:
        return _normalize_chat_session(existing)
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    selected_profile = _normalize_hermes_profile_name(profile_name) or _selected_hermes_profile_name()
    session = {
        "id": session_id,
        "messages": [],
        "created": now,
        "title": "New Chat",
        "updated": now,
        "profile": selected_profile,
        "segments": [{
            "id": "segment-1",
            "index": 1,
            "profile": selected_profile,
            "transport": "",
            "hermes_session_id": None,
            "started_at": now,
            "start_message_index": 0,
        }],
        "active_segment_id": "segment-1",
        "hermes_session_id": None,
        "transport_mode": None,
        "transport_preference": None,
        "continuity_mode": None,
        "transport_notice": "",
        "folder_id": "",
        "workspace_roots": [],
        "source_docs": [],
    }
    session = _normalize_chat_session(session)
    _write_session(session)
    return copy.deepcopy(session)


def _rollback_failed_chat_turn(session: dict, session_id: str, user_msg: dict) -> None:
    if session.get("messages") and session["messages"][-1] == user_msg:
        session["messages"].pop()
    if not session.get("messages"):
        _delete_session_from_disk(session_id)
        return
    session["updated"] = datetime.now().isoformat()
    _write_session(session)


@app.route("/api/chat", methods=["POST"])
@require_token
@rate_limit
def api_chat():
    chat_started_at = time.monotonic()
    data = request.get_json(silent=True) or {}
    message = data.get("message", "").strip()
    session_id = data.get("session_id")
    requested_profile = _normalize_hermes_profile_name(data.get("profile") or "")
    if requested_profile and requested_profile not in _available_hermes_profile_names():
        return jsonify({"error": "Invalid profile"}), 400
    requested_transport_preference = _normalize_transport_preference(data.get("transport_preference"))
    requested_folder_id = str(data.get("folder_id") or "").strip()
    request_id = (data.get("request_id") or str(uuid.uuid4())).strip()
    files = []
    file_display_names = {}
    for ref in (data.get("files") or []):
        stored_as = None
        display_name = None
        if isinstance(ref, str):
            stored_as = ref
            display_name = Path(ref).name
        elif isinstance(ref, dict):
            stored_as = str(ref.get("stored_as") or "").strip()
            display_name = str(ref.get("name") or "").strip() or None
        if not stored_as:
            continue
        fpath = UPLOAD_FOLDER / stored_as
        if fpath.exists():
            files.append(fpath)
            if display_name:
                file_display_names[fpath.name] = display_name
    if not message and not files:
        return jsonify({"error": "Message or attachment is required"}), 400
    sess = _get_or_create_chat_session(session_id, profile_name=requested_profile)
    sess["profile"] = _normalize_hermes_profile_name(sess.get("profile")) or _selected_hermes_profile_name()
    if data.get("transport_preference") is not None:
        validated_preference, preference_notice = _validated_transport_preference(requested_transport_preference)
        sess["transport_preference"] = validated_preference
        sess["transport_notice"] = preference_notice or ""
    if requested_folder_id:
        ensured = _ensure_folder_exists(requested_folder_id)
        requested_folder_id = ensured["id"] if ensured else requested_folder_id
    if requested_folder_id and not sess.get("folder_id"):
        sess["folder_id"] = requested_folder_id
    sid = sess["id"]
    with _scoped_profile_override(sess.get("profile")):
        request_plan = _plan_chat_request(sess, files)
        active_segment = _append_chat_segment(sess, sess["profile"], transport=request_plan["transport"])
        sess["hermes_session_id"] = _segment_hermes_session_id(active_segment)
        attachment_errors = _validate_attachment_selection(files, request_plan["image_support"])
    if attachment_errors:
        return jsonify({"error": "Unsupported attachment selection", "details": attachment_errors}), 400

    logger.info(
        "Chat request started request_id=%s session_id=%s transport=%s files=%s folder_id=%s",
        request_id,
        sid,
        request_plan["transport"],
        len(files),
        sess.get("folder_id") or "",
    )

    _register_chat_request(
        request_id,
        sid,
        transport=request_plan["transport"],
        cancel_supported=request_plan["cancel_supported"],
    )
    user_msg = {
        "role": "user",
        "content": message,
        "files": [_attachment_display_name(f, file_display_names) for f in files],
        "attachment_refs": _build_attachment_refs(files, file_display_names),
        "timestamp": datetime.now().isoformat(),
        "segment_id": active_segment.get("id"),
        "segment_index": active_segment.get("index"),
        "profile": active_segment.get("profile") or sess.get("profile"),
        "transport": request_plan["transport"],
    }
    sess["messages"].append(user_msg)
    # Auto-title from first user message
    if len(sess["messages"]) == 1 and sess.get("title") == "New Chat":
        if message:
            sess["title"] = message[:60] + ("..." if len(message) > 60 else "")
        elif files:
            file_label = ", ".join(f.name for f in files[:2])
            if len(files) > 2:
                file_label += f" +{len(files) - 2} more"
            sess["title"] = f"Files: {file_label}"
    sess["updated"] = datetime.now().isoformat()
    _write_session(sess)
    if sess.get("messages"):
        user_msg = sess["messages"][-1]
    try:
        with _scoped_profile_override(sess.get("profile")):
            use_api_server = request_plan["transport"] == CHAT_TRANSPORT_API
            if use_api_server:
                if sess.get("transport_mode") != CHAT_TRANSPORT_API:
                    sess["transport_mode"] = CHAT_TRANSPORT_API
                    sess["continuity_mode"] = CHAT_CONTINUITY_LOCAL
                    sess["transport_notice"] = request_plan["transport_notice"]
                    sess["hermes_session_id"] = None
                api_msgs = []
                for m in _messages_for_active_segment(sess):
                    msg = {"role": m["role"], "content": m["content"]}
                    if m.get("files"):
                        msg["files"] = m["files"]
                    api_msgs.append(msg)
                response_text = _call_api_server(
                    sess,
                    api_msgs,
                    sid,
                    files,
                    prefer_vision=_active_segment_has_image_history(sess) or any(
                        f.suffix.lower() in IMAGE_EXTENSIONS for f in files
                    ),
                    file_display_names=file_display_names,
                )
            else:
                sidecar_result = {}
                if any(f.suffix.lower() in IMAGE_EXTENSIONS for f in files) or _vision_reanalysis_requested(message, sess):
                    sidecar_result = _run_sidecar_vision_analysis(
                        sess,
                        message,
                        files,
                        user_message=user_msg,
                        file_display_names=file_display_names,
                    )
                if sidecar_result:
                    prompt = _compose_cli_prompt_with_sidecar(
                        sess,
                        message,
                        files,
                        sidecar_result=sidecar_result,
                        file_display_names=file_display_names,
                    )
                    response_text, hermes_session_id = _call_hermes_prompt(
                        sess,
                        prompt,
                        request_id=request_id,
                    )
                else:
                    response_text, hermes_session_id = _call_hermes_direct(
                        sess,
                        message,
                        files,
                        request_id=request_id,
                        file_display_names=file_display_names,
                    )
                sess["transport_mode"] = CHAT_TRANSPORT_CLI
                effective_hermes_session_id = _clean_hermes_session_id(hermes_session_id) or _segment_hermes_session_id(active_segment)
                if effective_hermes_session_id:
                    active_segment["hermes_session_id"] = effective_hermes_session_id
                    sess["hermes_session_id"] = effective_hermes_session_id
                    sess["continuity_mode"] = CHAT_CONTINUITY_HERMES
                    sess["transport_notice"] = ""
                else:
                    active_segment["hermes_session_id"] = None
                    sess["hermes_session_id"] = None
                    sess["continuity_mode"] = CHAT_CONTINUITY_LIMITED
                    sess["transport_notice"] = (
                        "Hermes CLI did not return a resumable session id for this chat yet. "
                        "Follow-up turns may not preserve Hermes-side context."
                    )
        _update_chat_request(request_id, status="completed")
    except ChatRequestCancelled:
        _rollback_failed_chat_turn(sess, sid, user_msg)
        _update_chat_request(request_id, status="cancelled")
        logger.info(
            "Chat request cancelled request_id=%s session_id=%s duration_ms=%s",
            request_id,
            sid,
            int((time.monotonic() - chat_started_at) * 1000),
        )
        return jsonify({"ok": False, "cancelled": True, "session_id": sid}), 499
    except ChatBackendError as exc:
        _rollback_failed_chat_turn(sess, sid, user_msg)
        _update_chat_request(request_id, status="failed", error=str(exc))
        logger.warning(
            "Chat request failed request_id=%s session_id=%s duration_ms=%s detail=%s",
            request_id,
            sid,
            int((time.monotonic() - chat_started_at) * 1000),
            exc,
        )
        return jsonify({"error": str(exc), "request_id": request_id, "session_id": sid}), exc.status_code
    except Exception as exc:
        _rollback_failed_chat_turn(sess, sid, user_msg)
        _update_chat_request(request_id, status="failed", error=str(exc))
        logger.exception(
            "Unexpected chat request failure request_id=%s session_id=%s duration_ms=%s",
            request_id,
            sid,
            int((time.monotonic() - chat_started_at) * 1000),
        )
        return jsonify({"error": f"Unexpected chat error: {exc}", "request_id": request_id, "session_id": sid}), 500
    finally:
        _remove_chat_request(request_id)
    assistant_msg = {"role": "assistant", "content": response_text,
                     "timestamp": datetime.now().isoformat(),
                     "segment_id": active_segment.get("id"),
                     "segment_index": active_segment.get("index"),
                     "profile": active_segment.get("profile") or sess.get("profile"),
                     "transport": sess.get("transport_mode") or request_plan["transport"]}
    sess["messages"].append(assistant_msg)
    sess["updated"] = datetime.now().isoformat()
    _write_session(sess)
    logger.info(
        "Chat request completed request_id=%s session_id=%s duration_ms=%s response_chars=%s transport=%s",
        request_id,
        sid,
        int((time.monotonic() - chat_started_at) * 1000),
        len(response_text),
        sess.get("transport_mode"),
    )
    session_meta = _chat_session_meta(sess)
    return jsonify({"session_id": sid, "response": response_text,
                     "message_count": len(sess["messages"]), "title": sess.get("title", ""),
                     "cancel_supported": request_plan["cancel_supported"],
                     "session": session_meta,
                     "user_message": user_msg,
                     "assistant_message": assistant_msg})


@app.route("/api/chat/cancel", methods=["POST"])
@require_token
@rate_limit
def api_chat_cancel():
    data = request.get_json(silent=True) or {}
    request_id = (data.get("request_id") or "").strip()
    if not request_id:
        return jsonify({"error": "request_id is required"}), 400
    cancelled, detail = _cancel_chat_request(request_id)
    status_code = 200 if cancelled else 409
    if detail == "Request not found":
        status_code = 404
    return jsonify({"cancelled": cancelled, "detail": detail, "request_id": request_id}), status_code


@app.route("/api/upload", methods=["POST"])
@require_token
@rate_limit
def api_upload():
    if request.content_length and request.content_length > MAX_REQUEST_BODY_SIZE:
        raise RequestEntityTooLarge()
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No filename"}), 400
    safe = secure_filename(f.filename) or "file"
    unique = f"{uuid.uuid4().hex[:8]}_{safe}"
    target = UPLOAD_FOLDER / unique
    try:
        size = _save_upload_stream(f, target)
    except RequestEntityTooLarge:
        logger.warning(
            "Rejected oversized multipart upload request_id=%s name=%s content_length=%s remote=%s",
            _request_id_or_dash(),
            safe,
            request.content_length,
            request.remote_addr,
        )
        raise
    logger.info(
        "Stored upload request_id=%s stored_as=%s name=%s size=%s type=%s remote=%s",
        _request_id_or_dash(),
        unique,
        safe,
        size,
        f.content_type,
        request.remote_addr,
    )
    return jsonify({"name": safe, "stored_as": unique, "size": size,
                     "type": f.content_type, "url": f"/uploads/{unique}"})


@app.route("/api/upload/base64", methods=["POST"])
@require_token
@rate_limit
def api_upload_base64():
    """Accept a base64-encoded image (from clipboard paste) and save it."""
    if request.content_length and request.content_length > MAX_REQUEST_BODY_SIZE:
        raise RequestEntityTooLarge()
    data = request.get_json(silent=True) or {}
    b64 = data.get("data", "")
    if not b64:
        return jsonify({"error": "No data"}), 400
    # Strip data URL prefix if present
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        estimated_size = _estimate_base64_decoded_size(b64)
    except ValueError:
        return jsonify({"error": "Invalid base64"}), 400
    if estimated_size > MAX_UPLOAD_SIZE:
        logger.warning(
            "Rejected oversized base64 upload request_id=%s estimated_size=%s remote=%s",
            _request_id_or_dash(),
            estimated_size,
            request.remote_addr,
        )
        return jsonify({"error": f"Too large (max {MAX_UPLOAD_SIZE//(1024*1024)}MB)"}), 400
    try:
        import base64
        img_bytes = base64.b64decode(b64, validate=True)
    except Exception:
        return jsonify({"error": "Invalid base64"}), 400
    if len(img_bytes) > MAX_UPLOAD_SIZE:
        return jsonify({"error": f"Too large (max {MAX_UPLOAD_SIZE//(1024*1024)}MB)"}), 400
    ext = secure_filename(str(data.get("ext", "png"))).lower().lstrip(".") or "png"
    if ext not in {"png", "jpg", "jpeg", "webp", "gif"}:
        ext = "png"
    unique = f"{uuid.uuid4().hex[:8]}_clipboard.{ext}"
    (UPLOAD_FOLDER / unique).write_bytes(img_bytes)
    logger.info(
        "Stored base64 upload request_id=%s stored_as=%s size=%s type=image/%s remote=%s",
        _request_id_or_dash(),
        unique,
        len(img_bytes),
        "jpeg" if ext == "jpg" else ext,
        request.remote_addr,
    )
    return jsonify({"name": f"clipboard.{ext}", "stored_as": unique,
                     "size": len(img_bytes), "type": f"image/{'jpeg' if ext == 'jpg' else ext}",
                     "url": f"/uploads/{unique}"})


@app.route("/uploads/<path:filename>")
@require_token
def serve_upload(filename):
    return send_from_directory(str(UPLOAD_FOLDER), filename)


@app.route("/api/chat/sessions", methods=["GET"])
@require_token
def api_chat_sessions():
    sessions = []
    for sid, s in _load_all_sessions().items():
        meta = _chat_session_meta(s)
        sessions.append({
            "id": s["id"],
            "title": s.get("title", "Untitled"),
            "message_count": len(s["messages"]),
            "created": s["created"],
            "updated": s.get("updated", s["created"]),
            "last_message": s["messages"][-1]["content"][:100] if s["messages"] else "",
            "session": meta,
        })
    # Sort by updated desc
    sessions.sort(key=lambda x: x.get("updated", ""), reverse=True)
    return jsonify({"sessions": sessions})


@app.route("/api/chat/folders", methods=["GET"])
@require_token
def api_chat_folders():
    sessions = _load_all_sessions()
    return jsonify({"folders": _folder_summaries(sessions)})


@app.route("/api/chat/folders", methods=["POST"])
@require_token
def api_chat_folders_create():
    data = request.get_json() or {}
    folder_payload, errors = _parse_folder_update(data)
    if errors:
        return jsonify({"error": "Invalid folder", "details": errors}), 400
    existing = _folder_title_conflict(folder_payload["title"])
    if existing:
        return jsonify({"error": "Folder name already exists", "folder": existing}), 409
    sessions = _load_all_sessions()
    legacy = _legacy_folder_from_sessions(folder_payload["title"], sessions)
    if legacy:
        folder = _write_folder({
            "id": legacy["id"],
            "title": folder_payload["title"],
            "created": legacy.get("created"),
            "updated": datetime.now().isoformat(),
            "workspace_roots": folder_payload["workspace_roots"] or legacy.get("workspace_roots") or [],
            "source_docs": folder_payload["source_docs"] or legacy.get("source_docs") or [],
        })
        summary = next((item for item in _folder_summaries(sessions) if item["id"] == folder["id"]), None)
        return jsonify({"ok": True, "folder": summary or folder})
    now = datetime.now().isoformat()
    folder = _write_folder({
        "id": str(uuid.uuid4())[:8],
        "title": folder_payload["title"],
        "created": now,
        "updated": now,
        "workspace_roots": folder_payload["workspace_roots"],
        "source_docs": folder_payload["source_docs"],
    })
    return jsonify({"ok": True, "folder": folder})


@app.route("/api/chat/folders/<folder_id>", methods=["GET"])
@require_token
def api_chat_folder_get(folder_id):
    folder = _folder_with_fallback(folder_id)
    if not folder:
        return jsonify({"error": "Folder not found"}), 404
    sessions = _load_all_sessions()
    summary = next((item for item in _folder_summaries(sessions) if item["id"] == folder["id"]), None)
    return jsonify({"folder": summary or folder})


@app.route("/api/chat/folders/<folder_id>", methods=["PUT"])
@require_token
def api_chat_folder_update(folder_id):
    existing = _folder_with_fallback(folder_id)
    if not existing:
        return jsonify({"error": "Folder not found"}), 404
    folder_payload, errors = _parse_folder_update(request.get_json() or {}, existing=existing)
    if errors:
        return jsonify({"error": "Invalid folder", "details": errors}), 400
    conflict = _folder_title_conflict(folder_payload["title"], exclude_folder_id=existing["id"])
    if conflict:
        return jsonify({"error": "Folder name already exists", "folder": conflict}), 409
    folder = _write_folder({
        "id": existing["id"],
        "title": folder_payload["title"],
        "created": existing.get("created"),
        "updated": datetime.now().isoformat(),
        "workspace_roots": folder_payload["workspace_roots"],
        "source_docs": folder_payload["source_docs"],
    })
    summary = next((item for item in _folder_summaries() if item["id"] == folder["id"]), None)
    return jsonify({"ok": True, "folder": summary or folder})


@app.route("/api/chat/folders/<folder_id>", methods=["DELETE"])
@require_token
def api_chat_folder_delete(folder_id):
    sessions = _load_all_sessions()
    folder = _folder_with_fallback(folder_id, sessions)
    if not folder:
        return jsonify({"error": "Folder not found"}), 404

    moved_session_ids = []
    now = datetime.now().isoformat()
    folders = _load_all_folders()
    for session in sessions.values():
        session_folder = _resolve_folder_reference(session.get("folder_id"), sessions=sessions, folders=folders, include_legacy=False)
        if not session_folder or session_folder["id"] != folder["id"]:
            continue
        session["folder_id"] = ""
        session["updated"] = now
        _write_session(session)
        moved_session_ids.append(session["id"])

    _delete_folder(folder_id)
    return jsonify({
        "ok": True,
        "deleted_folder_id": folder_id,
        "moved_session_count": len(moved_session_ids),
        "moved_session_ids": moved_session_ids,
    })


@app.route("/api/chat/folders/<folder_id>/sources/from-chat", methods=["POST"])
@require_token
def api_chat_folder_source_from_chat(folder_id):
    folder = _folder_with_fallback(folder_id)
    if not folder:
        return jsonify({"error": "Folder not found"}), 404
    data = request.get_json() or {}
    session_id = str(data.get("session_id") or "").strip()
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    lines = [
        f"# {session.get('title') or 'Chat'}",
        "",
        f"Session ID: {session.get('id')}",
        "",
    ]
    for message in session.get("messages", []):
        role = "User" if message.get("role") == "user" else "Hermes"
        lines.append(f"## {role}")
        lines.append("")
        lines.append(message.get("content") or "")
        if message.get("files"):
            lines.append("")
            lines.append("Attachments: " + ", ".join(message.get("files") or []))
        lines.append("")
    safe_name = secure_filename(session.get("title") or session.get("id") or "chat-source") or "chat-source"
    target_path = CHAT_FOLDER_SOURCE_DIR / f"{folder_id}_{session.get('id')}_{safe_name}.md"
    target_path.write_text("\n".join(lines), encoding="utf-8")
    updated_sources = _merge_unique_strings((folder.get("source_docs") or []), [str(target_path.resolve())])
    updated_workspace_roots = _merge_unique_strings(
        folder.get("workspace_roots") or [],
        _folder_workspace_roots_for_docs(updated_sources),
    )
    stored = _write_folder({
        "id": folder["id"],
        "title": folder["title"],
        "created": folder.get("created"),
        "updated": datetime.now().isoformat(),
        "source_docs": updated_sources,
        "workspace_roots": updated_workspace_roots,
    })
    summary = next((item for item in _folder_summaries() if item["id"] == stored["id"]), None)
    return jsonify({"ok": True, "folder": summary or stored, "source_path": str(target_path.resolve())})


@app.route("/api/chat/sessions", methods=["POST"])
@require_token
def api_chat_sessions_create():
    data = request.get_json() or {}
    requested_profile = _normalize_hermes_profile_name(data.get("profile") or "")
    if requested_profile and requested_profile not in _available_hermes_profile_names():
        return jsonify({"error": "Invalid profile"}), 400
    session = _get_or_create_chat_session(profile_name=requested_profile)
    context_update, errors = _parse_chat_context_update(data)
    transport_preference, transport_notice = _validated_transport_preference(data.get("transport_preference"))
    folder_id = context_update.get("folder_id") or ""
    if folder_id:
        ensured = _ensure_folder_exists(folder_id)
        context_update["folder_id"] = ensured["id"] if ensured else folder_id
    if errors:
        _delete_session_from_disk(session["id"])
        return jsonify({"error": "Invalid chat context", "details": errors}), 400
    session.update(context_update)
    session["transport_preference"] = transport_preference
    if transport_notice:
        session["transport_notice"] = transport_notice
    session["updated"] = datetime.now().isoformat()
    _write_session(session)
    return jsonify({
        "ok": True,
        "session_id": session["id"],
        "title": session.get("title", ""),
        "session": _chat_session_meta(session),
    })


@app.route("/api/chat/sessions/<session_id>/messages", methods=["GET"])
@require_token
def api_chat_messages(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    if _trim_trailing_empty_chat_segments(session):
        _write_session(session)
        session = _load_session(session_id) or session
    return jsonify({"messages": session["messages"],
                     "title": session.get("title", ""),
                     "session": _chat_session_meta(session)})


@app.route("/api/chat/sessions/<session_id>/rename", methods=["POST"])
@require_token
def api_chat_rename(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    new_title = data.get("title", "").strip()
    if new_title:
        session["title"] = new_title
        session["updated"] = datetime.now().isoformat()
        _write_session(session)
    return jsonify({"ok": True, "title": session.get("title", "")})


@app.route("/api/chat/sessions/<session_id>/context", methods=["PUT"])
@require_token
def api_chat_context_update(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    context_update, errors = _parse_chat_context_update(request.get_json() or {})
    folder_id = context_update.get("folder_id") or ""
    if folder_id:
        ensured = _ensure_folder_exists(folder_id)
        context_update["folder_id"] = ensured["id"] if ensured else folder_id
    if errors:
        return jsonify({"error": "Invalid chat context", "details": errors}), 400
    session.update(context_update)
    session["updated"] = datetime.now().isoformat()
    _write_session(session)
    return jsonify({"ok": True, "session": _chat_session_meta(session)})


@app.route("/api/chat/sessions/<session_id>/transport", methods=["PUT"])
@require_token
def api_chat_session_transport_update(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    requested = str(data.get("transport_preference") or "").strip().lower()
    if requested not in ("", CHAT_TRANSPORT_AUTO, CHAT_TRANSPORT_CLI, CHAT_TRANSPORT_API):
        return jsonify({"error": "Invalid transport preference"}), 400
    session["transport_preference"], session["transport_notice"] = _validated_transport_preference(requested)
    session["updated"] = datetime.now().isoformat()
    _write_session(session)
    return jsonify({"ok": True, "session": _chat_session_meta(session)})


@app.route("/api/chat/sessions/<session_id>/profile", methods=["PUT"])
@require_token
def api_chat_session_profile_update(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    requested_profile = _normalize_hermes_profile_name(data.get("profile"))
    if requested_profile not in _available_hermes_profile_names():
        return jsonify({"error": "Invalid profile"}), 400
    selected = requested_profile
    segment = _append_chat_segment(session, selected)
    session["profile"] = selected
    session["transport_mode"] = None
    session["continuity_mode"] = None
    session["hermes_session_id"] = _segment_hermes_session_id(segment)
    session["transport_notice"] = (
        f"Switched to Hermes profile {selected}. "
        "Next messages in this chat will use that profile."
    )
    session["updated"] = datetime.now().isoformat()
    _write_session(session)
    return jsonify({"ok": True, "selected": selected, "session": _chat_session_meta(session)})


@app.route("/api/chat/sessions/<session_id>/folder", methods=["PUT"])
@require_token
def api_chat_session_folder_update(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    data = request.get_json() or {}
    folder_id = str(data.get("folder_id") or "").strip()
    if folder_id:
        ensured = _ensure_folder_exists(folder_id)
        folder_id = ensured["id"] if ensured else folder_id
    session["folder_id"] = folder_id
    session["updated"] = datetime.now().isoformat()
    _write_session(session)
    return jsonify({"ok": True, "session": _chat_session_meta(session)})


@app.route("/api/chat/sessions/<session_id>/delete", methods=["POST"])
@require_token
def api_chat_delete(session_id):
    if not _load_session(session_id):
        return jsonify({"ok": False, "error": "Session not found"}), 404
    _delete_session_from_disk(session_id)
    return jsonify({"ok": True})


@app.route("/api/chat/sessions/<session_id>/clear", methods=["POST"])
@require_token
def api_chat_clear(session_id):
    session = _load_session(session_id)
    if not session:
        return jsonify({"ok": False, "error": "Session not found"}), 404
    session["messages"] = []
    session["updated"] = datetime.now().isoformat()
    session["hermes_session_id"] = None
    session["transport_mode"] = None
    session["continuity_mode"] = None
    session["transport_notice"] = ""
    _write_session(session)
    return jsonify({"ok": True, "session": _chat_session_meta(session)})


@app.route("/api/chat/status", methods=["GET"])
@require_token
def api_chat_status():
    request_id = str(request.args.get("request_id") or "").strip()
    if request_id:
        payload = _read_request_control(request_id)
        if payload is None:
            return jsonify({"error": "Request not found", "request_id": request_id}), 404
        response = jsonify({
            "request_id": request_id,
            "status": payload.get("status") or "running",
            "transport": payload.get("transport") or "",
            "cancel_supported": bool(payload.get("cancel_supported")),
            "session_id": payload.get("session_id") or "",
            "created_at": payload.get("created_at") or "",
            "updated_at": payload.get("updated_at") or "",
            "progress_lines": _request_progress_lines(request_id),
            "error": payload.get("error") or "",
        })
        response.headers["Cache-Control"] = "no-store, no-cache, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    api_server = _check_api_server()
    default_api_ok, default_api_reason, default_api_probe = _api_server_probe(timeout=2)
    image_support, image_reason = _image_attachment_support_status()
    vision_ready, vision_reason = _vision_configured()
    vision_target = _resolve_api_target(prefer_vision=True)
    runtime = _chat_runtime_status()
    api_selectable = api_server and not runtime.get("requires_cli")
    return jsonify({
        "api_server": api_server,
        "api_url": _effective_hermes_api_url(DEFAULT_HERMES_API_URL),
        "profile": _selected_hermes_profile_name(),
        "api_probe": {
            "reachable": default_api_ok,
            "reason": default_api_reason,
            "probe": default_api_probe,
        },
        "capabilities": {
            "text_attachments": True,
            "image_attachments": image_support,
            "audio_attachments": False,
        },
        "capability_reasons": {
            "image_attachments": image_reason,
        },
        "request_lifecycle": {
            "chat_timeout_seconds": CHAT_REQUEST_TIMEOUT,
            "server_timeout_seconds": CHAT_SERVER_TIMEOUT,
            "cancel_supported": {
                CHAT_TRANSPORT_CLI: True,
                CHAT_TRANSPORT_API: False,
            },
            "continuity": {
                CHAT_TRANSPORT_CLI: CHAT_CONTINUITY_HERMES,
                CHAT_TRANSPORT_API: CHAT_CONTINUITY_LOCAL,
            },
        },
        "limits": {
            "max_upload_bytes": MAX_UPLOAD_SIZE,
            "max_request_body_bytes": MAX_REQUEST_BODY_SIZE,
        },
        "debug": {
            "persist_trace": CHAT_PERSIST_DEBUG_TRACE,
        },
        "transport_policy": {
            "requires_cli": runtime.get("requires_cli"),
            "api_selectable": api_selectable,
            "reason": runtime.get("cli_reason") or "",
            "reasons": runtime.get("reasons") or [],
        },
        "runtime": runtime,
        "readiness": {
            "screenshots_ready": image_support,
            "vision_sidecar_ready": image_support,
            "vision_configured": vision_ready,
            "vision_reason": vision_reason,
            "vision_api_url": vision_target.get("base_url") or _effective_hermes_api_url(DEFAULT_HERMES_API_URL),
            "vision_model": vision_target.get("model") if vision_ready else "",
            "api_reachable": default_api_ok,
            "api_reason": default_api_reason,
        },
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
    print("[Hermes Admin Panel] Use ./start.sh 5000 to run in production, or DEV=1 ./start.sh 5000 for development.")
    exit(1)
