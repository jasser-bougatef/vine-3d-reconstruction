#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   GENERATE_PIED_JOBS  —  Lambert93 natif + lancement automatique         ║
║                                                                          ║
║  Tous les chemins sont lus depuis pipeline_config.json                   ║
║  Le filtrage background (z_min/z_max) est délégué au C++                 ║
║  — min_distance / max_distance supprimés de DEFAULT_3D                   ║
║                                                                          ║
║  PATCHES ROBUSTESSE (voir CHANGELOG en bas de fichier) :                 ║
║    P1 — Plus long segment contigu (au lieu de min/max global)            ║
║    P2 — Rayon adaptatif par pied (demi-distance au voisin)               ║
║    P4 — Détection de chevauchement inter-pieds                           ║
║    P5 — Marge de sécurité (gap) minimum entre deux pieds voisins         ║
║                                                                          ║
Usage :
    python3 Generate_pied_jobs_.py                      # tout automatique
    python3 Generate_pied_jobs_.py --no-run             # génère sans lancer
    python3 Generate_pied_jobs_.py --radius 0.5         # fenêtre ±50 cm
    python3 Generate_pied_jobs_.py --pass-select 1      # aller seulement
    python3 Generate_pied_jobs_.py --pieds 5 6 7 8      # pieds spécifiques
    python3 Generate_pied_jobs_.py --preview            # affiche, n'exécute pas
    python3 Generate_pied_jobs_.py --frame-gap 20       # marge min. entre pieds (frames)
    python3 Generate_pied_jobs_.py --no-adaptive-radius # revenir au rayon fixe uniforme
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

# ── Chemins stables (ne changent pas entre deux runs) ─────────────────────
PROJECT_ROOT        = Path(_CFG["project_root"])
BAG_INPUT_DIR       = PROJECT_ROOT / _CFG["paths"]["bag_input_dir"]
DEFAULT_SHP         = PROJECT_ROOT / _CFG["paths"]["shapefile"]
DEFAULT_SYN_DIR     = PROJECT_ROOT / _CFG["paths"]["syn_output_dir"]
RUN_PIPELINE_SCRIPT = PROJECT_ROOT / _CFG["paths"]["run_pipeline_script"]

# ── Défaut affiché dans --help (relu au lancement) ────────────────────────
DEFAULT_RADIUS = _CFG["params"]["default_radius"]


def _get_runtime_cfg() -> dict:
    """Relit pipeline_config.json à chaque appel.
    Garantit que toute modification du JSON (rayon, rotations, z_min/z_max…)
    est prise en compte sans relancer Python."""
    return _load_config()


def _make_default_cpp(cfg: dict) -> dict:
    return dict(cfg["params"]["params_extraction"])


def _make_default_3d(cfg: dict) -> dict:
    _f = cfg["params"]["filter_3d"]
    _r = cfg["params"]["registration"]
    return dict(
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
    )


