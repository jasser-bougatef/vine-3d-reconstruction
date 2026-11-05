#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  Run_full_pipeline.py  —  Extraction (C++) + Reconstruction 3D par job   ║
║                                                                          ║
║  CHANGEMENT MAJEUR (nouvel auto_extractor.cpp multithreadé) :            ║
║    • AVANT : un .cpp était régénéré puis RECOMPILÉ pour CHAQUE job       ║
║      (lent — plusieurs dizaines de secondes de compilation par pied).    ║
║    • MAINTENANT : le binaire auto_extractor est compilé UNE SEULE FOIS.  ║
║      Chaque job passe ses paramètres (frames, z_min/z_max, rotations…)   ║
║      en arguments CLI à l'exécution. Beaucoup plus rapide, surtout       ║
║      pour les balayages de paramètres (sliding window sweep) et la      ║
║      reconstruction à chaud depuis app_v5.py / app_v6.py.                ║
║    • Le nouvel extracteur est aussi multithreadé en interne (pool de     ║
║      workers), donc plus rapide frame par frame.                         ║
║    • Il ne fait plus l'extraction GPS (data_GPS.txt) : cette sortie      ║
║      n'était consommée par aucun autre script du pipeline (le GPS est    ║
║      géré indépendamment par PPK_Pipeline.py / syn_code.py en amont de  ║
║      l'assignation des frames).                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT           = Path(__file__).resolve().parent
BUILD_DIR               = PROJECT_ROOT / "build"
EXTRACTOR_SRC           = PROJECT_ROOT / "auto_extractor.cpp"
EXTRACTOR_BIN           = BUILD_DIR / "auto_extractor"
EXTRACTION_OUT_DEFAULT  = PROJECT_ROOT / "Extraction_Output"
FINAL_OUT_DEFAULT       = PROJECT_ROOT / "final_Output"

# Injecté par Generate_pied_jobs_.py::launch_pipeline() avant exec_module,
# puis réinjecté après (voir ce fichier) — conservé pour compatibilité.
BAG_JOBS = []


# ══════════════════════════════════════════════════════════════════════════
#  COMPILATION — UNE SEULE FOIS (au lieu d'une fois par job)
# ══════════════════════════════════════════════════════════════════════════

def compile_extractor(build_dir: Path = BUILD_DIR) -> Path:
    """
    Compile auto_extractor UNE SEULE FOIS (cmake + build), uniquement si le
    binaire n'existe pas encore ou si le .cpp source est plus récent que le
    binaire. Retourne le chemin de l'exécutable.
    """
    build_dir.mkdir(parents=True, exist_ok=True)

    needs_build = (
        not EXTRACTOR_BIN.exists()
        or (EXTRACTOR_SRC.exists() and EXTRACTOR_BIN.stat().st_mtime < EXTRACTOR_SRC.stat().st_mtime)
    )
    if not needs_build:
        return EXTRACTOR_BIN

    if not EXTRACTOR_SRC.exists():
        sys.exit(f"[ERREUR] Source introuvable : {EXTRACTOR_SRC}")
    if shutil.which("cmake") is None:
        sys.exit("[ERREUR] cmake introuvable dans le PATH.")

    print("[BUILD] Compilation de auto_extractor (une seule fois — peut prendre 1-2 min)…")

    cmake_cfg = subprocess.run(
        ["cmake", "-DCMAKE_BUILD_TYPE=Release", str(PROJECT_ROOT)],
        cwd=str(build_dir), capture_output=True, text=True,
    )
    if cmake_cfg.returncode != 0:
        print(cmake_cfg.stdout)
        print(cmake_cfg.stderr, file=sys.stderr)
        sys.exit("[ERREUR] Configuration CMake échouée (voir logs ci-dessus).")

    n_jobs = str(max(1, os.cpu_count() or 1))
    build = subprocess.run(
        ["cmake", "--build", ".", "--", f"-j{n_jobs}"],
        cwd=str(build_dir), capture_output=True, text=True,
    )
    if build.returncode != 0:
        print(build.stdout)
        print(build.stderr, file=sys.stderr)
        sys.exit("[ERREUR] Compilation échouée (voir logs ci-dessus).")

    if not EXTRACTOR_BIN.exists():
        sys.exit(f"[ERREUR] Binaire introuvable après compilation : {EXTRACTOR_BIN}")

    print(f"[BUILD] ✔ Binaire compilé → {EXTRACTOR_BIN}")
    return EXTRACTOR_BIN


