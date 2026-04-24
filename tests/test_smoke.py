import base64
import copy
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("HERMES_WEBUI_TOKEN", "test-token")

import app as mod


class FakeHTTPResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def runtime_status_stub(
    *,
    requires_cli=False,
    cli_reason="",
    reasons=None,
    memory=None,
    skills=None,
    integrations=None,
    hooks=None,
    starter_items=None,
):
    return {
        "requires_cli": requires_cli,
        "cli_reason": cli_reason,
        "reasons": list(reasons or []),
        "active_features": ["memory"] if requires_cli else [],
        "memory": {
            "enabled": False,
            "user_profile_enabled": False,
            "cli_tool_enabled": False,
            "openai_api_key_present": False,
            "openai_api_key_source": "",
            "semantic_search_ready": False,
            "detail": "Hermes memory is disabled.",
            **(memory or {}),
        },
        "skills": {
            "detected_count": 0,
            "enabled_count": 0,
            "tool_enabled": False,
            **(skills or {}),
        },
        "integrations": {
            "configured_count": 0,
            "configured_names": [],
            **(integrations or {}),
        },
        "hooks": {
            "configured": False,
            "keys": [],
            **(hooks or {}),
        },
        "starter_pack": {
            "items": list(starter_items or []),
        },
    }


class HermesWebUISmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self.tmpdir.name)
        mod.CHAT_DATA_DIR = tmp / "chat_data"
        mod.CHAT_DATA_DIR.mkdir()
        mod.CHAT_DATA_LOCK = mod.CHAT_DATA_DIR / ".lock"
        mod.CHAT_FOLDERS_PATH = mod.CHAT_DATA_DIR / ".folders.json"
        mod.CHAT_FOLDER_SOURCE_DIR = mod.CHAT_DATA_DIR / "sources"
        mod.CHAT_FOLDER_SOURCE_DIR.mkdir()
        mod.CHAT_REQUEST_DIR = tmp / "run" / "chat_requests"
        mod.CHAT_REQUEST_DIR.mkdir(parents=True)
        mod.UPLOAD_FOLDER = tmp / "uploads"
        mod.UPLOAD_FOLDER.mkdir()
        mod.CRON_JOBS_PATH = tmp / "run" / "cron_jobs.json"
        mod.chat_sessions.clear()
        mod.chat_folders.clear()
        self.original_config = copy.deepcopy(mod.cfg._config)
        self.client = mod.app.test_client()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self):
        mod.cfg._config = self.original_config
        self.tmpdir.cleanup()

    def test_chat_resume_and_disk_backed_crud(self):
        call_session_ids = []

        def fake_call(session, message, files=None, request_id=None, file_display_names=None):
            call_session_ids.append(session.get("hermes_session_id"))
            if message == "My name is Alice":
                return ("I will remember that.", "hermes-session-1")
            if message == "What is my name?":
                return ("Alice", session.get("hermes_session_id"))
            return (f"echo:{message}", session.get("hermes_session_id"))

        with patch.object(mod, "_call_hermes_direct", side_effect=fake_call), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")):
            first = self.client.post("/api/chat", json={"message": "My name is Alice"}, headers=self.headers)
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]

            mod.chat_sessions.clear()
            second = self.client.post("/api/chat", json={"message": "What is my name?", "session_id": session_id}, headers=self.headers)
            self.assertEqual(second.status_code, 200, second.data)
            self.assertEqual(second.get_json()["response"], "Alice")
            self.assertEqual(call_session_ids, [None, "hermes-session-1"])

            persisted = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
            self.assertEqual(persisted["hermes_session_id"], "hermes-session-1")

            mod.chat_sessions.clear()
            renamed = self.client.post(f"/api/chat/sessions/{session_id}/rename", json={"title": "Alice Chat"}, headers=self.headers)
            self.assertEqual(renamed.status_code, 200, renamed.data)
            self.assertEqual(renamed.get_json()["title"], "Alice Chat")

            mod.chat_sessions.clear()
            listing = self.client.get("/api/chat/sessions", headers=self.headers)
            sessions = listing.get_json()["sessions"]
            self.assertTrue(any(s["id"] == session_id and s["title"] == "Alice Chat" for s in sessions))

            mod.chat_sessions.clear()
            cleared = self.client.post(f"/api/chat/sessions/{session_id}/clear", headers=self.headers)
            self.assertEqual(cleared.status_code, 200, cleared.data)
            cleared_data = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
            self.assertEqual(cleared_data["messages"], [])
            self.assertIsNone(cleared_data["hermes_session_id"])

    def test_service_control_success_semantics(self):
        def fake_run(returncode=0, stdout="ok", stderr=""):
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        with patch.object(mod, "time") as fake_time, \
             patch.object(mod, "_run_hermes", return_value=fake_run(returncode=1, stdout="already stopped")), \
             patch.object(mod, "_gateway_status", return_value={"running": False}):
            fake_time.sleep.return_value = None
            stop_resp = self.client.post("/api/service/stop", headers=self.headers)
            self.assertEqual(stop_resp.status_code, 200, stop_resp.data)
            self.assertTrue(stop_resp.get_json()["ok"])

        with patch.object(mod, "_run_hermes", return_value=fake_run(returncode=0, stdout="doctor ok")), \
             patch.object(mod, "_gateway_status", return_value={"running": False}):
            doctor_resp = self.client.post("/api/service/doctor", headers=self.headers)
            self.assertEqual(doctor_resp.status_code, 200, doctor_resp.data)
            self.assertTrue(doctor_resp.get_json()["ok"])

    def test_call_api_server_handles_single_image_payload(self):
        image_path = Path(self.tmpdir.name) / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        captured = {}

        def fake_urlopen(req, timeout=300):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeHTTPResponse({"choices": [{"message": {"content": "vision ok"}}]})

        with patch.object(mod, "_normalized_model_config", return_value={
            "default_model": "default-model",
            "vision": {
                "provider": "openai",
                "model": "vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "vision-secret",
            },
        }), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server({}, [{"role": "user", "content": ""}], "sid-1", [image_path])

        self.assertEqual(result, "vision ok")
        self.assertEqual(captured["url"], "https://vision.example.test/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "vision-model")
        content = captured["payload"]["messages"][-1]["content"]
        self.assertTrue(any(item["type"] == "image_url" for item in content))

    def test_summarize_upstream_error_detail_prefers_metadata_raw(self):
        payload = json.dumps({
            "error": {
                "message": "Provider returned error",
                "code": 429,
                "metadata": {
                    "raw": "vision-model is temporarily rate-limited upstream. Please retry shortly."
                },
            }
        })
        self.assertEqual(
            mod._summarize_upstream_error_detail(payload),
            "vision-model is temporarily rate-limited upstream. Please retry shortly.",
        )

    def test_summarize_upstream_error_detail_handles_plain_text(self):
        self.assertEqual(
            mod._summarize_upstream_error_detail("plain upstream failure", "fallback"),
            "plain upstream failure",
        )

    def test_openrouter_discovery_endpoints_preserves_model_path_segments(self):
        captured = {}

        def fake_fetch(path, timeout=10):
            captured["path"] = path
            return {"data": {"endpoints": []}}

        with patch.object(mod, "_openrouter_fetch_json", side_effect=fake_fetch):
            endpoints = mod._openrouter_discovery_endpoints("minimax/minimax-m2.7")

        self.assertEqual(endpoints, [])
        self.assertEqual(captured["path"], "models/minimax/minimax-m2.7/endpoints")

    def test_call_api_server_can_stay_on_vision_target_without_new_image(self):
        captured = {}

        def fake_urlopen(req, timeout=300):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeHTTPResponse({"choices": [{"message": {"content": "vision text ok"}}]})

        with patch.object(mod, "_normalized_model_config", return_value={
            "default_model": "default-model",
            "base_url": "https://default.example.test/v1",
            "vision": {
                "provider": "openai",
                "model": "vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "vision-secret",
            },
        }), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server(
                {},
                [{"role": "user", "content": "continue"}],
                "sid-1",
                prefer_vision=True,
            )

        self.assertEqual(result, "vision text ok")
        self.assertEqual(captured["url"], "https://vision.example.test/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "vision-model")

    def test_call_api_server_uses_api_token_alias_for_authorization(self):
        captured = {}

        def fake_urlopen(req, timeout=300):
            captured["headers"] = dict(req.header_items())
            return FakeHTTPResponse({"choices": [{"message": {"content": "api ok"}}]})

        with patch.dict(mod.os.environ, {"HERMES_API_TOKEN": "server-token"}, clear=False), \
             patch.object(mod, "_normalized_model_config", return_value={
                 "default_model": "default-model",
                 "base_url": "https://api.example.test/v1",
             }), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server({}, [{"role": "user", "content": "hello"}], "sid-1")

        self.assertEqual(result, "api ok")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer server-token")

    def test_call_api_server_prefers_port_scoped_repo_token_over_generic_repo_env(self):
        captured = {}
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        leire_home = profiles_dir / "leire"
        profiles_dir.mkdir(parents=True)
        leire_home.mkdir(parents=True)
        (leire_home / ".env").write_text("API_SERVER_PORT=8643\n", encoding="utf-8")
        repo_env = Path(self.tmpdir.name) / "repo.env"
        repo_env.write_text("HERMES_API_TOKEN=repo-token\nHERMES_API_TOKEN_PORT_8643=port-token\n", encoding="utf-8")
        state_path = Path(self.tmpdir.name) / "webui_profile"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("leire\n", encoding="utf-8")

        def fake_urlopen(req, timeout=300):
            captured["headers"] = dict(req.header_items())
            return FakeHTTPResponse({"choices": [{"message": {"content": "api ok"}}]})

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "REPO_ENV_PATH", repo_env), \
             patch.object(mod, "_normalized_model_config", return_value={
                 "default_model": "default-model",
                 "base_url": "https://api.example.test:8643/v1",
             }), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server({}, [{"role": "user", "content": "hello"}], "sid-1")

        self.assertEqual(result, "api ok")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer port-token")

    def test_call_api_server_prefers_gateway_port_over_target_base_url_port(self):
        captured = {}
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        repo_env = Path(self.tmpdir.name) / "repo.env"
        repo_env.write_text(
            "HERMES_API_TOKEN_PORT_8642=gateway-token\nHERMES_API_TOKEN_PORT_443=target-token\n",
            encoding="utf-8",
        )

        def fake_urlopen(req, timeout=300):
            captured["headers"] = dict(req.header_items())
            return FakeHTTPResponse({"choices": [{"message": {"content": "api ok"}}]})

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "REPO_ENV_PATH", repo_env), \
             patch.object(mod, "_effective_hermes_api_url", return_value="http://127.0.0.1:8642"), \
             patch.object(mod, "_normalized_model_config", return_value={
                 "default_model": "default-model",
                 "base_url": "https://ollama.com/v1",
             }), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server({}, [{"role": "user", "content": "hello"}], "sid-1")

        self.assertEqual(result, "api ok")
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer gateway-token")

    def test_runtime_profile_api_token_endpoint_reads_repo_env_token_for_gateway_port(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        leire_home = profiles_dir / "leire"
        profiles_dir.mkdir(parents=True)
        leire_home.mkdir(parents=True)
        (leire_home / ".env").write_text("API_SERVER_PORT=8643\n", encoding="utf-8")
        repo_env = Path(self.tmpdir.name) / "repo.env"
        repo_env.write_text("HERMES_API_TOKEN_PORT_8643=profile-token\n", encoding="utf-8")

        with patch.object(mod, "_current_webui_token", return_value="test-token"), \
             patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "REPO_ENV_PATH", repo_env):
            resp = self.client.get("/api/runtime/profiles/leire/api-token", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["profile"], "leire")
        self.assertEqual(body["env_path"], str(repo_env))
        self.assertEqual(body["api_port"], "8643")
        self.assertTrue(body["has_token"])
        self.assertEqual(body["token_key"], "HERMES_API_TOKEN_PORT_8643")
        self.assertTrue(body["masked_token"].endswith("oken"))

    def test_runtime_profile_api_token_endpoint_writes_canonical_repo_token_for_gateway_port(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        leire_home = profiles_dir / "leire"
        profiles_dir.mkdir(parents=True)
        leire_home.mkdir(parents=True)
        (leire_home / ".env").write_text("API_SERVER_PORT=8643\n", encoding="utf-8")
        repo_env = Path(self.tmpdir.name) / "repo.env"
        repo_env.write_text("API_SERVER_KEY_PORT_8643=old-token\n", encoding="utf-8")

        with patch.object(mod, "_current_webui_token", return_value="test-token"), \
             patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "REPO_ENV_PATH", repo_env):
            resp = self.client.put(
                "/api/runtime/profiles/leire/api-token",
                json={"token": "new-profile-token"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        saved = repo_env.read_text(encoding="utf-8")
        self.assertIn("HERMES_API_TOKEN_PORT_8643=new-profile-token", saved)
        self.assertNotIn("API_SERVER_KEY_PORT_8643=old-token", saved)

    def test_call_api_server_surfaces_top_level_error_payload(self):
        def fake_urlopen(req, timeout=300):
            return FakeHTTPResponse({"error": {"message": "image too small"}})

        with patch.object(mod, "_normalized_model_config", return_value={
            "default_model": "default-model",
            "vision": {
                "provider": "openrouter",
                "model": "vision-model",
                "base_url": "https://vision.example.test/v1",
                "api_key": "vision-secret",
            },
        }), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(mod.ChatBackendError) as ctx:
                mod._call_api_server({}, [{"role": "user", "content": "describe"}], "sid-1", prefer_vision=True)

        self.assertIn("image too small", str(ctx.exception))

    def test_find_hermes_bin_accepts_string_path_from_shutil_which(self):
        tmp_home = Path(self.tmpdir.name) / "home"
        hermes_path = tmp_home / "bin" / "hermes"
        hermes_path.parent.mkdir(parents=True)
        hermes_path.write_text("#!/bin/sh\n", encoding="utf-8")

        with patch.object(mod, "HERMES_HOME", tmp_home / ".hermes"), \
             patch.object(mod, "HERMES_REPO_DIR", tmp_home / ".hermes" / "hermes-agent"), \
             patch.object(mod.Path, "home", return_value=tmp_home), \
             patch("shutil.which", return_value=str(hermes_path)):
            found = mod._find_hermes_bin()

        self.assertEqual(found, hermes_path)

    def test_find_hermes_bin_prefers_repo_managed_install(self):
        tmp_home = Path(self.tmpdir.name) / "home"
        managed_bin = tmp_home / ".hermes" / "hermes-agent" / "venv" / "bin" / "hermes"
        legacy_bin = tmp_home / ".hermes" / ".venv" / "bin" / "hermes"
        path_bin = tmp_home / ".local" / "bin" / "hermes"
        for path in (managed_bin, legacy_bin, path_bin):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")

        with patch.object(mod, "HERMES_HOME", tmp_home / ".hermes"), \
             patch.object(mod, "HERMES_REPO_DIR", tmp_home / ".hermes" / "hermes-agent"), \
             patch.object(mod.Path, "home", return_value=tmp_home), \
             patch("shutil.which", return_value=str(path_bin)):
            found = mod._find_hermes_bin()

        self.assertEqual(found, managed_bin)

    def test_chat_status_exposes_readiness_details(self):
        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_api_server_probe", return_value=(False, "models probe returned HTTP 401", None)), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "OpenAI-compatible image chat API is not reachable")), \
             patch.object(mod, "_vision_configured", return_value=(True, "")), \
             patch.object(mod, "_resolve_api_target", return_value={"base_url": "https://vision.example.test/v1", "model": "vision-model", "api_key": "secret"}), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub(
                 requires_cli=True,
                 cli_reason="Hermes CLI is required because memory is active.",
                 reasons=["Hermes memory is enabled for chat sessions."],
                 memory={
                     "enabled": True,
                     "cli_tool_enabled": True,
                     "openai_api_key_present": True,
                     "semantic_search_ready": True,
                     "detail": "Hermes memory is enabled and can use your OpenAI API key for semantic recall.",
                 },
                 starter_items=[
                     {"id": "memory", "label": "Memory", "kind": "runtime", "status": "ready", "detail": "ready"},
                 ],
             )):
            resp = self.client.get("/api/chat/status", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertIn("readiness", data)
        self.assertIn("runtime", data)
        self.assertTrue(data["transport_policy"]["requires_cli"])
        self.assertEqual(data["transport_policy"]["reason"], "Hermes CLI is required because memory is active.")
        self.assertTrue(data["runtime"]["memory"]["semantic_search_ready"])
        self.assertEqual(data["readiness"]["vision_api_url"], "https://vision.example.test/v1")
        self.assertEqual(data["readiness"]["vision_model"], "vision-model")
        self.assertFalse(data["readiness"]["screenshots_ready"])
        self.assertEqual(data["request_lifecycle"]["server_timeout_seconds"], mod.CHAT_SERVER_TIMEOUT)
        self.assertEqual(data["limits"]["max_upload_bytes"], mod.MAX_UPLOAD_SIZE)
        self.assertEqual(data["limits"]["max_request_body_bytes"], mod.MAX_REQUEST_BODY_SIZE)

    def test_chat_status_enables_api_transport_when_probe_is_reachable(self):
        with patch.object(mod, "_current_webui_token", return_value="test-token"), \
             patch.object(mod, "_api_server_healthcheck", return_value=True), \
             patch.object(mod, "_api_server_probe", return_value=(True, "", {"probe": "models", "status": 200, "url": "https://ollama.com/v1/models"})), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "Hermes vision is not configured")), \
             patch.object(mod, "_vision_configured", return_value=(False, "Hermes vision is not configured")), \
             patch.object(mod, "_resolve_api_target", return_value={"base_url": "https://ollama.com/v1", "model": "", "api_key": ""}), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub(
                 requires_cli=False,
                 reasons=["1 integration is configured."],
                 integrations={"configured_count": 1, "configured_names": ["Discord"]},
             )):
            resp = self.client.get("/api/chat/status", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertTrue(data["api_server"])
        self.assertTrue(data["api_probe"]["reachable"])
        self.assertTrue(data["transport_policy"]["api_selectable"])
        self.assertFalse(data["transport_policy"]["requires_cli"])

    def test_image_attachment_support_requires_credentials_for_remote_endpoint(self):
        target = {
            "base_url": "https://ollama.com/v1",
            "api_key": "",
            "model": "qwen3.5:cloud",
            "provider": "auto",
        }

        with patch.object(mod, "_vision_configured", return_value=(True, "")), \
             patch.object(mod, "_resolve_api_target", return_value=target), \
             patch.object(mod, "_api_server_probe", side_effect=AssertionError("probe should not run when credentials are missing")):
            ready, reason = mod._image_attachment_support_status()

        self.assertFalse(ready)
        self.assertIn("Vision API key is missing", reason)

    def test_resolved_target_api_key_expands_runtime_template(self):
        with patch.dict(mod.os.environ, {}, clear=True), \
             patch.object(mod, "_repo_env_values", return_value={}), \
             patch.object(mod, "_hermes_env_values", return_value={"OPENAI_API_KEY": "resolved-secret"}):
            resolved = mod._resolved_target_api_key({"api_key": "${OPENAI_API_KEY}", "provider": "openai"})

        self.assertEqual(resolved, "resolved-secret")

    def test_env_api_returns_metadata_and_presets(self):
        env_file = Path(self.tmpdir.name) / ".env"
        env_file.write_text("OPENAI_API_KEY=test-key\nOPENAI_BASE_URL=\n", encoding="utf-8")

        with patch.object(mod, "ENV_PATH", env_file):
            resp = self.client.get("/api/env", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertIn("metadata", data)
        self.assertIn("presets", data)
        self.assertEqual(data["metadata"]["OPENAI_API_KEY"]["label"], "OpenAI API Key")
        self.assertEqual(data["metadata"]["OPENAI_BASE_URL"]["default_value"], "https://api.openai.com/v1")
        self.assertIn("Provider", data["group_help"])
        self.assertTrue(any(item["key"] == "OPENAI_API_KEY" for item in data["presets"]["Provider"]))

    def test_env_api_post_writes_plain_unquoted_value(self):
        env_file = Path(self.tmpdir.name) / ".env"

        with patch.object(mod, "ENV_PATH", env_file):
            resp = self.client.post(
                "/api/env",
                json={"key": "OPENAI_API_KEY", "value": "sk-test-value"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(env_file.read_text(encoding="utf-8"), "OPENAI_API_KEY=sk-test-value\n")

    def test_env_api_put_updates_without_single_quotes(self):
        env_file = Path(self.tmpdir.name) / ".env"
        env_file.write_text("OPENAI_API_KEY=sk-old\n", encoding="utf-8")

        with patch.object(mod, "ENV_PATH", env_file):
            resp = self.client.put(
                "/api/env/OPENAI_API_KEY",
                json={"value": "sk-new-value"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(env_file.read_text(encoding="utf-8"), "OPENAI_API_KEY=sk-new-value\n")

    def test_runtime_profile_switch_changes_config_source(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        leire_home = profiles_dir / "leire"
        hermes_root.mkdir()
        profiles_dir.mkdir()
        leire_home.mkdir(parents=True)
        (hermes_root / "config.yaml").write_text("agent:\n  personality: default\n", encoding="utf-8")
        (leire_home / "config.yaml").write_text("agent:\n  personality: leire\n", encoding="utf-8")
        state_path = Path(self.tmpdir.name) / "webui_profile"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path):
            mod.cfg.load()

            before = self.client.get("/api/config", headers=self.headers)
            self.assertEqual(before.status_code, 200, before.data)
            self.assertEqual(before.get_json()["agent"]["personality"], "default")

            switched = self.client.put("/api/runtime/profiles", json={"profile": "leire"}, headers=self.headers)
            self.assertEqual(switched.status_code, 200, switched.data)
            self.assertEqual(switched.get_json()["selected"], "leire")

            after = self.client.get("/api/config", headers=self.headers)
            self.assertEqual(after.status_code, 200, after.data)
            self.assertEqual(after.get_json()["agent"]["personality"], "leire")

    def test_runtime_profile_switch_changes_chat_status_api_url(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        leire_home = profiles_dir / "leire"
        hermes_root.mkdir()
        profiles_dir.mkdir()
        leire_home.mkdir(parents=True)
        (leire_home / ".env").write_text("HERMES_API_URL=http://127.0.0.1:9876\n", encoding="utf-8")
        state_path = Path(self.tmpdir.name) / "webui_profile"

        with patch.dict(mod.os.environ, {"HERMES_WEBUI_TOKEN": "test-token"}, clear=True), \
             patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_api_server_probe", return_value=(False, "disabled", None)), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_vision_configured", return_value=(False, "not configured")), \
             patch.object(mod, "_resolve_api_target", return_value={"base_url": "", "model": "", "api_key": ""}), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()):
            switched = self.client.put("/api/runtime/profiles", json={"profile": "leire"}, headers=self.headers)
            self.assertEqual(switched.status_code, 200, switched.data)

            resp = self.client.get("/api/chat/status", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertEqual(data["profile"], "leire")
        self.assertEqual(data["api_url"], "http://127.0.0.1:9876")

    def test_chat_session_history_preserves_profile_used(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", return_value=("ok", "hermes-session-1")):
            switched = self.client.put("/api/runtime/profiles", json={"profile": "leire"}, headers=self.headers)
            self.assertEqual(switched.status_code, 200, switched.data)

            sent = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers)
            self.assertEqual(sent.status_code, 200, sent.data)
            session_id = sent.get_json()["session_id"]

            switched_back = self.client.put("/api/runtime/profiles", json={"profile": "default"}, headers=self.headers)
            self.assertEqual(switched_back.status_code, 200, switched_back.data)

            listing = self.client.get("/api/chat/sessions", headers=self.headers)
            self.assertEqual(listing.status_code, 200, listing.data)
            sessions = listing.get_json()["sessions"]
            matching = next(item for item in sessions if item["id"] == session_id)
            self.assertEqual(matching["session"]["profile"], "leire")

            messages = self.client.get(f"/api/chat/sessions/{session_id}/messages", headers=self.headers)
            self.assertEqual(messages.status_code, 200, messages.data)
            self.assertEqual(messages.get_json()["session"]["profile"], "leire")

    def test_chat_session_profile_switch_creates_new_runtime_segment(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]
            created_session = created.get_json()["session"]
            self.assertEqual(created_session["profile"], "default")
            self.assertEqual(created_session["active_segment_index"], 1)
            self.assertEqual(len(created_session["segments"]), 1)

            switched = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched.status_code, 200, switched.data)
            session = switched.get_json()["session"]
            self.assertEqual(session["profile"], "leire")
            self.assertEqual(session["active_segment_index"], 2)
            self.assertEqual(len(session["segments"]), 2)
            self.assertEqual(session["segments"][0]["profile"], "default")
            self.assertEqual(session["segments"][1]["profile"], "leire")

            persisted = mod._load_session(session_id)
            self.assertEqual(persisted["profile"], "leire")
            self.assertEqual(persisted["active_segment_id"], "segment-2")
            self.assertEqual(len(persisted["segments"]), 2)

    def test_chat_session_profile_switch_is_local_and_used_on_next_turn(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"
        seen_profiles = []

        def fake_call(session, message, files=None, request_id=None, file_display_names=None):
            seen_profiles.append(mod._selected_hermes_profile_name())
            return (f"ok:{mod._selected_hermes_profile_name()}", "hermes-session-1")

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", side_effect=fake_call):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]

            first = self.client.post(
                "/api/chat",
                json={"message": "hello", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            self.assertEqual(first.get_json()["assistant_message"]["profile"], "default")

            switched = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched.status_code, 200, switched.data)
            self.assertFalse(state_path.exists())

            second = self.client.post(
                "/api/chat",
                json={"message": "follow up", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)
            second_json = second.get_json()
            self.assertEqual(second_json["session"]["profile"], "leire")
            self.assertEqual(second_json["assistant_message"]["profile"], "leire")
            self.assertEqual(second_json["assistant_message"]["content"], "ok:leire")
            self.assertEqual(seen_profiles, ["default", "leire"])

    def test_chat_session_create_accepts_local_profile_without_writing_global_state(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path):
            created = self.client.post(
                "/api/chat/sessions",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200, created.data)

            session = created.get_json()["session"]
            self.assertEqual(session["profile"], "leire")
            self.assertEqual(session["segments"][0]["profile"], "leire")
            self.assertFalse(state_path.exists())

    def test_chat_session_profile_switch_reuses_prior_profile_cli_continuity(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"
        seen_calls = []

        def fake_call(session, message, files=None, request_id=None, file_display_names=None):
            selected_profile = mod._selected_hermes_profile_name()
            seen_calls.append({
                "profile": selected_profile,
                "resume": session.get("hermes_session_id"),
            })
            if selected_profile == "default":
                return ("ok:default", "hermes-default-1")
            return ("ok:leire", "hermes-leire-1")

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", side_effect=fake_call):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]

            first = self.client.post(
                "/api/chat",
                json={"message": "hello default", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)

            switched_to_leire = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched_to_leire.status_code, 200, switched_to_leire.data)

            second = self.client.post(
                "/api/chat",
                json={"message": "hello leire", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)

            switched_to_default = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "default"},
                headers=self.headers,
            )
            self.assertEqual(switched_to_default.status_code, 200, switched_to_default.data)
            switched_session = switched_to_default.get_json()["session"]
            self.assertEqual(switched_session["profile"], "default")
            self.assertEqual(switched_session["active_segment_index"], 3)
            self.assertEqual(switched_session["segments"][2]["hermes_session_id"], "hermes-default-1")

            third = self.client.post(
                "/api/chat",
                json={"message": "back to default", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(third.status_code, 200, third.data)
            self.assertEqual(third.get_json()["session"]["profile"], "default")

            self.assertEqual(seen_calls, [
                {"profile": "default", "resume": None},
                {"profile": "leire", "resume": None},
                {"profile": "default", "resume": "hermes-default-1"},
            ])

            persisted = mod._load_session(session_id)
            self.assertEqual(persisted["hermes_session_id"], "hermes-default-1")
            self.assertEqual(persisted["segments"][0]["hermes_session_id"], "hermes-default-1")
            self.assertEqual(persisted["segments"][1]["hermes_session_id"], "hermes-leire-1")
            self.assertEqual(persisted["segments"][2]["hermes_session_id"], "hermes-default-1")

    def test_chat_session_reload_uses_last_profile_with_messages(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"
        seen_calls = []

        def fake_call(session, message, files=None, request_id=None, file_display_names=None):
            selected_profile = mod._selected_hermes_profile_name()
            seen_calls.append({
                "profile": selected_profile,
                "resume": session.get("hermes_session_id"),
            })
            if selected_profile == "default":
                return ("ok:default", "hermes-default-1")
            return ("ok:leire", "hermes-leire-1")

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
             patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", side_effect=fake_call):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]

            first = self.client.post(
                "/api/chat",
                json={"message": "hello default", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)

            switched_to_leire = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched_to_leire.status_code, 200, switched_to_leire.data)

            second = self.client.post(
                "/api/chat",
                json={"message": "hello leire", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)

            switched_to_default = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "default"},
                headers=self.headers,
            )
            self.assertEqual(switched_to_default.status_code, 200, switched_to_default.data)
            self.assertEqual(switched_to_default.get_json()["session"]["profile"], "default")

            reloaded = self.client.get(f"/api/chat/sessions/{session_id}/messages", headers=self.headers)
            self.assertEqual(reloaded.status_code, 200, reloaded.data)
            reloaded_session = reloaded.get_json()["session"]
            self.assertEqual(reloaded_session["profile"], "leire")
            self.assertEqual(reloaded_session["active_segment_id"], "segment-2")
            self.assertEqual(len(reloaded_session["segments"]), 2)

            third = self.client.post(
                "/api/chat",
                json={"message": "still leire", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(third.status_code, 200, third.data)
            self.assertEqual(third.get_json()["session"]["profile"], "leire")
            self.assertEqual(third.get_json()["assistant_message"]["profile"], "leire")

            self.assertEqual(seen_calls, [
                {"profile": "default", "resume": None},
                {"profile": "leire", "resume": None},
                {"profile": "leire", "resume": "hermes-leire-1"},
            ])

            persisted = mod._load_session(session_id)
            self.assertEqual(persisted["profile"], "leire")
            self.assertEqual(persisted["active_segment_id"], "segment-2")
            self.assertEqual(len(persisted["segments"]), 2)

    def test_skill_setup_readiness_detects_missing_requirements(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "productivity" / "google-workspace"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: google-workspace
required_credential_files:
  - path: google_token.json
prerequisites:
  env_vars: [GOOGLE_API_KEY]
metadata:
  openclaw:
    requires:
      bins: [gog]
---
""",
            encoding="utf-8",
        )
        skill = {
            "name": "google-workspace",
            "path": "productivity/google-workspace",
            "frontmatter": mod._skill_frontmatter(skill_dir / "SKILL.md"),
        }

        with patch.object(mod, "SKILLS_DIR", skill_root), \
             patch.object(mod, "REPO_ENV_PATH", Path(self.tmpdir.name) / "repo.env"), \
             patch.object(mod, "ENV_PATH", Path(self.tmpdir.name) / "hermes.env"), \
             patch.dict(mod.os.environ, {}, clear=True), \
             patch.object(mod.shutil, "which", return_value=None):
            readiness = mod._skill_setup_readiness(skill)

        self.assertFalse(readiness["ready"])
        self.assertIn("missing credential file google_token.json", readiness["issues"])
        self.assertIn("missing env var GOOGLE_API_KEY", readiness["issues"])
        self.assertIn("missing command gog", readiness["issues"])
        self.assertEqual(readiness["blockers"][0]["kind"], "credential_file")
        self.assertEqual(readiness["blockers"][1]["kind"], "env_var")
        self.assertEqual(readiness["blockers"][1]["label"], "Google API Key")
        self.assertEqual(readiness["blockers"][2]["kind"], "command")
        self.assertTrue(any(action["type"] == "env_var" and action["key"] == "GOOGLE_API_KEY" for action in readiness["actions"]))

    def test_skill_setup_readiness_adds_integration_route_for_channel_skills(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "messaging" / "discord-helper"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: discord-helper
prerequisites:
  env_vars: [DISCORD_TOKEN]
---
""",
            encoding="utf-8",
        )
        skill = {
            "name": "discord-helper",
            "path": "messaging/discord-helper",
            "frontmatter": mod._skill_frontmatter(skill_dir / "SKILL.md"),
        }

        with patch.object(mod, "SKILLS_DIR", skill_root), \
             patch.object(mod, "REPO_ENV_PATH", Path(self.tmpdir.name) / "repo.env"), \
             patch.object(mod, "ENV_PATH", Path(self.tmpdir.name) / "hermes.env"), \
             patch.dict(mod.os.environ, {}, clear=True):
            readiness = mod._skill_setup_readiness(skill)

        self.assertFalse(readiness["ready"])
        self.assertTrue(any(action["type"] == "env_var" and action["key"] == "DISCORD_TOKEN" for action in readiness["actions"]))
        self.assertTrue(any(action["type"] == "screen" and action["screen"] == "channels" for action in readiness["actions"]))

    def test_chat_runtime_status_marks_installed_skill_needing_setup_as_attention(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "productivity" / "google-workspace"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: google-workspace
required_credential_files:
  - path: google_token.json
  - path: google_client_secret.json
---
""",
            encoding="utf-8",
        )
        raw = {
            "memory": {"memory_enabled": False},
            "platform_toolsets": {"cli": ["skills"]},
        }
        with patch.object(mod, "SKILLS_DIR", skill_root):
            runtime = mod._chat_runtime_status(raw=raw)

        items = {item["id"]: item for item in runtime["starter_pack"]["items"]}
        google = items["google_workspace"]
        self.assertEqual(google["status"], "attention")
        self.assertTrue(google["supports_install"])
        self.assertIn("missing credential file google_token.json", google["issues"])
        self.assertEqual(google["matched_skill_paths"], ["productivity/google-workspace"])
        self.assertTrue(google["setup_actions"])
        self.assertTrue(any(candidate["identifier"] == "skills-sh/steipete/clawdis/gog" for candidate in google["install_candidates"]))

    def test_discover_skill_entries_includes_source_metadata_and_setup_status(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "system-design"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: system-design
required_credential_files:
  - path: optional-token.json
---
""",
            encoding="utf-8",
        )
        (skill_dir / mod.SKILL_SOURCE_METADATA_FILENAME).write_text(
            json.dumps(
                {
                    "display": "wondelai/skills",
                    "identifier": "wondelai/skills",
                    "source_repo": "wondelai/skills",
                    "install_mode": "github_repo",
                }
            ),
            encoding="utf-8",
        )

        with patch.object(mod, "SKILLS_DIR", skill_root):
            skills = mod._discover_skill_entries()

        self.assertEqual(len(skills), 1)
        skill = skills[0]
        self.assertEqual(skill["source"]["display"], "wondelai/skills")
        self.assertEqual(skill["source"]["install_mode"], "github_repo")
        self.assertFalse(skill["setup"]["ready"])
        self.assertIn("missing credential file optional-token.json", skill["setup"]["issues"])
        self.assertEqual(skill["setup"]["blockers"][0]["kind"], "credential_file")

    def test_record_skill_install_source_writes_metadata_for_existing_skill(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "weather"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: weather\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            updated = mod._record_skill_install_source(
                ["weather"],
                identifier="skills-sh/steipete/clawdis/weather",
                install_mode="hermes",
                catalog_source="skills.sh",
            )
            skills = mod._discover_skill_entries()

        self.assertEqual(updated, ["weather"])
        self.assertEqual(skills[0]["source"]["identifier"], "skills-sh/steipete/clawdis/weather")
        self.assertEqual(skills[0]["source"]["catalog_source"], "skills.sh")
        self.assertEqual(skills[0]["source"]["install_mode"], "hermes")

    def test_skill_toggle_endpoint_uses_nested_skill_path(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "productivity" / "google-workspace"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: google-workspace\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.post("/api/skills/productivity/google-workspace/toggle", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(resp.get_json()["enabled"])
        self.assertFalse(skill_dir.exists())
        self.assertTrue((skill_root / "productivity" / "google-workspace.disabled" / "SKILL.md").exists())

    def test_skill_toggle_endpoint_reenables_disabled_path(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        disabled_dir = skill_root / "productivity" / "google-workspace.disabled"
        disabled_dir.mkdir(parents=True)
        (disabled_dir / "SKILL.md").write_text("---\nname: google-workspace\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.post("/api/skills/productivity/google-workspace.disabled/toggle", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.get_json()["enabled"])
        self.assertFalse(disabled_dir.exists())
        self.assertTrue((skill_root / "productivity" / "google-workspace" / "SKILL.md").exists())

    def test_skill_toggle_endpoint_normalizes_repeated_disabled_suffixes(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        disabled_dir = skill_root / "system-design.disabled.disabled"
        disabled_dir.mkdir(parents=True)
        (disabled_dir / "SKILL.md").write_text("---\nname: system-design\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.post("/api/skills/system-design.disabled/toggle", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.get_json()["enabled"])
        self.assertFalse(disabled_dir.exists())
        self.assertTrue((skill_root / "system-design" / "SKILL.md").exists())

    def test_skill_bulk_endpoint_removes_selected_paths(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        enabled_dir = skill_root / "system-design"
        disabled_dir = skill_root / "productivity" / "google-workspace.disabled"
        enabled_dir.mkdir(parents=True)
        disabled_dir.mkdir(parents=True)
        (enabled_dir / "SKILL.md").write_text("---\nname: system-design\n---\n", encoding="utf-8")
        (disabled_dir / "SKILL.md").write_text("---\nname: google-workspace\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.post(
                "/api/skills/bulk",
                json={"action": "remove", "paths": ["system-design", "productivity/google-workspace.disabled"]},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertEqual(sorted(body["removed_paths"]), ["productivity/google-workspace.disabled", "system-design"])
        self.assertFalse(enabled_dir.exists())
        self.assertFalse(disabled_dir.exists())

    def test_skill_bulk_endpoint_disables_and_enables_paths(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "system-design"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: system-design\n---\n", encoding="utf-8")

        with patch.object(mod, "SKILLS_DIR", skill_root):
            disabled = self.client.post(
                "/api/skills/bulk",
                json={"action": "disable", "paths": ["system-design"]},
                headers=self.headers,
            )
            enabled = self.client.post(
                "/api/skills/bulk",
                json={"action": "enable", "paths": ["system-design.disabled"]},
                headers=self.headers,
            )

        self.assertEqual(disabled.status_code, 200, disabled.data)
        self.assertEqual(disabled.get_json()["changed_paths"], ["system-design.disabled"])
        self.assertEqual(enabled.status_code, 200, enabled.data)
        self.assertEqual(enabled.get_json()["changed_paths"], ["system-design"])
        self.assertTrue((skill_root / "system-design" / "SKILL.md").exists())

    def test_chat_runtime_status_keeps_optional_cli_features_from_forcing_cli(self):
        raw = {
            "memory": {"memory_enabled": True, "user_profile_enabled": True},
            "platform_toolsets": {"cli": ["memory", "skills"]},
            "discord": {"require_mention": True},
        }
        skills = [{
            "name": "weather",
            "path": "weather",
            "enabled": True,
            "frontmatter": {},
        }]

        runtime = mod._chat_runtime_status(raw=raw, skills=skills)

        self.assertFalse(runtime["requires_cli"])
        self.assertEqual(runtime["cli_reason"], "")
        self.assertEqual(runtime["active_features"], ["memory", "skills", "integrations"])
        self.assertEqual(runtime["blocking_features"], [])
        self.assertIn("Hermes memory is enabled for chat sessions.", runtime["reasons"])
        self.assertIn("1 Hermes skill is enabled.", runtime["reasons"])
        items = {item["id"]: item for item in runtime["starter_pack"]["items"]}
        self.assertNotIn("memory", items)
        self.assertNotIn("discord", items)
        self.assertNotIn("whatsapp", items)
        self.assertNotIn("weather", items)
        self.assertTrue(runtime["memory"]["enabled"])

    def test_chat_runtime_status_marks_memory_action_as_edit_when_openai_key_exists(self):
        raw = {
            "memory": {"memory_enabled": True, "user_profile_enabled": True},
            "platform_toolsets": {"cli": ["memory"]},
        }

        with patch.dict(mod.os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            runtime = mod._chat_runtime_status(raw=raw, skills=[])

        items = {item["id"]: item for item in runtime["starter_pack"]["items"]}
        self.assertNotIn("memory", items)
        self.assertIn("google_workspace", items)
        self.assertIn("summarize", items)
        self.assertIn("weather", items)
        self.assertTrue(runtime["memory"]["semantic_search_ready"])
        self.assertTrue(runtime["memory"]["openai_api_key_present"])

    def test_starter_pack_summarize_stays_installable_when_builtin_summary_skills_exist(self):
        raw = {
            "memory": {"memory_enabled": False},
            "platform_toolsets": {"cli": ["skills"]},
        }
        skills = [
            {"name": "youtube-content", "path": "media/youtube-content", "enabled": True, "frontmatter": {}},
            {"name": "ocr-and-documents", "path": "productivity/ocr-and-documents", "enabled": True, "frontmatter": {}},
            {"name": "arxiv", "path": "research/arxiv", "enabled": True, "frontmatter": {}},
        ]

        runtime = mod._chat_runtime_status(raw=raw, skills=skills)

        items = {item["id"]: item for item in runtime["starter_pack"]["items"]}
        summarize = items["summarize"]
        self.assertEqual(summarize["status"], "missing")
        self.assertEqual(summarize["matches"], [])
        self.assertTrue(summarize["install_available"])
        self.assertEqual(summarize["install_action_label"], "Install")
        self.assertFalse(summarize["installed_candidates"])

    def test_starter_pack_hides_ready_recommended_skills(self):
        raw = {
            "memory": {"memory_enabled": False},
            "platform_toolsets": {"cli": ["skills"]},
        }
        skills = [
            {"name": "weather", "path": "weather", "enabled": True, "frontmatter": {}},
            {"name": "google-workspace", "path": "productivity/google-workspace", "enabled": True, "frontmatter": {}},
        ]

        runtime = mod._chat_runtime_status(raw=raw, skills=skills)

        items = {item["id"]: item for item in runtime["starter_pack"]["items"]}
        self.assertNotIn("weather", items)
        self.assertNotIn("google_workspace", items)

    def test_parse_sidecar_payload_extracts_embedded_json_and_normalizes_lists(self):
        raw = """
The screenshot shows a test page. Here is the structured summary:
```json
{
  "overall_summary": "Hermes test screen",
  "follow_up_hints": "Ask about the heading; Ask about the status line",
  "images": [
    {
      "label": "vision-test.png",
      "summary": "Shows the Hermes heading",
      "visible_text": "Hermes Vision Test\\nStatus Line: CLI continuity should remain enabled.",
      "details": "- White card\\n- Deploy and Cancel buttons"
    }
  ]
}
```
"""
        parsed = mod._parse_sidecar_payload(raw, ["vision-test.png"])
        self.assertEqual(parsed["overall_summary"], "Hermes test screen")
        self.assertEqual(parsed["follow_up_hints"], ["Ask about the heading", "Ask about the status line"])
        self.assertEqual(parsed["images"][0]["label"], "vision-test.png")
        self.assertEqual(
            parsed["images"][0]["visible_text"],
            ["Hermes Vision Test", "Status Line: CLI continuity should remain enabled."],
        )
        self.assertEqual(parsed["images"][0]["details"], ["White card", "Deploy and Cancel buttons"])

    def test_parse_sidecar_payload_uses_nested_payload_and_top_level_fields(self):
        raw = """
Sidecar output:
{
  "notes": "vision run complete",
  "result": {
    "overall_summary": "Settings screen",
    "visible_text": "[\\"General\\", \\"Advanced\\"]",
    "details": "Sidebar on the left; Save button in footer",
    "follow_up_hints": "Ask about the sidebar; Ask about the footer"
  }
}
"""
        parsed = mod._parse_sidecar_payload(raw, ["settings.png"])
        self.assertEqual(parsed["overall_summary"], "Settings screen")
        self.assertEqual(parsed["images"][0]["label"], "settings.png")
        self.assertEqual(parsed["images"][0]["visible_text"], ["General", "Advanced"])
        self.assertEqual(parsed["images"][0]["details"], ["Sidebar on the left", "Save button in footer"])
        self.assertEqual(
            parsed["images"][0]["follow_up_hints"],
            ["Ask about the sidebar", "Ask about the footer"],
        )

    def test_run_sidecar_vision_analysis_wraps_rate_limit_helpfully(self):
        image_path = Path(self.tmpdir.name) / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        user_message = {"timestamp": "2026-04-07T10:00:00"}
        session = {"messages": [user_message]}

        with patch.object(mod, "_resolve_api_target", return_value={
            "provider": "openrouter",
            "model": "vision-model",
            "base_url": "https://vision.example.test/v1",
            "api_key": "secret",
        }), patch.object(
            mod,
            "_chat_completion_request",
            side_effect=mod.ChatBackendError(
                "API server returned HTTP 429: vision-model is temporarily rate-limited upstream. Please retry shortly.",
                status_code=429,
            ),
        ):
            with self.assertRaises(mod.ChatBackendError) as ctx:
                mod._run_sidecar_vision_analysis(
                    session,
                    "describe",
                    [image_path],
                    user_message=user_message,
                )

        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("Vision sidecar is temporarily rate-limited", str(ctx.exception))
        self.assertIn("switch the vision model/provider in Providers", str(ctx.exception))

    def test_save_upload_stream_enforces_limit_and_cleans_partial_file(self):
        destination = mod.UPLOAD_FOLDER / "too-big.bin"
        partial = destination.with_name(f".{destination.name}.part")
        original_limit = mod.MAX_UPLOAD_SIZE
        try:
            mod.MAX_UPLOAD_SIZE = 4
            with self.assertRaises(mod.RequestEntityTooLarge):
                mod._save_upload_stream(SimpleNamespace(stream=io.BytesIO(b"abcdef")), destination)
        finally:
            mod.MAX_UPLOAD_SIZE = original_limit

        self.assertFalse(destination.exists())
        self.assertFalse(partial.exists())

    def test_api_upload_base64_rejects_large_request_with_json_error(self):
        original_limit = mod.MAX_REQUEST_BODY_SIZE
        original_app_limit = mod.app.config["MAX_CONTENT_LENGTH"]
        try:
            mod.MAX_REQUEST_BODY_SIZE = 16
            mod.app.config["MAX_CONTENT_LENGTH"] = 16
            resp = self.client.post(
                "/api/upload/base64",
                data=json.dumps({"data": "A" * 128}),
                headers={**self.headers, "Content-Type": "application/json"},
            )
        finally:
            mod.MAX_REQUEST_BODY_SIZE = original_limit
            mod.app.config["MAX_CONTENT_LENGTH"] = original_app_limit

        self.assertEqual(resp.status_code, 413, resp.data)
        body = resp.get_json()
        self.assertEqual(body["error"], "Request too large (max upload 50MB)")
        self.assertIn("request_id", body)

    def test_api_upload_base64_rejects_oversized_decoded_payload(self):
        payload = base64.b64encode(b"0123456789").decode("ascii")
        original_limit = mod.MAX_UPLOAD_SIZE
        try:
            mod.MAX_UPLOAD_SIZE = 4
            resp = self.client.post(
                "/api/upload/base64",
                json={"data": payload, "ext": "png"},
                headers=self.headers,
            )
        finally:
            mod.MAX_UPLOAD_SIZE = original_limit

        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("Too large", resp.get_json()["error"])

    def test_cli_image_turn_uses_sidecar_and_preserves_hermes_session(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        sidecar_calls = []
        cli_prompts = []

        def fake_sidecar(session, message, files, user_message=None, file_display_names=None):
            sidecar_calls.append({"message": message, "files": [f.name for f in files], "session_id": session.get("hermes_session_id")})
            session.setdefault("vision_assets", []).append({
                "id": "vis-1",
                "stored_as": image_path.name,
                "display_name": "shot.png",
                "mime_type": "image/png",
                "created_at": user_message["timestamp"],
                "source_message_index": len(session["messages"]) - 1,
                "source_message_timestamp": user_message["timestamp"],
                "last_analysis": {
                    "summary": "Login form screenshot",
                    "raw_text": "raw vision output",
                    "focus": message,
                    "analyzed_at": user_message["timestamp"],
                    "model": "vision-model",
                    "provider": "openai",
                },
            })
            user_message["sidecar_vision"] = {
                "used": True,
                "status": "ok",
                "asset_ids": ["vis-1"],
                "summary": "Login form screenshot",
                "analysis_mode": "sidecar",
                "reanalysis": False,
            }
            return {
                "overall_summary": "Login form screenshot",
                "images": [{"label": "shot.png", "summary": "Login form screenshot", "asset_id": "vis-1"}],
                "asset_ids": ["vis-1"],
                "reanalysis": False,
            }

        def fake_cli_prompt(session, prompt, request_id=None):
            cli_prompts.append({"session_id": session.get("hermes_session_id"), "prompt": prompt})
            return ("image turn ok", "hermes-session-1")

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_hermes_direct", return_value=("plain text ok", "hermes-session-1")), \
             patch.object(mod, "_run_sidecar_vision_analysis", side_effect=fake_sidecar), \
             patch.object(mod, "_call_hermes_prompt", side_effect=fake_cli_prompt), \
             patch.object(mod, "_call_api_server", side_effect=AssertionError("API replay should not be used")):
            first = self.client.post(
                "/api/chat",
                json={"message": "plain text"},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]
            second = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "session_id": session_id, "files": [image_path.name]},
                headers=self.headers,
            )

        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(len(sidecar_calls), 1)
        self.assertEqual(cli_prompts[0]["session_id"], "hermes-session-1")
        self.assertIn("Vision sidecar analysis", cli_prompts[0]["prompt"])
        session_meta = second.get_json()["session"]
        self.assertEqual(session_meta["transport_mode"], "cli")
        self.assertEqual(session_meta["continuity_mode"], "hermes_resume")
        self.assertTrue(session_meta["hermes_session_backed"])
        self.assertTrue(session_meta["last_turn_used_sidecar_vision"])
        persisted = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
        self.assertEqual(persisted["hermes_session_id"], "hermes-session-1")
        self.assertTrue(persisted["messages"][2]["sidecar_vision"]["used"])

    def test_mixed_text_and_image_followup_stays_cli_backed(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        call_session_ids = []

        def fake_call(session, message, files=None, request_id=None, file_display_names=None):
            call_session_ids.append(session.get("hermes_session_id"))
            return (f"cli:{message}", "hermes-session-1")

        def fake_sidecar(session, message, files, user_message=None, file_display_names=None):
            session.setdefault("vision_assets", []).append({
                "id": "vis-1",
                "stored_as": image_path.name,
                "display_name": "shot.png",
                "mime_type": "image/png",
                "created_at": user_message["timestamp"],
                "source_message_index": len(session["messages"]) - 1,
                "source_message_timestamp": user_message["timestamp"],
                "last_analysis": {"summary": "Screenshot", "raw_text": "Screenshot", "focus": message, "analyzed_at": user_message["timestamp"], "model": "vision-model", "provider": "openai"},
            })
            user_message["sidecar_vision"] = {
                "used": True,
                "status": "ok",
                "asset_ids": ["vis-1"],
                "summary": "Screenshot",
                "analysis_mode": "sidecar",
                "reanalysis": False,
            }
            return {"overall_summary": "Screenshot", "images": [{"label": "shot.png", "summary": "Screenshot", "asset_id": "vis-1"}], "asset_ids": ["vis-1"], "reanalysis": False}

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_hermes_direct", side_effect=fake_call), \
             patch.object(mod, "_run_sidecar_vision_analysis", side_effect=fake_sidecar), \
             patch.object(mod, "_call_hermes_prompt", return_value=("cli:image", "hermes-session-1")), \
             patch.object(mod, "_call_api_server", side_effect=AssertionError("API replay should not be used")):
            first = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "files": [image_path.name]},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]
            second = self.client.post(
                "/api/chat",
                json={"message": "Follow up", "session_id": session_id},
                headers=self.headers,
            )

        self.assertEqual(second.status_code, 200, second.data)
        session_meta = second.get_json()["session"]
        self.assertEqual(session_meta["transport_mode"], "cli")
        self.assertEqual(session_meta["continuity_mode"], "hermes_resume")
        self.assertFalse(session_meta["last_turn_used_sidecar_vision"])
        self.assertEqual(call_session_ids, ["hermes-session-1"])

    def test_followup_can_reanalyze_latest_screenshot(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        captured_requests = []

        def fake_completion(target, messages):
            captured_requests.append({"target": target, "messages": messages})
            return json.dumps({
                "overall_summary": "Settings screen",
                "images": [
                    {
                        "label": "shot.png",
                        "summary": "Settings screen",
                        "visible_text": ["General", "Advanced"],
                        "details": ["Sidebar on the left"],
                    }
                ],
            })

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_resolve_api_target", return_value={"base_url": "https://vision.example.test/v1", "model": "vision-model", "provider": "openai", "api_key": "secret"}), \
             patch.object(mod, "_chat_completion_request", side_effect=fake_completion), \
             patch.object(mod, "_call_hermes_prompt", return_value=("cli ok", "hermes-session-1")):
            first = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "files": [image_path.name]},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]

            second = self.client.post(
                "/api/chat",
                json={"message": "What does the screenshot from earlier say in the sidebar?", "session_id": session_id},
                headers=self.headers,
            )

        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(len(captured_requests), 2)
        followup_prompt = captured_requests[1]["messages"][0]["content"][0]["text"]
        self.assertIn("follow-up about an earlier screenshot", followup_prompt)
        self.assertTrue(second.get_json()["user_message"]["sidecar_vision"]["reanalysis"])
        self.assertTrue(second.get_json()["session"]["last_turn_used_sidecar_vision"])

    def test_api_transport_cannot_be_cancelled(self):
        mod._register_chat_request(
            "req-api",
            "sid-1",
            transport=mod.CHAT_TRANSPORT_API,
            cancel_supported=False,
        )
        resp = self.client.post("/api/chat/cancel", json={"request_id": "req-api"}, headers=self.headers)
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertIn("cannot be cancelled server-side", resp.get_json()["detail"])

    def test_image_attachment_is_rejected_when_vision_is_not_ready(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "vision unavailable")):
            resp = self.client.post(
                "/api/chat",
                json={"message": "describe", "files": [image_path.name]},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 400, resp.data)
        body = resp.get_json()
        self.assertEqual(body["error"], "Unsupported attachment selection")
        self.assertTrue(body["details"])

    def test_explicit_api_mode_still_works_when_enabled(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")

        with patch.object(mod, "_check_api_server", return_value=True), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_api_server", return_value="api ok"), \
             patch.object(mod, "_call_hermes_direct", side_effect=AssertionError("CLI should not be used in explicit API mode")), \
             patch.object(mod, "_call_hermes_prompt", side_effect=AssertionError("CLI sidecar path should not be used in explicit API mode")):
            resp = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "files": [image_path.name]},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        session_meta = resp.get_json()["session"]
        self.assertEqual(session_meta["transport_mode"], "api")
        self.assertEqual(session_meta["continuity_mode"], "local_replay")
        self.assertFalse(session_meta["hermes_session_backed"])

    def test_api_replay_is_limited_to_active_profile_segment(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"
        captured_requests = []

        def fake_call_api_server(session, messages, sid, files=None, prefer_vision=False, file_display_names=None):
            captured_requests.append({
                "profile": mod._selected_hermes_profile_name(),
                "messages": copy.deepcopy(messages),
                "prefer_vision": prefer_vision,
            })
            return f"api:{mod._selected_hermes_profile_name()}"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
               patch.object(mod, "_current_webui_token", return_value="test-token"), \
             patch.object(mod, "_check_api_server", return_value=True), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "")), \
             patch.object(mod, "_call_api_server", side_effect=fake_call_api_server), \
             patch.object(mod, "_call_hermes_direct", side_effect=AssertionError("CLI should not be used")), \
             patch.object(mod, "_call_hermes_prompt", side_effect=AssertionError("CLI prompt path should not be used")):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]

            first = self.client.post(
                "/api/chat",
                json={"message": "hello default", "session_id": session_id, "transport_preference": "api"},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)

            switched = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched.status_code, 200, switched.data)

            second = self.client.post(
                "/api/chat",
                json={"message": "hello leire", "session_id": session_id, "transport_preference": "api"},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)

            self.assertEqual(len(captured_requests), 2)
            self.assertEqual([entry["content"] for entry in captured_requests[0]["messages"]], ["hello default"])
            self.assertEqual([entry["content"] for entry in captured_requests[1]["messages"]], ["hello leire"])
            self.assertEqual(captured_requests[1]["profile"], "leire")
            self.assertEqual(second.get_json()["session"]["profile"], "leire")

    def test_api_replay_does_not_inherit_image_history_across_segments(self):
        hermes_root = Path(self.tmpdir.name) / ".hermes"
        profiles_dir = hermes_root / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "leire").mkdir()
        state_path = Path(self.tmpdir.name) / "webui_profile"
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        captured_requests = []

        def fake_call_api_server(session, messages, sid, files=None, prefer_vision=False, file_display_names=None):
            captured_requests.append({
                "profile": mod._selected_hermes_profile_name(),
                "messages": copy.deepcopy(messages),
                "prefer_vision": prefer_vision,
                "files": [path.name for path in (files or [])],
            })
            return f"api:{mod._selected_hermes_profile_name()}"

        with patch.object(mod, "HERMES_HOME", hermes_root), \
             patch.object(mod, "CONFIG_PATH", hermes_root / "config.yaml"), \
             patch.object(mod, "ENV_PATH", hermes_root / ".env"), \
             patch.object(mod, "SKILLS_DIR", hermes_root / "skills"), \
             patch.object(mod, "SESSIONS_DIR", hermes_root / "sessions"), \
             patch.object(mod, "BACKUP_DIR", hermes_root / "backups"), \
             patch.object(mod, "HERMES_PROFILES_DIR", profiles_dir), \
             patch.object(mod, "WEBUI_PROFILE_STATE_PATH", state_path), \
               patch.object(mod, "_current_webui_token", return_value="test-token"), \
             patch.object(mod, "_check_api_server", return_value=True), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_api_server", side_effect=fake_call_api_server), \
             patch.object(mod, "_call_hermes_direct", side_effect=AssertionError("CLI should not be used")), \
             patch.object(mod, "_call_hermes_prompt", side_effect=AssertionError("CLI prompt path should not be used")):
            created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]

            first = self.client.post(
                "/api/chat",
                json={"message": "describe image", "session_id": session_id, "transport_preference": "api", "files": [image_path.name]},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)

            switched = self.client.put(
                f"/api/chat/sessions/{session_id}/profile",
                json={"profile": "leire"},
                headers=self.headers,
            )
            self.assertEqual(switched.status_code, 200, switched.data)

            second = self.client.post(
                "/api/chat",
                json={"message": "plain text leire", "session_id": session_id, "transport_preference": "api"},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)

            self.assertEqual(len(captured_requests), 2)
            self.assertTrue(captured_requests[0]["prefer_vision"])
            self.assertFalse(captured_requests[1]["prefer_vision"])
            self.assertEqual([entry["content"] for entry in captured_requests[1]["messages"]], ["plain text leire"])

    def test_auto_transport_preference_is_not_sticky_to_last_transport(self):
        with patch.object(mod, "_check_api_server", return_value=True), \
             patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "")):
            plan = mod._plan_chat_request(
                {
                    "transport_mode": "cli",
                    "transport_preference": None,
                },
                [],
            )

        self.assertEqual(plan["transport"], "api")

    def test_sidecar_failure_does_not_silently_downgrade_thread(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_hermes_direct", return_value=("plain text ok", "hermes-session-1")), \
             patch.object(mod, "_run_sidecar_vision_analysis", side_effect=mod.ChatBackendError("vision sidecar failed")), \
             patch.object(mod, "_call_api_server", side_effect=AssertionError("API replay should not be used")):
            first = self.client.post(
                "/api/chat",
                json={"message": "plain text"},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]

            second = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "session_id": session_id, "files": [image_path.name]},
                headers=self.headers,
            )

        self.assertEqual(second.status_code, 502, second.data)
        self.assertEqual(second.get_json()["error"], "vision sidecar failed")
        persisted = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
        self.assertEqual(persisted["hermes_session_id"], "hermes-session-1")
        self.assertEqual(persisted["transport_mode"], "cli")
        self.assertEqual(persisted["continuity_mode"], "hermes_resume")
        self.assertEqual(len(persisted["messages"]), 2)

    def test_repo_env_token_is_accepted_without_process_env(self):
        repo_env = Path(self.tmpdir.name) / ".env"
        repo_env.write_text("HERMES_WEBUI_TOKEN=repo-token\n", encoding="utf-8")

        with patch.dict(mod.os.environ, {}, clear=True), \
             patch.object(mod, "REPO_ENV_PATH", repo_env), \
             patch.object(mod, "_run_hermes", return_value=SimpleNamespace(returncode=0, stdout="Hermes Agent 1.0", stderr="")), \
             patch.object(mod, "_gateway_status", return_value={"running": False, "pid": None, "status_text": "stopped", "raw": ""}):
            resp = self.client.get("/api/health", headers={"Authorization": "Bearer repo-token"})

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_hermes_env_token_is_accepted_without_process_or_repo_env(self):
        hermes_env = Path(self.tmpdir.name) / "hermes.env"
        hermes_env.write_text("HERMES_WEBUI_TOKEN=home-token\n", encoding="utf-8")

        with patch.dict(mod.os.environ, {}, clear=True), \
             patch.object(mod, "REPO_ENV_PATH", Path(self.tmpdir.name) / "missing.env"), \
             patch.object(mod, "ENV_PATH", hermes_env), \
             patch.object(mod, "_run_hermes", return_value=SimpleNamespace(returncode=0, stdout="Hermes Agent 1.0", stderr="")), \
             patch.object(mod, "_gateway_status", return_value={"running": False, "pid": None, "status_text": "stopped", "raw": ""}):
            resp = self.client.get("/api/health", headers={"Authorization": "Bearer home-token"})

        self.assertEqual(resp.status_code, 200, resp.data)

    def test_failed_chat_does_not_persist_fake_assistant_message(self):
        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", side_effect=mod.ChatBackendError("CLI exploded")):
            resp = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers)

        self.assertEqual(resp.status_code, 502, resp.data)
        body = resp.get_json()
        self.assertEqual(body["error"], "CLI exploded")
        self.assertEqual(list(mod.CHAT_DATA_DIR.glob("*.json")), [])

    def test_chat_persists_original_attachment_names(self):
        stored = mod.UPLOAD_FOLDER / "abc1234_notes.txt"
        stored.write_text("hello", encoding="utf-8")

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", return_value=("Processed file", "session-1")):
            resp = self.client.post(
                "/api/chat",
                json={
                    "message": "Read this",
                    "files": [{"stored_as": stored.name, "name": "notes.txt"}],
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        session_id = resp.get_json()["session_id"]
        persisted = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
        self.assertEqual(persisted["messages"][0]["files"], ["notes.txt"])

    def test_openrouter_target_uses_provider_specific_env_key(self):
        with patch.dict(mod.os.environ, {"OPENROUTER_API_KEY": "router-secret"}, clear=True), \
             patch.object(mod, "_normalized_model_config", return_value={
                 "default_provider": "openrouter",
                 "default_model": "text-model",
                 "vision": {
                     "provider": "openrouter",
                     "model": "vision-model",
                     "base_url": "https://openrouter.ai/api/v1",
                     "api_key": "",
                 },
             }):
            target = mod._resolve_api_target(prefer_vision=True)

        self.assertEqual(target["provider"], "openrouter")
        self.assertEqual(target["api_key"], "router-secret")

    def test_resolve_api_target_uses_linked_provider_profile(self):
        mod.cfg._config = {
            "model": {
                "default_profile": "router-prod",
                "default_provider": "openai",
                "default_model": "openai/gpt-5.4-mini",
                "base_url": "https://stale.example.test/v1",
                "api_key": "",
                "fallback_profile": "router-fallback",
                "fallback_provider": "openai",
                "fallback_model": "openai/gpt-4o-mini",
                "fallback_base_url": "https://stale.example.test/v1",
                "fallback_api_key": "",
            },
            "custom_providers": [
                {
                    "name": "router-prod",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-5.4-mini",
                    "api_key": "",
                },
                {
                    "name": "router-fallback",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-4o-mini",
                    "api_key": "",
                },
            ],
        }

        with patch.dict(mod.os.environ, {"OPENROUTER_API_KEY": "router-secret"}, clear=True):
            primary = mod._resolve_api_target()
            fallback = mod._resolve_fallback_api_target()

        self.assertEqual(primary["provider"], "openrouter")
        self.assertEqual(primary["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(primary["api_key"], "router-secret")
        self.assertEqual(fallback["provider"], "openrouter")
        self.assertEqual(fallback["model"], "openai/gpt-4o-mini")

    def test_model_roles_expose_implicit_profile_from_legacy_config(self):
        mod.cfg._config = {
            "model": {
                "provider": "openrouter",
                "default": "minimax/minimax-m2.7",
            },
            "auxiliary": {
                "vision": {
                    "provider": "openrouter",
                    "model": "qwen/qwen3.6-plus:free",
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key": "",
                }
            },
        }

        resp = self.client.get("/api/model-roles", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        profiles = data["profiles"]
        self.assertTrue(any(profile["name"] == "openrouter" for profile in profiles))
        self.assertEqual(data["roles"]["primary"]["profile"], "openrouter")
        self.assertEqual(data["roles"]["vision"]["profile"], "openrouter")

    def test_model_roles_preserve_declared_custom_provider_name_for_implicit_profile(self):
        mod.cfg._config = {
            "model": {
                "provider": "custom",
                "default": "qwen3.5:cloud",
                "base_url": "https://ollama.com/v1",
            }
        }

        resp = self.client.get("/api/model-roles", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        profiles = data["profiles"]
        self.assertTrue(any(profile["name"] == "custom" for profile in profiles))
        self.assertEqual(data["roles"]["primary"]["profile"], "custom")

    def test_resolve_api_target_defaults_known_provider_base_url(self):
        with patch.dict(mod.os.environ, {"OPENROUTER_API_KEY": "router-secret"}, clear=True):
            mod.cfg._config = {
                "model": {
                    "provider": "openrouter",
                    "default": "minimax/minimax-m2.7",
                }
            }
            target = mod._resolve_api_target()

        self.assertEqual(target["provider"], "openrouter")
        self.assertEqual(target["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(target["api_key"], "router-secret")

    def test_chat_completion_request_uses_resolved_target_base_url_and_api_key(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["authorization"] = req.headers.get("Authorization")
            captured["content_type"] = req.headers.get("Content-type")
            captured["timeout"] = timeout
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeHTTPResponse({
                "choices": [{"message": {"content": "vision ok"}}],
            })

        target = {
            "model": "vision-model",
            "provider": "custom",
            "base_url": "https://vision.example.test/v1",
            "api_key": "vision-secret",
        }

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._chat_completion_request(target, [{"role": "user", "content": "hello"}])

        self.assertEqual(result, "vision ok")
        self.assertEqual(captured["url"], "https://vision.example.test/v1/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer vision-secret")
        self.assertEqual(captured["content_type"], "application/json")
        self.assertEqual(captured["timeout"], mod.CHAT_REQUEST_TIMEOUT)
        self.assertEqual(captured["payload"]["model"], "vision-model")
        self.assertEqual(captured["payload"]["messages"], [{"role": "user", "content": "hello"}])

    def test_frontend_source_does_not_contain_python_unicode_escapes(self):
        source = (Path(mod.APP_ROOT) / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("\\U000", source)

    def test_provider_update_preserves_existing_secret_when_masked_value_is_sent(self):
        mod.cfg._config = {
            "custom_providers": [
                {
                    "name": "demo",
                    "base_url": "https://example.test/v1",
                    "model": "demo-model",
                    "api_key": "sk-real-secret",
                }
            ]
        }
        masked = mod.cfg.mask_secrets({"api_key": "sk-real-secret"})["api_key"]

        with patch.object(mod.cfg, "save", return_value=None):
            resp = self.client.put(
                "/api/providers/demo",
                json={
                    "base_url": "https://example.test/v2",
                    "model": "demo-model-2",
                    "api_key": masked,
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        provider = mod.cfg._config["custom_providers"][0]
        self.assertEqual(provider["api_key"], "sk-real-secret")
        self.assertEqual(provider["base_url"], "https://example.test/v2")
        self.assertEqual(provider["model"], "demo-model-2")

    def test_provider_update_syncs_linked_primary_role_fields(self):
        mod.cfg._config = {
            "model": {
                "default_profile": "router-prod",
                "default_provider": "openrouter",
                "default_model": "openai/gpt-5.4-mini",
                "base_url": "https://old.example.test/v1",
                "api_key": "sk-router-secret",
            },
            "custom_providers": [
                {
                    "name": "router-prod",
                    "provider": "openrouter",
                    "base_url": "https://old.example.test/v1",
                    "model": "openai/gpt-5.4-mini",
                    "api_key": "sk-router-secret",
                }
            ],
        }
        masked = mod.cfg.mask_secrets({"api_key": "sk-router-secret"})["api_key"]

        with patch.object(mod.cfg, "save", return_value=None):
            resp = self.client.put(
                "/api/providers/router-prod",
                json={
                    "provider": "openrouter",
                    "base_url": "https://new.example.test/v1",
                    "model": "openai/gpt-5.4",
                    "api_key": masked,
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(mod.cfg._config["model"]["base_url"], "https://new.example.test/v1")
        self.assertEqual(mod.cfg._config["model"]["default_provider"], "openrouter")
        self.assertEqual(mod.cfg._config["model"]["api_key"], "sk-router-secret")

    def test_provider_delete_is_rejected_when_profile_is_in_use(self):
        mod.cfg._config = {
            "model": {
                "default_profile": "demo",
                "default_provider": "openrouter",
                "default_model": "text-model",
            },
            "custom_providers": [
                {
                    "name": "demo",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "text-model",
                    "api_key": "",
                }
            ],
        }

        resp = self.client.delete("/api/providers/demo", headers=self.headers)
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertIn("Primary Chat", resp.get_json()["error"])

    def test_call_api_server_retries_with_fallback_role(self):
        seen_models = []

        def fake_completion(target, messages):
            seen_models.append(target["model"])
            if len(seen_models) == 1:
                raise mod.ChatBackendError("API server returned HTTP 503: upstream overloaded", status_code=503)
            return "fallback ok"

        with patch.object(mod, "_chat_completion_request", side_effect=fake_completion), \
             patch.object(mod, "_resolve_api_target", return_value={
                 "provider": "openrouter",
                 "model": "primary-model",
                 "base_url": "https://openrouter.ai/api/v1",
                 "api_key": "secret",
                 "routing_provider": "",
             }), \
             patch.object(mod, "_resolve_fallback_api_target", return_value={
                 "provider": "openrouter",
                 "model": "fallback-model",
                 "base_url": "https://openrouter.ai/api/v1",
                 "api_key": "secret",
                 "routing_provider": "",
             }):
            result = mod._call_api_server({}, [{"role": "user", "content": "hello"}], "sid-1")

        self.assertEqual(result, "fallback ok")
        self.assertEqual(seen_models, ["primary-model", "fallback-model"])

    def test_config_auxiliary_update_preserves_existing_secret_when_masked_value_is_sent(self):
        mod.cfg._config = {
            "auxiliary": {
                "vision": {
                    "provider": "openai",
                    "model": "vision-model",
                    "base_url": "https://vision.example.test/v1",
                    "api_key": "vision-secret",
                }
            }
        }
        masked = mod.cfg.mask_secrets({"api_key": "vision-secret"})["api_key"]

        with patch.object(mod.cfg, "save", return_value=None):
            resp = self.client.put(
                "/api/config/auxiliary",
                json={
                    "vision": {
                        "provider": "openai",
                        "model": "vision-model-2",
                        "base_url": "https://vision.example.test/v2",
                        "api_key": masked,
                    }
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        vision = mod.cfg._config["auxiliary"]["vision"]
        self.assertEqual(vision["api_key"], "vision-secret")
        self.assertEqual(vision["model"], "vision-model-2")
        self.assertEqual(vision["base_url"], "https://vision.example.test/v2")

    def test_chat_context_update_persists_and_survives_clear(self):
        workspace_root = Path(self.tmpdir.name) / "workspace"
        workspace_root.mkdir()
        source_doc = Path(self.tmpdir.name) / "brief.md"
        source_doc.write_text("# Brief\nhello\n", encoding="utf-8")

        created = self.client.post(
            "/api/chat/sessions",
            json={
                "folder_id": "Audit",
                "workspace_roots": [str(workspace_root)],
                "source_docs": [str(source_doc)],
            },
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200, created.data)
        session_id = created.get_json()["session_id"]

        cleared = self.client.post(f"/api/chat/sessions/{session_id}/clear", headers=self.headers)
        self.assertEqual(cleared.status_code, 200, cleared.data)
        meta = cleared.get_json()["session"]
        self.assertEqual(meta["folder_id"], "Audit")
        self.assertEqual(meta["workspace_roots"], [str(workspace_root)])
        self.assertEqual(meta["source_docs"], [str(source_doc)])

        persisted = json.loads((mod.CHAT_DATA_DIR / f"{session_id}.json").read_text())
        self.assertEqual(persisted["folder_id"], "Audit")
        self.assertEqual(persisted["workspace_roots"], [str(workspace_root)])
        self.assertEqual(persisted["source_docs"], [str(source_doc)])

    def test_invalid_chat_context_is_rejected(self):
        created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
        self.assertEqual(created.status_code, 200, created.data)
        session_id = created.get_json()["session_id"]

        resp = self.client.put(
            f"/api/chat/sessions/{session_id}/context",
            json={"workspace_roots": ["/definitely/missing/root"]},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.get_json()["error"], "Invalid chat context")

    def test_chat_session_transport_preference_round_trip(self):
        with patch.object(mod, "_chat_runtime_status", return_value=runtime_status_stub()), \
             patch.object(mod, "_check_api_server", return_value=True):
            created = self.client.post(
                "/api/chat/sessions",
                json={"transport_preference": "cli"},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200, created.data)
            session_id = created.get_json()["session_id"]
            self.assertEqual(created.get_json()["session"]["transport_preference"], "cli")

            updated = self.client.put(
                f"/api/chat/sessions/{session_id}/transport",
                json={"transport_preference": "api"},
                headers=self.headers,
            )
            self.assertEqual(updated.status_code, 200, updated.data)
            self.assertEqual(updated.get_json()["session"]["transport_preference"], "api")

            persisted = mod._load_session(session_id)
            self.assertEqual(persisted["transport_preference"], "api")

            reset = self.client.put(
                f"/api/chat/sessions/{session_id}/transport",
                json={"transport_preference": "auto"},
                headers=self.headers,
            )
            self.assertEqual(reset.status_code, 200, reset.data)
            self.assertEqual(reset.get_json()["session"]["transport_preference"], "auto")

            persisted = mod._load_session(session_id)
            self.assertIsNone(persisted["transport_preference"])

    def test_transport_preference_clamps_api_when_runtime_requires_cli(self):
        runtime = runtime_status_stub(
            requires_cli=True,
            cli_reason="Hermes CLI is required because memory and skills are active.",
            reasons=["Hermes memory is enabled for chat sessions.", "2 Hermes skills are enabled."],
        )

        with patch.object(mod, "_chat_runtime_status", return_value=runtime):
            created = self.client.post(
                "/api/chat/sessions",
                json={"transport_preference": "api"},
                headers=self.headers,
            )

        self.assertEqual(created.status_code, 200, created.data)
        session = created.get_json()["session"]
        self.assertEqual(session["transport_preference"], "cli")
        self.assertEqual(session["transport_notice"], "Hermes CLI is required because memory and skills are active.")

    def test_validated_transport_preference_allows_api_when_only_optional_cli_features_exist(self):
        raw = {
            "memory": {"memory_enabled": True, "user_profile_enabled": True},
            "platform_toolsets": {"cli": ["memory", "skills"]},
            "discord": {"require_mention": True},
        }
        skills = [{
            "name": "weather",
            "path": "weather",
            "enabled": True,
            "frontmatter": {},
        }]

        with patch.object(mod.cfg, "get_raw", return_value=raw), \
             patch.object(mod, "_discover_skill_entries", return_value=skills), \
             patch.object(mod, "_check_api_server", return_value=True):
            preference, notice = mod._validated_transport_preference("api")

        self.assertEqual(preference, "api")
        self.assertEqual(notice, "")

    def test_chat_request_forces_cli_when_runtime_requires_it(self):
        runtime = runtime_status_stub(
            requires_cli=True,
            cli_reason="Hermes CLI is required because memory is active.",
            reasons=["Hermes memory is enabled for chat sessions."],
        )

        with patch.object(mod, "_chat_runtime_status", return_value=runtime), \
             patch.object(mod, "_check_api_server", return_value=True), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "")), \
             patch.object(mod, "_call_hermes_direct", return_value=("cli ok", "hermes-session-1")), \
             patch.object(mod, "_call_api_server", side_effect=AssertionError("API replay should not be used when CLI is required")):
            resp = self.client.post(
                "/api/chat",
                json={"message": "hello", "transport_preference": "api"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        session = resp.get_json()["session"]
        self.assertEqual(session["transport_mode"], "cli")
        self.assertEqual(session["transport_notice"], "")

    def test_starter_pack_install_endpoint_runs_hermes_install(self):
        runtime = runtime_status_stub(starter_items=[])

        with patch.object(mod, "_run_hermes", return_value=SimpleNamespace(returncode=0, stdout="installed ok", stderr="")) as run_mock, \
             patch.object(mod, "_chat_runtime_status", return_value=runtime), \
             patch.object(mod, "_discover_skill_entries", side_effect=[[], []]), \
             patch.object(mod, "_record_skill_install_source", return_value=[]):
            resp = self.client.post("/api/starter-pack/weather/install", json={}, headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        run_mock.assert_called_once_with("skills", "install", "skills-sh/steipete/clawdis/weather", "--yes", timeout=300)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["candidate"]["identifier"], "skills-sh/steipete/clawdis/weather")
        self.assertIsNone(body["item"])
        self.assertTrue(body["setup_notes"])

    def test_starter_pack_install_endpoint_rejects_zero_exit_error_output(self):
        runtime = runtime_status_stub(starter_items=[])

        with patch.object(
            mod,
            "_run_hermes",
            return_value=SimpleNamespace(returncode=0, stdout="Fetching...\nError: Could not fetch skill", stderr=""),
        ), patch.object(mod, "_chat_runtime_status", return_value=runtime):
            resp = self.client.post("/api/starter-pack/weather/install", json={}, headers=self.headers)

        self.assertEqual(resp.status_code, 502, resp.data)
        self.assertIn("Could not fetch", resp.get_json()["error"])

    def test_starter_pack_install_endpoint_rejects_unknown_candidate(self):
        resp = self.client.post(
            "/api/starter-pack/weather/install",
            json={"identifier": "totally-unknown-skill"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.get_json()["error"], "Unsupported starter-pack install target")

    def test_skill_install_endpoint_runs_hermes_install_for_repo_identifier(self):
        with patch.object(mod, "_run_hermes", return_value=SimpleNamespace(returncode=0, stdout="installed ok", stderr="")) as run_mock, \
             patch.object(mod, "_discover_skill_entries", return_value=[{"name": "wondelai-skills", "enabled": True}]):
            resp = self.client.post(
                "/api/skills/install",
                json={"identifier": "wondelai/skills"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        run_mock.assert_called_once_with("skills", "install", "wondelai/skills", "--yes", timeout=300)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["identifier"], "wondelai/skills")
        self.assertEqual(body["install_mode"], "hermes")
        self.assertEqual(body["skills"][0]["name"], "wondelai-skills")

    def test_skill_install_endpoint_falls_back_to_github_repo_when_hermes_output_contains_error(self):
        fallback = {
            "mode": "github_repo",
            "source": "wondelai/skills",
            "requested_identifier": "wondelai/skills",
            "installed_paths": ["system-design", "clean-code"],
            "skipped_paths": [],
        }
        with patch.object(
            mod,
            "_run_hermes",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="Fetching: wondelai/skills\nError: Could not fetch 'wondelai/skills' from any source.",
                stderr="",
            ),
        ) as run_mock, patch.object(mod, "_install_skills_from_github_repo", return_value=fallback) as fallback_mock, patch.object(
            mod,
            "_discover_skill_entries",
            return_value=[{"name": "system-design", "enabled": True}, {"name": "clean-code", "enabled": True}],
        ):
            resp = self.client.post(
                "/api/skills/install",
                json={"identifier": "wondelai/skills"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        run_mock.assert_called_once_with("skills", "install", "wondelai/skills", "--yes", timeout=300)
        fallback_mock.assert_called_once_with("wondelai/skills")
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["install_mode"], "github_repo")
        self.assertEqual(body["fallback"]["installed_paths"], ["system-design", "clean-code"])

    def test_skill_install_endpoint_rejects_zero_exit_fetch_error_without_fallback(self):
        with patch.object(
            mod,
            "_run_hermes",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="Fetching: skills-sh/steipete/clawdis/weather\nError: Could not fetch target.",
                stderr="",
            ),
        ) as run_mock:
            resp = self.client.post(
                "/api/skills/install",
                json={"identifier": "skills-sh/steipete/clawdis/weather"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 502, resp.data)
        run_mock.assert_called_once_with("skills", "install", "skills-sh/steipete/clawdis/weather", "--yes", timeout=300)
        self.assertIn("Could not fetch", resp.get_json()["error"])

    def test_skill_install_endpoint_requires_identifier(self):
        resp = self.client.post(
            "/api/skills/install",
            json={},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertEqual(resp.get_json()["error"], "identifier is required")

    def test_capabilities_catalog_exposes_phased_rollout(self):
        resp = self.client.get("/api/capabilities", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertEqual(body["recommended_order"], ["Create Skill", "Create Integration", "Create Agent Preset"])
        types = {entry["id"]: entry for entry in body["types"]}
        self.assertEqual(types["skill"]["status"], "active")
        self.assertEqual(types["integration"]["phase"], "Phase 2")
        self.assertEqual(types["agent_preset"]["phase"], "Phase 3")

    def test_capabilities_preview_builds_skill_manifest_before_write(self):
        skill_root = Path(self.tmpdir.name) / "skills"

        with patch.object(mod, "SKILLS_DIR", skill_root), \
             patch.object(mod.shutil, "which", return_value=None):
            resp = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Google Workspace Helper",
                        "category": "productivity",
                        "description": "Handles Gmail and Calendar workflows.",
                        "instructions": "Use this skill when the task touches Google Workspace.",
                        "env_vars": [{
                            "key": "GOOGLE_CLIENT_ID",
                            "label": "Google Client ID",
                            "group": "Provider",
                            "description": "Used for OAuth setup.",
                        }],
                        "credential_files": [{
                            "path": "credentials/oauth-client.json",
                            "label": "OAuth Client",
                            "description": "Downloaded client secret file.",
                        }],
                        "required_commands": [{
                            "name": "uv",
                            "description": "Runs the helper script.",
                        }],
                        "include_scripts": True,
                        "include_references": True,
                    },
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["can_apply"])
        self.assertEqual(body["summary"]["slug"], "google-workspace-helper")
        skill = body["manifest"]["skill"]
        self.assertEqual(skill["path"], "google-workspace-helper")
        self.assertFalse(skill["setup"]["ready"])
        self.assertIn("missing env var GOOGLE_CLIENT_ID", skill["setup"]["issues"])
        self.assertIn("missing credential file credentials/oauth-client.json", skill["setup"]["issues"])
        self.assertIn("missing command uv", skill["setup"]["issues"])
        write_paths = [entry["path"] for entry in body["writes"]]
        self.assertIn(str(skill_root / "google-workspace-helper" / "SKILL.md"), write_paths)
        self.assertIn(str(skill_root / "google-workspace-helper" / "scripts"), write_paths)
        self.assertIn(str(skill_root / "google-workspace-helper" / "references"), write_paths)

    def test_capabilities_apply_writes_skill_and_source_metadata(self):
        skill_root = Path(self.tmpdir.name) / "skills"

        with patch.object(mod, "SKILLS_DIR", skill_root):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Weather Helper",
                        "description": "Checks weather conditions.",
                        "instructions": "Use this skill for weather questions.",
                        "include_references": True,
                    },
                },
                headers=self.headers,
            )
            preview_body = preview.get_json()
            resp = self.client.post(
                "/api/capabilities/apply",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Weather Helper",
                        "description": "Checks weather conditions.",
                        "instructions": "Use this skill for weather questions.",
                        "include_references": True,
                    },
                    "preview_token": preview_body["preview_token"],
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        created_dir = skill_root / "weather-helper"
        self.assertTrue((created_dir / "SKILL.md").exists())
        self.assertTrue((created_dir / "references").exists())
        source_meta = json.loads((created_dir / mod.SKILL_SOURCE_METADATA_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(source_meta["install_mode"], "webui_create")
        self.assertEqual(source_meta["display"], "Hermes Web UI")
        with patch.object(mod, "SKILLS_DIR", skill_root):
            skills = mod._discover_skill_entries()
        created = next(skill for skill in skills if skill["path"] == "weather-helper")
        self.assertEqual(created["source"]["install_mode"], "webui_create")

    def test_capabilities_apply_skill_preview_token_survives_second_rollover(self):
        skill_root = Path(self.tmpdir.name) / "skills"

        with patch.object(mod, "SKILLS_DIR", skill_root):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Second Boundary Skill",
                        "description": "Verifies preview token stability.",
                        "instructions": "Waits past a timestamp boundary before approval.",
                    },
                },
                headers=self.headers,
            )
            self.assertEqual(preview.status_code, 200, preview.data)
            preview_body = preview.get_json()
            time.sleep(max(0.0, 1.05 - (time.time() % 1)))
            resp = self.client.post(
                "/api/capabilities/apply",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Second Boundary Skill",
                        "description": "Verifies preview token stability.",
                        "instructions": "Waits past a timestamp boundary before approval.",
                    },
                    "preview_token": preview_body["preview_token"],
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue((skill_root / "second-boundary-skill" / "SKILL.md").exists())

    def test_capabilities_apply_cleans_up_partial_skill_on_write_failure(self):
        skill_root = Path(self.tmpdir.name) / "skills"

        with patch.object(mod, "SKILLS_DIR", skill_root):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "skill",
                    "draft": {
                        "name": "Cleanup Skill",
                        "description": "Should not leave partial files behind.",
                        "instructions": "Use this to verify cleanup behavior.",
                    },
                },
                headers=self.headers,
            )
            preview_body = preview.get_json()
            with patch.object(mod, "_write_skill_source_metadata", side_effect=RuntimeError("metadata write failed")):
                resp = self.client.post(
                    "/api/capabilities/apply",
                    json={
                        "type": "skill",
                        "draft": {
                            "name": "Cleanup Skill",
                            "description": "Should not leave partial files behind.",
                            "instructions": "Use this to verify cleanup behavior.",
                        },
                        "preview_token": preview_body["preview_token"],
                    },
                    headers=self.headers,
                )

        self.assertEqual(resp.status_code, 500, resp.data)
        self.assertFalse((skill_root / "cleanup-skill").exists())
        temp_dirs = list(skill_root.glob(".cleanup-skill.tmp-*"))
        self.assertEqual(temp_dirs, [])

    def test_env_api_includes_skill_declared_env_var_presets(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: custom-skill
description: Uses a custom API key.
metadata:
  hermes_web_ui:
    setup:
      env_vars:
        - key: CUSTOM_API_KEY
          label: Custom API Key
          group: Provider
          description: Used by the custom integration.
---
# Custom Skill
""",
            encoding="utf-8",
        )

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.get("/api/env", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        provider_presets = resp.get_json()["presets"]["Provider"]
        preset = next(item for item in provider_presets if item["key"] == "CUSTOM_API_KEY")
        self.assertEqual(preset["label"], "Custom API Key")
        self.assertEqual(preset["description"], "Used by the custom integration.")

    def test_capabilities_preview_builds_integration_manifest_before_write(self):
        mod.cfg._config = {
            "whatsapp": {},
        }

        resp = self.client.post(
            "/api/capabilities/preview",
            json={
                "type": "integration",
                "draft": {
                    "kind": "discord",
                    "config": {
                        "require_mention": True,
                        "auto_thread": True,
                    },
                    "env_vars": [{
                        "key": "DISCORD_TOKEN",
                        "label": "Discord Token",
                        "group": "Channel",
                        "description": "Bot token",
                        "value": "discord-secret",
                    }],
                },
            },
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["can_apply"])
        self.assertEqual(body["summary"]["kind"], "discord")
        manifest = body["manifest"]["integration"]
        self.assertTrue(manifest["configured"])
        self.assertTrue(manifest["readiness"]["ready"])
        write_paths = [entry["path"] for entry in body["writes"]]
        self.assertIn(str(mod.CONFIG_PATH), write_paths)
        self.assertIn(str(mod.ENV_PATH), write_paths)

    def test_capabilities_preview_blocks_when_integration_already_configured(self):
        mod.cfg._config = {
            "discord": {
                "require_mention": True,
            }
        }

        resp = self.client.post(
            "/api/capabilities/preview",
            json={
                "type": "integration",
                "draft": {
                    "kind": "discord",
                    "config": {
                        "require_mention": True,
                        "auto_thread": True,
                    },
                },
            },
            headers=self.headers,
        )

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertFalse(body["can_apply"])
        self.assertIn("already configured", " ".join(body["warnings"]))

    def test_capabilities_apply_writes_integration_config_and_env(self):
        tmp = Path(self.tmpdir.name)
        config_path = tmp / "config.yaml"
        env_path = tmp / ".env"
        backup_dir = tmp / "backups"
        mod.cfg._config = {
            "whatsapp": {},
        }

        with patch.object(mod, "CONFIG_PATH", config_path), \
             patch.object(mod, "ENV_PATH", env_path), \
             patch.object(mod, "BACKUP_DIR", backup_dir):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "integration",
                    "draft": {
                        "kind": "discord",
                        "config": {
                            "require_mention": True,
                            "auto_thread": True,
                        },
                        "env_vars": [{
                            "key": "DISCORD_TOKEN",
                            "group": "Channel",
                            "value": "discord-secret",
                        }],
                    },
                },
                headers=self.headers,
            )
            preview_body = preview.get_json()
            resp = self.client.post(
                "/api/capabilities/apply",
                json={
                    "type": "integration",
                    "draft": {
                        "kind": "discord",
                        "config": {
                            "require_mention": True,
                            "auto_thread": True,
                        },
                        "env_vars": [{
                            "key": "DISCORD_TOKEN",
                            "group": "Channel",
                            "value": "discord-secret",
                        }],
                    },
                    "preview_token": preview_body["preview_token"],
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        saved = mod.yaml.safe_load(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["discord"]["require_mention"], True)
        self.assertEqual(env_path.read_text(encoding="utf-8"), "DISCORD_TOKEN=discord-secret\n")

    def test_capabilities_apply_restores_env_when_integration_config_write_fails(self):
        tmp = Path(self.tmpdir.name)
        config_path = tmp / "config.yaml"
        env_path = tmp / ".env"
        backup_dir = tmp / "backups"
        env_path.write_text("DISCORD_TOKEN=old-secret\n", encoding="utf-8")
        mod.cfg._config = {}

        with patch.object(mod, "CONFIG_PATH", config_path), \
             patch.object(mod, "ENV_PATH", env_path), \
             patch.object(mod, "BACKUP_DIR", backup_dir):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "integration",
                    "draft": {
                        "kind": "discord",
                        "config": {
                            "require_mention": True,
                        },
                        "env_vars": [{
                            "key": "DISCORD_TOKEN",
                            "group": "Channel",
                            "value": "new-secret",
                        }],
                    },
                },
                headers=self.headers,
            )
            preview_body = preview.get_json()
            with patch.object(mod.cfg, "set", side_effect=RuntimeError("config write failed")):
                resp = self.client.post(
                    "/api/capabilities/apply",
                    json={
                        "type": "integration",
                        "draft": {
                            "kind": "discord",
                            "config": {
                                "require_mention": True,
                            },
                            "env_vars": [{
                                "key": "DISCORD_TOKEN",
                                "group": "Channel",
                                "value": "new-secret",
                            }],
                        },
                        "preview_token": preview_body["preview_token"],
                    },
                    headers=self.headers,
                )

        self.assertEqual(resp.status_code, 500, resp.data)
        self.assertEqual(env_path.read_text(encoding="utf-8"), "DISCORD_TOKEN=old-secret\n")

    def test_capabilities_preview_builds_agent_preset_manifest_before_write(self):
        skill_root = Path(self.tmpdir.name) / "skills"
        skill_dir = skill_root / "review-helper"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: review-helper
description: Reviews code carefully.
---
# Review Helper
""",
            encoding="utf-8",
        )
        mod.cfg._config = {
            "agent": {
                "max_turns": 90,
                "reasoning_effort": "medium",
            },
            "custom_providers": [
                {
                    "name": "router-prod",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-5.4-mini",
                    "api_key": "",
                }
            ],
            "model": {
                "default_profile": "router-prod",
                "default_provider": "openrouter",
                "default_model": "openai/gpt-5.4-mini",
            },
            "discord": {
                "require_mention": True,
            },
        }

        with patch.object(mod, "SKILLS_DIR", skill_root):
            resp = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "agent_preset",
                    "draft": {
                        "name": "review-preset",
                        "description": "Code review preset",
                        "system_prompt": "Review code changes carefully.",
                        "roles": {
                            "primary": {
                                "profile": "router-prod",
                                "model": "openai/gpt-5.4-mini",
                            },
                            "fallback": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                            "vision": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                        },
                        "skills": ["review-helper"],
                        "integrations": ["discord"],
                        "reasoning_effort": "high",
                        "max_turns": 40,
                    },
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["can_apply"])
        personality = body["manifest"]["personality"]
        self.assertEqual(personality["metadata"]["hermes_web_ui"]["capability_type"], "agent_preset")
        self.assertEqual(personality["metadata"]["hermes_web_ui"]["agent_defaults"]["reasoning_effort"], "high")
        self.assertEqual(body["summary"]["skill_count"], 1)

    def test_capabilities_apply_writes_agent_preset_to_agent_personalities(self):
        tmp = Path(self.tmpdir.name)
        config_path = tmp / "config.yaml"
        backup_dir = tmp / "backups"
        mod.cfg._config = {
            "agent": {
                "max_turns": 90,
                "reasoning_effort": "medium",
            },
            "custom_providers": [
                {
                    "name": "router-prod",
                    "provider": "openrouter",
                    "base_url": "https://openrouter.ai/api/v1",
                    "model": "openai/gpt-5.4-mini",
                    "api_key": "",
                }
            ],
            "model": {
                "default_profile": "router-prod",
                "default_provider": "openrouter",
                "default_model": "openai/gpt-5.4-mini",
            },
        }

        with patch.object(mod, "CONFIG_PATH", config_path), \
             patch.object(mod, "BACKUP_DIR", backup_dir):
            preview = self.client.post(
                "/api/capabilities/preview",
                json={
                    "type": "agent_preset",
                    "draft": {
                        "name": "review-preset",
                        "description": "Code review preset",
                        "system_prompt": "Review code changes carefully.",
                        "roles": {
                            "primary": {
                                "profile": "router-prod",
                                "model": "openai/gpt-5.4-mini",
                            },
                            "fallback": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                            "vision": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                        },
                        "skills": [],
                        "integrations": [],
                    },
                },
                headers=self.headers,
            )
            preview_body = preview.get_json()
            resp = self.client.post(
                "/api/capabilities/apply",
                json={
                    "type": "agent_preset",
                    "draft": {
                        "name": "review-preset",
                        "description": "Code review preset",
                        "system_prompt": "Review code changes carefully.",
                        "roles": {
                            "primary": {
                                "profile": "router-prod",
                                "model": "openai/gpt-5.4-mini",
                            },
                            "fallback": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                            "vision": {
                                "enabled": False,
                                "profile": "",
                                "model": "",
                            },
                        },
                        "skills": [],
                        "integrations": [],
                    },
                    "preview_token": preview_body["preview_token"],
                },
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        saved = mod.yaml.safe_load(config_path.read_text(encoding="utf-8"))
        preset = saved["agent"]["personalities"]["review-preset"]
        self.assertEqual(preset["system_prompt"], "Review code changes carefully.")
        self.assertEqual(preset["metadata"]["hermes_web_ui"]["capability_type"], "agent_preset")

    def test_agents_api_reads_legacy_top_level_personalities_and_returns_entries(self):
        mod.cfg._config = {
            "agent": {
                "max_turns": 90,
            },
            "personalities": {
                "legacy-reviewer": "Review code carefully.",
            },
        }

        resp = self.client.get("/api/agents", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        body = resp.get_json()
        self.assertIn("legacy-reviewer", body["personalities"])
        entry = next(item for item in body["entries"] if item["name"] == "legacy-reviewer")
        self.assertEqual(entry["system_prompt"], "Review code carefully.")
        self.assertEqual(entry["kind"], "personality")

    def test_capability_builder_js_uses_live_handlers_without_overescaped_quotes(self):
        source = (mod.APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn(
            "replace('<select', '<select onchange=\"updateCapabilityListItem(\\'env_vars\\', ' + index + ', \\'group\\', this.value)\"')",
            source,
        )
        self.assertIn(
            "replace('<textarea', '<textarea oninput=\"updateCapabilityDraftField(\\'description\\', this.value)\"')",
            source,
        )
        self.assertIn(
            "replace('<textarea', '<textarea oninput=\"updateCapabilityDraftField(\\'instructions\\', this.value)\"')",
            source,
        )

    def test_skills_inventory_js_exposes_category_filter_and_default_view_starter_pack_logic(self):
        source = (mod.APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("id=\"skill-category-filter\"", source)
        self.assertIn("Source / Origin", source)
        self.assertIn("function skillsInventoryIsDefaultView()", source)
        self.assertIn("openCreatedIntegrationInventory", source)
        self.assertIn("openCreatedAgentInventory", source)
        self.assertIn("integration-card-", source)
        self.assertIn("agent-card-", source)

    def test_channels_api_exposes_top_level_integrations(self):
        mod.cfg._config = {
            "discord": {
                "require_mention": True,
                "free_response_channels": "",
            },
            "whatsapp": {},
        }

        resp = self.client.get("/api/channels", headers=self.headers)
        self.assertEqual(resp.status_code, 200, resp.data)
        entries = {
            item["name"]: item for item in (resp.get_json().get("integrations") or [])
        }
        self.assertIn("discord", entries)
        self.assertIn("whatsapp", entries)
        self.assertEqual(entries["discord"]["kind"], "integration")
        self.assertTrue(entries["discord"]["configured"])
        self.assertFalse(entries["whatsapp"]["configured"])

    def test_channels_update_replaces_top_level_integration_block(self):
        mod.cfg._config = {
            "discord": {
                "require_mention": True,
                "auto_thread": True,
            }
        }

        with patch.object(mod.cfg, "save", return_value=None):
            resp = self.client.put(
                "/api/channels/discord",
                json={"free_response_channels": "general,alerts"},
                headers=self.headers,
            )

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            mod.cfg._config["discord"],
            {"free_response_channels": "general,alerts"},
        )

    def test_onboarding_accepts_top_level_integration_without_channels_map(self):
        mod.cfg._config = {
            "model": {
                "default_provider": "openrouter",
                "default_model": "openai/gpt-5.4-mini",
            },
            "discord": {
                "require_mention": True,
            },
        }

        with patch.dict(mod.os.environ, {"OPENROUTER_API_KEY": "router-secret", "HERMES_WEBUI_TOKEN": "test-token"}, clear=True):
            resp = self.client.get("/api/onboarding", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertNotIn("channel", resp.get_json()["missing"])

    def test_hooks_api_returns_empty_config_without_fake_webhook_block(self):
        mod.cfg._config = {}

        resp = self.client.get("/api/hooks", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.get_json(), {"config": {}})

    def test_config_manager_reloads_when_config_file_changes_on_disk(self):
        config_path = Path(self.tmpdir.name) / "config.yaml"
        config_path.write_text("agent:\n  personalities: {}\n", encoding="utf-8")

        with patch.object(mod, "CONFIG_PATH", config_path):
            manager = mod.ConfigManager()
            config_path.write_text(
                "agent:\n  personalities:\n    UI Test Preset:\n      system_prompt: Updated on disk\n",
                encoding="utf-8",
            )
            time.sleep(0.01)
            os.utime(config_path, None)
            data = manager.get_raw()

        self.assertIn("agent", data)
        self.assertIn("UI Test Preset", data["agent"]["personalities"])

    def test_config_manager_manual_override_does_not_reload_from_disk(self):
        config_path = Path(self.tmpdir.name) / "config.yaml"
        config_path.write_text("agent:\n  personalities: {}\n", encoding="utf-8")

        with patch.object(mod, "CONFIG_PATH", config_path):
            manager = mod.ConfigManager()
            manager._config = {"agent": {"personalities": {"Manual Preset": {"system_prompt": "manual"}}}}
            data = manager.get_raw()

        self.assertIn("Manual Preset", data["agent"]["personalities"])

    def test_compose_chat_turn_payload_includes_folder_and_source_doc_text(self):
        workspace_root = Path(self.tmpdir.name) / "repo"
        workspace_root.mkdir()
        source_doc = Path(self.tmpdir.name) / "requirements.md"
        source_doc.write_text("alpha\nbeta\n", encoding="utf-8")

        prompt, image_files = mod._compose_chat_turn_payload(
            {
                "folder_id": "Specs",
                "workspace_roots": [str(workspace_root)],
                "source_docs": [str(source_doc)],
            },
            "What should I build?",
            [],
            image_support=False,
        )

        self.assertEqual(image_files, [])
        self.assertIn("Folder: Specs", prompt)
        self.assertIn(str(workspace_root), prompt)
        self.assertIn(str(source_doc), prompt)
        self.assertIn("alpha\nbeta", prompt)
        self.assertIn("User message: What should I build?", prompt)

    def test_api_call_includes_chat_context_on_text_turns(self):
        workspace_root = Path(self.tmpdir.name) / "repo"
        workspace_root.mkdir()
        source_doc = Path(self.tmpdir.name) / "guide.md"
        source_doc.write_text("be concise", encoding="utf-8")
        captured = {}

        def fake_urlopen(req, timeout=300):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return FakeHTTPResponse({"choices": [{"message": {"content": "ok"}}]})

        with patch.object(mod, "_normalized_model_config", return_value={
            "default_model": "default-model",
            "base_url": "https://default.example.test/v1",
        }), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = mod._call_api_server(
                {
                    "folder_id": "Docs",
                    "workspace_roots": [str(workspace_root)],
                    "source_docs": [str(source_doc)],
                },
                [{"role": "user", "content": "Summarize"}],
                "sid-1",
            )

        self.assertEqual(result, "ok")
        content = captured["payload"]["messages"][-1]["content"]
        self.assertIn("Folder: Docs", content)
        self.assertIn(str(workspace_root), content)
        self.assertIn(str(source_doc), content)
        self.assertIn("User message: Summarize", content)

    def test_folder_endpoints_group_sessions_and_store_sources(self):
        source_doc = Path(self.tmpdir.name) / "source.md"
        source_doc.write_text("hello", encoding="utf-8")

        created = self.client.post(
            "/api/chat/folders",
            json={"title": "Audit Folder", "source_docs": [str(source_doc)]},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200, created.data)
        folder = created.get_json()["folder"]

        session_created = self.client.post(
            "/api/chat/sessions",
            json={"folder_id": folder["id"]},
            headers=self.headers,
        )
        self.assertEqual(session_created.status_code, 200, session_created.data)

        listed = self.client.get("/api/chat/folders", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.data)
        folders = listed.get_json()["folders"]
        summary = next(item for item in folders if item["id"] == folder["id"])
        self.assertEqual(summary["title"], "Audit Folder")
        self.assertEqual(summary["chat_count"], 1)
        self.assertIn(str(source_doc), summary["source_docs"])

    def test_folder_list_is_alphabetized_by_title(self):
        for title in ["Zulu", "alpha", "Bravo"]:
            created = self.client.post(
                "/api/chat/folders",
                json={"title": title},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200, created.data)

        listed = self.client.get("/api/chat/folders", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.data)
        titles = [folder["title"] for folder in listed.get_json()["folders"]]
        self.assertEqual(titles, ["alpha", "Bravo", "Zulu"])

    def test_folder_create_rejects_duplicate_title_case_insensitive(self):
        created = self.client.post(
            "/api/chat/folders",
            json={"title": "Audit Folder"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200, created.data)

        duplicate = self.client.post(
            "/api/chat/folders",
            json={"title": "audit folder"},
            headers=self.headers,
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.data)
        self.assertEqual(duplicate.get_json()["error"], "Folder name already exists")

    def test_folder_summaries_merge_legacy_title_sessions_into_unique_named_folder(self):
        created = self.client.post(
            "/api/chat/folders",
            json={"title": "Audit"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 200, created.data)
        folder_id = created.get_json()["folder"]["id"]

        legacy_session = mod._get_or_create_chat_session()
        legacy_session["folder_id"] = "Audit"
        legacy_session["updated"] = "2026-01-01T00:00:00"
        mod._write_session(legacy_session)

        listed = self.client.get("/api/chat/folders", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.data)
        folders = listed.get_json()["folders"]
        audit_folders = [folder for folder in folders if folder["title"] == "Audit"]
        self.assertEqual(len(audit_folders), 1)
        self.assertEqual(audit_folders[0]["id"], folder_id)
        self.assertEqual(audit_folders[0]["chat_count"], 1)

    def test_ensure_folder_exists_does_not_create_new_duplicate_title(self):
        now = "2026-01-01T00:00:00"
        mod._write_folder({"id": "dup-one", "title": "Folder", "created": now, "updated": now})
        mod._write_folder({"id": "dup-two", "title": "Folder", "created": now, "updated": now})

        ensured = mod._ensure_folder_exists("Folder")
        self.assertIn(ensured["id"], {"dup-one", "dup-two"})

        folders = mod._load_all_folders()
        self.assertEqual(sorted(folder["title"] for folder in folders.values()).count("Folder"), 2)

    def test_dedupe_legacy_folder_titles_merges_sources_and_sessions(self):
        mod._write_folder({
            "id": "older-folder",
            "title": "Folder",
            "created": "2026-01-01T00:00:00",
            "updated": "2026-01-01T00:00:00",
            "workspace_roots": ["/tmp/root-a"],
            "source_docs": ["/tmp/source-a.md"],
        })
        mod._write_folder({
            "id": "newer-folder",
            "title": "folder",
            "created": "2026-01-02T00:00:00",
            "updated": "2026-01-02T00:00:00",
            "workspace_roots": ["/tmp/root-b"],
            "source_docs": ["/tmp/source-b.md"],
        })
        session = mod._get_or_create_chat_session()
        session["folder_id"] = "newer-folder"
        session["updated"] = "2026-01-03T00:00:00"
        mod._write_session(session)

        report = mod._dedupe_legacy_folder_titles()

        self.assertTrue(report["changed"])
        self.assertEqual(report["merged_group_count"], 1)
        self.assertEqual(report["merged_groups"][0]["title"], "Folder")
        self.assertEqual(report["merged_groups"][0]["kept_id"], "older-folder")
        self.assertEqual(report["merged_groups"][0]["removed_ids"], ["newer-folder"])
        self.assertEqual(report["updated_session_ids"], [session["id"]])

        folders = mod._load_all_folders()
        self.assertIn("older-folder", folders)
        self.assertNotIn("newer-folder", folders)
        self.assertEqual(folders["older-folder"]["workspace_roots"], ["/tmp/root-a", "/tmp/root-b"])
        self.assertEqual(folders["older-folder"]["source_docs"], ["/tmp/source-a.md", "/tmp/source-b.md"])

        persisted = mod._load_session(session["id"])
        self.assertEqual(persisted["folder_id"], "older-folder")

    def test_folder_can_add_chat_transcript_as_source(self):
        folder_resp = self.client.post("/api/chat/folders", json={"title": "Folder"}, headers=self.headers)
        folder_id = folder_resp.get_json()["folder"]["id"]

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", return_value=("answer", "session-1")):
            chat_resp = self.client.post(
                "/api/chat",
                json={"message": "hello", "folder_id": folder_id},
                headers=self.headers,
            )
        self.assertEqual(chat_resp.status_code, 200, chat_resp.data)
        session_id = chat_resp.get_json()["session_id"]

        source_resp = self.client.post(
            f"/api/chat/folders/{folder_id}/sources/from-chat",
            json={"session_id": session_id},
            headers=self.headers,
        )
        self.assertEqual(source_resp.status_code, 200, source_resp.data)
        folder = source_resp.get_json()["folder"]
        self.assertEqual(folder["id"], folder_id)
        self.assertTrue(folder["source_docs"])
        self.assertTrue(Path(folder["source_docs"][-1]).exists())

    def test_folder_delete_moves_chats_to_ungrouped(self):
        folder_resp = self.client.post("/api/chat/folders", json={"title": "Delete Me"}, headers=self.headers)
        folder_id = folder_resp.get_json()["folder"]["id"]

        session_resp = self.client.post(
            "/api/chat/sessions",
            json={"folder_id": folder_id},
            headers=self.headers,
        )
        session_id = session_resp.get_json()["session_id"]

        deleted = self.client.delete(f"/api/chat/folders/{folder_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.assertEqual(deleted.get_json()["moved_session_count"], 1)
        self.assertEqual(deleted.get_json()["moved_session_ids"], [session_id])

        session = mod._load_session(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session["folder_id"], "")

        listed = self.client.get("/api/chat/folders", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertFalse(any(item["id"] == folder_id for item in listed.get_json()["folders"]))

    def test_chat_session_folder_update_assigns_existing_chat(self):
        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "disabled")), \
             patch.object(mod, "_call_hermes_direct", return_value=("answer", "session-1")):
            chat_resp = self.client.post("/api/chat", json={"message": "hello"}, headers=self.headers)
        self.assertEqual(chat_resp.status_code, 200, chat_resp.data)
        session_id = chat_resp.get_json()["session_id"]

        folder_resp = self.client.post("/api/chat/folders", json={"title": "Moved"}, headers=self.headers)
        folder_id = folder_resp.get_json()["folder"]["id"]

        moved = self.client.put(
            f"/api/chat/sessions/{session_id}/folder",
            json={"folder_id": folder_id},
            headers=self.headers,
        )
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.get_json()["session"]["folder_id"], folder_id)

    def test_chat_session_folder_update_canonicalizes_unique_title_match(self):
        folder_resp = self.client.post("/api/chat/folders", json={"title": "Moved"}, headers=self.headers)
        folder_id = folder_resp.get_json()["folder"]["id"]

        session_created = self.client.post("/api/chat/sessions", json={}, headers=self.headers)
        self.assertEqual(session_created.status_code, 200, session_created.data)
        session_id = session_created.get_json()["session_id"]

        moved = self.client.put(
            f"/api/chat/sessions/{session_id}/folder",
            json={"folder_id": "Moved"},
            headers=self.headers,
        )
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertEqual(moved.get_json()["session"]["folder_id"], folder_id)

        persisted = mod._load_session(session_id)
        self.assertEqual(persisted["folder_id"], folder_id)

    def test_cron_job_endpoints_store_managed_jobs(self):
        with patch.object(mod, "_crontab_available", return_value=True), \
             patch.object(mod, "_sync_cron_jobs_to_system") as sync_mock:
            created = self.client.post(
                "/api/cron/jobs",
                json={"name": "Daily", "schedule": "0 9 * * 1-5", "command": "echo hi"},
                headers=self.headers,
            )
            self.assertEqual(created.status_code, 200, created.data)
            job_id = created.get_json()["job"]["id"]
            self.assertTrue(sync_mock.called)

            listed = self.client.get("/api/cron/jobs", headers=self.headers)
            self.assertEqual(listed.status_code, 200, listed.data)
            jobs = listed.get_json()["jobs"]
            self.assertTrue(any(job["id"] == job_id and job["name"] == "Daily" for job in jobs))

    def test_build_hermes_update_payload_reports_selected_install(self):
        active_bin = Path(self.tmpdir.name) / "active-hermes"
        extra_bin = Path(self.tmpdir.name) / "other-hermes"
        repo_dir = Path(self.tmpdir.name) / "repo"
        active_bin.write_text("", encoding="utf-8")
        extra_bin.write_text("", encoding="utf-8")
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        payload = {
            "availability_status": "update_available",
            "checked_at": "2026-04-09T12:00:00Z",
            "source": {
                "remote": "origin",
                "branch": "main",
                "ref": "origin/main",
                "url": "https://github.com/NousResearch/hermes-agent.git",
                "official": True,
                "label": "GitHub origin/main",
            },
            "fetched": True,
            "fetch_error": "",
            "behind_commits": 12,
            "ahead_commits": 0,
            "local_commit": "abc12345",
            "latest_commit": "def67890",
            "latest_version": {
                "version": "0.8.0",
                "release_date": "2026.4.8",
                "display": "Hermes Agent v0.8.0 (2026.4.8)",
            },
            "worktree": {"tracked": 2, "untracked": 3, "total": 5, "sample": [" M foo.py"], "error": ""},
        }
        version_output = (
            "Hermes Agent v0.6.0 (2026.3.30)\n"
            f"Project: {repo_dir}\n"
            "Python: 3.12.3\n"
            "OpenAI SDK: 2.30.0\n"
        )

        with patch.object(mod, "_selected_hermes_candidate", return_value={
            "path": active_bin,
            "resolved_path": active_bin,
            "source": "active_gateway",
        }), \
             patch.object(mod, "_candidate_hermes_bins", return_value=[
                 {"path": active_bin, "resolved_path": active_bin, "source": "active_gateway"},
                 {"path": extra_bin, "resolved_path": extra_bin, "source": "user_local_bin"},
             ]), \
             patch.object(mod, "_run_hermes_with_bin", return_value=SimpleNamespace(stdout=version_output, stderr="", returncode=0)), \
             patch.object(mod, "_guess_repo_root", return_value=repo_dir), \
             patch.object(mod, "_get_repo_update_state", return_value=payload), \
             patch.object(mod, "_detect_managed_install", return_value=("", "")), \
             patch.object(mod, "_runtime_snapshot", return_value={
                 "status": "",
                 "started_at": "",
                 "finished_at": "",
                 "returncode": None,
                 "error": "",
                 "summary": "",
                 "logs": [],
                 "log_text": "",
                 "install_key": "",
                 "installed_version_before": "",
                 "installed_version_after": "",
             }):
            result = mod._build_hermes_update_payload(force_refresh=True)

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["availability_status"], "update_available")
        self.assertEqual(result["update_scope"], "release")
        self.assertEqual(result["bin_path"], str(active_bin))
        self.assertEqual(result["project_root"], str(repo_dir))
        self.assertEqual(result["latest_version"]["version"], "0.8.0")
        self.assertEqual(result["behind_commits"], 12)
        self.assertEqual(result["other_detected_bins"], [str(extra_bin)])
        self.assertTrue(result["can_update"])
        self.assertIn("gateway binary", result["selection_reason"].lower())

    def test_build_hermes_update_payload_marks_same_version_commit_drift_as_revision(self):
        managed_bin = Path(self.tmpdir.name) / "managed-hermes"
        repo_dir = Path(self.tmpdir.name) / "repo"
        managed_bin.write_text("", encoding="utf-8")
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        payload = {
            "availability_status": "update_available",
            "checked_at": "2026-04-09T12:00:00Z",
            "source": {
                "remote": "origin",
                "branch": "main",
                "ref": "origin/main",
                "url": "https://github.com/NousResearch/hermes-agent.git",
                "official": True,
                "label": "GitHub origin/main",
            },
            "fetched": True,
            "fetch_error": "",
            "behind_commits": 29,
            "ahead_commits": 0,
            "local_commit": "abc12345",
            "latest_commit": "def67890",
            "latest_version": {
                "version": "0.8.0",
                "release_date": "2026.4.8",
                "display": "Hermes Agent v0.8.0 (2026.4.8)",
            },
            "worktree": {"tracked": 0, "untracked": 0, "total": 0, "sample": [], "error": ""},
        }
        version_output = (
            "Hermes Agent v0.8.0 (2026.4.8)\n"
            f"Project: {repo_dir}\n"
            "Python: 3.11.15\n"
            "OpenAI SDK: 2.30.0\n"
        )

        with patch.object(mod, "_selected_hermes_candidate", return_value={
            "path": managed_bin,
            "resolved_path": managed_bin,
            "source": "managed_repo",
        }), \
             patch.object(mod, "_candidate_hermes_bins", return_value=[
                 {"path": managed_bin, "resolved_path": managed_bin, "source": "managed_repo"},
             ]), \
             patch.object(mod, "_run_hermes_with_bin", return_value=SimpleNamespace(stdout=version_output, stderr="", returncode=0)), \
             patch.object(mod, "_guess_repo_root", return_value=repo_dir), \
             patch.object(mod, "_get_repo_update_state", return_value=payload), \
             patch.object(mod, "_detect_managed_install", return_value=("", "")), \
             patch.object(mod, "_runtime_snapshot", return_value={
                 "status": "",
                 "started_at": "",
                 "finished_at": "",
                 "returncode": None,
                 "error": "",
                 "summary": "",
                 "logs": [],
                 "log_text": "",
                 "install_key": "",
                 "installed_version_before": "",
                 "installed_version_after": "",
             }):
            result = mod._build_hermes_update_payload(force_refresh=True)

        self.assertEqual(result["status"], "update_available")
        self.assertEqual(result["availability_status"], "update_available")
        self.assertEqual(result["update_scope"], "revision")
        self.assertIn("matches the latest released Hermes version", result["message"])
        self.assertEqual(result["latest_version"]["version"], "0.8.0")
        self.assertEqual(result["behind_commits"], 29)

    def test_api_hermes_update_requires_confirmation(self):
        resp = self.client.post("/api/hermes/update", json={}, headers=self.headers)
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertIn("confirmation", resp.get_json()["error"].lower())

    def test_api_hermes_update_returns_manual_command_when_direct_update_is_unsupported(self):
        with patch.object(mod, "_build_hermes_update_payload", return_value={
            "can_update": False,
            "manual_reason": "Managed install",
            "manual_command": "brew upgrade hermes-agent",
        }):
            resp = self.client.post("/api/hermes/update", json={"confirm": True}, headers=self.headers)

        self.assertEqual(resp.status_code, 409, resp.data)
        body = resp.get_json()
        self.assertEqual(body["manual_command"], "brew upgrade hermes-agent")
        self.assertIn("Managed install", body["error"])

    def test_api_hermes_update_starts_background_worker(self):
        thread_mock = MagicMock()
        payload = {
            "can_update": True,
            "manual_reason": "",
            "manual_command": "cd /tmp/repo && /tmp/hermes update",
            "install_key": "install-1",
            "installed_version": {"display": "Hermes Agent v0.6.0 (2026.3.30)"},
            "project_root": self.tmpdir.name,
        }

        with patch.object(mod, "_build_hermes_update_payload", return_value=payload), \
             patch.object(mod, "_invalidate_hermes_update_cache"), \
             patch.object(mod, "_set_update_runtime"), \
             patch.object(mod, "threading") as threading_mock:
            threading_mock.Thread.return_value = thread_mock
            resp = self.client.post("/api/hermes/update", json={"confirm": True}, headers=self.headers)

        self.assertEqual(resp.status_code, 202, resp.data)
        threading_mock.Thread.assert_called_once()
        thread_mock.start.assert_called_once()

    def test_update_ui_js_and_template_expose_banner_and_actions(self):
        template = (mod.APP_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (mod.APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("global-status-banner", template)
        self.assertIn("const HermesUpdate =", script)
        self.assertIn("/api/hermes/update-status", script)
        self.assertIn("openHermesUpdateConfirm", script)
        self.assertIn("renderHermesUpdateCard", script)
        self.assertIn("Update Hermes", script)

    def test_release_version_marker_matches_first_stable_release(self):
        template = (mod.APP_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        script = (mod.APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("UI v1.2.0", template)
        self.assertIn("const WEB_UI_VERSION = '1.2.0';", script)
        self.assertNotIn("v0.4.0", template)


if __name__ == "__main__":
    unittest.main()
