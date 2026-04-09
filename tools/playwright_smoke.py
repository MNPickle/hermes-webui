#!/usr/bin/env python3
"""Minimal Playwright smoke test for the Hermes web UI."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5057/", help="Base URL for the Hermes web UI")
    parser.add_argument("--token", required=True, help="HERMES_WEBUI_TOKEN used for browser auth")
    parser.add_argument(
        "--screenshot-dir",
        default="",
        help="Directory to write screenshots into. Defaults to a temp directory.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch a visible browser window instead of headless mode.",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        help="Delay Playwright actions by this many milliseconds. Useful for visible demo runs.",
    )
    return parser.parse_args()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait(page, ms: int = 600) -> None:
    page.wait_for_timeout(ms)


def toast_texts(page) -> list[str]:
    return page.locator("#toast-container .toast").all_inner_texts()


def wait_for_toast(page, needle: str, timeout_ms: int = 5000) -> bool:
    deadline = time.time() + (timeout_ms / 1000)
    needle = needle.lower()
    while time.time() < deadline:
        if any(needle in text.lower() for text in toast_texts(page)):
            return True
        page.wait_for_timeout(150)
    return False


def current_screen(page) -> str:
    return page.evaluate(
        """
        () => {
            const active = document.querySelector('.nav-item.active');
            return active ? active.dataset.screen || '' : '';
        }
        """
    )


def assert_screen_has_no_broken_literals(page, screen: str, timeout_ms: int = 5000) -> None:
    page.evaluate(f"window.navigate('{screen}')")
    page.wait_for_timeout(1000)
    text = page.locator("#content").inner_text(timeout=timeout_ms)
    bad_tokens = [token for token in ("U000", "undefined", "NaN") if token in text]
    expect(not bad_tokens, f"{screen} screen contains broken UI text: {bad_tokens}")


def find_first_folder_with_chats(page):
    cards = page.locator(".folder-admin-card")
    total = cards.count()
    for index in range(total):
        card = cards.nth(index)
        badge_text = " ".join(card.locator(".badge").all_inner_texts())
        if "0 chats" in badge_text:
            continue
        return card
    return None


def open_new_folder_modal(page) -> None:
    # Prefer the persistent sidebar action because the folders screen can still
    # be finishing its async render when the first header button appears.
    sidebar_create = page.locator("#folders-nav-create")
    if sidebar_create.count():
        sidebar_create.click()
        return
    page.locator("#folders-screen-create").click()


def main() -> int:
    args = parse_args()
    shot_dir = Path(args.screenshot_dir) if args.screenshot_dir else Path(tempfile.mkdtemp(prefix="hermes-pw-smoke-"))
    shot_dir.mkdir(parents=True, exist_ok=True)
    temp_folder_name = f"PW Smoke {time.strftime('%H%M%S')}"
    temp_chat_name = f"PW Chat {time.strftime('%H%M%S')}"
    temp_source = shot_dir / "smoke-source.txt"
    temp_source.write_text("This is a Playwright smoke source file.\n", encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.headed,
            slow_mo=max(0, args.slow_mo),
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1200})
        context.add_init_script(
            f"window.localStorage.setItem('hermes_webui_token', {json.dumps(args.token)});"
        )
        page = context.new_page()

        failures: list[str] = []
        page.on("pageerror", lambda exc: failures.append(f"pageerror: {exc}"))
        page.on("response", lambda resp: failures.append(f"http {resp.status}: {resp.url}") if resp.status >= 400 else None)

        page.goto(args.url, wait_until="networkidle", timeout=30000)
        wait(page, 1000)
        page.screenshot(path=str(shot_dir / "home.png"), full_page=True)
        expect("Hermes Agent" in page.locator("#content").inner_text(timeout=3000), "Home screen did not render")
        connection_status = page.locator("#connection-status .status-text").inner_text(timeout=3000).strip()
        expect(connection_status != "Running via CLI", "Sidebar still shows misleading stopped-state text")
        page.evaluate(
            """
            () => {
                window.chatApplySessionMetadata({
                    continuity_mode: 'local_replay',
                    transport_notice: 'This chat is local replay only and is not attached to a resumable Hermes CLI session.',
                });
            }
            """
        )
        wait(page, 250)
        expect(
            "local replay only"
            in page.locator("#chat-session-banner").inner_text(timeout=3000),
            "Chat banner did not surface the specific transport notice",
        )
        page.evaluate("() => window.chatApplySessionMetadata(null)")
        wait(page, 150)
        assert_screen_has_no_broken_literals(page, "skills")
        assert_screen_has_no_broken_literals(page, "channels")
        assert_screen_has_no_broken_literals(page, "capabilities")
        page.wait_for_selector("text=Skill Draft", timeout=15000)
        page.click('button[data-screen="skills"]')
        page.wait_for_selector("#skill-search", timeout=10000)
        expect(page.locator("#skill-category-filter").count() == 1, "Skills screen is missing the category filter")
        source_options = page.locator("#skill-source-filter option").evaluate_all(
            "options => options.map(option => ({ value: option.value, label: (option.textContent || '').trim() }))"
        )
        filtered_source = next((option["value"] for option in source_options if option["value"] != "all"), "")
        expect(bool(filtered_source), "Skills source filter did not expose any source options")
        page.select_option("#skill-source-filter", filtered_source)
        wait(page, 300)
        expect(
            page.locator(".starter-pack-block").count() == 0,
            "Starter Pack should be hidden once the Skills view is filtered by source",
        )
        page.select_option("#skill-source-filter", "all")
        wait(page, 300)
        expect(
            page.locator(".starter-pack-block").count() == 1,
            "Starter Pack should return in the default unfiltered Skills view",
        )
        page.locator("#skill-search").click()
        for ch in "Git ":
            page.keyboard.type(ch)
            page.wait_for_timeout(50)
        expect(
            page.locator("#skill-search").input_value() == "Git ",
            "Skills search input lost typed text while filtering the inventory",
        )
        page.locator("#skill-search").fill("")
        page.click('button[data-screen="capabilities"]')
        page.wait_for_selector("text=Skill Draft", timeout=10000)

        page.locator(".skill-page-actions button", has_text="Create Integration").click()
        page.wait_for_selector("text=Integration Draft", timeout=5000)
        expect(
            page.locator(".skill-page-actions .btn.btn-primary", has_text="Create Integration").count() == 1,
            "Capability switcher did not highlight Create Integration",
        )

        page.locator(".skill-page-actions button", has_text="Create Agent Preset").click()
        page.wait_for_selector("text=Preset Draft", timeout=5000)
        expect(
            page.locator(".skill-page-actions .btn.btn-primary", has_text="Create Agent Preset").count() == 1,
            "Capability switcher did not highlight Create Agent Preset",
        )

        page.locator(".skill-page-actions button", has_text="Create Skill").click()
        page.wait_for_selector("text=Skill Draft", timeout=5000)
        expect(
            page.locator(".skill-page-actions .btn.btn-primary", has_text="Create Skill").count() == 1,
            "Capability switcher did not highlight Create Skill",
        )
        page.locator("#capability-skill-name").click()
        page.keyboard.type("UI Test Skill")
        page.locator("#capability-skill-category").click()
        for ch in "testing":
            page.keyboard.type(ch)
            page.wait_for_timeout(50)
        expect(
            page.locator("#capability-skill-category").input_value() == "testing",
            "Capability builder lost typed text while editing the category field",
        )
        page.set_viewport_size({"width": 900, "height": 650})
        wait(page, 300)
        page.locator("#capability-skill-description").fill("Simple UI test skill")
        page.locator("#capability-skill-instructions").fill("This is a test skill created from the Hermes Web UI.")
        page.locator(".capability-builder-actions .btn.btn-primary", has_text="Preview Draft").click()
        expect(wait_for_toast(page, "draft preview ready"), "Capability preview ready toast did not appear")
        page.wait_for_selector("#capability-approve-button", timeout=5000)
        page.wait_for_timeout(800)
        approval_rect = page.locator("#capability-approve-button").evaluate(
            """
            el => {
                const rect = el.getBoundingClientRect();
                return {
                    top: rect.top,
                    bottom: rect.bottom,
                    innerHeight: window.innerHeight,
                };
            }
            """
        )
        expect(
            approval_rect["top"] < approval_rect["innerHeight"] and approval_rect["bottom"] > 0,
            "Capability preview did not bring the approval button into view",
        )
        page.set_viewport_size({"width": 1440, "height": 1200})
        wait(page, 300)

        page.click('button[data-screen="folders"]')
        page.wait_for_selector(".folder-admin-card", timeout=10000)
        page.screenshot(path=str(shot_dir / "folders.png"), full_page=True)

        first_folder_title = page.locator(".folder-admin-title").first.inner_text(timeout=3000).strip()
        open_new_folder_modal(page)
        page.wait_for_selector("#chat-folder-title", timeout=5000)
        page.fill("#chat-folder-title", first_folder_title)
        page.press("#chat-folder-title", "Enter")
        wait(page, 700)
        expect(wait_for_toast(page, "already exists"), "Duplicate folder warning did not appear on Enter")
        page.click('#modal-footer .btn:not(.btn-primary)')
        wait(page, 300)

        open_new_folder_modal(page)
        page.wait_for_selector("#chat-folder-title", timeout=5000)
        page.fill("#chat-folder-title", temp_folder_name)
        page.click("#modal-footer .btn.btn-primary")
        wait(page, 1000)
        expect(current_screen(page) == "chat", "Folder creation did not return to Chat")
        expect(
            page.locator(".chat-folder-overview-title").inner_text(timeout=3000).strip() == temp_folder_name,
            "Folder overview did not open after create",
        )
        expect(
            page.locator("#chat-context-panel").evaluate("el => el.classList.contains('hidden')"),
            "Folder-only overview should not show the context panel duplicate",
        )
        expect(wait_for_toast(page, "folder created"), "Folder created toast missing")
        temp_folder_id = page.evaluate(
            """
            async title => {
                const token = window.localStorage.getItem('hermes_webui_token') || '';
                const resp = await fetch('/api/chat/folders', {
                    headers: { 'Authorization': 'Bearer ' + token },
                });
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.error || 'folder list failed');
                const match = (data.folders || []).find(folder => folder.title === title);
                return match ? (match.id || '') : '';
            }
            """,
            temp_folder_name,
        )
        expect(bool(temp_folder_id), "Temporary folder id was not captured")
        page.screenshot(path=str(shot_dir / "folder-created.png"), full_page=True)

        page.click('button:has-text("Add Source")')
        page.set_input_files("#global-folder-source-input", str(temp_source))
        wait(page, 1200)
        expect(wait_for_toast(page, "sources added"), "Source add toast missing")
        expect(temp_source.name in page.locator("#content").inner_text(timeout=3000), "Added source file is not visible in folder view")
        page.screenshot(path=str(shot_dir / "folder-source-added.png"), full_page=True)

        temp_session_id = page.evaluate(
            """
            async ({ folderId, chatTitle }) => {
                const token = window.localStorage.getItem('hermes_webui_token') || '';
                const baseHeaders = {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + token,
                };
                const createResp = await fetch('/api/chat/sessions', {
                    method: 'POST',
                    headers: baseHeaders,
                    body: JSON.stringify({ folder_id: folderId }),
                });
                const created = await createResp.json();
                if (!createResp.ok) throw new Error(created.error || 'session create failed');
                const sessionId = created.session_id;
                const renameResp = await fetch('/api/chat/sessions/' + encodeURIComponent(sessionId) + '/rename', {
                    method: 'POST',
                    headers: baseHeaders,
                    body: JSON.stringify({ title: chatTitle }),
                });
                const renamed = await renameResp.json();
                if (!renameResp.ok) throw new Error(renamed.error || 'session rename failed');
                return sessionId;
            }
            """,
            {"folderId": temp_folder_id, "chatTitle": temp_chat_name},
        )
        expect(bool(temp_session_id), "Temporary session was not created")

        populated_folder = find_first_folder_with_chats(page)
        if populated_folder is not None:
            page.click('button[data-screen="folders"]')
            page.wait_for_selector(".folder-admin-card", timeout=10000)
            populated_folder = find_first_folder_with_chats(page)
            expect(populated_folder is not None, "Expected a folder with chats after reload")
            populated_folder.locator(".folder-admin-main").click()
            wait(page, 400)
            chat_pills = populated_folder.locator(".chat-folder-chat-pill")
            if chat_pills.count():
                first_chat_title = chat_pills.first.inner_text(timeout=3000).strip()
                chat_pills.first.click()
                wait(page, 1200)
                transcript = page.locator("#content").inner_text(timeout=3000)
                expect(first_chat_title in transcript, "Folder chat did not open in the main view")
                page.screenshot(path=str(shot_dir / "folder-chat-open.png"), full_page=True)

        page.click('button[data-screen="folders"]')
        page.wait_for_selector(".folder-admin-card", timeout=10000)
        temp_card = page.locator(".folder-admin-card").filter(
            has=page.locator(".folder-admin-title", has_text=temp_folder_name)
        ).first
        expect(temp_card.count() == 1, "Temporary folder was not found in folder list")
        temp_card.locator(".folder-admin-main").click()
        wait(page, 400)
        temp_chat_entry = temp_card.locator(".chat-folder-chat-entry").filter(
            has=page.locator(".chat-folder-chat-pill", has_text=temp_chat_name)
        ).first
        expect(temp_chat_entry.count() == 1, "Temporary chat did not appear in the folder UI")
        temp_chat_entry.locator(".chat-folder-chat-delete").click()
        expect(wait_for_toast(page, "chat deleted"), "Chat deleted toast missing")
        page.wait_for_function(
            """
            ({ folderName, chatName }) => {
                const cards = [...document.querySelectorAll('.folder-admin-card')];
                const card = cards.find(item => {
                    const title = item.querySelector('.folder-admin-title');
                    return title && (title.textContent || '').trim() === folderName;
                });
                if (!card) return false;
                return ![...card.querySelectorAll('.chat-folder-chat-pill')].some(item =>
                    (item.textContent || '').trim() === chatName
                );
            }
            """,
            arg={"folderName": temp_folder_name, "chatName": temp_chat_name},
            timeout=5000,
        )
        expect(
            temp_card.locator(".chat-folder-chat-entry").filter(
                has=page.locator(".chat-folder-chat-pill", has_text=temp_chat_name)
            ).count()
            == 0,
            "Temporary chat still appears after delete",
        )
        page.screenshot(path=str(shot_dir / "folder-chat-deleted.png"), full_page=True)

        page.click('button[data-screen="chat"]')
        wait(page, 800)
        expect("Your personal AI assistant" in page.locator("#content").inner_text(timeout=3000), "Chat did not reset to the welcome view")

        page.click('button[data-screen="folders"]')
        page.wait_for_selector(".folder-admin-card", timeout=10000)
        temp_card = page.locator(".folder-admin-card").filter(
            has=page.locator(".folder-admin-title", has_text=temp_folder_name)
        ).first
        temp_card.locator('button:has-text("Delete")').click()
        page.wait_for_selector("#modal-overlay.active", timeout=5000)
        page.click('#modal-footer .btn.btn-danger')
        expect(wait_for_toast(page, "folder deleted"), "Folder deleted toast missing")
        page.wait_for_function(
            """
            folderName => {
                return ![...document.querySelectorAll('.folder-admin-title')].some(item =>
                    (item.textContent || '').trim() === folderName
                );
            }
            """,
            arg=temp_folder_name,
            timeout=5000,
        )
        expect(
            page.locator(".folder-admin-card").filter(
                has=page.locator(".folder-admin-title", has_text=temp_folder_name)
            ).count()
            == 0,
            "Temporary folder still exists after delete",
        )
        page.screenshot(path=str(shot_dir / "folder-deleted.png"), full_page=True)

        browser.close()

    unexpected_failures = [item for item in failures if "http 409:" not in item]
    expect(not unexpected_failures, f"Browser failures detected: {unexpected_failures}")
    print(f"PLAYWRIGHT_SMOKE_OK screenshots={shot_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, PlaywrightTimeoutError) as exc:
        print(f"PLAYWRIGHT_SMOKE_FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
