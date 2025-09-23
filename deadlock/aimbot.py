from __future__ import annotations

"""Aimbot implementation used by :mod:`deadlock`.

The logic here aims to be straightforward and easy to maintain.  The
``Aimbot`` class exposes a ``run`` loop that continuously reads game
memory via :class:`deadlock.memory.DeadlockMemory` and adjusts the
player camera toward enemy targets.

This module is intentionally platform specific (Windows only) as it
relies on ``win32api`` to query mouse button state.
"""

from dataclasses import dataclass
import math
import random
import time
import logging
import argparse
import ctypes
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Sequence

import win32api
import win32con

try:
    from .heroes import HeroIds, get_body_bone_index, get_head_bone_index
    from .helpers import (
        Vector3,
        calculate_camera_rotation,
        calculate_new_camera_angles,
    )
    from .memory import DeadlockMemory
    from . import mem_offsets as mo
    from .update_checker import ensure_up_to_date
except ImportError:
    # Fallback for when running directly
    from heroes import HeroIds, get_body_bone_index, get_head_bone_index
    from helpers import Vector3, calculate_camera_rotation, calculate_new_camera_angles
    from memory import DeadlockMemory
    import mem_offsets as mo

logger = logging.getLogger(__name__)


_MOUSE_BUTTON_FLAGS: Dict[str, tuple[int, int]] = {
    "mouse1": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
    "mouse2": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
    "mouse3": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
}
"""Mapping of mouse button specifiers to press/release event flags."""

_KEY_ALIASES: Dict[str, int] = {
    "mouse1": win32con.VK_LBUTTON,
    "mouse2": win32con.VK_RBUTTON,
    "mouse3": win32con.VK_MBUTTON,
    "mouse4": win32con.VK_XBUTTON1,
    "mouse5": win32con.VK_XBUTTON2,
    "space": win32con.VK_SPACE,
    "shift": win32con.VK_SHIFT,
    "ctrl": win32con.VK_CONTROL,
    "alt": win32con.VK_MENU,
    "lshift": win32con.VK_LSHIFT,
    "rshift": win32con.VK_RSHIFT,
    "lctrl": win32con.VK_LCONTROL,
    "rctrl": win32con.VK_RCONTROL,
    "lalt": win32con.VK_LMENU,
    "ralt": win32con.VK_RMENU,
    "tab": win32con.VK_TAB,
    "enter": win32con.VK_RETURN,
    "backspace": win32con.VK_BACK,
    "delete": win32con.VK_DELETE,
    "esc": win32con.VK_ESCAPE,
    "escape": win32con.VK_ESCAPE,
}
"""Named virtual key aliases supported by the command line interface."""

for _idx in range(1, 13):
    _KEY_ALIASES[f"f{_idx}"] = getattr(win32con, f"VK_F{_idx}")

_REVERSE_KEY_ALIASES: Dict[int, str] = {
    code: name for name, code in _KEY_ALIASES.items()
}
"""Reverse mapping to describe virtual keys in human readable form."""


def list_virtual_key_aliases() -> list[str]:
    """Return the available human readable virtual-key aliases."""

    return sorted(_KEY_ALIASES)


def describe_virtual_key(code: int | None) -> str:
    """Return a readable description for a Windows virtual-key code."""

    if code is None:
        return ""
    alias = _REVERSE_KEY_ALIASES.get(code)
    if alias is not None:
        return alias
    if 32 <= code <= 126:
        return chr(code)
    return f"0x{code:02X}"


def parse_virtual_key(value: str | int | None) -> int | None:
    """Return the virtual-key code represented by ``value``.

    ``value`` may be ``None`` or an empty string to disable the binding.
    Named aliases from :func:`list_virtual_key_aliases` are supported as
    well as single character strings and integer specifications (decimal
    or hexadecimal).
    """

    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    alias = _KEY_ALIASES.get(lowered)
    if alias is not None:
        return alias
    if len(text) == 1:
        return ord(text.upper())
    try:
        return int(text, 0)
    except ValueError as exc:
        raise ValueError(f"Unknown key specification: {value!r}") from exc


def available_auto_fire_buttons() -> tuple[str, ...]:
    """Return the supported auto-fire mouse buttons."""

    return tuple(sorted(_MOUSE_BUTTON_FLAGS))


def _normalise_hero_key(value: str) -> str:
    """Return a normalised key for hero lookup."""

    return "".join(ch for ch in value.lower() if ch.isalnum())


_HERO_NAME_ALIASES: Dict[str, HeroIds] = {}
"""Lookup mapping normalised hero identifiers to :class:`HeroIds`."""

for _hero in HeroIds:
    _HERO_NAME_ALIASES[_hero.name.lower()] = _hero
    _HERO_NAME_ALIASES[_normalise_hero_key(_hero.name)] = _hero
    _HERO_NAME_ALIASES[str(_hero.value)] = _hero


def _parse_hero_identifier(value: Any) -> HeroIds:
    """Return the :class:`HeroIds` represented by ``value``."""

    if isinstance(value, HeroIds):
        return value

    if isinstance(value, int):
        try:
            return HeroIds(value)
        except ValueError as exc:
            raise ValueError(f"unknown hero id {value}") from exc

    if isinstance(value, str):
        spec = value.strip()
        if not spec:
            raise ValueError("hero identifier cannot be empty")
        lowered = spec.lower()
        hero = _HERO_NAME_ALIASES.get(lowered)
        if hero is None:
            hero = _HERO_NAME_ALIASES.get(_normalise_hero_key(spec))
        if hero is not None:
            return hero
        if lowered.startswith("0x"):
            try:
                return HeroIds(int(lowered, 16))
            except ValueError as exc:
                raise ValueError(f"unknown hero id {value}") from exc
        if lowered.isdigit():
            try:
                return HeroIds(int(lowered))
            except ValueError as exc:
                raise ValueError(f"unknown hero id {value}") from exc

    raise ValueError(f"unknown hero identifier {value!r}")


def parse_hero_identifier(value: Any) -> HeroIds:
    """Public wrapper returning the hero represented by ``value``."""

    return _parse_hero_identifier(value)


def _prettify_hero_name(hero: HeroIds) -> str:
    """Return a nicely spaced hero name for display purposes."""

    result: list[str] = []
    name = hero.name
    for idx, char in enumerate(name):
        if idx and char.isupper() and not name[idx - 1].isupper():
            result.append(" ")
        result.append(char)
    return "".join(result)


def format_hero_list(values: Sequence[HeroIds]) -> str:
    """Return ``values`` formatted as a comma separated string."""

    return ", ".join(_prettify_hero_name(hero) for hero in values)


def parse_hero_list(value: str | Sequence[Any] | None) -> tuple[HeroIds, ...]:
    """Return a tuple of heroes represented by ``value``.

    ``value`` may be an iterable of hero identifiers or a comma separated
    string. Blank entries are ignored and duplicates are removed while
    preserving order.
    """

    if value is None:
        return ()
    if isinstance(value, str):
        items: Iterable[Any] = [item.strip() for item in value.split(",")]
    else:
        items = value
    result: list[HeroIds] = []
    for item in items:
        if not item:
            continue
        hero = _parse_hero_identifier(item)
        if hero not in result:
            result.append(hero)
    return tuple(result)


