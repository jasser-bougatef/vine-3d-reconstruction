#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  main_rt_controller.py  —  Orchestrateur temps réel robot de scouting   ║
║                                                                          ║
║  DEUX MODES PRINCIPAUX :                                                 ║
║  ┌──────────────────────────────────────────────────────────────────┐    ║
║  │  --mode manual  Mode interactif clavier                          │    ║
║  │     S     = démarrer / arrêter l'enregistrement du pied courant  │    ║
║  │     N/→   = pied suivant     P/←   = pied précédent             │    ║
║  │     B     = bascule Passe 1 / Passe 2                            │    ║
║  │     R     = lancer la reconstruction 3D du pied sélectionné      │    ║
║  │     Q/Esc = quitter                                              │    ║
║  ├──────────────────────────────────────────────────────────────────┤    ║
║  │  --mode real    Vrai GPS U-blox + vraie caméra RealSense         │    ║
║  │  --mode sim     GPS simulé (bureau, sans matériel)               │    ║
║  │                 (Déclenchements autonomes par Géofencing GPS)    │    ║
║  └──────────────────────────────────────────────────────────────────┘    ║
║                                                                          ║
║  Config : pipeline_config.json (lu au démarrage)                        ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import math
import os
import queue
import random
import subprocess
import sys
import termios
import threading
import time
import tty
from datetime import datetime
from pathlib import Path

import numpy as np
from pyproj import Transformer

# ══════════════════════════════════════════════════════════════════════════════
#  CHARGEMENT DE LA CONFIGURATION CENTRALE
# ══════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH  = PROJECT_ROOT / "pipeline_config.json"

def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[WARN] pipeline_config.json introuvable → paramètres par défaut")
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

CFG = _load_config()

def _p(section: str, key: str, default):
    """Lit CFG[section][key] avec fallback sur default."""
    return CFG.get(section, {}).get(key, default)

# ── Chemins ──────────────────────────────────────────────────────────────────
BUILD_DIR     = PROJECT_ROOT / _p("paths", "build_dir",         "build")
EXTRACTOR_BIN = PROJECT_ROOT / _p("paths", "extractor_binary",  "build/auto_extractor")
BAG_INPUT_DIR = PROJECT_ROOT / _p("paths", "bag_input_dir",     "realsense_data")
EXTRACT_OUT   = PROJECT_ROOT / _p("paths", "extraction_output", "Extraction_Output")
FINAL_OUT     = PROJECT_ROOT / _p("paths", "final_output",      "final_Output")
SHP_PATH      = PROJECT_ROOT / _p("paths", "shapefile",         "pos_pied.shp")
LOGS_DIR      = PROJECT_ROOT / _p("paths", "logs_dir",          "logs")
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BAG_INPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Paramètres caméra ────────────────────────────────────────────────────────
CAM = CFG.get("camera", {})
CAM_DEPTH_W     = CAM.get("depth_width",    640)
CAM_DEPTH_H     = CAM.get("depth_height",   480)
CAM_COLOR_W     = CAM.get("color_width",    640)
CAM_COLOR_H     = CAM.get("color_height",   480)
CAM_FPS         = CAM.get("fps",            30)
CAM_EXT         = CAM.get("bag_extension",  ".db3")
CAM_START_DELAY = CAM.get("startup_delay_s", 1.2)
CAM_STOP_DELAY  = CAM.get("stop_delay_s",    0.5)

# ── Paramètres C++ ───────────────────────────────────────────────────────────
_cpp = CFG.get("extractor_cpp", {})
DEFAULT_CPP = dict(
    z_min  = _cpp.get("z_min",           0.1),
    z_max  = _cpp.get("z_max",           0.5),
    rot_x  = _cpp.get("rot_x_deg",       0.0),
    rot_y  = _cpp.get("rot_y_deg_pass1", 0.0),
    rot_z  = _cpp.get("rot_z_deg",       270.0),
)
ROT_Y_PASS2 = _cpp.get("rot_y_deg_pass2", 180.0)

# ── Paramètres pipeline 3D ───────────────────────────────────────────────────
_f3d = CFG.get("pipeline_3d", {}).get("filter", {})
_r3d = CFG.get("pipeline_3d", {}).get("registration", {})
DEFAULT_3D = dict(
    min_distance       = _f3d.get("min_distance",       0.4),
    max_distance       = _f3d.get("max_distance",       0.5),
    filter_mode        = _f3d.get("filter_mode",        "z"),
    rotate_output      = _f3d.get("rotate_output",      True),
    voxel_size         = _f3d.get("voxel_size",         0.005),
    use_statistical    = _f3d.get("use_statistical",    False),
    use_radius_filter  = _f3d.get("use_radius_filter",  True),
    eps                = _f3d.get("eps",                0.12),
    min_points         = _f3d.get("min_points",         10),
    min_cluster_size   = _f3d.get("min_cluster_size",   500),
    nb_neighbors       = _f3d.get("nb_neighbors",       10),
    std_ratio          = _f3d.get("std_ratio",          1.5),
    radius             = _f3d.get("radius",             0.1),
    min_radius_points  = _f3d.get("min_radius_points",  10),
    voxel_size_reg     = _r3d.get("voxel_size_reg",     0.02),
    window_size        = _r3d.get("window_size",        4),
    voxel_size_merge   = _r3d.get("voxel_size_merge",   0.005),
    sliding_voxel_size = _r3d.get("sliding_voxel_size", 0.01),
)

# ── Paramètres GPS ───────────────────────────────────────────────────────────
_gps = CFG.get("gps", {})
GPS_PORT   = _gps.get("port",              "/dev/ttyACM0")
GPS_BAUD   = _gps.get("baud_rate",         115200)
GPS_RADIUS = _gps.get("detection_radius_m", 0.35)