def _make_bag_registry(cfg: dict) -> dict:
    cpp = _make_default_cpp(cfg)
    return {
        date: {
            "bag_file":  str(BAG_INPUT_DIR / entry["bag_file"]),
            "ubx_file":  str(BAG_INPUT_DIR / entry["ubx_file"]),
            "rot_x_deg": entry.get("rot_x_deg", cpp["rot_x_deg"]),
            "rot_y_deg": entry.get("rot_y_deg", cpp["rot_y_deg"]),
            "rot_z_deg": entry.get("rot_z_deg", cpp["rot_z_deg"]),
        }
        for date, entry in cfg["bag_registry"].items()
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — LECTURE DU SHAPEFILE (coordonnées Lambert93 directes)
# ══════════════════════════════════════════════════════════════════════════════

def load_shapefile(shp_path: Path) -> list[tuple[float, float]]:
    """
    Lit le shapefile Lambert93 et retourne [(X_L93, Y_L93), ...].
    Aucune conversion nécessaire — le shapefile est déjà en EPSG:2154.
    """
    try:
        import shapefile
    except ImportError:
        sys.exit("[ERREUR] pyshp requis :  pip install pyshp")

    sf = shapefile.Reader(str(shp_path))
    points = [shape.points[0] for shape in sf.shapes()]
    print(f"  ✔  {len(points)} pieds chargés depuis le shapefile (Lambert93)")
    return points


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — CHARGEMENT & CONVERSION DES CSV SYNCHRONISÉS → LAMBERT93
# ══════════════════════════════════════════════════════════════════════════════

def load_sync_files(syn_dir: Path) -> dict[str, pd.DataFrame]:
    """
    Charge tous les synchronized_DD-MM-YYYY.csv.
    Convertit lat_rtk/lon_rtk (WGS84) → x_l93/y_l93 (Lambert93 EPSG:2154).
    Retourne { 'DD-MM-YYYY': DataFrame }.
    """
    try:
        from pyproj import Transformer
    except ImportError:
        sys.exit("[ERREUR] pyproj requis :  pip install pyproj")

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)

    result = {}
    for csv_path in sorted(syn_dir.glob("synchronized_*.csv")):
        m = re.search(r"synchronized_(\d{2}-\d{2}-\d{4})\.csv$", csv_path.name)
        if not m:
            continue
        label = m.group(1)
        df = pd.read_csv(csv_path)

        x_l93, y_l93 = transformer.transform(
            df["lon_rtk"].values, df["lat_rtk"].values
        )
        df["x_l93"] = x_l93
        df["y_l93"] = y_l93

        result[label] = df
        print(
            f"  ✔  {csv_path.name}  ({len(df)} frames)  "
            f"Y_L93 : {y_l93.min():.1f} → {y_l93.max():.1f} m"
        )

    if not result:
        sys.exit(f"[ERREUR] Aucun synchronized_*.csv dans : {syn_dir}")
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — DÉTECTION DU DEMI-TOUR
# ══════════════════════════════════════════════════════════════════════════════

def detect_turnaround(df: pd.DataFrame) -> int:
    """
    Retourne le numéro de frame au demi-tour de la caméra.
    Le demi-tour = Y_L93 maximum (point le plus au nord le long du rang).
    """
    return int(df["frame_number"].iloc[np.argmax(df["y_l93"].values)])


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — ASSIGNATION DES FRAMES À UN PIED  [PATCH P1]
# ══════════════════════════════════════════════════════════════════════════════

def _longest_contiguous_run(frame_numbers: np.ndarray, max_gap: int = 1):
    """
    [PATCH P1] Retourne (start, end) du plus long segment de frames
    quasi-consécutives, au lieu du min/max global.

    Pourquoi : un point GPS aberrant isolé (bruit RTK/multipath) qui retombe
    dans la fenêtre spatiale Y du pied, mais à une frame très éloignée du
    vrai passage, faisait exploser artificiellement la fenêtre min/max.
    Le plus long segment contigu ignore ces points isolés.

    max_gap=1 : tolère 1 frame manquante à l'intérieur d'un même segment
    (frame_number non strictement continue si une frame a été droppée).
    """
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


def frames_for_pied(
    df: pd.DataFrame,
    pied_y: float,
    radius: float,
    turn_frame: int,
    pass_select: str = "both",
    min_run_frames: int = 3,
    max_gap: int = 1,
) -> dict:
    """[PATCH P1] Sélectionne le plus long segment de frames contiguës dans
    la fenêtre spatiale, au lieu du min/max global (sensible au bruit GPS)."""
    in_window = (df["y_l93"] >= pied_y - radius) & (df["y_l93"] <= pied_y + radius)

    def _range(subset):
        run = _longest_contiguous_run(subset["frame_number"].values, max_gap=max_gap)
        if run is None:
            return None
        start, end = run
        if (end - start + 1) < min_run_frames:
            return None
        return start, end

    pass1_df = df[in_window & (df["frame_number"] <= turn_frame)]
    pass2_df = df[in_window & (df["frame_number"] >= turn_frame)]

    out = {"pass1": None, "pass2": None}
    if pass_select in ("1", "both"):
        out["pass1"] = _range(pass1_df)
    if pass_select in ("2", "both"):
        out["pass2"] = _range(pass2_df)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4b — RAYON ADAPTATIF PAR PIED  [PATCH P2]
# ══════════════════════════════════════════════════════════════════════════════

