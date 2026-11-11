from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def endpoint(value: str) -> str:
    u = urlparse(value)
    if not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("API URL must have a hostname and no credentials, query, or fragment.")
    if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1")):
        raise ValueError("Use HTTPS for API endpoints (HTTP is allowed only on localhost).")
    return value.rstrip("/")


@dataclass
class Settings:
    doubao_key: str = field(default="", repr=False)
    doubao_base: str = "https://ark.cn-beijing.volces.com/api/v3"
    doubao_model: str = "doubao-seed-2-1-pro-260628"
    jev_key: str = field(default="", repr=False)
    jev_url: str = "https://api.typesafe.ai/v1/systemone"
    jev_model: str = "jev-latest"
    moonshine_root: Path = ROOT / "integrations/moonshine-local"
    data_root: Path = ROOT / "data"
    soffice: str = ""
    vision_batch_size: int = 1
    vision_timeout: float = 120
    jev_timeout: float = 8
    bridge_port: int = 0

    @classmethod
    def load(cls, env_path: Path | None = None):
        # Read only this application's explicit config; never search other projects.
        env = {**dotenv_values(env_path or ROOT / ".env"), **os.environ}
        # Optional explicit reuse of existing credentials; no source file is edited.
        for reference, keys in {
            "DOUBAO_ENV_FILE": ("DOUBAO_API_KEY", "DOUBAO_BASE_URL", "DOUBAO_MODEL"),
            "JEV_ENV_FILE": ("JEV_API_KEY", "JEV_BASE_URL", "JEV_MODEL"),
        }.items():
            if env.get(reference):
                values = dotenv_values(Path(env[reference]).expanduser())
                for key in keys:
                    if not env.get(key) and values.get(key):
                        env[key] = values[key]
        default = cls()
        return cls(
            doubao_key=env.get("DOUBAO_API_KEY") or env.get("ARK_API_KEY", ""),
            doubao_base=endpoint(env.get("DOUBAO_BASE_URL", default.doubao_base)),
            doubao_model=env.get("DOUBAO_MODEL", default.doubao_model),
            jev_key=env.get("JEV_API_KEY") or env.get("TYPESAFE_API_KEY", ""),
            jev_url=endpoint(env.get("JEV_BASE_URL", default.jev_url)),
            jev_model=env.get("JEV_MODEL", default.jev_model),
            moonshine_root=Path(env.get("MOONSHINE_ROOT", str(default.moonshine_root))).expanduser(),
            data_root=Path(env.get("DATA_ROOT", str(default.data_root))).expanduser(),
            soffice=env.get("SOFFICE_PATH", ""),
            vision_batch_size=max(1, min(12, int(env.get("VISION_BATCH_SIZE", str(default.vision_batch_size))))),
            vision_timeout=float(env.get("VISION_TIMEOUT_SECONDS", "120")),
            jev_timeout=float(env.get("JEV_TIMEOUT_SECONDS", "8")),
            bridge_port=int(env.get("BRIDGE_PORT", "0")),
        )


@dataclass(frozen=True)
class GateSettings:
    cooldown_seconds: float = 5
    transcript_window: float = 30
    max_inference_age: float = 10
    inference_interval: float = 0.8

    def __post_init__(self):
        import math
        for name, value in vars(self).items():
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid gate setting: {name}")
