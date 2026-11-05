"""
syn_code_v3.py
==============
Synchronise automatiquement TOUTES les paires (pos + csv) dont la date correspond,
en évitant de retraiter les sessions déjà traitées.

Structure attendue
------------------
    <base_dir>/
    ├── Extraction_Output/
    │   ├── PPK_pos/
    │   │   ├── trk_20250704_1050.pos     ← format  trk_YYYYMMDD_HHMM.pos
    │   │   └── trk_20250717_0928.pos
    │   ├── data_04-07-2025/              ← format  data_DD-MM-YYYY
    │   │   └── frame_timestamps.csv
    │   └── data_17-07-2025/
    │       └── frame_timestamps.csv
    └── final_Output/
        └── Timesample_synchronised_files/
            ├── synchronized_04-07-2025.csv   ← sorties nommées par date
            ├── synchronized_04-07-2025.xlsx
            ├── synchronized_17-07-2025.csv
            └── synchronized_17-07-2025.xlsx

Logique de matching par date
-----------------------------
  1. Scan de PPK_pos/ : extraction de la date depuis trk_YYYYMMDD_HHMM.pos
  2. Scan de data_*/  : extraction de la date depuis data_DD-MM-YYYY
  3. Appariement : même date calendaire  →  une paire à traiter
  4. Vérification : si synchronized_DD-MM-YYYY.csv existe déjà → session ignorée
  5. Traitement de chaque nouvelle paire + rapport final

Stratégie d'interpolation
--------------------------
  Vitesse constante entre deux points GPS consécutifs :
      α = (t - t0) / (t1 - t0)
      pos = pos0 + α × (pos1 - pos0)

Colonnes de sortie
------------------
  frame_number | photo_time_utc | timestamp_ms
  lat_gmap | lon_gmap
  lat_rtk  | lon_rtk | alt_rtk
  enu_e_m  | enu_n_m | enu_u_m
  gps_Q | time_gap_s | in_gps_range | reliable

Usage
-----
    python syn_code_v3.py                        # détection & traitement automatique
    python syn_code_v3.py --base-dir /mon/projet
    python syn_code_v3.py --force                # retraite même les sessions existantes
    python syn_code_v3.py --max-gap 1.5 --quality 1 2
"""

import argparse
import re
import sys
from datetime import date, datetime, timezone
from math import cos, radians, sin, sqrt
from pathlib import Path

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════════════════════
#  Constantes WGS84
# ══════════════════════════════════════════════════════════════════════════════

