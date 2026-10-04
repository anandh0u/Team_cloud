"""Answers "where is my X?" from the most recent phone photo.

Positions are described from the camera's point of view (left/middle/right third
of the picture). Only YOLO detections are used: if the object isn't in a recent
photo we say so, and never guess.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from pathlib import Path

from app.assistant.responses import Responses
from app.catalog import load_json_catalog, require_keys
from app.config import ConfigError
from app.models import Detection, DetectionResult, ObjectLocation

# A live camera on a handheld or bumped phone catches a small object (a spoon) in some
# frames and misses it in others. An object counts as seen if any frame in this window
# showed it; its position comes from that frame.
SIGHTING_WINDOW_S = 10.0

RESPONSE_KEYS = (
    "find_seen", "find_seen_near", "find_not_seen", "find_not_trained", "find_no_photo",
    "position_left", "position_middle", "position_right",
)


def load_object_labels(path: Path, known_objects: Iterable[str]) -> dict[str, list[str]]:
    data = load_json_catalog(path)
    require_keys(data, ["object_labels"], f"Vision catalog {path}")
    table = data["object_labels"]
    if not isinstance(table, dict) or not all(
            isinstance(labels, list) and all(isinstance(x, str) for x in labels) for labels in table.values()):
        raise ConfigError(f"Vision catalog {path}: object_labels must map object names to lists of labels")
    unknown = sorted(set(table) - set(known_objects))
    if unknown:
        raise ConfigError(f"Vision catalog {path} lists objects the intent catalog doesn't know: {', '.join(unknown)}")
    return table


def _centre(d: Detection) -> tuple[float, float]:
    x1, y1, x2, y2 = d.box
    return (x1 + x2) / 2, (y1 + y2) / 2


class SceneLocator:
    def __init__(self, object_labels: dict[str, list[str]], responses: Responses, max_age_s: float,
                 clock: Callable[[], float] = time.monotonic):
        responses.require(RESPONSE_KEYS)
        self._labels = object_labels
        self._r = responses
        self._max_age_s = max_age_s
        self._clock = clock
        self._scene: DetectionResult | None = None
        self._scene_at = 0.0
        # label -> (time, frame it was in, its best detection in that frame)
        self._sightings: dict[str, tuple[float, DetectionResult, Detection]] = {}

    def update(self, scene: DetectionResult) -> None:
        now = self._clock()
        self._scene = scene
        self._scene_at = now
        for d in sorted(scene.detections, key=lambda d: d.confidence):  # best one per label wins
            self._sightings[d.label] = (now, scene, d)

    def _recent_sighting(self, labels: list[str]) -> tuple[float, DetectionResult, Detection] | None:
        now = self._clock()
        recent = [self._sightings[l] for l in labels
                  if l in self._sightings and now - self._sightings[l][0] <= SIGHTING_WINDOW_S]
        return max(recent, key=lambda s: (s[0], s[2].confidence), default=None)  # newest first

    async def describe_location(self, obj: str) -> ObjectLocation:
        labels = self._labels.get(obj) or []
        if not labels:
            return ObjectLocation(seen=False, reason="not_trained", response=self._r.get("find_not_trained", object=obj))

        age = self._clock() - self._scene_at
        if self._scene is None or age > self._max_age_s:
            return ObjectLocation(seen=False, reason="no_photo", response=self._r.get("find_no_photo", object=obj))

        sighting = self._recent_sighting(labels)
        if sighting is None:
            return ObjectLocation(seen=False, reason="not_seen", response=self._r.get("find_not_seen", object=obj),
                                  photo_age_s=round(age, 1))

        seen_at, scene, best = sighting
        age = self._clock() - seen_at
        cx, cy = _centre(best)
        third = cx / scene.image_width
        position = "left" if third < 1 / 3 else "right" if third > 2 / 3 else "middle"
        position_phrase = self._r.get(f"position_{position}")

        # The nearest other kind of object makes the answer easier to act on.
        others = [d for d in scene.detections if d.label not in labels]
        neighbour = None
        if others:
            nearest = min(others, key=lambda d: (_centre(d)[0] - cx) ** 2 + (_centre(d)[1] - cy) ** 2)
            neighbour = nearest.label
        response = (self._r.get("find_seen_near", object=obj, position=position_phrase, neighbour=neighbour)
                    if neighbour else self._r.get("find_seen", object=obj, position=position_phrase))
        return ObjectLocation(seen=True, response=response, label=best.label, confidence=best.confidence,
                              position=position, neighbour=neighbour, photo_age_s=round(age, 1))
