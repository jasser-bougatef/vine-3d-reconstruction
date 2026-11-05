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
║  25 pieds par rang — 5 piquetees de 5 pieds                               ║
║  Page 1 : Analyse ciblée — Passage 1 & Passage 2 côte à côte            ║
║            + switcher de date d'acquisition par passage                  ║
║  Page 2 : Suivi multi-temporel — vue par piquetee (5 pieds)               ║
║            Downsample adaptatif pour gérer le volume de points           ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import json
import os
import re
import glob
import numpy as np
import open3d as o3d
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

N_PIEDS      = 25   # pieds par rang
N_PAR_PIQUETEÉ = 5    # pieds par piquetee → 5 piquetees

# Voxel size utilisé pour le chargement individuel (Page 1)
VOXEL_PIED   = 0.005
# Voxel size utilisé pour la vue piquetee (Page 2) — plus agressif
VOXEL_PIQUETEÉ = 0.012
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
    st.session_state["theme"] = "dark"

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

.piquetee-label {
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

.piquetee-tab {
    display: inline-block; padding: 6px 18px; border-radius: 20px; margin: 2px;
    font-size: 0.78rem; font-weight: 600; cursor: pointer;
    background: {{CARD_BG}}; border: 1px solid {{BORDER}}; color: {{TEXT_MUTED}};
}
.piquetee-tab-active {
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

def find_all_dates_for_pied(pied_num: int, pass_num: int) -> list[tuple[str, str]]:
    """Retourne toutes les (date_label, ply_path) disponibles pour (pied, passage)."""
    pattern = FINAL_OUTPUT / f"pied{pied_num:02d}_pass{pass_num}_*"
    candidates = sorted(glob.glob(str(pattern)), reverse=True)
    found = []
    for job_dir in candidates:
        job_path = Path(job_dir)
        m = re.search(r"(\d{2}-\d{2}-\d{4})$", job_path.name)
        if not m:
            continue
        date_label = m.group(1)
        # Le sous-dossier "data_*" peut ne pas être au format data_{date_label}
        # (l'extracteur C++ nomme la session d'après l'ordre jour/mois/année
        # présent dans le nom du .bag, qui peut différer du format JJ-MM-AAAA
        # utilisé pour le nom du dossier job). On cherche donc le dossier
        # data_* réel plutôt que de construire son nom en dur.
        data_candidates = list(job_path.glob("data_*"))
        if not data_candidates:
            continue
        data_dir = data_candidates[0] / "PointCloud_ply"
        if not data_dir.exists():
            continue
        plys = list(data_dir.glob("Pied(s)_merged_*.ply")) + list(data_dir.glob("*.ply"))
        if plys:
            found.append((date_label, str(plys[0])))
    return found


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
    Toutes les dates disponibles pour chaque (pied, passage).
    """
    result = {}
    for p in range(1, N_PIEDS + 1):
        result[p] = {}
        for pas in (1, 2):
            result[p][pas] = find_all_dates_for_pied(p, pas)
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
    st.markdown('<div class="section-header">Paramètres Pipeline</div>', unsafe_allow_html=True)
    st.markdown("""
<div class="info-box" style="font-family:'JetBrains Mono',monospace;font-size:0.73rem;">
<strong>C++ Extractor</strong><br>
z_min=0.0 m · z_max=1.5 m<br>
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
    st.caption("Stage M1 — IMS Bordeaux — Équipe MOTIVE")


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
        st.session_state["sel_pied"] = 5
    if "sel_date_p1" not in st.session_state:
        st.session_state["sel_date_p1"] = None
    if "sel_date_p2" not in st.session_state:
        st.session_state["sel_date_p2"] = None

    sel_pied = st.session_state["sel_pied"]

    # ── Matrice des piquetees ──────────────────────────────────────────────
    st.markdown('<div class="section-header">Sélection du Pied — 5 Piqueteés × 5 Pieds</div>',
                unsafe_allow_html=True)

    for piquetee in range(1, 6):
        pied_start = (piquetee - 1) * N_PAR_PIQUETEÉ + 1
        pied_end   = piquetee * N_PAR_PIQUETEÉ
        piquetee_cols = st.columns([1.1] + [1] * N_PAR_PIQUETEÉ)

        with piquetee_cols[0]:
            st.markdown(f'<div class="piquetee-label">Piqueteé {piquetee}</div>',
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
    p1_dates_list = avail[sel_pied][1]
    p2_dates_list = avail[sel_pied][2]

    st.markdown(
        f'<div class="section-header">Pied n°{sel_pied} — '
        f'Piqueteé {piquetee_num} · Position {pos_in_piquetee}/{N_PAR_PIQUETEÉ}</div>',
        unsafe_allow_html=True)

    # ── Date switcher côte à côte ────────────────────────────────────────
    sw_col1, sw_col2 = st.columns(2)

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
            st.info("Aucun Passage 1 disponible pour ce pied.")
            p1_available = False
            ply_path_p1  = None

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
            st.info("Aucun Passage 2 disponible pour ce pied.")
            p2_available = False
            ply_path_p2  = None

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
            else:
                st.error(f"Erreur : {status2}")

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


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — SUIVI MULTI-TEMPOREL PAR PIQUETEÉ
# ══════════════════════════════════════════════════════════════════════════

elif page == "📈 Suivi Multi-Temporel":
    st.markdown('<div class="viti-title">Suivi Multi-Temporel — Vue par Piqueteé</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="viti-sub">5 pieds par piquetee · Downsampling adaptatif pour gérer le volume · '
        'Sélectionnez le piquetee, la date et le passage</div>',
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

    st.markdown("---")

    # ── Sélecteur de piquetee (boutons) ────────────────────────────────────
    if "sel_piquetee_p2" not in st.session_state:
        st.session_state["sel_piquetee_p2"] = 1

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
            is_active = (pk == st.session_state["sel_piquetee_p2"])
            icon_pk = "🟢" if n_dispo == N_PAR_PIQUETEÉ else ("🟡" if n_dispo > 0 else "⚫")
            label_pk = f"{'▶ ' if is_active else ''}{icon_pk} Piqueteé {pk}\n({n_dispo}/{N_PAR_PIQUETEÉ})"
            if st.button(label_pk, key=f"pq_btn_{pk}", width="stretch",
                         type="primary" if is_active else "secondary"):
                st.session_state["sel_piquetee_p2"] = pk
                st.rerun()

    sel_piquetee = st.session_state["sel_piquetee_p2"]
    pied_range = range((sel_piquetee - 1) * N_PAR_PIQUETEÉ + 1,
                        sel_piquetee * N_PAR_PIQUETEÉ + 1)

    st.markdown("---")

    # ════════════════════════════════════════════════════════════════════
    # MODE SIMULATION
    # ════════════════════════════════════════════════════════════════════
    if show_simul or not date_choisie:
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
        with ms2: st.metric("Pieds dans le piquetee", N_PAR_PIQUETEÉ)
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

        # ── Chargement des pieds du piquetee sélectionné ───────────────────
        piquetee_pts  = []   # un array par pied (après décalage X)
        piquetee_cols = []
        loaded_info = []   # (pied_num, pas, n_pts)
        missing     = []   # pieds sans données pour cette date

        prog = st.progress(0, text=f"Chargement piquetee {sel_piquetee}…")

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

            # ── Figure 3D du piquetee ───────────────────────────────────────
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

            # ── Métriques globales du piquetee ──────────────────────────────
            mr1, mr2, mr3, mr4, mr5 = st.columns(5)
            with mr1:
                st.metric("Points affichés", f"{n_apres:,}".replace(",", " "))
            with mr2:
                st.metric("Points bruts", f"{n_avant:,}".replace(",", " "))
            with mr3:
                st.metric("Pieds chargés", len(set(p for p, _, _ in loaded_info)))
            with mr4:
                h_pk = round(float(pts_all[:, 2].max() - pts_all[:, 2].min()), 3)
                st.metric("Hauteur piquetee", f"{h_pk} m")
            with mr5:
                st.metric("Voxel size", f"{voxel_choice*1000:.0f} mm")

            # ── Détail par pied dans le piquetee ────────────────────────────
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

            # ── Tableau de disponibilité global (tous piquetees) ────────────
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
                "ou sélectionnez un autre piquetee / une autre date."
            )
            if missing:
                st.markdown(
                    f'<span class="badge badge-miss">Pieds manquants : '
                    f'{", ".join(str(p) for p in missing)}</span>',
                    unsafe_allow_html=True)