WGS84_A  = 6_378_137.0
WGS84_B  = 6_356_752.314245
WGS84_E2 = 1 - (WGS84_B / WGS84_A) ** 2


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
    """
    Extrait la date depuis un nom de fichier .pos de type :
        trk_YYYYMMDD_HHMM.pos
    Retourne un objet date ou None si le format ne correspond pas.
    """
    m = re.search(r"_(\d{8})_", pos_path.stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def date_from_data_dir(dir_path: Path) -> date | None:
    """
    Extrait la date depuis un nom de dossier de type :
        data_DD-MM-YYYY
    Retourne un objet date ou None si le format ne correspond pas.
    """
    m = re.search(r"data_(\d{2}-\d{2}-\d{4})$", dir_path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d-%m-%Y").date()
    except ValueError:
        return None


def date_label(d: date) -> str:
    """Formate la date en DD-MM-YYYY (utilisé pour nommer les fichiers de sortie)."""
    return d.strftime("%d-%m-%Y")


# ══════════════════════════════════════════════════════════════════════════════
#  Scan et appariement des sessions
# ══════════════════════════════════════════════════════════════════════════════

def find_sessions(base_dir: Path, output_dir: Path, force: bool = False) -> list[dict]:
    """
    Scanne Extraction_Output/PPK_pos/ et Extraction_Output/data_*/ pour
    constituer des paires (pos_file, csv_file) ayant la même date.

    Pour chaque paire détectée :
      - si le fichier de sortie existe déjà ET force=False → session ignorée (déjà traitée)
      - sinon → session ajoutée à la liste de retour

    Retourne une liste de dicts :
        {
            "date"       : date object,
            "label"      : "DD-MM-YYYY",
            "pos_path"   : Path,
            "frames_path": Path,
            "out_csv"    : Path,
            "out_xlsx"   : Path,
            "already_done": bool,
        }
    """
    pos_dir   = base_dir / "Extraction_Output" / "PPK_pos"
    data_root = base_dir / "Extraction_Output" / "Timesample_files"                       

    # ── Indexer les .pos par date ─────────────────────────────────────────────
    pos_by_date: dict[date, list[Path]] = {}
    if pos_dir.exists():
        for p in sorted(pos_dir.glob("*.pos")):
            d = date_from_pos(p)
            if d:
                pos_by_date.setdefault(d, []).append(p)
            else:
                print(f"  [?] .pos ignoré (format inconnu) : {p.name}")
    else:
        print(f"[ERREUR] Dossier PPK_pos introuvable : {pos_dir}")
        sys.exit(1)

    # ── Indexer les dossiers data_* par date ─────────────────────────────────
    data_by_date: dict[date, Path] = {}
    if data_root.exists():
        for d in sorted(data_root.iterdir()):
            if not d.is_dir() or not d.name.startswith("data_"):
                continue
            dt = date_from_data_dir(d)
            if dt:
                csv_candidate = d / "frame_timestamps.csv"
                if csv_candidate.exists():
                    data_by_date[dt] = d
                else:
                    print(f"  [!] Dossier {d.name} trouvé mais frame_timestamps.csv absent → ignoré")
            else:
                print(f"  [?] Dossier ignoré (format de date inconnu) : {d.name}")
    else:
        print(f"[ERREUR] Dossier Extraction_Output introuvable : {data_root}")
        sys.exit(1)

    # ── Appariement par date ──────────────────────────────────────────────────
    all_dates = sorted(set(pos_by_date) | set(data_by_date))
    sessions  = []

    print(f"\n{'─'*65}")
    print(f"  Dates .pos    trouvées : {sorted(pos_by_date)}")
    print(f"  Dates data_*  trouvées : {sorted(data_by_date)}")
    print(f"{'─'*65}")

    for dt in all_dates:
        label = date_label(dt)

        has_pos  = dt in pos_by_date
        has_data = dt in data_by_date

        if not has_pos:
            print(f"  [{label}]  ✗  Aucun .pos pour cette date → session ignorée")
            continue
        if not has_data:
            print(f"  [{label}]  ✗  Aucun dossier data_ pour cette date → session ignorée")
            continue

        # Prend le .pos le plus récent si plusieurs fichiers ce jour-là
        pos_path    = pos_by_date[dt][-1]
        frames_path = data_by_date[dt] / "frame_timestamps.csv"
        out_csv     = output_dir / f"synchronized_{label}.csv"
        out_xlsx    = output_dir / f"synchronized_{label}.xlsx"

        already_done = out_csv.exists() and not force

        status = "✓ déjà traité (ignoré)" if already_done else "→ à traiter"
        print(f"  [{label}]  {status}")
        if not already_done:
            print(f"             .pos    : {pos_path.relative_to(base_dir)}")
            print(f"             frames  : {frames_path.relative_to(base_dir)}")
            print(f"             sortie  : {out_csv.relative_to(base_dir)}")

        sessions.append({
            "date"        : dt,
            "label"       : label,
            "pos_path"    : pos_path,
            "frames_path" : frames_path,
            "out_csv"     : out_csv,
            "out_xlsx"    : out_xlsx,
            "already_done": already_done,
        })

    print(f"{'─'*65}\n")
    return sessions


# ══════════════════════════════════════════════════════════════════════════════
#  Lecture du .pos RTKLIB
# ══════════════════════════════════════════════════════════════════════════════

def _parse_ref_pos(path: Path) -> tuple[float, float, float] | None:
    pattern = re.compile(
        r"%\s*ref pos\s*:\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)",
        re.IGNORECASE,
    )
    with open(path, "r", errors="replace") as f:
        for line in f:
            if not line.startswith("%"):
                break
            m = pattern.match(line)
            if m:
                return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None


def load_pos(path: Path) -> tuple[pd.DataFrame, tuple[float, float, float] | None]:
    raw_cols = [
        "date_", "time_",
        "lat_deg", "lon_deg", "height_m",
        "Q", "ns",
        "sdn_m", "sde_m", "sdu_m",
        "sdne_m", "sdeu_m", "sdun_m",
        "age_s", "ratio",
    ]
    df = pd.read_csv(path, comment="%", sep=r"\s+", header=None, names=raw_cols)
    df["datetime_str"] = df["date_"].str.strip() + " " + df["time_"].str.strip()
    df["gps_time_utc"] = pd.to_datetime(
        df["datetime_str"], format="%Y/%m/%d %H:%M:%S.%f", utc=True
    )
    df["gps_ts"] = df["gps_time_utc"].apply(lambda x: x.timestamp())
    df.drop(columns=["date_", "time_"], inplace=True)
    df.sort_values("gps_ts", inplace=True)
    df.reset_index(drop=True, inplace=True)

    ref = _parse_ref_pos(path)
    print(
        f"  [GPS]    {len(df)} points  "
        f"({df['gps_time_utc'].iloc[0].strftime('%H:%M:%S')} → "
        f"{df['gps_time_utc'].iloc[-1].strftime('%H:%M:%S')} UTC)"
    )
    if ref:
        print(f"  [GPS]    Ref ENU : lat={ref[0]:.6f}, lon={ref[1]:.6f}, alt={ref[2]:.2f} m")
    return df, ref


# ══════════════════════════════════════════════════════════════════════════════
#  Lecture du CSV de timestamps
# ══════════════════════════════════════════════════════════════════════════════

def load_frames(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]

    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if "frame" in cl and ("num" in cl or "no" in cl or cl == "frame"):
            col_map[c] = "frame_number"
        elif "time" in cl or "ts" in cl or "stamp" in cl:
            col_map[c] = "timestamp_ms"
    df.rename(columns=col_map, inplace=True)

    if "frame_number" not in df.columns or "timestamp_ms" not in df.columns:
        raise ValueError(
            f"Colonnes non reconnues dans {path}.\n"
            f"Colonnes trouvées : {list(df.columns)}\n"
            "Attendu : 'Frame Number' et 'Timestamp (ms)'."
        )

    df["frame_ts"] = df["timestamp_ms"] / 1000.0

    ref_epoch = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    if df["frame_ts"].iloc[0] < ref_epoch:
        raise ValueError(
            f"Timestamp suspect ({df['timestamp_ms'].iloc[0]}) — attendu en Unix ms (après 2020)."
        )

    df.sort_values("frame_ts", inplace=True)
    df.reset_index(drop=True, inplace=True)

    t0 = datetime.fromtimestamp(df["frame_ts"].iloc[0],  tz=timezone.utc)
    t1 = datetime.fromtimestamp(df["frame_ts"].iloc[-1], tz=timezone.utc)
    print(
        f"  [Frames] {len(df)} frames  "
        f"({t0.strftime('%H:%M:%S')} → {t1.strftime('%H:%M:%S')} UTC)"
    )
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  Interpolation à vitesse constante
# ══════════════════════════════════════════════════════════════════════════════

def interpolate_positions(
    gps: pd.DataFrame,
    frames: pd.DataFrame,
    max_gap_s: float = 2.0,
    quality_filter: list | None = None,
    enu_ref: tuple[float, float, float] | None = None,
) -> pd.DataFrame:

    if quality_filter:
        gps = gps[gps["Q"].isin(quality_filter)].copy()
        print(f"  [Filtre] Q∈{quality_filter} → {len(gps)} points GPS conservés")
        if gps.empty:
            raise ValueError("Aucun point GPS ne satisfait le filtre de qualité.")

    gps_ts  = gps["gps_ts"].values
    lat     = gps["lat_deg"].values
    lon     = gps["lon_deg"].values
    height  = gps["height_m"].values
    q_vals  = gps["Q"].values
    frame_ts = frames["frame_ts"].values

    lat_interp    = np.interp(frame_ts, gps_ts, lat,    left=np.nan, right=np.nan)
    lon_interp    = np.interp(frame_ts, gps_ts, lon,    left=np.nan, right=np.nan)
    height_interp = np.interp(frame_ts, gps_ts, height, left=np.nan, right=np.nan)

    out_of_range_mask = (frame_ts < gps_ts[0]) | (frame_ts > gps_ts[-1])
    lat_interp[out_of_range_mask]    = np.nan
    lon_interp[out_of_range_mask]    = np.nan
    height_interp[out_of_range_mask] = np.nan

    idx_right   = np.searchsorted(gps_ts, frame_ts, side="left")
    idx_left    = np.clip(idx_right - 1, 0, len(gps_ts) - 1)
    idx_right   = np.clip(idx_right,     0, len(gps_ts) - 1)
    gap_left    = np.abs(frame_ts - gps_ts[idx_left])
    gap_right   = np.abs(frame_ts - gps_ts[idx_right])
    min_gap     = np.minimum(gap_left, gap_right)
    nearest_idx = np.where(gap_left <= gap_right, idx_left, idx_right)
    nearest_Q   = q_vals[nearest_idx]

    in_range = ~out_of_range_mask
    reliable = in_range & (min_gap <= max_gap_s)

    out = frames[["frame_number", "timestamp_ms", "frame_ts"]].copy()
    out["photo_time_utc"] = pd.to_datetime(
        frames["frame_ts"], unit="s", utc=True
    ).dt.strftime("%Y-%m-%d %H:%M:%S.%f")

    out["lat_gmap"] = np.round(lat_interp, 8)
    out["lon_gmap"] = np.round(lon_interp, 8)
    out["lat_rtk"]  = np.round(lat_interp, 8)
    out["lon_rtk"]  = np.round(lon_interp, 8)
    out["alt_rtk"]  = np.round(height_interp, 4)

    if enu_ref is not None:
        lat0, lon0, alt0 = enu_ref
        valid = ~np.isnan(lat_interp)
        enu_e = np.full(len(out), np.nan)
        enu_n = np.full(len(out), np.nan)
        enu_u = np.full(len(out), np.nan)
        if valid.any():
            E, N, U = latlon_array_to_enu(
                lat_interp[valid], lon_interp[valid], height_interp[valid],
                lat0, lon0, alt0,
            )
            enu_e[valid] = E
            enu_n[valid] = N
            enu_u[valid] = U
        out["enu_e_m"] = np.round(enu_e, 4)
        out["enu_n_m"] = np.round(enu_n, 4)
        out["enu_u_m"] = np.round(enu_u, 4)
    else:
        out["enu_e_m"] = out["enu_n_m"] = out["enu_u_m"] = np.nan

    out["gps_Q"]        = nearest_Q
    out["time_gap_s"]   = np.round(min_gap, 4)
    out["in_gps_range"] = in_range
    out["reliable"]     = reliable

    n_ok = reliable.sum()
    print(f"  [Sync]   {n_ok}/{len(out)} frames fiables (gap ≤ {max_gap_s} s)")
    if len(out) - n_ok:
        print(f"           {len(out) - n_ok} frames hors plage ou gap trop grand → NaN")
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Colonnes d'export
# ══════════════════════════════════════════════════════════════════════════════

COLS_EXPORT = [
    "frame_number", "photo_time_utc", "timestamp_ms",
    "lat_gmap", "lon_gmap",
    "lat_rtk", "lon_rtk", "alt_rtk",
    "enu_e_m", "enu_n_m", "enu_u_m",
    "gps_Q", "time_gap_s", "in_gps_range", "reliable",
]


# ══════════════════════════════════════════════════════════════════════════════
#  Sauvegarde CSV + Excel
# ══════════════════════════════════════════════════════════════════════════════

def save_output(df: pd.DataFrame, out_csv: Path, out_xlsx: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    df[COLS_EXPORT].to_csv(out_csv, index=False)
    print(f"  [CSV]    → {out_csv.name}")

    try:
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
            df[COLS_EXPORT].to_excel(writer, index=False, sheet_name="Synchronized")
            ws = writer.sheets["Synchronized"]
            for col_cells in ws.columns:
                max_len = max(
                    len(str(cell.value)) if cell.value is not None else 0
                    for cell in col_cells
                )
                ws.column_dimensions[col_cells[0].column_letter].width = min(max_len + 2, 30)
        print(f"  [Excel]  → {out_xlsx.name}")
    except ImportError:
        print("  [!] openpyxl manquant → seul le CSV a été créé  (pip install openpyxl)")


# ══════════════════════════════════════════════════════════════════════════════
#  Traitement d'une session
# ══════════════════════════════════════════════════════════════════════════════

def process_session(session: dict, max_gap_s: float, quality_filter, enu_ref_override) -> bool:
    """
    Traite une session : charge, interpole, sauvegarde.
    Retourne True si succès, False si erreur.
    """
    label       = session["label"]
    pos_path    = session["pos_path"]
    frames_path = session["frames_path"]
    out_csv     = session["out_csv"]
    out_xlsx    = session["out_xlsx"]

    print(f"\n{'═'*65}")
    print(f"  SESSION  {label}")
    print(f"{'═'*65}")

    try:
        gps, ref_from_file = load_pos(pos_path)
        frames = load_frames(frames_path)

        # Résolution de l'origine ENU
        if enu_ref_override:
            enu_ref = enu_ref_override
            print(f"  [ENU]    Origine manuelle : {enu_ref}")
        elif ref_from_file:
            enu_ref = ref_from_file
            print(f"  [ENU]    Origine depuis .pos : {enu_ref}")
        else:
            enu_ref = (
                float(gps["lat_deg"].iloc[0]),
                float(gps["lon_deg"].iloc[0]),
                float(gps["height_m"].iloc[0]),
            )
            print(f"  [ENU]    Origine = premier point GPS : {enu_ref}")

        result = interpolate_positions(
            gps, frames,
            max_gap_s=max_gap_s,
            quality_filter=quality_filter,
            enu_ref=enu_ref,
        )

        save_output(result, out_csv, out_xlsx)

        # Aperçu rapide
        cols_preview = ["frame_number", "photo_time_utc",
                        "lat_gmap", "lon_gmap", "time_gap_s", "reliable"]
        print(f"\n  Aperçu (3 premières lignes) :")
        print(result[cols_preview].head(3).to_string(index=False, col_space=12))
        return True

    except Exception as exc:
        print(f"\n  [ERREUR] Session {label} : {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Synchronise par date les fichiers .pos PPK avec les timestamps de frames.\n"
            "Traite toutes les paires disponibles et ignore les sessions déjà traitées."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-dir", type=Path, default=None,
        help="Répertoire racine du projet (défaut : répertoire du script)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Retraite toutes les sessions même si les fichiers de sortie existent déjà",
    )
    parser.add_argument(
        "--max-gap", type=float, default=2.0,
        help="Gap temporel max (s) pour marquer une frame 'reliable' (défaut : 2.0)",
    )
    parser.add_argument(
        "--quality", type=int, nargs="+", default=None,
        help="Valeurs Q GPS acceptées : 1=fixe  2=float  5=seul (défaut : toutes)",
    )
    parser.add_argument(
        "--enu-ref", type=str, default=None,
        help="Origine ENU manuelle 'lat,lon,alt' appliquée à toutes les sessions",
    )
    args = parser.parse_args()

    # ── Répertoire de base ────────────────────────────────────────────────────
    base_dir = args.base_dir if args.base_dir else Path(__file__).resolve().parent

    # ── Dossier de sortie ─────────────────────────────────────────────────────
    output_dir = base_dir / "final_Output" / "Timesample_synchronised_files"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Origine ENU manuelle ──────────────────────────────────────────────────
    enu_ref_override = None
    if args.enu_ref:
        parts = [float(x) for x in args.enu_ref.replace(" ", "").split(",")]
        enu_ref_override = (parts[0], parts[1], parts[2])

    print("=" * 65)
    print("  Synchronisation GPS ↔ Frames  —  matching par date")
    print(f"  Projet : {base_dir}")
    print("=" * 65)

    # ── Scan et appariement ───────────────────────────────────────────────────
    sessions = find_sessions(base_dir, output_dir, force=args.force)

    to_process = [s for s in sessions if not s["already_done"]]
    already_ok = [s for s in sessions if s["already_done"]]

    if not to_process:
        if already_ok:
            print(f"Toutes les sessions ({len(already_ok)}) sont déjà traitées.")
            print("Utilisez --force pour les retraiter.")
        else:
            print("Aucune paire (pos + frames) trouvée avec la même date.")
        sys.exit(0)

    print(f"{len(to_process)} session(s) à traiter, {len(already_ok)} déjà traitée(s).\n")

    # ── Traitement de chaque session ──────────────────────────────────────────
    results = []
    for session in to_process:
        ok = process_session(session, args.max_gap, args.quality, enu_ref_override)
        results.append((session["label"], ok))

    # ── Rapport final ─────────────────────────────────────────────────────────
    print(f"\n{'═'*65}")
    print("  RAPPORT FINAL")
    print(f"{'═'*65}")
    for label, ok in results:
        status = "✓ OK" if ok else "✗ ERREUR"
        print(f"  [{label}]  {status}")
    for s in already_ok:
        print(f"  [{s['label']}]  — déjà traité (ignoré)")

    n_ok  = sum(1 for _, ok in results if ok)
    n_err = sum(1 for _, ok in results if not ok)
    print(f"\n  Total : {n_ok} succès, {n_err} erreur(s), {len(already_ok)} ignoré(s)")
    print(f"  Sorties dans : {output_dir}")
    print("=" * 65)

    if n_err:
        sys.exit(1)


if __name__ == "__main__":
    main()