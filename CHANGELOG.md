# Changelog

All notable changes to this project will be documented in this file.

## v1.2.0 - 2026-04-09

- Added Hermes update monitoring with installed-version detection, official upstream checks, and explicit state handling for checking, available, in-progress, failed, and unknown-latest cases.
- Added in-app Hermes update actions with confirmation, live progress/logging, and exact manual commands when direct updating is not supported for the active install method.
- Added a persistent update card and banner so the dashboard and service views clearly show installed version, update status, update source, and manual remediation steps.
- Aligned Hermes Web UI with the current Hermes installer layout by preferring the repo-managed `~/.hermes/hermes-agent` install and ignoring stale legacy gateway records.
- Cleaned up browser validation tooling and deployment guidance so the web UI uses its repo-local virtualenv while Hermes itself is managed from the official repo install.

## v1.1.0 - 2026-04-07

- Kept Hermes CLI as the canonical browser-chat backend, even when image turns use vision.
- Added a sidecar vision bridge that analyzes screenshots through the configured vision target, injects structured text back into the same Hermes CLI session, and preserves `hermes_session_id`.
- Added session and turn metadata for sidecar vision so follow-up questions can re-analyze earlier screenshots without silently switching the chat into replay mode.
- Updated the chat UI to show Hermes-session-backed vs local replay only, plus per-turn sidecar vision usage.
- Expanded smoke coverage for CLI continuity across mixed text and image turns, explicit API fallback mode, and sidecar failure handling.

## v1.0.0 - 2026-04-07

First official stable release of the Hermes Web UI.

- Hardened local deployment with Gunicorn startup/runtime improvements and clearer deployment guidance.
- Preserved provider and config secrets when masked values are resubmitted from the UI.
- Made gateway/chat behavior more honest by showing stopped-state accurately and surfacing API replay fallback reasons.
- Improved image chat continuity so image-backed chats stay on the API replay path across follow-up turns.
- Expanded smoke coverage for chat continuity, service controls, uploads, and broken UI literal regressions.
- Fixed broken Unicode icon literals that leaked raw `U000...` strings into the interface.
- Ignored local runtime artifacts such as `run/` and `.codex` to keep future publishes clean.
