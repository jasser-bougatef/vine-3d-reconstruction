"""
syn_code_v3.py
==============
Synchronise automatiquement TOUTES les paires (pos + csv) dont la date correspond.
Utilise le shapefile des pieds de vigne pour détecter les arrêts du robot
et supprimer les frames redondantes capturées sur place (évite les parasites 3D).

Usage
-----
    python syn_code_v3.py                        # détection & traitement automatique
    python syn_code_v3.py --force                # retraite même les sessions existantes
    python syn_code_v3.py --max-gap 1.5 --quality 1 2
"""

import json
import argparse
import re
import sys
from datetime import date, datetime, timezone
from math import cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from pyproj import Transformer
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

# ══════════════════════════════════════════════════════════════════════════════
#  Constantes WGS84
# ══════════════════════════════════════════════════════════════════════════════

WGS84_A  = 6_378_137.0
WGS84_B  = 6_356_752.314245
WGS84_E2 = 1 - (WGS84_B / WGS84_A) ** 2

# ══════════════════════════════════════════════════════════════════════════════
#  Bras de levier GPS → Caméra (en mètres, repère robot)
# ══════════════════════════════════════════════════════════════════════════════

LEVER_ARM_X = -0.24   # décalage latéral
LEVER_ARM_Y = 1.5  # décalage longitudinal
LEVER_ARM_Z =  0.0    # décalage vertical

# Transformers L93 ↔ WGS84 (instanciés une seule fois)
_TRANSFORMER_TO_L93   = Transformer.from_crs("epsg:4326", "epsg:2154", always_xy=True)
_TRANSFORMER_TO_WGS84 = Transformer.from_crs("epsg:2154", "epsg:4326", always_xy=True)


# ══════════════════════════════════════════════════════════════════════════════
#  Géodésie : WGS84 → ENU
# ══════════════════════════════════════════════════════════════════════════════

def _geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float):
    lat = radians(lat_deg)
    lon = radians(lon_deg)
    N = WGS84_A / sqrt(1 - WGS84_E2 * sin(lat) ** 2)
    X = (N + alt_m) * cos(lat) * cos(lon)
    Y = (N + alt_m) * cos(lat) * sin(lon)
    Z = (N * (1 - WGS84_E2) + alt_m) * sin(lat)
    return X, Y, Z

def _ecef_to_enu(X, Y, Z, lat0_deg, lon0_deg, alt0_m):
    X0, Y0, Z0 = _geodetic_to_ecef(lat0_deg, lon0_deg, alt0_m)
    dX, dY, dZ = X - X0, Y - Y0, Z - Z0
    lat0, lon0 = radians(lat0_deg), radians(lon0_deg)
    E = -sin(lon0) * dX + cos(lon0) * dY
    N = (-sin(lat0) * cos(lon0) * dX
         - sin(lat0) * sin(lon0) * dY
         + cos(lat0) * dZ)
    U = (cos(lat0) * cos(lon0) * dX
         + cos(lat0) * sin(lon0) * dY
         + sin(lat0) * dZ)
    return E, N, U

def latlon_array_to_enu(lat_arr, lon_arr, alt_arr, lat0, lon0, alt0):
    X, Y, Z = np.vectorize(_geodetic_to_ecef)(lat_arr, lon_arr, alt_arr)
    return _ecef_to_enu(X, Y, Z, lat0, lon0, alt0)


# ══════════════════════════════════════════════════════════════════════════════
#  Extraction de date depuis les noms de fichiers / dossiers
# ══════════════════════════════════════════════════════════════════════════════

def date_from_pos(pos_path: Path) -> date | None:
    m = re.search(r"_(\d{8})_", pos_path.stem)
    if not m: return None
    try: return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError: return None

def date_from_data_dir(dir_path: Path) -> date | None:
    m = re.search(r"data_(\d{2}-\d{2}-\d{4})$", dir_path.name)
    if not m: return None
    try: return datetime.strptime(m.group(1), "%d-%m-%Y").date()
    except ValueError: return None

