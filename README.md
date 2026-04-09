# Hermes Web UI

Hermes Web UI is a local admin and chat interface for [Hermes Agent](https://github.com/NousResearch/hermes-agent). It gives you a browser-based way to inspect gateway health, manage config and providers, work with chats and folders, review logs, and monitor or trigger Hermes updates without leaving your terminal workflow.

This repo is intended to sit alongside a normal Hermes install. The current preferred Hermes layout is:

- Hermes home and state: `~/.hermes`
- Hermes repo install: `~/.hermes/hermes-agent`
- Hermes CLI on PATH: `~/.local/bin/hermes`
- Hermes Web UI virtualenv: `~/hermes-web-ui/.venv`

## What It Does

- Shows Hermes gateway health, installed version, and update status.
- Lets you inspect and edit provider, model, channel, tool, and env configuration.
- Keeps browser chat tied to Hermes CLI sessions where possible.
- Organizes chats into folders with attached source files.
- Surfaces logs, sessions, onboarding state, and deployment health from one UI.
- Offers in-app Hermes update checks and, when safe, an update action with confirmation.

## Screenshots

These screenshots use sanitized demo data so the public repo does not expose local usernames, absolute machine-specific paths, or personal workspace details.

### Dashboard Overview

![Hermes Web UI dashboard overview](docs/images/dashboard-overview.png)

### Service and Updates

![Hermes Web UI service and updates view](docs/images/service-updates.png)

## Quick Start

If you do not already have Hermes installed, install it first:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
source ~/.bashrc
```

Then install and start the web UI:

```bash
git clone https://github.com/MNPickle/hermes-webui.git ~/hermes-web-ui
cd ~/hermes-web-ui
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and set a real token:

```bash
HERMES_WEBUI_TOKEN=replace-this-with-a-long-random-token
```

Start the app:

```bash
cd ~/hermes-web-ui
./start.sh 5000
```

Open `http://127.0.0.1:5000/` in your browser and enter the token when prompted.

## Daily Use From WSL

Once everything is installed, the normal startup flow is:

```bash
wsl
cd ~/hermes-web-ui
./start.sh 5000
```

Useful companion commands:

```bash
hermes --version
hermes gateway run
hermes doctor
hermes update
```

## Supported Environment

- Best fit: Linux and WSL2
- Also reasonable: macOS with Hermes installed in the standard user-home layout
- Native Windows is not the primary target for this repo; use WSL2 for the smoothest setup

## Updates

Hermes Web UI checks the active Hermes install against the official Hermes GitHub source and shows:

- installed version
- latest known release/version metadata
- whether the install is current, behind, checking, updating, failed, or unable to verify
- whether a direct in-app update is safe for the current install method

If direct update is not supported, the UI shows the exact manual command to run instead.

## Documentation

- [Install Guide](docs/INSTALL.md)
- [Usage Guide](docs/USAGE.md)
- [Configuration Reference](docs/CONFIG.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Contributing](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Deployment Notes](DEPLOYMENT_READY.md)
- [Changelog](CHANGELOG.md)

## Security Notes

- This app is designed as a local admin tool.
- All API routes require the `HERMES_WEBUI_TOKEN`.
- If you expose it beyond localhost, put it behind your own reverse proxy and TLS.

## License

This project is licensed under the [MIT License](LICENSE).
