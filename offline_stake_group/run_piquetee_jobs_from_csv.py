#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_piquetee_jobs_from_csv.py
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DATE_RE = re.compile(r"(\d{2}-\d{2}-\d{4})$")

def load_config() -> dict:
    cfg_path = PROJECT_ROOT / "pipeline_config.json"
    if not cfg_path.exists():
        sys.exit(f"[ERREUR] pipeline_config.json introuvable : {cfg_path}")
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)

def read_summary_csv(csv_path: Path, pass_select: str, panneau_filter) -> list[dict]:
    if not csv_path.exists():
        sys.exit(f"[ERREUR] CSV introuvable : {csv_path}")

    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            job_name = row.get("job_name", "").strip()
            pass_val = row.get("pass", "").strip()
            sf = row.get("start_frame", "").strip()
            ef = row.get("end_frame", "").strip()

            if not job_name:
                continue
            if pass_val != pass_select:
                continue

            m = DATE_RE.search(job_name)
            if not m:
                print(f"  [!] {job_name} : date introuvable dans le nom (attendu JJ-MM-AAAA en fin), ignoré.")
                continue
            date_label = m.group(1)

            if panneau_filter:
                try:
                    panneau_num = int(row.get("panneau_num", "").strip())
                except ValueError:
                    panneau_num = None
                if panneau_num not in panneau_filter:
                    continue

            if not sf or not ef:
                print(f"  [!] {job_name} : start_frame/end_frame vides, ignoré.")
                continue

            rows.append({
                "job_name":    job_name,
                "date":        date_label,
                "start_frame": int(float(sf)),
                "end_frame":   int(float(ef)),
            })
    return rows

def build_job(row: dict, cfg: dict) -> dict:
    date_label = row["date"]
    reg = cfg["bag_registry"].get(date_label)
    if reg is None:
        sys.exit(f"[ERREUR] Date {date_label} absente de bag_registry dans pipeline_config.json "
                  f"(job {row['job_name']})")

    bag_dir = PROJECT_ROOT / cfg["paths"]["bag_input_dir"]
    cpp   = cfg["params"].get("params_extraction", cfg["params"].get("cpp"))
    f3d   = cfg["params"]["filter_3d"]
    reg3d = cfg["params"]["registration"]
    frd   = cfg["params"].get("frame_reduction", {})

    return {
        "job_name":    row["job_name"],
        "bag_file":    str(bag_dir / reg["bag_file"]),
        "ubx_file":    str(bag_dir / reg["ubx_file"]),
        "z_min":       cpp["z_min"],
        "z_max":       cpp["z_max"],
        "start_frame": row["start_frame"],
        "end_frame":   row["end_frame"],
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
        "frame_reduction_enabled": frd.get("enabled", False),
        "frame_stride":            frd.get("stride", 2),
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
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=PROJECT_ROOT / "piquet_jobs_summary.csv",
                        help="CSV récapitulatif des piquets (défaut : piquet_jobs_summary.csv à la racine du projet)")
    parser.add_argument("--panneaux", type=int, nargs="+", default=None,
                        help="Ne traiter que certains panneau_num (ex: --panneaux 1 3 5)")
    parser.add_argument("--pass-select", default="pass1",
                        help="Valeur de la colonne 'pass' à traiter (défaut : pass1, passage 1 uniquement)")
    args = parser.parse_args()

    cfg = load_config()
    rows = read_summary_csv(args.csv, args.pass_select, args.panneaux)

    if not rows:
        sys.exit("[!] Aucune ligne valide trouvée dans le CSV pour cette sélection.")

    print(f"  {len(rows)} job(s) à reconstruire ({args.pass_select}) :")
    for r in rows:
        print(f"    {r['job_name']}  frames {r['start_frame']}→{r['end_frame']}")

    jobs = [build_job(r, cfg) for r in rows]

    # CORRECTION : Le chemin pointe désormais vers Run_full_pipeline_piquetee.py (avec underscores)
    rfp_path = PROJECT_ROOT / "Run_full_pipeline_piquetee.py"
    if not rfp_path.exists():
        sys.exit(f"[ERREUR] Script introuvable : {rfp_path}")

    spec = importlib.util.spec_from_file_location("run_full_pipeline", str(rfp_path))
    mod = importlib.util.module_from_spec(spec)
    mod.BAG_JOBS = jobs
    spec.loader.exec_module(mod)
    mod.BAG_JOBS = jobs
    mod.main()

    print(f"\n  Normalisation des noms de dossiers data_* ({len(rows)} job(s))...")
    for r in rows:
        normalize_session_folder(mod.EXTRACTION_OUT_DEFAULT / r["job_name"], r["date"])
        normalize_session_folder(mod.FINAL_OUT_DEFAULT / r["job_name"], r["date"])

if __name__ == "__main__":
    main()