@dataclass
class AimbotSettings:
    """Configuration for :class:`Aimbot`."""

    headshot_probability: float = 0.25
    #: chance to aim at the enemy's head instead of centre mass

    target_select_type: str = "fov"  # "distance", "fov" or "hybrid"
    #: prioritisation strategy when selecting targets

    smooth_speed: float = 5.0
    #: maximum angle change (degrees) per frame when locking on

    min_smooth_speed: float = 2.0
    #: minimum smoothing speed when adaptive smoothing is active

    smoothing_mode: str = "constant"
    #: how smoothing speed is resolved: ``"constant"``, ``"distance"`` or ``"fov"``

    yaw_smooth_scale: float = 1.0
    #: multiplier applied to smoothing speed for yaw adjustments

    pitch_smooth_scale: float = 1.0
    #: multiplier applied to smoothing speed for pitch adjustments

    distance_smoothing_min: float = 5.0
    #: distance (metres) where ``min_smooth_speed`` takes effect

    distance_smoothing_max: float = 35.0
    #: distance (metres) where ``smooth_speed`` takes effect

    fov_smoothing_min: float = 2.0
    #: combined FOV angle (degrees) where ``min_smooth_speed`` takes effect

    fov_smoothing_max: float = 12.0
    #: combined FOV angle (degrees) where ``smooth_speed`` takes effect

    lock_ramp_duration: float = 0.25
    #: seconds for smoothing speed to reach full strength after acquiring a target

    lock_ramp_start_speed: float = 1.5
    #: smoothing speed used immediately after acquiring a new target

    target_switch_cooldown: float = 0.2
    #: seconds to stay on a target before switching to a marginally better one

    target_switch_leeway: float = 4.0
    #: score improvement required to switch targets inside the cooldown window

    activation_key: Optional[int] = win32con.VK_LBUTTON
    #: virtual-key code that activates aiming; ``None`` disables manual activation

    activation_mode: str = "hold"
    #: how ``activation_key`` works: ``"hold"`` or ``"toggle"``

    grey_talon_lock: float = 0.5
    #: seconds to keep aiming after Grey Talon's ability 1 (``Q``)

    grey_talon_lock_enabled: bool = True
    #: if ``True`` check for Grey Talon's ability 1 key

    grey_talon_key: int = ord("Q")
    #: virtual-key code for Grey Talon's lock trigger

    yamato_lock: float = 1.5
    #: seconds to keep aiming after Yamato's ability 1 (``Q``)

    yamato_lock_enabled: bool = True
    #: if ``True`` check for Yamato's ability 1 key

    yamato_key: int = ord("Q")
    #: virtual-key code for Yamato's lock trigger

    vindicta_lock: float = 0.65
    #: seconds to keep aiming after Vindicta's ability 4 (``R``)

    vindicta_lock_enabled: bool = True
    #: if ``True`` check for Vindicta's ability 4 key

    vindicta_key: int = ord("R")
    #: virtual-key code for Vindicta's lock trigger

    paradox_shortcut_enabled: bool = True
    #: if ``True`` trigger Paradox ability combo

    paradox_r_key: int = ord("R")
    #: key that initiates Paradox combo

    paradox_e_key: int = ord("E")
    #: key automatically pressed after ``paradox_r_key``

    headshot_on_acquire: bool = True
    #: force headshots for ``headshot_force_duration`` after a cooldown gap

    headshot_cache_interval: float = 0.4
    #: seconds to reuse random headshot choice before re-rolling

    headshot_force_duration: float = 0.4
    #: seconds to force headshots after reacquiring a target

    headshot_reacquire_cooldown: float = 2.0
    #: downtime required before forcing headshots on reacquire

    auto_fire_enabled: bool = True
    #: enable simulated fire button hold when ``auto_fire_key`` is pressed

    auto_fire_key: Optional[int] = win32con.VK_RBUTTON
    #: virtual-key that triggers auto fire; ``None`` requires manual enabling via settings

    auto_fire_button: str = "mouse1"
    #: which mouse button to simulate while auto fire is active

    auto_fire_blocks_aim: bool = True
    #: when ``True`` the aimbot pauses while auto fire is active

    max_target_distance: Optional[float] = None
    #: maximum distance to consider for targets; ``None`` removes the limit

    max_target_fov: Optional[float] = None
    #: maximum angular deviation (degrees) accepted when selecting targets

    aim_tolerance: float = 0.35
    #: combined FOV angle at which the bot considers aim "good enough"

    target_distance_weight: float = 1.0
    #: weight applied to distance when using hybrid target selection

    target_fov_weight: float = 1.0
    #: weight applied to angular difference when using hybrid selection

    ignore_heroes: tuple[HeroIds, ...] = ()
    #: heroes ignored entirely when evaluating potential targets

    preferred_heroes: tuple[HeroIds, ...] = ()
    #: heroes that receive a favourable weighting when scoring

    preferred_hero_weight: float = 0.75
    #: multiplier applied to scores for heroes listed in ``preferred_heroes``

    low_health_bonus: float = 0.0
    #: score reduction applied to enemies below ``low_health_threshold``

    low_health_threshold: int = 75
    #: health value where the low health bonus applies in full

    reaction_delay_enabled: bool = False
    #: if ``True`` pause briefly after switching targets to mimic reactions

    reaction_delay_min: float = 0.05
    #: minimum seconds to wait before aiming after switching targets

    reaction_delay_max: float = 0.15
    #: maximum seconds to wait before aiming after switching targets

    jitter_enabled: bool = False
    #: enable subtle random offsets once fully locked on

    jitter_amount: float = 0.15
    #: maximum random angular offset (degrees) when jitter updates

    jitter_interval: float = 0.18
    #: seconds between jitter updates while active

    jitter_max_fov: Optional[float] = 2.0
    #: only apply jitter when combined FOV is below this angle

    def __post_init__(self) -> None:
        """Normalise configuration values supplied by external callers."""

        self.target_select_type = self.target_select_type.lower()
        self.activation_mode = self.activation_mode.lower()
        self.smoothing_mode = self.smoothing_mode.lower()
        self.auto_fire_button = self.auto_fire_button.lower()
        self.headshot_probability = min(max(self.headshot_probability, 0.0), 1.0)

        if self.max_target_distance is not None and self.max_target_distance <= 0:
            self.max_target_distance = None

        if self.max_target_fov is not None and self.max_target_fov <= 0:
            self.max_target_fov = None

        if self.target_select_type not in {"distance", "fov", "hybrid"}:
            raise ValueError(
                "target_select_type must be 'distance', 'fov' or 'hybrid'"
            )

        if self.activation_mode not in {"hold", "toggle"}:
            raise ValueError("activation_mode must be 'hold' or 'toggle'")

        if self.smoothing_mode not in {"constant", "distance", "fov"}:
            raise ValueError(
                "smoothing_mode must be 'constant', 'distance' or 'fov'"
            )

        if self.min_smooth_speed < 0:
            self.min_smooth_speed = 0.0

        if self.min_smooth_speed > self.smooth_speed:
            self.min_smooth_speed = self.smooth_speed

        if self.yaw_smooth_scale < 0:
            self.yaw_smooth_scale = 0.0

        if self.pitch_smooth_scale < 0:
            self.pitch_smooth_scale = 0.0

        if self.distance_smoothing_min < 0:
            self.distance_smoothing_min = 0.0

        if self.distance_smoothing_max <= self.distance_smoothing_min:
            self.distance_smoothing_max = self.distance_smoothing_min + 0.01

        if self.fov_smoothing_min < 0:
            self.fov_smoothing_min = 0.0

        if self.fov_smoothing_max <= self.fov_smoothing_min:
            self.fov_smoothing_max = self.fov_smoothing_min + 0.01

        if self.lock_ramp_duration < 0:
            self.lock_ramp_duration = 0.0

        if self.lock_ramp_start_speed < 0:
            self.lock_ramp_start_speed = 0.0
        if self.lock_ramp_start_speed > self.smooth_speed:
            self.lock_ramp_start_speed = self.smooth_speed

        if self.target_switch_cooldown < 0:
            self.target_switch_cooldown = 0.0

        if self.target_switch_leeway < 0:
            self.target_switch_leeway = 0.0

        if self.auto_fire_button not in _MOUSE_BUTTON_FLAGS:
            raise ValueError(
                f"auto_fire_button must be one of {sorted(_MOUSE_BUTTON_FLAGS)}"
            )

        if self.aim_tolerance < 0:
            self.aim_tolerance = 0.0

        if self.target_distance_weight < 0:
            self.target_distance_weight = 0.0

        if self.target_fov_weight < 0:
            self.target_fov_weight = 0.0

        def _coerce_hero_sequence(
            name: str, raw: Sequence[Any] | HeroIds | str | int | None
        ) -> tuple[HeroIds, ...]:
            if raw is None:
                return ()
            if isinstance(raw, (str, HeroIds, int)):
                items: Iterable[Any] = (raw,)
            else:
                items = raw
            result: list[HeroIds] = []
            for item in items:
                try:
                    hero = _parse_hero_identifier(item)
                except ValueError as exc:
                    raise ValueError(f"{name} contains invalid hero: {exc}") from exc
                if hero not in result:
                    result.append(hero)
            return tuple(result)

        self.ignore_heroes = _coerce_hero_sequence("ignore_heroes", self.ignore_heroes)
        self.preferred_heroes = _coerce_hero_sequence(
            "preferred_heroes", self.preferred_heroes
        )

        if self.preferred_hero_weight < 0:
            self.preferred_hero_weight = 0.0

        if self.low_health_bonus < 0:
            self.low_health_bonus = 0.0

        if self.low_health_threshold < 0:
            self.low_health_threshold = 0

        if self.reaction_delay_min < 0:
            self.reaction_delay_min = 0.0

        if self.reaction_delay_max < 0:
            self.reaction_delay_max = 0.0

        if self.reaction_delay_max < self.reaction_delay_min:
            self.reaction_delay_min, self.reaction_delay_max = (
                self.reaction_delay_max,
                self.reaction_delay_min,
            )

        if self.headshot_cache_interval < 0:
            self.headshot_cache_interval = 0.0

        if self.headshot_force_duration < 0:
            self.headshot_force_duration = 0.0

        if self.headshot_reacquire_cooldown < 0:
            self.headshot_reacquire_cooldown = 0.0

        if self.jitter_amount < 0:
            self.jitter_amount = 0.0

        if self.jitter_interval <= 0:
            self.jitter_interval = 0.01

        if self.jitter_max_fov is not None and self.jitter_max_fov <= 0:
            self.jitter_max_fov = None


