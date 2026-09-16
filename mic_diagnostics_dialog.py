"""Live Mic Inputs dialog (Prompt 7): device/channel mapping, meters, calibration.

Diagnostic only. Opening it never touches the laptop microphone: meters start
only when the host picks a device and presses Start Meters. Nothing is recorded.
All helper launches and device listing happen off the GUI thread.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QHBoxLayout,
                             QLabel, QProgressBar, QPushButton, QSpinBox, QVBoxLayout)

import mic_activity
import mic_config
import mic_monitor

ROLE_LABELS = {"singer1": "Singer Mic 1", "singer2": "Singer Mic 2", "singers": "Singers (combined)", "host": "Host Mic"}
PRESET_ROLES = {"ui24r": ("singer1", "singer2", "host"), "signature10": ("singers", "host"),
                "signature22mtk": ("singer1", "singer2", "host"), "custom": ("singer1", "singer2", "host")}
STATE_TEXT = {"silent": "quiet", "active": "ACTIVE", "sustained": "SUSTAINED", "clipping": "CLIPPING",
              "suspect_noise": "noise/feedback?", "stale": "no signal"}


class _DeviceLister(QObject):
    done = pyqtSignal(list)


class MicDiagnosticsDialog(QDialog):
    def __init__(self, parent, *, settings: dict, helper_path: Path, on_save: Callable[[dict], None]):
        super().__init__(parent)
        self.setWindowTitle("Live Mic Inputs (diagnostic)")
        self._settings = settings
        self._helper = Path(helper_path)
        self._on_save = on_save
        self._config = mic_config.MicInputConfig.from_settings(settings.get("ia_mic_config"))
        self._monitor: mic_monitor.MicMonitor | None = None
        self._devices: list[dict] = []
        self._calibrating: dict | None = None

        root = QVBoxLayout(self)
        warn = QLabel(
            "<b>Feedback safety:</b> turn speakers/amps down before testing. Never route the mixer's "
            "USB return into the Aux buses or channels that feed these inputs. SingWS Pro only reads "
            "levels here; it sends no audio back and records nothing.")
        warn.setWordWrap(True)
        root.addWidget(warn)

        form = QFormLayout()
        self.enabled_cb = QCheckBox("Use live mic awareness (observer only in this version)")
        self.enabled_cb.setChecked(bool(settings.get("ia_mic_awareness_enabled", False)))
        form.addRow(self.enabled_cb)
        self.mode = QComboBox()
        for key, label in (("singer_protection_host_ducking", "Singer protection + host ducking"),
                           ("singer_protection", "Singer protection"),
                           ("observe", "Observe only"), ("off", "Off")):
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(
            settings.get("ia_mic_observer_mode", "singer_protection_host_ducking"))))
        self.mode.setToolTip("All modes only log suggestions in this version; playback is never changed.")
        form.addRow("Transition suggestions", self.mode)
        self.preset = QComboBox()
        for key, data in mic_config.PRESETS.items():
            self.preset.addItem(data["label"], key)
        self.preset.setCurrentIndex(max(0, self.preset.findData(self._config.preset)))
        form.addRow("Mixer", self.preset)
        dev_row = QHBoxLayout()
        self.device = QComboBox()
        if self._config.device_uid:
            self.device.addItem(self._config.device_name or self._config.device_uid, self._config.device_uid)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_devices)
        dev_row.addWidget(self.device, 1)
        dev_row.addWidget(refresh)
        form.addRow("USB audio device", dev_row)
        root.addLayout(form)

        grid = QGridLayout()
        self.spins, self.bars, self.states = {}, {}, {}
        for row, role in enumerate(("singer1", "singer2", "singers", "host")):
            label = QLabel(ROLE_LABELS[role])
            spin = QSpinBox()
            spin.setRange(0, 64)
            spin.setSpecialValueText("unused")
            spin.setValue(int(self._config.roles.get(role, 0)))
            bar = QProgressBar()
            bar.setRange(0, 60)
            bar.setTextVisible(False)
            state = QLabel("—")
            for col, w in enumerate((label, spin, bar, state)):
                grid.addWidget(w, row, col)
            self.spins[role], self.bars[role], self.states[role] = (spin, label), bar, state
        root.addLayout(grid)

        self.status = QLabel("Meters stopped.")
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start Meters")
        self.start_btn.clicked.connect(self._toggle_meters)
        self.cal_btn = QPushButton("Calibrate Quiet Level (3 s)")
        self.cal_btn.clicked.connect(self._start_calibration)
        save = QPushButton("Save")
        save.clicked.connect(self._save)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for b in (self.start_btn, self.cal_btn, save, close):
            buttons.addWidget(b)
        root.addLayout(buttons)

        self.preset.currentIndexChanged.connect(self._apply_preset)
        self._apply_preset(keep_values=True)
        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._lister = _DeviceLister()
        self._lister.done.connect(self._devices_listed)
        self._refresh_devices()

    # ---- config ------------------------------------------------------------
    def _apply_preset(self, *_args, keep_values: bool = False):
        key = self.preset.currentData() or "custom"
        visible = PRESET_ROLES.get(key, PRESET_ROLES["custom"])
        if not keep_values and key != "custom":
            defaults = mic_config.PRESETS[key]["roles"]
            for role, (spin, _l) in self.spins.items():
                spin.setValue(int(defaults.get(role, 0)))
        for role, (spin, label) in self.spins.items():
            show = role in visible
            for w in (spin, label, self.bars[role], self.states[role]):
                w.setVisible(show)

    def _current_config(self) -> mic_config.MicInputConfig:
        key = self.preset.currentData() or "custom"
        visible = PRESET_ROLES.get(key, PRESET_ROLES["custom"])
        cfg = mic_config.MicInputConfig(preset=key, device_uid=str(self.device.currentData() or ""),
                                        device_name=self.device.currentText(),
                                        noise_floor_db=dict(self._config.noise_floor_db))
        for role, (spin, _l) in self.spins.items():
            if role in visible and spin.value() > 0:
                cfg.roles[role] = spin.value()
        return cfg

    def _refresh_devices(self):
        self.status.setText("Looking for USB audio inputs…")
        helper = self._helper
        threading.Thread(target=lambda: self._lister.done.emit(mic_monitor.list_input_devices(helper)),
                         daemon=True, name="mic-device-list").start()

    def _devices_listed(self, devices: list):
        self._devices = devices
        current = self.device.currentData()
        self.device.clear()
        for d in devices:
            self.device.addItem(f"{d.get('name')} — {d.get('input_channels')} inputs", d.get("uid"))
        if current:
            idx = self.device.findData(current)
            if idx < 0:
                self.device.addItem(f"{self._config.device_name or current} (not connected)", current)
                idx = self.device.count() - 1
            self.device.setCurrentIndex(idx)
        self.status.setText(f"{len(devices)} input device(s) found." if devices else
                            "No USB audio inputs found (or the mic meter helper is not built).")

    # ---- meters --------------------------------------------------------------
    def _toggle_meters(self):
        if self._monitor is not None:
            self._stop_meters()
            return
        cfg = self._current_config()
        channels = next((d.get("input_channels") for d in self._devices if d.get("uid") == cfg.device_uid), None)
        problems = cfg.problems(input_channels=channels)
        if problems:
            self.status.setText("Fix first: " + ", ".join(problems))
            return
        self._monitor = mic_monitor.MicMonitor(self._helper, cfg)
        self._monitor.arm()
        self._timer.start()
        self.start_btn.setText("Stop Meters")

    def _stop_meters(self):
        self._timer.stop()
        if self._monitor is not None:
            self._monitor.shutdown()
        self._monitor = None
        self.start_btn.setText("Start Meters")
        self.status.setText("Meters stopped.")
        for role in self.bars:
            self.bars[role].setValue(0)
            self.states[role].setText("—")

    def _tick(self):
        mon = self._monitor
        if mon is None:
            return
        snap = mon.latest()
        if snap is None:
            reason = mon.reason or mon.state
            if "waiting" in reason:
                self.status.setText("Mixer disconnected or changed format — waiting for it. Playback is unaffected.")
            else:
                self.status.setText(f"Mic meters: {mon.state} ({reason})")
            for role in self.bars:
                self.bars[role].setValue(0)
                self.states[role].setText("no signal")
            return
        self.status.setText(f"Metering {snap.get('device')}.")
        for role, data in snap["roles"].items():
            self.bars[role].setValue(int(max(0.0, min(60.0, data["rms_db"] + 60.0))))
            self.states[role].setText(STATE_TEXT.get(data["state"], data["state"]))
        if self._calibrating is not None:
            for role, data in snap["roles"].items():
                self._calibrating.setdefault(role, []).append(data["rms_db"])

    def _start_calibration(self):
        if self._monitor is None:
            self.status.setText("Start meters first, keep all mics quiet, then calibrate.")
            return
        self._calibrating = {}
        self.cal_btn.setEnabled(False)
        self.status.setText("Calibrating — keep every mic quiet for 3 seconds…")
        QTimer.singleShot(3000, self._finish_calibration)

    def _finish_calibration(self):
        samples, self._calibrating = self._calibrating or {}, None
        for role, values in samples.items():
            self._config.noise_floor_db[role] = mic_activity.calibrate_floor(values)
        self.cal_btn.setEnabled(True)
        self.status.setText("Quiet levels: " + ", ".join(
            f"{ROLE_LABELS.get(r, r)} {v:.0f} dB" for r, v in self._config.noise_floor_db.items()) + ". Press Save.")

    # ---- save/close ------------------------------------------------------------
    def _save(self):
        cfg = self._current_config()
        self._on_save({"ia_mic_awareness_enabled": bool(self.enabled_cb.isChecked()),
                       "ia_mic_config": cfg.to_settings(),
                       "ia_mic_observer_mode": str(self.mode.currentData() or "off")})
        self.status.setText("Saved.")

    def closeEvent(self, event):
        self._stop_meters()
        super().closeEvent(event)
