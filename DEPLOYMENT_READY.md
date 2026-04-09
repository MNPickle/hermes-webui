# Hermes Web UI - Deployment Status

## Production Status

The web UI is hardened enough for local deployment, but image chat and a few deployment checks still depend on correct Hermes/provider configuration.

Recent runtime improvements in this repo:
- CLI-backed chats now stay Hermes-session-backed even when a screenshot turn uses sidecar vision.
- The UI shows when a chat is Hermes-session-backed versus local replay only, and flags turns that used sidecar vision.
- Stop/cancel is only offered for real Hermes CLI subprocesses; API/vision requests no longer pretend to be cancellable.
- `start.sh` now launches from the repo it lives in rather than assuming `~/hermes-web-ui`.

---

## Quick Start

### 1. Install Dependencies
```bash
cd ~/hermes-web-ui
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 2. Set Authentication Token
```bash
# Option A: export it in your shell
export HERMES_WEBUI_TOKEN=your-secure-random-token-here

# Option B: store it in ~/hermes-web-ui/.env for app startup
cat > .env <<'EOF'
HERMES_WEBUI_TOKEN=your-secure-random-token-here
EOF
```

### 3. Start the Server
```bash
# Production mode (recommended)
./start.sh 5000

# Development mode
DEV=1 ./start.sh 5000

# Or run gunicorn directly from the repo root
HERMES_WEBUI_HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes" \
  ./.venv/bin/gunicorn --chdir "$(pwd)" --bind 127.0.0.1:5000 --workers 2 --worker-tmp-dir /dev/shm app:app
```

### 4. Access the UI
1. Open browser to `http://127.0.0.1:5000/`
2. When prompted, enter your token: `your-secure-random-token-here`
3. Token is saved in browser localStorage for future visits

---

## Completed Fixes

