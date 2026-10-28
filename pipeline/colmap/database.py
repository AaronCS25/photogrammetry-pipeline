"""Transactional import of COLMAP 3.13 SIFT databases with stable image IDs.

Never edits source DBs. Camera sensors and trivial single-camera rigs are
remapped, as are frame_data and pose priors. Other sensor types are rejected.
Matches from a parent are retained only if the caller certifies compatibility.
"""
from __future__ import annotations

from pathlib import Path

from ..config import ConfigError
from ..validation import feature_database, readonly, writable


def backup(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as src, writable(dest) as dst:
        src.backup(dst)


def _insert(db, table, columns, values):
    marks = ",".join("?" for _ in values)
    return db.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})", values).lastrowid


def consolidate(dest: Path, assets: list[dict], parent: Path | None = None,
                keep_matches: bool = True) -> dict:
    """assets: [{database, names: {local_name: canonical_name}, camera_group}]."""
    if not assets:
        raise ConfigError("No hay features que consolidar")
    if dest.exists():
        raise ConfigError(f"La BD de destino debe ser nueva: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if parent:
        backup(parent, dest)
    else:
        with readonly(Path(assets[0]["database"])) as src, writable(dest) as db:
            for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"):
                db.execute(sql)
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS index_name ON images(name)")
    try:
        with writable(dest) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE IF NOT EXISTS pipeline_camera_groups(camera_id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            if not keep_matches:
                db.execute("DELETE FROM matches")
                db.execute("DELETE FROM two_view_geometries")
            for asset in assets:
                source = Path(asset["database"])
                feature_database(source, set(asset["names"]))
                with readonly(source) as src:
                    camera_map, image_map, rig_map, frame_map = {}, {}, {}, {}
                    existing = dict(db.execute("SELECT name,image_id FROM images"))
                    images = src.execute("SELECT image_id,name,camera_id FROM images ORDER BY name").fetchall()
                    new_images = [r for r in images if asset["names"][r[1]] not in existing]
                    if not new_images:
                        continue
                    if len(new_images) != len(images):
                        raise ConfigError("Importación parcial de un asset: reconstruir desde caché")
                    group = asset["camera_group"]
                    for cid, model, w, h, params, prior in src.execute("SELECT * FROM cameras"):
                        found = db.execute("""SELECT c.camera_id FROM cameras c
                            JOIN pipeline_camera_groups g USING(camera_id)
                            WHERE g.name=? AND c.model=? AND c.width=? AND c.height=? AND c.params=?""",
                                           (group, model, w, h, params)).fetchone()
                        if found:
                            camera_map[cid] = found[0]
                        else:
                            new = _insert(db, "cameras", ("model", "width", "height", "params", "prior_focal_length"),
                                          (model, w, h, params, prior))
                            camera_map[cid] = new
                            db.execute("INSERT INTO pipeline_camera_groups VALUES (?,?)", (new, group))
                    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "rigs" in tables:
                        for rid, sensor, kind in src.execute("SELECT * FROM rigs"):
                            if kind != 0 or sensor not in camera_map:
                                raise ConfigError("Solo se admite sensor de cámara COLMAP (type=0)")
                            rig_map[rid] = _insert(db, "rigs", ("ref_sensor_id", "ref_sensor_type"), (camera_map[sensor], kind))
                        for rid, sid, kind, transform in src.execute("SELECT * FROM rig_sensors"):
                            if kind != 0:
                                raise ConfigError("Rig con sensor no soportado")
                            _insert(db, "rig_sensors", ("rig_id", "sensor_id", "sensor_type", "sensor_from_rig"),
                                    (rig_map[rid], camera_map[sid], kind, transform))
                        for fid, rid in src.execute("SELECT * FROM frames"):
                            frame_map[fid] = _insert(db, "frames", ("rig_id",), (rig_map[rid],))
                    for iid, name, cid in images:
                        image_map[iid] = _insert(db, "images", ("name", "camera_id"),
                                                 (asset["names"][name], camera_map[cid]))
                    for table in ("keypoints", "descriptors", "pose_priors"):
                        if table not in tables:
                            continue
                        cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
                        for row in src.execute(f"SELECT * FROM {table}"):
                            values = list(row)
                            values[cols.index("image_id")] = image_map[values[cols.index("image_id")]]
                            _insert(db, table, cols, values)
                    if "frame_data" in tables:
                        for fid, did, sid, kind in src.execute("SELECT * FROM frame_data"):
                            if kind != 0:
                                raise ConfigError("frame_data no corresponde a cámara")
                            _insert(db, "frame_data", ("frame_id", "data_id", "sensor_id", "sensor_type"),
                                    (frame_map[fid], image_map[did], camera_map[sid], kind))
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ConfigError("Consolidación con referencias inconsistentes")
        expected = {n for a in assets for n in a["names"].values()}
        return feature_database(dest, expected)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