def compute_adaptive_radii(pieds_l93: list[tuple[float, float]],
                            default_radius: float,
                            safety_factor: float = 0.9) -> list[float]:
    """
    [PATCH P2] Rayon = safety_factor * demi-distance au voisin le plus
    proche, plafonné par default_radius.

    Garantie mathématique d'absence de chevauchement : pour deux voisins
    i, i+1 séparés de d, chacun calcule half_min <= d/2, donc
    r_i + r_{i+1} <= safety_factor * d < d.
    => Aucun chevauchement possible, quelle que soit la densité du rang.
    """
    ys = [y for _, y in pieds_l93]
    n = len(ys)
    radii = []
    for i in range(n):
        half_prev = abs(ys[i] - ys[i - 1]) / 2 if i > 0 else default_radius
        half_next = abs(ys[i + 1] - ys[i]) / 2 if i < n - 1 else default_radius
        half_min = min(half_prev, half_next)
        r = half_min * safety_factor
        radii.append(min(r, default_radius))
    return radii


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4c — VALIDATION POST-ASSIGNATION  [PATCH P4 + P5]
# ══════════════════════════════════════════════════════════════════════════════

def check_overlaps(jobs: list[dict]) -> list[str]:
    """[PATCH P4] Détecte les chevauchements de frames entre pieds voisins
    (même passe/date). Ne modifie rien — sert de diagnostic."""
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


def enforce_frame_gap(jobs: list[dict], min_gap: int = 5,
                       min_run_frames: int = 3) -> list[str]:
    """
    [PATCH P5] Garantit un écart minimum de `min_gap` frames entre la fin
    de la fenêtre d'un pied et le début de la fenêtre du pied suivant
    (même passe/date), en rognant les deux fenêtres de façon symétrique
    autour de leur frontière commune.

    Modifie `jobs` EN PLACE (start_frame / end_frame / _n_frames mis à jour).
    Retourne les avertissements si un rognement ferait passer une fenêtre
    sous min_run_frames (dans ce cas, la paire concernée n'est PAS modifiée
    et doit être vérifiée manuellement).
    """
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
                continue  # déjà assez d'écart, rien à faire

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
                    f"impossible d'imposer {min_gap} frames d'écart sans passer "
                    f"sous min_run_frames={min_run_frames} "
                    f"(fenêtres trop proches/étroites : {a['start_frame']}-{a['end_frame']} "
                    f"vs {b['start_frame']}-{b['end_frame']})"
                )
                continue  # on ne touche pas ces deux jobs, à traiter manuellement

            a["end_frame"]   = new_a_end
            b["start_frame"] = new_b_start
            a["_n_frames"]   = len_a
            b["_n_frames"]   = len_b

    return warnings


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — CONSTRUCTION DES BAG_JOBS
# ══════════════════════════════════════════════════════════════════════════════