# ══════════════════════════════════════════════════════════════════════════
#  RÉSOLUTION DU NOM DE SESSION — ".last_session"
# ══════════════════════════════════════════════════════════════════════════

def get_last_session_name(extraction_out_dir: Path, timeout_s: float = 30.0) -> str:
    """
    Lit le fichier ".last_session" écrit par auto_extractor au moment où il
    crée son dossier data_<horodatage>. Attend jusqu'à `timeout_s` secondes
    au cas où l'écriture disque serait légèrement en retard.
    """
    marker = extraction_out_dir / ".last_session"
    t0 = time.time()
    while not marker.exists():
        if time.time() - t0 > timeout_s:
            sys.exit(f"[ERREUR] .last_session introuvable dans {extraction_out_dir} "
                      f"après {timeout_s}s — l'extraction a-t-elle bien démarré ?")
        time.sleep(0.2)
    name = marker.read_text(encoding="utf-8").strip()
    if not name:
        sys.exit(f"[ERREUR] .last_session vide dans {extraction_out_dir}.")
    return name


# ══════════════════════════════════════════════════════════════════════════
#  CONSTRUCTION DES ARGUMENTS CLI PAR JOB
# ══════════════════════════════════════════════════════════════════════════

def build_extractor_args(job: dict, out_dir: Path) -> list:
    """
    Traduit un dict `job` (mêmes clés que Generate_pied_jobs_.py /
    app_v5.py::rebuild_construction) en arguments CLI pour le nouveau
    auto_extractor.

    NOTES :
      • Fichier .bag passé en argument positionnel (pas de --bag).
      • --no-keep-camera-frame active les rotations (rot-x/y/z) tout en
        laissant le centrage désactivé par défaut (voir auto_extractor.cpp,
        indispensable pour que la registration ICP fonctionne).
      • ubx_file n'est plus utilisé par l'extracteur (plus de GPS ici) —
        conservé dans le dict job pour compatibilité mais ignoré ici.
      • --rgb --ply --timestamps --params : seules les sorties réellement
        utilisées par le reste du pipeline (RGB pour l'aperçu, PLY pour la
        reconstruction, timestamps, intrinsics). --depth-raw / --pcd omis
        pour gagner du temps (rien ne les consomme).
    """
    return [
        str(job["bag_file"]),
        "--out",   str(out_dir),
        "--start", str(job["start_frame"]),
        "--end",   str(job["end_frame"]),
        "--zmin",  str(job.get("z_min", 0.0)),
        "--zmax",  str(job.get("z_max", 1.5)),
        "--no-keep-camera-frame",
        "--rot-x", str(job.get("rot_x_deg", 0.0)),
        "--rot-y", str(job.get("rot_y_deg", 0.0)),
        "--rot-z", str(job.get("rot_z_deg", 270.0)),
        "--rgb", "--ply", "--timestamps", "--params",
        "--jobs", str(job.get("num_threads", 0)),   # 0 = auto (tous les cœurs)
    ]


