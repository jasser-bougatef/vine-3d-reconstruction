#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║   IMS Bordeaux — Supervision 3D Viticulture  |  app_RT.py               ║
║                                                                          ║
║  Compatible avec la structure de sortie du pipeline RT :                 ║
║    final_Output/pied{NN}_pass{1|2}_DD-MM-YYYY/                          ║
║              data_DD-MM-YYYY/PointCloud_ply/Pied(s)_merged_*.ply        ║
║  ET la structure double-passe :                                          ║
║    final_Output/pied{NN}_pass1_and_pass2_DD-MM-YYYY/                   ║
║                                                                          ║
║  25 pieds par rang — 5 piquets de 5 pieds                               ║
║  Page 1 : Analyse ciblée — Passage 1 & 2 côte à côte + switcher date   ║
║  Page 2 : Suivi multi-temporel du rang complet                          ║
║  Page 3 : Tableau de bord temps réel (WebSocket live)                   ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import re
import glob
import numpy as np
import open3d as o3d
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════

# ── Lecture pipeline_config.json ────────────────────────────────────────────
_APP_DIR = Path(__file__).resolve().parent
_CONFIG_PATH = _APP_DIR / "pipeline_config.json"

def _load_cfg() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
            return json.load(_f)
    return {}

_CFG = _load_cfg()

def _p(section, key, default):
    return _CFG.get(section, {}).get(key, default)

PROJECT_ROOT = _APP_DIR  # racine = dossier du script
FINAL_OUTPUT = PROJECT_ROOT / _p("paths", "final_output", "final_Output")
LOGO_PATH    = str(PROJECT_ROOT / "logo_ims.png")
WS_URL       = f'ws://localhost:{_p("network","websocket_port",8765)}'
HTTP_URL     = f'http://localhost:{_p("network","http_port",8080)}'

N_PIEDS      = 25
N_PAR_PIQUET = 5

# ══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="IMS Bordeaux · Viticulture RT",
    page_icon="🍇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Initialiser le thème dans session_state ───────────────────────────────
if "theme" not in st.session_state:
    st.session_state["theme"] = "dark"

is_dark = st.session_state["theme"] == "dark"

# ── Variables CSS selon thème ─────────────────────────────────────────────
if is_dark:
    BG_APP    = "#0f1117"
    BG_SIDE   = "#161b27"
    BG_CARD   = "#1a2035"
    BG_INFO   = "#131929"
    BG_PLOT   = "#0f1117"
    BORDER    = "#263050"
    BORDER2   = "#232b3e"
    T_MAIN    = "#e8eaf0"
    T_SUB     = "#6b7a99"
    T_VALUE   = "#e8eaf0"
    T_GRID    = "#232b3e"
    ACCENT1   = "#4e9af1"
    ACCENT2   = "#a78bfa"
    GREEN_C   = "#4ade80"
    RED_C     = "#f87171"
    WARN_BG   = "#3b1515"
    WARN_BORDER="#7f1d1d"
    OK_BG     = "#14532d"
    OK_BORDER = "#166534"
    SIM_BG    = "#1e2a4a"
    SIM_BORDER= "#2d4070"
else:
    BG_APP    = "#f5f7fa"
    BG_SIDE   = "#eef1f6"
    BG_CARD   = "#ffffff"
    BG_INFO   = "#f0f4f9"
    BG_PLOT   = "#ffffff"
    BORDER    = "#cdd5e0"
    BORDER2   = "#d8e0ea"
    T_MAIN    = "#1a2340"
    T_SUB     = "#5a6a8a"
    T_VALUE   = "#1a2340"
    T_GRID    = "#dde3ed"
    ACCENT1   = "#1d6fdb"
    ACCENT2   = "#7c3aed"
    GREEN_C   = "#16a34a"
    RED_C     = "#dc2626"
    WARN_BG   = "#fef2f2"
    WARN_BORDER="#fca5a5"
    OK_BG     = "#f0fdf4"
    OK_BORDER = "#86efac"
    SIM_BG    = "#eff6ff"
    SIM_BORDER= "#93c5fd"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
.stApp {{ background-color: {BG_APP}; color: {T_MAIN}; }}
[data-testid="stSidebar"] {{ background: {BG_SIDE}; border-right: 1px solid {BORDER}; }}

