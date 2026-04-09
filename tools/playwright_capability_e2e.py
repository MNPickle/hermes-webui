#!/usr/bin/env python3
"""Browser E2E for Create Capability flows against an isolated Hermes home."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Base URL for the Hermes web UI")
    parser.add_argument("--token", required=True, help="HERMES_WEBUI_TOKEN used for browser auth and API setup")
    parser.add_argument("--hermes-home", required=True, help="Temporary Hermes home used for this isolated run")
    parser.add_argument("--screenshot-dir", required=True, help="Directory to write screenshots into")
    parser.add_argument("--headed", action="store_true", help="Launch a visible browser window")
    parser.add_argument("--slow-mo", type=int, default=0, help="Delay browser actions by this many milliseconds")
    return parser.parse_args()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait(page, ms: int = 600) -> None:
    page.wait_for_timeout(ms)


def wait_for_toast(page, needle: str, timeout_ms: int = 7000) -> bool:
    deadline = time.time() + (timeout_ms / 1000)
    target = needle.lower()
    while time.time() < deadline:
        texts = page.locator("#toast-container .toast").all_inner_texts()
        if any(target in text.lower() for text in texts):
            return True
        page.wait_for_timeout(150)
    return False


def api_request(base_url: str, token: str, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body) if body else {}


def ensure_provider_profile(base_url: str, token: str) -> str:
    profile_name = "pw-capability-profile"
    status, body = api_request(
        base_url,
        token,
        "POST",
        "/api/providers",
        {
            "name": profile_name,
            "provider": "openrouter",
            "model": "openai/gpt-5.4-mini",
            "api_key": "pw-capability-test-key",
        },
    )
    expect(status in (200, 409), f"Provider setup failed: status={status} body={body}")
    return profile_name


def run_skill_flow(page, shot_dir: Path, skill_name: str, skill_slug: str) -> None:
    page.click('button[data-screen="skills"]')
    page.locator(".skill-page-actions button", has_text="Create Skill").click()
    page.wait_for_selector("text=Skill Draft", timeout=10000)
    page.fill("#capability-skill-name", skill_name)
    page.fill("#capability-skill-category", "testing")
    page.fill("#capability-skill-description", "Browser end-to-end capability test skill")
    page.fill("#capability-skill-instructions", "This skill was created by the isolated capability browser test.")
    page.locator(".capability-builder-column .btn.btn-primary", has_text="Preview Draft").first.click()
    expect(wait_for_toast(page, "draft preview ready"), "Skill preview ready toast did not appear")
    page.wait_for_selector("#capability-approve-button", timeout=10000)
    wait(page, 1300)
    page.locator("#capability-approve-button").click()
    page.wait_for_selector("text=Skill Created", timeout=10000)
    page.screenshot(path=str(shot_dir / "skill-created.png"), full_page=True)
    page.locator("button", has_text="Open Skills").click()
    page.wait_for_selector(f'[data-skill-path="{skill_slug}"]', timeout=10000)
    skill_card = page.locator(f'[data-skill-path="{skill_slug}"]').first
    expect(skill_card.count() == 1, f"Created skill {skill_slug} did not appear in Skills UI")
    expect(skill_card.locator("text=testing").count() >= 1, "Created skill did not show its category in the Skills UI")
    expect(page.locator("text=Hermes Web UI").count() >= 1, "Created skill did not appear under the Hermes Web UI source block")
    expect(
        page.locator(".card").filter(has=page.locator("text=" + skill_name)).count() >= 1
        or page.locator("text=" + skill_name).count() >= 1,
        f"Created skill {skill_name} did not appear in Skills UI",
    )


def run_integration_flow(page, shot_dir: Path, webhook_url: str) -> None:
    page.click('button[data-screen="capabilities"]')
    page.locator(".skill-page-actions button", has_text="Create Integration").click()
    page.wait_for_selector("text=Integration Draft", timeout=10000)
    page.select_option("#capability-integration-kind", "webhook")
    page.fill("#capability-integration-config", json.dumps({"url": webhook_url}, indent=2))
    page.locator(".capability-builder-column .btn.btn-primary", has_text="Preview Draft").first.click()
    expect(wait_for_toast(page, "draft preview ready"), "Integration preview ready toast did not appear")
    page.wait_for_selector("#capability-approve-button", timeout=10000)
    wait(page, 1300)
    page.locator("#capability-approve-button").click()
    page.wait_for_selector("text=Integration Created", timeout=10000)
    page.screenshot(path=str(shot_dir / "integration-created.png"), full_page=True)
    page.locator("button", has_text="Open Apps & Integrations").click()
    page.wait_for_selector("#integration-card-webhook", timeout=10000)
    integration_card = page.locator("#integration-card-webhook").first
    expect(integration_card.count() == 1, "Created webhook card did not appear in Apps & Integrations")
    expect(integration_card.locator("text=New").count() >= 1, "Created webhook card was not highlighted as new")


def run_preset_flow(page, shot_dir: Path, preset_name: str, profile_name: str, skill_name: str) -> None:
    page.click('button[data-screen="capabilities"]')
    page.locator(".skill-page-actions button", has_text="Create Agent Preset").click()
    page.wait_for_selector("text=Preset Draft", timeout=10000)
    page.fill("#capability-preset-name", preset_name)
    page.fill("#capability-preset-description", "Browser end-to-end preset test")
    page.fill("#capability-preset-system-prompt", "Use the selected tools and summarize clearly.")
    page.select_option("#capability-role-profile-primary", profile_name)
    page.fill("#capability-role-model-primary", "openai/gpt-5.4-mini")

    skills_card = page.locator(".card").filter(has=page.locator(".card-header", has_text="Skills")).first
    skills_card.locator("label", has_text=skill_name).locator('input[type="checkbox"]').check()
    integrations_card = page.locator(".card").filter(has=page.locator(".card-header", has_text="Integrations")).first
    integrations_card.locator("label", has_text="webhook").locator('input[type="checkbox"]').check()

    page.locator(".capability-builder-column .btn.btn-primary", has_text="Preview Draft").first.click()
    expect(wait_for_toast(page, "draft preview ready"), "Preset preview ready toast did not appear")
    page.wait_for_selector("#capability-approve-button", timeout=10000)
    wait(page, 1300)
    page.locator("#capability-approve-button").click()
    page.wait_for_selector("text=Agent Preset Created", timeout=10000)
    page.screenshot(path=str(shot_dir / "preset-created.png"), full_page=True)
    page.locator("button", has_text="Open Agents").click()
    page.wait_for_selector(f"#agent-card-{preset_name}", timeout=10000)
    preset_card = page.locator(f"#agent-card-{preset_name}").first
    expect(preset_card.count() == 1, f"Created preset {preset_name} did not appear in Agents")
    expect(preset_card.locator("text=New").count() >= 1, "Created preset card was not highlighted as new")


def verify_disk_state(hermes_home: Path, skill_slug: str, preset_name: str, webhook_url: str) -> None:
    skill_dir = hermes_home / "skills" / skill_slug
    skill_md = skill_dir / "SKILL.md"
    source_meta = skill_dir / ".hermes-webui-source.json"
    config_path = hermes_home / "config.yaml"

    expect(skill_md.exists(), f"Skill file was not created at {skill_md}")
    expect(source_meta.exists(), f"Skill source metadata was not created at {source_meta}")
    expect(config_path.exists(), f"Config file was not created at {config_path}")

    skill_text = skill_md.read_text(encoding="utf-8")
    config_text = config_path.read_text(encoding="utf-8")
    source_payload = json.loads(source_meta.read_text(encoding="utf-8"))

    expect("Browser end-to-end capability test skill" in skill_text, "Skill file content did not match the created skill")
    expect(webhook_url in config_text, "Webhook integration was not written to config.yaml")
    expect(preset_name in config_text, "Agent preset was not written to config.yaml")
    expect(source_payload.get("install_mode") == "webui_create", "Skill source metadata did not record web UI creation")


def main() -> int:
    args = parse_args()
    base_url = args.url.rstrip("/")
    shot_dir = Path(args.screenshot_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)
    hermes_home = Path(args.hermes_home) / ".hermes"
    unique = int(time.time())
    skill_name = f"PW Capability Skill {unique}"
    skill_slug = f"pw-capability-skill-{unique}"
    preset_name = f"pw-capability-preset-{unique}"
    webhook_url = f"https://example.invalid/pw-capability-{unique}"
    profile_name = ensure_provider_profile(base_url, args.token)

    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed, slow_mo=max(0, args.slow_mo))
        context = browser.new_context(viewport={"width": 1280, "height": 980})
        context.add_init_script(
            f"window.localStorage.setItem('hermes_webui_token', {json.dumps(args.token)});"
        )
        page = context.new_page()
        page.on("pageerror", lambda exc: failures.append(f"pageerror: {exc}"))
        page.on("response", lambda resp: failures.append(f"http {resp.status}: {resp.url}") if resp.status >= 400 else None)

        page.goto(base_url + "/", wait_until="networkidle", timeout=30000)
        wait(page, 1000)
        page.screenshot(path=str(shot_dir / "home.png"), full_page=True)

        run_skill_flow(page, shot_dir, skill_name, skill_slug)
        run_integration_flow(page, shot_dir, webhook_url)
        run_preset_flow(page, shot_dir, preset_name, profile_name, skill_name)

        browser.close()

    unexpected_failures = [item for item in failures if "http 409:" not in item]
    expect(not unexpected_failures, f"Browser failures detected: {unexpected_failures}")
    verify_disk_state(hermes_home, skill_slug, preset_name, webhook_url)
    print(
        "PLAYWRIGHT_CAPABILITY_E2E_OK "
        f"screenshots={shot_dir} "
        f"hermes_home={hermes_home} "
        f"skill_slug={skill_slug} "
        f"preset_name={preset_name}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, PlaywrightTimeoutError) as exc:
        print(f"PLAYWRIGHT_CAPABILITY_E2E_FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
