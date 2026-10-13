"""Etapa 'clean' (opcional): limpieza de la malla ANTES de texturizar.

La reconstrucción deja piezas sueltas (fotos tomadas a otra distancia detrás
de un obstáculo, restos de cables...), huecos donde había árboles o autos y
superficies "grumosas". Esta etapa opera sobre scene_mesh[_refined].ply:

  1. Componentes conexas: se descartan las que tienen menos caras que una
     fracción de la componente mayor (o que un mínimo absoluto). Python puro
     (numpy): el PLY binario de OpenMVS se lee y escribe aquí mismo.
  2. ReconstructMesh --mesh-file (modo "solo limpieza" de OpenMVS): cierre de
     huecos, suavizado laplaciano, decimación y, opcionalmente, remallado.

El resultado es scene_mesh_clean.ply, que TextureMesh texturiza con las fotos
originales (acepta cualquier malla en el marco de la escena). Iterar la
limpieza no requiere densificar de nuevo: `--from-stage clean --force`.

También vive aquí el relleno ("inpainting") de las zonas sin textura del
atlas, que TextureMesh pinta de un color plano (ver `inpaint_atlases`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config import Context
from .executor import CommandError, extra_args_to_cli, run_cmd

MESH_COMPONENTS_PLY = "scene_mesh_components.ply"   # intermedio (tras el paso 1)

_PLY_TYPES = {
    "char": "i1", "int8": "i1", "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2", "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4", "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4", "double": "f8", "float64": "f8",
}


# ---------------------------------------------------------------------------
# PLY binario (little endian) con triángulos: lo que escribe OpenMVS
# ---------------------------------------------------------------------------

def read_ply_mesh(path: Path):
    """-> (vertices: array estructurado con TODAS sus propiedades, faces: (M, 3) int,
    face_dtype: dtype del registro de cara). Solo binary_little_endian y caras
    triangulares (una lista por cara): suficiente para las mallas de OpenMVS."""
    import numpy as np

    with open(path, "rb") as fh:
        header_lines = []
        while True:
            line = fh.readline()
            if not line:
                raise CommandError(f"PLY sin end_header: {path}")
            header_lines.append(line.decode("ascii", errors="replace").strip())
            if header_lines[-1] == "end_header":
                break
        if "format binary_little_endian 1.0" not in header_lines:
            raise CommandError(f"PLY no soportado (se esperaba binary_little_endian): {path}")

        elements: list[tuple[str, int, list]] = []   # (nombre, nº, [(prop, dtype|list)])
        for ln in header_lines:
            parts = ln.split()
            if not parts:
                continue
            if parts[0] == "element":
                elements.append((parts[1], int(parts[2]), []))
            elif parts[0] == "property":
                if parts[1] == "list":
                    elements[-1][2].append((parts[4], ("list", _PLY_TYPES[parts[2]], _PLY_TYPES[parts[3]])))
                else:
                    elements[-1][2].append((parts[2], _PLY_TYPES[parts[1]]))

        vertices = faces = None
        face_dtype = None
        for name, count, props in elements:
            if name == "vertex":
                dtype = np.dtype([(p, "<" + t) for p, t in props])
                vertices = np.frombuffer(fh.read(dtype.itemsize * count), dtype=dtype).copy()
            elif name == "face":
                if len(props) != 1 or not isinstance(props[0][1], tuple):
                    raise CommandError(f"PLY: elemento face no soportado ({props}) en {path}")
                _, (_, cnt_t, idx_t) = props[0]
                face_dtype = np.dtype([("n", "<" + cnt_t), ("v", "<" + idx_t, (3,))])
                raw = np.frombuffer(fh.read(face_dtype.itemsize * count), dtype=face_dtype)
                if count and not (raw["n"] == 3).all():
                    raise CommandError(f"PLY: caras no triangulares en {path}")
                faces = raw["v"].astype(np.int64)
            else:   # otros elementos (edges...): no se leen; OpenMVS no los escribe
                raise CommandError(f"PLY: elemento '{name}' no soportado en {path}")
        if vertices is None or faces is None:
            raise CommandError(f"PLY sin vertex/face: {path}")
    return vertices, faces, face_dtype


def write_ply_mesh(path: Path, vertices, faces, face_dtype=None) -> None:
    import numpy as np

    if face_dtype is None:
        face_dtype = np.dtype([("n", "u1"), ("v", "<i4", (3,))])
    props = "".join(f"property {_ply_name(vertices.dtype[name])} {name}\n" for name in vertices.dtype.names)
    cnt_t, idx_t = face_dtype["n"], face_dtype["v"].base
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment limpieza: pipeline clean\n"
        f"element vertex {len(vertices)}\n{props}"
        f"element face {len(faces)}\nproperty list {_ply_name(cnt_t)} {_ply_name(idx_t)} vertex_indices\n"
        "end_header\n"
    )
    raw = np.empty(len(faces), dtype=face_dtype)
    raw["n"] = 3
    raw["v"] = faces
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        fh.write(vertices.astype(vertices.dtype.newbyteorder("<"), copy=False).tobytes())
        fh.write(raw.tobytes())


def _ply_name(dt) -> str:
    inv = {"i1": "char", "u1": "uchar", "i2": "short", "u2": "ushort",
           "i4": "int", "u4": "uint", "f4": "float", "f8": "double"}
    return inv[dt.str[1:]]


# ---------------------------------------------------------------------------
# Componentes conexas (numpy puro: hooking + salto de punteros)
# ---------------------------------------------------------------------------

def connected_components(faces, n_vertices: int):
    """Etiqueta de componente por vértice (int64, valores = raíz mínima)."""
    import numpy as np

    labels = np.arange(n_vertices, dtype=np.int64)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    a, b = edges[:, 0], edges[:, 1]
    while True:
        la, lb = labels[a], labels[b]
        m = np.minimum(la, lb)
        before = labels.copy()
        np.minimum.at(labels, a, m)
        np.minimum.at(labels, b, m)
        while True:                       # compresión de caminos
            nl = labels[labels]
            if np.array_equal(nl, labels):
                break
            labels = nl
        if np.array_equal(labels, before):
            return labels


def keep_main_components(vertices, faces, min_fraction: float = 0.0, min_faces: int = 0):
    """Descarta componentes con menos caras que max(min_faces, min_fraction *
    caras de la mayor). -> (vertices, faces, info)."""
    import numpy as np

    labels = connected_components(faces, len(vertices))
    face_label = labels[faces[:, 0]]
    comp_ids, comp_faces = np.unique(face_label, return_counts=True)
    largest = int(comp_faces.max()) if len(comp_faces) else 0
    threshold = max(int(min_faces), int(round(min_fraction * largest)))
    keep_ids = comp_ids[comp_faces >= threshold]
    keep_face = np.isin(face_label, keep_ids)
    faces_kept = faces[keep_face]
    used = np.zeros(len(vertices), dtype=bool)
    used[faces_kept.ravel()] = True
    remap = np.cumsum(used) - 1
    info = {
        "components": int(len(comp_ids)), "components_kept": int(len(keep_ids)),
        "largest_faces": largest, "threshold_faces": threshold,
        "faces_before": int(len(faces)), "faces_after": int(len(faces_kept)),
        "vertices_before": int(len(vertices)), "vertices_after": int(used.sum()),
    }
    return vertices[used], remap[faces_kept], info


# ---------------------------------------------------------------------------
# Etapa
# ---------------------------------------------------------------------------

def clean_cfg(ctx: Context) -> dict:
    return (ctx.cfg.get("openmvs") or {}).get("clean") or {}


def clean_enabled(ctx: Context) -> bool:
    return bool(clean_cfg(ctx).get("enabled"))


def run_clean(ctx: Context) -> None:
    from . import mvs
    ccfg = clean_cfg(ctx)
    if not ccfg.get("enabled"):
        print("[clean] openmvs.clean.enabled = false: etapa omitida")
        return
    source = None
    for candidate in (mvs.MESH_REFINED_PLY, mvs.MESH_PLY):
        if (ctx.mvs_dir / candidate).is_file():
            source = ctx.mvs_dir / candidate
            break
    if source is None:
        raise CommandError(f"No hay malla en {ctx.mvs_dir}; ¿se ejecutó la etapa 'mesh'?")
    out = ctx.mvs_dir / mvs.MESH_CLEAN_PLY
    out.unlink(missing_ok=True)
    info: dict = {"source": source.name}

    # 1) componentes conexas
    min_component = float(ccfg.get("min_component", 0) or 0)
    min_fraction, min_faces = (min_component, 0) if min_component <= 1 else (0.0, int(min_component))
    current = source
    if min_component > 0:
        vertices, faces, face_dtype = read_ply_mesh(source)
        vertices, faces, comp = keep_main_components(vertices, faces, min_fraction, min_faces)
        current = ctx.mvs_dir / MESH_COMPONENTS_PLY
        write_ply_mesh(current, vertices, faces, face_dtype)
        info["components"] = comp
        print(f"[clean] componentes: {comp['components']} -> {comp['components_kept']} "
              f"(umbral {comp['threshold_faces']} caras); caras {comp['faces_before']} -> {comp['faces_after']}")

    # 2) OpenMVS: huecos, suavizado, decimación, remallado
    decimate = float(ccfg.get("decimate", 1.0))
    close_holes = int(ccfg.get("close_holes", 0) or 0)
    smooth = int(ccfg.get("smooth", 0) or 0)
    remove_spurious = float(ccfg.get("remove_spurious", 0) or 0)
    edge_length = float(ccfg.get("edge_length", 0) or 0)
    user_args = ccfg.get("extra_args") or {}
    if decimate < 1 or close_holes or smooth or remove_spurious or edge_length or user_args:
        scene = mvs.SCENE_ROI if (ctx.mvs_dir / mvs.SCENE_ROI).is_file() else mvs.SCENE
        cmd = [
            "ReconstructMesh", "-w", ctx.mvs_dir, scene,
            "--mesh-file", current.name,          # modo limpieza: no reconstruye
            "-o", mvs.SCENE_MESH_CLEAN,
            "--decimate", str(decimate),
            "--close-holes", str(close_holes),
            "--smooth", str(smooth),
            "--remove-spurious", str(remove_spurious),
            "--remove-spikes", "1",
            "--edge-length", str(edge_length),
            "--crop-to-roi", "0",                 # la malla ya se recortó al reconstruirla
        ]
        cmd += extra_args_to_cli(user_args)
        run_cmd(cmd, ctx.logs_dir / "clean.log", cwd=ctx.mvs_dir)
        if not out.is_file():
            raise CommandError(f"ReconstructMesh --mesh-file no produjo {out}")
        info["openmvs"] = {"decimate": decimate, "close_holes": close_holes, "smooth": smooth,
                           "remove_spurious": remove_spurious, "edge_length": edge_length}
    else:
        shutil.copyfile(current, out)
    if current != source:
        current.unlink(missing_ok=True)

    from .metrics import parse_ply_counts
    counts = parse_ply_counts(out)
    info["result"] = {"vertices": counts.get("vertex"), "faces": counts.get("face")}
    ctx.metrics_dir.mkdir(parents=True, exist_ok=True)
    with open(ctx.metrics_dir / "clean_info.json", "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    print(f"[clean] malla limpia: {out.name} ({counts.get('face')} caras)")


# ---------------------------------------------------------------------------
# Relleno del atlas de textura
# ---------------------------------------------------------------------------

def parse_empty_color(value) -> tuple[int, int, int]:
    """--empty-color de OpenMVS como entero 0xRRGGBB -> (r, g, b)."""
    v = int(value)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _down2(a):
    """Media 2x2 (relleno con ceros si la dimensión es impar)."""
    import numpy as np
    if a.shape[0] % 2:
        a = np.concatenate([a, np.zeros_like(a[:1])], axis=0)
    if a.shape[1] % 2:
        a = np.concatenate([a, np.zeros_like(a[:, :1])], axis=1)
    return (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) * 0.25


def _up_to(a, shape):
    """Reescala (bilineal) un array float (h, w[, c]) al tamaño shape = (h, w)."""
    import numpy as np
    from PIL import Image
    h, w = shape
    if a.ndim == 2:
        return np.asarray(Image.fromarray(a, mode="F").resize((w, h), Image.BILINEAR))
    return np.stack([np.asarray(Image.fromarray(np.ascontiguousarray(a[..., c]), mode="F")
                                .resize((w, h), Image.BILINEAR)) for c in range(a.shape[2])], axis=2)


def inpaint_image(img, color: tuple[int, int, int], tolerance: int = 24, dilate: int = 2):
    """Rellena los píxeles ~color con el color del entorno (pull-push piramidal,
    ponderado solo por píxeles conocidos: el color vacío nunca se mezcla en el
    relleno). `dilate` amplía la máscara unos px para cubrir el halo JPEG.
    -> (array, fracción de píxeles vacíos)."""
    import numpy as np

    arr = np.asarray(img.convert("RGB"))
    a16 = arr.astype(np.int16)
    if tuple(color) == (255, 0, 255):
        # Magenta (por defecto): detección por tono, min(R,B) - G, que capta
        # también el halo que deja la compresión JPEG en el borde de cada zona
        # (una mezcla al 50 % con una pared clara sigue dando > 100).
        mask = (np.minimum(a16[..., 0], a16[..., 2]) - a16[..., 1]) >= 255 - 5 * tolerance
    else:
        mask = (np.abs(a16 - np.array(color, dtype=np.int16)) <= tolerance).all(axis=2)
    frac = float(mask.mean())
    if frac == 0.0 or frac > 0.98:
        return arr, frac
    for _ in range(dilate):
        grown = mask.copy()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grown |= np.roll(mask, (dy, dx), axis=(0, 1))
        mask = grown
    # pull: pirámide de (color premultiplicado, peso) hasta que todo sea conocido
    weight = (~mask).astype(np.float32)
    premul = arr.astype(np.float32) * weight[..., None]
    levels = [(premul, weight)]
    while levels[-1][1].min() <= 0.0 and max(levels[-1][1].shape) > 1:
        levels.append((_down2(levels[-1][0]), _down2(levels[-1][1])))
    premul, weight = levels[-1]
    known = weight > 0
    fill = np.zeros_like(premul)
    fill[known] = premul[known] / weight[known][:, None]
    if not known.all():                      # imagen vacía casi por completo
        fill[~known] = fill[known].mean(axis=0) if known.any() else 0.0
    # push: cada nivel toma su valor conocido y completa con el nivel grueso
    for premul, weight in reversed(levels[:-1]):
        up = _up_to(fill, weight.shape)
        fill = premul + up * (1.0 - weight)[..., None]
    out = arr.copy()
    out[mask] = np.clip(fill[mask] + 0.5, 0, 255).astype(np.uint8)
    return out, frac


def inpaint_atlases(mvs_dir: Path, color: tuple[int, int, int], tolerance: int = 24) -> dict:
    """Aplica inpaint_image a los atlas de scene_texture (JPG/PNG) in situ."""
    from PIL import Image

    report = {}
    for path in sorted(mvs_dir.glob("scene_texture*")):
        if path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        with Image.open(path) as img:
            out, frac = inpaint_image(img, color, tolerance)
        if frac > 0.0:
            Image.fromarray(out).save(path, quality=95) if path.suffix.lower() != ".png" \
                else Image.fromarray(out).save(path)
        report[path.name] = round(frac, 4)
    return report
