#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  simulate_robot_scan.py  —  Simulation visuelle du passage du robot      ║
║                                                                          ║
║  Anime la trajectoire GPS (Lambert93) frame par frame, avec :            ║
║    • les cercles de détection de chaque pied (rayon adaptatif, comme     ║
║      dans Generate_pied_jobs_.py — même logique, même valeurs)           ║
║    • un point robot qui se colore quand il entre dans la fenêtre d'un    ║
║      pied (vert = passage 1 / violet = passage 2, gris = hors-pied)      ║
║    • un panneau d'état (frame courante, pied détecté, distance)          ║
║                                                                          ║
║  Usage :                                                                 ║
║    python3 simulate_robot_scan.py                    # dernière date     ║
║    python3 simulate_robot_scan.py --date 04-07-2025                      ║
║    python3 simulate_robot_scan.py --speed 5           # x5 plus rapide   ║
║    python3 simulate_robot_scan.py --speed 20 --loop                      ║
║    python3 simulate_robot_scan.py --save simu.mp4     # export vidéo     ║
║    python3 simulate_robot_scan.py --save simu.gif                        ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.patches import Circle

# ══════════════════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent / "pipeline_config.json"
    if not cfg_path.exists():
        sys.exit(f"[ERREUR] pipeline_config.json introuvable : {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_shapefile(shp_path: Path) -> list[tuple[float, float]]:
    try:
        import shapefile
    except ImportError:
        sys.exit("[ERREUR] pyshp requis : pip install pyshp")
    sf = shapefile.Reader(str(shp_path))
    return [shape.points[0] for shape in sf.shapes()]


def load_sync_csv(csv_path: Path) -> pd.DataFrame:
    from pyproj import Transformer
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2154", always_xy=True)
    df = pd.read_csv(csv_path)
    x, y = transformer.transform(df["lon_rtk"].values, df["lat_rtk"].values)
    df["x_l93"] = x
    df["y_l93"] = y
    return df.sort_values("frame_number").reset_index(drop=True)


def compute_adaptive_radii(pieds_l93, default_radius, safety_factor=0.9) -> list[float]:
    """Même formule que Generate_pied_jobs_.py [PATCH P2] pour que les
    cercles affichés correspondent exactement aux fenêtres réellement
    utilisées lors de l'assignation des frames."""
    ys = [y for _, y in pieds_l93]
    n = len(ys)
    radii = []
    for i in range(n):
        half_prev = abs(ys[i] - ys[i - 1]) / 2 if i > 0 else default_radius
        half_next = abs(ys[i + 1] - ys[i]) / 2 if i < n - 1 else default_radius
        half_min = min(half_prev, half_next)
        radii.append(min(half_min * safety_factor, default_radius))
    return radii


def detect_turnaround(df: pd.DataFrame) -> int:
    return int(df["frame_number"].iloc[np.argmax(df["y_l93"].values)])


def find_pied_at(y: float, pieds_l93, radii):
    for i, (_, py) in enumerate(pieds_l93):
        if abs(y - py) <= radii[i]:
            return i + 1, abs(y - py)
    return None, None


# ══════════════════════════════════════════════════════════════════════════
#  SIMULATION
# ══════════════════════════════════════════════════════════════════════════

def run_simulation(date_label: str, cfg: dict, args):
    project_root = Path(cfg["project_root"])
    shp_path = project_root / cfg["paths"]["shapefile"]
    syn_dir  = project_root / cfg["paths"]["syn_output_dir"]
    radius   = cfg["params"]["default_radius"]

    csv_path = syn_dir / f"synchronized_{date_label}.csv"
    if not csv_path.exists():
        sys.exit(f"[ERREUR] Fichier introuvable : {csv_path}")

    print(f"[SIMU] Chargement pieds  : {shp_path}")
    pieds_l93 = load_shapefile(shp_path)
    radii = compute_adaptive_radii(pieds_l93, radius)

    print(f"[SIMU] Chargement trajectoire : {csv_path.name}")
    df = load_sync_csv(csv_path)
    turn_frame = detect_turnaround(df)

    xs, ys, frames = df["x_l93"].values, df["y_l93"].values, df["frame_number"].values
    n = len(df)
    print(f"[SIMU] {n} positions — demi-tour détecté à la frame {turn_frame}")

    # ── Figure ──────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 10))
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")
    ax.set_title(f"Simulation du scan robot — {date_label}", color="#e8eaf0", fontsize=13)
    ax.set_xlabel("X Lambert93 (m)", color="#6b7a99")
    ax.set_ylabel("Y Lambert93 (m)", color="#6b7a99")
    ax.tick_params(colors="#6b7a99")
    for spine in ax.spines.values():
        spine.set_color("#263050")

    all_x = list(xs) + [p[0] for p in pieds_l93]
    all_y = list(ys) + [p[1] for p in pieds_l93]
    pad_x = (max(all_x) - min(all_x)) * 0.20 + 0.5
    pad_y = (max(all_y) - min(all_y)) * 0.05 + 0.5
    ax.set_xlim(min(all_x) - pad_x, max(all_x) + pad_x)
    ax.set_ylim(min(all_y) - pad_y, max(all_y) + pad_y)
    ax.set_aspect("equal")

    # Cercles des pieds + labels (rayon adaptatif réel)
    for i, (px, py) in enumerate(pieds_l93):
        ax.add_patch(Circle((px, py), radii[i], fill=False, edgecolor="#4e9af1",
                             linewidth=1.2, linestyle="--", alpha=0.7))
        ax.plot(px, py, "+", color="#7eb8f7", markersize=6)
        ax.text(px, py + radii[i] + 0.05, f"P{i+1}", color="#7eb8f7",
                 fontsize=7, ha="center")

    trail_line, = ax.plot([], [], "-", color="#3a4a6a", linewidth=1, alpha=0.8)
    scat_free  = ax.scatter([], [], s=6,  c="#555f7a")                # hors-pied
    scat_pied1 = ax.scatter([], [], s=14, c="#4ade80")                # dans un pied, aller
    scat_pied2 = ax.scatter([], [], s=14, c="#c084fc")                # dans un pied, retour
    robot_dot, = ax.plot([], [], "o", color="#fbbf24", markersize=9,
                          markeredgecolor="white", markeredgewidth=1, zorder=10)
    status_txt = ax.text(0.02, 0.98, "", transform=ax.transAxes, color="#e8eaf0",
                          fontsize=10, va="top", family="monospace",
                          bbox=dict(facecolor="#131929", edgecolor="#263050", pad=6))

    trail_x, trail_y = [], []
    free_x, free_y, p1_x, p1_y, p2_x, p2_y = [], [], [], [], [], []

    def init():
        trail_line.set_data([], [])
        return trail_line, scat_free, scat_pied1, scat_pied2, robot_dot, status_txt

    def update(i):
        x, y, fr = xs[i], ys[i], frames[i]
        trail_x.append(x); trail_y.append(y)
        trail_line.set_data(trail_x, trail_y)

        pied_num, dist = find_pied_at(y, pieds_l93, radii)
        is_pass2 = fr >= turn_frame

        if pied_num is not None:
            if is_pass2:
                p2_x.append(x); p2_y.append(y)
                scat_pied2.set_offsets(np.c_[p2_x, p2_y])
            else:
                p1_x.append(x); p1_y.append(y)
                scat_pied1.set_offsets(np.c_[p1_x, p1_y])
        else:
            free_x.append(x); free_y.append(y)
            scat_free.set_offsets(np.c_[free_x, free_y])

        robot_dot.set_data([x], [y])

        passage  = "PASSAGE 2 (retour)" if is_pass2 else "PASSAGE 1 (aller)"
        pied_str = f"Pied {pied_num:02d}  (Δ={dist:.2f} m)" if pied_num else "— hors pied —"
        status_txt.set_text(
            f"Frame    : {fr}\n"
            f"{passage}\n"
            f"Détection : {pied_str}\n"
            f"Y_L93    : {y:.2f} m"
        )
        return trail_line, scat_free, scat_pied1, scat_pied2, robot_dot, status_txt

    base_interval_ms = 40                                   # vitesse "normale" (x1)
    interval = max(1, int(base_interval_ms / max(args.speed, 0.05)))
    # Au-delà de x5, on sous-échantillonne les frames en plus de réduire l'interval
    step = max(1, int(args.speed // 5)) if args.speed > 5 else 1
    frame_indices = range(0, n, step)

    anim = animation.FuncAnimation(
        fig, update, frames=frame_indices, init_func=init,
        interval=interval, blit=False, repeat=args.loop,
    )

    if args.save:
        out_path = Path(args.save)
        print(f"[SIMU] Export en cours → {out_path}  (peut prendre plusieurs minutes)")
        writer = "ffmpeg" if out_path.suffix.lower() == ".mp4" else "pillow"
        anim.save(str(out_path), writer=writer, fps=max(1, int(1000 / interval)))
        print(f"[SIMU] ✔ Vidéo enregistrée → {out_path}")
    else:
        plt.tight_layout()
        plt.show()


# ══════════════════════════════════════════════════════════════════════════
#  ENTRÉE
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Simulation visuelle du scan robot (trajectoire GPS + cercles pieds)")
    parser.add_argument("--date", type=str, default=None,
                         help="Date DD-MM-YYYY à simuler (défaut : la plus récente disponible)")
    parser.add_argument("--speed", type=float, default=1.0,
                         help="Facteur de vitesse (1=normal, 5=x5, 20=x20...)")
    parser.add_argument("--save", type=str, default=None,
                         help="Chemin .mp4 ou .gif pour exporter au lieu d'afficher")
    parser.add_argument("--loop", action="store_true",
                         help="Boucle l'animation en continu (mode affichage seulement)")
    args = parser.parse_args()

    cfg = load_config()
    project_root = Path(cfg["project_root"])
    syn_dir = project_root / cfg["paths"]["syn_output_dir"]

    if args.date:
        date_label = args.date
    else:
        candidates = sorted(syn_dir.glob("synchronized_*.csv"))
        if not candidates:
            sys.exit(f"[ERREUR] Aucun synchronized_*.csv dans {syn_dir}")
        m = re.search(r"synchronized_(\d{2}-\d{2}-\d{4})\.csv$", candidates[-1].name)
        date_label = m.group(1)

    run_simulation(date_label, cfg, args)


if __name__ == "__main__":
    main()