from __future__ import annotations

"""Modern Tkinter GUI for configuring and running the aimbot.

This module provides a lightweight, modernised interface using ttk
widgets, a custom dark theme, and an improved layout with a header,
tabbed content, and a status bar. Functionality remains the same while
presenting a cleaner, more professional experience.
"""

import logging
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from .aimbot import (
    Aimbot,
    AimbotSettings,
    available_auto_fire_buttons,
    describe_virtual_key,
    format_hero_list,
    list_virtual_key_aliases,
    parse_hero_list,
    parse_virtual_key,
)
from .memory import DeadlockMemory
from .gui_utils import (
    GUILogHandler,
    PresetInfo,
    delete_preset,
    export_settings,
    get_build_sha,
    import_preset,
    list_presets,
    load_preset,
    load_saved_settings,
    save_preset,
    save_settings,
)
from .update_checker import update_available, open_release_page




class AimbotApp:
    """Main application controller for the DeadUnlock GUI."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("DeadUnlock")
        self.root.geometry("860x580")
        self.root.minsize(720, 520)

        # Set window icon (best-effort)
        try:
            icon_path = os.path.join(
                os.path.dirname(__file__), "..", "img", "deadunlock_icon.png"
            )
            if os.path.exists(icon_path):
                icon = tk.PhotoImage(file=icon_path)
                self.root.iconphoto(True, icon)
        except Exception:
            pass

        self.settings = load_saved_settings()
        self.bot: Aimbot | None = None
        self.bot_thread: threading.Thread | None = None
        self.is_running = False
        self.is_paused = False
        self.key_aliases = list_virtual_key_aliases()
        self.auto_fire_buttons = available_auto_fire_buttons()
        self.presets: list[PresetInfo] = []
        self.selected_preset_path: str | None = None
        self.preset_var = tk.StringVar(value="")
        self.preset_combo: ttk.Combobox | None = None
        self.preset_message_var = tk.StringVar(
            value="Type a name and click Save to create a preset."
        )
        self.preset_message: ttk.Label | None = None
        self._init_variables()

        # Set up logging
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.log_handler = GUILogHandler(self.log_queue)
        self.log_handler.setFormatter(
            logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        )

        # Attach loggers
        aimbot_logger = logging.getLogger('deadlock.aimbot')
        aimbot_logger.addHandler(self.log_handler)
        aimbot_logger.setLevel(logging.INFO)

        offset_finder_logger = logging.getLogger('offset_finder')
        offset_finder_logger.addHandler(self.log_handler)
        offset_finder_logger.setLevel(logging.INFO)

        self._build_widgets()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Start log processing and do initial update check
        self._process_log_queue()
        self._notify_if_outdated()

    def _notify_if_outdated(self) -> None:
        """Show a warning dialog if the local repo is outdated."""
        if update_available():
            result = messagebox.askyesno(
                "Update available",
                "A newer DeadUnlock version is available. Open the download page?",
            )
            if result:
                open_release_page()

    def _init_variables(self) -> None:
        """Initialise Tk variables that mirror :class:`AimbotSettings`."""

        self.headshot_var = tk.DoubleVar(value=self.settings.headshot_probability)
        self.acquire_headshot_var = tk.BooleanVar(
            value=self.settings.headshot_on_acquire
        )
        self.target_var = tk.StringVar(value=self.settings.target_select_type)
        self.smooth_speed_var = tk.DoubleVar(value=self.settings.smooth_speed)
        self.min_smooth_speed_var = tk.DoubleVar(value=self.settings.min_smooth_speed)
        self.yaw_smooth_scale_var = tk.DoubleVar(
            value=self.settings.yaw_smooth_scale
        )
        self.pitch_smooth_scale_var = tk.DoubleVar(
            value=self.settings.pitch_smooth_scale
        )
        self.smoothing_mode_var = tk.StringVar(value=self.settings.smoothing_mode)
        self.distance_smoothing_min_var = tk.DoubleVar(
            value=self.settings.distance_smoothing_min
        )
        self.distance_smoothing_max_var = tk.DoubleVar(
            value=self.settings.distance_smoothing_max
        )
        self.fov_smoothing_min_var = tk.DoubleVar(
            value=self.settings.fov_smoothing_min
        )
        self.fov_smoothing_max_var = tk.DoubleVar(
            value=self.settings.fov_smoothing_max
        )
        self.lock_ramp_duration_var = tk.DoubleVar(
            value=self.settings.lock_ramp_duration
        )
        self.lock_ramp_start_speed_var = tk.DoubleVar(
            value=self.settings.lock_ramp_start_speed
        )
        self.target_switch_cooldown_var = tk.DoubleVar(
            value=self.settings.target_switch_cooldown
        )
        self.target_switch_leeway_var = tk.DoubleVar(
            value=self.settings.target_switch_leeway
        )
        self.activation_mode_var = tk.StringVar(value=self.settings.activation_mode)
        self.activation_key_var = tk.StringVar(
            value=describe_virtual_key(self.settings.activation_key)
        )
        self.auto_fire_enabled_var = tk.BooleanVar(value=self.settings.auto_fire_enabled)
        self.auto_fire_key_var = tk.StringVar(
            value=describe_virtual_key(self.settings.auto_fire_key)
        )
        self.auto_fire_button_var = tk.StringVar(value=self.settings.auto_fire_button)
        self.auto_fire_blocks_var = tk.BooleanVar(
            value=self.settings.auto_fire_blocks_aim
        )
        self.max_target_distance_var = tk.StringVar(
            value=self._format_optional_float(self.settings.max_target_distance)
        )
        self.max_target_fov_var = tk.StringVar(
            value=self._format_optional_float(self.settings.max_target_fov)
        )
        self.aim_tolerance_var = tk.DoubleVar(value=self.settings.aim_tolerance)
        self.target_distance_weight_var = tk.DoubleVar(
            value=self.settings.target_distance_weight
        )
        self.target_fov_weight_var = tk.DoubleVar(value=self.settings.target_fov_weight)
        self.low_health_bonus_var = tk.DoubleVar(value=self.settings.low_health_bonus)
        self.low_health_threshold_var = tk.IntVar(
            value=self.settings.low_health_threshold
        )
        self.reaction_delay_enabled_var = tk.BooleanVar(
            value=self.settings.reaction_delay_enabled
        )
        self.reaction_delay_min_var = tk.DoubleVar(
            value=self.settings.reaction_delay_min
        )
        self.reaction_delay_max_var = tk.DoubleVar(
            value=self.settings.reaction_delay_max
        )
        self.jitter_enabled_var = tk.BooleanVar(value=self.settings.jitter_enabled)
        self.jitter_amount_var = tk.DoubleVar(value=self.settings.jitter_amount)
        self.jitter_interval_var = tk.DoubleVar(value=self.settings.jitter_interval)
        self.jitter_max_fov_var = tk.StringVar(
            value=self._format_optional_float(self.settings.jitter_max_fov)
        )
        self.headshot_cache_interval_var = tk.DoubleVar(
            value=self.settings.headshot_cache_interval
        )
        self.headshot_force_duration_var = tk.DoubleVar(
            value=self.settings.headshot_force_duration
        )
        self.headshot_reacquire_cooldown_var = tk.DoubleVar(
            value=self.settings.headshot_reacquire_cooldown
        )
        self.grey_enabled = tk.BooleanVar(value=self.settings.grey_talon_lock_enabled)
        self.grey_key = tk.StringVar(
            value=self._format_key_char(self.settings.grey_talon_key)
        )
        self.yamato_enabled = tk.BooleanVar(value=self.settings.yamato_lock_enabled)
        self.yamato_key = tk.StringVar(
            value=self._format_key_char(self.settings.yamato_key)
        )
        self.vindicta_enabled = tk.BooleanVar(
            value=self.settings.vindicta_lock_enabled
        )
        self.vindicta_key = tk.StringVar(
            value=self._format_key_char(self.settings.vindicta_key)
        )
        self.paradox_enabled = tk.BooleanVar(
            value=self.settings.paradox_shortcut_enabled
        )
        self.paradox_r_key = tk.StringVar(
            value=self._format_key_char(self.settings.paradox_r_key)
        )
        self.paradox_e_key = tk.StringVar(
            value=self._format_key_char(self.settings.paradox_e_key)
        )
        self.preferred_hero_weight_var = tk.DoubleVar(
            value=self.settings.preferred_hero_weight
        )
        self.ignore_heroes_var = tk.StringVar(
            value=format_hero_list(self.settings.ignore_heroes)
        )
        self.preferred_heroes_var = tk.StringVar(
            value=format_hero_list(self.settings.preferred_heroes)
        )

    @staticmethod
    def _format_optional_float(value: float | None) -> str:
        """Format optional floats for entry widgets."""

        return "" if value is None else f"{value:g}"

    @staticmethod
    def _format_key_char(key_code: int | None) -> str:
        """Return a printable character for ``key_code`` if possible."""

        if not key_code:
            return ""
        try:
            char = chr(key_code)
        except (TypeError, ValueError):
            return ""
        return char.upper() if char.isprintable() else ""

    def _refresh_variables_from_settings(self) -> None:
        """Synchronise Tk variables with :attr:`settings`."""

        self.headshot_var.set(self.settings.headshot_probability)
        self.acquire_headshot_var.set(self.settings.headshot_on_acquire)
        self.target_var.set(self.settings.target_select_type)
        self.smooth_speed_var.set(self.settings.smooth_speed)
        self.min_smooth_speed_var.set(self.settings.min_smooth_speed)
        self.yaw_smooth_scale_var.set(self.settings.yaw_smooth_scale)
        self.pitch_smooth_scale_var.set(self.settings.pitch_smooth_scale)
        self.smoothing_mode_var.set(self.settings.smoothing_mode)
        self.distance_smoothing_min_var.set(self.settings.distance_smoothing_min)
        self.distance_smoothing_max_var.set(self.settings.distance_smoothing_max)
        self.fov_smoothing_min_var.set(self.settings.fov_smoothing_min)
        self.fov_smoothing_max_var.set(self.settings.fov_smoothing_max)
        self.lock_ramp_duration_var.set(self.settings.lock_ramp_duration)
        self.lock_ramp_start_speed_var.set(self.settings.lock_ramp_start_speed)
        self.target_switch_cooldown_var.set(self.settings.target_switch_cooldown)
        self.target_switch_leeway_var.set(self.settings.target_switch_leeway)
        self.activation_mode_var.set(self.settings.activation_mode)
        self.activation_key_var.set(describe_virtual_key(self.settings.activation_key))
        self.auto_fire_enabled_var.set(self.settings.auto_fire_enabled)
        self.auto_fire_key_var.set(describe_virtual_key(self.settings.auto_fire_key))
        self.auto_fire_button_var.set(self.settings.auto_fire_button)
        self.auto_fire_blocks_var.set(self.settings.auto_fire_blocks_aim)
        self.max_target_distance_var.set(
            self._format_optional_float(self.settings.max_target_distance)
        )
        self.max_target_fov_var.set(
            self._format_optional_float(self.settings.max_target_fov)
        )
        self.aim_tolerance_var.set(self.settings.aim_tolerance)
        self.target_distance_weight_var.set(self.settings.target_distance_weight)
        self.target_fov_weight_var.set(self.settings.target_fov_weight)
        self.low_health_bonus_var.set(self.settings.low_health_bonus)
        self.low_health_threshold_var.set(self.settings.low_health_threshold)
        self.reaction_delay_enabled_var.set(self.settings.reaction_delay_enabled)
        self.reaction_delay_min_var.set(self.settings.reaction_delay_min)
        self.reaction_delay_max_var.set(self.settings.reaction_delay_max)
        self.jitter_enabled_var.set(self.settings.jitter_enabled)
        self.jitter_amount_var.set(self.settings.jitter_amount)
        self.jitter_interval_var.set(self.settings.jitter_interval)
        self.jitter_max_fov_var.set(
            self._format_optional_float(self.settings.jitter_max_fov)
        )
        self.headshot_cache_interval_var.set(self.settings.headshot_cache_interval)
        self.headshot_force_duration_var.set(self.settings.headshot_force_duration)
        self.headshot_reacquire_cooldown_var.set(
            self.settings.headshot_reacquire_cooldown
        )
        self.grey_enabled.set(self.settings.grey_talon_lock_enabled)
        self.grey_key.set(self._format_key_char(self.settings.grey_talon_key))
        self.yamato_enabled.set(self.settings.yamato_lock_enabled)
        self.yamato_key.set(self._format_key_char(self.settings.yamato_key))
        self.vindicta_enabled.set(self.settings.vindicta_lock_enabled)
        self.vindicta_key.set(self._format_key_char(self.settings.vindicta_key))
        self.paradox_enabled.set(self.settings.paradox_shortcut_enabled)
        self.paradox_r_key.set(self._format_key_char(self.settings.paradox_r_key))
        self.paradox_e_key.set(self._format_key_char(self.settings.paradox_e_key))
        self.preferred_hero_weight_var.set(self.settings.preferred_hero_weight)
        self.ignore_heroes_var.set(format_hero_list(self.settings.ignore_heroes))
        self.preferred_heroes_var.set(
            format_hero_list(self.settings.preferred_heroes)
        )


    def _refresh_preset_list(self, select_path: str | None = None) -> None:
        """Update the preset combobox to reflect files on disk."""

        self.presets = list_presets()
        names = [info.name for info in self.presets]
        if self.preset_combo is not None:
            self.preset_combo["values"] = names
        target = select_path or self.selected_preset_path
        matched: PresetInfo | None = None
        if target:
            target_norm = os.path.normcase(os.path.abspath(target))
            for info in self.presets:
                if os.path.normcase(os.path.abspath(info.path)) == target_norm:
                    matched = info
                    break
        if matched:
            self.selected_preset_path = matched.path
            self.preset_var.set(matched.name)
        else:
            self.selected_preset_path = None
            if self.preset_var.get() not in names:
                self.preset_var.set("")
        if not self.presets and not self.preset_var.get():
            self._set_preset_message(
                "Type a name and click Save to create a preset."
            )


    def _get_preset_by_name(self, name: str) -> PresetInfo | None:
        """Return preset metadata for ``name`` if available."""

        sought = name.strip().lower()
        if not sought:
            return None
        for info in self.presets:
            if info.name.lower() == sought:
                return info
        return None


    def _set_preset_message(self, message: str, state: str = "info") -> None:
        """Update the preset helper message with optional tone."""

        colours = {
            "info": "#9ca3af",
            "success": "#10b981",
            "error": "#ef4444",
        }
        if hasattr(self, "preset_message") and self.preset_message is not None:
            self.preset_message.configure(foreground=colours.get(state, "#9ca3af"))
        self.preset_message_var.set(message)


    def _on_preset_selection(self, _event: object | None = None) -> None:
        """Update tracked preset path when the combobox selection changes."""

        info = self._get_preset_by_name(self.preset_var.get())
        if info:
            self.selected_preset_path = info.path
            self._set_preset_message(f"Selected preset '{info.name}'.")
        else:
            self.selected_preset_path = None
            self._set_preset_message(
                "Type a name and click Save to create a preset."
            )


    def _apply_settings(
        self, new_settings: AimbotSettings, *, preset: PresetInfo | None = None
    ) -> None:
        """Replace :attr:`settings` and refresh dependent widgets."""

        self.settings = new_settings
        self._refresh_variables_from_settings()
        self._on_headshot_change()
        self._update_headshot_warning()
        self.selected_preset_path = preset.path if preset else None
        self._refresh_preset_list(select_path=self.selected_preset_path)
        if preset:
            self.preset_var.set(preset.name)


    def _on_load_preset(self) -> None:
        """Load the preset selected in the combobox."""

        info = self._get_preset_by_name(self.preset_var.get())
        if not info:
            self._set_preset_message("Select a preset to load.", state="error")
            return
        try:
            settings = load_preset(info)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Preset error", f"Failed to load preset: {exc}")
            return
        self._apply_settings(settings, preset=info)
        save_settings(self.settings)
        self._set_preset_message(f"Loaded preset '{info.name}'.", state="success")


    def _on_save_preset(self) -> None:
        """Save the current configuration to the chosen preset."""

        name = self.preset_var.get().strip()
        if not name:
            self._set_preset_message("Enter a preset name before saving.", state="error")
            return
        try:
            self._apply_widget_values()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc))
            return
        existing = self._get_preset_by_name(name)
        path = existing.path if existing else self.selected_preset_path
        try:
            info = save_preset(name, self.settings, path=path)
        except OSError as exc:
            messagebox.showerror("Preset error", f"Failed to save preset: {exc}")
            return
        self.selected_preset_path = info.path
        self._refresh_preset_list(select_path=info.path)
        save_settings(self.settings)
        self._set_preset_message(f"Saved preset '{info.name}'.", state="success")


    def _on_delete_preset(self) -> None:
        """Delete the currently selected preset from disk."""

        info = self._get_preset_by_name(self.preset_var.get())
        if not info:
            self._set_preset_message("Select a preset to delete.", state="error")
            return
        if not messagebox.askyesno(
            "Delete preset", f"Remove preset '{info.name}' permanently?"
        ):
            return
        delete_preset(info)
        if self.selected_preset_path and os.path.abspath(info.path) == os.path.abspath(
            self.selected_preset_path
        ):
            self.selected_preset_path = None
        self._refresh_preset_list()
        self._set_preset_message(f"Deleted preset '{info.name}'.", state="info")


    def _on_import_preset(self) -> None:
        """Import a preset JSON file and apply it immediately."""

        path = filedialog.askopenfilename(
            title="Import preset",
            filetypes=(("Preset JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        try:
            info, settings = import_preset(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import failed", f"Could not import preset: {exc}")
            return
        self._apply_settings(settings, preset=info)
        save_settings(self.settings)
        self._set_preset_message(f"Imported preset '{info.name}'.", state="success")


    def _on_export_preset(self) -> None:
        """Export the current configuration to a JSON file."""

        try:
            self._apply_widget_values()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc))
            return
        path = filedialog.asksaveasfilename(
            title="Export preset",
            defaultextension=".json",
            filetypes=(("Preset JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        name = self.preset_var.get().strip() or "custom"
        try:
            export_settings(self.settings, path, name=name)
        except OSError as exc:
            messagebox.showerror("Export failed", f"Could not export preset: {exc}")
            return
        filename = os.path.basename(path)
        self._set_preset_message(
            f"Exported preset to {filename}.", state="success"
        )


    def _check_for_updates(self) -> None:
        """Manually check for updates and offer to update if available."""
        try:
            if update_available():
                result = messagebox.askyesno(
                    "Update Available",
                    "A newer DeadUnlock version is available. Open the download page?",
                )
                if result:
                    open_release_page()
            else:
                messagebox.showinfo(
                    "No Updates",
                    "You are running the latest version of DeadUnlock.",
                )
        except Exception as e:
            messagebox.showerror(
                "Update Check Failed",
                f"Failed to check for updates: {e}"
            )

    def _build_widgets(self) -> None:
        """Create and arrange the GUI widgets."""
        self._configure_style()
        self._create_menu()

        # Root grid: header, notebook, statusbar
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._build_header()
        self._build_tabs()
        self._build_statusbar()

    def _configure_style(self) -> None:
        """Create a clean dark ttk theme with accent buttons and sliders."""
        style = ttk.Style(self.root)
        base_theme = "clam" if "clam" in style.theme_names() else style.theme_use()
        if "deadunlock" not in style.theme_names():
            # Palette
            bg = "#0f1115"
            surface = "#151923"
            border = "#2a2f3a"
            fg = "#e5e7eb"
            muted = "#9ca3af"
            accent = "#2563eb"
            accent_active = "#1d4ed8"
            danger = "#ef4444"
            danger_active = "#dc2626"

            style.theme_create(
                "deadunlock",
                parent=base_theme,
                settings={
                    ".": {
                        "configure": {
                            "background": bg,
                            "foreground": fg,
                        }
                    },
                    "TFrame": {
                        "configure": {"background": bg}
                    },
                    "TLabelframe": {
                        "configure": {
                            "background": surface,
                            "bordercolor": border,
                            "relief": "groove",
                            "padding": 10,
                        }
                    },
                    "TLabelframe.Label": {
                        "configure": {"foreground": muted, "font": ("Segoe UI", 10, "bold")}
                    },
                    "TLabel": {
                        "configure": {"background": bg, "foreground": fg}
                    },
                    "TButton": {
                        "configure": {
                            "padding": 8,
                            "background": surface,
                            "foreground": fg,
                            "bordercolor": border,
                            "relief": "flat",
                        },
                        "map": {
                            "background": [
                                ("active", "#1f2430"),
                                ("disabled", surface),
                            ],
                            "foreground": [("disabled", muted)],
                        },
                    },
                    "Accent.TButton": {
                        "configure": {
                            "background": accent,
                            "foreground": "#ffffff",
                        },
                        "map": {
                            "background": [("active", accent_active)],
                            "foreground": [("disabled", "#c7d2fe")],
                        },
                    },
                    "Danger.TButton": {
                        "configure": {
                            "background": danger,
                            "foreground": "#ffffff",
                        },
                        "map": {
                            "background": [("active", danger_active)],
                            "foreground": [("disabled", "#fecaca")],
                        },
                    },
                    "TCheckbutton": {
                        "configure": {"background": bg, "foreground": fg, "padding": (2, 2)}
                    },
                    "TEntry": {
                        "configure": {"fieldbackground": surface, "foreground": fg}
                    },
                    "TCombobox": {
                        "configure": {
                            "selectbackground": surface,
                            "fieldbackground": surface,
                            "foreground": fg,
                        }
                    },
                    "Horizontal.TScale": {
                        "configure": {"background": bg},
                    },
                    "TNotebook": {
                        "configure": {"background": bg}
                    },
                    "TNotebook.Tab": {
                        "configure": {"padding": (12, 6)}
                    },
                },
            )
        style.theme_use("deadunlock" if "deadunlock" in style.theme_names() else base_theme)

        default_font = ("Segoe UI", 10)
        self.root.option_add("*Font", default_font)
        try:
            self.root.configure(bg="#0f1115")
        except Exception:
            pass

    def _create_menu(self) -> None:
        """Create the application menubar."""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(
            label="Check for Updates", command=self._check_for_updates
        )

    def _build_header(self) -> None:
        """Create the top header with title and primary controls."""
        header = ttk.Frame(self.root, padding=(12, 12, 12, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(2, weight=1)

        # Title + icon
        title_frame = ttk.Frame(header)
        title_frame.grid(row=0, column=0, sticky="w")
        try:
            icon_path = os.path.join(
                os.path.dirname(__file__), "..", "img", "deadunlock_icon.png"
            )
            if os.path.exists(icon_path):
                icon = tk.PhotoImage(file=icon_path)
                icon_label = ttk.Label(title_frame, image=icon)
                icon_label.image = icon  # keep reference
                icon_label.grid(row=0, column=0, padx=(0, 8))
        except Exception:
            pass
        ttk.Label(
            title_frame, text="DeadUnlock", font=("Segoe UI", 14, "bold")
        ).grid(row=0, column=1, sticky="w")

        # Spacer
        ttk.Frame(header).grid(row=0, column=1, padx=10)

        # Controls: single Start/Stop toggle for clarity
        controls = ttk.Frame(header)
        controls.grid(row=0, column=2, sticky="e")
        self.toggle_button = ttk.Button(
            controls,
            text="Start",
            style="Accent.TButton",
            command=self.toggle_run,
            width=14,
        )
        self.toggle_button.grid(row=0, column=0, padx=(0, 0))

    def _build_tabs(self) -> None:
        """Create the main notebook and its tabs."""
        self.notebook = ttk.Notebook(self.root)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 8))

        settings_tab = ttk.Frame(self.notebook, padding=10)
        log_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(settings_tab, text="Settings")
        self.notebook.add(log_tab, text="Logs")

        self._build_settings_frame(settings_tab)
        self._build_log_frame(log_tab)

    def _build_statusbar(self) -> None:
        """Create a slim status bar at the bottom."""
        bar = ttk.Frame(self.root, padding=(12, 6))
        bar.grid(row=2, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self.status_label = ttk.Label(bar, text="Status: Stopped", foreground="red")
        self.status_label.grid(row=0, column=0, sticky="w")

        sha = get_build_sha()
        ttk.Label(
            bar, text=f"build {sha}", font=("Segoe UI", 9)
        ).grid(row=0, column=1, sticky="e")

    def _build_settings_frame(self, parent: ttk.Frame) -> None:
        """Build the settings tab content."""
        parent.columnconfigure(0, weight=1)

        preset_frame = ttk.LabelFrame(parent, text="Presets")
        preset_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        preset_frame.columnconfigure(1, weight=1)

        ttk.Label(preset_frame, text="Preset").grid(row=0, column=0, sticky="w")
        self.preset_combo = ttk.Combobox(
            preset_frame,
            textvariable=self.preset_var,
            values=[info.name for info in self.presets],
            width=28,
        )
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=8)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selection)

        preset_buttons = ttk.Frame(preset_frame)
        preset_buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(
            preset_buttons,
            text="Load",
            command=self._on_load_preset,
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(
            preset_buttons,
            text="Save",
            style="Accent.TButton",
            command=self._on_save_preset,
        ).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(
            preset_buttons,
            text="Delete",
            style="Danger.TButton",
            command=self._on_delete_preset,
        ).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(
            preset_buttons,
            text="Import...",
            command=self._on_import_preset,
        ).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(
            preset_buttons,
            text="Export...",
            command=self._on_export_preset,
        ).grid(row=0, column=4)

        self.preset_message = ttk.Label(
            preset_frame,
            textvariable=self.preset_message_var,
            foreground="#9ca3af",
        )
        self.preset_message.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._refresh_preset_list()

        aim_frame = ttk.LabelFrame(parent, text="Aim Settings")
        aim_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        aim_frame.columnconfigure(1, weight=1)

        aim_row = 0
        ttk.Label(aim_frame, text="Headshot probability").grid(
            row=aim_row, column=0, sticky="w"
        )
        self.headshot_percent = ttk.Label(
            aim_frame, text=f"{int(self.headshot_var.get() * 100)}%"
        )
        self.headshot_percent.grid(row=aim_row, column=2, sticky="e")
        hs_scale = ttk.Scale(
            aim_frame,
            variable=self.headshot_var,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            length=220,
            command=lambda _evt=None: self._on_headshot_change(),
        )
        hs_scale.grid(row=aim_row, column=1, sticky="ew", padx=8)

        self.headshot_warning = ttk.Label(
            aim_frame,
            text="High headshot values may flag your account!",
            foreground="red",
        )
        aim_row += 1
        self.headshot_warning.grid(
            row=aim_row, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )
        self.headshot_warning.grid_remove()
        self._update_headshot_warning()

        aim_row += 1
        ttk.Checkbutton(
            aim_frame,
            text="Headshot on acquire",
            variable=self.acquire_headshot_var,
        ).grid(row=aim_row, column=0, columnspan=3, sticky="w", pady=(6, 0))

        aim_row += 1
        ttk.Label(aim_frame, text="Headshot cache (s)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.headshot_cache_interval_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Headshot force duration (s)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.headshot_force_duration_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Headshot reacquire cooldown (s)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.headshot_reacquire_cooldown_var,
            from_=0.0,
            to=10.0,
            increment=0.1,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Smoothing mode").grid(row=aim_row, column=0, sticky="w")
        ttk.Combobox(
            aim_frame,
            textvariable=self.smoothing_mode_var,
            values=["constant", "distance", "fov"],
            state="readonly",
            width=12,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Smooth speed").grid(row=aim_row, column=0, sticky="w")
        ttk.Spinbox(
            aim_frame,
            textvariable=self.smooth_speed_var,
            from_=0.5,
            to=50.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Minimum smooth speed").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.min_smooth_speed_var,
            from_=0.0,
            to=50.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Yaw speed multiplier").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.yaw_smooth_scale_var,
            from_=0.0,
            to=4.0,
            increment=0.05,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Pitch speed multiplier").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.pitch_smooth_scale_var,
            from_=0.0,
            to=4.0,
            increment=0.05,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Distance smoothing start (m)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.distance_smoothing_min_var,
            from_=0.0,
            to=100.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Distance smoothing end (m)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.distance_smoothing_max_var,
            from_=0.0,
            to=150.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="FOV smoothing start (deg)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.fov_smoothing_min_var,
            from_=0.0,
            to=30.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="FOV smoothing end (deg)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.fov_smoothing_max_var,
            from_=0.0,
            to=60.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Lock ramp duration (s)").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.lock_ramp_duration_var,
            from_=0.0,
            to=2.0,
            increment=0.05,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        aim_row += 1
        ttk.Label(aim_frame, text="Lock ramp start speed").grid(
            row=aim_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            aim_frame,
            textvariable=self.lock_ramp_start_speed_var,
            from_=0.0,
            to=50.0,
            increment=0.5,
            width=8,
        ).grid(row=aim_row, column=1, sticky="w", padx=8)

        target_frame = ttk.LabelFrame(parent, text="Targeting & Limits")
        target_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        target_frame.columnconfigure(1, weight=1)

        target_row = 0
        ttk.Label(target_frame, text="Target selection").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Combobox(
            target_frame,
            textvariable=self.target_var,
            values=["fov", "distance", "hybrid"],
            state="readonly",
            width=12,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Target switch cooldown (s)").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.target_switch_cooldown_var,
            from_=0.0,
            to=2.0,
            increment=0.05,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Target switch leeway").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.target_switch_leeway_var,
            from_=0.0,
            to=25.0,
            increment=0.5,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Aim tolerance (deg)").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.aim_tolerance_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Max target distance (m)").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Entry(
            target_frame,
            textvariable=self.max_target_distance_var,
            width=12,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Max target FOV (deg)").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Entry(
            target_frame,
            textvariable=self.max_target_fov_var,
            width=12,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Distance weight").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.target_distance_weight_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="FOV weight").grid(row=target_row, column=0, sticky="w")
        ttk.Spinbox(
            target_frame,
            textvariable=self.target_fov_weight_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Low health bonus").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.low_health_bonus_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(target_frame, text="Low health threshold").grid(
            row=target_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            target_frame,
            textvariable=self.low_health_threshold_var,
            from_=0,
            to=400,
            increment=5,
            width=8,
        ).grid(row=target_row, column=1, sticky="w", padx=8)

        target_row += 1
        ttk.Label(
            target_frame,
            text="Leave blank to disable distance or FOV limits.",
            foreground="#9ca3af",
        ).grid(row=target_row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        behaviour_frame = ttk.LabelFrame(parent, text="Activation & Behaviour")
        behaviour_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        behaviour_frame.columnconfigure(1, weight=1)

        beh_row = 0
        ttk.Label(behaviour_frame, text="Activation mode").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Combobox(
            behaviour_frame,
            textvariable=self.activation_mode_var,
            values=["hold", "toggle"],
            state="readonly",
            width=12,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(behaviour_frame, text="Activation key").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Combobox(
            behaviour_frame,
            textvariable=self.activation_key_var,
            values=self.key_aliases,
            width=12,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Checkbutton(
            behaviour_frame,
            text="Enable auto fire",
            variable=self.auto_fire_enabled_var,
        ).grid(row=beh_row, column=0, columnspan=2, sticky="w", pady=(6, 0))

        beh_row += 1
        ttk.Label(behaviour_frame, text="Auto fire key").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Combobox(
            behaviour_frame,
            textvariable=self.auto_fire_key_var,
            values=self.key_aliases,
            width=12,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(behaviour_frame, text="Auto fire button").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Combobox(
            behaviour_frame,
            textvariable=self.auto_fire_button_var,
            values=self.auto_fire_buttons,
            state="readonly",
            width=12,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Checkbutton(
            behaviour_frame,
            text="Pause aim while auto fire is active",
            variable=self.auto_fire_blocks_var,
        ).grid(row=beh_row, column=0, columnspan=2, sticky="w")

        beh_row += 1
        ttk.Checkbutton(
            behaviour_frame,
            text="Enable reaction delay",
            variable=self.reaction_delay_enabled_var,
        ).grid(row=beh_row, column=0, columnspan=2, sticky="w", pady=(6, 0))

        beh_row += 1
        ttk.Label(behaviour_frame, text="Reaction delay min (s)").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            behaviour_frame,
            textvariable=self.reaction_delay_min_var,
            from_=0.0,
            to=1.0,
            increment=0.01,
            width=8,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(behaviour_frame, text="Reaction delay max (s)").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            behaviour_frame,
            textvariable=self.reaction_delay_max_var,
            from_=0.0,
            to=1.5,
            increment=0.01,
            width=8,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Checkbutton(
            behaviour_frame,
            text="Enable jitter",
            variable=self.jitter_enabled_var,
        ).grid(row=beh_row, column=0, columnspan=2, sticky="w", pady=(6, 0))

        beh_row += 1
        ttk.Label(behaviour_frame, text="Jitter amount (deg)").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            behaviour_frame,
            textvariable=self.jitter_amount_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(behaviour_frame, text="Jitter interval (s)").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            behaviour_frame,
            textvariable=self.jitter_interval_var,
            from_=0.05,
            to=1.0,
            increment=0.01,
            width=8,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(behaviour_frame, text="Jitter max FOV (deg)").grid(
            row=beh_row, column=0, sticky="w"
        )
        ttk.Entry(
            behaviour_frame,
            textvariable=self.jitter_max_fov_var,
            width=12,
        ).grid(row=beh_row, column=1, sticky="w", padx=8)

        beh_row += 1
        ttk.Label(
            behaviour_frame,
            text="Type key names like mouse1 or F1, or press a single letter.",
            foreground="#9ca3af",
        ).grid(row=beh_row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        hero_frame = ttk.LabelFrame(parent, text="Hero Ability Locks")
        hero_frame.grid(row=4, column=0, sticky="ew", pady=(10, 0))

        hero_row = 0
        ttk.Label(hero_frame, text="Hero").grid(row=hero_row, column=0, sticky="w")
        ttk.Label(hero_frame, text="Key 1").grid(row=hero_row, column=1, sticky="w")
        ttk.Label(hero_frame, text="Key 2").grid(row=hero_row, column=2, sticky="w")
        hero_row += 1

        ttk.Checkbutton(
            hero_frame, text="Grey Talon", variable=self.grey_enabled
        ).grid(row=hero_row, column=0, sticky="w")
        ttk.Entry(hero_frame, textvariable=self.grey_key, width=4).grid(
            row=hero_row, column=1, sticky="w"
        )
        hero_row += 1

        ttk.Checkbutton(hero_frame, text="Yamato", variable=self.yamato_enabled).grid(
            row=hero_row, column=0, sticky="w"
        )
        ttk.Entry(hero_frame, textvariable=self.yamato_key, width=4).grid(
            row=hero_row, column=1, sticky="w"
        )
        hero_row += 1

        ttk.Checkbutton(
            hero_frame, text="Vindicta", variable=self.vindicta_enabled
        ).grid(row=hero_row, column=0, sticky="w")
        ttk.Entry(hero_frame, textvariable=self.vindicta_key, width=4).grid(
            row=hero_row, column=1, sticky="w"
        )
        hero_row += 1

        ttk.Checkbutton(hero_frame, text="Paradox", variable=self.paradox_enabled).grid(
            row=hero_row, column=0, sticky="w"
        )
        ttk.Entry(hero_frame, textvariable=self.paradox_r_key, width=4).grid(
            row=hero_row, column=1, sticky="w"
        )
        ttk.Entry(hero_frame, textvariable=self.paradox_e_key, width=4).grid(
            row=hero_row, column=2, sticky="w"
        )

        pref_frame = ttk.LabelFrame(parent, text="Hero Preferences")
        pref_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        pref_frame.columnconfigure(1, weight=1)

        pref_row = 0
        ttk.Label(pref_frame, text="Ignore heroes").grid(
            row=pref_row, column=0, sticky="w"
        )
        ttk.Entry(
            pref_frame,
            textvariable=self.ignore_heroes_var,
        ).grid(row=pref_row, column=1, sticky="ew", padx=8)

        pref_row += 1
        ttk.Label(pref_frame, text="Preferred heroes").grid(
            row=pref_row, column=0, sticky="w"
        )
        ttk.Entry(
            pref_frame,
            textvariable=self.preferred_heroes_var,
        ).grid(row=pref_row, column=1, sticky="ew", padx=8)

        pref_row += 1
        ttk.Label(pref_frame, text="Preferred hero weight").grid(
            row=pref_row, column=0, sticky="w"
        )
        ttk.Spinbox(
            pref_frame,
            textvariable=self.preferred_hero_weight_var,
            from_=0.0,
            to=5.0,
            increment=0.05,
            width=8,
        ).grid(row=pref_row, column=1, sticky="w", padx=8)

        pref_row += 1
        ttk.Label(
            pref_frame,
            text="Separate heroes with commas. Names, numbers or hex IDs work.",
            foreground="#9ca3af",
        ).grid(row=pref_row, column=0, columnspan=2, sticky="w", pady=(4, 0))

        button_frame = ttk.Frame(parent)
        button_frame.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        button_frame.columnconfigure(0, weight=1)

        ttk.Button(
            button_frame,
            text="Reset to defaults",
            command=self._reset_to_defaults,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            button_frame,
            text="Save settings",
            style="Accent.TButton",
            command=self._on_save_settings,
        ).grid(row=0, column=1, sticky="e")


    def _build_status_frame(self, parent: ttk.Frame) -> None:
        """Deprecated: status is now in the status bar."""
        # Kept for backward compatibility if referenced elsewhere.
        pass

    def _build_log_frame(self, parent: ttk.Frame) -> None:
        """Build the log tab with a dark console-like view."""
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            parent, width=50, height=20, state="disabled"
        )
        self.log_text.configure(
            background="#111318",
            foreground="#dcdcdc",
            insertbackground="#ffffff",
            borderwidth=0,
            relief="flat",
            padx=8,
            pady=8,
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(parent, text="Clear Log", command=self.clear_log).grid(
            row=1, column=0, sticky="e", pady=(8, 0)
        )

    def _add_build_label(self, parent: ttk.Frame) -> None:
        """Deprecated: build label is shown in status bar."""
        # Preserved to avoid accidental removal; no-op now.
        pass
    def _process_log_queue(self) -> None:
        """Process log messages from queue and display them."""
        try:
            while True:
                log_message = self.log_queue.get_nowait()
                self.log_text.config(state='normal')
                self.log_text.insert(tk.END, log_message + '\n')
                self.log_text.see(tk.END)
                self.log_text.config(state='disabled')
        except queue.Empty:
            pass
        finally:
            # Schedule next check
            self.root.after(100, self._process_log_queue)
    
    def clear_log(self) -> None:
        """Clear the log display."""
        self.log_text.config(state='normal')
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state='disabled')
    
    def _update_status(self, status: str, color: str = "black") -> None:
        """Update the status label in the status bar."""
        self.status_label.config(text=f"Status: {status}", foreground=color)
    
    def _update_button_states(self) -> None:
        """Update the single toggle button to reflect running state."""
        if not self.is_running:
            self.toggle_button.config(
                text="Start",
                style="Accent.TButton",
                state="normal",
            )
        else:
            self.toggle_button.config(
                text="Stop",
                style="Danger.TButton",
                state="normal",
            )

    def toggle_run(self) -> None:
        """Toggle between starting and stopping the aimbot."""
        if self.is_running:
            self.stop()
        else:
            self.start()

    def _update_headshot_warning(self) -> None:
        """Show or hide the headshot probability warning."""
        try:
            value = float(self.headshot_var.get())
        except tk.TclError:
            value = 0.0
        if value > 0.35:
            self.headshot_warning.grid()
        else:
            self.headshot_warning.grid_remove()

    def _on_headshot_change(self) -> None:
        """Update headshot percentage label and warning visibility."""
        try:
            val = max(0.0, min(1.0, float(self.headshot_var.get())))
        except tk.TclError:
            val = 0.0
        self.headshot_percent.config(text=f"{int(val * 100)}%")
        self._update_headshot_warning()

    def _apply_widget_values(self) -> None:
        """Update :attr:`settings` from widget values."""
        try:
            self.settings.headshot_probability = self._read_probability(
                self.headshot_var.get(), "Headshot probability"
            )
            self.settings.headshot_on_acquire = self.acquire_headshot_var.get()
            self.settings.headshot_cache_interval = self._read_float(
                self.headshot_cache_interval_var.get(),
                "Headshot cache",
                minimum=0.0,
            )
            self.settings.headshot_force_duration = self._read_float(
                self.headshot_force_duration_var.get(),
                "Headshot force duration",
                minimum=0.0,
            )
            self.settings.headshot_reacquire_cooldown = self._read_float(
                self.headshot_reacquire_cooldown_var.get(),
                "Headshot reacquire cooldown",
                minimum=0.0,
            )
            self.settings.smoothing_mode = self.smoothing_mode_var.get()
            self.settings.smooth_speed = self._read_float(
                self.smooth_speed_var.get(), "Smooth speed", minimum=0.0
            )
            self.settings.min_smooth_speed = self._read_float(
                self.min_smooth_speed_var.get(),
                "Minimum smooth speed",
                minimum=0.0,
            )
            self.settings.yaw_smooth_scale = self._read_float(
                self.yaw_smooth_scale_var.get(),
                "Yaw speed multiplier",
                minimum=0.0,
            )
            self.settings.pitch_smooth_scale = self._read_float(
                self.pitch_smooth_scale_var.get(),
                "Pitch speed multiplier",
                minimum=0.0,
            )
            self.settings.distance_smoothing_min = self._read_float(
                self.distance_smoothing_min_var.get(),
                "Distance smoothing start",
                minimum=0.0,
            )
            self.settings.distance_smoothing_max = self._read_float(
                self.distance_smoothing_max_var.get(),
                "Distance smoothing end",
                minimum=0.0,
            )
            self.settings.fov_smoothing_min = self._read_float(
                self.fov_smoothing_min_var.get(),
                "FOV smoothing start",
                minimum=0.0,
            )
            self.settings.fov_smoothing_max = self._read_float(
                self.fov_smoothing_max_var.get(),
                "FOV smoothing end",
                minimum=0.0,
            )
            self.settings.lock_ramp_duration = self._read_float(
                self.lock_ramp_duration_var.get(),
                "Lock ramp duration",
                minimum=0.0,
            )
            self.settings.lock_ramp_start_speed = self._read_float(
                self.lock_ramp_start_speed_var.get(),
                "Lock ramp start speed",
                minimum=0.0,
            )
            self.settings.target_select_type = self.target_var.get()
            self.settings.target_switch_cooldown = self._read_float(
                self.target_switch_cooldown_var.get(),
                "Target switch cooldown",
                minimum=0.0,
            )
            self.settings.target_switch_leeway = self._read_float(
                self.target_switch_leeway_var.get(),
                "Target switch leeway",
                minimum=0.0,
            )
            self.settings.aim_tolerance = self._read_float(
                self.aim_tolerance_var.get(), "Aim tolerance", minimum=0.0
            )
            self.settings.max_target_distance = self._read_optional_float(
                self.max_target_distance_var.get(),
                "Max target distance",
                minimum=0.0,
            )
            self.settings.max_target_fov = self._read_optional_float(
                self.max_target_fov_var.get(), "Max target FOV", minimum=0.0
            )
            self.settings.target_distance_weight = self._read_float(
                self.target_distance_weight_var.get(),
                "Distance weight",
                minimum=0.0,
            )
            self.settings.target_fov_weight = self._read_float(
                self.target_fov_weight_var.get(), "FOV weight", minimum=0.0
            )
            self.settings.low_health_bonus = self._read_float(
                self.low_health_bonus_var.get(), "Low health bonus", minimum=0.0
            )
            self.settings.low_health_threshold = int(
                self._read_float(
                    self.low_health_threshold_var.get(),
                    "Low health threshold",
                    minimum=0.0,
                )
            )
            self.settings.activation_mode = self.activation_mode_var.get()
            self.settings.activation_key = self._read_virtual_key(
                self.activation_key_var.get(), "Activation key"
            )
            self.settings.auto_fire_enabled = self.auto_fire_enabled_var.get()
            self.settings.auto_fire_key = self._read_virtual_key(
                self.auto_fire_key_var.get(), "Auto fire key"
            )
            self.settings.auto_fire_button = self.auto_fire_button_var.get()
            self.settings.auto_fire_blocks_aim = self.auto_fire_blocks_var.get()
            self.settings.reaction_delay_enabled = (
                self.reaction_delay_enabled_var.get()
            )
            self.settings.reaction_delay_min = self._read_float(
                self.reaction_delay_min_var.get(),
                "Reaction delay minimum",
                minimum=0.0,
            )
            self.settings.reaction_delay_max = self._read_float(
                self.reaction_delay_max_var.get(),
                "Reaction delay maximum",
                minimum=0.0,
            )
            self.settings.jitter_enabled = self.jitter_enabled_var.get()
            self.settings.jitter_amount = self._read_float(
                self.jitter_amount_var.get(), "Jitter amount", minimum=0.0
            )
            self.settings.jitter_interval = self._read_float(
                self.jitter_interval_var.get(), "Jitter interval", minimum=0.0
            )
            self.settings.jitter_max_fov = self._read_optional_float(
                self.jitter_max_fov_var.get(), "Jitter max FOV", minimum=0.0
            )
            self.settings.grey_talon_lock_enabled = self.grey_enabled.get()
            self.settings.grey_talon_key = self._read_char_key(
                self.grey_key.get(), self.settings.grey_talon_key, "Grey Talon key"
            )
            self.settings.yamato_lock_enabled = self.yamato_enabled.get()
            self.settings.yamato_key = self._read_char_key(
                self.yamato_key.get(), self.settings.yamato_key, "Yamato key"
            )
            self.settings.vindicta_lock_enabled = self.vindicta_enabled.get()
            self.settings.vindicta_key = self._read_char_key(
                self.vindicta_key.get(), self.settings.vindicta_key, "Vindicta key"
            )
            self.settings.paradox_shortcut_enabled = self.paradox_enabled.get()
            self.settings.paradox_r_key = self._read_char_key(
                self.paradox_r_key.get(), self.settings.paradox_r_key, "Paradox R key"
            )
            self.settings.paradox_e_key = self._read_char_key(
                self.paradox_e_key.get(), self.settings.paradox_e_key, "Paradox E key"
            )
            self.settings.ignore_heroes = parse_hero_list(
                self.ignore_heroes_var.get()
            )
            self.settings.preferred_heroes = parse_hero_list(
                self.preferred_heroes_var.get()
            )
            self.settings.preferred_hero_weight = self._read_float(
                self.preferred_hero_weight_var.get(),
                "Preferred hero weight",
                minimum=0.0,
            )
            self.settings.__post_init__()
            self._refresh_variables_from_settings()
            self._on_headshot_change()
            self._update_headshot_warning()
        except ValueError as exc:
            raise ValueError(str(exc))

    def _read_probability(self, raw: float | str, name: str) -> float:
        """Parse probability values between 0 and 1."""

        value = self._read_float(raw, name, minimum=0.0, maximum=1.0)
        return value

    @staticmethod
    def _read_float(
        raw: float | str,
        name: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float:
        """Parse ``raw`` as a float with optional bounds."""

        try:
            value = float(raw)
        except (TypeError, ValueError, tk.TclError) as exc:
            raise ValueError(f"{name} must be a number.") from exc
        if minimum is not None and value < minimum:
            raise ValueError(f"{name} must be at least {minimum}.")
        if maximum is not None and value > maximum:
            raise ValueError(f"{name} must be at most {maximum}.")
        return value

    @staticmethod
    def _read_optional_float(
        raw: str,
        name: str,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> float | None:
        """Parse an optional float returning ``None`` for blank strings."""

        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        value = AimbotApp._read_float(text, name, minimum=minimum, maximum=maximum)
        return value

    @staticmethod
    def _read_virtual_key(raw: str, name: str) -> int | None:
        """Parse a user supplied virtual-key specification."""

        try:
            return parse_virtual_key(raw)
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}") from exc

    @staticmethod
    def _read_char_key(raw: str, fallback: int, name: str) -> int:
        """Parse a single character fallback key code."""

        text = (raw or "").strip()
        if not text:
            return fallback
        if len(text) != 1:
            raise ValueError(f"{name} must be a single character.")
        return ord(text.upper())

    def _on_save_settings(self) -> None:
        """Persist the current settings without launching the aimbot."""

        try:
            self._apply_widget_values()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc))
            return
        save_settings(self.settings)
        messagebox.showinfo("Settings saved", "Your preferences have been stored.")

    def _reset_to_defaults(self) -> None:
        """Restore default :class:`AimbotSettings` values."""

        if not messagebox.askyesno(
            "Reset settings",
            "Restore all settings to their default values?",
        ):
            return
        self._apply_settings(AimbotSettings())
        save_settings(self.settings)
        self._set_preset_message("Restored default settings.")

    def start(self) -> None:
        """Start the aimbot."""
        if self.is_running:
            return

        try:
            self._apply_widget_values()
        except ValueError as exc:
            messagebox.showerror("Invalid setting", str(exc))
            return
        save_settings(self.settings)

        try:
            # For binary releases, update check is handled in _notify_if_outdated
            # For source installations, we can still do a quick check here
            if not getattr(sys, 'frozen', False):
                # Show a simple progress dialog for source updates too
                if update_available():
                    result = messagebox.askyesno(
                        "Update Available",
                        "A newer version is available. Open the download page?",
                    )
                    if result:
                        open_release_page()
                        return

            self.is_running = True
            self.is_paused = False
            self._update_status("Starting...", "#60a5fa")
            self._update_button_states()

            # Start initialisation and aimbot in a separate thread
            self.bot_thread = threading.Thread(
                target=self._initialise_and_run, daemon=True
            )
            self.bot_thread.start()

        except Exception as e:
            self._update_status("Error", "red")
            self.is_running = False
            self._update_button_states()
            messagebox.showerror("Error", f"Failed to start aimbot: {str(e)}")

    def _initialise_and_run(self) -> None:
        """Initialise memory and run the aimbot."""
        try:
            mem = DeadlockMemory()
            self.bot = Aimbot(mem, self.settings)
            self.log_queue.put("Aimbot started successfully.")
            self.root.after(0, lambda: (
                self._update_status("Running", "#10b981"),
                self._update_button_states()
            ))
            self._run_aimbot()
        except Exception as exc:
            self.is_running = False
            msg = str(exc)
            self.log_queue.put(f"Aimbot init error: {msg}")
            self.root.after(0, lambda: (
                self._update_status("Error", "red"),
                self._update_button_states(),
                messagebox.showerror("Error", f"Failed to start aimbot: {msg}")
            ))
    
    def _run_aimbot(self) -> None:
        """Run the aimbot loop with pause support."""
        if self.bot:
            try:
                self.bot.run()
            except Exception as e:
                self.log_queue.put(f"Aimbot error: {str(e)}")
                self.is_running = False
                self.root.after(0, lambda: (
                    self._update_status("Error", "red"),
                    self._update_button_states()
                ))
    
    def toggle_pause(self) -> None:
        """Toggle pause state of the aimbot."""
        if not self.is_running or not self.bot:
            return
            
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.bot.pause()
            self._update_status("Paused", "#f59e0b")
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, "Aimbot paused.\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        else:
            self.bot.resume()
            self._update_status("Running", "#10b981")
            self.log_text.config(state='normal')
            self.log_text.insert(tk.END, "Aimbot resumed.\n")
            self.log_text.see(tk.END)
            self.log_text.config(state='disabled')
        
        self._update_button_states()
    
    def stop(self) -> None:
        """Stop the aimbot."""
        if not self.is_running:
            return
            
        if self.bot:
            self.bot.stop()
            
        self.is_running = False
        self.is_paused = False
        self.bot = None
        
        self._update_status("Stopped", "red")
        self._update_button_states()
        
        self.log_text.config(state='normal')
        self.log_text.insert(tk.END, "Aimbot stopped.\n")
        self.log_text.see(tk.END)
        self.log_text.config(state='disabled')

    def on_close(self) -> None:
        """Handle window close event."""
        self._apply_widget_values()
        save_settings(self.settings)
        
        if self.is_running:
            self.stop()
        
        # Clean up logging handler
        if hasattr(self, 'log_handler'):
            aimbot_logger = logging.getLogger('deadlock.aimbot')
            aimbot_logger.removeHandler(self.log_handler)
            
            # Also clean up offset finder logger
            offset_finder_logger = logging.getLogger('offset_finder')
            offset_finder_logger.removeHandler(self.log_handler)
            
        self.root.destroy()


def main() -> None:
    """Main entry point for the GUI application."""
    # Ensure only one instance can run
    try:
        root = tk.Tk()
        root.resizable(True, True)
        app = AimbotApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Error starting GUI: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