def run_extraction(job: dict, extraction_out_dir: Path, log_cb=None) -> tuple:
    """
    Compile (si besoin) puis lance l'extraction pour UN job, en streamant
    les lignes de sortie vers `log_cb` (ex: status.write dans Streamlit, ou
    print() en mode batch).
    Retourne (succès: bool, session_name: str | None).
    """
    executable = compile_extractor()
    extraction_out_dir.mkdir(parents=True, exist_ok=True)
    args = build_extractor_args(job, extraction_out_dir)

    if log_cb:
        log_cb("▶ " + " ".join([executable.name] + args))

    process = subprocess.Popen(
        [str(executable)] + args, cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    for line in process.stdout:
        line = line.rstrip()
        if line and log_cb:
            log_cb(line)
    process.wait()

    if process.returncode != 0:
        return False, None

    session_name = get_last_session_name(extraction_out_dir)
    return True, session_name


# ══════════════════════════════════════════════════════════════════════════
#  RECONSTRUCTION 3D (filtrage DBSCAN + registration ICP/RANSAC) — inchangé
# ══════════════════════════════════════════════════════════════════════════

def run_3d_reconstruction(job: dict, ply_input_dir: Path, output_dir: Path):
    """Charge all_pipeline_3D.py dynamiquement et lance filtrage + registration."""
    spec = importlib.util.spec_from_file_location(
        "pipeline3d", str(PROJECT_ROOT / "all_pipeline_3D.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ply_out   = output_dir / "PointCloud_ply"
    trans_out = output_dir / "Transformations"
    ply_out.mkdir(parents=True, exist_ok=True)
    trans_out.mkdir(parents=True, exist_ok=True)

    filter_mod = mod.PointCloudFilter(
        voxel_size        = job.get("voxel_size", mod._F3D["voxel_size"]),
        use_statistical    = job.get("use_statistical", mod._F3D["use_statistical"]),
        use_radius_filter  = job.get("use_radius_filter", mod._F3D["use_radius_filter"]),
        eps                = job.get("eps", mod._F3D["eps"]),
        min_points         = job.get("min_points", mod._F3D["min_points"]),
        min_cluster_size   = job.get("min_cluster_size", mod._F3D["min_cluster_size"]),
        nb_neighbors       = job.get("nb_neighbors", mod._F3D["nb_neighbors"]),
        std_ratio          = job.get("std_ratio", mod._F3D["std_ratio"]),
        radius             = job.get("radius", mod._F3D["radius"]),
        min_radius_points  = job.get("min_radius_points", mod._F3D["min_radius_points"]),
    )
    reg_mod = mod.SlidingWindowRegistration(
        output_ply_dir     = str(ply_out),
        output_trans_dir   = str(trans_out),
        voxel_size_reg     = job.get("voxel_size_reg", mod._REG["voxel_size_reg"]),
        window_size        = job.get("window_size", mod._REG["window_size"]),
        voxel_size_merge   = job.get("voxel_size_merge", mod._REG["voxel_size_merge"]),
        sliding_voxel_size = job.get("sliding_voxel_size", mod._REG["sliding_voxel_size"]),
    )

    clouds = filter_mod.process_folder(str(ply_input_dir))
    if not clouds:
        raise RuntimeError("Aucun nuage valide après filtrage — assouplir eps/min_cluster_size.")

    reg_mod.run_registration(clouds)


# ══════════════════════════════════════════════════════════════════════════
#  MODE BATCH — appelé par Generate_pied_jobs_.py::launch_pipeline()
# ══════════════════════════════════════════════════════════════════════════

def main():
    if not BAG_JOBS:
        print("[!] BAG_JOBS est vide — rien à faire.")
        return

    print(f"\n{'═'*70}\n  RUN_FULL_PIPELINE — {len(BAG_JOBS)} job(s)\n{'═'*70}")
    compile_extractor()   # une seule compilation pour tous les jobs qui suivent

    n_ok, n_fail = 0, 0
    for job in BAG_JOBS:
        job_name = job.get("job_name", "job_sans_nom")
        print(f"\n{'─'*70}\n  {job_name}\n{'─'*70}")

        extraction_out = EXTRACTION_OUT_DEFAULT / job_name
        ok, session_name = run_extraction(job, extraction_out, log_cb=print)
        if not ok:
            print(f"[ERREUR] Extraction échouée pour {job_name}")
            n_fail += 1
            continue

        ply_input_dir = extraction_out / session_name / "PointCloud_ply"
        if not ply_input_dir.exists() or not any(ply_input_dir.glob("*.ply")):
            print(f"[ERREUR] Aucun PLY brut trouvé dans {ply_input_dir}")
            n_fail += 1
            continue

        final_session_out = FINAL_OUT_DEFAULT / job_name / session_name
        try:
            run_3d_reconstruction(job, ply_input_dir, final_session_out)
            print(f"[OK] {job_name} → {final_session_out}")
            n_ok += 1
        except Exception as e:
            print(f"[ERREUR] Reconstruction 3D échouée pour {job_name} : {e}")
            n_fail += 1

    print(f"\n{'═'*70}\n  TERMINÉ — {n_ok} succès, {n_fail} échec(s)\n{'═'*70}")


if __name__ == "__main__":
    main()