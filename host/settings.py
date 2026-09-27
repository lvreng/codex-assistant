"""Project-local appearance settings; reload safely without reopening USB."""
from pathlib import Path
from datetime import datetime
import json
import os

DEFAULTS = {"theme":"dark", "transition_ms":650, "api_mode":"auto",
            "rotation_mode": "active",
            "membership_expires_at": ""}


def validate_expiry(value):
    if not isinstance(value, str):
        raise ValueError("Expiry must be a local date/time or an empty string")
    if value:
        if len(value) != 16:
            raise ValueError("Expiry must use YYYY-MM-DDTHH:MM")
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        if not 2000 <= parsed.year <= 2199 or parsed.strftime("%Y-%m-%dT%H:%M") != value:
            raise ValueError("Invalid membership expiry")
    return value


def validate(data):
    if not isinstance(data,dict) or set(data)-set(DEFAULTS):
        raise ValueError("Unknown settings fields")
    value={**DEFAULTS,**data}
    validate_expiry(value["membership_expires_at"])
    if value["api_mode"] not in ("auto", "official", "morecode"):
        raise ValueError("Invalid API source")
    if value["rotation_mode"] not in ("off", "active", "all"):
        raise ValueError("Invalid conversation rotation mode")
    if value["theme"] not in ("light", "dark", "beach", "pixel", "mechanical", "paper", "glass", "vangogh"):
        raise ValueError("theme must be light, dark, beach, pixel, mechanical, paper, glass, or vangogh")
    if type(value["transition_ms"]) is not int or not 200<=value["transition_ms"]<=2000:
        raise ValueError("transition_ms must be an integer from 200 to 2000")
    return value


class Settings:
    def __init__(self,path:Path,**overrides):
        self.path=path
        self.overrides={k:v for k,v in overrides.items() if v is not None}
        validate(self.overrides)
        self.value={**DEFAULTS,**self.overrides}
        self.stamp=None
        self.error=None
        self.changed=False

    def set_api_mode(self, mode):
        return self._save(api_mode=mode)

    def set_membership_expires_at(self, value):
        return self._save(membership_expires_at=validate_expiry(value))

    def set_rotation_mode(self, mode):
        return self._save(rotation_mode=mode)

    def _save(self, **changes):
        value = validate({**self.poll(), **changes})
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary, self.path)
        self.stamp = None
        return self.poll()

    def poll(self):
        self.changed=False
        try:
            st=self.path.stat()
            stamp=(st.st_ino,st.st_mtime_ns,st.st_size)
            if stamp==self.stamp:return self.value.copy()
            self.stamp=stamp
            if st.st_size>4096:raise ValueError("Settings file exceeds 4096 bytes")
            value=validate({**validate(json.loads(self.path.read_text())),**self.overrides})
            self.changed=value!=self.value
            self.value=value
            self.error=None
        except (OSError,ValueError,TypeError) as exc:
            # Keep the last valid settings during an editor's partial save.
            self.error=f"{type(exc).__name__}: {exc}"
        return self.value.copy()
