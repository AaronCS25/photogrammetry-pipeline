"""Local contract tests: no Slurm jobs, feature extraction or container builds."""
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from pipeline.cache import asset_signature, feature_signature, legacy_allowed
from pipeline.colmap.database import consolidate
from pipeline.colmap.matching import temporal_pairs
from pipeline.config import ConfigError, REPO_ROOT
from pipeline.inventory import scan
from pipeline.planner import plan_scene
from pipeline.scheduler import slurm
from pipeline.state import atomic_json, lock
from pipeline.validation import feature_database, readonly, writable


def fixture_db(path, names=("f00002.jpg", "f00001.jpg")):
    """3.13 schema including rigs/frame_data; deliberately non-temporal IDs."""
    with writable(path) as d:
        d.executescript("""
        CREATE TABLE cameras(camera_id INTEGER PRIMARY KEY,model INTEGER,width INTEGER,height INTEGER,params BLOB,prior_focal_length INTEGER);
        CREATE TABLE images(image_id INTEGER PRIMARY KEY,name TEXT UNIQUE,camera_id INTEGER REFERENCES cameras(camera_id));
        CREATE TABLE keypoints(image_id INTEGER PRIMARY KEY REFERENCES images(image_id),rows INTEGER,cols INTEGER,data BLOB);
        CREATE TABLE descriptors(image_id INTEGER PRIMARY KEY REFERENCES images(image_id),rows INTEGER,cols INTEGER,data BLOB);
        CREATE TABLE pose_priors(image_id INTEGER PRIMARY KEY REFERENCES images(image_id),position BLOB,coordinate_system INTEGER,position_covariance BLOB);
        CREATE TABLE rigs(rig_id INTEGER PRIMARY KEY,ref_sensor_id INTEGER,ref_sensor_type INTEGER);
        CREATE TABLE rig_sensors(rig_id INTEGER REFERENCES rigs(rig_id),sensor_id INTEGER,sensor_type INTEGER,sensor_from_rig BLOB);
        CREATE TABLE frames(frame_id INTEGER PRIMARY KEY,rig_id INTEGER REFERENCES rigs(rig_id));
        CREATE TABLE frame_data(frame_id INTEGER REFERENCES frames(frame_id),data_id INTEGER,sensor_id INTEGER,sensor_type INTEGER);
        CREATE TABLE matches(pair_id INTEGER PRIMARY KEY,rows INTEGER,cols INTEGER,data BLOB);
        CREATE TABLE two_view_geometries(pair_id INTEGER PRIMARY KEY,rows INTEGER,cols INTEGER,data BLOB,config INTEGER,F BLOB,E BLOB,H BLOB,qvec BLOB,tvec BLOB);
        """)
        d.execute("INSERT INTO cameras VALUES(1,2,3840,2160,?,0)", (struct.pack("dddd", 2000, 1920, 1080, 0),))
        d.execute("INSERT INTO rigs VALUES(1,1,0)")
        for i, name in enumerate(names, 1):
            d.execute("INSERT INTO images VALUES(?,?,1)", (i, name))
            d.execute("INSERT INTO frames VALUES(?,1)", (i,))
            d.execute("INSERT INTO frame_data VALUES(?,?,1,0)", (i, i))
            d.execute("INSERT INTO keypoints VALUES(?,2,4,?)", (i, bytes(32)))
            d.execute("INSERT INTO descriptors VALUES(?,2,128,?)", (i, bytes([i] * 256)))
            d.execute("INSERT INTO pose_priors VALUES(?,?,0,NULL)", (i, bytes(24)))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for n in ("a", "b"):
            fixture_db(self.root / f"{n}.db")

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, name):
        return {"database": str(self.root / f"{name}.db"), "camera_group": name,
                "names": {x: f"drone/{name}_{x}" for x in ("f00001.jpg", "f00002.jpg")}}

    def test_merge_remaps_rigs_and_preserves_descriptor_bytes(self):
        out = self.root / "merged.db"
        stats = consolidate(out, [self.asset("a"), self.asset("b")])
        self.assertEqual(stats, {"images": 4, "features": 8})
        with readonly(out) as d:
            names = dict(d.execute("SELECT name,image_id FROM images"))
            self.assertEqual(len(names), 4)
            for name, iid in names.items():
                expected = bytes([1 if "f00002" in name else 2] * 256)
                self.assertEqual(d.execute("SELECT data FROM descriptors WHERE image_id=?", (iid,)).fetchone()[0], expected)
                frame = d.execute("SELECT sensor_id FROM frame_data WHERE data_id=?", (iid,)).fetchone()
                cid = d.execute("SELECT camera_id FROM images WHERE image_id=?", (iid,)).fetchone()[0]
                self.assertEqual(frame[0], cid)
            self.assertEqual(d.execute("SELECT COUNT(*) FROM frames").fetchone()[0], 4)
            self.assertEqual(d.execute("SELECT COUNT(*) FROM pose_priors").fetchone()[0], 4)

    def test_update_preserves_old_ids_and_matches(self):
        parent, updated = self.root / "parent.db", self.root / "new.db"
        consolidate(parent, [self.asset("a")])
        with writable(parent) as d:
            before = d.execute("SELECT name,image_id FROM images ORDER BY name").fetchall()
            d.execute("INSERT INTO matches VALUES(2147483649,1,2,?)", (bytes(8),))
        consolidate(updated, [self.asset("a"), self.asset("b")], parent=parent)
        with readonly(updated) as d:
            after = d.execute("SELECT name,image_id FROM images WHERE name LIKE 'drone/a_%' ORDER BY name").fetchall()
            self.assertEqual(after, before)
            self.assertEqual(d.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 1)
        # No writes to source/parent: parent still contains just the old images.
        self.assertEqual(feature_database(parent)["images"], 2)

    def test_rebuild_matches_does_not_reextract_features(self):
        parent, out = self.root / "parent.db", self.root / "new.db"
        consolidate(parent, [self.asset("a")])
        with writable(parent) as d:
            d.execute("INSERT INTO matches VALUES(1,1,2,?)", (bytes(8),))
        consolidate(out, [self.asset("a")], parent, keep_matches=False)
        with readonly(out) as d:
            self.assertEqual(d.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 0)
        self.assertEqual(feature_database(out)["features"], 4)

    def test_empty_or_truncated_descriptors_rejected(self):
        with writable(self.root / "a.db") as d:
            d.execute("UPDATE descriptors SET data=? WHERE image_id=1", (b"bad",))
        with self.assertRaises(ConfigError):
            consolidate(self.root / "bad.db", [self.asset("a")])
        self.assertFalse((self.root / "bad.db").exists())

    def test_temporal_order_is_not_image_id_or_cross_clip(self):
        images = [{"relative": "a_2", "asset": "a", "index": 2, "kind": "video"},
                  {"relative": "b_1", "asset": "b", "index": 1, "kind": "video"},
                  {"relative": "a_1", "asset": "a", "index": 1, "kind": "video"},
                  {"relative": "a_3", "asset": "a", "index": 3, "kind": "video"}]
        self.assertEqual(temporal_pairs(images, 1), [("a_1", "a_2"), ("a_2", "a_3")])


class SceneFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "configs/profiles").mkdir(parents=True)
        (self.root / "datasets/raw/001").mkdir(parents=True)
        self.profile = yaml.safe_load((REPO_ROOT / "configs/profiles/barranco_sparse4k.yaml").read_text())
        self.save_profile()
        self.video = self.root / "datasets/raw/001/clip.MP4"
        self.video.write_bytes(b"\x00\x00\x00\x20ftypisom" + bytes(32))

    def save_profile(self):
        (self.root / "configs/profiles/barranco_sparse4k.yaml").write_text(yaml.safe_dump(self.profile))

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, **kw):
        return plan_scene("001", root=self.root, **kw)

    def seed_parent(self):
        p = self.plan()
        run = "outputs/001/old"
        atomic_json(self.root / run / "run.json", {"assets": p["assets"], "feature_signature": p["feature_signature"],
                                                  "matching": self.profile["matching"], "mapping": self.profile["mapping"],
                                                  "code_signature": p["code_signature"]})
        atomic_json(self.root / "datasets/_state/001/latest.json", {"run": run})
        return p

class PlanTests(SceneFixture):
    def test_plan_read_only_and_maps_excluded(self):
        (self.video.parent / "01_map.png").write_bytes(b"not a photo")
        p = self.plan()
        self.assertTrue(p["ready"])
        self.assertEqual(len(p["assets"]), 1)
        self.assertFalse((self.root / "datasets/_state").exists())

    def test_quota_html_blocks_unless_partial_explicit(self):
        (self.video.parent / "failed.MP4").write_text("<!DOCTYPE html><title>Quota exceeded</title>")
        self.assertFalse(self.plan()["ready"])
        self.assertTrue(self.plan(allow_partial=True)["ready"])
        self.assertEqual(len(self.plan()["errors"]), 1)

    def test_new_asset_reuses_old_recipe_and_enables_incremental(self):
        old = self.seed_parent()
        (self.video.parent / "new.MP4").write_bytes(b"ftyp" + bytes(40))
        new = self.plan()
        self.assertTrue(new["incremental"])
        self.assertFalse(new["reuse_sparse"])
        self.assertEqual(new["assets"][0]["cache_key"], old["assets"][0]["cache_key"])

    def test_only_matching_change_keeps_feature_keys(self):
        old = self.seed_parent()
        self.profile["matching"]["overlap"] = 8
        self.save_profile()
        new = self.plan()
        self.assertTrue(new["incremental"])
        self.assertFalse(new["reuse_matches"])
        self.assertEqual(old["assets"][0]["cache_key"], new["assets"][0]["cache_key"])

    def test_no_new_media_can_continue_to_dense_without_sfm(self):
        self.seed_parent()
        self.assertTrue(self.plan(target="dense")["reuse_sparse"])

    def test_camera_or_resolution_change_invalidates_features_and_legacy(self):
        old = self.seed_parent()
        self.profile["frames"]["resize"] = {"long_edge": 1920}
        self.save_profile()
        new = self.plan()
        self.assertFalse(new["incremental"])
        self.assertNotEqual(old["assets"][0]["cache_key"], new["assets"][0]["cache_key"])
        self.assertFalse(legacy_allowed(self.profile))

    def test_deleted_asset_requires_rebuild(self):
        self.seed_parent()
        self.video.unlink()
        (self.video.parent / "replacement.MP4").write_bytes(b"ftyp" + bytes(50))
        self.assertFalse(self.plan()["incremental"])
        self.assertEqual(len(self.plan()["removed"]), 1)

    def test_lock_refuses_second_writer(self):
        with lock(self.root / "lock"):
            with self.assertRaises(ConfigError):
                with lock(self.root / "lock"):
                    pass


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        image = self.root / "image.sif"
        image.touch()
        profile = yaml.safe_load((REPO_ROOT / "configs/profiles/barranco_sparse4k.yaml").read_text())
        profile["runtime"]["image"] = str(image)
        self.request = {"id": "abc123", "scene": "001", "target": "sparse", "profile": profile}
        self.path = self.root / "request.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_dependencies_and_idempotent_receipt(self):
        from subprocess import CompletedProcess
        with patch("pipeline.scheduler.slurm.subprocess.run") as run:
            run.side_effect = [CompletedProcess([], 0, f"{i}\n", "") for i in (101, 102, 103, 104, 105)]
            receipt = slurm.submit(self.root, self.path, self.request)
            self.assertEqual([j["phase"] for j in receipt["jobs"]], ["prepare", "features", "matching", "mapping", "publish"])
            self.assertIn("--dependency=afterok:101", run.call_args_list[1].args[0])
            self.assertEqual(slurm.submit(self.root, self.path, self.request), receipt)
            self.assertEqual(run.call_count, 5)

    def test_uncertain_submission_cannot_duplicate(self):
        import subprocess
        with patch("pipeline.scheduler.slurm.subprocess.run", side_effect=subprocess.TimeoutExpired("sbatch", 60)):
            with self.assertRaises(ConfigError):
                slurm.submit(self.root, self.path, self.request)
        with patch("pipeline.scheduler.slurm.subprocess.run") as run:
            with self.assertRaises(ConfigError):
                slurm.submit(self.root, self.path, self.request)
            run.assert_not_called()

    def test_missing_image_prevents_any_submission(self):
        self.request["profile"]["runtime"]["image"] = "missing.sif"
        with patch("pipeline.scheduler.slurm.subprocess.run") as run:
            with self.assertRaises(ConfigError):
                slurm.submit(self.root, self.path, self.request)
            run.assert_not_called()

    def test_invalid_later_phase_does_not_submit_earlier_phase(self):
        self.request["profile"]["resources"]["mapping"]["mem_gb"] = 200
        with patch("pipeline.scheduler.slurm.subprocess.run") as run:
            with self.assertRaises(ConfigError):
                slurm.submit(self.root, self.path, self.request)
            run.assert_not_called()

    def test_reconcile_recovers_exact_lost_ack_without_resubmitting(self):
        import subprocess
        atomic_json(self.path, self.request)
        atomic_json(self.path.with_suffix(".receipt.json"), {
            "request": "abc123", "status": "uncertain", "jobs": [], "attempting": {"phase": "prepare"}})
        comment = "scene:001:request:abc123:phase:prepare"
        with patch("pipeline.scheduler.slurm.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, f"101|{comment}\n", "")
            receipt = slurm.reconcile(self.path)
            self.assertEqual(receipt["jobs"][0]["job"], "101")
            self.assertEqual(receipt["status"], "submitting")
            self.assertTrue(all(c.args[0][0] != "sbatch" for c in run.call_args_list))


class AdoptionTests(SceneFixture):
    def test_legacy_features_are_imported_without_running_sift(self):
        from pipeline import workflow
        legacy = self.root / "legacy/clip"
        (legacy / "frames").mkdir(parents=True)
        fixture_db(legacy / "database.db")
        for name in ("f00001.jpg", "f00002.jpg"):
            (legacy / "frames" / name).write_bytes(b"jpeg fixture")
        (self.root / "configs/scenes").mkdir(parents=True)
        (self.root / "configs/scenes/001.yaml").write_text(yaml.safe_dump({
            "raw_dir": "datasets/raw/001", "legacy_root": "legacy"}))
        (self.root / "configs/default.yaml").write_text((REPO_ROOT / "configs/default.yaml").read_text())
        request = self.plan()
        self.assertEqual(request["assets"][0]["action"], "import-legacy")
        request.update(experiment="test_run", run="outputs/001/test_run")
        ctx = workflow.context(self.root, request)
        ctx.prepare()
        with patch("pipeline.workflow.image_dimensions", return_value=(3840, 2160)), \
             patch("pipeline.workflow.probe_video", return_value={"streams": [{"duration": "1"}]}), \
             patch("pipeline.workflow.run_cmd") as cmd:
            workflow.prepare(self.root, request, ctx)
            imports = workflow.features(self.root, request, ctx)
            self.assertEqual(len(imports), 1)
            self.assertEqual(feature_database(Path(imports[0]["database"]))["images"], 2)
            cmd.assert_not_called()
            # Second call is a validated cache hit, still no SIFT.
            workflow.features(self.root, request, ctx)
            cmd.assert_not_called()
        self.assertEqual(feature_database(legacy / "database.db")["images"], 2)

    def test_sift_thread_budget_does_not_invalidate_descriptors(self):
        before = feature_signature(self.profile)
        self.profile["features"]["threads"] = 4
        self.assertEqual(before, feature_signature(self.profile))


class LifecycleTests(SceneFixture):
    def test_all_worker_phases_connect_and_publish_without_sift(self):
        from pipeline import workflow
        legacy = self.root / "legacy/clip"
        (legacy / "frames").mkdir(parents=True)
        fixture_db(legacy / "database.db")
        for name in ("f00001.jpg", "f00002.jpg"):
            (legacy / "frames" / name).write_bytes(b"jpeg fixture")
        (self.root / "configs/scenes").mkdir(parents=True)
        (self.root / "configs/scenes/001.yaml").write_text(yaml.safe_dump({"raw_dir": "datasets/raw/001", "legacy_root": "legacy"}))
        (self.root / "configs/default.yaml").write_text((REPO_ROOT / "configs/default.yaml").read_text())
        request = self.plan()
        request.update(id="trial", experiment="trial", run="outputs/001/trial")
        ctx = workflow.context(self.root, request)

        def matching(ctx, images, config, new):
            with writable(ctx.colmap_dir / "database.db") as db:
                db.execute("INSERT INTO two_view_geometries(pair_id,rows,cols,data,config) VALUES(1,2,2,?,2)", (bytes(16),))

        def mapping(ctx, config, parent):
            model = ctx.colmap_dir / "sparse/0"
            model.mkdir(parents=True)
            (model / "images.bin").write_bytes(b"mock sparse")
            (ctx.colmap_dir / "best_model.txt").write_text("0")
            (ctx.colmap_dir / "sparse.ply").write_bytes(b"mock ply")
            stats = {"path": str(model), "registered_images": 2, "points": 8, "reprojection_error": 0.2, "track_length": 2}
            return {"components": [stats], "best": stats, "disconnected": False}

        def command(cmd, log):
            self.assertEqual(cmd[1], "model_converter")  # Never feature_extractor.
            out = Path(cmd[cmd.index("--output_path") + 1])
            names = [i["relative"] for a in workflow.read_json(ctx.out_dir / "assets.json") for i in a["images"]]
            (out / "images.txt").write_text("".join(f"{n} 1 0 0 0 0 0 0 1 {name}\n0 0 -1\n" for n, name in enumerate(names, 1)))

        def metrics(ctx, times):
            atomic_json(ctx.metrics_dir / "metrics.json", {"scene": "001"})

        with patch("pipeline.workflow.require_version"), \
             patch("pipeline.workflow.image_dimensions", return_value=(3840, 2160)), \
             patch("pipeline.workflow.probe_video", return_value={"streams": [{"duration": "1"}]}), \
             patch("pipeline.workflow.run_matching", side_effect=matching), \
             patch("pipeline.workflow.run_mapping", side_effect=mapping), \
             patch("pipeline.workflow.run_cmd", side_effect=command) as cmd, \
             patch("pipeline.workflow.run_metrics", side_effect=metrics):
            for phase in slurm.phases(request):
                workflow.execute_phase(self.root, request, phase)
            self.assertEqual(cmd.call_count, 1)
            workflow.execute_phase(self.root, request, "features")
            self.assertEqual(cmd.call_count, 1)
        state = self.root / "datasets/_state/001/latest.json"
        self.assertEqual(workflow.read_json(state)["run"], "outputs/001/trial")
        self.assertEqual(workflow.read_json(ctx.out_dir / "run.json")["status"], "complete")

    def test_partial_result_does_not_replace_complete_latest(self):
        from pipeline import workflow
        self.seed_parent()
        (self.root / "configs/default.yaml").write_text((REPO_ROOT / "configs/default.yaml").read_text())
        request = self.plan()
        request.update(id="partial", experiment="partial", run="outputs/001/partial")
        ctx = workflow.context(self.root, request)
        ctx.prepare()
        ctx.colmap_dir.mkdir(parents=True)
        fixture_db(ctx.colmap_dir / "database.db")
        atomic_json(ctx.out_dir / "quality.json", {"status": "partial", "excluded": ["missing-video"]})
        def metrics(ctx, times):
            atomic_json(ctx.metrics_dir / "metrics.json", {})
        with patch("pipeline.workflow.run_metrics", side_effect=metrics):
            workflow.publish(self.root, request, ctx)
        self.assertEqual(workflow.read_json(self.root / "datasets/_state/001/latest.json")["run"], "outputs/001/old")
        self.assertEqual(workflow.read_json(self.root / "datasets/_state/001/last_result.json")["status"], "partial")


class TelemetryReferenceTests(unittest.TestCase):
    def test_interpolation_has_time_gap_and_no_extrapolation(self):
        from pipeline.srt_positions import position
        rows = [{"t": 0., "latitude": -12., "longitude": -77., "abs_alt": 100.},
                {"t": 1., "latitude": -12.001, "longitude": -77.001, "abs_alt": 102.}]
        for actual, expected in zip(position(rows, 0.5, 1), (-12.0005, -77.0005, 101.)):
            self.assertAlmostEqual(actual, expected)
        self.assertIsNone(position(rows, 2, 1))
        self.assertIsNone(position(rows, 0.5, 0.1))

    def test_relative_altitude_is_not_treated_as_absolute(self):
        from pipeline.srt_positions import references
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.MP4"
            video.touch()
            rows = [{"timestamp": "00:00:00.000", "latitude": -12., "longitude": -77., "rel_alt": 30}]
            with patch("pipeline.srt_positions.parse_srt", return_value=rows):
                video.with_suffix(".SRT").touch()
                with self.assertRaises(ConfigError):
                    references([{"kind": "video", "path": str(video),
                                 "images": [{"relative": "a.jpg", "time_s": 0}]}],
                               Path(tmp) / "refs.csv", {"min_images": 1})


if __name__ == "__main__":
    unittest.main()