@dataclass(frozen=True)
class _HeroLockConfig:
    """Configuration describing how ability locks behave for a hero."""

    enabled_attr: str
    duration_attr: str
    key_attr: str
    description: str


@dataclass(frozen=True)
class _TargetMetrics:
    """Aggregated targeting metrics for a potential enemy entity."""

    distance: float
    yaw_delta: float
    pitch_delta: float

    @property
    def combined_fov(self) -> float:
        """Return the combined angular difference in degrees."""

        return math.hypot(self.yaw_delta, self.pitch_delta)


@dataclass(frozen=True)
class _ScoredTarget:
    """Bundle of metrics and scoring details for a candidate target."""

    metrics: _TargetMetrics
    score: float
    hero: HeroIds
    health: int


class Aimbot:
    """Basic aimbot controller."""

    _ABILITY_LOCKS: Dict[str, _HeroLockConfig] = {
        "GreyTalon": _HeroLockConfig(
            "grey_talon_lock_enabled",
            "grey_talon_lock",
            "grey_talon_key",
            "Grey Talon ability lock",
        ),
        "Yamato": _HeroLockConfig(
            "yamato_lock_enabled",
            "yamato_lock",
            "yamato_key",
            "Yamato ability lock",
        ),
        "Vindicta": _HeroLockConfig(
            "vindicta_lock_enabled",
            "vindicta_lock",
            "vindicta_key",
            "Vindicta ability lock",
        ),
    }

    def __init__(self, mem: DeadlockMemory, settings: AimbotSettings | None = None) -> None:
        """Create a new aimbot bound to ``mem``."""

        self.mem = mem
        self.settings = settings or AimbotSettings()
        self.locked_on: int | None = None
        self._lock_acquired_at: float = 0.0
        self.force_aim_until: float = 0.0
        self.paused = False
        self.stop_requested = False

        # Headshot decision caching
        self._headshot_cache: bool = False
        self._headshot_cache_time: float = 0.0
        self._headshot_cache_interval: float = max(
            self.settings.headshot_cache_interval, 0.0
        )

        self._force_head_until: float = 0.0
        self._last_lock_lost: float = 0.0

        # Paradox shortcut state
        self._paradox_next_e: float = 0.0
        self._paradox_r_held: bool = False

        # Auto fire and activation behaviour
        self._auto_fire_down_flag, self._auto_fire_up_flag = self._resolve_button_flags(
            self.settings.auto_fire_button
        )
        self._auto_fire_active: bool = False
        self._activation_toggle_state: bool = False
        self._activation_toggle_down: bool = False
        self._reaction_until: float = 0.0

        # Aim jitter state
        self._jitter_vector: tuple[float, float] = (0.0, 0.0)
        self._jitter_next_update: float = 0.0

        # Announcements for ignored heroes so logs stay readable
        self._ignored_hero_announced: set[HeroIds] = set()

        logger.info("Aimbot initialised with settings: %s", self.settings)

    @staticmethod
    def _is_key_pressed(key_code: int) -> bool:
        """Return ``True`` when the given ``key_code`` is currently pressed."""

        return win32api.GetKeyState(key_code) < 0

    @staticmethod
    def _describe_key(key_code: Optional[int]) -> str:
        """Return a human readable description for ``key_code``."""

        if key_code is None:
            return "no key"

        if key_code in _REVERSE_KEY_ALIASES:
            return _REVERSE_KEY_ALIASES[key_code].upper()

        if 0x30 <= key_code <= 0x5A:
            return chr(key_code)

        return f"VK {key_code}"

    @staticmethod
    def _angular_difference(target: float, current: float) -> float:
        """Return the absolute shortest angular distance between two angles."""

        diff = (target - current + 180.0) % 360.0 - 180.0
        return abs(diff)

    @staticmethod
    def _resolve_button_flags(button: str) -> tuple[int, int]:
        """Return ``(down, up)`` event flags for ``button``."""

        return _MOUSE_BUTTON_FLAGS[button]

    def _update_ability_lock(self, hero: HeroIds) -> None:
        """Extend ``force_aim_until`` when ability keys are pressed."""
        now = time.monotonic()
        hero_config = self._ABILITY_LOCKS.get(hero.name)
        if hero_config:
            enabled = getattr(self.settings, hero_config.enabled_attr)
            duration = getattr(self.settings, hero_config.duration_attr)
            key_code = getattr(self.settings, hero_config.key_attr)
            if enabled and duration > 0 and self._is_key_pressed(key_code):
                self.force_aim_until = max(self.force_aim_until, now + duration)
                logger.debug(
                    "%s triggered; holding until %.2f",
                    hero_config.description,
                    self.force_aim_until,
                )
        if hero.name == "Paradox" and self.settings.paradox_shortcut_enabled:
            self._handle_paradox_shortcut(now)

    def _handle_paradox_shortcut(self, now: float) -> None:
        """Trigger Paradox ``E`` after ``R`` is pressed."""
        if self._is_key_pressed(self.settings.paradox_r_key):
            if not self._paradox_r_held:
                self._paradox_r_held = True
                self._paradox_next_e = now + 0.05
        else:
            self._paradox_r_held = False

        if self._paradox_next_e and now >= self._paradox_next_e:
            win32api.keybd_event(self.settings.paradox_e_key, 0, 0, 0)
            win32api.keybd_event(
                self.settings.paradox_e_key, 0, win32con.KEYEVENTF_KEYUP, 0
            )
            logger.debug("Paradox shortcut triggered")
            self._paradox_next_e = 0.0

    def _activation_active(self) -> bool:
        """Return ``True`` when the activation key requests aiming."""

        key_code = self.settings.activation_key
        if key_code is None:
            return False

        pressed = self._is_key_pressed(key_code)
        if self.settings.activation_mode == "toggle":
            if pressed and not self._activation_toggle_down:
                self._activation_toggle_state = not self._activation_toggle_state
                logger.info(
                    "Activation toggled %s",
                    "on" if self._activation_toggle_state else "off",
                )
            self._activation_toggle_down = pressed
            return self._activation_toggle_state

        return pressed

    def _update_auto_fire(self) -> bool:
        """Handle simulated firing behaviour and return active state."""

        if (
            not self.settings.auto_fire_enabled
            or self.settings.auto_fire_key is None
        ):
            if self._auto_fire_active:
                ctypes.windll.user32.mouse_event(self._auto_fire_up_flag, 0, 0, 0, 0)
                self._auto_fire_active = False
            return False

        pressed = self._is_key_pressed(self.settings.auto_fire_key)
        if pressed and not self._auto_fire_active:
            ctypes.windll.user32.mouse_event(self._auto_fire_down_flag, 0, 0, 0, 0)
            self._auto_fire_active = True
        elif not pressed and self._auto_fire_active:
            ctypes.windll.user32.mouse_event(self._auto_fire_up_flag, 0, 0, 0, 0)
            self._auto_fire_active = False

        return self._auto_fire_active

    def _schedule_reaction_delay(self, now: float) -> None:
        """Update the reaction delay timer after a target switch."""

        if not self.settings.reaction_delay_enabled:
            self._reaction_until = 0.0
            return

        delay = random.uniform(
            self.settings.reaction_delay_min,
            self.settings.reaction_delay_max,
        )
        self._reaction_until = now + delay
        logger.debug("Reaction delay triggered for %.3fs", delay)

    def _reset_jitter(self) -> None:
        """Clear any active jitter offsets."""

        self._jitter_vector = (0.0, 0.0)
        self._jitter_next_update = 0.0

    def _resolve_jitter_offset(
        self, now: float, metrics: Optional[_TargetMetrics]
    ) -> tuple[float, float]:
        """Return yaw/pitch offsets representing micro jitter."""

        if (
            not self.settings.jitter_enabled
            or metrics is None
            or self.settings.jitter_amount <= 0.0
        ):
            self._reset_jitter()
            return 0.0, 0.0

        if (
            self.settings.jitter_max_fov is not None
            and metrics.combined_fov > self.settings.jitter_max_fov
        ):
            self._reset_jitter()
            return 0.0, 0.0

        if now >= self._jitter_next_update or self._jitter_next_update == 0.0:
            amount = self.settings.jitter_amount
            yaw_offset = random.uniform(-amount, amount)
            pitch_offset = random.uniform(-amount, amount)
            self._jitter_vector = (yaw_offset, pitch_offset)
            self._jitter_next_update = now + self.settings.jitter_interval

        return self._jitter_vector

    def should_aim_for_head(self) -> bool:
        """Return ``True`` if the bot should attempt a headshot.

        Caches the random decision based on ``headshot_cache_interval`` to avoid
        frequent changes.
        """
        current_time = time.monotonic()

        if self.settings.headshot_on_acquire and current_time < self._force_head_until:
            return True

        if current_time - self._headshot_cache_time >= self._headshot_cache_interval:
            self._headshot_cache = random.random() < self.settings.headshot_probability
            self._headshot_cache_time = current_time

        return self._headshot_cache

    def _score_target(
        self,
        select_type: str,
        metrics: _TargetMetrics,
        hero: HeroIds,
        health: int,
    ) -> float:
        """Return a comparable score for ``hero`` based on ``select_type``."""

        if select_type == "distance":
            score = metrics.distance * self.settings.target_distance_weight
        elif select_type == "hybrid":
            score = (
                metrics.distance * self.settings.target_distance_weight
                + metrics.combined_fov * self.settings.target_fov_weight
            )
        else:
            score = metrics.combined_fov * self.settings.target_fov_weight

        if hero in self.settings.preferred_heroes:
            score *= self.settings.preferred_hero_weight

        if (
            self.settings.low_health_bonus > 0.0
            and self.settings.low_health_threshold > 0
            and health < self.settings.low_health_threshold
        ):
            deficit = self.settings.low_health_threshold - max(health, 0)
            fraction = min(deficit / self.settings.low_health_threshold, 1.0)
            score -= fraction * self.settings.low_health_bonus

        return score

    def _collect_candidate_metrics(
        self,
        my_data: Dict[str, Any],
        cam_pos: Vector3,
        current_yaw: float,
        current_pitch: float,
        select_type: str,
    ) -> Dict[int, _ScoredTarget]:
        """Return mapping of entity index to metrics and score."""

        candidates: Dict[int, _ScoredTarget] = {}
        for idx in range(1, 16):
            try:
                data = self.mem.read_entity(idx)
            except Exception:
                continue
            hero = data["hero"]
            if data["team"] == my_data["team"] or data["health"] <= 0:
                continue
            if hero in self.settings.ignore_heroes:
                if hero not in self._ignored_hero_announced:
                    logger.info("Ignoring hero %s due to configuration", hero.name)
                    self._ignored_hero_announced.add(hero)
                continue
            metrics = self._evaluate_target(
                my_data, data, cam_pos, current_yaw, current_pitch
            )
            if metrics is None:
                continue
            score = self._score_target(select_type, metrics, hero, data["health"])
            candidates[idx] = _ScoredTarget(metrics, score, hero, data["health"])
        return candidates

    def _evaluate_target(
        self,
        my_data: Dict[str, Any],
        target: Dict[str, Any],
        cam_pos: Vector3,
        current_yaw: float,
        current_pitch: float,
    ) -> Optional[_TargetMetrics]:
        """Return targeting metrics when ``target`` satisfies configured limits."""

        distance = math.dist(my_data["position"], target["position"])
        target_yaw, target_pitch = calculate_camera_rotation(cam_pos, target["position"])
        yaw_delta = self._angular_difference(target_yaw, current_yaw)
        pitch_delta = self._angular_difference(-target_pitch, current_pitch)
        metrics = _TargetMetrics(distance, yaw_delta, pitch_delta)
        if not self._within_target_constraints(metrics):
            return None
        return metrics

    def _within_target_constraints(self, metrics: _TargetMetrics) -> bool:
        """Return ``True`` when ``metrics`` pass distance and FOV limits."""

        if (
            self.settings.max_target_distance is not None
            and metrics.distance > self.settings.max_target_distance
        ):
            return False
        if (
            self.settings.max_target_fov is not None
            and metrics.combined_fov > self.settings.max_target_fov
        ):
            return False
        return True

    def _compute_smooth_speed(self, metrics: Optional[_TargetMetrics]) -> float:
        """Return the smoothing speed to apply for ``metrics``."""

        mode = self.settings.smoothing_mode
        if metrics is None or mode == "constant":
            return self._apply_lock_ramp(self.settings.smooth_speed)

        if mode == "distance":
            distance = metrics.distance
            min_distance = self.settings.distance_smoothing_min
            max_distance = self.settings.distance_smoothing_max
            if max_distance <= min_distance:
                return self._apply_lock_ramp(self.settings.smooth_speed)

            clamped = min(max(distance, min_distance), max_distance)
            ratio = (clamped - min_distance) / (max_distance - min_distance)
        elif mode == "fov":
            angle = metrics.combined_fov
            min_angle = self.settings.fov_smoothing_min
            max_angle = self.settings.fov_smoothing_max
            if max_angle <= min_angle:
                return self._apply_lock_ramp(self.settings.smooth_speed)

            clamped = min(max(angle, min_angle), max_angle)
            ratio = (clamped - min_angle) / (max_angle - min_angle)
        else:
            return self._apply_lock_ramp(self.settings.smooth_speed)

        blended = (
            self.settings.min_smooth_speed
            + (self.settings.smooth_speed - self.settings.min_smooth_speed) * ratio
        )
        return self._apply_lock_ramp(blended)

    def _apply_lock_ramp(self, base_speed: float) -> float:
        """Return ``base_speed`` adjusted by the current lock ramp settings."""

        if (
            base_speed <= 0
            or self.settings.lock_ramp_duration <= 0
            or self._lock_acquired_at == 0.0
            or self.locked_on is None
        ):
            return base_speed

        start_speed = max(
            0.0, min(self.settings.lock_ramp_start_speed, base_speed)
        )
        if start_speed >= base_speed:
            return base_speed

        elapsed = max(0.0, time.monotonic() - self._lock_acquired_at)
        if elapsed >= self.settings.lock_ramp_duration:
            return base_speed

        ratio = elapsed / self.settings.lock_ramp_duration
        return start_speed + (base_speed - start_speed) * ratio

    def pause(self) -> None:
        """Pause the aimbot."""
        self.paused = True
        logger.info("Aimbot paused")
    
    def resume(self) -> None:
        """Resume the aimbot."""
        self.paused = False
        logger.info("Aimbot resumed")
    
    def stop(self) -> None:
        """Request the aimbot to stop."""
        self.stop_requested = True
        logger.info("Aimbot stop requested")

    def run(self) -> None:
        """Main aimbot loop."""

        if self.settings.activation_key is None:
            activation_hint = "ability locks only"
        else:
            verb = "hold" if self.settings.activation_mode == "hold" else "press"
            activation_hint = f"{verb} {self._describe_key(self.settings.activation_key)}"
        logger.info("Aimbot loop started - %s to aim", activation_hint)
        active = False
        log_state_changes = False
        prev_locked = None
        while not self.stop_requested:
            try:
                # Check if paused
                if self.paused:
                    time.sleep(0.1)
                    continue

                my_data = self.mem.read_entity(0)
                self._update_ability_lock(my_data["hero"])
                my_aim_angle = my_data["aim_angle"]

                auto_fire_active = self._update_auto_fire()
                if auto_fire_active and self.settings.auto_fire_blocks_aim:
                    self._reset_jitter()
                    time.sleep(0.01)
                    continue

                now = time.monotonic()
                mouse_down = self._activation_active() or now < self.force_aim_until
                if not log_state_changes and not mouse_down:
                    log_state_changes = True
                    active = False
                elif mouse_down != active:
                    active = mouse_down
                    if log_state_changes:
                        logger.info("Aimbot turned %s", "on" if active else "off")
                if not mouse_down:
                    # Left mouse button is not held and no ability lock active
                    if self.locked_on is not None:
                        self._last_lock_lost = time.monotonic()
                    self.locked_on = None
                    self._lock_acquired_at = 0.0
                    self._reaction_until = 0.0
                    self._reset_jitter()
                    time.sleep(0.01)
                    continue

                cam_pos = self.mem.camera_position()
                current_yaw, current_pitch = self.mem.current_angles()

                select_type = self.settings.target_select_type.lower()
                candidates = self._collect_candidate_metrics(
                    my_data, cam_pos, current_yaw, current_pitch, select_type
                )
                active_metrics: Optional[_TargetMetrics] = None
                active_candidate: Optional[_ScoredTarget] = None
                old_target = self.locked_on
                switched_target = False

                if candidates:
                    best_idx, best_entry = min(
                        candidates.items(), key=lambda item: item[1].score
                    )
                    chosen_idx = best_idx
                    chosen_entry = best_entry
                    best_score = best_entry.score
                    if old_target is None:
                        switched_target = True
                    else:
                        locked_entry = candidates.get(old_target)
                        if locked_entry is None:
                            switched_target = True
                            self._last_lock_lost = now
                            logger.debug(
                                "Current target %d no longer valid; reacquiring",
                                old_target,
                            )
                        else:
                            if best_idx != old_target:
                                improvement = locked_entry.score - best_score
                                elapsed = now - self._lock_acquired_at
                                threshold = (
                                    self.settings.target_switch_leeway
                                    if elapsed < self.settings.target_switch_cooldown
                                    else 1e-6
                                )
                                if improvement < threshold:
                                    chosen_idx = old_target
                                    chosen_entry = locked_entry
                                    best_score = locked_entry.score
                                else:
                                    switched_target = True
                            else:
                                chosen_idx = old_target
                                chosen_entry = locked_entry
                                best_score = locked_entry.score

                    self.locked_on = chosen_idx
                    if self.locked_on is not None:
                        active_candidate = chosen_entry
                        active_metrics = chosen_entry.metrics
                    if self.locked_on is not None and switched_target:
                        if (
                            self.settings.headshot_on_acquire
                            and self.settings.headshot_force_duration > 0
                            and (
                                now - self._last_lock_lost
                                > self.settings.headshot_reacquire_cooldown
                            )
                        ):
                            self._force_head_until = (
                                now + self.settings.headshot_force_duration
                            )
                        self._lock_acquired_at = now
                        self._schedule_reaction_delay(now)
                        self._reset_jitter()
                    elif self.locked_on is not None:
                        if self._lock_acquired_at == 0.0:
                            self._lock_acquired_at = now
                        self._reaction_until = 0.0
                else:
                    if self.locked_on is not None:
                        self._last_lock_lost = now
                    self.locked_on = None
                    self._lock_acquired_at = 0.0
                    self._reaction_until = 0.0
                    self._reset_jitter()

                if prev_locked != self.locked_on:
                    if self.locked_on is None:
                        if prev_locked is not None:
                            logger.debug("Lost target")
                    else:
                        hero_name = (
                            active_candidate.hero.name
                            if active_candidate is not None
                            else "unknown"
                        )
                        if prev_locked is not None:
                            logger.debug(
                                "Changed target from %d to %d (%s)",
                                prev_locked,
                                self.locked_on,
                                hero_name,
                            )
                        else:
                            logger.debug(
                                "Locked on target %d (%s)",
                                self.locked_on,
                                hero_name,
                            )
                        if (
                            active_candidate is not None
                            and active_candidate.hero in self.settings.preferred_heroes
                        ):
                            logger.debug(
                                "Preferred hero weighting %.2f applied to %s",
                                self.settings.preferred_hero_weight,
                                hero_name,
                            )
                    prev_locked = self.locked_on

                if self.locked_on is None:
                    self._reset_jitter()
                    time.sleep(0.01)
                    continue

                try:
                    target = self.mem.read_entity(self.locked_on)
                except Exception:
                    logger.debug(
                        "Failed to read entity %s; losing target", self.locked_on
                    )
                    if self.locked_on is not None:
                        self._last_lock_lost = time.monotonic()
                    self.locked_on = None
                    self._lock_acquired_at = 0.0
                    self._reaction_until = 0.0
                    self._reset_jitter()
                    continue

                now = time.monotonic()
                if (
                    self._reaction_until
                    and self.settings.reaction_delay_enabled
                    and now < self._reaction_until
                ):
                    time.sleep(0.001)
                    continue

                self._reaction_until = 0.0

                bone_index = (
                    get_head_bone_index(target["hero"])
                    if self.should_aim_for_head()
                    else get_body_bone_index(target["hero"])
                )
                if bone_index is not None:
                    bone_array = self.mem.read_longlong(
                        target["node"] + mo.BONE_ARRAY
                    )
                    head_vector = (
                        self.mem.read_float(bone_array + bone_index * mo.BONE_STEP),
                        self.mem.read_float(bone_array + bone_index * mo.BONE_STEP + 4),
                        self.mem.read_float(bone_array + bone_index * mo.BONE_STEP + 8),
                    )
                    target_pos = head_vector
                else:
                    target_pos = target["position"]

                # Gradually rotate the camera towards the desired angles for a
                # slightly more human-like movement.
                yaw, pitch = calculate_camera_rotation(cam_pos, target_pos)
                smooth_speed = self._compute_smooth_speed(active_metrics)
                jitter_yaw, jitter_pitch = self._resolve_jitter_offset(now, active_metrics)
                within_tolerance = (
                    active_metrics is not None
                    and self.settings.aim_tolerance > 0
                    and active_metrics.combined_fov <= self.settings.aim_tolerance
                )
                if (
                    within_tolerance
                    and not (
                        self.settings.jitter_enabled
                        and self.settings.jitter_amount > 0
                    )
                ):
                    time.sleep(0.001)
                    continue
                yaw += jitter_yaw
                pitch += jitter_pitch
                yaw_limit = smooth_speed * self.settings.yaw_smooth_scale
                pitch_limit = smooth_speed * self.settings.pitch_smooth_scale
                new_yaw, new_pitch = calculate_new_camera_angles(
                    current_yaw,
                    current_pitch,
                    yaw,
                    -pitch,
                    smooth_speed,
                    max_yaw_change=yaw_limit,
                    max_pitch_change=pitch_limit,
                )
                self.mem.set_angles(new_yaw, new_pitch, my_aim_angle)
                time.sleep(0.001)
            except Exception as exc:
                logger.exception("Aimbot loop error: %s", exc)
                time.sleep(0.01)
            
        logger.info("Aimbot loop ended")