.viti-title {{
    font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em;
    background: linear-gradient(120deg, {ACCENT1} 0%, {ACCENT2} 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 0.1rem;
}}
.viti-sub {{ color: {T_SUB}; font-size: 0.82rem; font-weight: 400; margin-bottom: 1.2rem; }}

.metric-card {{
    background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 13px 17px; margin-bottom: 7px;
}}
.metric-card .label {{ font-size: 0.71rem; color: {T_SUB}; text-transform: uppercase;
                       letter-spacing: 0.07em; margin-bottom: 4px; }}
.metric-card .value {{ font-size: 1.05rem; font-weight: 600; color: {T_VALUE};
                       font-family: 'JetBrains Mono', monospace; }}
.metric-card .delta {{ font-size: 0.71rem; margin-top: 2px; }}

.piquet-label {{
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.1em;
    color: {ACCENT1}; text-transform: uppercase; margin-bottom: 5px;
    border-left: 3px solid {ACCENT1}; padding-left: 8px; line-height: 2.2;
}}
.section-header {{
    font-size: 0.77rem; font-weight: 600; color: {ACCENT1};
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid {BORDER2}; padding-bottom: 5px; margin-bottom: 11px;
}}
.badge {{ display: inline-block; padding: 2px 10px; border-radius: 20px;
          font-size: 0.7rem; font-weight: 600; letter-spacing: 0.05em; }}
.badge-ok   {{ background: {OK_BG};   color: {GREEN_C}; border: 1px solid {OK_BORDER}; }}
.badge-miss {{ background: {WARN_BG}; color: {RED_C};   border: 1px solid {WARN_BORDER}; }}
.badge-sim  {{ background: {SIM_BG};  color: {ACCENT1}; border: 1px solid {SIM_BORDER}; }}
.badge-rt   {{ background: {OK_BG};   color: {GREEN_C}; border: 1px solid {OK_BORDER}; font-size: 0.72rem; }}

.info-box {{
    background: {BG_INFO}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 11px 15px; font-size: 0.79rem; color: {T_SUB}; margin-bottom: 11px;
}}
.info-box strong {{ color: {T_MAIN}; }}

.pass-header {{
    text-align: center; font-size: 0.81rem; font-weight: 600;
    padding: 7px 0; border-radius: 8px 8px 0 0; margin-bottom: 4px;
}}
.pass1-header {{ background: {SIM_BG}; color: {ACCENT1}; border: 1px solid {SIM_BORDER}; }}
.pass2-header {{ background: {"#2d1a3e" if is_dark else "#faf5ff"}; color: {ACCENT2};
                 border: 1px solid {"#5b2d82" if is_dark else "#c4b5fd"}; }}

.rt-card {{
    background: {BG_CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 14px 16px; margin-bottom: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,{"0.25" if is_dark else "0.06"});
}}
.rt-title {{ font-size: 0.72rem; color: {T_SUB}; text-transform: uppercase;
             letter-spacing: 0.07em; margin-bottom: 4px; }}
.rt-value {{ font-size: 1.4rem; font-weight: 700; font-family: 'JetBrains Mono',monospace;
             color: {T_MAIN}; }}
.rt-sub {{ font-size: 0.7rem; color: {T_SUB}; margin-top: 2px; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES — DÉTECTION DES PLY (double-passe ET single-passe)
# ══════════════════════════════════════════════════════════════════════════

def find_all_dates_for_pied(pied_num: int, pass_num: int) -> list:
    """Retourne toutes les (date_label, ply_path) pour (pied, passage)."""
    patterns = [
        FINAL_OUTPUT / f"pied{pied_num:02d}_pass{pass_num}_*",
        FINAL_OUTPUT / f"pied{pied_num:02d}_pass1_and_pass2_*",
    ]
    found = []
    seen  = set()
    for pattern in patterns:
        for job_dir in sorted(glob.glob(str(pattern)), reverse=True):
            job_path   = Path(job_dir)
            m = re.search(r"(\d{2}-\d{2}-\d{4})$", job_path.name)
            if not m:
                continue
            date_label = m.group(1)
            data_dir   = job_path / f"data_{date_label}" / "PointCloud_ply"
            if not data_dir.exists():
                continue
            plys = (list(data_dir.glob("Pied(s)_merged_*.ply"))
                    + list(data_dir.glob("Pied_merged_*.ply"))
                    + list(data_dir.glob("Pied*.ply")))
            if plys:
                key = (date_label, str(plys[0]))
                if key not in seen:
                    seen.add(key)
                    found.append(key)
    return found


@st.cache_data(show_spinner=False, ttl=300)
def load_ply(path_str: str) -> tuple:
    try:
        pcd = o3d.io.read_point_cloud(path_str)
        if len(pcd.points) == 0:
            return None, None, "EMPTY"
        pcd = pcd.voxel_down_sample(voxel_size=0.005)
        pts = np.asarray(pcd.points)
        col = np.asarray(pcd.colors) if pcd.has_colors() else None
        # Remapping axes : Z = hauteur
        out = np.zeros_like(pts)
        out[:, 0] = pts[:, 0]
        out[:, 1] = -pts[:, 2]
        out[:, 2] = pts[:, 1]
        return out, col, "OK"
    except Exception as e:
        return None, None, f"ERROR:{e}"


@st.cache_data(show_spinner=False, ttl=60)
def scan_available_pieds() -> dict:
    result = {}
    for p in range(1, N_PIEDS + 1):
        result[p] = {1: find_all_dates_for_pied(p, 1),
                     2: find_all_dates_for_pied(p, 2)}
    return result


def get_session_metadata(avail: dict) -> dict:
    all_dates, total_jobs = set(), 0
    for p in avail.values():
        for dl in p.values():
            if dl:
                total_jobs += 1
                for d, _ in dl:
                    all_dates.add(d)
    return {
        "dates":      sorted(all_dates) if all_dates else ["—"],
        "total_jobs": total_jobs,
        "sensor":     "Intel RealSense D4xx RGB-D",
        "crs":        "Lambert93 / EPSG:2154",
        "pipeline":   "PPK → Sync → DBSCAN → ICP/RANSAC",
    }


def make_3d_figure(points, colors, title="", height=420):
    if colors is not None:
        cvals = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r,g,b in colors]
    else:
        z = points[:,2]
        cvals = (z - z.min()) / (float(np.ptp(z)) + 1e-9)

    fig = go.Figure(data=[go.Scatter3d(
        x=points[:,0], y=points[:,1], z=points[:,2],
        mode='markers',
        marker=dict(size=1.8, color=cvals,
                    colorscale='Viridis' if colors is None else None,
                    opacity=0.9),
        hovertemplate='x:%{x:.3f}<br>y:%{y:.3f}<br>z:%{z:.3f}<extra></extra>',
    )])
    fig.update_layout(
        margin=dict(l=0,r=0,b=0,t=30),
        title=dict(text=title, font=dict(color=T_MAIN, size=12), x=0.04),
        paper_bgcolor=BG_PLOT,
        scene=dict(
            bgcolor=BG_PLOT,
            xaxis=dict(showbackground=False, gridcolor=T_GRID, color=T_SUB, title='X'),
            yaxis=dict(showbackground=False, gridcolor=T_GRID, color=T_SUB, title='Y'),
            zaxis=dict(showbackground=False, gridcolor=T_GRID, color=T_SUB, title='Z'),
            aspectmode='data',
            camera=dict(eye=dict(x=0,y=-2.0,z=0.5), up=dict(x=0,y=0,z=1)),
        ),
        height=height,
    )
    return fig


def pied_metrics(pts) -> dict:
    if pts is None or len(pts) == 0:
        return {}
    return {
        "n_points":   len(pts),
        "hauteur":    round(float(pts[:,2].max() - pts[:,2].min()), 3),
        "largeur":    round(float(np.ptp(pts[:,0])), 3),
        "profondeur": round(float(np.ptp(pts[:,1])), 3),
        "z_max":      round(float(pts[:,2].max()), 3),
    }


# ══════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════

avail = scan_available_pieds()
meta  = get_session_metadata(avail)

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=210)
    else:
        st.markdown("### 🍇 IMS Bordeaux")

    # ── Switcher jour / nuit ─────────────────────────────────────────────
    st.markdown("---")
    col_th1, col_th2 = st.columns([3, 1])
    with col_th1:
        st.markdown(f"**{'🌙 Thème sombre' if is_dark else '☀️ Thème clair'}**")
    with col_th2:
        if st.button("🔄", key="theme_toggle", help="Basculer jour / nuit"):
            st.session_state["theme"] = "light" if is_dark else "dark"
            st.rerun()

    st.markdown("---")
    st.markdown("**🧭 Navigation**")
    page = st.radio(
        "Navigation",
        ["🔍 Analyse par Pied", "📈 Suivi Multi-Temporel", "📡 Temps Réel"],
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown('<div class="section-header">Informations Session</div>',
                unsafe_allow_html=True)
    dates_str = "<br>".join(f"• {d}" for d in meta["dates"]) \
                if meta["dates"] != ["—"] else "—"
    st.markdown(f"""
<div class="info-box">
<strong>📅 Dates d'acquisition</strong><br>{dates_str}<br><br>
<strong>🤖 Capteur</strong><br>{meta["sensor"]}<br><br>
<strong>🗺️ CRS</strong><br>{meta["crs"]}<br><br>
<strong>⚙️ Pipeline</strong><br>{meta["pipeline"]}
</div>""", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    with c1: st.metric("Jobs OK", meta["total_jobs"])
    with c2: st.metric("Pieds", N_PIEDS)

    st.markdown("---")
    st.markdown('<div class="section-header">Paramètres Pipeline</div>',
                unsafe_allow_html=True)
    st.markdown(f"""
<div class="info-box" style="font-family:'JetBrains Mono',monospace;font-size:0.73rem;">
<strong>C++ Extractor</strong><br>
z_min=0.0 m · z_max=1.5 m<br>rot_z=270°<br><br>
<strong>Filtre 3D</strong><br>
voxel=0.005 m · eps=0.12<br>DBSCAN · radius_filter=True<br><br>
<strong>Registration</strong><br>
RANSAC + ICP PointToPlane<br>window_size=4 frames
</div>""", unsafe_allow_html=True)

    st.markdown("---")
    st.caption("Stage M1 — IMS Bordeaux — Équipe MOTIVE")


# ══════════════════════════════════════════════════════════════════════════
# PAGE 1 — ANALYSE PAR PIED
# ══════════════════════════════════════════════════════════════════════════

if page == "🔍 Analyse par Pied":
    st.markdown('<div class="viti-title">Analyse Géométrique par Pied de Vigne</div>',
                unsafe_allow_html=True)
    st.markdown(
        '<div class="viti-sub">Sélectionnez un pied · Choisissez la date · '
        'Passage 1 (aller) et Passage 2 (retour) côte à côte</div>',
        unsafe_allow_html=True)

    if "sel_pied"    not in st.session_state: st.session_state["sel_pied"]    = 5
    if "sel_date_p1" not in st.session_state: st.session_state["sel_date_p1"] = None
    if "sel_date_p2" not in st.session_state: st.session_state["sel_date_p2"] = None

    sel_pied = st.session_state["sel_pied"]

    # ── Matrice des piquets ──────────────────────────────────────────────
    st.markdown('<div class="section-header">Sélection du Pied — 5 Piquets × 5 Pieds</div>',
                unsafe_allow_html=True)
    for piquet in range(1, 6):
        pied_start = (piquet - 1) * N_PAR_PIQUET + 1
        pcols = st.columns([1.1] + [1] * N_PAR_PIQUET)
        with pcols[0]:
            st.markdown(f'<div class="piquet-label">Piquet {piquet}</div>',
                        unsafe_allow_html=True)
        for i, pn in enumerate(range(pied_start, pied_start + N_PAR_PIQUET)):
            with pcols[i + 1]:
                has1 = bool(avail[pn][1])
                has2 = bool(avail[pn][2])
                icon = "🟢" if (has1 and has2) else ("🟡" if (has1 or has2) else "⚫")
                is_sel = pn == sel_pied
                if st.button(f"{'▶' if is_sel else ''}{icon} P{pn}",
                             key=f"btn_{pn}", width="stretch",
                             type="primary" if is_sel else "secondary"):
                    st.session_state["sel_pied"]    = pn
                    st.session_state["sel_date_p1"] = None
                    st.session_state["sel_date_p2"] = None
                    st.rerun()

    st.caption("🟢 P1 & P2 disponibles  |  🟡 Un seul passage  |  ⚫ Non filmé")
    st.markdown("---")

    piquet_num    = ((sel_pied - 1) // N_PAR_PIQUET) + 1
    pos_in_piquet = ((sel_pied - 1) % N_PAR_PIQUET) + 1
    p1_dates_list = avail[sel_pied][1]
    p2_dates_list = avail[sel_pied][2]

    st.markdown(
        f'<div class="section-header">Pied n°{sel_pied} — '
        f'Piquet {piquet_num} · Position {pos_in_piquet}/{N_PAR_PIQUET}</div>',
        unsafe_allow_html=True)

    sw1, sw2 = st.columns(2)

    with sw1:
        st.markdown('<div class="pass-header pass1-header">🔵 Passage 1 — Aller</div>',
                    unsafe_allow_html=True)
        if p1_dates_list:
            opts1 = [d for d, _ in p1_dates_list]
            if st.session_state["sel_date_p1"] not in opts1:
                st.session_state["sel_date_p1"] = opts1[0]
            sd1 = st.selectbox("📅 Date — Passage 1", opts1,
                               index=opts1.index(st.session_state["sel_date_p1"]),
                               key="dsw1", format_func=lambda d: f"🗓 {d}")
            st.session_state["sel_date_p1"] = sd1
            ply_p1 = next(p for d, p in p1_dates_list if d == sd1)
            p1_ok = True
        else:
            st.info("Aucun Passage 1 disponible.")
            p1_ok, ply_p1, sd1 = False, None, None

    with sw2:
        st.markdown('<div class="pass-header pass2-header">🟣 Passage 2 — Retour</div>',
                    unsafe_allow_html=True)
        if p2_dates_list:
            opts2 = [d for d, _ in p2_dates_list]
            if st.session_state["sel_date_p2"] not in opts2:
                st.session_state["sel_date_p2"] = opts2[0]
            sd2 = st.selectbox("📅 Date — Passage 2", opts2,
                               index=opts2.index(st.session_state["sel_date_p2"]),
                               key="dsw2", format_func=lambda d: f"🗓 {d}")
            st.session_state["sel_date_p2"] = sd2
            ply_p2 = next(p for d, p in p2_dates_list if d == sd2)
            p2_ok = True
        else:
            st.info("Aucun Passage 2 disponible.")
            p2_ok, ply_p2, sd2 = False, None, None

    # ── Vues 3D côte à côte ──────────────────────────────────────────────
    pts1, col1, m1 = None, None, {}
    pts2, col2, m2 = None, None, {}
    vc1, vc2 = st.columns(2)

    with vc1:
        if p1_ok:
            with st.spinner("Chargement passage 1…"):
                pts1, col1, st1 = load_ply(ply_p1)
            if st1 == "OK" and pts1 is not None:
                st.plotly_chart(make_3d_figure(pts1, col1,
                    f"Pied {sel_pied:02d} · P1 · {sd1}"), use_container_width=True)
                m1 = pied_metrics(pts1)
                ca, cb = st.columns(2)
                with ca: st.metric("Points", f"{m1.get('n_points',0):,}".replace(",", " "))
                with cb: st.metric("Hauteur", f"{m1.get('hauteur',0)} m")
            else:
                st.error(f"Erreur PLY P1 : {st1}")

    with vc2:
        if p2_ok:
            with st.spinner("Chargement passage 2…"):
                pts2, col2, st2 = load_ply(ply_p2)
            if st2 == "OK" and pts2 is not None:
                st.plotly_chart(make_3d_figure(pts2, col2,
                    f"Pied {sel_pied:02d} · P2 · {sd2}"), use_container_width=True)
                m2 = pied_metrics(pts2)
                ca, cb = st.columns(2)
                with ca: st.metric("Points", f"{m2.get('n_points',0):,}".replace(",", " "))
                with cb: st.metric("Hauteur", f"{m2.get('hauteur',0)} m")
            else:
                st.error(f"Erreur PLY P2 : {st2}")

    # ── Tableau comparatif ───────────────────────────────────────────────
    if m1 or m2:
        st.markdown("---")
        st.markdown(f'<div class="section-header">Comparaison P1 / P2 — Pied {sel_pied}</div>',
                    unsafe_allow_html=True)
        mc1, mc2, mc3, mc4, mc5 = st.columns(5)

        def cmp_card(col, label, key, unit=""):
            v1 = m1.get(key); v2 = m2.get(key)
            s1 = f"{v1:,}{unit}".replace(",", " ") if isinstance(v1, int) \
                 else (f"{v1}{unit}" if v1 is not None else "—")
            s2 = f"{v2:,}{unit}".replace(",", " ") if isinstance(v2, int) \
                 else (f"{v2}{unit}" if v2 is not None else "—")
            dh = ""
            if v1 is not None and v2 is not None and isinstance(v1, (int, float)):
                diff = round(v2 - v1, 3)
                sign = "+" if diff >= 0 else ""
                dc = GREEN_C if diff >= 0 else RED_C
                dh = f'<div class="delta" style="color:{dc}">Δ {sign}{diff}{unit}</div>'
            col.markdown(f"""
<div class="metric-card">
<div class="label">{label}</div>
<div class="value" style="font-size:0.88rem">
  <span style="color:{ACCENT1}">P1: {s1}</span><br>
  <span style="color:{ACCENT2}">P2: {s2}</span>
</div>{dh}
</div>""", unsafe_allow_html=True)

        cmp_card(mc1, "Nb points",  "n_points")
        cmp_card(mc2, "Hauteur",    "hauteur",    " m")
        cmp_card(mc3, "Largeur",    "largeur",    " m")
        cmp_card(mc4, "Profondeur", "profondeur", " m")
        cmp_card(mc5, "Z max",      "z_max",      " m")

    # ── Carte de couverture ──────────────────────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-header">Couverture du Rang</div>',
                unsafe_allow_html=True)
    cov1 = [1 if avail[p][1] else 0 for p in range(1, N_PIEDS+1)]
    cov2 = [1 if avail[p][2] else 0 for p in range(1, N_PIEDS+1)]
    pieds_x = list(range(1, N_PIEDS+1))

    fig_cov = go.Figure()
    fig_cov.add_trace(go.Bar(name="Passage 1", x=pieds_x, y=cov1, width=0.38, offset=-0.19,
        marker_color=[ACCENT1 if v else BG_CARD for v in cov1],
        marker_line_color=[ACCENT1 if v else BORDER for v in cov1], marker_line_width=1))
    fig_cov.add_trace(go.Bar(name="Passage 2", x=pieds_x, y=cov2, width=0.38, offset=0.19,
        marker_color=[ACCENT2 if v else BG_CARD for v in cov2],
        marker_line_color=[ACCENT2 if v else BORDER for v in cov2], marker_line_width=1))
    for pk in range(1, 5):
        fig_cov.add_vline(x=pk*N_PAR_PIQUET+0.5, line_dash="dot",
                          line_color=T_GRID, line_width=1)
    fig_cov.add_vline(x=sel_pied, line_color="#fbbf24", line_width=2,
                      annotation_text=f"P{sel_pied}", annotation_font_color="#fbbf24",
                      annotation_position="top")
    fig_cov.update_layout(
        paper_bgcolor=BG_PLOT, plot_bgcolor=BG_PLOT,
        barmode="overlay", height=190,
        margin=dict(l=0,r=0,t=35,b=10),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=1.18,
                    font=dict(color=T_MAIN), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title="Numéro de pied", color=T_SUB, gridcolor=T_GRID,
                   tickmode="linear", tick0=1, dtick=1),
        yaxis=dict(visible=False),
    )
    st.plotly_chart(fig_cov, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — SUIVI MULTI-TEMPOREL
# ══════════════════════════════════════════════════════════════════════════

elif page == "📈 Suivi Multi-Temporel":
    st.markdown('<div class="viti-title">Suivi Multi-Temporel du Rang</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="viti-sub">Évolution de la croissance entre campagnes</div>',
                unsafe_allow_html=True)

    all_dates = sorted({d for p in avail.values()
                          for dl in p.values() for d, _ in dl})

    ctrl1, ctrl2, ctrl3 = st.columns([2,2,1])
    with ctrl1:
        date_choisie = st.select_slider("📅 Campagne",
            options=all_dates if all_dates else ["—"],
            value=all_dates[-1] if all_dates else "—") if all_dates else None
    with ctrl2:
        pass_choice = st.radio("Passage", ["Passage 1","Passage 2","Les deux"],
                               horizontal=True)
    with ctrl3:
        show_simul = st.checkbox("Mode simulation", value=not bool(all_dates))

    passes_to_show = ([1] if pass_choice=="Passage 1"
                      else [2] if pass_choice=="Passage 2" else [1,2])
    st.markdown("---")

    if show_simul or not date_choisie:
        st.markdown('<span class="badge badge-sim">MODE SIMULATION</span>',
                    unsafe_allow_html=True)
        st.markdown(" ")
        month_labels = ["Déc. 2024","Jan. 2025","Fév. 2025","Mars 2025",
                        "Avr. 2025","Mai 2025","Juin 2025","Juil. 2025"]
        mi = month_labels.index(
            st.select_slider("Mois simulé", options=month_labels, value="Juil. 2025"))

        @st.cache_data(show_spinner=False)
        def simul_rang(mi):
            cr = 1.0 + mi * 0.25
            pts, cols = [], []
            for c in range(N_PIEDS):
                xo = c * 1.2; nt=400; nf=int(800*cr)
                pts += [np.column_stack([np.random.normal(xo,0.04,nt),
                                         np.random.normal(0,0.04,nt),
                                         np.random.uniform(0,0.5,nt)]),
                        np.column_stack([np.random.normal(xo,0.25*cr,nf),
                                         np.random.normal(0,0.15*cr,nf),
                                         np.random.uniform(0.4,0.7*cr,nf)])]
                cols += [np.tile([0.55,0.27,0.07],(nt,1)),
                         np.tile([0.13,0.55,0.13],(nf,1))]
            return np.vstack(pts), np.vstack(cols)

        with st.spinner("Simulation…"):
            ps, cs = simul_rang(mi)
            fig_s = go.Figure(data=[go.Scatter3d(
                x=ps[:,0],y=ps[:,1],z=ps[:,2],mode='markers',
                marker=dict(size=1.2,opacity=0.85,
                    color=[f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r,g,b in cs]))])
            fig_s.update_layout(paper_bgcolor=BG_PLOT, height=500,
                margin=dict(l=0,r=0,b=0,t=20),
                scene=dict(bgcolor=BG_PLOT,
                    xaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Rang"),
                    yaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Profondeur"),
                    zaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Hauteur"),
                    aspectmode="data",
                    camera=dict(eye=dict(x=0,y=-2.5,z=0.7),up=dict(x=0,y=0,z=1))))
            st.plotly_chart(fig_s, use_container_width=True)
        ms1,ms2,ms3,ms4 = st.columns(4)
        with ms1: st.metric("Volume feuillage", f"{1.2+mi*0.6:.2f} m³", "+0.6 m³/mois")
        with ms2: st.metric("Pieds simulés", N_PIEDS)
        with ms3: st.metric("Hauteur moy.", f"{0.5+mi*0.03:.2f} m")
        with ms4: st.metric("Piquets", N_PIEDS//N_PAR_PIQUET)
    else:
        st.markdown(f'<span class="badge badge-ok">DONNÉES RÉELLES — {date_choisie}</span>',
                    unsafe_allow_html=True)
        all_pts, all_cols, loaded = [], [], []
        prog = st.progress(0, text="Chargement…")
        for i, pn in enumerate(range(1, N_PIEDS+1)):
            prog.progress(i/N_PIEDS, text=f"Pied {pn}/{N_PIEDS}…")
            xo = (pn-1)*1.2
            for pas in passes_to_show:
                match = [(d,p) for d,p in avail[pn][pas] if d == date_choisie]
                if not match: continue
                pts, col, status = load_ply(match[0][1])
                if status == "OK" and pts is not None:
                    sh = pts.copy(); sh[:,0] += xo; sh[:,1] += (0.8 if pas==2 else 0.0)
                    all_pts.append(sh)
                    if col is not None:
                        tc = col.copy()
                        if pas==2: tc[:,2] = np.clip(tc[:,2]+0.15,0,1)
                        all_cols.append(tc)
                    else:
                        all_cols.append(np.tile([0.4,0.7,0.4],(len(pts),1)))
                    loaded.append((pn,pas))
        prog.empty()
        if all_pts:
            pa = np.vstack(all_pts); ca = np.vstack(all_cols)
            fig_r = go.Figure(data=[go.Scatter3d(
                x=pa[:,0],y=pa[:,1],z=pa[:,2],mode='markers',
                marker=dict(size=1.2,opacity=0.85,
                    color=[f'rgb({int(r*255)},{int(g*255)},{int(b*255)})' for r,g,b in ca]),
                hovertemplate='x:%{x:.3f} y:%{y:.3f} z:%{z:.3f}<extra></extra>')])
            fig_r.update_layout(
                paper_bgcolor=BG_PLOT, height=520,
                title=dict(text=f"Rang complet — {date_choisie} — {len(loaded)} nuages",
                           font=dict(color=T_MAIN,size=13), x=0.03),
                margin=dict(l=0,r=0,b=0,t=40),
                scene=dict(bgcolor=BG_PLOT,
                    xaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Rang"),
                    yaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Passage"),
                    zaxis=dict(showbackground=False,gridcolor=T_GRID,color=T_SUB,title="Hauteur"),
                    aspectmode="data",
                    camera=dict(eye=dict(x=0,y=-2.5,z=0.7),up=dict(x=0,y=0,z=1))))
            st.plotly_chart(fig_r, use_container_width=True)
            mr1,mr2,mr3,mr4 = st.columns(4)
            with mr1: st.metric("Points total", f"{len(pa):,}".replace(",", " "))
            with mr2: st.metric("Pieds chargés", len({p for p,_ in loaded}))
            with mr3: st.metric("Hauteur rang", f"{round(float(pa[:,2].ptp()),3)} m")
            with mr4: st.metric("Passages", f"{len({ps for _,ps in loaded})}/2")
        else:
            st.warning(f"Aucun nuage trouvé pour **{date_choisie}**. "
                       "Activez le mode simulation ou vérifiez le pipeline.")


# ══════════════════════════════════════════════════════════════════════════
# PAGE 3 — TEMPS RÉEL (embed WebSocket dashboard + viewer)
# ══════════════════════════════════════════════════════════════════════════

elif page == "📡 Temps Réel":
    st.markdown('<div class="viti-title">Tableau de Bord Temps Réel</div>',
                unsafe_allow_html=True)
    st.markdown(
        f'<div class="viti-sub">Connexion WebSocket → <code>{WS_URL}</code> · '
        f'Dashboard → <a href="{HTTP_URL}/webapp/index.html" target="_blank">'
        f'{HTTP_URL}/webapp/index.html</a></div>',
        unsafe_allow_html=True)

    # ── Liens rapides ─────────────────────────────────────────────────────
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        st.markdown(
            f'<a href="{HTTP_URL}/webapp/index.html" target="_blank" '
            f'style="display:block;text-align:center;padding:12px;'
            f'background:{BG_CARD};border:1px solid {BORDER};border-radius:8px;'
            f'color:{ACCENT1};text-decoration:none;font-weight:600">📊 Dashboard Live</a>',
            unsafe_allow_html=True)
    with lc2:
        st.markdown(
            f'<a href="{HTTP_URL}/webapp/viewer.html" target="_blank" '
            f'style="display:block;text-align:center;padding:12px;'
            f'background:{BG_CARD};border:1px solid {BORDER};border-radius:8px;'
            f'color:{ACCENT2};text-decoration:none;font-weight:600">🔷 Viewer 3D</a>',
            unsafe_allow_html=True)
    with lc3:
        if st.button("🔄 Rafraîchir pieds", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    st.markdown("---")

    # ── Embed du dashboard WebSocket ──────────────────────────────────────
    tab1, tab2 = st.tabs(["📊 Dashboard", "🔷 Viewer 3D"])

    with tab1:
        components.iframe(
            f"{HTTP_URL}/webapp/index.html",
            height=720, scrolling=True)

    with tab2:
        components.iframe(
            f"{HTTP_URL}/webapp/viewer.html",
            height=720, scrolling=False)

    st.markdown("---")

    # ── Pieds reconstruits disponibles en local ────────────────────────────
    st.markdown('<div class="section-header">Pieds Reconstruits — Disponibles Localement</div>',
                unsafe_allow_html=True)
    total_pts = 0
    rows = []
    for pn in range(1, N_PIEDS+1):
        for pas in [1, 2]:
            for date, path in avail[pn][pas]:
                pcd_info = ""
                try:
                    pcd = o3d.io.read_point_cloud(path)
                    n   = len(pcd.points)
                    total_pts += n
                    pcd_info = f"{n:,}".replace(",", " ")
                except Exception:
                    pcd_info = "—"
                rows.append({"Pied": f"pied{pn:02d}", "Passage": pas,
                             "Date": date, "Points": pcd_info, "Chemin": path})

    if rows:
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df[["Pied","Passage","Date","Points"]], use_container_width=True,
                     hide_index=True)
        st.metric("Total points (tous pieds)", f"{total_pts:,}".replace(",", " "))
    else:
        st.info("Aucun PLY disponible dans final_Output/. "
                "Lancez le pipeline ou utilisez le mode simulation.")