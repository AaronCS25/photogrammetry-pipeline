"""Etapas OpenMVS: InterfaceCOLMAP -> Densify -> ReconstructMesh -> RefineMesh -> TextureMesh.

Los archivos .mvs se encadenan (cada herramienta escribe la escena con su
resultado embebido), por lo que las etapas posteriores usan la salida .mvs de la
anterior. Todos los artefactos quedan en outputs/<escena>/<exp>/mvs/.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Context
from .executor import CommandError, extra_args_to_cli, run_cmd
from .sfm import undistorted_dir

SCENE = "scene.mvs"
SCENE_DENSE = "scene_dense.mvs"
SCENE_MESH = "scene_mesh.mvs"
SCENE_MESH_REFINED = "scene_mesh_refined.mvs"
SCENE_TEXTURE = "scene_texture.mvs"
# Las herramientas de malla escriben el resultado como PLY derivado del -o;
# la escena de entrada sigue siendo scene_dense.mvs + --mesh-file <ply>.
MESH_PLY = "scene_mesh.ply"
MESH_REFINED_PLY = "scene_mesh_refined.ply"


def _cuda_args(ctx: Context) -> list[str]:
    # OpenMVS usa CUDA automáticamente; --cuda-device -2 la desactiva.
    return [] if ctx.gpu else ["--cuda-device", "-2"]


def _run(ctx: Context, cmd: list, log_name: str) -> None:
    run_cmd(cmd, ctx.logs_dir / log_name, cwd=ctx.mvs_dir)


def _require(ctx: Context, filename: str, produced_by: str) -> Path:
    path = ctx.mvs_dir / filename
    if not path.is_file():
        raise CommandError(f"Falta {path}; ¿se ejecutó la etapa '{produced_by}'?")
    return path


def _link_undistorted_images(ctx: Context, workspace: Path) -> None:
    """scene.mvs referencia las imágenes como rutas relativas ('images/...')
    resueltas contra la carpeta de trabajo de OpenMVS; se enlazan las imágenes
    undistorted dentro de mvs/ para que esas rutas existan."""
    link = ctx.mvs_dir / "images"
    if link.is_symlink():
        link.unlink()
    if not link.exists():
        target = os.path.relpath(workspace / "images", ctx.mvs_dir)
        link.symlink_to(target, target_is_directory=True)


def run_densify(ctx: Context) -> None:
    ctx.mvs_dir.mkdir(parents=True, exist_ok=True)
    workspace = undistorted_dir(ctx)
    if not workspace.is_dir():
        raise CommandError(f"No existe {workspace}: ejecutar antes la etapa 'undistort'.")
    _link_undistorted_images(ctx, workspace)

    # 1) Conversión del workspace COLMAP a formato OpenMVS (.mvs)
    _run(ctx, [
        "InterfaceCOLMAP",
        "-w", ctx.mvs_dir,
        "-i", workspace,
        "-o", SCENE,
    ], "dense.log")

    # 2) Nube de puntos densa
    dcfg = ctx.cfg["openmvs"]["densify"]
    cmd = [
        "DensifyPointCloud",
        "-w", ctx.mvs_dir,
        SCENE,
        "-o", SCENE_DENSE,
        "--resolution-level", str(dcfg.get("resolution_level", 1)),
        "--number-views", str(dcfg.get("number_views", 0)),
    ]
    cmd += _cuda_args(ctx)
    cmd += extra_args_to_cli(dcfg.get("extra_args"))
    _run(ctx, cmd, "dense.log")
    _require(ctx, SCENE_DENSE, "dense")


def run_mesh(ctx: Context) -> None:
    mcfg = ctx.cfg["openmvs"]["mesh"]
    if not mcfg.get("enabled", True):
        print("[mesh] openmvs.mesh.enabled = false: etapa omitida")
        return
    _require(ctx, SCENE_DENSE, "dense")

    cmd = [
        "ReconstructMesh",
        "-w", ctx.mvs_dir,
        SCENE_DENSE,
        "-o", SCENE_MESH,
        "--decimate", str(mcfg.get("decimate", 1.0)),
    ]
    cmd += extra_args_to_cli(mcfg.get("extra_args"))
    _run(ctx, cmd, "mesh.log")
    _require(ctx, MESH_PLY, "mesh")

    rcfg = ctx.cfg["openmvs"]["refine"]
    if rcfg.get("enabled", True):
        cmd = [
            "RefineMesh",
            "-w", ctx.mvs_dir,
            SCENE_DENSE,
            "--mesh-file", MESH_PLY,
            "-o", SCENE_MESH_REFINED,
            "--scales", str(rcfg.get("scales", 2)),
        ]
        cmd += _cuda_args(ctx)
        cmd += extra_args_to_cli(rcfg.get("extra_args"))
        try:
            _run(ctx, cmd, "mesh.log")
        except CommandError as exc:
            # El refinado es una mejora, no un requisito: si falla se continúa
            # con la malla sin refinar para no bloquear el texturizado.
            print(f"[mesh] ADVERTENCIA: RefineMesh falló; se continúa con la malla "
                  f"sin refinar. Detalle: {exc}")


def run_texture(ctx: Context) -> None:
    tcfg = ctx.cfg["openmvs"]["texture"]
    if not tcfg.get("enabled", True):
        print("[texture] openmvs.texture.enabled = false: etapa omitida")
        return
    # Usar la malla refinada si existe; si no, la malla base
    mesh_file = None
    for candidate in (MESH_REFINED_PLY, MESH_PLY):
        if (ctx.mvs_dir / candidate).is_file():
            mesh_file = candidate
            break
    if mesh_file is None:
        raise CommandError(f"No hay malla en {ctx.mvs_dir}; ¿se ejecutó la etapa 'mesh'?")

    cmd = [
        "TextureMesh",
        "-w", ctx.mvs_dir,
        SCENE_DENSE,
        "--mesh-file", mesh_file,
        "-o", SCENE_TEXTURE,
        "--export-type", str(tcfg.get("export_type", "obj")),
    ]
    cmd += _cuda_args(ctx)
    cmd += extra_args_to_cli(tcfg.get("extra_args"))
    _run(ctx, cmd, "texture.log")
