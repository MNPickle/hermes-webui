#!/usr/bin/env python3
"""Generate sanitized public-facing screenshots for the README."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


DEMO_UPDATE_STATE = {
    "status": "update_available",
    "availability_status": "update_available",
    "update_scope": "revision",
    "installed_version": {
        "display": "Hermes Agent v0.8.0 (2026.4.8)",
        "version": "0.8.0",
        "release_date": "2026.4.8",
    },
    "latest_version": {
        "display": "Hermes Agent v0.8.0 (2026.4.8)",
        "version": "0.8.0",
        "release_date": "2026.4.8",
    },
    "message": "Hermes Agent v0.8.0 matches the latest released version, but GitHub origin/main is 12 commits ahead.",
    "behind_commits": 12,
    "ahead_commits": 0,
    "checked_at": "2026-04-09T19:00:00Z",
    "bin_path": "/home/user/.hermes/hermes-agent/venv/bin/hermes",
    "project_root": "/home/user/.hermes/hermes-agent",
    "manual_command": "cd /home/user/.hermes/hermes-agent && /home/user/.hermes/hermes-agent/venv/bin/hermes update",
    "selection_reason": "Using the repo-managed Hermes install under /home/user/.hermes/hermes-agent.",
    "managed_system": "No",
    "official_source": {
        "label": "GitHub origin/main",
    },
    "worktree": {
        "tracked": 1,
        "untracked": 0,
    },
    "can_update": True,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5000/", help="Base URL for the Hermes Web UI")
    parser.add_argument("--token", default=os.environ.get("HERMES_WEBUI_TOKEN", ""), help="Web UI token")
    parser.add_argument(
        "--output-dir",
        default="docs/images",
        help="Directory where screenshots should be written",
    )
    return parser.parse_args()


def require_token(token: str) -> str:
    value = (token or "").strip()
    if not value:
        raise SystemExit("HERMES_WEBUI_TOKEN is required for screenshot generation")
    return value


def render_demo_dashboard(page, state: dict) -> None:
    page.evaluate(
        """
        (state) => {
            const content = document.getElementById('content');
            const banner = document.getElementById('global-status-banner');
            document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
            document.querySelector('[data-screen="dashboard"]')?.classList.add('active');
            document.getElementById('topbar')?.classList.remove('hidden-by-chat');
            if (content) {
                content.style.padding = '';
                content.style.overflow = '';
            }
            const dot = document.querySelector('#connection-status .status-dot');
            const text = document.querySelector('#connection-status .status-text');
            if (dot) dot.className = 'status-dot online';
            if (text) text.textContent = 'Gateway Running';
            HermesUpdate.applyState(state);
            if (content) {
                content.innerHTML = `
                    <div class="stats-grid">
                        <div class="stat-card green">
                            <div class="stat-value">Running</div>
                            <div class="stat-label">Gateway Status</div>
                        </div>
                        <div class="stat-card blue">
                            <div class="stat-value">Hermes Agent v0.8.0</div>
                            <div class="stat-label">Hermes Version</div>
                        </div>
                        <div class="stat-card amber">
                            <div class="stat-value">Updates Available</div>
                            <div class="stat-label">Hermes Update Status</div>
                        </div>
                        <div class="stat-card blue">
                            <div class="stat-value">Python 3.12</div>
                            <div class="stat-label">Python</div>
                        </div>
                    </div>
                    ${renderHermesUpdateCard(state)}
                    <div class="card">
                        <div class="card-header"><span>Quick Actions</span></div>
                        <div class="card-body" style="display:flex;gap:8px;flex-wrap:wrap">
                            <button class="btn btn-primary">Open Service</button>
                            <button class="btn">Check Now</button>
                            <button class="btn">View Logs</button>
                            <button class="btn">Reload Config</button>
                        </div>
                    </div>
                `;
            }
            renderGlobalStatusBanner();
            if (banner) banner.classList.remove('hidden');
        }
        """,
        state,
    )


def render_demo_service(page, state: dict) -> None:
    page.evaluate(
        """
        (state) => {
            const content = document.getElementById('content');
            document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
            document.querySelector('[data-screen="service"]')?.classList.add('active');
            document.getElementById('topbar')?.classList.remove('hidden-by-chat');
            const dot = document.querySelector('#connection-status .status-dot');
            const text = document.querySelector('#connection-status .status-text');
            if (dot) dot.className = 'status-dot online';
            if (text) text.textContent = 'Gateway Running';
            HermesUpdate.applyState(state);
            if (content) {
                content.style.padding = '';
                content.style.overflow = '';
                content.innerHTML = `
                    ${renderHermesUpdateCard(state)}
                    <div class="card">
                        <div class="card-header">
                            <span>Gateway Service</span>
                            <span class="badge badge-success">Running</span>
                        </div>
                        <div class="card-body">
                            <div class="kv-grid">
                                <div><strong>Status:</strong> Running</div>
                                <div><strong>PID:</strong> 24831</div>
                                <div><strong>Hermes Binary:</strong> <code>/home/user/.hermes/hermes-agent/venv/bin/hermes</code></div>
                                <div><strong>Hermes Home:</strong> <code>/home/user/.hermes</code></div>
                            </div>
                            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
                                <button class="btn btn-primary">Restart Gateway</button>
                                <button class="btn">Run Diagnostics</button>
                                <button class="btn">Update Hermes</button>
                            </div>
                        </div>
                    </div>
                `;
            }
            renderGlobalStatusBanner();
        }
        """,
        state,
    )


def sanitize_residual_text(page) -> None:
    page.evaluate(
        """
        () => {
            const replacements = [
                [/\\/home\\/[^/\\s]+/g, '/home/user'],
                [/\\/mnt\\/c\\/Users\\/[^/\\s]+/gi, '/mnt/c/Users/user'],
                (text => text.replace(/[A-Z]:\\\\Users\\\\[^\\\\\\s]+/g, 'C:\\\\Users\\\\user')),
            ];
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const nodes = [];
            while (walker.nextNode()) nodes.push(walker.currentNode);
            for (const node of nodes) {
                let value = node.nodeValue || '';
                for (const replacement of replacements) {
                    if (typeof replacement === 'function') value = replacement(value);
                    else value = value.replace(replacement[0], replacement[1]);
                }
                node.nodeValue = value;
            }
        }
        """
    )


def main() -> int:
    args = parse_args()
    token = require_token(args.token)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1180}, device_scale_factor=1)
        context.add_init_script(
            f"window.localStorage.setItem('hermes_webui_token', {json.dumps(token)});"
        )
        page = context.new_page()
        page.goto(args.url, wait_until="networkidle", timeout=30000)
        page.wait_for_selector("#content", timeout=10000)

        render_demo_dashboard(page, DEMO_UPDATE_STATE)
        sanitize_residual_text(page)
        page.screenshot(path=str(output_dir / "dashboard-overview.png"), full_page=True)

        render_demo_service(page, DEMO_UPDATE_STATE)
        sanitize_residual_text(page)
        page.screenshot(path=str(output_dir / "service-updates.png"), full_page=True)

        browser.close()

    print(f"Saved screenshots to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