### Phase 1: Security & Infrastructure ✓
- **Fix 1**: requirements.txt with pinned dependencies
- **Fix 2**: Token-based authentication on all /api/* routes
- **Fix 3**: XSS hardening in chat rendering
- **Fix 4**: Path traversal protection for file uploads

### Phase 2: Stability ✓
- **Fix 5**: Gunicorn production server (replaces Flask dev server)
- **Fix 6**: Structured logging (startup, errors, auth failures)
- **Fix 7**: Simple in-memory rate limiting (60 req/min per IP)

### Phase 3: UX Cleanup ✓
- **Fix 8**: Removed non-functional Plugins menu item
- **Fix 9**: Removed non-functional model selector from chat
- **Fix 10**: Removed non-functional global search bar
- **Fix 11**: Removed non-functional chat mode tabs
- **Fix 12**: Frontend authentication token handling
- **Fix 13**: Updated Hermes executable and sessions paths

---

## Verification Results

All core endpoints tested and working:
- ✓ /api/health - Returns gateway status and version
- ✓ /api/system - Returns system information
- ✓ /api/config - Returns configuration (masked secrets)
- ✓ /api/env - Returns environment variables (masked)
- ✓ /api/providers - Returns provider list
- ✓ /api/models - Returns available models
- ✓ /api/agents - Returns agent configurations
- ✓ /api/skills - Returns 27+ skills from ~/.hermes/skills
- ✓ /api/channels - Returns channel configurations
- ✓ /api/sessions - Returns 27+ session files from ~/.hermes/sessions
- ✓ /api/hooks - Returns webhook configurations
- ✓ /api/logs - Returns Hermes logs
- ✓ /api/tools - Returns tool list
- ✓ /api/onboarding - Returns onboarding status
- ✓ /api/chat/status - Returns chat readiness and image capability status

**Authentication**: Working correctly
- 401 responses for missing/invalid tokens
- 200 responses with valid Bearer token

**Rate Limiting**: Working correctly
- Chat endpoint limited to 60 requests/minute per IP
- 429 responses when limit exceeded

**Path Resolution**: Working correctly
- Hermes binary: /home/pickle/.local/bin/hermes ✓
- Sessions directory: /home/pickle/.hermes/sessions ✓
- Config file: /home/pickle/.hermes/config.yaml ✓

---

## Security Features

1. **Authentication**: All API routes require Bearer token
2. **CORS**: Restricted to localhost origins only
3. **XSS Protection**: Sanitized output in chat rendering
4. **Path Traversal**: Secure file upload handling
5. **Rate Limiting**: Basic DoS protection
6. **Logging**: Security events and auth failures logged

---

## Image Chat Requirements

For pasted screenshots to work end-to-end, all of these must be true:

1. A vision-capable auxiliary model is configured in Hermes.
2. An OpenAI-compatible API endpoint is reachable.
3. The API endpoint accepts the configured model and API key.

This repo now helps with the repo-side pieces:
- the Providers screen shows screenshot readiness
- the Providers screen lets you edit the Hermes `auxiliary.vision` config
- the sidecar vision path probes generic OpenAI-compatible APIs, not just `/health`
- image-only pasted screenshots are analyzed through sidecar vision and then bridged back into the Hermes CLI session
- follow-up turns can re-analyze the latest screenshot instead of silently downgrading the chat into API replay

Minimum configuration:
```bash
# App auth
export HERMES_WEBUI_TOKEN=your-secure-token

# OpenAI-compatible image chat endpoint
export HERMES_API_URL=https://your-api.example.com/v1
export HERMES_API_KEY=your-api-key
```

Then set `auxiliary.vision.model` in Hermes to your image-capable model, either in the Providers screen or in `~/.hermes/config.yaml`.

## Production Deployment

### Environment Variables
```bash
# Required
export HERMES_WEBUI_TOKEN=your-secure-token

# Optional for OpenAI-compatible API mode
export HERMES_API_URL=https://your-api.example.com/v1
export HERMES_API_KEY=your-api-key
export API_SERVER_KEY=your-api-key
export HERMES_USE_API=true
```

Port is set with:
```bash
./start.sh 5000
```

### Running as a Service (systemd)
Create `/etc/systemd/system/hermes-webui.service`:
```ini
[Unit]
Description=Hermes Web UI
After=network.target

[Service]
Type=simple
User=pickle
WorkingDirectory=/home/pickle/hermes-web-ui
Environment="HERMES_WEBUI_TOKEN=your-secure-token"
Environment="HERMES_WEBUI_HERMES_BIN=/home/pickle/.hermes/hermes-agent/venv/bin/hermes"
Environment="PATH=/home/pickle/hermes-web-ui/.venv/bin:/usr/bin:/bin"
ExecStart=/home/pickle/hermes-web-ui/start.sh 5000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable hermes-webui
sudo systemctl start hermes-webui
```

---

## Known Limitations

1. **In-Memory Rate Limiting**: Doesn't persist across restarts or work across multiple gunicorn workers
2. **No HTTPS**: Should be behind nginx/caddy with SSL for external access
3. **Single Token**: No per-user authentication or role-based access control
4. **Vision Depends on Provider Setup**: Screenshot paste only works after a real image-capable API/model is configured
5. **Basic Logging**: No log rotation or structured JSON output
6. **Voice Input**: Browser speech-to-text is supported when available; raw audio upload is still unsupported
7. **API Requests Are Not Server-Cancellable**: The UI now reflects this honestly by only showing Stop for CLI-backed requests

These are acceptable for a local admin tool on localhost.

---

## Next Steps (Optional)

If you want to enhance further:
- Add HTTPS via nginx reverse proxy
- Implement Redis-based rate limiting for multi-worker setup
- Add per-user authentication with JWT
- Implement chat streaming with SSE/WebSocket
- Add log rotation with logrotate
- Create systemd service unit
- Add health check monitoring

Treat the current state as **deployable with configuration checks**, not “set and forget” production-ready.

---

## Support

If issues occur:
1. Check logs: `journalctl -u hermes-webui -f` (if using systemd)
2. Or check stdout: `tail -f /tmp/webui.log`
3. Verify token: `echo $HERMES_WEBUI_TOKEN`
4. Test backend auth: `curl -H "Authorization: Bearer $HERMES_WEBUI_TOKEN" http://127.0.0.1:5000/api/health`
5. Check browser localStorage has token: Open dev tools → Application → Local Storage
6. Run repo smoke tests: `./.venv/bin/python -m unittest discover -s tests -q`

---

Generated: 2026-04-07
Release: v1.2.0 (Deployment Checklist)
