"""GUI helper classes and functions for DeadUnlock."""

from __future__ import annotations

import json
import os
import sys
import logging
import queue
from dataclasses import asdict, dataclass
from typing import Any

from .update_checker import _get_current_version
from .aimbot import AimbotSettings

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(__file__)

SETTINGS_FILE = os.path.join(BASE_DIR, "aimbot_settings.json")
PRESETS_DIR = os.path.join(BASE_DIR, "presets")


@dataclass(frozen=True)
class PresetInfo:
    """Metadata describing a saved settings preset."""

    name: str
    path: str


def _ensure_presets_dir() -> str:
    """Create :data:`PRESETS_DIR` if necessary and return its path."""

    os.makedirs(PRESETS_DIR, exist_ok=True)
    return PRESETS_DIR


def _unique_preset_path(name: str) -> str:
    """Return a unique filesystem path for ``name`` within :data:`PRESETS_DIR`."""

    _ensure_presets_dir()
    safe = [ch.lower() if ch.isalnum() else "-" for ch in name.strip()]
    slug = "".join(safe).strip("-") or "preset"
    candidate = os.path.join(PRESETS_DIR, f"{slug}.json")
    index = 2
    while os.path.exists(candidate):
        candidate = os.path.join(PRESETS_DIR, f"{slug}_{index}.json")
        index += 1
    return candidate


def _load_preset_payload(path: str) -> tuple[str, dict[str, Any]]:
    """Return the preset name and raw settings payload from ``path``."""

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("Preset file must contain a JSON object.")
    if "settings" in data:
        payload = data["settings"]
        if not isinstance(payload, dict):
            raise ValueError("Preset settings must be a JSON object.")
        name = str(data.get("name") or os.path.splitext(os.path.basename(path))[0])
        return name, payload
    return os.path.splitext(os.path.basename(path))[0], data


def list_presets() -> list[PresetInfo]:
    """Return all available presets stored in :data:`PRESETS_DIR`."""

    if not os.path.isdir(PRESETS_DIR):
        return []
    presets: list[PresetInfo] = []
    for entry in sorted(os.listdir(PRESETS_DIR)):
        if not entry.lower().endswith(".json"):
            continue
        path = os.path.join(PRESETS_DIR, entry)
        try:
            name, _ = _load_preset_payload(path)
        except Exception:
            continue
        presets.append(PresetInfo(name=name, path=path))
    presets.sort(key=lambda info: info.name.lower())
    return presets


def load_preset(preset: PresetInfo | str) -> AimbotSettings:
    """Load ``preset`` and return the contained :class:`AimbotSettings`."""

    path = preset.path if isinstance(preset, PresetInfo) else os.fspath(preset)
    _, payload = _load_preset_payload(path)
    return AimbotSettings(**payload)


def save_preset(
    name: str, settings: AimbotSettings, *, path: str | None = None
) -> PresetInfo:
    """Persist ``settings`` under ``name`` and return preset metadata."""

    target_path = path or _unique_preset_path(name)
    payload = {"name": name, "settings": asdict(settings)}
    with open(target_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return PresetInfo(name=name, path=target_path)


def delete_preset(preset: PresetInfo | str) -> None:
    """Remove the preset file represented by ``preset`` if it exists."""

    path = preset.path if isinstance(preset, PresetInfo) else os.fspath(preset)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def export_settings(
    settings: AimbotSettings, path: str, *, name: str | None = None
) -> None:
    """Write ``settings`` to ``path`` in a portable JSON format."""

    payload: dict[str, Any] = {"settings": asdict(settings)}
    if name:
        payload["name"] = name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def import_preset(path: str) -> tuple[PresetInfo, AimbotSettings]:
    """Import the preset stored at ``path`` into :data:`PRESETS_DIR`."""

    name, payload = _load_preset_payload(path)
    settings = AimbotSettings(**payload)
    info = save_preset(name, settings)
    return info, settings


def load_saved_settings() -> AimbotSettings:
    """Return stored :class:`AimbotSettings` or defaults."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return AimbotSettings(**data)
    except Exception:
        return AimbotSettings()


def save_settings(settings: AimbotSettings) -> None:
    """Persist ``settings`` to :data:`SETTINGS_FILE`."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(asdict(settings), fh, indent=2)
    except Exception as exc:
        print(f"Failed to save settings: {exc}")


def get_build_sha() -> str:
    """Return the short commit SHA for the current build."""
    try:
        sha = _get_current_version()
        if sha:
            return sha[:7]
    except Exception:
        pass
    return "unknown"


class GUILogHandler(logging.Handler):
    """Simple log handler that forwards records to a queue."""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass



