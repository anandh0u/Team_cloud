"""The fixed set of named arm poses the Pi is allowed to request (config/poses.json)."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError, model_validator

from app.catalog import load_json_catalog
from app.config import ConfigError


class PoseCatalog(BaseModel):
    poses: list[str]
    home_pose: str
    safe_pose: str
    return_pose: str
    object_to_pose: dict[str, str]

    @model_validator(mode="after")
    def _consistent(self) -> PoseCatalog:
        self.poses = [p.upper() for p in self.poses]
        self.home_pose, self.safe_pose, self.return_pose = (
            self.home_pose.upper(), self.safe_pose.upper(), self.return_pose.upper())
        self.object_to_pose = {k.lower(): v.upper() for k, v in self.object_to_pose.items()}
        referenced = {self.home_pose, self.safe_pose, self.return_pose, *self.object_to_pose.values()}
        unknown = referenced - set(self.poses)
        if unknown:
            raise ValueError(f"poses referenced but not listed in 'poses': {sorted(unknown)}")
        return self

    @classmethod
    def load(cls, path: Path) -> PoseCatalog:
        data = {k: v for k, v in load_json_catalog(path).items() if not k.startswith("_")}
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(f"Pose catalog {path} is invalid: {exc}") from exc

    def is_allowed(self, pose: str) -> bool:
        return pose.upper() in self.poses

    def pose_for_object(self, obj: str) -> str | None:
        return self.object_to_pose.get(obj.strip().lower())
