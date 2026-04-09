# Install Guide

This guide covers the normal install path for Hermes Web UI on WSL2 or Linux.

## Prerequisites

- Hermes Agent installed in the current standard layout
- `git`
- `python3`
- a shell that can run `bash`

If you are starting fresh, install Hermes first:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc
```

## 1. Verify Hermes

Make sure the `hermes` command works before installing the web UI:

```bash
which hermes
hermes --version
```

Expected layout for a standard install:

- Hermes repo: `~/.hermes/hermes-agent`
- Hermes binary: `~/.hermes/hermes-agent/venv/bin/hermes`
- PATH launcher: `~/.local/bin/hermes`

## 2. Clone This Repo

```bash
git clone https://github.com/MNPickle/hermes-webui.git ~/hermes-web-ui
cd ~/hermes-web-ui
```

If you already cloned it, just `cd ~/hermes-web-ui`.

## 3. Create the Web UI Virtualenv

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

The web UI uses its own repo-local virtualenv at `~/hermes-web-ui/.venv`.

## 4. Set the Web UI Token

Copy the example file:

```bash
cp .env.example .env
```

Then edit `.env` and set a real token:

```bash
HERMES_WEBUI_TOKEN=replace-this-with-a-long-random-token
```

This token is required for browser access to the UI API.

## 5. Start the Web UI

Recommended:

```bash
cd ~/hermes-web-ui
./start.sh 5000
```

Development mode:

```bash
cd ~/hermes-web-ui
DEV=1 ./start.sh 5000
```

## 6. Open the Browser

Open:

```text
http://127.0.0.1:5000/
```

When prompted, paste the same token you set in `.env`.

## What `start.sh` Detects

By default the launcher will:

- use `~/hermes-web-ui/.venv` for the web UI runtime
- prefer `~/.hermes/hermes-agent/venv/bin/hermes` as the Hermes binary
- fall back to `WEBUI_VENV` or `HERMES_WEBUI_HERMES_BIN` if you override them

## Daily Start Commands

Once installed, your normal WSL routine is:

```bash
wsl
cd ~/hermes-web-ui
./start.sh 5000
```

## Optional: Run as a Service

If you want systemd or a more production-style deployment, use the examples in [DEPLOYMENT_READY.md](../DEPLOYMENT_READY.md).
