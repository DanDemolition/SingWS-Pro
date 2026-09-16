"""Live-mic input configuration (Prompt 7): presets, validation, persistence.

Pure data. Channel numbers are 1-based Core Audio INPUT channels of one device,
as reported by ``SingWSMicMeter --list-devices``. They must be confirmed against
the mixer's own USB routing page; nothing here is hard-wired to a mixer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

ROLES = ("singer1", "singer2", "singers", "host")

PRESETS: Mapping[str, dict] = {
    # Ui24R: 32x32 USB multitrack; SingWS default assumes USB input N carries
    # mixer channel N. Edit to match the mixer's USB routing.
    "ui24r": {"label": "Soundcraft Ui24R (separate mic channels)",
              "roles": {"singer1": 1, "singer2": 2, "host": 3}},
    # Signature 10: two USB channels, switched to Aux 1 / Aux 2 on the mixer.
    "signature10": {"label": "Soundcraft Signature 10 (Aux 1 singers / Aux 2 host)",
                    "roles": {"singers": 1, "host": 2}},
    # Signature 22 MTK: multitrack; example strips 6/7 singers, 5 host.
    "signature22mtk": {"label": "Soundcraft Signature 22 MTK (multitrack)",
                       "roles": {"singer1": 6, "singer2": 7, "host": 5}},
    "custom": {"label": "Custom", "roles": {}},
}


@dataclass
class MicInputConfig:
    preset: str = "ui24r"
    device_uid: str = ""
    device_name: str = ""
    roles: dict = field(default_factory=dict)       # role -> channel number
    noise_floor_db: dict = field(default_factory=dict)   # role -> calibrated floor

    @classmethod
    def from_preset(cls, preset: str) -> "MicInputConfig":
        data = PRESETS.get(preset, PRESETS["custom"])
        return cls(preset=preset if preset in PRESETS else "custom", roles=dict(data["roles"]))

    @classmethod
    def from_settings(cls, payload) -> "MicInputConfig":
        if not isinstance(payload, dict):
            return cls.from_preset("ui24r")
        cfg = cls(preset=str(payload.get("preset", "custom")),
                  device_uid=str(payload.get("device_uid", "") or ""),
                  device_name=str(payload.get("device_name", "") or ""))
        roles = payload.get("roles", {})
        if isinstance(roles, dict):
            for role, ch in roles.items():
                try:
                    if role in ROLES and int(ch) > 0:
                        cfg.roles[role] = int(ch)
                except (TypeError, ValueError):
                    continue
        floors = payload.get("noise_floor_db", {})
        if isinstance(floors, dict):
            for role, value in floors.items():
                try:
                    if role in ROLES and -120.0 <= float(value) <= 0.0:
                        cfg.noise_floor_db[role] = float(value)
                except (TypeError, ValueError):
                    continue
        return cfg

    def to_settings(self) -> dict:
        return {"preset": self.preset, "device_uid": self.device_uid, "device_name": self.device_name,
                "roles": dict(self.roles), "noise_floor_db": dict(self.noise_floor_db)}

    def channels(self) -> list[int]:
        return sorted(set(self.roles.values()))

    def problems(self, *, input_channels: int | None = None) -> list[str]:
        issues = []
        if not self.device_uid:
            issues.append("no_device_selected")
        if not self.roles:
            issues.append("no_channels_mapped")
        if "singers" in self.roles and ({"singer1", "singer2"} & set(self.roles)):
            issues.append("combined_and_separate_singers")
        seen = {}
        for role, ch in self.roles.items():
            if ch in seen:
                issues.append(f"channel_{ch}_used_by_{seen[ch]}_and_{role}")
            seen[ch] = role
            if input_channels is not None and ch > int(input_channels):
                issues.append(f"{role}_channel_{ch}_missing_on_device")
        return issues

    @property
    def separate_singers(self) -> bool:
        return "singer1" in self.roles or "singer2" in self.roles
