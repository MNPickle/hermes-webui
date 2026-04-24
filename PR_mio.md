# PR Draft

## Title

Add profile-aware Hermes runtime selection to the web UI

## Summary

This PR adds profile-aware runtime selection to Hermes Web UI so the portal can switch between Hermes profiles such as `default` and `leire` without changing Hermes backend state.

The web UI now resolves config, environment variables, sessions, gateway actions, and CLI chat execution from the selected profile home. It also exposes a Settings UI control to switch the active portal profile.

## What Changed

- Added backend runtime profile selection endpoints.
- Made config and env resolution profile-aware.
- Made Hermes CLI chat and gateway service actions use the selected `HERMES_HOME`.
- Added a Settings card in the frontend to choose the active Hermes profile.
- Improved gateway status parsing so systemd-managed gateways are detected correctly.
- Fixed API server URL resolution so it follows the selected profile and derives the URL from `API_SERVER_HOST` and `API_SERVER_PORT` when needed.
- Added smoke tests covering profile switching and API URL updates.

## Files Changed

- `app.py`
- `static/app.js`
- `tests/test_smoke.py`

## Testing

Validated with focused smoke tests:

```bash
cd /root/hermes-web-ui
source .venv/bin/activate
python -m unittest \
  tests.test_smoke.HermesWebUISmokeTests.test_runtime_profile_switch_changes_config_source \
  tests.test_smoke.HermesWebUISmokeTests.test_runtime_profile_switch_changes_chat_status_api_url \
  tests.test_smoke.HermesWebUISmokeTests.test_chat_status_exposes_readiness_details \
  tests.test_smoke.HermesWebUISmokeTests.test_env_api_returns_metadata_and_presets \
  tests.test_smoke.HermesWebUISmokeTests.test_env_api_post_writes_plain_unquoted_value \
  tests.test_smoke.HermesWebUISmokeTests.test_env_api_put_updates_without_single_quotes
```

## Notes

- This PR does not change Hermes' own root `active_profile` file.
- The portal profile is stored independently for the web UI runtime.
- The branch for this work is `mio`.