def build_bag_jobs(
    pieds_l93: list[tuple[float, float]],
    sync_data: dict[str, pd.DataFrame],
    radius: float,
    pass_select: str,
    pied_filter: list[int] | None,
    use_adaptive_radius: bool = True,   # [PATCH P2]
    frame_gap: int = 5,                # [PATCH P5]
    min_run_frames: int = 2,            # [PATCH P1/P5]
) -> list[dict]:
    # ── Relecture du JSON à chaque appel ─────────────────────────────────
    cfg         = _get_runtime_cfg()
    DEFAULT_CPP = _make_default_cpp(cfg)
    DEFAULT_3D  = _make_default_3d(cfg)
    BAG_REGISTRY = _make_bag_registry(cfg)
    # ─────────────────────────────────────────────────────────────────────

    # ── [PATCH P2] Rayon adaptatif par pied (remplace le rayon uniforme) ──
    if use_adaptive_radius:
        adaptive_radii = compute_adaptive_radii(pieds_l93, radius)
    else:
        adaptive_radii = [radius] * len(pieds_l93)

    jobs = []
    pied_has_job = {i + 1: False for i in range(len(pieds_l93))}

    for date_label, df in sorted(sync_data.items()):
        if date_label not in BAG_REGISTRY:
            print(f"  [!]  Date {date_label} absente du BAG_REGISTRY → ignorée")
            continue

        reg        = BAG_REGISTRY[date_label]
        turn_frame = detect_turnaround(df)
        cam_y_min  = df["y_l93"].min()
        cam_y_max  = df["y_l93"].max()

        print(
            f"\n  ── {date_label}  "
            f"(demi-tour frame {turn_frame}, "
            f"Y_L93 caméra : {cam_y_min:.1f} → {cam_y_max:.1f} m)  ──"
        )

        for idx, (pied_x, pied_y) in enumerate(pieds_l93):
            pied_num = idx + 1
            if pied_filter and pied_num not in pied_filter:
                continue

            pied_radius = adaptive_radii[idx]   # [PATCH P2]

            if pied_y < cam_y_min - pied_radius or pied_y > cam_y_max + pied_radius:
                print(
                    f"  Pied {pied_num:02d} (Y={pied_y:.1f}m) : "
                    f"hors zone caméra [{cam_y_min:.1f}, {cam_y_max:.1f}] m  → non filmé"
                )
                continue

            ranges = frames_for_pied(
                df, pied_y, pied_radius, turn_frame, pass_select,
                min_run_frames=min_run_frames,
            )

            for pass_name, fr_range in ranges.items():
                if fr_range is None:
                    print(
                        f"  Pied {pied_num:02d} {pass_name} : "
                        f"aucune frame dans Y=[{pied_y-pied_radius:.1f}, {pied_y+pied_radius:.1f}]"
                    )
                    continue

                start_fr, end_fr = fr_range
                n_frames  = end_fr - start_fr + 1
                job_name  = f"pied{pied_num:02d}_{pass_name}_{date_label}"

                print(
                    f"  Pied {pied_num:02d} {pass_name} : "
                    f"frames {start_fr}→{end_fr}  ({n_frames} fr)  "
                    f"Y_L93={pied_y:.1f}m  rayon={pied_radius:.3f}m"
                )

                jobs.append({
                    # ── Méta ────────────────────────────────────────────────
                    "_pied_num":   pied_num,
                    "_pass":       pass_name,
                    "_date":       date_label,
                    "_pied_x_l93": round(pied_x, 3),
                    "_pied_y_l93": round(pied_y, 3),
                    "_n_frames":   n_frames,
                    "_radius_used": round(pied_radius, 3),

                    # ── Identifiant ──────────────────────────────────────────
                    "job_name": job_name,

                    # ── Chemins fichiers ─────────────────────────────────────
                    "bag_file": reg["bag_file"],
                    "ubx_file": reg["ubx_file"],

                    # ── Paramètres C++ ───────────────────────────────────────
                    "z_min":       DEFAULT_CPP["z_min"],
                    "z_max":       DEFAULT_CPP["z_max"],
                    "start_frame": start_fr,
                    "end_frame":   end_fr,
                    "rot_x_deg":   reg["rot_x_deg"],
                    "rot_y_deg":   reg["rot_y_deg"],
                    "rot_z_deg":   reg["rot_z_deg"],

                    # ── Paramètres Python 3D (sans min/max_distance) ─────────
                    **DEFAULT_3D,
                })
                pied_has_job[pied_num] = True

    never_filmed = [
        n for n, covered in pied_has_job.items()
        if not covered and (not pied_filter or n in pied_filter)
    ]
    if never_filmed:
        print(
            f"\n  ⚠  Pieds sans aucune frame : {never_filmed}\n"
            f"     Cause : position Y_L93 hors zone d'enregistrement.\n"
            f"     → Augmenter --radius ou vérifier la couverture du .bag."
        )

    # ── [PATCH P4] Diagnostic AVANT correction du gap ─────────────────────
    overlap_warnings_before = check_overlaps(jobs)
    if overlap_warnings_before:
        print(f"\n  ⚠  {len(overlap_warnings_before)} chevauchement(s) AVANT enforce_frame_gap :")
        for w in overlap_warnings_before:
            print(f"    {w}")

    # ── [PATCH P5] Marge de sécurité minimum entre pieds voisins ──────────
    if frame_gap > 0:
        gap_warnings = enforce_frame_gap(jobs, min_gap=frame_gap, min_run_frames=min_run_frames)
        print(f"\n  ✔  Marge de sécurité appliquée : {frame_gap} frames minimum entre pieds voisins")
        if gap_warnings:
            print(f"  ⚠  {len(gap_warnings)} paire(s) trop proches/étroites pour imposer le gap :")
            for w in gap_warnings:
                print(f"    {w}")

    # ── [PATCH P4] Diagnostic FINAL après correction du gap ────────────────
    overlap_warnings_after = check_overlaps(jobs)
    if overlap_warnings_after:
        print(f"\n  ⚠⚠⚠  {len(overlap_warnings_after)} CHEVAUCHEMENT(S) RÉSIDUEL(S) ⚠⚠⚠")
        for w in overlap_warnings_after:
            print(f"    {w}")
        print("  → Ces jobs vont contaminer les nuages de points voisins.")
        print("  → Vérifiez le rayon, la calibration GPS, ou filtrez ces pieds.\n")
    else:
        print("\n  ✔  Aucun chevauchement résiduel entre pieds voisins.")

    return jobs


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — EXPORT CSV RÉCAPITULATIF
# ══════════════════════════════════════════════════════════════════════════════

