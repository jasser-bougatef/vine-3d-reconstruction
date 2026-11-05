#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rang_pass1.py
"""

DATE         = "17-07-2025"    
FIRST_FRAME  = 185             
LAST_FRAME   = 1574            
FRAME_STRIDE = 3               

import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PASS_NUM = 1

def load_config() -> dict:
    cfg_path = PROJECT_ROOT / "pipeline_config.json"
    if not cfg_path.exists():
        sys.exit(f"[ERREUR] pipeline_config.json introuvable : {cfg_path}")
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)

def build_job(cfg: dict) -> dict:
    reg = cfg["bag_registry"].get(DATE)
    if reg is None:
        sys.exit(f"[ERREUR] Date {DATE} absente de bag_registry dans pipeline_config.json")

    bag_dir = PROJECT_ROOT / cfg["paths"]["bag_input_dir"]
    cpp   = cfg["params"].get("params_extraction", cfg["params"].get("cpp"))
    if cpp is None:
        sys.exit("[ERREUR] Ni 'params_extraction' ni 'cpp' trouvés dans params de pipeline_config.json")
    f3d   = cfg["params"]["filter_3d"]
    reg3d = cfg["params"]["registration"]

    job_name = f"rang_pass{PASS_NUM}_{DATE}_piquetee"

    return {
        "job_name":    job_name,
        "bag_file":    str(bag_dir / reg["bag_file"]),
        "ubx_file":    str(bag_dir / reg["ubx_file"]),
        "z_min":       cpp["z_min"],
        "z_max":       cpp["z_max"],
        "start_frame": FIRST_FRAME,
        "end_frame":   LAST_FRAME,
        "rot_x_deg":   reg.get("rot_x_deg", cpp["rot_x_deg"]),
        "rot_y_deg":   reg.get("rot_y_deg", cpp["rot_y_deg"]),
        "rot_z_deg":   reg.get("rot_z_deg", cpp["rot_z_deg"]),
        "voxel_size":         f3d["voxel_size"],
        "use_statistical":    f3d["use_statistical"],
        "use_radius_filter":  f3d["use_radius_filter"],
        "eps":                f3d["eps"],
        "min_points":         f3d["min_points"],
        "min_cluster_size":   f3d["min_cluster_size"],
        "nb_neighbors":       f3d["nb_neighbors"],
        "std_ratio":          f3d["std_ratio"],
        "radius":             f3d["radius"],
        "min_radius_points":  f3d["min_radius_points"],
        "voxel_size_reg":     reg3d["voxel_size_reg"],
        "window_size":        reg3d["window_size"],
        "voxel_size_merge":   reg3d["voxel_size_merge"],
        "sliding_voxel_size": reg3d["sliding_voxel_size"],
        "frame_reduction_enabled": True,
        "frame_stride":            FRAME_STRIDE,
    }

def normalize_session_folder(job_dir: Path, date_label: str) -> None:
    if not job_dir.exists():
        return
    expected = job_dir / f"data_{date_label}"
    if expected.exists():
        return
    candidates = [d for d in job_dir.glob("data_*") if d.is_dir()]
    if len(candidates) != 1:
        return
    candidates[0].rename(expected)
    print(f"    ↳ Normalisé : {candidates[0].name} → {expected.name}")

def main():
    cfg = load_config()
    job = build_job(cfg)

    print(f"  Rang complet — passage {PASS_NUM} — {DATE}")
    print(f"  Frames {FIRST_FRAME} → {LAST_FRAME}  "
          f"(réduction : 1 frame conservée sur {FRAME_STRIDE})")
    print(f"  Job : {job['job_name']}")

    rfp_path = PROJECT_ROOT / "Run_full_pipeline_piquetee.py"
    if not rfp_path.exists():
        sys.exit(f"[ERREUR] Script introuvable : {rfp_path}")

    spec = importlib.util.spec_from_file_location("run_full_pipeline", str(rfp_path))
    mod = importlib.util.module_from_spec(spec)
    mod.BAG_JOBS = [job]
    spec.loader.exec_module(mod)
    mod.BAG_JOBS = [job]
    mod.main()

    normalize_session_folder(mod.EXTRACTION_OUT_DEFAULT / job["job_name"], DATE)
    normalize_session_folder(mod.FINAL_OUT_DEFAULT / job["job_name"], DATE)

if __name__ == "__main__":
    main()