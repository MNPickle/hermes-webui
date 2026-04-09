# Configuration Reference

This page summarizes the main environment variables and paths used by Hermes Web UI.

## Where Config Lives

Hermes Web UI commonly uses:

- repo env file: `~/hermes-web-ui/.env`
- web UI venv: `~/hermes-web-ui/.venv`
- Hermes home: `~/.hermes`
- Hermes repo install: `~/.hermes/hermes-agent`
- Hermes config: `~/.hermes/config.yaml`
- Hermes sessions: `~/.hermes/sessions`

## Core Environment Variables

### Required

`HERMES_WEBUI_TOKEN`

- Required for API access to the web UI.
- Set this in `~/hermes-web-ui/.env` or export it before starting the app.

Example:

```bash
HERMES_WEBUI_TOKEN=replace-this-with-a-long-random-token
```

### Common Optional Overrides

`HERMES_WEBUI_HERMES_BIN`

- Explicit Hermes binary path for the web UI to manage.
- Usually not needed if Hermes is installed in the standard layout.

Example:

```bash
HERMES_WEBUI_HERMES_BIN=$HOME/.hermes/hermes-agent/venv/bin/hermes
```

`WEBUI_VENV`

- Override the Python virtualenv used by `start.sh`.
- Default is `~/hermes-web-ui/.venv`.

`PORT`

- Default listen port if you do not pass one to `./start.sh`.

`DEV`

- Set `DEV=1` to use the Flask development server instead of gunicorn.

## API and Model-Related Variables

`HERMES_API_URL`

- Overrides the URL the web UI uses for API replay and some compatibility probes.
- Default is `http://127.0.0.1:8642`.

`HERMES_API_KEY`

- Optional API key used by API-compatible provider flows.

`HERMES_USE_API`

- Re-enables the API replay/server mode path.
- By default the app prefers Hermes CLI mode because it currently has better session behavior and compression support.

## Runtime and Timeout Variables

`HERMES_CHAT_TIMEOUT`

- How long the web UI waits for a Hermes chat turn.
- Default is `300`.

`HERMES_WEBUI_MAX_REQUEST_BYTES`

- Maximum request body size before oversized uploads are rejected.

`HERMES_WEBUI_SLOW_REQUEST_MS`

- Threshold for slow-request logging.

## Update UI Variables

`HERMES_WEBUI_UPDATE_CACHE_SECONDS`

- Cache duration for update-status refreshes.
- Default is `600`.

`HERMES_WEBUI_UPDATE_LOG_LINES`

- Maximum number of update log lines retained in memory.
- Default is `400`.

## Gunicorn Variables

`start.sh` supports these gunicorn-related settings:

- `GUNICORN_TIMEOUT`
- `GUNICORN_TIMEOUT_HEADROOM`
- `GUNICORN_WORKERS`
- `GUNICORN_GRACEFUL_TIMEOUT`
- `GUNICORN_KEEPALIVE`
- `GUNICORN_MAX_REQUESTS`
- `GUNICORN_MAX_REQUESTS_JITTER`
- `GUNICORN_LOG_LEVEL`
- `GUNICORN_CONFIG`
- `GUNICORN_WORKER_TMP`

Defaults are chosen inside `start.sh` for local use.

## Example `.env`

```bash
HERMES_WEBUI_TOKEN=replace-this-with-a-long-random-token
# HERMES_WEBUI_HERMES_BIN=/home/your-user/.hermes/hermes-agent/venv/bin/hermes
# HERMES_API_URL=http://127.0.0.1:8642
# HERMES_USE_API=false
```

## Related References

- [Install Guide](INSTALL.md)
- [Usage Guide](USAGE.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Deployment Notes](../DEPLOYMENT_READY.md)
