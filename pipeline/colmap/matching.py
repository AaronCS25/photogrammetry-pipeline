"""Temporal pairs come from the manifest, never from insertion/image ID order."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from ..config import ConfigError
from ..executor import extra_args_to_cli, run_cmd
from .runtime import general_flags


def temporal_pairs(images: list[dict], overlap: int) -> list[tuple[str, str]]:
    clips = defaultdict(list)
    for image in images:
        if image.get("kind") == "video":
            clips[image["asset"]].append(image)
    pairs = set()
    for clip in clips.values():
        clip.sort(key=lambda i: i["index"])
        for i, a in enumerate(clip):
            for b in clip[i + 1:i + 1 + overlap]:
                pairs.add(tuple(sorted((a["relative"], b["relative"]))))
    return sorted(pairs)


def run_matching(ctx, images: list[dict], cfg: dict, new_names: set[str] | None):
    log = ctx.logs_dir / "matching.log"
    flags = general_flags("Matching", cfg["threads"], cfg.get("gpu", False))
    pairs = temporal_pairs(images, cfg.get("overlap", 15))
    if new_names is not None:
        pairs = [p for p in pairs if any(n in new_names for n in p)]
    path = ctx.colmap_dir / "temporal_pairs.txt"
    path.write_text("".join(f"{a} {b}\n" for a, b in pairs))
    if pairs:
        run_cmd(["colmap", "matches_importer", "--database_path", ctx.colmap_dir / "database.db",
                 "--match_list_path", path, "--match_type", "pairs", *flags,
                 *extra_args_to_cli(cfg.get("extra_args"))], log)
    method = cfg.get("global", "exhaustive")
    if method not in ("exhaustive", "vocab_tree", "none"):
        raise ConfigError(f"Matching global no soportado: {method}")
    if method == "exhaustive":
        # Explicit list bounds work for incremental updates to new-old/new-new.
        names = sorted(i["relative"] for i in images)
        path = ctx.colmap_dir / "global_pairs.txt"
        count = 0
        with path.open("w") as f:
            for j, a in enumerate(names):
                for b in names[j + 1:]:
                    if new_names is None or a in new_names or b in new_names:
                        count += 1
                        if count > cfg.get("max_pairs", 250000):
                            raise ConfigError("Demasiados pares: configurar vocab_tree o aumentar max_pairs explícitamente")
                        f.write(f"{a} {b}\n")
        if count:
            run_cmd(["colmap", "matches_importer", "--database_path", ctx.colmap_dir / "database.db",
                     "--match_list_path", path, "--match_type", "pairs", *flags,
                     *extra_args_to_cli(cfg.get("extra_args"))], log)
    elif method == "vocab_tree":
        vocab = Path(cfg.get("vocab_tree_path", ""))
        if not vocab.is_file():
            raise ConfigError("Falta vocab_tree_path")
        # Global retrieval may revisit old images; feature extraction is still cached.
        run_cmd(["colmap", "vocab_tree_matcher", "--database_path", ctx.colmap_dir / "database.db",
                 "--VocabTreeMatching.vocab_tree_path", vocab, *flags,
                 *extra_args_to_cli(cfg.get("extra_args"))], log)
