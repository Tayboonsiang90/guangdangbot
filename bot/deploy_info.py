"""Resolve git commit / branch for deploy visibility (Render env, optional local git)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone

# When this process started (after deploy or local restart).
PROCESS_STARTED_AT: datetime = datetime.now(timezone.utc)


def discord_timestamp_markdown(dt: datetime, style: str = "F") -> str:
    """Return Discord dynamic timestamp markup.

    Renders in **each viewer's local timezone** (and locale) in the Discord client.
    Styles: t T d D f F R — see https://discord.com/developers/docs/reference#message-formatting-formats

    Common: F = long date/time, R = relative ("2 hours ago").
    """
    aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    ts = int(aware.timestamp())
    return f"<t:{ts}:{style}>"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _run_git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def get_commit_full() -> str | None:
    """Full SHA if available (Render or git)."""
    for key in ("RENDER_GIT_COMMIT", "APP_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        raw = os.getenv(key, "").strip()
        if raw:
            return raw
    return _run_git("rev-parse", "HEAD")


def get_commit_short() -> str | None:
    full = get_commit_full()
    if not full:
        return None
    if len(full) <= 12:
        return full
    return full[:7]


def get_branch() -> str | None:
    for key in ("RENDER_GIT_BRANCH", "APP_GIT_BRANCH", "GIT_BRANCH"):
        raw = os.getenv(key, "").strip()
        if raw:
            return raw
    return _run_git("rev-parse", "--abbrev-ref", "HEAD")


def is_render_runtime() -> bool:
    return os.getenv("RENDER", "").strip().lower() in ("true", "1", "yes")


def commit_compare_url() -> str | None:
    """https://github.com/owner/repo/commit/sha when GITHUB_REPO and full SHA are set."""
    repo = os.getenv("GITHUB_REPO", "").strip()
    full = get_commit_full()
    if not repo or not full or "/" not in repo:
        return None
    return f"https://github.com/{repo}/commit/{full}"


def format_testalert_build_text() -> str:
    """Content for embed field + ephemeral message (keep under ~900 chars for field limit)."""
    short = get_commit_short() or "unknown"
    branch = get_branch() or "unknown"
    env_label = "Render" if is_render_runtime() else "local"
    # Discord <t:...> shows in each user's local timezone; R adds relative hint.
    started_abs = discord_timestamp_markdown(PROCESS_STARTED_AT, "F")
    started_rel = discord_timestamp_markdown(PROCESS_STARTED_AT, "R")
    lines = [
        f"**Commit:** `{short}`",
        f"**Branch:** {branch}",
        f"**Runtime:** {env_label}",
        f"**Process started:** {started_abs} ({started_rel})",
    ]
    link = commit_compare_url()
    if link:
        lines.append(f"**Compare:** [View commit on GitHub]({link})")
    else:
        lines.append(
            "_Optional: set `GITHUB_REPO=owner/repo` for a commit link. Render sets `RENDER_GIT_*` automatically._"
        )
    text = "\n".join(lines)
    if len(text) > 1024:
        return text[:1021] + "…"
    return text