# ── Paramètres modes ─────────────────────────────────────────────────────────
_manual      = CFG.get("modes", {}).get("manual", {})
_auto        = CFG.get("modes", {}).get("auto",   {})
_simply      = CFG.get("modes", {}).get("synthetic_ply", {})
FRAME_HZ     = _manual.get("frame_sample_hz",   10)
LOG_HISTORY  = _manual.get("log_history_size",  100)
LOG_DISPLAY  = _manual.get("log_display_lines",  7)
SIM_STEP     = _auto.get("sim_step_m",           0.12)
SIM_INTERVAL = _auto.get("sim_interval_s",       0.10)
SIM_PAUSE_T  = _auto.get("sim_pause_turnaround_s", 2.5)
SIM_PAUSE_R  = _auto.get("sim_pause_restart_s",  3.0)
SIM_NOISE    = _auto.get("sim_gps_noise_sigma_m", 0.03)
SIM_PLY_NFRA = _simply.get("n_frames",  20)
SIM_PLY_NPTS = _simply.get("n_points", 6000)

# ── Paramètres réseau ────────────────────────────────────────────────────────
_net      = CFG.get("network", {})
WS_PORT   = _net.get("websocket_port", 8765)
HTTP_PORT = _net.get("http_port",      8080)


# ══════════════════════════════════════════════════════════════════════════════
#  LOGGER (avec système de Dashboard anti-chevauchement)
# ══════════════════════════════════════════════════════════════════════════════

class Logger:
    _lock = threading.Lock()
    history = []
    update_counter = 0
    ui_mode = False

    @staticmethod
    def _ts():
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    @staticmethod
    def _add(msg):
        with Logger._lock:
            Logger.history.append(msg)
            if len(Logger.history) > LOG_HISTORY:
                Logger.history.pop(0)
            Logger.update_counter += 1
            if not Logger.ui_mode:
                sys.stdout.write(msg + "\n")
                sys.stdout.flush()

    @staticmethod
    def info(msg):   Logger._add(f"\033[96m[{Logger._ts()}] INFO  {msg}\033[0m")
    @staticmethod
    def ok(msg):     Logger._add(f"\033[92m[{Logger._ts()}] ✔     {msg}\033[0m")
    @staticmethod
    def warn(msg):   Logger._add(f"\033[93m[{Logger._ts()}] ⚠     {msg}\033[0m")
    @staticmethod
    def err(msg):    Logger._add(f"\033[91m[{Logger._ts()}] ✘     {msg}\033[0m")
    @staticmethod
    def state(p, s): Logger._add(f"\033[95m[{Logger._ts()}] STATE [{p}] → {s}\033[0m")
    @staticmethod
    def manual(msg): Logger._add(f"\033[33m[{Logger._ts()}] MANUAL {msg}\033[0m")
    @staticmethod
    def sim(msg):    Logger._add(f"\033[33m[{Logger._ts()}] SIM   {msg}\033[0m")

log = Logger()


# ══════════════════════════════════════════════════════════════════════════════
#  SHAPEFILE
# ══════════════════════════════════════════════════════════════════════════════

def load_shapefile(shp_path: Path) -> list:
    try:
        import shapefile
    except ImportError:
        log.err("pyshp requis : pip install pyshp")
        sys.exit(1)
    sf = shapefile.Reader(str(shp_path))
    points = [shape.points[0] for shape in sf.shapes()]
    log.ok(f"{len(points)} pieds chargés depuis {shp_path.name}")
    return points


# ══════════════════════════════════════════════════════════════════════════════
#  GÉNÉRATEUR DE PLY SYNTHÉTIQUES
# ══════════════════════════════════════════════════════════════════════════════