def date_label(d: date) -> str:
    return d.strftime("%d-%m-%Y")


# ══════════════════════════════════════════════════════════════════════════════
#  Scan et appariement des sessions
# ══════════════════════════════════════════════════════════════════════════════

def find_sessions(pos_dir: Path, data_root: Path, output_dir: Path, force: bool = False) -> list[dict]:
    pos_by_date: dict[date, list[Path]] = {}
    if pos_dir.exists():
        for p in sorted(pos_dir.glob("*.pos")):
            d = date_from_pos(p)
            if d: pos_by_date.setdefault(d, []).append(p)
    else:
        print(f"[ERREUR] Dossier PPK_pos introuvable : {pos_dir}")
        sys.exit(1)

    data_by_date: dict[date, Path] = {}
    if data_root.exists():
        for d in sorted(data_root.iterdir()):
            if not d.is_dir() or not d.name.startswith("data_"): continue
            dt = date_from_data_dir(d)
            if dt and (d / "frame_timestamps.csv").exists():
                data_by_date[dt] = d
    else:
        print(f"[ERREUR] Dossier Extraction_Output introuvable : {data_root}")
        sys.exit(1)

    all_dates = sorted(set(pos_by_date) | set(data_by_date))
    sessions  = []

    print(f"\n{'─'*65}")
    print(f"  Dates .pos    trouvées : {sorted(pos_by_date)}")
    print(f"  Dates data_* trouvées : {sorted(data_by_date)}")
    print(f"{'─'*65}")

    for dt in all_dates:
        label = date_label(dt)
        if dt not in pos_by_date or dt not in data_by_date: continue

        pos_path    = pos_by_date[dt][-1]
        frames_path = data_by_date[dt] / "frame_timestamps.csv"
        out_csv     = output_dir / f"synchronized_{label}.csv"
        out_xlsx    = output_dir / f"synchronized_{label}.xlsx"
        already_done = out_csv.exists() and not force

        status = "✓ déjà traité (ignoré)" if already_done else "→ à traiter"
        print(f"  [{label}]  {status}")
        
        sessions.append({
            "date": dt, "label": label, "pos_path": pos_path,
            "frames_path": frames_path, "out_csv": out_csv,
            "out_xlsx": out_xlsx, "already_done": already_done,
        })
    return sessions


# ══════════════════════════════════════════════════════════════════════════════
#  Lecture des fichiers
# ══════════════════════════════════════════════════════════════════════════════

def _parse_ref_pos(path: Path) -> tuple[float, float, float] | None:
    pattern = re.compile(r"%\s*ref pos\s*:\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)", re.IGNORECASE)
    with open(path, "r", errors="replace") as f:
        for line in f:
            if not line.startswith("%"): break
            m = pattern.match(line)
            if m: return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None

