# Troubleshooting

This page covers the most common setup and runtime issues for Hermes Web UI.

## I Started WSL. What Do I Type Now?

For the normal daily flow:

```bash
wsl
cd ~/hermes-web-ui
./start.sh 5000
```

Then open `http://127.0.0.1:5000/`.

## `hermes --version` Looks Wrong or Uses the Wrong Install

Check where `hermes` resolves:

```bash
which hermes
readlink -f "$(which hermes)"
hermes --version
```

For the current preferred Hermes installer layout, the binary should normally come from:

```text
~/.hermes/hermes-agent/venv/bin/hermes
```

## Hermes Web UI Says It Cannot Find Hermes

Set the binary explicitly:

```bash
export HERMES_WEBUI_HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes"
cd ~/hermes-web-ui
./start.sh 5000
```

If you want that override every time, add it to your shell profile or your startup environment.

## The Browser Keeps Asking for the Token

Check that the token is set in `~/hermes-web-ui/.env`:

```bash
cat ~/hermes-web-ui/.env
```

It should contain:

```bash
HERMES_WEBUI_TOKEN=your-token-here
```

Also verify the server accepts it:

```bash
curl -H "Authorization: Bearer $HERMES_WEBUI_TOKEN" http://127.0.0.1:5000/api/health
```

If the browser has an old token saved, clear local storage for the site and sign in again.

## `./start.sh` Says the App Is Already Running

That usually means something is already listening on that port.

If you want to reuse the running instance, just open the URL it prints.

If you want a new port:

```bash
./start.sh 5001
```

## The Gateway Is Not Running

Check the Service screen in the UI first.

From the terminal, useful commands are:

```bash
hermes gateway run
hermes doctor
```

## Screenshot or Vision Chat Is Not Working

The repo-side requirements are:

- a reachable API-compatible endpoint
- a valid API key if the endpoint requires one
- a vision-capable model configured in Hermes

Useful checks:

```bash
echo "$HERMES_API_URL"
echo "$HERMES_API_KEY"
```

Then confirm the vision model in Hermes config or the Models/Providers screens.

## The Update Card Says Hermes Is Behind, But the Version Number Looks the Same

That can be legitimate.

For git-based Hermes installs, the update check can report that the current install is behind the official `main` branch even when the release version string is the same. In that case the UI should describe it as a revision-level update rather than a new release version.

## The Update Button Is Disabled

When Hermes Web UI decides in-app update is not safe for the current install method, it will show:

- why direct update is unavailable
- the exact manual command to run instead

If Hermes is installed in the standard repo-managed layout, the normal manual command is:

```bash
cd ~/.hermes/hermes-agent
~/.hermes/hermes-agent/venv/bin/hermes update
```

## I Think This Machine Has Multiple Hermes Installs

Check these:

```bash
which hermes
readlink -f "$(which hermes)"
hermes --version
ls -la ~/.hermes
```

If the web UI and your shell disagree, explicitly set:

```bash
export HERMES_WEBUI_HERMES_BIN="$HOME/.hermes/hermes-agent/venv/bin/hermes"
```

## Where Do I Look for Logs?

If you started the app manually, start with the same terminal where you launched `./start.sh`.

If you are using systemd:

```bash
journalctl -u hermes-webui -f
```

For Hermes itself, check the Hermes home under `~/.hermes`.