def generate_synthetic_ply(out_dir: Path, n_frames: int = SIM_PLY_NFRA,
                            n_points: int = SIM_PLY_NPTS, date_label: str = None):
    try:
        import open3d as o3d
    except ImportError:
        log.warn("open3d non disponible — PLY synthétiques impossibles")
        return None
    date_label = date_label or datetime.now().strftime("%d-%m-%Y")
    ply_dir = out_dir / f"data_{date_label}" / "PointCloud_ply"
    ply_dir.mkdir(parents=True, exist_ok=True)
    log.sim(f"Génération {n_frames} PLY synthétiques → {ply_dir}")
    rng = np.random.default_rng(int(time.time()))
    frame_offset = _simply.get("frame_offset_m", 0.025)
    for i in range(n_frames):
        theta  = rng.uniform(0, 2 * np.pi, n_points // 2)
        height = rng.uniform(0.0, _simply.get("trunk_height_m", 1.2), n_points // 2)
        r_n    = rng.normal(_simply.get("trunk_radius_m", 0.08), 0.015, n_points // 2)
        tx = r_n * np.cos(theta); ty = r_n * np.sin(theta); tz = height
        tc = np.tile([0.55, 0.30, 0.10], (n_points // 2, 1))
        spread = _simply.get("foliage_spread_m", 0.22)
        lx = rng.normal(0, spread, n_points // 2)
        ly = rng.normal(0, spread * 0.82, n_points // 2)
        lz = rng.uniform(0.4, 1.5, n_points // 2)
        lc = np.tile([0.15, 0.55, 0.10], (n_points // 2, 1))
        pts  = np.vstack([np.c_[tx, ty, tz], np.c_[lx, ly, lz]])
        pts[:, 1] += i * frame_offset
        cols = np.vstack([tc, lc])
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pts)
        pcd.colors = o3d.utility.Vector3dVector(cols)
        o3d.io.write_point_cloud(str(ply_dir / f"frame_{i+1:04d}.ply"), pcd)
    log.sim(f"PLY synthétiques OK ({n_frames} frames)")
    return ply_dir


# ══════════════════════════════════════════════════════════════════════════════
#  LECTEUR DE TOUCHE (non-bloquant, Unix)
# ══════════════════════════════════════════════════════════════════════════════

class KeyReader:
    def __init__(self, key_queue: queue.Queue):
        self.q = key_queue
        self.running = True
        self._old_settings = None

    def _set_cbreak(self):
        if not sys.stdin.isatty(): return
        self._old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())

    def _restore(self):
        if self._old_settings and sys.stdin.isatty():
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)

    def run_in_thread(self):
        t = threading.Thread(target=self._read_loop, daemon=True, name="key-reader")
        t.start()
        return t

    def _read_loop(self):
        self._set_cbreak()
        try:
            while self.running:
                import select
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    ch = sys.stdin.read(1)
                    if ch == '\x1b':
                        r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                        if r2:
                            ch2 = sys.stdin.read(1)
                            r3, _, _ = select.select([sys.stdin], [], [], 0.05)
                            if r3:
                                ch3 = sys.stdin.read(1)
                                if ch2 == '[' and ch3 == 'C':   self.q.put('RIGHT')
                                elif ch2 == '[' and ch3 == 'D': self.q.put('LEFT')
                                else: self.q.put('ESC')
                            else: self.q.put('ESC')
                        else: self.q.put('ESC')
                    else:
                        self.q.put(ch)
        finally:
            self._restore()

    def stop(self):
        self.running = False
        self._restore()


# ══════════════════════════════════════════════════════════════════════════════
#  GESTIONNAIRES DE CAMÉRA
# ══════════════════════════════════════════════════════════════════════════════

def _rs_streams(config):
    """Configure les streams RealSense depuis la config JSON."""
    try:
        import pyrealsense2 as rs
        config.enable_stream(rs.stream.depth,
                             CAM_DEPTH_W, CAM_DEPTH_H, rs.format.z16,  CAM_FPS)
        config.enable_stream(rs.stream.color,
                             CAM_COLOR_W, CAM_COLOR_H, rs.format.rgb8, CAM_FPS)
    except Exception as e:
        log.warn(f"Erreur config streams RealSense : {e}")


class ContinuousRecorder(threading.Thread):
    """Enregistrement continu (mode lecture d'un .bag existant ou simulation)."""
    def __init__(self, bag_path, sim_mode=False):
        super().__init__(daemon=True)
        self.sim_mode = sim_mode
        self.running  = True
        self.frame_number = 0
        self._lock    = threading.Lock()
        self.bag_path = bag_path

    @property
    def current(self):
        with self._lock: return self.frame_number

    def run(self):
        if self.sim_mode:
            while self.running:
                time.sleep(1.0 / CAM_FPS)
                with self._lock: self.frame_number += 1
            return
        try:
            import pyrealsense2 as rs
            pipeline = rs.pipeline()
            config   = rs.config()
            _rs_streams(config)
            Path(self.bag_path).parent.mkdir(parents=True, exist_ok=True)
            config.enable_record_to_file(str(self.bag_path))
            log.info(f"Enregistrement continu → {self.bag_path}")
            pipeline.start(config)
            while self.running:
                pipeline.wait_for_frames()
                with self._lock: self.frame_number += 1
        except Exception as e:
            log.err(f"RealSense error : {e}")
        finally:
            try: pipeline.stop()
            except: pass

    def stop(self): self.running = False


class PerPassRecorder:
    """
    Enregistrement par passe (un fichier .db3 par passage).
    start_record() ouvre un nouveau fichier, stop_record() le ferme.
    """
    def __init__(self, sim_mode=False):
        self.sim_mode = sim_mode
        self.frame_number = 0
        self.running = False
        self._lock   = threading.Lock()
        self.thread  = None

    def start_record(self, bag_path):
        self.running = True
        self.frame_number = 0
        self.thread = threading.Thread(target=self._run, args=(bag_path,), daemon=True)
        self.thread.start()
        if not self.sim_mode:
            time.sleep(CAM_START_DELAY)

    def stop_record(self):
        self.running = False
        if self.thread:
            self.thread.join()
            time.sleep(CAM_STOP_DELAY)

    def _run(self, bag_path):
        if self.sim_mode:
            while self.running:
                time.sleep(1.0 / CAM_FPS)
                with self._lock: self.frame_number += 1
            return
        try:
            import pyrealsense2 as rs
            pipeline = rs.pipeline()
            config   = rs.config()
            _rs_streams(config)
            Path(bag_path).parent.mkdir(parents=True, exist_ok=True)
            config.enable_record_to_file(str(bag_path))
            pipeline.start(config)
            while self.running:
                pipeline.wait_for_frames()
                with self._lock: self.frame_number += 1
        except Exception as e:
            log.err(f"Erreur enregistrement : {e}")
        finally:
            try: pipeline.stop()
            except: pass

    @property
    def current(self):
        with self._lock: return self.frame_number


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER DE RECONSTRUCTION 3D
# ══════════════════════════════════════════════════════════════════════════════

class ReconstructionWorker(threading.Thread):
    def __init__(self, job_queue, streamer_cb, ubx_file,
                 rot_y_pass2=ROT_Y_PASS2, sim_ply=False):
        super().__init__(daemon=True)
        self.q           = job_queue
        self.streamer_cb = streamer_cb
        self.ubx_file    = ubx_file
        self.rot_y_pass2 = rot_y_pass2
        self.sim_ply     = sim_ply
        self.running     = True

    def run(self):
        while self.running:
            try:
                job = self.q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._process(job)
            except Exception as e:
                log.err(f"Reconstruction [{job['pied_id']}] : {e}")
                import traceback; traceback.print_exc()
            finally:
                self.q.task_done()

    def _process(self, job):
        pied_id      = job["pied_id"]
        pass1_bag    = job.get("pass1_bag", "")
        pass2_bag    = job.get("pass2_bag", "")
        pass1_frames = job["pass1_frames"]
        pass2_frames = job["pass2_frames"]
        date_label   = job["date"]

        log.info(f"[{pied_id}] Reconstruction "
                 f"({len(pass1_frames)} fr P1, {len(pass2_frames)} fr P2)")

        job_name    = f"{pied_id}_pass1_and_pass2_{date_label}"
        extract_job = EXTRACT_OUT / job_name
        final_job   = FINAL_OUT   / job_name
        session     = f"data_{date_label}"
        merged_dir  = extract_job / "merged_ply"
        merged_dir.mkdir(parents=True, exist_ok=True)

        if self.sim_ply:
            p1 = generate_synthetic_ply(extract_job / "pass1",
                                        max(5, len(pass1_frames)), date_label)
            p2 = generate_synthetic_ply(extract_job / "pass2",
                                        max(5, len(pass2_frames)), date_label)
            if not p1 or not p2:
                log.err(f"[{pied_id}] PLY synthétiques échoués")
                return
            self._merge_ply_dirs(p1, p2, merged_dir)
        else:
            p1_out = extract_job / "pass1"
            p2_out = extract_job / "pass2"
            ok1 = self._run_extractor(
                p1_out, pass1_frames[0][0], pass1_frames[-1][0],
                DEFAULT_CPP["rot_y"], pass1_bag)
            ok2 = self._run_extractor(
                p2_out, pass2_frames[0][0], pass2_frames[-1][0],
                self.rot_y_pass2, pass2_bag)
            if not ok1 or not ok2:
                log.err(f"[{pied_id}] Extraction C++ échouée")
                return
            self._merge_ply_dirs(
                p1_out / session / "PointCloud_ply",
                p2_out / session / "PointCloud_ply",
                merged_dir)

        final_session = final_job / session
        final_session.mkdir(parents=True, exist_ok=True)
        ply_final = self._run_3d(merged_dir, final_session, pied_id)

        if ply_final is None:
            log.err(f"[{pied_id}] Pipeline 3D échoué")
            return

        log.ok(f"[{pied_id}] PLY → {ply_final.name}")

        if self.streamer_cb:
            self.streamer_cb(pied_id, ply_final, date_label)

    def _run_extractor(self, out_dir, start_frame, end_frame, rot_y, bag_file):
        if not EXTRACTOR_BIN.exists():
            log.err(f"Binaire absent : {EXTRACTOR_BIN}")
            return False
        if not bag_file or not Path(bag_file).exists():
            log.err(f"Fichier bag introuvable : {bag_file}")
            return False
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        ubx_arg = self.ubx_file
        if not ubx_arg or not Path(ubx_arg).exists():
            dummy = out_dir / "dummy.ubx"
            dummy.touch()
            ubx_arg = str(dummy)

        cmd = [
            str(EXTRACTOR_BIN),
            "--bag",   str(bag_file),
            "--ubx",   ubx_arg,
            "--out",   str(out_dir),
            "--start", str(start_frame),
            "--end",   str(end_frame),
            "--z-min", str(DEFAULT_CPP["z_min"]),
            "--z-max", str(DEFAULT_CPP["z_max"]),
            "--rot-x", str(DEFAULT_CPP["rot_x"]),
            "--rot-y", str(rot_y),
            "--rot-z", str(DEFAULT_CPP["rot_z"]),
        ]
        log.info(f"Extractor : frames {start_frame}→{end_frame}  rot_y={rot_y}")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log.err(f"C++ error : {r.stderr.strip()[:120]}")
            return False
        return True

    def _merge_ply_dirs(self, p1_ply, p2_ply, out):
        import shutil
        out.mkdir(parents=True, exist_ok=True)
        idx = 0
        for src, prefix in [(p1_ply, "p1"), (p2_ply, "p2")]:
            if not src or not Path(src).exists():
                log.warn(f"PLY dir absent : {src}")
                continue
            for ply in sorted(Path(src).glob("*.ply")):
                shutil.copy2(ply, out / f"{prefix}_{idx:04d}.ply")
                idx += 1
        log.info(f"Fusion : {idx} PLY → {out}")

    def _run_3d(self, ply_input, output_dir, pied_id):
        spec = importlib.util.spec_from_file_location(
            "pipeline3d", str(PROJECT_ROOT / "all_pipeline_3D.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        ply_out   = output_dir / "PointCloud_ply"
        trans_out = output_dir / "Transformations"
        ply_out.mkdir(parents=True, exist_ok=True)
        trans_out.mkdir(parents=True, exist_ok=True)
        f3d_keys = [
            "min_distance","max_distance","filter_mode","rotate_output",
            "voxel_size","use_statistical","use_radius_filter",
            "eps","min_points","min_cluster_size",
            "nb_neighbors","std_ratio","radius","min_radius_points",
        ]
        filter_mod = mod.PointCloudFilter(**{k: DEFAULT_3D[k] for k in f3d_keys})
        reg_mod = mod.SlidingWindowRegistration(
            output_ply_dir     = str(ply_out),
            output_trans_dir   = str(trans_out),
            voxel_size_reg     = DEFAULT_3D["voxel_size_reg"],
            window_size        = DEFAULT_3D["window_size"],
            voxel_size_merge   = DEFAULT_3D["voxel_size_merge"],
            sliding_voxel_size = DEFAULT_3D["sliding_voxel_size"],
        )
        log.info(f"[{pied_id}] Filtrage 3D → {ply_input}")
        with contextlib.redirect_stdout(io.StringIO()):
            clouds = filter_mod.process_folder(str(ply_input))
        if not clouds:
            log.err(f"[{pied_id}] Aucun nuage valide après filtrage")
            return None
        log.info(f"[{pied_id}] Registration {len(clouds)} nuages")
        with contextlib.redirect_stdout(io.StringIO()):
            reg_mod.run_registration(clouds)
        matches = list(ply_out.glob("Pied*.ply")) + list(ply_out.glob("Pied(s)*.ply"))
        return matches[0] if matches else None

    def stop(self): self.running = False


# ══════════════════════════════════════════════════════════════════════════════
#  MODE MANUEL — INTERFACE CLAVIER (DASHBOARD)
# ══════════════════════════════════════════════════════════════════════════════

class ManualController:
    IDLE       = "IDLE"
    RECORDING  = "RECORDING"
    DONE_P1    = "DONE_P1"
    DONE_BOTH  = "DONE_BOTH"
    PROCESSING = "PROCESSING"

    def __init__(self, args):
        self.args       = args
        self.sim_ply    = getattr(args, "sim_ply", False)
        self.date_label = datetime.now().strftime("%d-%m-%Y")

        self.current_pied_idx = 0
        self.current_pass     = 1
        self.recording        = False

        self.pied_status = {}
        self.job_queue   = queue.Queue()
        self.worker      = None
        self.key_queue   = queue.Queue()
        self.key_reader  = KeyReader(self.key_queue)
        self.streamer    = None

        # Décider du mode d'enregistrement
        in_bag = getattr(args, "in_bag", "")
        if in_bag and Path(in_bag).exists():
            self.record_new       = False
            self.global_bag       = in_bag
            self.global_counter   = ContinuousRecorder(bag_path="", sim_mode=True)
            self.per_pass_recorder= None
        else:
            self.record_new       = True
            self.global_bag       = ""
            if self.sim_ply:
                self.global_counter    = ContinuousRecorder(bag_path="", sim_mode=True)
                self.per_pass_recorder = None
            else:
                self.global_counter    = None
                self.per_pass_recorder = PerPassRecorder(sim_mode=False)

        ubx_files = sorted(BAG_INPUT_DIR.glob("*.ubx"),
                           key=lambda p: p.stat().st_mtime)
        self.ubx_file = str(ubx_files[-1]) if ubx_files else ""
        self.running  = True
        self.pieds    = []
        self.n_pieds  = 0
        self.recorded = {}

    def _setup(self):
        Logger.ui_mode = True
        sys.stdout.write("\033[2J\033[H")
        self.pieds   = load_shapefile(SHP_PATH)
        self.n_pieds = len(self.pieds)
        self.recorded = {
            i: {1: {"bag": "", "frames": []},
                2: {"bag": "", "frames": []}}
            for i in range(self.n_pieds)
        }
        for i in range(self.n_pieds):
            self.pied_status[i] = self.IDLE

        log.info(f"Config chargée : {CONFIG_PATH.name}")
        if self.global_bag:  log.info(f"Lecture bag   : {self.global_bag}")
        if self.ubx_file:    log.info(f"UBX GPS       : {self.ubx_file}")
        if self.sim_ply:     log.info("PLY synthétiques activés (--sim-ply)")
        if not self.sim_ply and not EXTRACTOR_BIN.exists():
            log.warn(f"Binaire absent : {EXTRACTOR_BIN} — compilez d'abord")

        if self.global_counter:
            self.global_counter.start()

        rot_y_p2 = getattr(self.args, "rot_y_pass2", ROT_Y_PASS2)
        self.worker = ReconstructionWorker(
            job_queue    = self.job_queue,
            streamer_cb  = self._on_ply_ready,
            ubx_file     = self.ubx_file,
            rot_y_pass2  = rot_y_p2,
            sim_ply      = self.sim_ply,
        )
        self.worker.start()

        # Chargement du streamer WebSocket
        streamer_path = PROJECT_ROOT / "rt_streamer.py"
        if streamer_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "rt_streamer", str(streamer_path))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self.streamer = mod
                log.ok("rt_streamer.py chargé")
            except Exception as e:
                log.warn(f"rt_streamer non chargé : {e}")

        log.ok("Initialisation terminée — appuyez sur S pour commencer")

    def _on_ply_ready(self, pied_id, ply_path, date_label):
        idx = int(pied_id.replace("pied", "")) - 1
        self.pied_status[idx] = self.DONE_BOTH
        log.ok(f"[{pied_id}] Reconstruction terminée ✔")
        if self.streamer:
            try:
                asyncio.run(self.streamer.send_ply_async(pied_id, ply_path, date_label))
            except Exception: pass

    def _pied_id(self, idx=None):
        if idx is None: idx = self.current_pied_idx
        return f"pied{idx + 1:02d}"

    def _toggle_recording(self):
        idx = self.current_pied_idx
        pas = self.current_pass
        pid = self._pied_id()

        if not self.recording:
            self.recording = True
            self.recorded[idx][pas]["frames"] = []

            if self.per_pass_recorder:
                ext      = CAM_EXT
                bag_name = str(BAG_INPUT_DIR /
                               f"{pid}_P{pas}_{self.date_label}_{int(time.time())}{ext}")
                self.recorded[idx][pas]["bag"] = bag_name
                log.manual(f"[{pid}] Préparation caméra... patientez.")
                self._render_ui()
                self.per_pass_recorder.start_record(bag_name)
            else:
                self.recorded[idx][pas]["bag"] = self.global_bag

            log.manual(f"[{pid}] ▶ ENREGISTREMENT P{pas} DÉMARRÉ  (S pour arrêter)")
        else:
            if self.per_pass_recorder:
                self.per_pass_recorder.stop_record()

            frames = self.recorded[idx][pas]["frames"]
            self.recording = False
            log.manual(f"[{pid}] ⏹ P{pas} ARRÊTÉ — {len(frames)} frames")

            if pas == 1:
                self.pied_status[idx] = self.DONE_P1
                log.ok(f"[{pid}] Passe 1 OK — passez à la Passe 2 (touche B)")
            elif pas == 2:
                if self.recorded[idx][1]["frames"]:
                    self.pied_status[idx] = self.DONE_BOTH
                    log.ok(f"[{pid}] Passe 2 OK — appuyez R pour reconstruire")
                else:
                    log.warn(f"[{pid}] Passe 2 OK, Passe 1 manquante → pas de reconstruction")
        self._render_ui()

    def _record_frame(self):
        idx = self.current_pied_idx
        pas = self.current_pass
        f   = (self.per_pass_recorder.current if self.per_pass_recorder
               else self.global_counter.current)
        self.recorded[idx][pas]["frames"].append((f, time.time()))

    def _launch_reconstruction(self, idx):
        pid = self._pied_id(idx)
        p1  = self.recorded[idx][1]
        p2  = self.recorded[idx][2]
        if not p1["frames"] or not p2["frames"]:
            log.warn(f"[{pid}] P1 et P2 requis")
            return
        self.pied_status[idx] = self.PROCESSING
        self.job_queue.put({
            "pied_id":      pid,
            "pass1_bag":    p1["bag"],
            "pass1_frames": p1["frames"],
            "pass2_bag":    p2["bag"],
            "pass2_frames": p2["frames"],
            "date":         self.date_label,
        })
        log.ok(f"[{pid}] Reconstruction lancée en arrière-plan")
        self._render_ui()

    def _render_ui(self,
                   title_mode="Mode MANUEL",
                   hints="S=Enreg/Arrêt  N/→=Pied suiv.  P/←=Pied préc.  B=P1↔P2  R=Reconstruire  Q=Quitter"):
        C = {
            "R": "\033[0m", "B": "\033[1m",
            "blue": "\033[94m", "green": "\033[92m",
            "yellow": "\033[93m", "red": "\033[91m",
            "purple": "\033[95m", "cyan": "\033[96m",
            "dim": "\033[2m",
        }
        STATUS_ICON = {
            self.IDLE:       f"{C['dim']}⚫ IDLE{C['R']}",
            self.DONE_P1:    f"{C['yellow']}🟡 P1 OK{C['R']}",
            self.DONE_BOTH:  f"{C['green']}✅ TERMINÉ{C['R']}",
            self.PROCESSING: f"{C['purple']}⚙ RECONST.{C['R']}",
        }
        idx   = self.current_pied_idx
        pas   = self.current_pass
        pid   = self._pied_id()
        p1_n  = len(self.recorded[idx][1]["frames"]) if self.recorded else 0
        p2_n  = len(self.recorded[idx][2]["frames"]) if self.recorded else 0
        frame = (self.per_pass_recorder.current if self.per_pass_recorder
                 else (self.global_counter.current if self.global_counter else 0))

        if self.recording:
            cur_n   = len(self.recorded[idx][pas]["frames"])
            rec_str = f"{C['red']}● REC P{pas}  {cur_n} frames{C['R']}"
        else:
            rec_str = STATUS_ICON.get(self.pied_status.get(idx, self.IDLE), "")

        L = ["\033[H"]
        SEP1 = "═" * 75
        SEP2 = "─" * 75
        L.append(f"{C['B']}{SEP1}{C['R']}")
        L.append(f"  {C['blue']}🍇 IMS Bordeaux — Pipeline Vigne{C['R']}  "
                 f"{C['B']}{title_mode}{C['R']}  {C['dim']}{self.date_label}{C['R']}")
        L.append(SEP2)
        L.append(f"  Pied : {C['cyan']}{C['B']}{pid}{C['R']} ({idx+1}/{self.n_pieds})"
                 f"  Passe : P{pas}  {rec_str}")
        L.append(f"  P1:{p1_n} fr  P2:{p2_n} fr  Frame cam:{frame}"
                 f"  Jobs:{self.job_queue.qsize()}")
        L.append(SEP2)
        L.append("  Rang :")

        # Affichage en rangées de 10
        row_cells = []
        for p_idx in range(self.n_pieds):
            p_id = f"P{p_idx+1:02d}"
            if p_idx == idx:
                c = C["red"] if self.recording else C["cyan"]
                cell = f"{c}{C['B']}[{p_id}]{C['R']}"
            else:
                st = self.pied_status.get(p_idx, self.IDLE)
                if   st == self.DONE_BOTH:  cell = f"{C['green']}{p_id}{C['R']}"
                elif st == self.DONE_P1:    cell = f"{C['yellow']}{p_id}{C['R']}"
                elif st == self.PROCESSING: cell = f"{C['purple']}{p_id}{C['R']}"
                else:                       cell = f"{C['dim']}{p_id}{C['R']}"
            row_cells.append(cell)

        for i in range(0, len(row_cells), 10):
            L.append("  " + " ─ ".join(row_cells[i:i+10]))

        L.append(SEP2)
        L.append(f"  {C['dim']}⚫ IDLE{C['R']}  {C['yellow']}🟡 P1 ok{C['R']}"
                 f"  {C['green']}✅ Terminé{C['R']}  {C['purple']}⚙ Reconst.{C['R']}"
                 f"  {C['red']}● Enreg.{C['R']}  {C['cyan']}[ ] Sélect.{C['R']}")
        L.append(SEP2)
        L.append(f"  {C['B']}Derniers événements :{C['R']}")

        with Logger._lock:
            for msg in Logger.history[-LOG_DISPLAY:]:
                L.append(f"  {msg}")
            for _ in range(LOG_DISPLAY - len(Logger.history[-LOG_DISPLAY:])):
                L.append("")

        L.append(f"{C['B']}{SEP1}{C['R']}")
        L.append(f"  {hints}")
        L.append("\033[J")
        sys.stdout.write("\r\n".join(L) + "\r\n")
        sys.stdout.flush()

    def _handle_key(self, key: str) -> bool:
        k = key.lower()
        if k in ("s", "\r", "\n"):
            self._toggle_recording()
        elif k in ("n", "right"):
            if self.recording: return True
            self.current_pied_idx = (self.current_pied_idx + 1) % self.n_pieds
            self.current_pass = 1
            self._render_ui()
        elif k in ("p", "left"):
            if self.recording: return True
            self.current_pied_idx = (self.current_pied_idx - 1) % self.n_pieds
            self.current_pass = 1
            self._render_ui()
        elif k == "b":
            if self.recording: return True
            self.current_pass = 2 if self.current_pass == 1 else 1
            self._render_ui()
        elif k == "a":
            if not self.recording:
                self.current_pass = 1
                self._render_ui()
        elif k == "r":
            if self.recording: return True
            self._launch_reconstruction(self.current_pied_idx)
        elif k in ("q", "esc", "\x1b", "\x03"):
            return False
        else:
            self._render_ui()
        return True

    def run(self):
        self._setup()
        self._render_ui()
        self.key_reader.run_in_thread()

        last_frame_t      = 0.0
        last_log_counter  = -1
        last_render_t     = 0.0

        try:
            while self.running:
                now = time.time()
                if self.recording and (now - last_frame_t >= 1.0 / FRAME_HZ):
                    last_frame_t = now
                    self._record_frame()
                if (Logger.update_counter != last_log_counter
                        or now - last_render_t > 0.5):
                    self._render_ui()
                    last_log_counter = Logger.update_counter
                    last_render_t    = now
                try:
                    if not self._handle_key(self.key_queue.get_nowait()): break
                except queue.Empty: pass
                time.sleep(0.02)
        except KeyboardInterrupt: pass
        finally:
            self.running = False
            self.key_reader.stop()
            Logger.ui_mode = False
            if self.per_pass_recorder: self.per_pass_recorder.stop_record()
            if self.global_counter:    self.global_counter.stop()
            pending = self.job_queue.qsize()
            if pending:
                log.info(f"Attente de {pending} job(s)...")
                self.job_queue.join()
            if self.worker: self.worker.stop()
            print("\n\n✔ Mode manuel terminé.\n")


# ══════════════════════════════════════════════════════════════════════════════
#  LECTEUR GPS (modes sim et real)
# ══════════════════════════════════════════════════════════════════════════════

class GPSReader(threading.Thread):
    def __init__(self, port, baud, gps_queue, sim_mode=False,
                 sim_speed=1.0, pieds_l93=None):
        super().__init__(daemon=True)
        self.port=port; self.baud=baud; self.q=gps_queue
        self.sim_mode=sim_mode; self.sim_speed=sim_speed
        self.pieds_l93=pieds_l93 or []; self.running=True; self.ser=None
        self._tr=Transformer.from_crs("EPSG:4326","EPSG:2154",always_xy=True)

    def _simulate(self):
        if self.pieds_l93:
            ys=sorted(p[1] for p in self.pieds_l93)
            y_start=ys[0]-1.5; y_end=ys[-1]+1.5; x_base=self.pieds_l93[0][0]
        else:
            y_start,y_end,x_base=6280440.0,6280472.0,355000.0
        step=SIM_STEP*self.sim_speed; pause_at=0.0; tick=0
        y=y_start; direction=1; phase="ALLER"
        while self.running:
            now=time.time()
            if now<pause_at: time.sleep(SIM_INTERVAL); continue
            y+=direction*step; tick+=1
            if direction==1 and y>=y_end:
                y,direction,phase=y_end,-1,"RETOUR"; pause_at=now+SIM_PAUSE_T
            elif direction==-1 and y<=y_start:
                y,direction,phase=y_start,1,"ALLER"; pause_at=now+SIM_PAUSE_R
            self.q.put({"x_l93":x_base+random.gauss(0,SIM_NOISE),
                        "y_l93":y+random.gauss(0,SIM_NOISE),
                        "quality":1,"ts":now,"sim":True,"phase":phase,"tick":tick})
            time.sleep(SIM_INTERVAL)

    def _parse_gga(self, sentence):
        try:
            p=sentence.strip().split(",")
            if len(p)<7 or p[6] in ("0",""): return None
            rl,ld=float(p[2]),p[3]; rlo,lo=float(p[4]),p[5]; q=int(p[6])
            ld_=math.floor(rl/100); lat=ld_+(rl-ld_*100)/60
            lo_=math.floor(rlo/100); lon=lo_+(rlo-lo_*100)/60
            if ld=="S": lat=-lat
            if lo=="W": lon=-lon
            return lat,lon,q
        except Exception: return None

    def _run_real(self):
        try:
            import serial
        except ImportError:
            log.err("pyserial requis"); return
        try:
            self.ser=serial.Serial(self.port,self.baud,timeout=0.3)
        except Exception as e:
            log.err(f"Port {self.port} : {e}"); return
        buf=""; last_warn=0.0; fix=0
        log.info("En attente du fix GPS...")
        while self.running:
            try:
                raw=self.ser.read(256).decode("ascii",errors="ignore")
                if not raw: continue
                buf+=raw
                while "\n" in buf:
                    line,buf=buf.split("\n",1); line=line.strip()
                    if line.startswith(("$GPGGA","$GNGGA")):
                        parsed=self._parse_gga(line)
                        if parsed:
                            lat,lon,q=parsed; x,y=self._tr.transform(lon,lat)
                            fix+=1
                            if fix==1: log.ok(f"FIX GPS : X={x:.2f} Y={y:.2f} Q={q}")
                            self.q.put({"x_l93":x,"y_l93":y,"quality":q,
                                        "ts":time.time(),"sim":False})
                        else:
                            now=time.time()
                            if now-last_warn>3.0:
                                last_warn=now
                                log.warn("Pas de fix GPS — vue dégagée ?")
            except Exception as e:
                if self.running: log.warn(f"GPS error : {e}"); time.sleep(0.5)
        try:
            if self.ser: self.ser.close()
        except: pass

    def run(self):
        if self.sim_mode: self._simulate()
        else: self._run_real()

    def stop(self): self.running=False


# ══════════════════════════════════════════════════════════════════════════════
#  MODE AUTO — GÉOFENCING GPS (hérite de ManualController)
# ══════════════════════════════════════════════════════════════════════════════

class RobotController(ManualController):
    def __init__(self, args):
        super().__init__(args)
        self.radius    = getattr(args, "radius", GPS_RADIUS)
        self.sim_mode  = args.mode == "sim"
        self.y_max_seen= -math.inf
        self.gps_queue = queue.Queue(maxsize=500)

        # En mode auto la caméra est par passe
        self.per_pass_recorder = PerPassRecorder(sim_mode=self.sim_mode)
        self.global_counter    = None

    def _setup(self):
        Logger.ui_mode = True
        sys.stdout.write("\033[2J\033[H")
        self.pieds   = load_shapefile(SHP_PATH)
        self.n_pieds = len(self.pieds)
        self.recorded = {
            i: {1: {"bag": "", "frames": []},
                2: {"bag": "", "frames": []}}
            for i in range(self.n_pieds)
        }
        for i in range(self.n_pieds):
            self.pied_status[i] = self.IDLE

        log.info(f"Config chargée : {CONFIG_PATH.name}")
        if self.ubx_file: log.info(f"UBX GPS : {self.ubx_file}")
        if not self.sim_ply and not EXTRACTOR_BIN.exists():
            log.warn(f"Binaire absent : {EXTRACTOR_BIN}")

        rot_y_p2 = getattr(self.args, "rot_y_pass2", ROT_Y_PASS2)
        self.worker = ReconstructionWorker(
            job_queue    = self.job_queue,
            streamer_cb  = self._on_ply_ready,
            ubx_file     = self.ubx_file,
            rot_y_pass2  = rot_y_p2,
            sim_ply      = self.sim_ply,
        )
        self.worker.start()

        streamer_path = PROJECT_ROOT / "rt_streamer.py"
        if streamer_path.exists():
            try:
                spec = importlib.util.spec_from_file_location(
                    "rt_streamer", str(streamer_path))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                self.streamer = mod
                log.ok("rt_streamer.py chargé")
            except Exception: pass

        self.gps_reader = GPSReader(
            port      = getattr(self.args, "gps_port", GPS_PORT),
            baud      = getattr(self.args, "gps_baud", GPS_BAUD),
            gps_queue = self.gps_queue,
            sim_mode  = self.sim_mode,
            sim_speed = getattr(self.args, "sim_speed", 1.0),
            pieds_l93 = self.pieds,
        )
        log.ok("Mode AUTO initialisé — en attente du GPS...")

    def run(self):
        self.running = True
        self._setup()
        self._render_ui(title_mode="Mode AUTO",
                        hints="Q/Esc = Quitter")
        self.key_reader.run_in_thread()
        self.gps_reader.start()

        last_render_t    = 0.0
        last_log_counter = -1

        try:
            while self.running:
                now = time.time()
                try:
                    gps = self.gps_queue.get(timeout=0.05)
                    x, y = gps["x_l93"], gps["y_l93"]
                    ts   = gps["ts"]

                    if self.sim_mode:
                        is_return = gps.get("phase", "ALLER") == "RETOUR"
                    else:
                        self.y_max_seen = max(self.y_max_seen, y)
                        is_return = y < self.y_max_seen - self.radius * 0.5

                    active_pass = 2 if is_return else 1
                    distances   = sorted(
                        [(i, abs(py - y)) for i,(_, py) in enumerate(self.pieds)],
                        key=lambda d: d[1])
                    closest_idx, min_dist = distances[0]
                    in_zone = min_dist <= self.radius

                    # ── Sortie de zone → arrêt enregistrement ─────────────
                    if self.recording:
                        if (not in_zone or active_pass != self.current_pass
                                or closest_idx != self.current_pied_idx):
                            self.recording = False
                            self.per_pass_recorder.stop_record()
                            idx = self.current_pied_idx
                            pas = self.current_pass
                            fr  = self.recorded[idx][pas]["frames"]
                            log.info(f"[{self._pied_id(idx)}] ⏹ Sortie zone P{pas} ({len(fr)} fr)")
                            if pas == 1:
                                self.pied_status[idx] = self.DONE_P1
                            elif pas == 2 and self.recorded[idx][1]["frames"]:
                                self._launch_reconstruction(idx)

                    # ── Entrée en zone → démarrage enregistrement ─────────
                    if not self.recording and in_zone:
                        st = self.pied_status[closest_idx]
                        skip = (st in (self.DONE_BOTH, self.PROCESSING)
                                or (active_pass == 1 and st == self.DONE_P1))
                        if not skip:
                            self.current_pied_idx = closest_idx
                            self.current_pass     = active_pass
                            self.recording        = True
                            pid = self._pied_id(closest_idx)
                            ext = CAM_EXT
                            bag = str(BAG_INPUT_DIR /
                                      f"{pid}_P{active_pass}_{self.date_label}_{int(ts)}{ext}")
                            self.recorded[closest_idx][active_pass]["bag"]    = bag
                            self.recorded[closest_idx][active_pass]["frames"] = []
                            log.info(f"[{pid}] ▶ Entrée zone P{active_pass} — caméra démarrée")
                            self.per_pass_recorder.start_record(bag)

                    # ── Enregistrement des frames ──────────────────────────
                    if self.recording:
                        f  = self.per_pass_recorder.current
                        fl = self.recorded[self.current_pied_idx][self.current_pass]["frames"]
                        if not fl or fl[-1][0] != f:
                            fl.append((f, ts))

                except queue.Empty: pass

                # Touches
                try:
                    k = self.key_queue.get_nowait().lower()
                    if k in ("q","esc","\x1b","\x03"): self.running = False
                except queue.Empty: pass

                if (Logger.update_counter != last_log_counter
                        or now - last_render_t > 0.5):
                    self._render_ui(title_mode="Mode AUTO",
                                    hints="Q/Esc = Quitter")
                    last_log_counter = Logger.update_counter
                    last_render_t    = now

        except KeyboardInterrupt: pass
        finally:
            self.running = False
            self.key_reader.stop()
            self.gps_reader.stop()
            Logger.ui_mode = False
            if self.per_pass_recorder: self.per_pass_recorder.stop_record()
            pending = self.job_queue.qsize()
            if pending:
                log.info(f"Attente de {pending} job(s)...")
                self.job_queue.join()
            if self.worker: self.worker.stop()
            print("\n\n✔ Mode AUTO terminé.\n")


# ══════════════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Contrôleur RT robot vigne — pipeline 3D double passe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Config : pipeline_config.json  (lu au démarrage)

EXEMPLES :
  python3 main_rt_controller.py --mode manual
  python3 main_rt_controller.py --mode manual --sim-ply
  python3 main_rt_controller.py --mode manual --in-bag realsense_data/rec.db3
  python3 main_rt_controller.py --mode sim    --sim-speed 2.0
  python3 main_rt_controller.py --mode real   --gps-port /dev/ttyACM0
        """)

    p.add_argument("--mode", choices=["manual","sim","real"], default="manual",
                   help="manual=clavier | sim=GPS synthétique | real=U-blox+RealSense")
    p.add_argument("--in-bag",      type=str, default="",
                   help="Fichier .bag/.db3 existant (désactive l'enregistrement caméra)")
    p.add_argument("--sim-speed",   type=float, default=1.0)
    p.add_argument("--sim-ply",     action="store_true",
                   help="PLY synthétiques — test sans caméra ni GPS")
    p.add_argument("--gps-port",    default=GPS_PORT)
    p.add_argument("--gps-baud",    type=int, default=GPS_BAUD)
    p.add_argument("--radius",      type=float, default=GPS_RADIUS)
    p.add_argument("--rot-y-pass2", type=float, default=ROT_Y_PASS2)

    args = p.parse_args()

    if args.mode == "manual":
        ManualController(args).run()
    else:
        RobotController(args).run()

if __name__ == "__main__":
    main()