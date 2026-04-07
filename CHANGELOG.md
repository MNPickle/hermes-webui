# Changelog

All notable changes to this project will be documented in this file.

## v1.0.0 - 2026-04-07

First official stable release of the Hermes Web UI.

- Hardened local deployment with Gunicorn startup/runtime improvements and clearer deployment guidance.
- Preserved provider and config secrets when masked values are resubmitted from the UI.
- Made gateway/chat behavior more honest by showing stopped-state accurately and surfacing API replay fallback reasons.
- Improved image chat continuity so image-backed chats stay on the API replay path across follow-up turns.
- Expanded smoke coverage for chat continuity, service controls, uploads, and broken UI literal regressions.
- Fixed broken Unicode icon literals that leaked raw `U000...` strings into the interface.
- Ignored local runtime artifacts such as `run/` and `.codex` to keep future publishes clean.
