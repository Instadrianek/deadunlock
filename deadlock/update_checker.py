"""Utility functions for update notifications."""

from __future__ import annotations

import os
import subprocess
import sys
import webbrowser
from typing import Optional

import requests

_TOKEN_ENV_VARS = ("DEADUNLOCK_GITHUB_TOKEN", "GITHUB_TOKEN")
_SKIP_ENV_VARS = ("DEADUNLOCK_SKIP_UPDATE_CHECK", "DEADUNLOCK_DISABLE_UPDATE_CHECK")
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _github_headers() -> dict[str, str]:
    """Return default headers for GitHub API requests."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "deadunlock-update-checker",
    }
    token = next(
        (
            os.environ.get(var)
            for var in _TOKEN_ENV_VARS
            if os.environ.get(var)
        ),
        None,
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _update_checks_enabled() -> bool:
    """Return ``True`` when environment toggles allow online update checks."""

    for variable in _SKIP_ENV_VARS:
        value = os.environ.get(variable)
        if value is None:
            continue
        normalized = value.strip().lower()
        if not normalized or normalized in {"0", "false", "no", "off"}:
            return True
        return False
    return True

REPO_API_COMMIT = "https://api.github.com/repos/hmate9/deadunlock/commits/main"
REPO_API_RELEASES = "https://api.github.com/repos/hmate9/deadunlock/releases/latest"
RELEASE_PAGE = "https://github.com/hmate9/deadunlock/releases/latest"


def _is_binary_release() -> bool:
    """Return ``True`` if running as a PyInstaller binary."""
    return getattr(sys, "frozen", False)


def _local_commit() -> Optional[str]:
    """Return the SHA of the current local commit or ``None`` on error."""
    try:
        out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT_DIR)
        return out.decode().strip()
    except Exception:
        return None


def _remote_commit() -> Optional[str]:
    """Return the SHA of the latest commit on GitHub or ``None`` on error."""
    try:
        resp = requests.get(REPO_API_COMMIT, headers=_github_headers(), timeout=5)
        if resp.status_code == 200:
            return resp.json()["sha"]
    except requests.RequestException:
        pass
    return None


def _get_latest_release() -> Optional[dict]:
    """Return the latest release info from GitHub or ``None`` on error."""
    try:
        resp = requests.get(REPO_API_RELEASES, headers=_github_headers(), timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return None


def _version_file_candidates() -> list[str]:
    """Return candidate paths to the packaged version file."""

    candidates: list[str] = []
    if _is_binary_release():
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "version.txt"))
        candidates.append(os.path.join(_ROOT_DIR, "version.txt"))
    else:
        candidates.append(os.path.join(_ROOT_DIR, "version.txt"))

    unique: list[str] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def _read_version_file() -> Optional[str]:
    """Return the version string from ``version.txt`` if it exists."""

    for path in _version_file_candidates():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                version = handle.read().strip()
            if version:
                return version
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def _get_current_version() -> Optional[str]:
    """Return the current version string or ``None`` if unavailable."""
    if _is_binary_release():
        return _read_version_file()

    commit = _local_commit()
    if commit:
        return commit
    return _read_version_file()


def update_available() -> bool:
    """Return ``True`` if a newer commit or release exists."""
    if not _update_checks_enabled():
        return False
    if _is_binary_release():
        release = _get_latest_release()
        if not release:
            return False
        tag = release.get("tag_name", "")
        if not tag.startswith("build-"):
            return False
        latest = tag[6:]
        current = _get_current_version()
        return not current or current != latest
    current = _get_current_version()
    remote = _remote_commit()
    if not remote:
        return False
    if not current:
        return True
    return current != remote


def open_release_page() -> None:
    """Open the project's latest release page in the default browser."""
    webbrowser.open(RELEASE_PAGE)


def ensure_up_to_date() -> None:
    """Check for updates and open the release page if a newer version exists."""
    if update_available():
        print("A newer DeadUnlock version is available. Opening download page...")
        open_release_page()
        sys.exit(0)

