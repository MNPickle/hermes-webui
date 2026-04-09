# Usage Guide

This guide covers the normal day-to-day workflow for Hermes Web UI after it is installed.

## Start the App

From WSL or Linux:

```bash
cd ~/hermes-web-ui
./start.sh 5000
```

Then open `http://127.0.0.1:5000/`.

## First-Run Checklist

After the UI opens, a good first pass is:

1. Confirm the Dashboard shows the expected Hermes version and gateway status.
2. Open the Service screen and verify the Hermes binary path looks correct.
3. Open Providers and configure any provider credentials you want Hermes to use.
4. Open Models and confirm your model roles are set the way you expect.
5. Open Chat and start a simple session to confirm Hermes is responding.

## Main Screens

### Dashboard

Use the Dashboard for the fastest health overview:

- gateway running or stopped
- installed Hermes version
- update status
- Python/runtime status

The Hermes Updates card also appears here.

### Service

Use Service when you need operational controls:

- view gateway status and PID
- start, stop, or restart Hermes gateway actions
- inspect the Hermes binary currently managed by the web UI
- check Hermes update state and run updates when supported

### Providers and Models

These screens help you wire Hermes to model providers and roles:

- add or edit provider endpoints
- keep keys masked in the UI
- configure model routing
- set vision-specific model configuration

If screenshot or image chat is important to you, make sure the vision model is configured here.

### Chat and Folders

The chat UI is designed to stay attached to Hermes CLI sessions where possible.

You can:

- create chats
- rename or delete chats
- group chats into folders
- attach source files to folders
- keep context organized for longer-running work

### Skills and Capabilities

These screens are for managing skills, starter packs, and capability draft flows exposed by the web UI.

### Logs and Runtime Status

Use the Logs-related screens when you need to diagnose:

- startup problems
- provider failures
- gateway issues
- update failures

## Hermes Updates

Hermes Web UI can show these update states:

- up to date
- checking
- update available
- update in progress
- update failed
- unable to determine latest version

Important detail: an available update does not always mean a brand-new release version. For git-based installs, it can also mean your current install is behind the official `main` branch by some number of commits.

The update card shows:

- installed version
- latest known version metadata
- update type
- source channel
- manual command when direct update is not available

If direct update is supported, the UI asks for confirmation before running it.

## Useful Companion CLI Commands

These still pair well with the UI:

```bash
hermes --version
hermes model
hermes gateway run
hermes doctor
hermes update
```

## Recommended Routine

For a typical local session:

1. Start Hermes Web UI with `./start.sh 5000`.
2. Check Dashboard or Service for health and update state.
3. Use Providers and Models if you need to change model setup.
4. Use Chat and Folders for actual work.
5. Use Logs if something feels off.
