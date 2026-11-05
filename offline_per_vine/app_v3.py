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
║  25 pieds par rang — 5 piquets de 5 pieds                               ║
║  Page 1 : Analyse ciblée avec vue Passage 1 & Passage 2 côte à côte     ║
║            + switcher de date d'acquisition par passage                  ║
║  Page 2 : Suivi multi-temporel du rang complet                          ║
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

# On cherche le JSON dans le même dossier que ce script
config_file = Path(__file__).resolve().parent / "pipeline_config.json"

if not config_file.exists():
    st.error(f"❌ [ERREUR] Le fichier de configuration {config_file} est introuvable.")
    st.stop()  # st.stop() arrête proprement l'application Streamlit

with open(config_file, "r", encoding="utf-8") as f:
    config = json.load(f)

# Lecture des chemins depuis le JSON (sans .strip() pour conserver ton espace)
PROJECT_ROOT = Path(config["project_root"])
FINAL_OUTPUT = PROJECT_ROOT / config["paths"]["final_output"]

# Le chemin du logo est lu directement depuis le JSON
LOGO_PATH    = config["paths"]["logo_path"]

N_PIEDS      = 25  # pieds par rang
N_PAR_PIQUET = 5   # pieds par piquet → 5 piquets

# ══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG & CSS
# ══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="IMS Bordeaux · Viticulture 3D",
    page_icon="🍇",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.stApp { background-color: #0f1117; color: #e8eaf0; }
[data-testid="stSidebar"] { background: #161b27; border-right: 1px solid #232b3e; }

.viti-title {
    font-size: 1.65rem; font-weight: 700; letter-spacing: -0.02em;
    background: linear-gradient(120deg, #7eb8f7 0%, #4e9af1 50%, #a78bfa 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    margin-bottom: 0.1rem;
}
.viti-sub { color: #6b7a99; font-size: 0.82rem; font-weight: 400; margin-bottom: 1.5rem; }

.metric-card {
    background: #1a2035; border: 1px solid #263050; border-radius: 10px;
    padding: 14px 18px; margin-bottom: 8px;
}
.metric-card .label { font-size: 0.72rem; color: #6b7a99; text-transform: uppercase;
                      letter-spacing: 0.07em; margin-bottom: 4px; }
.metric-card .value { font-size: 1.1rem; font-weight: 600; color: #e8eaf0;
                      font-family: 'JetBrains Mono', monospace; }
.metric-card .delta { font-size: 0.72rem; color: #4ade80; margin-top: 2px; }

.piquet-label {
    font-size: 0.7rem; font-weight: 600; letter-spacing: 0.1em;
    color: #4e9af1; text-transform: uppercase; margin-bottom: 6px;
    border-left: 3px solid #4e9af1; padding-left: 8px; line-height: 2.2;
}

.section-header {
    font-size: 0.78rem; font-weight: 600; color: #4e9af1;
    text-transform: uppercase; letter-spacing: 0.08em;
    border-bottom: 1px solid #232b3e; padding-bottom: 6px; margin-bottom: 12px;
}

.badge { display: inline-block; padding: 2px 10px; border-radius: 20px;
         font-size: 0.7rem; font-weight: 600; letter-spacing: 0.05em; }
.badge-ok   { background: #14532d; color: #4ade80; border: 1px solid #166534; }
.badge-miss { background: #3b1515; color: #f87171; border: 1px solid #7f1d1d; }
.badge-sim  { background: #1e2a4a; color: #7eb8f7; border: 1px solid #2d4070; }

.info-box {
    background: #131929; border: 1px solid #263050; border-radius: 8px;
    padding: 12px 16px; font-size: 0.8rem; color: #8a9bc0; margin-bottom: 12px;
}
.info-box strong { color: #c5d3ef; }

.pass-header {
    text-align: center; font-size: 0.82rem; font-weight: 600;
    padding: 8px 0; border-radius: 8px 8px 0 0; margin-bottom: 4px;
}
.pass1-header { background: #1a2d4a; color: #7eb8f7; border: 1px solid #2d4a7a; }
.pass2-header { background: #2d1a3e; color: #c084fc; border: 1px solid #5b2d82; }

.date-switcher {
    background: #131929; border: 1px solid #263050; border-radius: 8px;
    padding: 10px 14px; margin-bottom: 10px; font-size: 0.8rem;
}
.date-switcher .ds-label { color: #6b7a99; font-size: 0.72rem;
                            text-transform: uppercase; letter-spacing: 0.06em; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
# UTILITAIRES — DÉTECTION DES PLY
# ══════════════════════════════════════════════════════════════════════════

def find_all_dates_for_pied(pied_num: int, pass_num: int) -> list[tuple[str, str]]:
    """
    Retourne toutes les (date_label, ply_path) disponibles pour (pied, passage).
    Triées du plus récent au plus ancien.
    """
    pattern = FINAL_OUTPUT / f"pied{pied_num:02d}_pass{pass_num}_*"
    candidates = sorted(glob.glob(str(pattern)), reverse=True)
    found = []
    for job_dir in candidates:
        job_path = Path(job_dir)
        m = re.search(r"(\d{2}-\d{2}-\d{4})$", job_path.name)
        if not m:
            continue
        date_label = m.group(1)
        data_dir = job_path / f"data_{date_label}" / "PointCloud_ply"
        if not data_dir.exists():
            continue
        plys = list(data_dir.glob("Pied(s)_merged_*.ply")) + list(data_dir.glob("*.ply"))
        if plys:
            found.append((date_label, str(plys[0])))
    return found


@st.cache_data(show_spinner=False, ttl=300)
def load_ply(path_str: str) -> tuple:
    """Charge un PLY et retourne (points, colors, status)."""
    try:
        pcd = o3d.io.read_point_cloud(path_str)
        if len(pcd.points) == 0:
            return None, None, "EMPTY"
        pcd = pcd.voxel_down_sample(voxel_size=0.005)
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
                   height: int = 420) -> go.Figure:
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
            size=1.8,
            color=color_vals,
            colorscale='Viridis' if colors is None else None,
            opacity=0.9,
        ),
        hovertemplate='x: %{x:.3f}<br>y: %{y:.3f}<br>z: %{z:.3f}<extra></extra>',
    )])
    fig.update_layout(
        margin=dict(l=0, r=0, b=0, t=30),
        title=dict(text=title, font=dict(color="#c5d3ef", size=12), x=0.04),
        paper_bgcolor='#0f1117',
        scene=dict(
            bgcolor='#0f1117',
            xaxis=dict(showbackground=False, gridcolor='#232b3e', color='#6b7a99', title='X'),
            yaxis=dict(showbackground=False, gridcolor='#232b3e', color='#6b7a99', title='Y'),
            zaxis=dict(showbackground=False, gridcolor='#232b3e', color='#6b7a99', title='Z'),
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

    # ── Matrice des piquets ──────────────────────────────────────────────
    st.markdown('<div class="section-header">Sélection du Pied — 5 Piquets × 5 Pieds</div>',
                unsafe_allow_html=True)

    for piquet in range(1, 6):
        pied_start = (piquet - 1) * N_PAR_PIQUET + 1
        pied_end   = piquet * N_PAR_PIQUET
        piquet_cols = st.columns([1.1] + [1] * N_PAR_PIQUET)

        with piquet_cols[0]:
            st.markdown(f'<div class="piquet-label">Piquet {piquet}</div>',
                        unsafe_allow_html=True)

        for i, pied_num in enumerate(range(pied_start, pied_end + 1)):
            with piquet_cols[i + 1]:
                p1_dates = avail[pied_num][1]
                p2_dates = avail[pied_num][2]
                if p1_dates and p2_dates:
                    icon = "🟢"
                elif p1_dates or p2_dates:
                    icon = "🟡"
                else:
                    icon = "⚫"
                is_sel = (pied_num == sel_pied)
                label  = f"{'▶' if is_sel else ''}{icon} P{pied_num}"
                if st.button(label, key=f"btn_{pied_num}", width="stretch",
                             type="primary" if is_sel else "secondary"):
                    st.session_state["sel_pied"] = pied_num
                    # Réinitialiser les dates lors du changement de pied
                    st.session_state["sel_date_p1"] = None
                    st.session_state["sel_date_p2"] = None
                    st.rerun()

    st.caption("🟢 P1 & P2 disponibles  |  🟡 Un seul passage  |  ⚫ Non filmé")
    st.markdown("---")

    # ── Info pied sélectionné ────────────────────────────────────────────
    piquet_num    = ((sel_pied - 1) // N_PAR_PIQUET) + 1
    pos_in_piquet = ((sel_pied - 1) % N_PAR_PIQUET) + 1
    p1_dates_list = avail[sel_pied][1]   # [(date, path), ...]
    p2_dates_list = avail[sel_pied][2]

    st.markdown(
        f'<div class="section-header">Pied n°{sel_pied} — '
        f'Piquet {piquet_num} · Position {pos_in_piquet}/{N_PAR_PIQUET}</div>',
        unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════════
    # DATE SWITCHER — une ligne par passage
    # ══════════════════════════════════════════════════════════════════
    sw_col1, sw_col2 = st.columns(2)

    # ── Switcher Passage 1 ───────────────────────────────────────────
    with sw_col1:
        st.markdown('<div class="pass-header pass1-header">🔵 Passage 1 — Aller</div>',
                    unsafe_allow_html=True)
        if p1_dates_list:
            date_options_p1 = [d for d, _ in p1_dates_list]
            # Valeur par défaut : la plus récente (index 0)
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
            # Retrouver le chemin correspondant
            ply_path_p1 = next(path for d, path in p1_dates_list if d == selected_date_p1)
            p1_available = True
        else:
            st.info("Aucun Passage 1 disponible pour ce pied.")
            p1_available = False
            ply_path_p1  = None

    # ── Switcher Passage 2 ───────────────────────────────────────────
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

    # ══════════════════════════════════════════════════════════════════
    # CHARGEMENT ET AFFICHAGE 3D CÔTE À CÔTE
    # ══════════════════════════════════════════════════════════════════
    pts1, col1_data, metrics1 = None, None, {}
    pts2, col2_data, metrics2 = None, None, {}

    viz_col1, viz_col2 = st.columns(2)

    with viz_col1:
        if p1_available:
            with st.spinner("Chargement passage 1…"):
                pts1, col1_data, status1 = load_ply(ply_path_p1)
            if status1 == "OK" and pts1 is not None:
                fig1 = make_3d_figure(
                    pts1, col1_data,
                    title=f"Pied {sel_pied:02d} · Passage 1 · {selected_date_p1}",
                )
                st.plotly_chart(fig1, width='stretch')
                metrics1 = pied_metrics(pts1)
                n1 = metrics1.get("n_points", 0)
                h1 = metrics1.get("hauteur", 0)
                c_a, c_b = st.columns(2)
                with c_a: st.metric("Points", f"{n1:,}".replace(",", " "))
                with c_b: st.metric("Hauteur", f"{h1} m")
            else:
                st.error(f"Erreur : {status1}")

    with viz_col2:
        if p2_available:
            with st.spinner("Chargement passage 2…"):
                pts2, col2_data, status2 = load_ply(ply_path_p2)
            if status2 == "OK" and pts2 is not None:
                fig2 = make_3d_figure(
                    pts2, col2_data,
                    title=f"Pied {sel_pied:02d} · Passage 2 · {selected_date_p2}",
                )
                st.plotly_chart(fig2, width='stretch')
                metrics2 = pied_metrics(pts2)
                n2 = metrics2.get("n_points", 0)
                h2 = metrics2.get("hauteur", 0)
                c_a, c_b = st.columns(2)
                with c_a: st.metric("Points", f"{n2:,}".replace(",", " "))
                with c_b: st.metric("Hauteur", f"{h2} m")
            else:
                st.error(f"Erreur : {status2}")

    # ══════════════════════════════════════════════════════════════════
    # TABLEAU COMPARATIF DES MÉTRIQUES P1 vs P2
    # ══════════════════════════════════════════════════════════════════
    if metrics1 or metrics2:
        st.markdown("---")
        st.markdown(
            f'<div class="section-header">Comparaison P1 / P2 — Pied {sel_pied}</div>',
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

        cmp_card(mc1, "Nb points",    "n_points")
        cmp_card(mc2, "Hauteur",      "hauteur",    " m")
        cmp_card(mc3, "Largeur",      "largeur",    " m")
        cmp_card(mc4, "Profondeur",   "profondeur", " m")
        cmp_card(mc5, "Z max",        "z_max",      " m")

    # ══════════════════════════════════════════════════════════════════
    # CARTE DE COUVERTURE DU RANG
    # ══════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown('<div class="section-header">Couverture du Rang — Toutes dates confondues</div>',
                unsafe_allow_html=True)

    cov_p1 = [1 if avail[p][1] else 0 for p in range(1, N_PIEDS + 1)]
    cov_p2 = [1 if avail[p][2] else 0 for p in range(1, N_PIEDS + 1)]
    pieds_x = list(range(1, N_PIEDS + 1))

    fig_cov = go.Figure()
    fig_cov.add_trace(go.Bar(
        name="Passage 1", x=pieds_x, y=cov_p1,
        marker_color=["#4e9af1" if v else "#1a2035" for v in cov_p1],
        marker_line_color=["#7eb8f7" if v else "#263050" for v in cov_p1],
        marker_line_width=1, width=0.38, offset=-0.19,
    ))
    fig_cov.add_trace(go.Bar(
        name="Passage 2", x=pieds_x, y=cov_p2,
        marker_color=["#a78bfa" if v else "#1a2035" for v in cov_p2],
        marker_line_color=["#c084fc" if v else "#263050" for v in cov_p2],
        marker_line_width=1, width=0.38, offset=0.19,
    ))
    # Séparateurs piquets
    for pk in range(1, 5):
        fig_cov.add_vline(x=pk * N_PAR_PIQUET + 0.5, line_dash="dot",
                          line_color="#3a4a6a", line_width=1)
        fig_cov.add_annotation(
            x=pk * N_PAR_PIQUET + 0.5 + N_PAR_PIQUET / 2 - N_PAR_PIQUET / 2,
            y=1.12, text=f"Piquet {pk+1}", showarrow=False,
            font=dict(color="#6b7a99", size=10), xref="x", yref="paper",
        )
    fig_cov.add_annotation(x=3, y=1.12, text="Piquet 1", showarrow=False,
                           font=dict(color="#6b7a99", size=10), xref="x", yref="paper")
    # Pied sélectionné
    fig_cov.add_vline(x=sel_pied, line_color="#fbbf24", line_width=2,
                      annotation_text=f"P{sel_pied}", annotation_font_color="#fbbf24",
                      annotation_position="top")
    fig_cov.update_layout(
        paper_bgcolor="#0f1117", plot_bgcolor="#0f1117",
        barmode="overlay", height=190,
        margin=dict(l=0, r=0, t=35, b=10),
        legend=dict(orientation="h", x=0.5, xanchor="center", y=1.18,
                    font=dict(color="#c5d3ef"), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(title="Numéro de pied", color="#6b7a99", gridcolor="#1a2035",
                   tickmode="linear", tick0=1, dtick=1),
        yaxis=dict(visible=False),
    )
    st.plotly_chart(fig_cov, width='stretch')


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — SUIVI MULTI-TEMPOREL
# ══════════════════════════════════════════════════════════════════════════

elif page == "📈 Suivi Multi-Temporel":
    st.markdown('<div class="viti-title">Suivi Multi-Temporel du Rang</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="viti-sub">Évolution de la croissance entre les campagnes d\'acquisition</div>',
                unsafe_allow_html=True)

    all_dates = sorted({d for p in avail.values()
                          for dl in p.values()
                          for d, _ in dl})

    ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 1])

    with ctrl1:
        if all_dates:
            date_choisie = st.select_slider(
                "📅 Campagne d'acquisition",
                options=all_dates,
                value=all_dates[-1],
            )
        else:
            st.warning("Aucune date détectée dans final_Output/")
            date_choisie = None

    with ctrl2:
        pass_choice = st.radio(
            "Passage à afficher",
            ["Passage 1", "Passage 2", "Les deux"],
            horizontal=True,
        )

    with ctrl3:
        show_simul = st.checkbox(
            "Mode simulation",
            value=(not bool(all_dates)),
            help="Activer si aucune donnée réelle n'est disponible",
        )

    passes_to_show = [1] if pass_choice == "Passage 1" \
                  else [2] if pass_choice == "Passage 2" \
                  else [1, 2]

    st.markdown("---")

    if show_simul or not date_choisie:
        # ── Mode simulation ───────────────────────────────────────────
        st.markdown('<span class="badge badge-sim">MODE SIMULATION</span>',
                    unsafe_allow_html=True)
        st.markdown(" ")

        month_labels = ["Déc. 2024", "Jan. 2025", "Fév. 2025", "Mars 2025",
                        "Avr. 2025", "Mai 2025", "Juin 2025", "Juil. 2025"]
        mois_idx = month_labels.index(
            st.select_slider("Mois simulé", options=month_labels, value="Juil. 2025")
        )

        @st.cache_data(show_spinner=False)
        def simul_rang(mi):
            cr = 1.0 + mi * 0.25
            pts, cols = [], []
            for c in range(N_PIEDS):
                xo = c * 1.2
                nt = 400
                nf = int(800 * cr)
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
            pts_sim, cols_sim = simul_rang(mois_idx)
            color_sim = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})'
                         for r, g, b in cols_sim]
            fig_s = go.Figure(data=[go.Scatter3d(
                x=pts_sim[:, 0], y=pts_sim[:, 1], z=pts_sim[:, 2],
                mode='markers',
                marker=dict(size=1.2, color=color_sim, opacity=0.85),
            )])
            fig_s.update_layout(
                paper_bgcolor="#0f1117",
                scene=dict(
                    bgcolor="#0f1117",
                    xaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Rang (X)"),
                    yaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Profondeur (Y)"),
                    zaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Hauteur (Z)"),
                    aspectmode="data",
                    camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1)),
                ),
                margin=dict(l=0, r=0, b=0, t=20), height=500,
            )
            st.plotly_chart(fig_s, width='stretch')

        ms1, ms2, ms3, ms4 = st.columns(4)
        with ms1: st.metric("Volume feuillage", f"{1.2 + mois_idx * 0.6:.2f} m³",
                            delta="+0.6 m³/mois")
        with ms2: st.metric("Pieds simulés", N_PIEDS)
        with ms3: st.metric("Hauteur moy. estimée", f"{0.5 + mois_idx * 0.03:.2f} m")
        with ms4: st.metric("Piquets", N_PIEDS // N_PAR_PIQUET)

    else:
        # ── Mode données réelles ──────────────────────────────────────
        st.markdown(f'<span class="badge badge-ok">DONNÉES RÉELLES — {date_choisie}</span>',
                    unsafe_allow_html=True)
        st.markdown(" ")

        all_pts, all_cols, loaded_pieds = [], [], []
        prog = st.progress(0, text="Chargement des nuages…")

        for i, pied_num in enumerate(range(1, N_PIEDS + 1)):
            prog.progress(i / N_PIEDS, text=f"Pied {pied_num}/{N_PIEDS}…")
            x_offset = (pied_num - 1) * 1.2

            for pas in passes_to_show:
                dates_list = avail[pied_num][pas]
                # Chercher la date sélectionnée dans les disponibles pour ce pied/passage
                match = [(d, p) for d, p in dates_list if d == date_choisie]
                if not match:
                    continue
                _, ply_path = match[0]
                pts, cols, status = load_ply(ply_path)
                if status == "OK" and pts is not None:
                    shifted = pts.copy()
                    shifted[:, 0] += x_offset
                    shifted[:, 1] += (0.8 if pas == 2 else 0.0)
                    all_pts.append(shifted)
                    if cols is not None:
                        tinted = cols.copy()
                        if pas == 2:
                            tinted[:, 2] = np.clip(tinted[:, 2] + 0.15, 0, 1)
                        all_cols.append(tinted)
                    else:
                        all_cols.append(np.tile([0.4, 0.7, 0.4], (len(pts), 1)))
                    loaded_pieds.append((pied_num, pas))

        prog.empty()

        if all_pts:
            pts_all  = np.vstack(all_pts)
            cols_all = np.vstack(all_cols)
            color_all = [f'rgb({int(r*255)},{int(g*255)},{int(b*255)})'
                         for r, g, b in cols_all]

            fig_r = go.Figure(data=[go.Scatter3d(
                x=pts_all[:, 0], y=pts_all[:, 1], z=pts_all[:, 2],
                mode='markers',
                marker=dict(size=1.2, color=color_all, opacity=0.85),
                hovertemplate='x:%{x:.3f} y:%{y:.3f} z:%{z:.3f}<extra></extra>',
            )])
            fig_r.update_layout(
                paper_bgcolor="#0f1117",
                title=dict(
                    text=f"Rang complet — {date_choisie} — {len(loaded_pieds)} nuages",
                    font=dict(color="#c5d3ef", size=13), x=0.03,
                ),
                scene=dict(
                    bgcolor="#0f1117",
                    xaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Rang (X)"),
                    yaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Passage (Y)"),
                    zaxis=dict(showbackground=False, gridcolor="#232b3e",
                               color="#6b7a99", title="Hauteur (Z)"),
                    aspectmode="data",
                    camera=dict(eye=dict(x=0, y=-2.5, z=0.7), up=dict(x=0, y=0, z=1)),
                ),
                margin=dict(l=0, r=0, b=0, t=40), height=520,
            )
            st.plotly_chart(fig_r, width='stretch')

            mr1, mr2, mr3, mr4 = st.columns(4)
            with mr1: st.metric("Points total", f"{len(pts_all):,}".replace(",", " "))
            with mr2: st.metric("Pieds chargés", len(set(p for p, _ in loaded_pieds)))
            with mr3:
                h_rang = round(float(pts_all[:, 2].max() - pts_all[:, 2].min()), 3)
                st.metric("Hauteur rang", f"{h_rang} m")
            with mr4: st.metric("Passages", f"{len(set(ps for _, ps in loaded_pieds))}/2")

            # ── Tableau de disponibilité par date ──────────────────────
            st.markdown("---")
            st.markdown(
                f'<div class="section-header">Disponibilité par pied — {date_choisie}</div>',
                unsafe_allow_html=True)

            loaded_set = set(loaded_pieds)
            rows_html = ""
            for p in range(1, N_PIEDS + 1):
                has_p1 = any(d == date_choisie for d, _ in avail[p][1])
                has_p2 = any(d == date_choisie for d, _ in avail[p][2])
                ok1 = "✅" if (p, 1) in loaded_set else ("⚪" if has_p1 else "❌")
                ok2 = "✅" if (p, 2) in loaded_set else ("⚪" if has_p2 else "❌")
                pk  = ((p - 1) // N_PAR_PIQUET) + 1
                bg  = "#1a2035" if p % 2 == 0 else "#131929"
                hi  = "border-left:3px solid #fbbf24;" if p == st.session_state.get("sel_pied", 1) else ""
                rows_html += (
                    f'<tr style="background:{bg};{hi}">'
                    f'<td style="padding:4px 8px;color:#c5d3ef;font-family:monospace;">Pied {p:02d}</td>'
                    f'<td style="text-align:center;padding:4px;">{ok1}</td>'
                    f'<td style="text-align:center;padding:4px;">{ok2}</td>'
                    f'<td style="padding:4px 8px;color:#6b7a99;">Piquet {pk}</td>'
                    f'</tr>'
                )
            st.markdown(
                f'<table style="width:100%;font-size:0.75rem;border-collapse:collapse;">'
                f'<tr>'
                f'<th style="color:#6b7a99;text-align:left;padding:4px 8px;">Pied</th>'
                f'<th style="color:#4e9af1;text-align:center;padding:4px 8px;">Passage 1</th>'
                f'<th style="color:#a78bfa;text-align:center;padding:4px 8px;">Passage 2</th>'
                f'<th style="color:#6b7a99;text-align:left;padding:4px 8px;">Piquet</th>'
                f'</tr>{rows_html}</table>',
                unsafe_allow_html=True,
            )
            st.caption("✅ Chargé  |  ⚪ Disponible/autre date  |  ❌ Non filmé")

        else:
            st.warning(
                f"Aucun nuage trouvé pour **{date_choisie}** avec les passes sélectionnées.\n\n"
                "Vérifiez que le pipeline a été exécuté pour cette date, "
                "ou activez le mode simulation."
            )