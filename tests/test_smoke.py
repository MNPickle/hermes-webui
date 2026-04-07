import base64
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_chat_status_exposes_readiness_details(self):
        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_api_server_probe", return_value=(False, "models probe returned HTTP 401", None)), \
             patch.object(mod, "_image_attachment_support_status", return_value=(False, "OpenAI-compatible image chat API is not reachable")), \
             patch.object(mod, "_vision_configured", return_value=(True, "")), \
             patch.object(mod, "_resolve_api_target", return_value={"base_url": "https://vision.example.test/v1", "model": "vision-model", "api_key": "secret"}):
            resp = self.client.get("/api/chat/status", headers=self.headers)

        self.assertEqual(resp.status_code, 200, resp.data)
        data = resp.get_json()
        self.assertIn("readiness", data)
        self.assertEqual(data["readiness"]["vision_api_url"], "https://vision.example.test/v1")
        self.assertEqual(data["readiness"]["vision_model"], "vision-model")
        self.assertFalse(data["readiness"]["screenshots_ready"])
        self.assertEqual(data["request_lifecycle"]["server_timeout_seconds"], mod.CHAT_SERVER_TIMEOUT)
        self.assertEqual(data["limits"]["max_upload_bytes"], mod.MAX_UPLOAD_SIZE)
        self.assertEqual(data["limits"]["max_request_body_bytes"], mod.MAX_REQUEST_BODY_SIZE)

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

    def test_image_session_stays_on_api_replay_for_followups(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        api_calls = []

        def fake_api(session, messages, session_id, files=None, prefer_vision=False, file_display_names=None):
            api_calls.append({
                "session_id": session_id,
                "folder_id": session.get("folder_id"),
                "messages": [m["content"] for m in messages],
                "files": [f.name for f in (files or [])],
                "prefer_vision": prefer_vision,
            })
            return f"api:{len(api_calls)}"

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_api_server", side_effect=fake_api), \
             patch.object(mod, "_call_hermes_direct", side_effect=AssertionError("CLI path should not be used after API replay starts")):
            first = self.client.post(
                "/api/chat",
                json={"message": "Look at this", "files": [image_path.name]},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]
            self.assertEqual(first.get_json()["session"]["transport_mode"], "api")
            self.assertEqual(first.get_json()["session"]["continuity_mode"], "local_replay")

            second = self.client.post(
                "/api/chat",
                json={"message": "Follow up", "session_id": session_id},
                headers=self.headers,
            )
            self.assertEqual(second.status_code, 200, second.data)
            self.assertEqual(second.get_json()["session"]["transport_mode"], "api")
            self.assertEqual(len(api_calls), 2)
            self.assertEqual(api_calls[1]["files"], [])
            self.assertTrue(api_calls[1]["prefer_vision"])

    def test_cli_session_switch_to_api_replay_sets_transport_notice(self):
        image_path = mod.UPLOAD_FOLDER / "shot.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nstub")

        with patch.object(mod, "_check_api_server", return_value=False), \
             patch.object(mod, "_image_attachment_support_status", return_value=(True, "")), \
             patch.object(mod, "_call_hermes_direct", return_value=("cli ok", "hermes-session-1")), \
             patch.object(mod, "_call_api_server", return_value="api ok"):
            first = self.client.post(
                "/api/chat",
                json={"message": "plain text"},
                headers=self.headers,
            )
            self.assertEqual(first.status_code, 200, first.data)
            session_id = first.get_json()["session_id"]

            second = self.client.post(
                "/api/chat",
                json={"message": "look at this", "session_id": session_id, "files": [image_path.name]},
                headers=self.headers,
            )

        self.assertEqual(second.status_code, 200, second.data)
        session_meta = second.get_json()["session"]
        self.assertEqual(session_meta["transport_mode"], "api")
        self.assertEqual(session_meta["continuity_mode"], "local_replay")
        self.assertIn("switched to API replay", session_meta["transport_notice"])

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


if __name__ == "__main__":
    unittest.main()