def export_summary_csv(jobs: list[dict], out_path: Path):
    rows = [
        {
            "job_name":    j["job_name"],
            "pied_num":    j["_pied_num"],
            "pass":        j["_pass"],
            "date":        j["_date"],
            "start_frame": j["start_frame"],
            "end_frame":   j["end_frame"],
            "n_frames":    j["_n_frames"],
            "x_l93_m":     j["_pied_x_l93"],
            "y_l93_m":     j["_pied_y_l93"],
            "radius_used_m": j.get("_radius_used", ""),
        }
        for j in jobs
    ]
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"  ✔  Résumé CSV → {out_path}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — LANCEMENT AUTOMATIQUE DE Run_full_pipeline.py
# ══════════════════════════════════════════════════════════════════════════════

def _strip_meta(job: dict) -> dict:
    return {k: v for k, v in job.items() if not k.startswith("_")}


def launch_pipeline(jobs: list[dict], pipeline_script: Path):
    if not pipeline_script.exists():
        sys.exit(f"[ERREUR] Script pipeline introuvable : {pipeline_script}")

    print(f"\n{'═'*70}")
    print(f"  LANCEMENT AUTOMATIQUE → {pipeline_script.name}")
    print(f"  {len(jobs)} job(s) à exécuter")
    print(f"{'═'*70}\n")

    clean_jobs = [_strip_meta(j) for j in jobs]

    spec = importlib.util.spec_from_file_location("run_full_pipeline", str(pipeline_script))
    mod  = importlib.util.module_from_spec(spec)
    mod.BAG_JOBS = clean_jobs
    spec.loader.exec_module(mod)
    mod.BAG_JOBS = clean_jobs
    mod.main()


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 8 — POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Assigne les frames aux pieds de vigne (calculs en Lambert93)\n"
            "et lance le pipeline 3D automatiquement.\n"
            "Tous les chemins sont lus depuis pipeline_config.json."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--shp",     type=Path, default=DEFAULT_SHP,
                        help=f"Shapefile Lambert93 (défaut : {DEFAULT_SHP})")
    parser.add_argument("--syn-dir", type=Path, default=DEFAULT_SYN_DIR,
                        help=f"Dossier synchronized_*.csv (défaut : {DEFAULT_SYN_DIR})")
    # Relire le JSON ici pour que le défaut --radius soit toujours à jour
    _rt = _get_runtime_cfg()
    _current_radius = _rt["params"]["default_radius"]
    parser.add_argument("--radius",  type=float, default=_current_radius,
                        help=f"Demi-fenêtre Y_L93 en mètres (défaut depuis config : {_current_radius} m). "
                             f"Sert de plafond au rayon adaptatif si --no-adaptive-radius n'est pas utilisé.")
    parser.add_argument("--pass-select", choices=["1", "2", "both"], default="both",
                        help="Passages : aller (1), retour (2), les deux (both, défaut)")
    parser.add_argument("--pieds",   type=int, nargs="+", default=None,
                        help="Numéros de pieds à traiter. Ex: --pieds 5 6 7 8")
    parser.add_argument("--pipeline-script", type=Path, default=RUN_PIPELINE_SCRIPT,
                        help=f"Chemin vers Run_full_pipeline.py (défaut : {RUN_PIPELINE_SCRIPT})")
    parser.add_argument("--no-run",  action="store_true",
                        help="Génère les jobs sans lancer le pipeline")
    parser.add_argument("--preview", action="store_true",
                        help="Affiche les jobs uniquement, n'écrit rien")
    parser.add_argument("--out-csv", type=Path,
                        default=PROJECT_ROOT / _rt["paths"]["pied_jobs_summary"],
                        help="Fichier CSV récapitulatif")
    # ── Nouvelles options (patches robustesse) ─────────────────────────────
    parser.add_argument("--no-adaptive-radius", action="store_true",
                        help="[PATCH P2] Désactive le rayon adaptatif, revient au rayon fixe --radius pour tous les pieds")
    parser.add_argument("--frame-gap", type=int, default=20,
                        help="[PATCH P5] Écart minimum en frames entre deux pieds voisins (défaut : 20). "
                             "Mettre 0 pour désactiver.")
    parser.add_argument("--min-run-frames", type=int, default=3,
                        help="[PATCH P1] Nombre minimum de frames dans un segment pour qu'il soit retenu (défaut : 3)")
    args = parser.parse_args()

    print("═" * 70)
    print("  GENERATE_PIED_JOBS   —  Lambert93 natif + pipeline automatique")
    print("  (avec patches robustesse P1/P2/P4/P5)")
    print("═" * 70)

    print(f"\n[1/3] Lecture du shapefile : {args.shp}")
    pieds_l93 = load_shapefile(args.shp)
    for i, (x, y) in enumerate(pieds_l93):
        print(f"  Pied {i+1:02d}: X_L93={x:.2f}m  Y_L93={y:.2f}m")

    print(f"\n[2/3] Chargement et conversion WGS84 → Lambert93 : {args.syn_dir}")
    sync_data = load_sync_files(args.syn_dir)

    use_adaptive = not args.no_adaptive_radius
    print(
        f"\n[3/3] Assignation  (rayon plafond = ±{args.radius} m, "
        f"rayon adaptatif = {'ON' if use_adaptive else 'OFF'}, "
        f"gap min. = {args.frame_gap} fr, passes = {args.pass_select})"
    )
    jobs = build_bag_jobs(
        pieds_l93, sync_data, args.radius, args.pass_select, args.pieds,
        use_adaptive_radius=use_adaptive,
        frame_gap=args.frame_gap,
        min_run_frames=args.min_run_frames,
    )

    if not jobs:
        print(
            "\n[!] Aucun job généré.\n"
            "    → Essayez --radius 0.6 ou --radius 1.0\n"
            "    → Vérifiez que BAG_REGISTRY contient les dates de vos CSV"
        )
        sys.exit(0)

    print(f"\n  {'─'*76}")
    print(f"  {'JOB NAME':<28} {'FRAMES':>14}   {'N FR':>6}   {'RAYON':>7}")
    print(f"  {'─'*76}")
    for j in jobs:
        print(
            f"  {j['job_name']:<28} "
            f"{j['start_frame']:>6} → {j['end_frame']:<6}  "
            f"{j['_n_frames']:>5} fr   "
            f"{j.get('_radius_used', args.radius):>5.3f}m"
        )
    print(f"  {'─'*76}")
    print(f"  Total : {len(jobs)} job(s)\n")

    if args.preview:
        print("  [--preview] Aucune action exécutée.")
        return

    export_summary_csv(jobs, args.out_csv)

    if args.no_run:
        print(f"\n  [--no-run] Pipeline non lancé.\n  Résumé exporté → {args.out_csv}")
        return

    launch_pipeline(jobs, args.pipeline_script)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