def parse_key_spec(value: str) -> int:
    """Return the Windows virtual-key code described by ``value``."""

    spec = value.strip()
    if not spec:
        raise ValueError("key specification cannot be empty")

    lowered = spec.lower()
    if lowered in _KEY_ALIASES:
        return _KEY_ALIASES[lowered]

    if len(spec) == 1:
        return ord(spec.upper())

    if lowered.startswith("0x"):
        return int(lowered, 16)

    if lowered.startswith("vk") and lowered[2:].isdigit():
        return int(lowered[2:], 10)

    if lowered.isdigit():
        return int(lowered, 10)

    raise ValueError(f"unrecognised key spec '{value}'")


def _load_config_file(path: Path) -> Dict[str, Any]:
    """Return dictionary settings loaded from ``path``."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SystemExit(f"Configuration {path} does not exist") from exc
    except OSError as exc:
        raise SystemExit(f"Failed to read configuration {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse configuration {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Configuration {path} must contain a JSON object")

    return data


def build_settings_from_args(args: argparse.Namespace) -> AimbotSettings:
    """Create :class:`AimbotSettings` from command line arguments."""

    settings_data: Dict[str, Any] = {}
    if args.config:
        settings_data.update(_load_config_file(args.config))

    if args.headshot_probability is not None:
        settings_data["headshot_probability"] = args.headshot_probability
    if args.target_select:
        settings_data["target_select_type"] = args.target_select
    if args.smooth_speed is not None:
        settings_data["smooth_speed"] = args.smooth_speed
    if args.min_smooth_speed is not None:
        settings_data["min_smooth_speed"] = args.min_smooth_speed
    if args.smoothing_mode:
        settings_data["smoothing_mode"] = args.smoothing_mode
    if args.yaw_smooth_scale is not None:
        settings_data["yaw_smooth_scale"] = args.yaw_smooth_scale
    if args.pitch_smooth_scale is not None:
        settings_data["pitch_smooth_scale"] = args.pitch_smooth_scale
    if args.distance_smoothing_min is not None:
        settings_data["distance_smoothing_min"] = args.distance_smoothing_min
    if args.distance_smoothing_max is not None:
        settings_data["distance_smoothing_max"] = args.distance_smoothing_max
    if args.fov_smoothing_min is not None:
        settings_data["fov_smoothing_min"] = args.fov_smoothing_min
    if args.fov_smoothing_max is not None:
        settings_data["fov_smoothing_max"] = args.fov_smoothing_max
    if args.lock_ramp_duration is not None:
        settings_data["lock_ramp_duration"] = args.lock_ramp_duration
    if args.lock_ramp_start_speed is not None:
        settings_data["lock_ramp_start_speed"] = args.lock_ramp_start_speed
    if args.activation_mode:
        settings_data["activation_mode"] = args.activation_mode
    if args.disable_headshot_on_acquire:
        settings_data["headshot_on_acquire"] = False
    if args.headshot_cache_interval is not None:
        settings_data["headshot_cache_interval"] = args.headshot_cache_interval
    if args.headshot_force_duration is not None:
        settings_data["headshot_force_duration"] = args.headshot_force_duration
    if args.headshot_reacquire_cooldown is not None:
        settings_data[
            "headshot_reacquire_cooldown"
        ] = args.headshot_reacquire_cooldown

    if args.auto_fire_button:
        settings_data["auto_fire_button"] = args.auto_fire_button
    if args.disable_auto_fire:
        settings_data["auto_fire_enabled"] = False
    if args.allow_auto_fire_aim:
        settings_data["auto_fire_blocks_aim"] = False

    if args.max_target_distance is not None:
        settings_data["max_target_distance"] = args.max_target_distance
    if args.max_target_fov is not None:
        settings_data["max_target_fov"] = args.max_target_fov
    if args.aim_tolerance is not None:
        settings_data["aim_tolerance"] = args.aim_tolerance
    if args.target_distance_weight is not None:
        settings_data["target_distance_weight"] = args.target_distance_weight
    if args.target_fov_weight is not None:
        settings_data["target_fov_weight"] = args.target_fov_weight

    def _merge_hero_option(field: str, values: Optional[Sequence[str]]) -> None:
        if not values:
            return
        existing = settings_data.get(field)
        if existing is None:
            merged: list[Any] = []
        elif isinstance(existing, list):
            merged = existing[:]
        elif isinstance(existing, tuple):
            merged = list(existing)
        else:
            merged = [existing]
        merged.extend(values)
        settings_data[field] = merged

    _merge_hero_option("ignore_heroes", args.ignore_heroes)
    _merge_hero_option("preferred_heroes", args.preferred_heroes)

    if args.preferred_hero_weight is not None:
        settings_data["preferred_hero_weight"] = args.preferred_hero_weight
    if args.low_health_bonus is not None:
        settings_data["low_health_bonus"] = args.low_health_bonus
    if args.low_health_threshold is not None:
        settings_data["low_health_threshold"] = args.low_health_threshold
    if args.reaction_delay is not None:
        settings_data["reaction_delay_enabled"] = args.reaction_delay
    if args.reaction_delay_min is not None:
        settings_data["reaction_delay_min"] = args.reaction_delay_min
    if args.reaction_delay_max is not None:
        settings_data["reaction_delay_max"] = args.reaction_delay_max
    if args.aim_jitter is not None:
        settings_data["jitter_enabled"] = args.aim_jitter
    if args.jitter_amount is not None:
        settings_data["jitter_amount"] = args.jitter_amount
    if args.jitter_interval is not None:
        settings_data["jitter_interval"] = args.jitter_interval
    if args.jitter_max_fov is not None:
        settings_data["jitter_max_fov"] = args.jitter_max_fov
    if args.target_switch_cooldown is not None:
        settings_data["target_switch_cooldown"] = args.target_switch_cooldown
    if args.target_switch_leeway is not None:
        settings_data["target_switch_leeway"] = args.target_switch_leeway

    if args.grey_talon_lock is not None:
        settings_data["grey_talon_lock"] = args.grey_talon_lock
    if args.disable_grey_talon_lock:
        settings_data["grey_talon_lock_enabled"] = False

    if args.yamato_lock is not None:
        settings_data["yamato_lock"] = args.yamato_lock
    if args.disable_yamato_lock:
        settings_data["yamato_lock_enabled"] = False

    if args.vindicta_lock is not None:
        settings_data["vindicta_lock"] = args.vindicta_lock
    if args.disable_vindicta_lock:
        settings_data["vindicta_lock_enabled"] = False

    if args.paradox_shortcut is not None:
        settings_data["paradox_shortcut_enabled"] = args.paradox_shortcut

    key_overrides = [
        ("activation_key", args.activation_key, True),
        ("auto_fire_key", args.auto_fire_key, True),
        ("grey_talon_key", args.grey_talon_key, False),
        ("yamato_key", args.yamato_key, False),
        ("vindicta_key", args.vindicta_key, False),
        ("paradox_r_key", args.paradox_r_key, False),
        ("paradox_e_key", args.paradox_e_key, False),
    ]

    for field, value, allow_none in key_overrides:
        if value is None:
            continue
        if allow_none and value.lower() == "none":
            settings_data[field] = None
            if field == "auto_fire_key":
                settings_data["auto_fire_enabled"] = False
            continue
        try:
            settings_data[field] = parse_key_spec(value)
        except ValueError as exc:
            flag = field.replace("_", "-")
            raise SystemExit(f"Invalid key for --{flag}: {exc}") from exc

    return AimbotSettings(**settings_data)


def main(argv: list[str] | None = None) -> None:
    """Run the aimbot entry point.

    Parameters
    ----------
    argv:
        Optional command line arguments.  ``--debug`` enables verbose output
        while ``--config`` points to a JSON file with persistent settings.
    """

    parser = argparse.ArgumentParser(description="Deadlock aimbot")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="path to JSON file providing aimbot settings overrides",
    )
    parser.add_argument(
        "--headshot-probability",
        type=float,
        help="chance of selecting headshots (0.0-1.0)",
    )
    parser.add_argument(
        "--target-select",
        choices=["distance", "fov", "hybrid"],
        help="target selection strategy",
    )
    parser.add_argument(
        "--smooth-speed",
        type=float,
        help="maximum degrees per frame when adjusting aim",
    )
    parser.add_argument(
        "--min-smooth-speed",
        type=float,
        help="minimum smoothing speed when adaptive smoothing is used",
    )
    parser.add_argument(
        "--smoothing-mode",
        choices=["constant", "distance", "fov"],
        help="strategy for deriving smoothing speed",
    )
    parser.add_argument(
        "--yaw-smooth-scale",
        type=float,
        help="multiplier applied to smoothing speed for yaw adjustments",
    )
    parser.add_argument(
        "--pitch-smooth-scale",
        type=float,
        help="multiplier applied to smoothing speed for pitch adjustments",
    )
    parser.add_argument(
        "--distance-smoothing-min",
        type=float,
        help="distance where the minimum smoothing speed applies",
    )
    parser.add_argument(
        "--distance-smoothing-max",
        type=float,
        help="distance where the maximum smoothing speed applies",
    )
    parser.add_argument(
        "--fov-smoothing-min",
        type=float,
        help="combined FOV angle where the minimum smoothing speed applies",
    )
    parser.add_argument(
        "--fov-smoothing-max",
        type=float,
        help="combined FOV angle where the maximum smoothing speed applies",
    )
    parser.add_argument(
        "--lock-ramp-duration",
        type=float,
        help="seconds smoothing speed takes to reach full strength after a lock",
    )
    parser.add_argument(
        "--lock-ramp-start-speed",
        type=float,
        help="initial smoothing speed immediately after acquiring a target",
    )
    parser.add_argument(
        "--activation-key",
        type=str,
        help="virtual key or alias to activate aiming (use 'none' to disable)",
    )
    parser.add_argument(
        "--activation-mode",
        choices=["hold", "toggle"],
        help="whether the activation key toggles or must be held",
    )
    parser.add_argument(
        "--auto-fire-key",
        type=str,
        help="virtual key or alias to trigger simulated firing ('none' disables)",
    )
    parser.add_argument(
        "--auto-fire-button",
        choices=sorted(_MOUSE_BUTTON_FLAGS.keys()),
        help="which mouse button to simulate while auto fire is active",
    )
    parser.add_argument(
        "--disable-auto-fire",
        action="store_true",
        help="disable auto fire regardless of trigger key",
    )
    parser.add_argument(
        "--allow-auto-fire-aim",
        action="store_true",
        help="continue aiming while auto fire is active",
    )
    parser.add_argument(
        "--max-target-distance",
        type=float,
        help="ignore targets further than this distance",
    )
    parser.add_argument(
        "--max-target-fov",
        type=float,
        help="ignore targets outside the combined FOV angle",
    )
    parser.add_argument(
        "--aim-tolerance",
        type=float,
        help="stop adjusting aim once within this combined FOV angle",
    )
    parser.add_argument(
        "--aim-jitter",
        dest="aim_jitter",
        action="store_true",
        help="enable subtle random offsets once locked on",
    )
    parser.add_argument(
        "--no-aim-jitter",
        dest="aim_jitter",
        action="store_false",
        help="disable the subtle random offsets",
    )
    parser.add_argument(
        "--jitter-amount",
        type=float,
        help="maximum degrees added to yaw/pitch when jitter updates",
    )
    parser.add_argument(
        "--jitter-interval",
        type=float,
        help="seconds between jitter updates",
    )
    parser.add_argument(
        "--jitter-max-fov",
        type=float,
        help="only apply jitter once combined FOV falls below this angle",
    )
    parser.add_argument(
        "--target-distance-weight",
        type=float,
        help="weight applied to distance when using hybrid target selection",
    )
    parser.add_argument(
        "--target-fov-weight",
        type=float,
        help="weight applied to angle when using hybrid target selection",
    )
    parser.add_argument(
        "--ignore-hero",
        action="append",
        dest="ignore_heroes",
        help="hero name or ID to ignore (can be provided multiple times)",
    )
    parser.add_argument(
        "--prefer-hero",
        action="append",
        dest="preferred_heroes",
        help="hero name or ID to favour during selection",
    )
    parser.add_argument(
        "--preferred-hero-weight",
        type=float,
        help="score multiplier applied to heroes listed via --prefer-hero",
    )
    parser.add_argument(
        "--low-health-bonus",
        type=float,
        help="score reduction applied to enemies below the low health threshold",
    )
    parser.add_argument(
        "--low-health-threshold",
        type=int,
        help="health amount where the low health bonus reaches full effect",
    )
    parser.add_argument(
        "--reaction-delay-min",
        type=float,
        help="minimum seconds to wait before aiming after switching targets",
    )
    parser.add_argument(
        "--reaction-delay-max",
        type=float,
        help="maximum seconds to wait before aiming after switching targets",
    )
    parser.add_argument(
        "--reaction-delay",
        dest="reaction_delay",
        action="store_true",
        help="enable a short human-like delay after switching targets",
    )
    parser.add_argument(
        "--no-reaction-delay",
        dest="reaction_delay",
        action="store_false",
        help="disable the human-like delay after switching targets",
    )
    parser.add_argument(
        "--target-switch-cooldown",
        type=float,
        help="seconds to wait before switching to small improvements",
    )
    parser.add_argument(
        "--target-switch-leeway",
        type=float,
        help="score improvement required to switch during the cooldown",
    )
    parser.add_argument(
        "--disable-headshot-on-acquire",
        action="store_true",
        help="disable guaranteed headshots immediately after acquiring targets",
    )
    parser.add_argument(
        "--headshot-cache-interval",
        type=float,
        help="seconds to reuse the random headshot decision (0 rerolls each frame)",
    )
    parser.add_argument(
        "--headshot-force-duration",
        type=float,
        help="seconds to guarantee headshots after regaining a target",
    )
    parser.add_argument(
        "--headshot-reacquire-cooldown",
        type=float,
        help="downtime required before headshot forcing can trigger again",
    )
    parser.add_argument(
        "--grey-talon-key",
        type=str,
        help="override Grey Talon's ability lock key",
    )
    parser.add_argument(
        "--grey-talon-lock",
        type=float,
        help="seconds to hold aim after Grey Talon's ability",
    )
    parser.add_argument(
        "--disable-grey-talon-lock",
        action="store_true",
        help="disable Grey Talon's ability lock behaviour",
    )
    parser.add_argument(
        "--yamato-key",
        type=str,
        help="override Yamato's ability lock key",
    )
    parser.add_argument(
        "--yamato-lock",
        type=float,
        help="seconds to hold aim after Yamato's ability",
    )
    parser.add_argument(
        "--disable-yamato-lock",
        action="store_true",
        help="disable Yamato's ability lock behaviour",
    )
    parser.add_argument(
        "--vindicta-key",
        type=str,
        help="override Vindicta's ability lock key",
    )
    parser.add_argument(
        "--vindicta-lock",
        type=float,
        help="seconds to hold aim after Vindicta's ability",
    )
    parser.add_argument(
        "--disable-vindicta-lock",
        action="store_true",
        help="disable Vindicta's ability lock behaviour",
    )
    parser.add_argument(
        "--paradox-r-key",
        type=str,
        help="override Paradox combo start key",
    )
    parser.add_argument(
        "--paradox-e-key",
        type=str,
        help="override Paradox combo follow-up key",
    )
    parser.add_argument(
        "--paradox-shortcut",
        dest="paradox_shortcut",
        action="store_true",
        help="enable automatic Paradox combo shortcut",
    )
    parser.add_argument(
        "--no-paradox-shortcut",
        dest="paradox_shortcut",
        action="store_false",
        help="disable automatic Paradox combo shortcut",
    )
    parser.set_defaults(paradox_shortcut=None, reaction_delay=None, aim_jitter=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ensure_up_to_date()
    mem = DeadlockMemory()
    settings = build_settings_from_args(args)
    bot = Aimbot(mem, settings)
    bot.run()


if __name__ == "__main__":
    main()
