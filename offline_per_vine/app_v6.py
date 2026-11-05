#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   IMS Bordeaux — Supervision 3D Viticulture  |  app_v3.py               ║
║                                                                          ║
║  Compatible avec la structure de sortie du pipeline :                    ║
║    final_Output/pied{NN}_pass{1|2}_DD-MM-YYYY/                          ║
║              data_DD-MM-YYYY/PointCloud_ply/Pied(s)_merged_*.ply        ║
║                                                                          ║
║  25 pieds par rang — 5 piquetees de 5 pieds                              ║
║  Page 1 : Analyse ciblée — Passage 1 & Passage 2 côte à côte            ║
║            + switcher de date d'acquisition par passage                  ║
║  Page 2 : Suivi multi-temporel — vue par piquetee (5 pieds)              ║
║            Downsample adaptatif pour gérer le volume de points           ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import json
import os
import re
import glob
import shutil
import subprocess
import importlib.util
import time
import copy
from datetime import datetime
import numpy as np
import open3d as o3d
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

config_file = Path(__file__).resolve().parent / "pipeline_config.json"

if not config_file.exists():
    st.error(f"❌ [ERREUR] Le fichier de configuration {config_file} est introuvable.")
    st.stop()

with open(config_file, "r", encoding="utf-8") as f:
    config = json.load(f)

PROJECT_ROOT = Path(config["project_root"])
FINAL_OUTPUT = PROJECT_ROOT / config["paths"]["final_output"]
LOGO_PATH    = config["paths"]["logo_path"]

# ── Chemins additionnels pour la reconstruction à chaud ────────────────────
EXTRACTION_OUTPUT  = PROJECT_ROOT / config["paths"]["extraction_output"]
BUILD_DIR          = PROJECT_ROOT / config["paths"]["build_dir"]
BAG_INPUT_DIR       = PROJECT_ROOT / config["paths"]["bag_input_dir"]
RUN_PIPELINE_SCRIPT = PROJECT_ROOT / config["paths"]["run_pipeline_script"]
PIED_JOBS_CSV       = PROJECT_ROOT / config["paths"]["pied_jobs_summary"]
SYN_OUTPUT_DIR      = PROJECT_ROOT / config["paths"]["syn_output_dir"]
RECON_LOCK_FILE     = PROJECT_ROOT / ".reconstruction.lock"
RECON_LOCK_TIMEOUT_S = 1800   # 30 min — au-delà, on considère le verrou périmé (crash précédent)

N_PIEDS       = 25   # pieds par rang
N_PAR_PIQUETEÉ = 5    # pieds par piquetee → 5 piquetees

# Voxel size utilisé pour le chargement individuel (Page 1)
VOXEL_PIED      = 0.005
# Voxel size utilisé pour la vue piquetee (Page 2) — plus agressif
VOXEL_PIQUETEÉ   = 0.012
# Limite max de points affichés par figure piquetee
MAX_PTS_PIQUETEÉ = 600_000

# ══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG & CSS
# ══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="IMS Bordeaux · Viticulture 3D",
    page_icon="🍇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Thème (mode nuit / jour) ────────────────────────────────────────────────
THEMES = {
    "dark": {
        "bg": "#0f1117", "sidebar_bg": "#161b27", "card_bg": "#1a2035",
        "border": "#263050", "border2": "#232b3e", "text": "#e8eaf0",
        "text_muted": "#6b7a99", "text_light": "#c5d3ef",
        "info_bg": "#131929", "info_text": "#8a9bc0",
    },
    "light": {
        "bg": "#f4f6fb", "sidebar_bg": "#ffffff", "card_bg": "#ffffff",
        "border": "#d7deed", "border2": "#e2e7f0", "text": "#1a2035",
        "text_muted": "#5b6786", "text_light": "#26314e",
        "info_bg": "#eef2fa", "info_text": "#4b5975",
    },
}

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

dark_mode = st.sidebar.toggle(
    "🌙 Mode nuit", value=(st.session_state["theme"] == "dark"))
st.session_state["theme"] = "dark" if dark_mode else "light"

TH = THEMES[st.session_state["theme"]]

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.stApp { background-color: {{BG}}; color: {{TEXT}}; }
[data-testid="stSidebar"] { background: {{SIDEBAR_BG}}; border-right: 1px solid {{BORDER2}}; }

