#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_selected_jobs.py — Lance la reconstruction 3D pour une sélection manuelle
de pieds, à partir des frames déjà connues dans pied_jobs_summary.csv.
Ne repasse PAS par l'assignation GPS (Generate_pied_jobs_.py) : les frames
start/end sont lues directement dans le CSV.

CORRECTIF : après chaque job, le sous-dossier "data_*" généré par
l'extracteur C++ est renommé pour matcher exactement "data_{date_label}"
(format JJ-MM-AAAA, celui du dossier job parent). L'extracteur C++ nomme ce
sous-dossier d'après l'ordre jour/mois/année trouvé dans le nom du .bag, qui
peut différer du format JJ-MM-AAAA utilisé partout ailleurs dans le pipeline
(ex: data_2025-04-30 au lieu de data_30-04-2025) — sans cette normalisation,
les apps Streamlit (app_v4.py / app_piquet.py) ne retrouvent plus le nuage
car elles s'attendent à data_{date_label}.

Usage :
    python3 run_selected_jobs.py --pieds 4 12 24
    python3 run_selected_jobs.py --job-names pied04_pass1_04-07-2025 pied12_pass1_04-07-2025
    python3 run_selected_jobs.py --csv pied_jobs_summary.csv --pieds 4 12 --pass-select 1
"""

import argparse
import csv
import importlib.util
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_config() -> dict:
    with open(PROJECT_ROOT / "pipeline_config.json", encoding="utf-8") as f:
        return json.load(f)


def build_job(row: dict, cfg: dict) -> dict:
    date_label = row["date"]
    reg = cfg["bag_registry"].get(date_label)
    if reg is None:
        sys.exit(f"[ERREUR] Date {date_label} absente de bag_registry dans pipeline_config.json")

    bag_dir = PROJECT_ROOT / cfg["paths"]["bag_input_dir"]
    cpp   = cfg["params"]["params_extraction"]
    f3d   = cfg["params"]["filter_3d"]
    reg3d = cfg["params"]["registration"]

    return {
        "job_name":    row["job_name"],
        "bag_file":    str(bag_dir / reg["bag_file"]),
        "ubx_file":    str(bag_dir / reg["ubx_file"]),
        "z_min":       cpp["z_min"],
        "z_max":       cpp["z_max"],
        "start_frame": int(row["start_frame"]),
        "end_frame":   int(row["end_frame"]),
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
    }


def normalize_session_folder(job_dir: Path, date_label: str) -> None:
    """
    Renomme le sous-dossier data_* du job pour qu'il corresponde exactement
    à data_{date_label} (JJ-MM-AAAA), quel que soit l'ordre jour/mois/année
    utilisé par l'extracteur C++ pour le nommer.
    """
    if not job_dir.exists():
        return

    expected = job_dir / f"data_{date_label}"
    if expected.exists():
        return  # déjà au bon format

    candidates = [d for d in job_dir.glob("data_*") if d.is_dir()]
    if not candidates:
        return
    if len(candidates) > 1:
        print(f"    [!] {job_dir.name} : plusieurs dossiers data_* trouvés, "
              f"normalisation ignorée : {[c.name for c in candidates]}")
        return

    candidates[0].rename(expected)
    print(f"    ↳ Normalisé : {candidates[0].name} → {expected.name}")


def main():
    parser = argparse.ArgumentParser(description="Lance une sélection manuelle de jobs depuis le CSV récapitulatif.")
    parser.add_argument("--csv", type=Path, default=PROJECT_ROOT / "pied_jobs_summary.csv",
                         help="Chemin vers pied_jobs_summary.csv")
    parser.add_argument("--pieds", type=int, nargs="+", default=None,
                         help="Numéros de pieds à traiter (ex: --pieds 4 12 24)")
    parser.add_argument("--job-names", nargs="+", default=None,
                         help="job_name exacts à traiter (alternative à --pieds)")
    parser.add_argument("--pass-select", choices=["1", "2", "both"], default="both",
                         help="Filtrer par passage (défaut : les deux)")
    args = parser.parse_args()

    if not args.pieds and not args.job_names:
        sys.exit("[!] Précisez --pieds ou --job-names pour sélectionner des jobs.")

    if not args.csv.exists():
        sys.exit(f"[ERREUR] CSV introuvable : {args.csv}")

    cfg = load_config()

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    selected = []
    for row in rows:
        if args.job_names and row["job_name"] not in args.job_names:
            continue
        if args.pieds and int(row["pied_num"]) not in args.pieds:
            continue
        if args.pass_select != "both" and row["pass"] != f"pass{args.pass_select}":
            continue
        selected.append(row)

    if not selected:
        sys.exit("[!] Aucun job trouvé dans le CSV pour cette sélection.")

    print(f"  {len(selected)} job(s) sélectionné(s) :")
    for r in selected:
        print(f"    {r['job_name']}  frames {r['start_frame']}→{r['end_frame']}  ({r['n_frames']} fr)")

    jobs = [build_job(r, cfg) for r in selected]

    # Charge Run full pipeline.py et lui injecte uniquement les jobs sélectionnés
    rfp_path = PROJECT_ROOT / "Run full pipeline.py"
    if not rfp_path.exists():
        sys.exit(f"[ERREUR] Script introuvable : {rfp_path}")

    spec = importlib.util.spec_from_file_location("run_full_pipeline", str(rfp_path))
    mod = importlib.util.module_from_spec(spec)
    mod.BAG_JOBS = jobs          # évite l'exécution du job d'exemple pendant l'import
    spec.loader.exec_module(mod)
    mod.BAG_JOBS = jobs          # ré-assigne après exec_module (au cas où)
    mod.main()

    # ── Normalisation des dossiers data_* après reconstruction ─────────────
    print(f"\n  Normalisation des noms de dossiers data_* ({len(selected)} job(s))...")
    for r in selected:
        job_name   = r["job_name"]
        date_label = r["date"]
        normalize_session_folder(mod.EXTRACTION_OUTPUT_BASE / job_name, date_label)
        normalize_session_folder(mod.FINAL_OUTPUT_BASE / job_name, date_label)


if __name__ == "__main__":
    main()