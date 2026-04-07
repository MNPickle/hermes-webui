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
import mimetypes
import subprocess
import signal
import time
import logging
from datetime import datetime
from pathlib import Path
from functools import wraps
from contextlib import contextmanager

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
    if ENV_PATH.exists():
        return {
            key: value for key, value in dotenv_values(str(ENV_PATH)).items()
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
HERMES_API_URL = _runtime_env_value("HERMES_API_URL", "http://127.0.0.1:8642")
BACKUP_DIR = HERMES_HOME / "backups"

# Chat session storage (persisted to disk)
CHAT_DATA_DIR = APP_ROOT / "chat_data"
CHAT_DATA_DIR.mkdir(exist_ok=True)
CHAT_DATA_LOCK = CHAT_DATA_DIR / ".lock"
CHAT_FOLDERS_PATH = CHAT_DATA_DIR / ".folders.json"
CHAT_REQUEST_DIR = APP_ROOT / "run" / "chat_requests"
CHAT_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
CHAT_REQUEST_TIMEOUT = int(_runtime_env_value("HERMES_CHAT_TIMEOUT", "300"))
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

chat_sessions: dict = {}  # runtime cache: sid -> session dict
chat_folders: dict = {}  # runtime cache: folder_id -> folder dict

CHAT_TRANSPORT_CLI = "cli"
CHAT_TRANSPORT_API = "api"
CHAT_CONTINUITY_HERMES = "hermes_resume"
CHAT_CONTINUITY_LOCAL = "local_replay"
CHAT_CONTINUITY_LIMITED = "cli_without_resume"
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


def _normalize_chat_session(session: dict) -> dict:
    transport_mode = session.get("transport_mode")
    continuity_mode = session.get("continuity_mode")
    if transport_mode == CHAT_TRANSPORT_CLI and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_HERMES if session.get("hermes_session_id") else CHAT_CONTINUITY_LIMITED
    elif transport_mode == CHAT_TRANSPORT_API and not continuity_mode:
        continuity_mode = CHAT_CONTINUITY_LOCAL
    folder_id = session.get("folder_id")
    if not isinstance(folder_id, str):
        folder_id = ""
    session.setdefault("transport_mode", transport_mode)
    session.setdefault("continuity_mode", continuity_mode)
    session.setdefault("transport_notice", "")
    session["messages"] = _normalize_chat_messages(session.get("messages"))
    session["vision_assets"] = _normalize_vision_assets(session.get("vision_assets"))
    session["folder_id"] = folder_id.strip()
    session["workspace_roots"] = _clean_string_list(session.get("workspace_roots"))
    session["source_docs"] = _clean_string_list(session.get("source_docs"))
    return session


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

def require_token(f):
    """Decorator to require HERMES_WEBUI_TOKEN for API endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        expected_token = _current_webui_token()
        if not expected_token:
            logger.warning("Authentication not configured - rejecting API request")
            # Fail closed: if no token is configured, deny access
            return jsonify({"ok": False, "error": "API authentication not configured"}), 401
        
        # Check Authorization header for Bearer token
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning("API request missing Authorization header from %s", request.remote_addr)
            return jsonify({"ok": False, "error": "Missing or invalid Authorization header"}), 401
        
        provided_token = auth_header[7:]  # Remove "Bearer " prefix
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
    for aux_key in ("vision", "web_extract", "compression", "session_search",
                    "summarization", "embedding", "tts", "stt"):
        if aux_key not in normalized and aux_key in auxiliary_cfg:
            normalized[aux_key] = copy.deepcopy(auxiliary_cfg.get(aux_key))
    return normalized


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
        current = dotenv_values(str(ENV_PATH)).get(key) if ENV_PATH.exists() else None
        if (
            isinstance(value, str)
            and isinstance(current, str)
            and current
            and value == _mask_value(key, current)
        ):
            return jsonify({"ok": True})
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
    model_cfg = _normalized_model_config()
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
                sanitized = _preserve_masked_secret_updates(p, data)
                custom[i] = ConfigManager.deep_merge(p, sanitized)
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
        model_cfg = _normalized_model_config()
        custom = raw.get("custom_providers", []) or []
        provider_cfg = None
        for p in custom:
            if p.get("name") == name:
                provider_cfg = p
                break

        if not provider_cfg:
            # Maybe it's the default provider
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
                HERMES_HOME / "logs" / "gateway.log",
                HERMES_HOME / "logs" / "errors.log",
                HERMES_HOME / "hermes.log",
                HERMES_HOME / "gateway.log",
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
            env = {**os.environ, "HERMES_HOME": str(HERMES_HOME)}
            pid_file = HERMES_HOME / "gateway.pid"
            log_path = HERMES_HOME / "logs" / "gateway.log"
            log_path.parent.mkdir(exist_ok=True)
            # Kill any existing gateway process first
            _run_hermes("gateway", "stop", timeout=10)
            time.sleep(1)
            with open(log_path, "a") as lf:
                proc = subprocess.Popen(
                    [str(HERMES_BIN), "gateway", "run"],
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
            env = {**os.environ, "HERMES_HOME": str(HERMES_HOME)}
            log_path = HERMES_HOME / "logs" / "gateway.log"
            log_path.parent.mkdir(exist_ok=True)
            time.sleep(1)
            with open(log_path, "a") as lf:
                subprocess.Popen(
                    [str(HERMES_BIN), "gateway", "run"],
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
    return {
        "transport_mode": normalized.get("transport_mode"),
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


def _chat_completion_request(target: dict, messages: list[dict]) -> str:
    import socket
    import urllib.error
    import urllib.request

    payload = {"model": target["model"] or "hermes-agent", "messages": messages, "stream": False}
    headers = _api_server_headers(target.get("api_key"), target.get("provider"))
    headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        _build_openai_api_url(target["base_url"], "chat/completions"),
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
    transport_mode = normalized.get("transport_mode")
    has_image_files = any(f.suffix.lower() in IMAGE_EXTENSIONS for f in files or [])
    image_support, image_reason = _image_attachment_support_status()
    api_server_enabled = _check_api_server()

    if transport_mode == CHAT_TRANSPORT_API:
        transport = CHAT_TRANSPORT_API
    elif transport_mode == CHAT_TRANSPORT_CLI:
        transport = CHAT_TRANSPORT_CLI
    else:
        transport = CHAT_TRANSPORT_API if api_server_enabled else CHAT_TRANSPORT_CLI

    notice = normalized.get("transport_notice") or ""

    return {
        "transport": transport,
        "cancel_supported": transport == CHAT_TRANSPORT_CLI,
        "image_support": image_support,
        "image_reason": image_reason,
        "api_server_enabled": api_server_enabled,
        "transport_notice": notice,
        "use_sidecar_vision": transport == CHAT_TRANSPORT_CLI and has_image_files,
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
    cmd = [str(HERMES_BIN), "chat", "-Q"]
    if session.get("hermes_session_id"):
        cmd.extend(["--resume", session["hermes_session_id"]])
    cmd.extend(["-q", prompt])
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(Path.home()),
            env={**os.environ, "NO_COLOR": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if request_id:
            _update_chat_request(request_id, pid=proc.pid, pgid=os.getpgid(proc.pid))

        deadline = time.time() + CHAT_REQUEST_TIMEOUT
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
                    _update_chat_request(request_id, status="cancelled")
                    raise ChatRequestCancelled("Request cancelled")
            remaining = deadline - time.time()
            if remaining <= 0:
                _terminate_chat_process(proc.pid, os.getpgid(proc.pid), signal.SIGKILL)
                proc.communicate()
                raise subprocess.TimeoutExpired(proc.args, CHAT_REQUEST_TIMEOUT)
            try:
                stdout, stderr = proc.communicate(timeout=min(CHAT_CANCEL_POLL_INTERVAL, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if request_id:
            state = _read_request_control(request_id)
            if state and state.get("cancel_requested_at"):
                _update_chat_request(request_id, status="cancelled")
                raise ChatRequestCancelled("Request cancelled")

        if proc.returncode != 0:
            error_output = stderr.strip() or stdout.strip() or f"Hermes CLI exited with status {proc.returncode}"
            raise ChatBackendError(error_output)

        output = stdout.strip()
        if not output:
            raise ChatBackendError(stderr.strip() or "Hermes returned an empty response")
        import re as _re
        output = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
        output = _re.sub(r'\x1b\].*?\x07', '', output)
        return _parse_hermes_chat_result(output)
    except ChatRequestCancelled:
        raise
    except subprocess.TimeoutExpired:
        raise ChatRequestTimeout(f"Hermes did not respond within {CHAT_REQUEST_TIMEOUT} seconds")
    except ChatBackendError:
        raise
    except Exception as e:
        raise ChatBackendError(f"Error calling Hermes: {e}") from e


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
    except ChatBackendError:
        raise
    except Exception as exc:
        raise ChatBackendError(f"API server error: {exc}") from exc


def _provider_env_api_key(provider: str | None) -> str:
    provider_name = (provider or "").strip().lower()
    provider_env_map = {
        "openrouter": "OPENROUTER_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai-codex": "OPENAI_API_KEY",
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
    env_key = provider_env_map.get(provider_name)
    return _runtime_env_value(env_key, "") if env_key else ""


def _resolved_target_api_key(target: dict | None) -> str:
    target = target or {}
    explicit_api_key = (target.get("api_key") or "").strip()
    if explicit_api_key:
        return explicit_api_key
    provider_api_key = _provider_env_api_key(target.get("provider"))
    if provider_api_key:
        return provider_api_key
    return _runtime_env_value(
        "HERMES_API_KEY",
        _runtime_env_value("API_SERVER_KEY", ""),
    ).strip()


def _api_server_headers(api_key: str | None = None, provider: str | None = None) -> dict:
    headers = {}
    resolved_api_key = (api_key or "").strip() if api_key is not None else ""
    if not resolved_api_key and provider:
        resolved_api_key = _provider_env_api_key(provider)
    if not resolved_api_key:
        resolved_api_key = _runtime_env_value(
            "HERMES_API_KEY",
            _runtime_env_value("API_SERVER_KEY", ""),
        ).strip()
    if resolved_api_key:
        headers["Authorization"] = f"Bearer {resolved_api_key}"
    return headers


def _resolve_api_target(prefer_vision: bool = False) -> dict:
    model_cfg = _normalized_model_config()
    default_target = {
        "base_url": (_runtime_env_value("HERMES_API_URL", "") or model_cfg.get("base_url") or HERMES_API_URL or "").strip(),
        "api_key": (model_cfg.get("api_key") or "").strip(),
        "model": (model_cfg.get("default_model") or "").strip(),
        "provider": (model_cfg.get("default_provider") or "").strip(),
    }
    default_target["api_key"] = _resolved_target_api_key(default_target)
    if not prefer_vision:
        return default_target

    vision_cfg = model_cfg.get("vision")
    if isinstance(vision_cfg, str) and vision_cfg.strip():
        merged = dict(default_target)
        merged["model"] = vision_cfg.strip()
        return merged
    if isinstance(vision_cfg, dict):
        merged = dict(default_target)
        for key in ("base_url", "api_key", "model", "provider"):
            if isinstance(vision_cfg.get(key), str) and vision_cfg.get(key).strip():
                merged[key] = vision_cfg.get(key).strip()
        merged["api_key"] = _resolved_target_api_key(merged)
        return merged
    return default_target


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
    """Check if Hermes API server is reachable and compression is enabled.

    Currently returns False to force CLI mode — the CLI (hermes chat -q)
    respects compression settings in ~/.hermes/config.yaml (threshold: 0.5).
    The API server does not yet support session-level compression.
    Set HERMES_USE_API=true in the environment to re-enable API server mode.
    """
    if _runtime_env_value("HERMES_USE_API", "").lower() in ("1", "true", "yes"):
        try:
            return _api_server_healthcheck()
        except Exception:
            return False
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
    api_ok, api_reason, _ = _api_server_probe(timeout=2, prefer_vision=True)
    if api_ok:
        image_model_ok, image_model_reason = _openrouter_model_supports_images(target, timeout=3)
        if image_model_ok is False:
            return False, image_model_reason
        return True, ""
    api_url = target.get("base_url") or HERMES_API_URL
    return False, f"OpenAI-compatible vision sidecar API is not reachable at {api_url} ({api_reason})"


def _get_or_create_chat_session(session_id=None):
    existing = _load_session(session_id) if session_id else None
    if existing:
        return _normalize_chat_session(existing)
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    session = {
        "id": session_id,
        "messages": [],
        "created": now,
        "title": "New Chat",
        "updated": now,
        "hermes_session_id": None,
        "transport_mode": None,
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
    sess = _get_or_create_chat_session(session_id)
    if requested_folder_id:
        ensured = _ensure_folder_exists(requested_folder_id)
        requested_folder_id = ensured["id"] if ensured else requested_folder_id
    if requested_folder_id and not sess.get("folder_id"):
        sess["folder_id"] = requested_folder_id
    sid = sess["id"]
    request_plan = _plan_chat_request(sess, files)
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
        use_api_server = request_plan["transport"] == CHAT_TRANSPORT_API
        if use_api_server:
            if sess.get("transport_mode") != CHAT_TRANSPORT_API:
                sess["transport_mode"] = CHAT_TRANSPORT_API
                sess["continuity_mode"] = CHAT_CONTINUITY_LOCAL
                sess["transport_notice"] = request_plan["transport_notice"]
                sess["hermes_session_id"] = None
            api_msgs = []
            for m in sess["messages"]:
                msg = {"role": m["role"], "content": m["content"]}
                if m.get("files"):
                    msg["files"] = m["files"]
                api_msgs.append(msg)
            response_text = _call_api_server(
                sess,
                api_msgs,
                sid,
                files,
                prefer_vision=_session_has_image_history(sess) or any(
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
            if hermes_session_id:
                sess["hermes_session_id"] = hermes_session_id
                sess["continuity_mode"] = CHAT_CONTINUITY_HERMES
                sess["transport_notice"] = ""
            else:
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
                     "timestamp": datetime.now().isoformat()}
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
    session = _get_or_create_chat_session()
    context_update, errors = _parse_chat_context_update(data)
    folder_id = context_update.get("folder_id") or ""
    if folder_id:
        ensured = _ensure_folder_exists(folder_id)
        context_update["folder_id"] = ensured["id"] if ensured else folder_id
    if errors:
        _delete_session_from_disk(session["id"])
        return jsonify({"error": "Invalid chat context", "details": errors}), 400
    session.update(context_update)
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
    api_server = _check_api_server()
    default_api_ok, default_api_reason, default_api_probe = _api_server_probe(timeout=2)
    image_support, image_reason = _image_attachment_support_status()
    vision_ready, vision_reason = _vision_configured()
    vision_target = _resolve_api_target(prefer_vision=True)
    return jsonify({
        "api_server": api_server,
        "api_url": _runtime_env_value("HERMES_API_URL", HERMES_API_URL),
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
        "readiness": {
            "screenshots_ready": image_support,
            "vision_sidecar_ready": image_support,
            "vision_configured": vision_ready,
            "vision_reason": vision_reason,
            "vision_api_url": vision_target.get("base_url") or _runtime_env_value("HERMES_API_URL", HERMES_API_URL),
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