.viti-title {
    font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em;
    background: linear-gradient(120deg, #7eb8f7 0%, #4e9af1 50%, #a78bfa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 0.1rem;
}
.viti-sub { color: {{TEXT_MUTED}}; font-size: 0.82rem; font-weight: 400; margin-bottom: 1.5rem; }

.metric-card {
    background: {{CARD_BG}}; border: 1px solid {{BORDER}}; border-radius: 10px;
    padding: 14px 18px; margin-bottom: 8px;
}
.metric-card .label { font-size: 0.72rem; color: {{TEXT_MUTED}}; text-transform: uppercase;
                      letter-spacing: 0.07em; margin-bottom: 4px; }
.metric-card .value { font-size: 1.1rem; font-weight: 600; color: {{TEXT}};
                      font-family: 'JetBrains Mono', monospace; }
.metric-card .delta { font-size: 0.72rem; color: #4ade80; margin-top: 2px; }

.Piqueteé-label {
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.1em;
    color: #4e9af1; text-transform: uppercase; margin-bottom: 6px;
    border-left: 3px solid #4e9af1; padding-left: 8px; line-height: 2.2;
}

.section-header {
    font-size: 0.78rem; font-weight: 600; color: #4e9af1;
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid {{BORDER2}}; padding-bottom: 6px; margin-bottom: 12px;
}

.badge { display: inline-block; padding: 2px 10px; border-radius: 20px;
         font-size: 0.7rem; font-weight: 600; letter-spacing: 0.05em; }
.badge-ok   { background: #14532d; color: #4ade80; border: 1px solid #166534; }
.badge-miss { background: #3b1515; color: #f87171; border: 1px solid #7f1d1d; }
.badge-sim  { background: #1e2a4a; color: #7eb8f7; border: 1px solid #2d4070; }
.badge-warn { background: #3b2a00; color: #fbbf24; border: 1px solid #78560a; }

.info-box {
    background: {{INFO_BG}}; border: 1px solid {{BORDER}}; border-radius: 8px;
    padding: 12px 16px; font-size: 0.8rem; color: {{INFO_TEXT}}; margin-bottom: 12px;
}
.info-box strong { color: {{TEXT_LIGHT}}; }

.pass-header {
    text-align: center; font-size: 0.82rem; font-weight: 600;
    padding: 8px 0; border-radius: 8px 8px 0 0; margin-bottom: 4px;
}
.pass1-header { background: #1a2d4a; color: #7eb8f7; border: 1px solid #2d4a7a; }
.pass2-header { background: #2d1a3e; color: #c084fc; border: 1px solid #5b2d82; }

.Piqueteé-tab {
    display: inline-block; padding: 6px 18px; border-radius: 20px; margin: 2px;
    font-size: 0.78rem; font-weight: 600; cursor: pointer;
    background: {{CARD_BG}}; border: 1px solid {{BORDER}}; color: {{TEXT_MUTED}};
}
.Piqueteé-tab-active {
    background: #1a2d4a; border: 1px solid #4e9af1; color: #7eb8f7;
}
</style>
"""
for _k, _v in TH.items():
    _CSS = _CSS.replace("{{" + _k.upper() + "}}", _v)
st.markdown(_CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES — DÉTECTION DES PLY
# ══════════════════════════════════════════════════════════════════════════

_JOB_DIR_RE = re.compile(r"^pied(\d{2})_pass([12])_(\d{2}-\d{2}-\d{4})$")


def find_all_dates_for_pied(pied_num: int, pass_num: int) -> list[tuple[str, str]]:
    """Retourne toutes les (date_label, ply_path) disponibles pour (pied, passage).
    Conservée pour compatibilité — préférer scan_available_pieds() qui fait un seul scan."""
    return scan_available_pieds().get(pied_num, {}).get(pass_num, [])


@st.cache_data(show_spinner=False, ttl=300)
def load_ply(path_str: str, voxel_size: float = VOXEL_PIED) -> tuple:
    """Charge un PLY avec downsampling configurable. Retourne (points, colors, status)."""
    try:
        pcd = o3d.io.read_point_cloud(path_str)
        if len(pcd.points) == 0:
            return None, None, "EMPTY"
        pcd = pcd.voxel_down_sample(voxel_size=voxel_size)
        points = np.asarray(pcd.points)
        colors = np.asarray(pcd.colors) if pcd.has_colors() else None
        # Remapping axes : Z = hauteur
        pts = np.zeros_like(points)
        pts[:, 0] = points[:, 0]
        pts[:, 1] = -points[:, 2]
        pts[:, 2] = points[:, 1]
        return pts, colors, "OK"
    except Exception as e:
        return None, None, f"ERROR:{e}"


@st.cache_data(show_spinner=False, ttl=60)
def scan_available_pieds() -> dict:
    """
    {pied_num: {pass_num: [(date_label, ply_path), ...] }}
    Un seul parcours de final_Output/ (au lieu de 25×2 glob séparés) — les dossiers
    sont triés par nom décroissant donc la date la plus récente arrive en premier.
    """
    result = {p: {1: [], 2: []} for p in range(1, N_PIEDS + 1)}
    if not FINAL_OUTPUT.exists():
        return result

    for job_dir in sorted(FINAL_OUTPUT.iterdir(), key=lambda p: p.name, reverse=True):
        if not job_dir.is_dir():
            continue
        m = _JOB_DIR_RE.match(job_dir.name)
        if not m:
            continue
        pied_num, pass_num, date_label = int(m.group(1)), int(m.group(2)), m.group(3)
        if pied_num not in result:
            continue
        data_candidates = list(job_dir.glob("data_*"))
        if not data_candidates:
            continue
        data_dir = data_candidates[0] / "PointCloud_ply"
        if not data_dir.exists():
            continue
        plys = list(data_dir.glob("Pied(s)_merged_*.ply")) + list(data_dir.glob("*.ply"))
        if plys:
            result[pied_num][pass_num].append((date_label, str(plys[0])))

    return result



def get_session_metadata(avail: dict) -> dict:
    all_dates = set()
    total_jobs = 0
    for p in avail.values():
        for dates_list in p.values():
            if dates_list:
                total_jobs += 1
                for d, _ in dates_list:
                    all_dates.add(d)
    csv_path = PROJECT_ROOT / "pied_jobs_summary.csv"
    n_frames = "—"
    if csv_path.exists():
        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            if "n_frames" in df.columns:
                n_frames = int(df["n_frames"].sum())
        except Exception:
            pass
    return {
        "dates":      sorted(all_dates) if all_dates else ["—"],
        "total_jobs": total_jobs,
        "n_frames":   n_frames,
        "sensor":     "Intel RealSense D4xx RGB-D",
        "crs":        "Lambert93 / EPSG:2154",
        "pipeline":   "PPK → Sync → DBSCAN → ICP/RANSAC",
    }


def make_3d_figure(points: np.ndarray, colors, title: str = "",
                   height: int = 420, marker_size: float = 1.8) -> go.Figure:
    if colors is not None:
        color_vals = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r, g, b in colors]
    else:
        z = points[:, 2]
        z_range = float(np.ptp(z))
        z_norm = (z - z.min()) / (z_range + 1e-9)
        color_vals = z_norm

    fig = go.Figure(data=[go.Scatter3d(
        x=points[:, 0], y=points[:, 1], z=points[:, 2],
        mode='markers',
        marker=dict(
            size=marker_size,
            color=color_vals,
            colorscale='Viridis' if colors is None else None,
            opacity=0.9,
        ),
        hovertemplate='x: %{x:.3f}<br>y: %{y:.3f}<br>z: %{z:.3f}<extra></extra>',
    )])
    fig.update_layout(
        margin=dict(l=0, r=0, b=0, t=30),
        title=dict(text=title, font=dict(color=TH["text_light"], size=12), x=0.04),
        paper_bgcolor=TH["bg"],
        scene=dict(
            bgcolor=TH["bg"],
            xaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title='X'),
            yaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title='Y'),
            zaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title='Z'),
            aspectmode='data',
            camera=dict(eye=dict(x=0, y=-2.0, z=0.5), up=dict(x=0, y=0, z=1)),
        ),
        height=height,
    )
    return fig


def pied_metrics(points: np.ndarray) -> dict:
    if points is None or len(points) == 0:
        return {}
    return {
        "n_points":   len(points),
        "hauteur":    round(float(points[:, 2].max() - points[:, 2].min()), 3),
        "largeur":    round(float(np.ptp(points[:, 0])), 3),
        "profondeur": round(float(np.ptp(points[:, 1])), 3),
        "z_max":      round(float(points[:, 2].max()), 3),
    }


def random_subsample(pts: np.ndarray, cols, max_pts: int):
    """Sous-échantillonnage aléatoire si le nombre de points dépasse max_pts."""
    if len(pts) <= max_pts:
        return pts, cols
    idx = np.random.choice(len(pts), max_pts, replace=False)
    idx.sort()
    return pts[idx], cols[idx] if cols is not None else None


# ══════════════════════════════════════════════════════════════════════════
# QUALITÉ GPS (Q=1 fixe / Q=2 float) — depuis synchronized_DD-MM-YYYY.csv
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False, ttl=60)
def get_gps_quality_for_job(job_name: str, date_label: str):
    """% de frames en fix (Q=1) / float (Q=2) pour les frames du job, ou None si indisponible."""
    syn_csv = SYN_OUTPUT_DIR / f"synchronized_{date_label}.csv"
    if not syn_csv.exists():
        return None
    frange = get_job_frame_range(job_name)
    if frange is None:
        return None
    start_fr, end_fr = frange
    try:
        df = pd.read_csv(syn_csv, usecols=["frame_number", "gps_Q"])
    except Exception:
        return None
    sub = df[(df["frame_number"] >= start_fr) & (df["frame_number"] <= end_fr)]
    if sub.empty:
        return None
    n = len(sub)
    return {
        "n":         n,
        "pct_fix":   round(100 * (sub["gps_Q"] == 1).sum() / n, 1),
        "pct_float": round(100 * (sub["gps_Q"] == 2).sum() / n, 1),
        "q_mean":    round(float(sub["gps_Q"].mean()), 2),
    }


@st.cache_data(show_spinner=False, ttl=120)
def get_global_gps_quality():
    """Qualité GPS agrégée sur tous les synchronized_*.csv disponibles."""
    files = list(SYN_OUTPUT_DIR.glob("synchronized_*.csv")) if SYN_OUTPUT_DIR.exists() else []
    if not files:
        return None
    parts = []
    for f in files:
        try:
            parts.append(pd.read_csv(f, usecols=["gps_Q"]))
        except Exception:
            continue
    if not parts:
        return None
    allq = pd.concat(parts)["gps_Q"]
    n = len(allq)
    if n == 0:
        return None
    return {
        "n":         n,
        "pct_fix":   round(100 * (allq == 1).sum() / n, 1),
        "pct_float": round(100 * (allq == 2).sum() / n, 1),
    }


def _pied_quality_pct(avail: dict, pied_num: int):
    """% fixe moyen (dernière date dispo) pour un pied, sur les passages disponibles."""
    scores = []
    for pas in (1, 2):
        dates_list = avail.get(pied_num, {}).get(pas, [])
        if not dates_list:
            continue
        date_label, _ = dates_list[0]   # date la plus récente
        job_name = f"pied{pied_num:02d}_pass{pas}_{date_label}"
        q = get_gps_quality_for_job(job_name, date_label)
        if q:
            scores.append(q["pct_fix"])
    return round(sum(scores) / len(scores), 1) if scores else None


# ══════════════════════════════════════════════════════════════════════════
# APERÇU RGB — frame du milieu de l'extraction brute
# ══════════════════════════════════════════════════════════════════════════

def find_preview_image(pied_num: int, pass_num: int, date_label: str):
    """Chemin de la frame RGB du milieu pour (pied, passage, date), ou None."""
    job_dir = EXTRACTION_OUTPUT / f"pied{pied_num:02d}_pass{pass_num}_{date_label}"
    if not job_dir.exists():
        return None
    data_candidates = list(job_dir.glob("data_*"))
    if not data_candidates:
        return None
    rgb_dir = data_candidates[0] / "RGB_image"
    if not rgb_dir.exists():
        return None
    def _frame_idx(p):
        m = re.search(r"frame_(\d+)_rgb", p.name)
        return int(m.group(1)) if m else 0
    imgs = sorted(rgb_dir.glob("frame_*_rgb.png"), key=_frame_idx)
    return str(imgs[len(imgs) // 2]) if imgs else None


# ══════════════════════════════════════════════════════════════════════════
# TRAÇABILITÉ — paramètres utilisés pour générer un PLY final
# ══════════════════════════════════════════════════════════════════════════

def load_reconstruction_params(ply_path: str):
    """Lit reconstruction_params.json déposé à côté du PLY final (s'il existe)."""
    p = Path(ply_path).parent.parent / "reconstruction_params.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════
# VERROU DE RECONSTRUCTION — évite deux reconstructions concurrentes
# (le dossier build/ et auto_extractor.cpp sont partagés entre sessions)
# ══════════════════════════════════════════════════════════════════════════

def _acquire_recon_lock(job_name: str) -> tuple[bool, str]:
    if RECON_LOCK_FILE.exists():
        try:
            info = json.loads(RECON_LOCK_FILE.read_text(encoding="utf-8"))
            age_s = time.time() - info.get("ts", 0)
        except Exception:
            info, age_s = {}, RECON_LOCK_TIMEOUT_S + 1
        if age_s < RECON_LOCK_TIMEOUT_S:
            return False, (f"Une reconstruction est déjà en cours : {info.get('job_name', '?')} "
                            f"(depuis {int(age_s)}s). Réessaie plus tard.")
        # verrou périmé (crash / kill précédent) → on le remplace
    RECON_LOCK_FILE.write_text(json.dumps({"job_name": job_name, "ts": time.time()}), encoding="utf-8")
    return True, ""


def _release_recon_lock():
    try:
        RECON_LOCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════
# VUE SUPERPOSÉE P1/P2 — alignement ICP pour affichage uniquement
# ══════════════════════════════════════════════════════════════════════════

def _load_pipeline3d_module():
    spec = importlib.util.spec_from_file_location(
        "pipeline3d_live", str(PROJECT_ROOT / "all_pipeline_3D.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@st.cache_data(show_spinner=False, ttl=300)
def align_pass2_on_pass1(ply_path_p1: str, ply_path_p2: str):
    """
    Aligne (RANSAC + ICP) le nuage final du passage 2 sur celui du passage 1.
    Affichage uniquement — ne modifie aucun fichier. Les deux passages ont chacun
    leur propre repère local (origine = première frame de leur registration),
    donc ils ne se superposent pas naturellement sans cet alignement.
    """
    try:
        p3d = _load_pipeline3d_module()
        pcd1 = o3d.io.read_point_cloud(ply_path_p1)
        pcd2 = o3d.io.read_point_cloud(ply_path_p2)
        if len(pcd1.points) == 0 or len(pcd2.points) == 0:
            return None, "Nuage vide."

        helper = p3d.SlidingWindowRegistration(
            output_ply_dir=".", output_trans_dir=".", voxel_size_reg=0.03)
        src_down, tgt_down, src_fpfh, tgt_fpfh = helper._prepare_dataset(pcd2, pcd1)
        res_global = helper._execute_global_registration(src_down, tgt_down, src_fpfh, tgt_fpfh)
        res_local  = helper._refine_registration(pcd2, pcd1, res_global.transformation)

        pcd2_aligned = o3d.io.read_point_cloud(ply_path_p2).transform(res_local.transformation)
        pts = np.asarray(pcd2_aligned.points)
        out = np.zeros_like(pts)
        out[:, 0] = pts[:, 0]
        out[:, 1] = -pts[:, 2]
        out[:, 2] = pts[:, 1]
        return out, None
    except Exception as e:
        return None, f"Alignement ICP échoué : {e}"


# ══════════════════════════════════════════════════════════════════════════
# SSGLR — recalage inter-pieds d'un PIQUETÉE (version sûre)
#
# Le RANSAC global de all_pipeline_3D.SlidingWindowRegistration marche bien
# pour fusionner des frames CONSÉCUTIVES d'un même pied (fort recouvrement,
# mouvement de caméra continu). Il est en revanche dangereux entre 5 pieds
# DIFFÉRENTS : les troncs se ressemblent trop (features FPFH quasi identiques)
# → RANSAC matche parfois le tronc du pied 3 sur celui du pied 1 → les nuages
# s'effondrent les uns sur les autres ("boule").
#
# Méthode ici : chaque pied est d'abord placé à sa distance RÉELLE le long du
# rang (shapefile pos_pied.shp, ou espacement par défaut réglable), donc les
# pieds restent TOUJOURS séparés même si l'affinement échoue. Un ICP local
# (pas de RANSAC, pas de recherche globale) affine ensuite chaque pied par
# rapport à son voisin déjà placé, avec une distance de recherche bornée et
# un garde-fou : si l'ICP propose une correction énorme ou peu fiable
# (fitness faible), elle est rejetée et l'espacement brut est conservé.
# ══════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def load_pied_positions():
    """Positions Lambert93 (X, Y) des pieds, indexées 1..N, depuis pos_pied.shp. None si indisponible."""
    shp_key = config.get("paths", {}).get("shapefile")
    if not shp_key:
        return None
    shp_path = PROJECT_ROOT / shp_key
    if not shp_path.exists():
        return None
    try:
        import shapefile
        sf = shapefile.Reader(str(shp_path))
        pts = [s.points[0] for s in sf.shapes()]
        return {i + 1: (float(x), float(y)) for i, (x, y) in enumerate(pts)}
    except Exception:
        return None


def default_pied_spacing(pied_range) -> float:
    """Distance moyenne réelle (m) entre pieds consécutifs du piquetée (shapefile), sinon 1.2 m."""
    positions = load_pied_positions()
    if not positions:
        return 1.2
    dists, prev = [], None
    for p in pied_range:
        pos = positions.get(p)
        if pos and prev is not None:
            dists.append(((pos[0] - prev[0]) ** 2 + (pos[1] - prev[1]) ** 2) ** 0.5)
        if pos:
            prev = pos
    return round(sum(dists) / len(dists), 2) if dists else 1.2


def run_ssglr_on_piquetee(avail: dict, pied_range, date_label: str, passes_to_show: list,
                          piquetee_num: int, spacing_m: float = 1.2,
                          icp_max_dist: float = 0.10, use_icp_refine: bool = True,
                          status=None) -> tuple[bool, str, str | None]:
    """
    Recale les nuages finaux des pieds d'un piquetée les uns par rapport aux
    autres (espacement réel + ICP local borné et validé — PAS de RANSAC
    global) et écrit le résultat dans
    FINAL_OUTPUT/Piqueteé_SSGLR/piquetee{N}_{date}/.
    Ne touche à aucun fichier existant des pieds individuels.
    Retourne (succès, message, chemin_ply_résultat | None).
    """
    def _log(msg: str):
        if status is not None:
            status.write(msg)

    lock_ok, lock_msg = _acquire_recon_lock(f"ssglr_piquetee{piquetee_num}_{date_label}")
    if not lock_ok:
        return False, lock_msg, None

    try:
        clouds, included = [], []
        for pied_num in pied_range:
            pied_clouds = []
            for pas in passes_to_show:
                match = [(d, p) for d, p in avail.get(pied_num, {}).get(pas, []) if d == date_label]
                if not match:
                    continue
                _, ply_path = match[0]
                pcd = o3d.io.read_point_cloud(ply_path)
                if len(pcd.points) > 0:
                    pied_clouds.append(pcd)
            if not pied_clouds:
                continue
            merged = pied_clouds[0]
            for c in pied_clouds[1:]:
                merged += c
            clouds.append(merged)
            included.append(pied_num)

        if len(clouds) < 2:
            return False, f"Pas assez de pieds avec données pour ce piquetée ({len(clouds)}/5) — il en faut au moins 2.", None

        _log(f"Pieds inclus : {included} ({len(clouds)}/5) — espacement initial {spacing_m} m/pied")

        aligned = [clouds[0]]
        cum_offset = 0.0
        for i in range(1, len(clouds)):
            cum_offset += spacing_m
            init_T = np.eye(4)
            init_T[0, 3] = cum_offset   # décalage le long de l'axe X local, comme l'affichage normal

            source = clouds[i]
            target = aligned[-1]        # recalage par rapport au voisin réel, pas au pied 1

            final_T = init_T
            if use_icp_refine:
                try:
                    src_tmp = copy.deepcopy(source).transform(init_T)
                    src_tmp.estimate_normals(
                        o3d.geometry.KDTreeSearchParamHybrid(radius=icp_max_dist * 2, max_nn=30))
                    tgt_tmp = copy.deepcopy(target)
                    tgt_tmp.estimate_normals(
                        o3d.geometry.KDTreeSearchParamHybrid(radius=icp_max_dist * 2, max_nn=30))
                    result = o3d.pipelines.registration.registration_icp(
                        src_tmp, tgt_tmp, icp_max_dist, np.eye(4),
                        o3d.pipelines.registration.TransformationEstimationPointToPlane())
                    extra_t = float(np.linalg.norm(result.transformation[:3, 3]))
                    # Garde-fou : on ne garde la correction ICP que si elle est fiable ET raisonnable
                    if result.fitness > 0.3 and extra_t < min(icp_max_dist * 3, spacing_m * 0.5):
                        final_T = result.transformation @ init_T
                        _log(f"  Pied {included[i]} : ICP appliqué "
                             f"(fitness={result.fitness:.2f}, correction={extra_t*100:.1f} cm)")
                    else:
                        _log(f"  Pied {included[i]} : ICP rejeté "
                             f"(fitness={result.fitness:.2f}, correction={extra_t*100:.1f} cm) "
                             f"— espacement brut conservé")
                except Exception as e:
                    _log(f"  Pied {included[i]} : ICP échoué ({e}) — espacement brut conservé")
            else:
                _log(f"  Pied {included[i]} : espacement brut (pas d'ICP demandé)")

            aligned.append(copy.deepcopy(source).transform(final_T))

        _log("Fusion des nuages alignés…")
        merged = aligned[0]
        for c in aligned[1:]:
            merged += c
        merge_voxel = config["params"]["registration"].get("voxel_size_merge", 0.005)
        if merge_voxel > 0:
            merged = merged.voxel_down_sample(merge_voxel)

        output_dir = FINAL_OUTPUT / "Piqueteé_SSGLR" / f"piquetee{piquetee_num}_{date_label}"
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        out_path = output_dir / f"Pied(s)_merged_piquetee{piquetee_num}_{date_label.replace('-', '_')}.ply"
        o3d.io.write_point_cloud(str(out_path), merged)

        try:
            (output_dir / "ssglr_info.json").write_text(json.dumps({
                "generated_at":   datetime.now().isoformat(timespec="seconds"),
                "piquetee":       piquetee_num,
                "date":           date_label,
                "pieds_inclus":   included,
                "passes":         passes_to_show,
                "spacing_m":      spacing_m,
                "icp_max_dist_m": icp_max_dist,
                "icp_refine":     use_icp_refine,
                "methode":        "espacement réel + ICP local borné (pas de RANSAC global)",
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

        _log("✔ Terminé.")
        return True, f"✔ Piquetée homogénéisé — {len(included)} pieds alignés : {included}", str(out_path)

    except Exception as e:
        return False, f"SSGLR échoué : {e}", None
    finally:
        _release_recon_lock()


# ══════════════════════════════════════════════════════════════════════════
# RECONSTRUCTION À CHAUD — réutilise Run_full_pipeline.py / all_pipeline_3D.py
# ══════════════════════════════════════════════════════════════════════════

def _cpp_defaults() -> dict:
    p = config["params"]
    return p.get("params_extraction") 


def get_bag_registry_entry(date_label: str):
    return config.get("bag_registry", {}).get(date_label)


@st.cache_data(show_spinner=False, ttl=30)
def get_job_frame_range(job_name: str):
    """Lit start_frame / end_frame pour ce job dans pied_jobs_summary.csv."""
    if not PIED_JOBS_CSV.exists():
        return None
    try:
        df = pd.read_csv(PIED_JOBS_CSV)
    except Exception:
        return None
    row = df[df["job_name"] == job_name]
    if row.empty:
        return None
    return int(row.iloc[0]["start_frame"]), int(row.iloc[0]["end_frame"])


def _load_run_pipeline_module():
    """Charge Run_full_pipeline.py comme module (mêmes fonctions que le pipeline batch)."""
    if not RUN_PIPELINE_SCRIPT.exists():
        raise FileNotFoundError(f"Script pipeline introuvable : {RUN_PIPELINE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("run_full_pipeline_live", str(RUN_PIPELINE_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rebuild_construction(pied_num: int, pass_num: int, date_label: str,
                         cpp_over: dict, f3d_over: dict, reg_over: dict,
                         redo_extraction: bool, status=None) -> tuple[bool, str]:
    """
    Relance la reconstruction pour (pied, passage, date) avec les paramètres
    fournis. Supprime l'ancienne extraction/construction avant de régénérer.
    Fonctionne aussi si le pied n'a jamais été construit (redo_extraction est
    alors forcé automatiquement, faute de PLY bruts existants à réutiliser).
    `status` est un conteneur st.status() optionnel pour afficher les logs en direct.
    Retourne (succès, message).
    """
    def _log(msg: str):
        if status is not None:
            status.write(msg)

    job_name = f"pied{pied_num:02d}_pass{pass_num}_{date_label}"

    reg_entry = get_bag_registry_entry(date_label)
    if reg_entry is None:
        return False, f"Date {date_label} absente de 'bag_registry' dans pipeline_config.json."

    frange = get_job_frame_range(job_name)
    if frange is None:
        return False, (f"Frames introuvables pour {job_name} dans {PIED_JOBS_CSV.name}. "
                        f"Ce pied n'a jamais été assigné pour cette date — relance "
                        f"launch_pipeline.py (étape 4) au moins une fois pour ce .bag.")
    start_fr, end_fr = frange

    # --- AJUSTEMENT DES FRAMES (décalage + nombre d'images forcé) ---
    # .get() (pas .pop()) pour ne jamais muter le dict passé par l'appelant
    offset = cpp_over.get("frame_offset", 0)
    count  = cpp_over.get("frame_count", 0)
    cpp_over = {k: v for k, v in cpp_over.items() if k not in ("frame_offset", "frame_count")}

    # 1. On applique le décalage
    start_fr = max(1, start_fr + offset)

    # 2. On définit la fin selon le nombre d'images voulu (bornes incluses,
    #    comme dans auto_extractor.cpp : start..end inclus → count images = end - start + 1)
    if count > 0:
        end_fr = start_fr + count - 1
    else:
        end_fr = end_fr + offset  # pas de count forcé → on décale juste la fin de la même valeur
    # ------------------------------------------------

    extraction_job_out = EXTRACTION_OUTPUT / job_name
    final_job_out       = FINAL_OUTPUT       / job_name

    already_extracted = any((extraction_job_out).glob("data_*")) if extraction_job_out.exists() else False
    
    # CRITIQUE : On force l'extraction si l'utilisateur applique un décalage ou modifie le compte,
    # pour empêcher le script de charger les anciens nuages de points obsolètes.
    force_extraction = redo_extraction or not already_extracted or (offset != 0) or (count > 0)

    job = {
        "job_name":    job_name,
        "bag_file":    str(BAG_INPUT_DIR / reg_entry["bag_file"]),
        "ubx_file":    str(BAG_INPUT_DIR / reg_entry["ubx_file"]),
        "start_frame": start_fr,
        "end_frame":   end_fr,
        **cpp_over,
        **f3d_over,
        **reg_over,
    }

    lock_ok, lock_msg = _acquire_recon_lock(job_name)
    if not lock_ok:
        return False, lock_msg

    try:
        try:
            mod = _load_run_pipeline_module()
        except Exception as e:
            return False, f"Impossible de charger {RUN_PIPELINE_SCRIPT.name} : {e}"

        if force_extraction:
            if not already_extracted:
                _log(f"ℹ️ {job_name} n'a jamais été construit — extraction complète nécessaire.")
            # Nettoyage de l'ancienne extraction avant de relancer le C++
            if extraction_job_out.exists():
                shutil.rmtree(extraction_job_out)
            extraction_job_out.mkdir(parents=True, exist_ok=True)

            # Compilation UNE SEULE FOIS (no-op si le binaire est déjà à jour) —
            # plus de génération de .cpp par job avec le nouvel extracteur
            # multithreadé : les paramètres passent en arguments CLI.
            _log("① Vérification du binaire auto_extractor (compilé une seule fois)…")
            try:
                mod.compile_extractor(mod.BUILD_DIR)
            except SystemExit as e:
                return False, f"Compilation C++ échouée : {e}"

            _log(f"② Extraction en cours — frames {start_fr} → {end_fr}…")
            log_box = status.empty() if status is not None else None
            tail = []

            def _stream_line(line: str):
                tail.append(line)
                del tail[:-8]
                if log_box is not None:
                    log_box.code("\n".join(tail))

            ok_extract, session_name = mod.run_extraction(job, extraction_job_out, log_cb=_stream_line)
            if not ok_extract:
                return False, "Extraction C++ échouée (voir logs ci-dessus)."
        else:
            existing = sorted(extraction_job_out.glob("data_*"))
            session_name = existing[-1].name

        ply_input_dir = extraction_job_out / session_name / "PointCloud_ply"
        if not ply_input_dir.exists() or not any(ply_input_dir.glob("*.ply")):
            return False, f"Aucun PLY brut trouvé dans {ply_input_dir}."

        # Suppression de l'ancienne construction 3D avant de régénérer
        final_session_out = final_job_out / session_name
        if final_session_out.exists():
            shutil.rmtree(final_session_out)
        final_session_out.mkdir(parents=True, exist_ok=True)

        _log("④ Filtrage (DBSCAN / outliers) + Registration ICP/RANSAC…")
        mod.run_3d_reconstruction(job, ply_input_dir, final_session_out)

        result_ply = list((final_session_out / "PointCloud_ply").glob("Pied(s)_merged_*.ply"))
        if not result_ply:
            return False, ("Reconstruction terminée mais aucun PLY final généré "
                           "— assouplis les paramètres de filtrage (eps, min_cluster_size…).")

        # ── Traçabilité : sauvegarde des paramètres utilisés ────────────────
        try:
            params_record = {
                "generated_at":    datetime.now().isoformat(timespec="seconds"),
                "job_name":        job_name,
                "pied_num":        pied_num,
                "pass_num":        pass_num,
                "date_label":      date_label,
                "redo_extraction": force_extraction,
                "extraction":      cpp_over,
                "filter_3d":       f3d_over,
                "registration":    reg_over,
            }
            (final_session_out / "reconstruction_params.json").write_text(
                json.dumps(params_record, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass   # non bloquant

        _log("✔ Terminé.")
        return True, f"✔ Reconstruction terminée → {result_ply[0].name}"

    finally:
        _release_recon_lock()


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════

avail = scan_available_pieds()
meta  = get_session_metadata(avail)

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=220)
    else:
        st.markdown("### 🍇 IMS Bordeaux")

    st.markdown("---")
    st.markdown("**🧭 Navigation**")
    page = st.radio(
        "Navigation",
        ["🔍 Analyse par Pied (Passages 1 & 2)", "📈 Suivi Multi-Temporel"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown('<div class="section-header">Informations Session</div>', unsafe_allow_html=True)
    dates_str = "<br>".join(f"• {d}" for d in meta["dates"]) if meta["dates"] != ["—"] else "—"
    st.markdown(f"""
<div class="info-box">
<strong>📅 Dates d'acquisition</strong><br>
{dates_str}<br><br>
<strong>🤖 Capteur</strong><br>
{meta["sensor"]}<br><br>
<strong>🗺️ CRS</strong><br>
{meta["crs"]}<br><br>
<strong>⚙️ Pipeline</strong><br>
{meta["pipeline"]}
</div>
""", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1:
        st.metric("Jobs OK", meta["total_jobs"])
    with c2:
        st.metric("Pieds", N_PIEDS)

    st.markdown("---")
    st.markdown('<div class="section-header">Qualité GPS (PPK)</div>', unsafe_allow_html=True)
    _gq = get_global_gps_quality()
    if _gq is None:
        st.caption("Aucun synchronized_*.csv trouvé.")
    elif _gq["pct_fix"] == 0:
        st.markdown(
            f'<span class="badge badge-miss">⚠ 0% fixe — 100% float ({_gq["n"]} pts GPS)</span>',
            unsafe_allow_html=True)
        st.caption("Résolution d'ambiguïté PPK en échec systématique — voir rtkpost.conf.")
    elif _gq["pct_fix"] < 50:
        st.markdown(
            f'<span class="badge badge-warn">{_gq["pct_fix"]}% fixe / {_gq["pct_float"]}% float</span>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            f'<span class="badge badge-ok">{_gq["pct_fix"]}% fixe / {_gq["pct_float"]}% float</span>',
            unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div class="section-header">Paramètres recommandé</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="info-box" style="font-family:'JetBrains Mono',monospace;font-size:0.73rem;">
<strong>C++ Extractor</strong><br>
z_min=0.4 m · z_max=1.0 m<br>
rot_z=270°<br><br>
<strong>Filtre 3D</strong><br>
voxel=0.005 m · eps=0.12<br>
DBSCAN · radius_filter=True<br><br>
<strong>Registration</strong><br>
RANSAC + ICP PointToPlane<br>
window_size=4 frames
</div>
""", unsafe_allow_html=True)

    st.markdown("---")
    st.caption("IMS Bordeaux — Équipe MOTIVE")



# ══════════════════════════════════════════════════════════════════════════
# PAGE 1 — ANALYSE PAR PIED
# ══════════════════════════════════════════════════════════════════════════

if page == "🔍 Analyse par Pied (Passages 1 & 2)":
    st.markdown('<div class="viti-title">Analyse Géométrique par Pied de Vigne</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="viti-sub">Sélectionnez un pied dans la matrice · '
        'Choisissez la date d\'acquisition · Passage 1 (aller) et Passage 2 (retour) côte à côte</div>',
        unsafe_allow_html=True)

    # ── Session state ────────────────────────────────────────────────────
    if "sel_pied" not in st.session_state:
        _default_pied = next(
            (p for p in range(1, N_PIEDS + 1) if avail[p][1] or avail[p][2]),
            1,   # aucun pied construit → on retombe sur le pied 1
        )
        st.session_state["sel_pied"] = _default_pied
    if "sel_date_p1" not in st.session_state:
        st.session_state["sel_date_p1"] = None
    if "sel_date_p2" not in st.session_state:
        st.session_state["sel_date_p2"] = None

    sel_pied = st.session_state["sel_pied"]

    # ── Matrice des piquetees ─────────────────────────────────────────────
    st.markdown('<div class="section-header">Sélection du Pied — 5 Piqueteés × 5 Pieds</div>',
                unsafe_allow_html=True)

    for piquetee in range(1, 6):
        pied_start = (piquetee - 1) * N_PAR_PIQUETEÉ + 1
        pied_end   = piquetee * N_PAR_PIQUETEÉ
        piquetee_cols = st.columns([1.1] + [1] * N_PAR_PIQUETEÉ)

        with piquetee_cols[0]:
            st.markdown(f'<div class="Piqueteé-label">Piqueteé {piquetee}</div>',
                        unsafe_allow_html=True)

        for i, pied_num in enumerate(range(pied_start, pied_end + 1)):
            with piquetee_cols[i + 1]:
                p1_dates = avail[pied_num][1]
                p2_dates = avail[pied_num][2]
                icon = "🟢" if (p1_dates and p2_dates) else ("🟡" if (p1_dates or p2_dates) else "⚫")
                is_sel = (pied_num == sel_pied)
                label  = f"{'▶' if is_sel else ''}{icon} P{pied_num}"
                if st.button(label, key=f"btn_{pied_num}", width="stretch",
                             type="primary" if is_sel else "secondary"):
                    st.session_state["sel_pied"] = pied_num
                    st.session_state["sel_date_p1"] = None
                    st.session_state["sel_date_p2"] = None
                    st.rerun()

    st.caption("🟢 P1 & P2 disponibles  |  🟡 Un seul passage  |  ⚫ Non filmé")
    st.markdown("---")

    piquetee_num    = ((sel_pied - 1) // N_PAR_PIQUETEÉ) + 1
    pos_in_piquetee = ((sel_pied - 1) % N_PAR_PIQUETEÉ) + 1
    p1_dates_list   = avail[sel_pied][1]
    p2_dates_list   = avail[sel_pied][2]

    st.markdown(
        f'<div class="section-header">Pied n°{sel_pied} — '
        f'Piqueteé {piquetee_num} · Position {pos_in_piquetee}/{N_PAR_PIQUETEÉ}</div>',
        unsafe_allow_html=True)

    # ── Date switcher côte à côte ────────────────────────────────────────
    sw_col1, sw_col2 = st.columns(2)
    bag_dates = sorted(config.get("bag_registry", {}).keys())

    with sw_col1:
        st.markdown('<div class="pass-header pass1-header">🔵 Passage 1 — Aller</div>',
                    unsafe_allow_html=True)
        if p1_dates_list:
            date_options_p1 = [d for d, _ in p1_dates_list]
            if st.session_state["sel_date_p1"] not in date_options_p1:
                st.session_state["sel_date_p1"] = date_options_p1[0]
            selected_date_p1 = st.selectbox(
                "📅 Date d'acquisition — Passage 1",
                options=date_options_p1,
                index=date_options_p1.index(st.session_state["sel_date_p1"]),
                key="date_sw_p1",
                format_func=lambda d: f"🗓 {d}",
            )
            st.session_state["sel_date_p1"] = selected_date_p1
            ply_path_p1 = next(path for d, path in p1_dates_list if d == selected_date_p1)
            p1_available = True
        else:
            p1_available = False
            ply_path_p1  = None
            if bag_dates:
                st.info("Pas encore construit pour ce pied — choisis une date pour le construire ⬇️")
                selected_date_p1 = st.selectbox(
                    "📅 Date à construire — Passage 1",
                    options=bag_dates, key="date_new_p1", format_func=lambda d: f"🗓 {d}",
                )
            else:
                st.warning("Aucune date dans 'bag_registry' — impossible de construire.")
                selected_date_p1 = None

    with sw_col2:
        st.markdown('<div class="pass-header pass2-header">🟣 Passage 2 — Retour</div>',
                    unsafe_allow_html=True)
        if p2_dates_list:
            date_options_p2 = [d for d, _ in p2_dates_list]
            if st.session_state["sel_date_p2"] not in date_options_p2:
                st.session_state["sel_date_p2"] = date_options_p2[0]
            selected_date_p2 = st.selectbox(
                "📅 Date d'acquisition — Passage 2",
                options=date_options_p2,
                index=date_options_p2.index(st.session_state["sel_date_p2"]),
                key="date_sw_p2",
                format_func=lambda d: f"🗓 {d}",
            )
            st.session_state["sel_date_p2"] = selected_date_p2
            ply_path_p2 = next(path for d, path in p2_dates_list if d == selected_date_p2)
            p2_available = True
        else:
            p2_available = False
            ply_path_p2  = None
            if bag_dates:
                st.info("Pas encore construit pour ce pied — choisis une date pour le construire ⬇️")
                selected_date_p2 = st.selectbox(
                    "📅 Date à construire — Passage 2",
                    options=bag_dates, key="date_new_p2", format_func=lambda d: f"🗓 {d}",
                )
            else:
                st.warning("Aucune date dans 'bag_registry' — impossible de construire.")
                selected_date_p2 = None

    # ══════════════════════════════════════════════════════════════════
    # PANNEAU DE RECONSTRUCTION — extraction C++ + filtrage/registration 3D
    # ══════════════════════════════════════════════════════════════════
    with st.expander("⚙️ Paramètres de reconstruction (extraction + 3D)", expanded=False):
        st.caption(
            "Ajuste les paramètres puis clique sur « Reconstruire ». "
            "L'ancienne construction 3D (et l'extraction brute si cochée) est supprimée et remplacée. "
            "Si le pied n'existe pas encore pour cette date, l'extraction est automatiquement lancée."
        )
        cpp_def = _cpp_defaults()
        f3d_def = config["params"]["filter_3d"]
        reg_def = config["params"]["registration"]

        st.markdown("**Sélection des images (Frames)**")
        cf_1, cf_2 = st.columns(2)
        with cf_1:
            frame_offset = st.number_input("Décalage de départ (images)", -5000, 5000, 0, 1, key="rc_offset",
                                           help="Ex: +50 retarde le début de 50 images. -50 l'avance.")
        with cf_2:
            frame_count = st.number_input("Forcer le nombre d'images (0 = défaut)", 0, 5000, 0, 1, key="rc_count",
                                          help="0 = conserve le nombre d'images prévu par le pipeline.")

        if frame_offset != 0 or frame_count > 0:
            _preview_bits = []
            for _pas, _date in ((1, selected_date_p1), (2, selected_date_p2)):
                if not _date:
                    continue
                _job = f"pied{sel_pied:02d}_pass{_pas}_{_date}"
                _fr = get_job_frame_range(_job)
                if _fr is None:
                    _preview_bits.append(f"P{_pas} : pas de plage assignée dans {PIED_JOBS_CSV.name}")
                    continue
                _s, _e = _fr
                _s2 = max(1, _s + frame_offset)
                _e2 = (_s2 + frame_count - 1) if frame_count > 0 else (_e + frame_offset)
                _n2 = _e2 - _s2 + 1
                _preview_bits.append(f"P{_pas} : {_s}→{_e} ({_e-_s+1} img) → **{_s2}→{_e2} ({_n2} img)**")
            if _preview_bits:
                st.caption("Plage résultante :  " + "  ·  ".join(_preview_bits))

        st.markdown("**Extraction C++**")
        ce1, ce2, ce3 = st.columns(3)
        with ce1:
            z_min = st.number_input("z_min (m)", 0.0, 5.0,
                                     float(cpp_def.get("z_min", 0.4)), 0.05, key="rc_z_min")
        with ce2:
            z_max = st.number_input("z_max (m)", 0.0, 5.0,
                                     float(cpp_def.get("z_max", 1.0)), 0.05, key="rc_z_max")
        with ce3:
            redo_extraction = st.checkbox(
                "Ré-extraire depuis le .bag", value=False, key="rc_redo_extract",
                help="Obligatoire si z_min/z_max/rotations changent. Sinon les PLY bruts "
                     "déjà extraits sont réutilisés (beaucoup plus rapide).")
        ce4, ce5, ce6 = st.columns(3)
        with ce4:
            rot_x = st.number_input("rot_x (°)", -360.0, 360.0,
                                     float(cpp_def.get("rot_x_deg", 0.0)), 1.0, key="rc_rot_x")
        with ce5:
            rot_y = st.number_input("rot_y (°)", -360.0, 360.0,
                                     float(cpp_def.get("rot_y_deg", 0.0)), 1.0, key="rc_rot_y")
        with ce6:
            rot_z = st.number_input("rot_z (°)", -360.0, 360.0,
                                     float(cpp_def.get("rot_z_deg", 270.0)), 1.0, key="rc_rot_z")

        st.markdown("**Filtrage 3D**")
        cf1, cf2, cf3, cf4 = st.columns(4)
        with cf1:
            voxel_size = st.number_input("voxel_size", 0.001, 0.05,
                                          float(f3d_def["voxel_size"]), 0.001,
                                          format="%.3f", key="rc_voxel")
        with cf2:
            eps = st.number_input("eps (DBSCAN)", 0.01, 1.0,
                                   float(f3d_def["eps"]), 0.01, key="rc_eps")
        with cf3:
            min_points = st.number_input("min_points (DBSCAN)", 1, 200,
                                          int(f3d_def["min_points"]), 1, key="rc_min_points")
        with cf4:
            min_cluster_size = st.number_input("min_cluster_size", 10, 5000,
                                                int(f3d_def["min_cluster_size"]), 10, key="rc_min_cluster")
        cf5, cf6, cf7, cf8 = st.columns(4)
        with cf5:
            use_statistical = st.checkbox("Filtre statistique",
                                           value=bool(f3d_def["use_statistical"]), key="rc_use_stat")
        with cf6:
            use_radius_filter = st.checkbox("Filtre par rayon",
                                             value=bool(f3d_def["use_radius_filter"]), key="rc_use_radius")
        with cf7:
            nb_neighbors = st.number_input("nb_neighbors", 1, 100,
                                            int(f3d_def["nb_neighbors"]), 1, key="rc_nb_neigh")
        with cf8:
            std_ratio = st.number_input("std_ratio", 0.1, 5.0,
                                         float(f3d_def["std_ratio"]), 0.1, key="rc_std_ratio")
        cf9, cf10 = st.columns(2)
        with cf9:
            radius_filt = st.number_input("radius (outlier)", 0.01, 2.0,
                                           float(f3d_def["radius"]), 0.01, key="rc_radius_filt")
        with cf10:
            min_radius_points = st.number_input("min_radius_points", 1, 200,
                                                 int(f3d_def["min_radius_points"]), 1, key="rc_min_radius_pts")

        st.markdown("**Registration (ICP / RANSAC)**")
        cr1, cr2, cr3, cr4 = st.columns(4)
        with cr1:
            voxel_size_reg = st.number_input("voxel_size_reg", 0.005, 0.2,
                                              float(reg_def["voxel_size_reg"]), 0.005, key="rc_voxel_reg")
        with cr2:
            window_size = st.number_input("window_size", 1, 20,
                                           int(reg_def["window_size"]), 1, key="rc_window")
        with cr3:
            voxel_size_merge = st.number_input("voxel_size_merge", 0.001, 0.1,
                                                float(reg_def["voxel_size_merge"]), 0.001,
                                                format="%.3f", key="rc_voxel_merge")
        with cr4:
            sliding_voxel_size = st.number_input("sliding_voxel_size", 0.001, 0.1,
                                                  float(reg_def["sliding_voxel_size"]), 0.001,
                                                  format="%.3f", key="rc_sliding_voxel")

        cpp_over = {"z_min": z_min, "z_max": z_max,
                    "rot_x_deg": rot_x, "rot_y_deg": rot_y, "rot_z_deg": rot_z,
                    "frame_offset": frame_offset, "frame_count": frame_count}
        f3d_over = {
            "voxel_size": voxel_size, "use_statistical": use_statistical,
            "use_radius_filter": use_radius_filter, "eps": eps, "min_points": min_points,
            "min_cluster_size": min_cluster_size, "nb_neighbors": nb_neighbors,
            "std_ratio": std_ratio, "radius": radius_filt, "min_radius_points": min_radius_points,
        }
        reg_over = {
            "voxel_size_reg": voxel_size_reg, "window_size": window_size,
            "voxel_size_merge": voxel_size_merge, "sliding_voxel_size": sliding_voxel_size,
        }

        st.markdown("---")
        rreset, rb1, rb2 = st.columns([1, 2, 2])
        with rreset:
            if st.button("↺ Réinitialiser", width="stretch", key="rc_reset",
                         help="Revenir aux valeurs de pipeline_config.json"):
                for k in list(st.session_state.keys()):
                    if k.startswith("rc_") and k != "rc_reset":
                        del st.session_state[k]
                st.rerun()
        with rb1:
            label_p1 = (f"🔧 {'Reconstruire' if p1_available else 'Construire'} "
                        f"Pied {sel_pied:02d} — Passage 1")
            if st.button(label_p1, width="stretch",
                         disabled=selected_date_p1 is None, key="rc_btn_p1"):
                with st.status(f"Reconstruction pied{sel_pied:02d}_pass1_{selected_date_p1}…",
                               expanded=True) as status:
                    success, msg = rebuild_construction(
                        sel_pied, 1, selected_date_p1, cpp_over, f3d_over, reg_over,
                        redo_extraction, status=status)
                    status.update(label="✔ Terminé" if success else "✘ Échec",
                                  state="complete" if success else "error")
                if success:
                    st.success(msg)
                    load_ply.clear()
                    scan_available_pieds.clear()
                    get_job_frame_range.clear()
                    get_gps_quality_for_job.clear()
                    st.rerun()
                else:
                    st.error(msg)
        with rb2:
            label_p2 = (f"🔧 {'Reconstruire' if p2_available else 'Construire'} "
                        f"Pied {sel_pied:02d} — Passage 2")
            if st.button(label_p2, width="stretch",
                         disabled=selected_date_p2 is None, key="rc_btn_p2"):
                with st.status(f"Reconstruction pied{sel_pied:02d}_pass2_{selected_date_p2}…",
                               expanded=True) as status:
                    success, msg = rebuild_construction(
                        sel_pied, 2, selected_date_p2, cpp_over, f3d_over, reg_over,
                        redo_extraction, status=status)
                    status.update(label="✔ Terminé" if success else "✘ Échec",
                                  state="complete" if success else "error")
                if success:
                    st.success(msg)
                    load_ply.clear()
                    scan_available_pieds.clear()
                    get_job_frame_range.clear()
                    get_gps_quality_for_job.clear()
                    st.rerun()
                else:
                    st.error(msg)


    # ── Vues 3D côte à côte ──────────────────────────────────────────────
    pts1, col1_data, metrics1 = None, None, {}
    pts2, col2_data, metrics2 = None, None, {}

    viz_col1, viz_col2 = st.columns(2)

    with viz_col1:
        if p1_available:
            with st.spinner("Chargement passage 1…"):
                pts1, col1_data, status1 = load_ply(ply_path_p1, voxel_size=VOXEL_PIED)
            if status1 == "OK" and pts1 is not None:
                st.plotly_chart(
                    make_3d_figure(pts1, col1_data,
                                   title=f"Pied {sel_pied:02d} · Passage 1 · {selected_date_p1}"),
                    width='stretch')
                metrics1 = pied_metrics(pts1)
                c_a, c_b = st.columns(2)
                with c_a: st.metric("Points", f"{metrics1['n_points']:,}".replace(",", " "))
                with c_b: st.metric("Hauteur", f"{metrics1['hauteur']} m")

                job_p1 = f"pied{sel_pied:02d}_pass1_{selected_date_p1}"
                q1 = get_gps_quality_for_job(job_p1, selected_date_p1)
                if q1:
                    badge_cls = "badge-ok" if q1["pct_fix"] >= 50 else ("badge-warn" if q1["pct_fix"] > 0 else "badge-miss")
                    st.markdown(f'<span class="badge {badge_cls}">GPS : {q1["pct_fix"]}% fixe / '
                                f'{q1["pct_float"]}% float ({q1["n"]} pts)</span>', unsafe_allow_html=True)

                prev1 = find_preview_image(sel_pied, 1, selected_date_p1)
                params1 = load_reconstruction_params(ply_path_p1)
                if prev1 or params1:
                    with st.expander("📷 Aperçu & paramètres de génération"):
                        if prev1:
                            st.image(prev1, caption="Frame RGB du milieu", width=220)
                        if params1:
                            st.caption(f"Généré le {params1.get('generated_at', '?')}")
                            st.json(params1, expanded=False)
            else:
                st.error(f"Erreur : {status1}")

    with viz_col2:
        if p2_available:
            with st.spinner("Chargement passage 2…"):
                pts2, col2_data, status2 = load_ply(ply_path_p2, voxel_size=VOXEL_PIED)
            if status2 == "OK" and pts2 is not None:
                st.plotly_chart(
                    make_3d_figure(pts2, col2_data,
                                   title=f"Pied {sel_pied:02d} · Passage 2 · {selected_date_p2}"),
                    width='stretch')
                metrics2 = pied_metrics(pts2)
                c_a, c_b = st.columns(2)
                with c_a: st.metric("Points", f"{metrics2['n_points']:,}".replace(",", " "))
                with c_b: st.metric("Hauteur", f"{metrics2['hauteur']} m")

                job_p2 = f"pied{sel_pied:02d}_pass2_{selected_date_p2}"
                q2 = get_gps_quality_for_job(job_p2, selected_date_p2)
                if q2:
                    badge_cls = "badge-ok" if q2["pct_fix"] >= 50 else ("badge-warn" if q2["pct_fix"] > 0 else "badge-miss")
                    st.markdown(f'<span class="badge {badge_cls}">GPS : {q2["pct_fix"]}% fixe / '
                                f'{q2["pct_float"]}% float ({q2["n"]} pts)</span>', unsafe_allow_html=True)

                prev2 = find_preview_image(sel_pied, 2, selected_date_p2)
                params2 = load_reconstruction_params(ply_path_p2)
                if prev2 or params2:
                    with st.expander("📷 Aperçu & paramètres de génération"):
                        if prev2:
                            st.image(prev2, caption="Frame RGB du milieu", width=220)
                        if params2:
                            st.caption(f"Généré le {params2.get('generated_at', '?')}")
                            st.json(params2, expanded=False)
            else:
                st.error(f"Erreur : {status2}")

    # ── Vue superposée P1/P2 (alignement ICP, affichage uniquement) ────────
    if p1_available and p2_available and pts1 is not None and pts2 is not None:
        with st.expander("🔀 Vue superposée Passage 1 / Passage 2 (alignement ICP)"):
            st.caption(
                "P1 et P2 sont reconstruits chacun dans leur propre repère local — "
                "ce bouton aligne P2 sur P1 par ICP pour comparaison visuelle uniquement "
                "(aucun fichier n'est modifié)."
            )
            if st.button("Aligner et superposer", key="btn_overlay_icp"):
                with st.spinner("Alignement ICP en cours…"):
                    pts2_aligned, err = align_pass2_on_pass1(ply_path_p1, ply_path_p2)
                if err:
                    st.error(err)
                else:
                    combined = np.vstack([pts1, pts2_aligned])
                    combined_colors = (
                        ["rgb(78,154,241)"] * len(pts1) + ["rgb(192,132,252)"] * len(pts2_aligned)
                    )
                    fig_overlay = go.Figure(data=[go.Scatter3d(
                        x=combined[:, 0], y=combined[:, 1], z=combined[:, 2],
                        mode='markers',
                        marker=dict(size=1.5, color=combined_colors, opacity=0.85),
                    )])
                    fig_overlay.update_layout(
                        paper_bgcolor=TH["bg"],
                        title=dict(text=f"Pied {sel_pied:02d} · P1 (bleu) + P2 aligné (violet)",
                                   font=dict(color=TH["text_light"], size=12), x=0.03),
                        scene=dict(
                            bgcolor=TH["bg"], aspectmode="data",
                            xaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                            yaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                            zaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                            camera=dict(eye=dict(x=0, y=-2.0, z=0.5), up=dict(x=0, y=0, z=1)),
                        ),
                        height=480,
                    )
                    st.plotly_chart(fig_overlay, width='stretch')

    # ── Tableau comparatif ───────────────────────────────────────────────
    if metrics1 or metrics2:
        st.markdown("---")
        st.markdown(f'<div class="section-header">Comparaison P1 / P2 — Pied {sel_pied}</div>',
                    unsafe_allow_html=True)
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)

        def cmp_card(col, label, key, unit=""):
            v1 = metrics1.get(key)
            v2 = metrics2.get(key)
            s1 = f"{v1:,}{unit}".replace(",", " ") if isinstance(v1, int) else (f"{v1}{unit}" if v1 is not None else "—")
            s2 = f"{v2:,}{unit}".replace(",", " ") if isinstance(v2, int) else (f"{v2}{unit}" if v2 is not None else "—")
            delta_html = ""
            if v1 is not None and v2 is not None and isinstance(v1, (int, float)):
                diff = round(v2 - v1, 3)
                sign = "+" if diff >= 0 else ""
                col_d = "#4ade80" if diff >= 0 else "#f87171"
                delta_html = f'<div class="delta" style="color:{col_d}">Δ {sign}{diff}{unit}</div>'
            col.markdown(f"""
<div class="metric-card">
<div class="label">{label}</div>
<div class="value" style="font-size:0.9rem;">
  <span style="color:#7eb8f7">P1: {s1}</span><br>
  <span style="color:#c084fc">P2: {s2}</span>
</div>
{delta_html}
</div>
""", unsafe_allow_html=True)

        cmp_card(mc1, "Nb points",  "n_points")
        cmp_card(mc2, "Hauteur",    "hauteur",    " m")
        cmp_card(mc3, "Largeur",    "largeur",    " m")
        cmp_card(mc4, "Profondeur", "profondeur", " m")
        cmp_card(mc5, "Z max",      "z_max",      " m")

    # ── Barre de couverture ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Couverture du Rang — Toutes dates confondues</div>',
                unsafe_allow_html=True)

    cov_p1 = [1 if avail[p][1] else 0 for p in range(1, N_PIEDS + 1)]
    cov_p2 = [1 if avail[p][2] else 0 for p in range(1, N_PIEDS + 1)]
    pieds_x = list(range(1, N_PIEDS + 1))

    fig_cov = go.Figure()
    fig_cov.add_trace(go.Bar(
        name="Passage 1", x=pieds_x, y=cov_p1,
        marker_color=["#4e9af1" if v else TH["card_bg"] for v in cov_p1],
        marker_line_color=["#7eb8f7" if v else TH["border"] for v in cov_p1],
        marker_line_width=1, width=0.38, offset=-0.19,
    ))
    fig_cov.add_trace(go.Bar(
        name="Passage 2", x=pieds_x, y=cov_p2,
        marker_color=["#a78bfa" if v else TH["card_bg"] for v in cov_p2],
        marker_line_color=["#c084fc" if v else TH["border"] for v in cov_p2],
        marker_line_width=1, width=0.38, offset=0.19,
    ))
    for pk in range(1, 5):
        fig_cov.add_vline(x=pk * N_PAR_PIQUETEÉ + 0.5, line_dash="dot",
                          line_color="#3a4a6a", line_width=1)
        fig_cov.add_annotation(x=pk * N_PAR_PIQUETEÉ + 0.5 + N_PAR_PIQUETEÉ / 2 - N_PAR_PIQUETEÉ / 2,
                               y=1.12, text=f"Piqueteé {pk+1}", showarrow=False,
                               font=dict(color=TH["text_muted"], size=10), xref="x", yref="paper")
    fig_cov.add_annotation(x=3, y=1.12, text="Piqueteé 1", showarrow=False,
                           font=dict(color=TH["text_muted"], size=10), xref="x", yref="paper")
    fig_cov.add_vline(x=sel_pied, line_color="#fbbf24", line_width=2,
                      annotation_text=f"P{sel_pied}", annotation_font_color="#fbbf24",
                      annotation_position="top")
    fig_cov.update_layout(
        paper_bgcolor=TH["bg"], plot_bgcolor=TH["bg"],
        barmode="overlay", height=190,
        margin=dict(l=0, r=0, t=35, b=10),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=1.18,
                    font=dict(color=TH["text_light"]), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title="Numéro de pied", color=TH["text_muted"], gridcolor=TH["card_bg"],
                   tickmode="linear", tick0=1, dtick=1),
        yaxis=dict(visible=False),
    )
    st.plotly_chart(fig_cov, width='stretch')

    # ── Qualité GPS par pied (dernière acquisition disponible) ─────────────
    st.markdown('<div class="section-header">Qualité GPS par pied — dernière acquisition</div>',
                unsafe_allow_html=True)

    quality_pct, quality_colors = [], []
    for p in pieds_x:
        pct = _pied_quality_pct(avail, p)
        quality_pct.append(pct if pct is not None else 0)
        if pct is None:
            quality_colors.append(TH["border"])
        elif pct >= 80:
            quality_colors.append("#4ade80")
        elif pct >= 20:
            quality_colors.append("#fbbf24")
        else:
            quality_colors.append("#f87171")

    fig_q = go.Figure(go.Bar(
        x=pieds_x, y=quality_pct, marker_color=quality_colors,
        hovertext=[f"Pied {p} : {v}% fixe" if _pied_quality_pct(avail, p) is not None
                   else f"Pied {p} : pas de données" for p, v in zip(pieds_x, quality_pct)],
        hoverinfo="text",
    ))
    fig_q.update_layout(
        paper_bgcolor=TH["bg"], plot_bgcolor=TH["bg"], height=150,
        margin=dict(l=0, r=0, t=10, b=10),
        yaxis=dict(title="% fixe", range=[0, 100], color=TH["text_muted"], gridcolor=TH["card_bg"]),
        xaxis=dict(title="Numéro de pied", color=TH["text_muted"], gridcolor=TH["card_bg"],
                   tickmode="linear", tick0=1, dtick=1),
    )
    st.plotly_chart(fig_q, width='stretch')
    st.caption("🟢 ≥80% fixe · 🟠 20–80% · 🔴 <20% · gris = pas de données GPS pour ce pied")


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — SUIVI MULTI-TEMPOREL PAR PIQUETEÉ
# ══════════════════════════════════════════════════════════════════════════

elif page == "📈 Suivi Multi-Temporel":
    st.markdown('<div class="viti-title">Suivi Multi-Temporel — Vue par Piqueteé</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="viti-sub">5 pieds par Piqueteé · Downsampling adaptatif pour gérer le volume · '
        'Sélectionnez le Piqueteé, la date et le passage</div>',
        unsafe_allow_html=True)

    # ── Contrôles principaux ─────────────────────────────────────────────
    all_dates = sorted({d for p in avail.values()
                          for dl in p.values()
                          for d, _ in dl})

    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 1])

    with ctrl1:
        if all_dates:
            date_choisie = st.selectbox(
                "📅 Campagne d'acquisition",
                options=all_dates,
                index=len(all_dates) - 1,
                format_func=lambda d: f"🗓 {d}",
            )
        else:
            st.warning("Aucune date détectée dans final_Output/")
            date_choisie = None

    with ctrl2:
        pass_choice = st.radio(
            "Passage",
            ["Passage 1", "Passage 2", "Les deux"],
            horizontal=True,
        )

    with ctrl3:
        voxel_choice = st.select_slider(
            "🔬 Densité d'affichage",
            options=[0.005, 0.008, 0.012, 0.02, 0.03],
            value=VOXEL_PIQUETEÉ,
            format_func=lambda v: f"voxel {v*1000:.0f} mm",
        )

    with ctrl4:
        show_simul = st.checkbox(
            "Simulation",
            value=(not bool(all_dates)),
            help="Activer si aucune donnée réelle n'est disponible",
        )

    passes_to_show = [1] if pass_choice == "Passage 1" \
                  else [2] if pass_choice == "Passage 2" \
                  else [1, 2]

    compare_mode, date_a, date_b = False, None, None
    if len(all_dates) >= 2:
        compare_mode = st.checkbox("🔬 Comparer deux dates (croissance)", value=False, key="compare_mode")
        if compare_mode:
            cmp1, cmp2 = st.columns(2)
            with cmp1:
                date_a = st.selectbox("Date A (référence)", options=all_dates,
                                      index=0, key="cmp_date_a", format_func=lambda d: f"🗓 {d}")
            with cmp2:
                date_b = st.selectbox("Date B (comparée)", options=all_dates,
                                      index=len(all_dates) - 1, key="cmp_date_b", format_func=lambda d: f"🗓 {d}")
            if date_a == date_b:
                st.warning("Choisis deux dates différentes.")
                compare_mode = False

    st.markdown("---")

    # ── Sélecteur de piquetee (boutons) ──────────────────────────────────
    if "sel_Piqueteé_p2" not in st.session_state:
        st.session_state["sel_Piqueteé_p2"] = 1

    st.markdown('<div class="section-header">Sélection du Piqueteé</div>', unsafe_allow_html=True)

    pq_cols = st.columns(5)
    for pk in range(1, 6):
        pied_start = (pk - 1) * N_PAR_PIQUETEÉ + 1
        pied_end   = pk * N_PAR_PIQUETEÉ
        with pq_cols[pk - 1]:
            # Compter combien de pieds du piquetee ont des données pour la date choisie
            n_dispo = 0
            if date_choisie:
                for p in range(pied_start, pied_end + 1):
                    for pas in passes_to_show:
                        if any(d == date_choisie for d, _ in avail[p][pas]):
                            n_dispo += 1
                            break
            is_active = (pk == st.session_state["sel_Piqueteé_p2"])
            icon_pk = "🟢" if n_dispo == N_PAR_PIQUETEÉ else ("🟡" if n_dispo > 0 else "⚫")
            label_pk = f"{'▶ ' if is_active else ''}{icon_pk} Piqueteé {pk}\n({n_dispo}/{N_PAR_PIQUETEÉ})"
            if st.button(label_pk, key=f"pq_btn_{pk}", width="stretch",
                         type="primary" if is_active else "secondary"):
                st.session_state["sel_Piqueteé_p2"] = pk
                st.rerun()

    sel_piquetee = st.session_state["sel_Piqueteé_p2"]
    pied_range  = range((sel_piquetee - 1) * N_PAR_PIQUETEÉ + 1,
                        sel_piquetee * N_PAR_PIQUETEÉ + 1)

    st.markdown("---")

    # ════════════════════════════════════════════════════════════════════
    # MODE COMPARAISON — 2 dates, delta de croissance par pied
    # ════════════════════════════════════════════════════════════════════
    if compare_mode and date_a and date_b:
        st.markdown(
            f'<span class="badge badge-ok">COMPARAISON — {date_a} → {date_b} — Piqueteé {sel_piquetee}</span>',
            unsafe_allow_html=True)
        st.markdown(" ")

        def _load_piquetee_pts(date_label):
            pts_list, cols_list, info = [], [], []
            for i, pied_num in enumerate(pied_range):
                x_offset = i * 1.2
                for pas in passes_to_show:
                    match = [(d, p) for d, p in avail[pied_num][pas] if d == date_label]
                    if not match:
                        continue
                    _, ply_path = match[0]
                    pts, cols, status = load_ply(ply_path, voxel_size=voxel_choice)
                    if status != "OK" or pts is None:
                        continue
                    shifted = pts.copy()
                    shifted[:, 0] += x_offset
                    shifted[:, 1] += (0.6 if pas == 2 else 0.0)
                    pts_list.append(shifted)
                    cols_list.append(cols if cols is not None else np.tile([0.4, 0.7, 0.4], (len(pts), 1)))
                    info.append((pied_num, pas))
            return pts_list, cols_list, info

        with st.spinner(f"Chargement {date_a}…"):
            pts_a, cols_a, info_a = _load_piquetee_pts(date_a)
        with st.spinner(f"Chargement {date_b}…"):
            pts_b, cols_b, info_b = _load_piquetee_pts(date_b)

        vcol_a, vcol_b = st.columns(2)
        with vcol_a:
            if pts_a:
                fig_a = go.Figure(data=[go.Scatter3d(
                    x=np.vstack(pts_a)[:, 0], y=np.vstack(pts_a)[:, 1], z=np.vstack(pts_a)[:, 2],
                    mode='markers', marker=dict(size=1.3, color="#4e9af1", opacity=0.85),
                )])
                fig_a.update_layout(
                    paper_bgcolor=TH["bg"],
                    title=dict(text=f"Piqueteé {sel_piquetee} · {date_a}", font=dict(color=TH["text_light"], size=12)),
                    scene=dict(bgcolor=TH["bg"], aspectmode="data",
                              xaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              yaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              zaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1))),
                    margin=dict(l=0, r=0, b=0, t=30), height=420,
                )
                st.plotly_chart(fig_a, width='stretch')
            else:
                st.info(f"Aucune donnée pour {date_a}.")
        with vcol_b:
            if pts_b:
                fig_b = go.Figure(data=[go.Scatter3d(
                    x=np.vstack(pts_b)[:, 0], y=np.vstack(pts_b)[:, 1], z=np.vstack(pts_b)[:, 2],
                    mode='markers', marker=dict(size=1.3, color="#a78bfa", opacity=0.85),
                )])
                fig_b.update_layout(
                    paper_bgcolor=TH["bg"],
                    title=dict(text=f"Piqueteé {sel_piquetee} · {date_b}", font=dict(color=TH["text_light"], size=12)),
                    scene=dict(bgcolor=TH["bg"], aspectmode="data",
                              xaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              yaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              zaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"]),
                              camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1))),
                    margin=dict(l=0, r=0, b=0, t=30), height=420,
                )
                st.plotly_chart(fig_b, width='stretch')
            else:
                st.info(f"Aucune donnée pour {date_b}.")

        # ── Table de delta par pied (fusion des passages disponibles) ──────
        st.markdown("---")
        st.markdown('<div class="section-header">Delta de croissance par pied</div>', unsafe_allow_html=True)

        rows = []
        for pied_num in pied_range:
            arrs_a = [p for (pn, _), p in zip(info_a, pts_a) if pn == pied_num]
            arrs_b = [p for (pn, _), p in zip(info_b, pts_b) if pn == pied_num]
            ma = pied_metrics(np.vstack(arrs_a)) if arrs_a else {}
            mb = pied_metrics(np.vstack(arrs_b)) if arrs_b else {}
            row = {"pied": pied_num}
            for key, label in [("hauteur", "hauteur_m"), ("largeur", "largeur_m"),
                               ("profondeur", "profondeur_m"), ("n_points", "n_points")]:
                va, vb = ma.get(key), mb.get(key)
                row[f"{label}_{date_a}"] = va
                row[f"{label}_{date_b}"] = vb
                row[f"delta_{label}"] = round(vb - va, 3) if (va is not None and vb is not None) else None
            rows.append(row)
        delta_df = pd.DataFrame(rows)
        st.dataframe(delta_df, width='stretch', hide_index=True)
        st.download_button(
            "⬇️ Exporter le comparatif (CSV)",
            data=delta_df.to_csv(index=False).encode("utf-8"),
            file_name=f"comparaison_piquetee{sel_piquetee}_{date_a}_vs_{date_b}.csv",
            mime="text/csv",
        )

    # ════════════════════════════════════════════════════════════════════
    # MODE SIMULATION
    # ════════════════════════════════════════════════════════════════════
    elif show_simul or not date_choisie:
        st.markdown('<span class="badge badge-sim">MODE SIMULATION</span>',
                    unsafe_allow_html=True)
        st.markdown(" ")

        month_labels = ["Déc. 2024", "Jan. 2025", "Fév. 2025", "Mars 2025",
                        "Avr. 2025", "Mai 2025", "Juin 2025", "Juil. 2025"]
        mois_idx = month_labels.index(
            st.select_slider("Mois simulé", options=month_labels, value="Juil. 2025")
        )

        @st.cache_data(show_spinner=False)
        def simul_piquetee(piquetee_idx: int, mi: int):
            cr = 1.0 + mi * 0.25
            pts, cols = [], []
            for i in range(N_PAR_PIQUETEÉ):
                xo = i * 1.2
                nt, nf = 400, int(800 * cr)
                pts += [
                    np.column_stack([np.random.normal(xo, 0.04, nt),
                                     np.random.normal(0, 0.04, nt),
                                     np.random.uniform(0, 0.5, nt)]),
                    np.column_stack([np.random.normal(xo, 0.25 * cr, nf),
                                     np.random.normal(0, 0.15 * cr, nf),
                                     np.random.uniform(0.4, 0.7 * cr, nf)]),
                ]
                cols += [
                    np.tile([0.55, 0.27, 0.07], (nt, 1)),
                    np.tile([0.13, 0.55, 0.13], (nf, 1)),
                ]
            return np.vstack(pts), np.vstack(cols)

        with st.spinner("Simulation en cours…"):
            pts_sim, cols_sim = simul_piquetee(sel_piquetee, mois_idx)
            color_sim = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r, g, b in cols_sim]

        fig_s = go.Figure(data=[go.Scatter3d(
            x=pts_sim[:, 0], y=pts_sim[:, 1], z=pts_sim[:, 2],
            mode='markers',
            marker=dict(size=1.5, color=color_sim, opacity=0.85),
        )])
        fig_s.update_layout(
            paper_bgcolor=TH["bg"],
            title=dict(text=f"Piqueteé {sel_piquetee} — Simulation — {month_labels[mois_idx]}",
                       font=dict(color=TH["text_light"], size=13), x=0.03),
            scene=dict(
                bgcolor=TH["bg"],
                xaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title="X (pieds)"),
                yaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title="Y"),
                zaxis=dict(showbackground=False, gridcolor=TH["border2"], color=TH["text_muted"], title="Z (hauteur)"),
                aspectmode="data",
                camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1)),
            ),
            margin=dict(l=0, r=0, b=0, t=40), height=520,
        )
        st.plotly_chart(fig_s, width='stretch')

        ms1, ms2, ms3, ms4 = st.columns(4)
        with ms1: st.metric("Volume feuillage", f"{1.2 + mois_idx * 0.6:.2f} m³")
        with ms2: st.metric("Pieds dans le Piqueteé", N_PAR_PIQUETEÉ)
        with ms3: st.metric("Hauteur moy. estimée", f"{0.5 + mois_idx * 0.03:.2f} m")
        with ms4: st.metric("Points simulés", f"{len(pts_sim):,}".replace(",", " "))

    # ════════════════════════════════════════════════════════════════════
    # MODE DONNÉES RÉELLES — VUE PAR PIQUETEÉ
    # ════════════════════════════════════════════════════════════════════
    else:
        st.markdown(
            f'<span class="badge badge-ok">DONNÉES RÉELLES — {date_choisie} — Piqueteé {sel_piquetee}</span>',
            unsafe_allow_html=True)
        st.markdown(" ")

        # ── Chargement des pieds du piquetee sélectionné ──────────────────
        piquetee_pts  = []   # un array par pied (après décalage X)
        piquetee_cols = []
        loaded_info   = []   # (pied_num, pas, n_pts)
        missing       = []   # pieds sans données pour cette date

        prog = st.progress(0, text=f"Chargement Piqueteé {sel_piquetee}…")

        for i, pied_num in enumerate(pied_range):
            prog.progress(i / N_PAR_PIQUETEÉ, text=f"Pied {pied_num}…")
            x_offset = i * 1.2   # espace entre pieds dans la vue piquetee

            pied_loaded = False
            for pas in passes_to_show:
                dates_list = avail[pied_num][pas]
                match = [(d, p) for d, p in dates_list if d == date_choisie]
                if not match:
                    continue
                _, ply_path = match[0]

                pts, cols, status = load_ply(ply_path, voxel_size=voxel_choice)
                if status != "OK" or pts is None:
                    continue

                # Décalage X pour séparer les pieds visuellement
                shifted = pts.copy()
                shifted[:, 0] += x_offset
                # Décalage Y léger pour distinguer les passages
                shifted[:, 1] += (0.6 if pas == 2 else 0.0)

                piquetee_pts.append(shifted)

                if cols is not None:
                    tinted = cols.copy()
                    if pas == 2:
                        tinted[:, 2] = np.clip(tinted[:, 2] + 0.15, 0, 1)
                    piquetee_cols.append(tinted)
                else:
                    piquetee_cols.append(np.tile([0.4, 0.7, 0.4], (len(pts), 1)))

                loaded_info.append((pied_num, pas, len(pts)))
                pied_loaded = True

            if not pied_loaded:
                missing.append(pied_num)

        prog.empty()

        # ── Export CSV des métriques (piquetee, date sélectionnée) ─────────
        if loaded_info:
            metrics_rows = [
                {"pied": pn, "passage": pas, "n_points_brut": npts, **pied_metrics(arr)}
                for (pn, pas, npts), arr in zip(loaded_info, piquetee_pts)
            ]
            metrics_df = pd.DataFrame(metrics_rows)
            st.download_button(
                f"⬇️ Exporter métriques CSV — Piqueteé {sel_piquetee} — {date_choisie}",
                data=metrics_df.to_csv(index=False).encode("utf-8"),
                file_name=f"metriques_piquetee{sel_piquetee}_{date_choisie}.csv",
                mime="text/csv",
                key="dl_metrics_piquetee",
            )

        # ── Affichage ─────────────────────────────────────────────────────
        if piquetee_pts:
            pts_all  = np.vstack(piquetee_pts)
            cols_all = np.vstack(piquetee_cols)

            # Sous-échantillonnage si trop de points
            n_avant = len(pts_all)
            pts_all, cols_all = random_subsample(pts_all, cols_all, MAX_PTS_PIQUETEÉ)
            n_apres = len(pts_all)
            subsampled = n_apres < n_avant

            color_all = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r, g, b in cols_all]

            # ── Badge de sous-échantillonnage si actif ────────────────────
            if subsampled:
                ratio = round(n_apres / n_avant * 100, 1)
                st.markdown(
                    f'<span class="badge badge-warn">⚡ Rendu sous-échantillonné : '
                    f'{n_apres:,} / {n_avant:,} pts ({ratio}%) — voxel={voxel_choice*1000:.0f} mm</span>'.replace(",", " "),
                    unsafe_allow_html=True)
                st.markdown(" ")

            # ── Figure 3D du piquetee ──────────────────────────────────────
            pied_nums_str = " · ".join(f"P{p}" for p in pied_range)
            fig_pk = go.Figure(data=[go.Scatter3d(
                x=pts_all[:, 0], y=pts_all[:, 1], z=pts_all[:, 2],
                mode='markers',
                marker=dict(size=1.5, color=color_all, opacity=0.88),
                hovertemplate='x:%{x:.3f} y:%{y:.3f} z:%{z:.3f}<extra></extra>',
            )])
            fig_pk.update_layout(
                paper_bgcolor=TH["bg"],
                title=dict(
                    text=f"Piqueteé {sel_piquetee}  ·  {pied_nums_str}  ·  {date_choisie}",
                    font=dict(color=TH["text_light"], size=13), x=0.03,
                ),
                scene=dict(
                    bgcolor=TH["bg"],
                    xaxis=dict(showbackground=False, gridcolor=TH["border2"],
                               color=TH["text_muted"], title="X (pieds espacés)"),
                    yaxis=dict(showbackground=False, gridcolor=TH["border2"],
                               color=TH["text_muted"], title="Passage (Y)"),
                    zaxis=dict(showbackground=False, gridcolor=TH["border2"],
                               color=TH["text_muted"], title="Hauteur (Z)"),
                    aspectmode="data",
                    camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1)),
                ),
                margin=dict(l=0, r=0, b=0, t=40), height=560,
            )
            st.plotly_chart(fig_pk, width='stretch')

            # ── Métriques globales du piquetee ─────────────────────────────
            mr1, mr2, mr3, mr4, mr5 = st.columns(5)
            with mr1:
                st.metric("Points affichés", f"{n_apres:,}".replace(",", " "))
            with mr2:
                st.metric("Points bruts", f"{n_avant:,}".replace(",", " "))
            with mr3:
                st.metric("Pieds chargés", len(set(p for p, _, _ in loaded_info)))
            with mr4:
                h_pk = round(float(pts_all[:, 2].max() - pts_all[:, 2].min()), 3)
                st.metric("Hauteur Piqueteé", f"{h_pk} m")
            with mr5:
                st.metric("Voxel size", f"{voxel_choice*1000:.0f} mm")

            # ── SSGLR : homogénéiser le piquetée (recalage inter-pieds) ────
            st.markdown("---")
            st.markdown('<div class="section-header">Homogénéisation du piquetée (SSGLR)</div>',
                        unsafe_allow_html=True)
            st.caption(
                "Vue ci-dessus = affichage brut, pieds simplement décalés côte à côte. "
                "Le SSGLR place d'abord chaque pied à son espacement réel (pas de RANSAC global — "
                "trop risqué entre 5 troncs qui se ressemblent, ça donne une « boule »), puis affine "
                "chaque pied par rapport à son voisin avec un ICP local borné et validé."
            )

            with st.expander("Paramètres SSGLR", expanded=False):
                ssg1, ssg2, ssg3 = st.columns(3)
                with ssg1:
                    spacing_m = st.number_input(
                        "Espacement entre pieds (m)", 0.1, 5.0,
                        float(default_pied_spacing(pied_range)), 0.05, key="rc_ssglr_spacing",
                        help="Pré-rempli depuis pos_pied.shp si disponible, sinon 1.2 m par défaut.")
                with ssg2:
                    use_icp_refine = st.checkbox("Affiner par ICP local", value=True, key="rc_ssglr_icp",
                                                 help="Décoche pour ne garder QUE l'espacement réel "
                                                      "(zéro risque de déformation, mais moins précis).")
                with ssg3:
                    icp_max_dist = st.number_input(
                        "Distance ICP max (m)", 0.02, 1.0, 0.10, 0.01, key="rc_ssglr_icpdist",
                        disabled=not use_icp_refine,
                        help="Recherche de correspondance ICP bornée — plus petit = plus prudent.")

            ssglr_result_ply = FINAL_OUTPUT / "Piqueteé_SSGLR" / f"piquetee{sel_piquetee}_{date_choisie}"
            existing_ssglr = list(ssglr_result_ply.glob("Pied(s)_merged_*.ply")) if ssglr_result_ply.exists() else []

            bssglr, binfo = st.columns([2, 1])
            with bssglr:
                if st.button("🔗 Lancer le SSGLR sur ce piquetée (5 pieds)", key="btn_ssglr",
                             width="stretch"):
                    with st.status(f"SSGLR — Piquetée {sel_piquetee} — {date_choisie}…",
                                   expanded=True) as status_ssglr:
                        ok_ssglr, msg_ssglr, ply_ssglr = run_ssglr_on_piquetee(
                            avail, pied_range, date_choisie, passes_to_show, sel_piquetee,
                            spacing_m=spacing_m, icp_max_dist=icp_max_dist,
                            use_icp_refine=use_icp_refine, status=status_ssglr)
                        status_ssglr.update(label="✔ Terminé" if ok_ssglr else "✘ Échec",
                                            state="complete" if ok_ssglr else "error")
                    if ok_ssglr:
                        st.success(msg_ssglr)
                        st.session_state["ssglr_last_result"] = ply_ssglr
                    else:
                        st.error(msg_ssglr)
            with binfo:
                if existing_ssglr:
                    st.caption(f"✔ Déjà généré ({existing_ssglr[0].stat().st_size // 1024} Ko)")


            _ssglr_to_show = st.session_state.get("ssglr_last_result") or (str(existing_ssglr[0]) if existing_ssglr else None)
            if _ssglr_to_show and Path(_ssglr_to_show).exists():
                with st.spinner("Chargement du piquetée homogénéisé…"):
                    pts_h, cols_h, status_h = load_ply(_ssglr_to_show, voxel_size=voxel_choice)
                if status_h == "OK" and pts_h is not None:
                    fig_h = make_3d_figure(
                        pts_h, cols_h,
                        title=f"Piquetée {sel_piquetee} homogénéisé (SSGLR) · {date_choisie}",
                        height=520, marker_size=1.3,
                    )
                    st.plotly_chart(fig_h, width='stretch')
                    m_h = pied_metrics(pts_h)
                    hc1, hc2, hc3 = st.columns(3)
                    with hc1: st.metric("Points (homogénéisé)", f"{m_h.get('n_points', 0):,}".replace(",", " "))
                    with hc2: st.metric("Hauteur", f"{m_h.get('hauteur', 0)} m")
                    with hc3: st.metric("Largeur totale", f"{m_h.get('largeur', 0)} m")
                    with open(_ssglr_to_show, "rb") as f:
                        st.download_button("⬇️ Télécharger le PLY homogénéisé", data=f.read(),
                                           file_name=Path(_ssglr_to_show).name, mime="application/octet-stream")

            # ── Détail par pied dans le piquetee ───────────────────────────
            st.markdown("---")
            st.markdown(
                f'<div class="section-header">Détail par pied — Piqueteé {sel_piquetee} — {date_choisie}</div>',
                unsafe_allow_html=True)

            det_cols = st.columns(N_PAR_PIQUETEÉ)
            for i, pied_num in enumerate(pied_range):
                with det_cols[i]:
                    pied_data = [(p, ps, n) for p, ps, n in loaded_info if p == pied_num]
                    if pied_data:
                        total_pts = sum(n for _, _, n in pied_data)
                        passes_ok = [ps for _, ps, _ in pied_data]
                        passes_str = " + ".join(f"P{ps}" for ps in passes_ok)
                        st.markdown(f"""
<div class="metric-card">
<div class="label">Pied {pied_num:02d}</div>
<div class="value" style="font-size:1rem;color:#4ade80;">✓ {passes_str}</div>
<div class="delta">{total_pts:,} pts</div>
</div>
""".replace(",", " "), unsafe_allow_html=True)
                    else:
                        st.markdown(f"""
<div class="metric-card">
<div class="label">Pied {pied_num:02d}</div>
<div class="value" style="font-size:1rem;color:#f87171;">✗ Non filmé</div>
<div class="delta">—</div>
</div>
""", unsafe_allow_html=True)

            # ── Tableau de disponibilité global (tous piquetees) ───────────
            st.markdown("---")
            st.markdown(
                f'<div class="section-header">Couverture globale du rang — {date_choisie}</div>',
                unsafe_allow_html=True)

            rows_html = ""
            for pk in range(1, 6):
                for p in range((pk - 1) * N_PAR_PIQUETEÉ + 1, pk * N_PAR_PIQUETEÉ + 1):
                    has_p1 = any(d == date_choisie for d, _ in avail[p][1])
                    has_p2 = any(d == date_choisie for d, _ in avail[p][2])
                    ok1 = "✅" if has_p1 else "❌"
                    ok2 = "✅" if has_p2 else "❌"
                    bg  = TH["card_bg"] if p % 2 == 0 else TH["info_bg"]
                    hi  = "border-left:3px solid #4e9af1;" if pk == sel_piquetee else ""
                    rows_html += (
                        f'<tr style="background:{bg};{hi}">'
                        f'<td style="padding:4px 8px;color:{TH["text_light"]};font-family:monospace;">Pied {p:02d}</td>'
                        f'<td style="text-align:center;padding:4px;">{ok1}</td>'
                        f'<td style="text-align:center;padding:4px;">{ok2}</td>'
                        f'<td style="padding:4px 8px;color:{TH["text_muted"]};">Piqueteé {pk}</td>'
                        f'</tr>'
                    )
            st.markdown(
                f'<table style="width:100%;font-size:0.75rem;border-collapse:collapse;">'
                f'<tr>'
                f'<th style="color:{TH["text_muted"]};text-align:left;padding:4px 8px;">Pied</th>'
                f'<th style="color:#4e9af1;text-align:center;padding:4px 8px;">Passage 1</th>'
                f'<th style="color:#a78bfa;text-align:center;padding:4px 8px;">Passage 2</th>'
                f'<th style="color:{TH["text_muted"]};text-align:left;padding:4px 8px;">Piqueteé</th>'
                f'</tr>{rows_html}</table>',
                unsafe_allow_html=True,
            )
            st.caption("✅ Disponible pour cette date  |  ❌ Non filmé / autre date")

        else:
            # Aucun nuage trouvé pour ce piquetee/date
            pieds_str = ", ".join(f"Pied {p}" for p in pied_range)
            st.warning(
                f"Aucun nuage trouvé pour le **Piqueteé {sel_piquetee}** ({pieds_str}) "
                f"à la date **{date_choisie}**.\n\n"
                "Vérifiez que le pipeline a été exécuté pour ces pieds et cette date, "
                "ou sélectionnez un autre Piqueteé / une autre date."
            )
            if missing:
                st.markdown(
                    f'<span class="badge badge-miss">Pieds manquants : '
                    f'{", ".join(str(p) for p in missing)}</span>',
                    unsafe_allow_html=True)