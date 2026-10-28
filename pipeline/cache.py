"""Feature cache identity includes extraction, camera model, masks and toolchain."""
from __future__ import annotations

from .state import digest


def feature_signature(profile: dict) -> str:
    # Thread budgets change resource usage, not the feature recipe.
    features = {k: v for k, v in profile["features"].items() if k != "threads"}
    return digest({"frames": profile["frames"], "features": features,
                   "colmap": profile["colmap_version"], "masking": profile.get("masking", {}),
                   "mask_inputs": profile.get("mask_inputs", [])})


def asset_signature(item: dict, profile: dict) -> str:
    return digest({"asset": item["id"], "stamp": item["stamp"],
                   "camera_group": item["camera_group"], "features": feature_signature(profile)})


def legacy_allowed(profile: dict) -> bool:
    """The one explicitly declared historical recipe; never infer from filename."""
    f, s = profile["frames"], profile["features"]
    return (profile.get("legacy_compatible", False) and profile["colmap_version"] == "3.13.0"
            and f.get("fps") == 2 and not f.get("resize") and f.get("format", "jpg") == "jpg"
            and not f.get("start") and not f.get("end") and not f.get("extra_args")
            and f.get("jpg_quality", 2) == 2 and s["camera_model"] == "SIMPLE_RADIAL"
            and s["max_image_size"] == 4000 and s["max_num_features"] == 8192
            and not s.get("gpu", False) and not s.get("extra_args")
            and not profile.get("masking", {}).get("enabled"))