def load_pos(path: Path) -> tuple[pd.DataFrame, tuple[float, float, float] | None]:
    raw_cols = ["date_", "time_", "lat_deg", "lon_deg", "height_m", "Q", "ns",
                "sdn_m", "sde_m", "sdu_m", "sdne_m", "sdeu_m", "sdun_m", "age_s", "ratio"]
    df = pd.read_csv(path, comment="%", sep=r"\s+", header=None, names=raw_cols)
    df["datetime_str"] = df["date_"].str.strip() + " " + df["time_"].str.strip()
    df["gps_time_utc"] = pd.to_datetime(df["datetime_str"], format="%Y/%m/%d %H:%M:%S.%f", utc=True)
    df["gps_ts"] = df["gps_time_utc"].apply(lambda x: x.timestamp())
    df.drop(columns=["date_", "time_"], inplace=True)
    df.sort_values("gps_ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    
    ref = _parse_ref_pos(path)
    print(f"  [GPS]    {len(df)} points")
    return df, ref

def load_frames(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if "frame" in cl and ("num" in cl or "no" in cl or cl == "frame"): col_map[c] = "frame_number"
        elif "time" in cl or "ts" in cl or "stamp" in cl: col_map[c] = "timestamp_ms"
    df.rename(columns=col_map, inplace=True)
    
    df["frame_ts"] = df["timestamp_ms"] / 1000.0
    df.sort_values("frame_ts", inplace=True)
    df.reset_index(drop=True, inplace=True)
    print(f"  [Frames] {len(df)} frames au départ")
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  Filtrage des frames redondantes (Arrêts près des pieds)
# ══════════════════════════════════════════════════════════════════════════════

def filter_redundant_frames_at_stops(gps_df: pd.DataFrame, frames_df: pd.DataFrame, shp_path: Path) -> pd.DataFrame:
    """
    Détecte les moments où le robot est à l'arrêt à proximité d'un pied de vigne.
    Conserve une seule frame par arrêt et supprime les autres pour éviter la redondance.
    """
    # ═════════════════════════════════════════════════════════════════════
    # ⚙️ PARAMÈTRES DE RÉGLAGE DU FILTRE (À modifier selon tes tests)
    # ═════════════════════════════════════════════════════════════════════
    TAILLE_FENETRE     = 5    # Baisse à 10 ou 5 si tes pauses sont très courtes (ex: 1 à 2 secondes).
    SEUIL_IMMOBILITE_M = 0.20  # 0.15 = 15 cm. Augmente à 0.20 ou 0.30 si le GPS bouge beaucoup à l'arrêt.
    DISTANCE_MAX_PIED  = 3.5   # Tolérance en mètres par rapport au point du Shapefile.
    # ═════════════════════════════════════════════════════════════════════

    print(f"  [Filtrage] Chargement du Shapefile : {shp_path.name}")
    try:
        vines_gdf = gpd.read_file(shp_path)
        if vines_gdf.crs and vines_gdf.crs.to_epsg() != 2154:
            vines_gdf = vines_gdf.to_crs(epsg=2154)
        vine_coords_l93 = np.array([(geom.x, geom.y) for geom in vines_gdf.geometry])
        tree_vines = cKDTree(vine_coords_l93)
    except Exception as e:
        print(f"  [!] Erreur lors du chargement du Shapefile : {e}. Aucun filtrage appliqué.")
        return frames_df

    # 1. Convertir les coordonnées GPS en Lambert93 pour analyse métrique
    gps_x, gps_y = _TRANSFORMER_TO_L93.transform(gps_df["lon_deg"].values, gps_df["lat_deg"].values)
    
    # 2. Détecter les arrêts (écart-type faible sur la fenêtre glissante)
    std_x = pd.Series(gps_x).rolling(window=TAILLE_FENETRE, center=True).std()
    std_y = pd.Series(gps_y).rolling(window=TAILLE_FENETRE, center=True).std()
    
    is_stopped = (std_x < SEUIL_IMMOBILITE_M) & (std_y < SEUIL_IMMOBILITE_M)
    is_stopped = is_stopped.fillna(False)
    
    # Grouper les points consécutifs où le robot est arrêté
    stop_id = (is_stopped != is_stopped.shift()).cumsum()
    
    gps_df_temp = gps_df.copy()
    gps_df_temp["is_stopped"] = is_stopped
    gps_df_temp["stop_id"] = stop_id
    gps_df_temp["x_l93"] = gps_x
    gps_df_temp["y_l93"] = gps_y
    
    stop_groups = gps_df_temp[gps_df_temp["is_stopped"]].groupby("stop_id")
    
    frames_to_drop = []
    stops_filtered = 0

    # 3. Analyser chaque arrêt
    for sid, group in stop_groups:
        if len(group) < TAILLE_FENETRE: 
            continue # Ignorer les micro-pauses
        
        # Centroïde de l'arrêt
        mean_x, mean_y = group["x_l93"].mean(), group["y_l93"].mean()
        
        # Trouver le pied de vigne le plus proche
        dist, idx = tree_vines.query((mean_x, mean_y))
        
        # Si on est près d'un pied
        if dist < DISTANCE_MAX_PIED:
            t_start = group["gps_ts"].iloc[0]
            t_end   = group["gps_ts"].iloc[-1]
            
            # Trouver toutes les frames capturées pendant cet arrêt
            mask = (frames_df["frame_ts"] >= t_start) & (frames_df["frame_ts"] <= t_end)
            frames_in_stop = frames_df[mask]
            
            if len(frames_in_stop) > 1:
                # On conserve uniquement la frame du milieu
                keep_idx = frames_in_stop.index[len(frames_in_stop) // 2]
                drop_indices = frames_in_stop.index.difference([keep_idx])
                
                frames_to_drop.extend(drop_indices)
                stops_filtered += 1

    # 4. Appliquer la suppression
    if frames_to_drop:
        frames_df_filtered = frames_df.drop(index=frames_to_drop).reset_index(drop=True)
        print(f"  [Filtrage] {stops_filtered} arrêts près de pieds détectés.")
        print(f"  [Filtrage] {len(frames_to_drop)} frames redondantes supprimées.")
        print(f"  [Frames] {len(frames_df_filtered)} frames conservées pour l'interpolation.")
        return frames_df_filtered
    else:
        print("  [Filtrage] Aucune frame redondante détectée. Les paramètres sont peut-être trop stricts.")
        return frames_df


# ══════════════════════════════════════════════════════════════════════════════
#  Interpolation et Bras de Levier
# ══════════════════════════════════════════════════════════════════════════════

def _apply_lever_arm_correction(lat_interp, lon_interp, height_interp):
    n = len(lat_interp)
    gps_l93_e, gps_l93_n, gps_l93_u = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    cam_l93_e, cam_l93_n, cam_l93_u = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)

    valid = ~np.isnan(lat_interp)
    if not valid.any(): return gps_l93_e, gps_l93_n, gps_l93_u, cam_l93_e, cam_l93_n, cam_l93_u

    x, y = _TRANSFORMER_TO_L93.transform(lon_interp[valid], lat_interp[valid])
    gps_l93_e[valid], gps_l93_n[valid], gps_l93_u[valid] = x, y, height_interp[valid]

    dE = np.diff(gps_l93_e[valid], prepend=gps_l93_e[valid][0])
    dN = np.diff(gps_l93_n[valid], prepend=gps_l93_n[valid][0])
    vx, vy = gaussian_filter1d(dE, sigma=2), gaussian_filter1d(dN, sigma=2)
    dist_lissee = np.sqrt(vx**2 + vy**2)
    raw_theta = np.arctan2(vy, vx)

    theta_valid = np.where(dist_lissee > 0.1, raw_theta, np.nan)
    theta = pd.Series(theta_valid).ffill().bfill().fillna(0.0).values

    cos_t, sin_t = np.cos(theta), np.sin(theta)
    cam_l93_e[valid] = gps_l93_e[valid] + (LEVER_ARM_X * cos_t - LEVER_ARM_Y * sin_t)
    cam_l93_n[valid] = gps_l93_n[valid] + (LEVER_ARM_X * sin_t + LEVER_ARM_Y * cos_t)
    cam_l93_u[valid] = gps_l93_u[valid] + LEVER_ARM_Z

    return gps_l93_e, gps_l93_n, gps_l93_u, cam_l93_e, cam_l93_n, cam_l93_u

def interpolate_positions(gps: pd.DataFrame, frames: pd.DataFrame, max_gap_s: float = 2.0, quality_filter: list = None, enu_ref: tuple = None) -> pd.DataFrame:
    if quality_filter:
        gps = gps[gps["Q"].isin(quality_filter)].copy()
        if gps.empty: raise ValueError("Aucun point GPS ne satisfait le filtre.")

    gps_ts = gps["gps_ts"].values
    frame_ts = frames["frame_ts"].values
    
    lat_interp = np.interp(frame_ts, gps_ts, gps["lat_deg"].values, left=np.nan, right=np.nan)
    lon_interp = np.interp(frame_ts, gps_ts, gps["lon_deg"].values, left=np.nan, right=np.nan)
    height_interp = np.interp(frame_ts, gps_ts, gps["height_m"].values, left=np.nan, right=np.nan)

    out_of_range = (frame_ts < gps_ts[0]) | (frame_ts > gps_ts[-1])
    lat_interp[out_of_range] = np.nan
    lon_interp[out_of_range] = np.nan
    height_interp[out_of_range] = np.nan

    idx_right = np.clip(np.searchsorted(gps_ts, frame_ts, side="left"), 0, len(gps_ts) - 1)
    idx_left  = np.clip(idx_right - 1, 0, len(gps_ts) - 1)
    gap_left, gap_right = np.abs(frame_ts - gps_ts[idx_left]), np.abs(frame_ts - gps_ts[idx_right])
    min_gap = np.minimum(gap_left, gap_right)
    
    reliable = ~out_of_range & (min_gap <= max_gap_s)

    out = frames[["frame_number", "timestamp_ms", "frame_ts"]].copy()
    out["photo_time_utc"] = pd.to_datetime(frames["frame_ts"], unit="s", utc=True).dt.strftime("%Y-%m-%d %H:%M:%S.%f")
    out["lat_gmap"], out["lon_gmap"] = np.round(lat_interp, 8), np.round(lon_interp, 8)
    
    gps_l93_e, gps_l93_n, gps_l93_u, cam_l93_e, cam_l93_n, cam_l93_u = _apply_lever_arm_correction(lat_interp, lon_interp, height_interp)
    
    out["lat_rtk"], out["lon_rtk"], out["alt_rtk"] = np.round(lat_interp, 8), np.round(lon_interp, 8), np.round(height_interp, 4)
    out["gps_L93_e_m"], out["gps_L93_n_m"], out["gps_L93_u_m"] = np.round(gps_l93_e, 3), np.round(gps_l93_n, 3), np.round(gps_l93_u, 4)
    out["cam_L93_e_m"], out["cam_L93_n_m"], out["cam_L93_u_m"] = np.round(cam_l93_e, 3), np.round(cam_l93_n, 3), np.round(cam_l93_u, 4)

    valid_cam = ~np.isnan(cam_l93_e)
    if valid_cam.any():
        lon_cam, lat_cam = _TRANSFORMER_TO_WGS84.transform(cam_l93_e[valid_cam], cam_l93_n[valid_cam])
        out.loc[valid_cam, "lat_rtk"] = np.round(lat_cam, 8)
        out.loc[valid_cam, "lon_rtk"] = np.round(lon_cam, 8)
        out.loc[valid_cam, "alt_rtk"] = np.round(cam_l93_u[valid_cam], 4)

    if enu_ref:
        valid = ~np.isnan(lat_interp)
        E, N, U = np.full(len(out), np.nan), np.full(len(out), np.nan), np.full(len(out), np.nan)
        if valid.any(): E[valid], N[valid], U[valid] = latlon_array_to_enu(lat_interp[valid], lon_interp[valid], height_interp[valid], *enu_ref)
        out["enu_e_m"], out["enu_n_m"], out["enu_u_m"] = np.round(E, 4), np.round(N, 4), np.round(U, 4)
    else:
        out["enu_e_m"] = out["enu_n_m"] = out["enu_u_m"] = np.nan

    out["gps_Q"] = gps["Q"].values[np.where(gap_left <= gap_right, idx_left, idx_right)]
    out["time_gap_s"], out["in_gps_range"], out["reliable"] = np.round(min_gap, 4), ~out_of_range, reliable

    print(f"  [Sync]   {reliable.sum()}/{len(out)} frames fiables")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Traitement complet et Sauvegarde
# ══════════════════════════════════════════════════════════════════════════════

COLS_EXPORT = ["frame_number", "photo_time_utc", "timestamp_ms", "lat_gmap", "lon_gmap", "lat_rtk", "lon_rtk", "alt_rtk",
               "enu_e_m", "enu_n_m", "enu_u_m", "gps_L93_e_m", "gps_L93_n_m", "gps_L93_u_m", "cam_L93_e_m", "cam_L93_n_m", "cam_L93_u_m",
               "gps_Q", "time_gap_s", "in_gps_range", "reliable"]

def process_session(session: dict, max_gap_s: float, quality_filter, enu_ref_override, shp_path: Path) -> bool:
    print(f"\n{'═'*65}\n  SESSION  {session['label']}\n{'═'*65}")
    try:
        gps, ref_from_file = load_pos(session["pos_path"])
        frames = load_frames(session["frames_path"])

        # NOUVEAU : Filtrage des frames redondantes à l'arrêt
        #_____________________________________________________________________________________________________________________________________

        if shp_path and shp_path.exists():
            frames = filter_redundant_frames_at_stops(gps, frames, shp_path)
        else:
            print(f"  [!] Shapefile introuvable : {shp_path}. Aucun filtrage appliqué.")
        #__________________________________________________________________________________commenter ca si vous vouler pas avoir un suppression des immages
    
        enu_ref = enu_ref_override or ref_from_file or (float(gps["lat_deg"].iloc[0]), float(gps["lon_deg"].iloc[0]), float(gps["height_m"].iloc[0]))
        result = interpolate_positions(gps, frames, max_gap_s, quality_filter, enu_ref)

        session["out_csv"].parent.mkdir(parents=True, exist_ok=True)
        result[COLS_EXPORT].to_csv(session["out_csv"], index=False)
        
        try:
            with pd.ExcelWriter(session["out_xlsx"], engine="openpyxl") as writer:
                result[COLS_EXPORT].to_excel(writer, index=False, sheet_name="Synchronized")
        except ImportError: pass

        return True
    except Exception as exc:
        print(f"\n  [ERREUR] Session {session['label']} : {exc}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", type=Path, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--max-gap", type=float, default=2.0)
    parser.add_argument("--quality", type=int, nargs="+", default=None)
    parser.add_argument("--enu-ref", type=str, default=None)
    args = parser.parse_args()

    config_file = Path(__file__).resolve().parent / "pipeline_config.json"
    if not config_file.exists(): sys.exit(1)
        
    with open(config_file, "r", encoding="utf-8") as f: config = json.load(f)

    project_root = args.base_dir or Path(config["project_root"])
    pos_dir      = project_root / config["paths"]["ppk_pos_dir"]
    data_root    = project_root / config["paths"]["timesample_files"]
    output_dir   = project_root / config["paths"]["syn_output_dir"]
    shp_path     = project_root / config["paths"]["shapefile"]
    
    enu_ref_override = tuple(float(x) for x in args.enu_ref.replace(" ", "").split(",")) if args.enu_ref else None

    sessions = find_sessions(pos_dir, data_root, output_dir, args.force)
    to_process = [s for s in sessions if not s["already_done"]]

    if not to_process: sys.exit(0)

    results = [(s["label"], process_session(s, args.max_gap, args.quality, enu_ref_override, shp_path)) for s in to_process]
    
    print(f"\n{'═'*65}\n  RAPPORT FINAL\n{'═'*65}")
    for label, ok in results: print(f"  [{label}]  {'✓ OK' if ok else '✗ ERREUR'}")
    if any(not ok for _, ok in results): sys.exit(1)

if __name__ == "__main__":
    main()