#  CHANGELOG PATCHES ROBUSTESSE
# ══════════════════════════════════════════════════════════════════════════════
#
# P1 — _longest_contiguous_run() / frames_for_pied()
#      Remplace le min/max global par le plus long segment de frames
#      contiguës. Évite qu'un point GPS aberrant isolé dans la fenêtre
#      spatiale du pied ne fasse exploser artificiellement la plage de
#      frames extraites.
#
# P2 — compute_adaptive_radii()
#      Rayon par pied = 0.9 * demi-distance au voisin le plus proche,
#      plafonné par --radius. Garantit mathématiquement l'absence de
#      chevauchement spatial entre fenêtres de pieds voisins, même sur
#      un rang dense où l'espacement varie fortement (0.5m à 1.6m
#      observés sur ce jeu de données).
#
# P4 — check_overlaps()
#      Diagnostic (ne modifie rien) : détecte les paires de jobs dont les
#      plages de frames se chevauchent, appelé avant ET après P5 pour
#      tracer l'effet de la correction.
#
# P5 — enforce_frame_gap()
#      Garantit un écart minimum configurable (--frame-gap, défaut 20
#      frames) entre la fin de la fenêtre d'un pied et le début de celle
#      du suivant, en rognant symétriquement les deux fenêtres. Filet de
#      sécurité supplémentaire au-delà de P2 (qui garantit l'absence de
#      chevauchement mais pas de marge). Si une paire ne peut pas
#      absorber le gap sans passer sous --min-run-frames, elle est
#      signalée mais non modifiée (à vérifier manuellement).
#
# ══════════════════════════════════════════════════════════════════════════════