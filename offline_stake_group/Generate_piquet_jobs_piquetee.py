#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   GENERATE_PIQUET_JOBS_PIQUETEÉ  —  Lambert93 natif + lancement          ║
║                                                                          ║
║  Logique "INTER-PIQUET" (Panneaux) :                                     ║
║  Le shapefile (pos_piquet.shp) contient les extrémités des piquets       ║
║  (les poteaux physiques). Le script génère des jobs correspondant à      ║
║  l'intervalle exact entre le Poteau N et le Poteau N+1.                  ║
║                                                                          ║
Usage :
    python3 Generate_piquet_jobs_piquetee.py                      
    python3 Generate_piquet_jobs_piquetee.py --no-run             
    python3 Generate_piquet_jobs_piquetee.py --pass-select 1      
    python3 Generate_piquet_jobs_piquetee.py --piquets 1 2        
"""

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 0 — CHARGEMENT DE LA CONFIG CENTRALE
# ══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent / "pipeline_config.json"
    if not cfg_path.exists():
        sys.exit(f"[ERREUR] pipeline_config.json introuvable : {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

_CFG = _load_config()

PROJECT_ROOT  = Path(_CFG["project_root"])
BAG_INPUT_DIR = PROJECT_ROOT / _CFG["paths"]["bag_input_dir"]
DEFAULT_SHP   = PROJECT_ROOT / _CFG["paths"]["shapefile"]
DEFAULT_SYN_DIR = PROJECT_ROOT / _CFG["paths"]["syn_output_dir"]
RUN_PIPELINE_SCRIPT = PROJECT_ROOT / _CFG["paths"]["run_pipeline_script"]

DEFAULT_CPP = dict(_CFG["params"]["cpp"])
_f = _CFG["params"]["filter_3d"]
_r = _CFG["params"]["registration"]
_frd = _CFG["params"].get("frame_reduction", {})

DEFAULT_3D = dict(
    voxel_size        = _f["voxel_size"],
    use_statistical   = _f["use_statistical"],
    use_radius_filter = _f["use_radius_filter"],
    eps               = _f["eps"],
    min_points        = _f["min_points"],
    min_cluster_size  = _f["min_cluster_size"],
    nb_neighbors      = _f["nb_neighbors"],
    std_ratio         = _f["std_ratio"],
    radius            = _f["radius"],
    min_radius_points = _f["min_radius_points"],
    voxel_size_reg     = _r["voxel_size_reg"],
    window_size        = _r["window_size"],
    voxel_size_merge   = _r["voxel_size_merge"],
    sliding_voxel_size = _r["sliding_voxel_size"],
    frame_reduction_enabled = _frd.get("enabled", False),
    frame_stride            = _frd.get("stride", 2),
)

BAG_REGISTRY = {
    date: {
        "bag_file":  str(BAG_INPUT_DIR / entry["bag_file"]),
        "ubx_file":  str(BAG_INPUT_DIR / entry["ubx_file"]),
        "rot_x_deg": entry.get("rot_x_deg", DEFAULT_CPP["rot_x_deg"]),
        "rot_y_deg": entry.get("rot_y_deg", DEFAULT_CPP["rot_y_deg"]),
        "rot_z_deg": entry.get("rot_z_deg", DEFAULT_CPP["rot_z_deg"]),
    }
    for date, entry in _CFG["bag_registry"].items()
}

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — LECTURE DU SHAPEFILE (Les Poteaux)
# ══════════════════════════════════════════════════════════════════════════════

def load_shapefile(shp_path: Path) -> list[tuple[float, float]]:
    """Lit le shapefile Lambert93 et retourne les coordonnées des extrémités."""
    try:
        import shapefile
    except ImportError:
        sys.exit("[ERREUR] pyshp requis :  pip install pyshp")

    sf = shapefile.Reader(str(shp_path))
    points = [shape.points[0] for shape in sf.shapes()]
    print(f"  ✔  {len(points)} extrémité(s) (poteaux) chargée(s) depuis le shapefile (Lambert93)")
    return points

def load_sync_files(syn_dir: Path) -> dict[str, pd.DataFrame]:
    try:
        from pyproj import Transformer
    except ImportError:
        sys.exit("[ERREUR] pyproj requis :  pip install pyproj")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    result = {}
    for csv_path in sorted(syn_dir.glob("synchronized_*.csv")):
        m = re.search(r"synchronized_(\d{2}-\d{2}-\d{4})\.csv$", csv_path.name)
        if not m: continue
        label = m.group(1)
        df = pd.read_csv(csv_path)
        x_l93, y_l93 = transformer.transform(df["lon_rtk"].values, df["lat_rtk"].values)
        df["x_l93"] = x_l93
        df["y_l93"] = y_l93
        result[label] = df
        print(f"  ✔  {csv_path.name}  ({len(df)} frames)")

    if not result:
        sys.exit(f"[ERREUR] Aucun synchronized_*.csv dans : {syn_dir}")
    return result

def detect_turnaround(df: pd.DataFrame) -> int:
    return int(df["frame_number"].iloc[np.argmax(df["y_l93"].values)])

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — PLUS LONG SEGMENT DE FRAMES CONTIGUËS
# ══════════════════════════════════════════════════════════════════════════════

def _longest_contiguous_run(frame_numbers: np.ndarray, max_gap: int = 1):
    if len(frame_numbers) == 0:
        return None
    frame_numbers = np.sort(np.unique(frame_numbers))
    if len(frame_numbers) == 1:
        return int(frame_numbers[0]), int(frame_numbers[0])

    gaps = np.where(np.diff(frame_numbers) > max_gap)[0]
    run_starts = np.insert(gaps + 1, 0, 0)
    run_ends   = np.append(gaps, len(frame_numbers) - 1)
    run_lengths = run_ends - run_starts + 1
    best = int(np.argmax(run_lengths))
    return int(frame_numbers[run_starts[best]]), int(frame_numbers[run_ends[best]])

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — DÉCOUPAGE EN PANNEAUX EXACTS
# ══════════════════════════════════════════════════════════════════════════════

def compute_panels_from_posts(posts_l93: list[tuple[float, float]], edge_padding: float = 0.0) -> dict[int, dict]:
    by_y = sorted(posts_l93, key=lambda p: p[1])
    n = len(by_y)
    
    if n < 2:
        sys.exit("[ERREUR] Le shapefile doit contenir au moins 2 points pour définir un panneau.")

    panels = {}
    for i in range(n - 1):
        p1_x, p1_y = by_y[i]
        p2_x, p2_y = by_y[i + 1]

        y_min = min(p1_y, p2_y)
        y_max = max(p1_y, p2_y)

        if i == 0:
            y_min -= edge_padding
        if i == n - 2:
            y_max += edge_padding

        center_x = (p1_x + p2_x) / 2.0
        center_y = (y_min + y_max) / 2.0

        panel_num = i + 1
        panels[panel_num] = {
            "center_x": center_x,
            "center_y": center_y,
            "y_min": y_min,
            "y_max": y_max,
        }
    return panels

def frames_for_piquet(df: pd.DataFrame, y_min: float, y_max: float, turn_frame: int, pass_select: str, min_run_frames: int = 3, max_gap: int = 1) -> dict:
    in_window = (df["y_l93"] >= y_min) & (df["y_l93"] <= y_max)

    def _range(subset):
        run = _longest_contiguous_run(subset["frame_number"].values, max_gap=max_gap)
        if run is None: return None
        start, end = run
        if (end - start + 1) < min_run_frames: return None
        return start, end

    out = {"pass1": None, "pass2": None}
    if pass_select in ("1", "both"):
        out["pass1"] = _range(df[in_window & (df["frame_number"] <= turn_frame)])
    if pass_select in ("2", "both"):
        out["pass2"] = _range(df[in_window & (df["frame_number"] >= turn_frame)])
    return out

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4b — VALIDATION POST-ASSIGNATION
# ══════════════════════════════════════════════════════════════════════════════

def check_overlaps(jobs: list[dict]) -> list[str]:
    warnings = []
    by_pass_date = {}
    for j in jobs:
        key = (j["_pass"], j["_date"])
        by_pass_date.setdefault(key, []).append(j)
    for key, group in by_pass_date.items():
        group.sort(key=lambda j: j["start_frame"])
        for a, b in zip(group, group[1:]):
            if a["end_frame"] >= b["start_frame"]:
                warnings.append(
                    f"[CHEVAUCHEMENT] {a['job_name']} ({a['start_frame']}-{a['end_frame']}) "
                    f"chevauche {b['job_name']} ({b['start_frame']}-{b['end_frame']})"
                )
    return warnings

def enforce_frame_gap(jobs: list[dict], min_gap: int = 10, min_run_frames: int = 3) -> list[str]:
    warnings = []
    by_pass_date = {}
    for j in jobs:
        key = (j["_pass"], j["_date"])
        by_pass_date.setdefault(key, []).append(j)

    for key, group in by_pass_date.items():
        group.sort(key=lambda j: j["start_frame"])
        for a, b in zip(group, group[1:]):
            current_gap = b["start_frame"] - a["end_frame"] - 1
            if current_gap >= min_gap:
                continue

            needed = min_gap - current_gap
            shrink_a = needed // 2
            shrink_b = needed - shrink_a

            new_a_end   = a["end_frame"]   - shrink_a
            new_b_start = b["start_frame"] + shrink_b

            len_a = new_a_end - a["start_frame"] + 1
            len_b = b["end_frame"] - new_b_start + 1

            if len_a < min_run_frames or len_b < min_run_frames:
                warnings.append(
                    f"[GAP IMPOSSIBLE] {a['job_name']} / {b['job_name']} : "
                    f"impossible d'imposer {min_gap} frames d'écart"
                )
                continue

            a["end_frame"]   = new_a_end
            b["start_frame"] = new_b_start
            a["_n_frames"]   = len_a
            b["_n_frames"]   = len_b

    return warnings

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — CONSTRUCTION DES BAG_JOBS
# ══════════════════════════════════════════════════════════════════════════════

def build_bag_jobs(
    posts_l93: list[tuple[float, float]],
    sync_data: dict[str, pd.DataFrame],
    pass_select: str,
    panel_filter: list[int] | None,
    edge_padding: float = 0.0,
    frame_gap: int = 10,
    min_run_frames: int = 3,
) -> list[dict]:
    
    jobs = []
    panels = compute_panels_from_posts(posts_l93, edge_padding=edge_padding)
    panel_has_job = {num: False for num in panels.keys()}

    print(f"\n  ── Découpage en {len(panels)} panneaux inter-piquets ──")
    for num, data in panels.items():
        print(f"  Panneau {num:02d} : de Y={data['y_min']:.2f} à {data['y_max']:.2f} m (longueur {data['y_max']-data['y_min']:.2f} m)")

    for date_label, df in sorted(sync_data.items()):
        if date_label not in BAG_REGISTRY: continue
        reg = BAG_REGISTRY[date_label]
        turn_frame = detect_turnaround(df)
        cam_y_min, cam_y_max = df["y_l93"].min(), df["y_l93"].max()

        for panel_num, pdata in panels.items():
            if panel_filter and panel_num not in panel_filter: continue

            center_x = pdata["center_x"]
            y_min = pdata["y_min"]
            y_max = pdata["y_max"]

            if y_max < cam_y_min or y_min > cam_y_max:
                continue

            ranges = frames_for_piquet(df, y_min, y_max, turn_frame, pass_select, min_run_frames=min_run_frames)

            for pass_name, fr_range in ranges.items():
                if fr_range is None: continue

                start_fr, end_fr = fr_range
                n_frames = end_fr - start_fr + 1
                job_name = f"piquetee{panel_num:02d}_{pass_name}_{date_label}"

                jobs.append({
                    "_piquet_num": panel_num,
                    "_pass":       pass_name,
                    "_date":       date_label,
                    "_piquet_x_l93": round(center_x, 3),
                    "_piquet_y_l93": round(pdata["center_y"], 3),
                    "_y_min":      round(y_min, 3),
                    "_y_max":      round(y_max, 3),
                    "_n_frames":   n_frames,
                    "job_name": job_name,
                    "bag_file": reg["bag_file"],
                    "ubx_file": reg["ubx_file"],
                    "z_min":       DEFAULT_CPP["z_min"],
                    "z_max":       DEFAULT_CPP["z_max"],
                    "start_frame": start_fr,
                    "end_frame":   end_fr,
                    "rot_x_deg":   reg["rot_x_deg"],
                    "rot_y_deg":   reg["rot_y_deg"],
                    "rot_z_deg":   reg["rot_z_deg"],
                    **DEFAULT_3D,
                })
                panel_has_job[panel_num] = True

    overlap_before = check_overlaps(jobs)
    if overlap_before:
        print(f"\n  ⚠  {len(overlap_before)} chevauchement(s) AVANT correction :")
        for w in overlap_before: print(f"    {w}")

    if frame_gap > 0:
        gap_warnings = enforce_frame_gap(jobs, min_gap=frame_gap, min_run_frames=min_run_frames)
        print(f"\n  ✔  Marge de sécurité appliquée : {frame_gap} frames min entre panneaux")

    overlap_after = check_overlaps(jobs)
    if overlap_after:
        print(f"\n  ⚠⚠⚠  {len(overlap_after)} CHEVAUCHEMENT(S) RÉSIDUEL(S) ⚠⚠⚠")
        for w in overlap_after: print(f"    {w}")

    return jobs

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 & 7 — EXPORT ET LANCEMENT
# ══════════════════════════════════════════════════════════════════════════════

def export_summary_csv(jobs: list[dict], out_path: Path):
    rows = [
        {
            "job_name":    j["job_name"],
            "panneau_num": j["_piquet_num"],
            "pass":        j["_pass"],
            "date":        j["_date"],
            "start_frame": j["start_frame"],
            "end_frame":   j["end_frame"],
            "n_frames":    j["_n_frames"],
            "y_l93_min":   j.get("_y_min", j["_piquet_y_l93"]),
            "y_l93_max":   j.get("_y_max", j["_piquet_y_l93"]),
        }
        for j in jobs
    ]
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"  ✔  Résumé CSV → {out_path}")

def launch_pipeline(jobs: list[dict], pipeline_script: Path):
    if not pipeline_script.exists():
        sys.exit(f"[ERREUR] Script pipeline introuvable : {pipeline_script}")
    print(f"\n{'═'*70}\n  LANCEMENT AUTOMATIQUE → {pipeline_script.name}\n{'═'*70}\n")
    clean_jobs = [{k: v for k, v in j.items() if not k.startswith("_")} for j in jobs]
    spec = importlib.util.spec_from_file_location("run_full_pipeline", str(pipeline_script))
    mod  = importlib.util.module_from_spec(spec)
    mod.BAG_JOBS = clean_jobs
    spec.loader.exec_module(mod)
    mod.BAG_JOBS = clean_jobs
    mod.main()

def main():
    parser = argparse.ArgumentParser(
        description="Assigne les frames aux PANNEAUX (inter-piquets) et lance le pipeline 3D."
    )
    parser.add_argument("--shp",     type=Path, default=DEFAULT_SHP)
    parser.add_argument("--syn-dir", type=Path, default=DEFAULT_SYN_DIR)
    parser.add_argument("--pass-select", choices=["1", "2", "both"], default="both")
    parser.add_argument("--piquets",   type=int, nargs="+", default=None)
    parser.add_argument("--pipeline-script", type=Path, default=RUN_PIPELINE_SCRIPT)
    parser.add_argument("--no-run",  action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--out-csv", type=Path, default=PROJECT_ROOT / _CFG["paths"].get("piquet_jobs_summary", "piquet_jobs_summary.csv"))
    parser.add_argument("--edge-padding", type=float, default=0.0)
    parser.add_argument("--frame-gap", type=int, default=10)
    parser.add_argument("--min-run-frames", type=int, default=3)
    args = parser.parse_args()

    print("═" * 70)
    print("  GENERATE_PIQUET_JOBS_PIQUETEÉ   —  Reconstruction par PANNEAUX INTER-PIQUETS")
    print("═" * 70)

    posts_l93 = load_shapefile(args.shp)
    sync_data = load_sync_files(args.syn_dir)

    jobs = build_bag_jobs(
        posts_l93, sync_data, args.pass_select, args.piquets,
        edge_padding=args.edge_padding,
        frame_gap=args.frame_gap,
        min_run_frames=args.min_run_frames,
    )

    if not jobs:
        print("\n[!] Aucun job généré.")
        sys.exit(0)

    if args.preview: return
    export_summary_csv(jobs, args.out_csv)
    if args.no_run: return
    launch_pipeline(jobs, args.pipeline_script)

if __name__ == "__main__":
    main()