#!/usr/bin/env python3
"""
PPK_Pipeline_piquetee.py — Traitement batch PPK : boucle sur ubx/ → pos/
Usage :
    python3 PPK_Pipeline_piquetee.py                          
    python3 PPK_Pipeline_piquetee.py --ubx-dir /autre/chemin  
    python3 PPK_Pipeline_piquetee.py --pos-dir /autre/sortie  
    python3 PPK_Pipeline_piquetee.py --acronyme BXME --verbose
    python3 PPK_Pipeline_piquetee.py --skip-existing          
"""

from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, date, timedelta
from pathlib import Path

RESET  = "\033[0m";  BOLD   = "\033[1m"
GREEN  = "\033[92m"; YELLOW = "\033[93m"
RED    = "\033[91m"; CYAN   = "\033[96m"
GREY   = "\033[90m"; MAGENTA= "\033[95m"

def ok(msg):   print(f"{GREEN}✅  {msg}{RESET}")
def err(msg):  print(f"{RED}❌  {msg}{RESET}", file=sys.stderr)
def info(msg): print(f"{CYAN}ℹ️   {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠️   {msg}{RESET}")
def step(msg): print(f"\n{BOLD}{CYAN}▶  {msg}{RESET}")
def banner(msg): print(f"\n{BOLD}{MAGENTA}{'═'*50}\n  {msg}\n{'═'*50}{RESET}")

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(txt: str) -> str:
    return ANSI_RE.sub("", txt)

def run_cmd(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return p.returncode, strip_ansi(p.stdout), strip_ansi(p.stderr)

def round_down_quarter(dt: datetime) -> datetime:
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)

def round_up_quarter(dt: datetime) -> datetime:
    m = ((dt.minute + 14) // 15) * 15
    if m == 60:
        dt = dt.replace(minute=0) + timedelta(hours=1)
    else:
        dt = dt.replace(minute=m)
    return dt.replace(second=0, microsecond=0)

def extract_gga_timestamps(ubx_path: Path):
    text = ubx_path.read_bytes().decode(errors="ignore")

    timestamps = []
    for sentence in re.findall(r"\$(?:G[NP]GGA),[^\r\n]*", text):
        parts = sentence.split(",")
        if len(parts) > 1:
            try:
                t = parts[1]
                timestamps.append((int(t[0:2]), int(t[2:4]), int(t[4:6])))
            except Exception:
                continue

    acquisition_date = None
    for sentence in re.findall(r"\$(?:G[NP]RMC),[^\r\n]*", text):
        parts = sentence.split(",")
        if len(parts) > 9 and parts[9]:
            try:
                d = parts[9]
                acquisition_date = date(int(d[4:6]) + 2000, int(d[2:4]), int(d[0:2]))
                break
            except Exception:
                continue

    if not timestamps:
        return None, None, None

    h1, m1, s1 = timestamps[0]
    h2, m2, s2 = timestamps[-1]

    if acquisition_date:
        start_dt = datetime(acquisition_date.year, acquisition_date.month, acquisition_date.day, h1, m1, s1)
        end_dt   = datetime(acquisition_date.year, acquisition_date.month, acquisition_date.day, h2, m2, s2)
    else:
        today = date.today()
        start_dt = datetime(today.year, today.month, today.day, h1, m1, s1)
        end_dt   = datetime(today.year, today.month, today.day, h2, m2, s2)

    return start_dt, end_dt, acquisition_date

def update_config_json(config_path: Path, start_dt: datetime, end_dt: datetime,
                       acq_date, acronyme: str | None = None):
    start_r = round_down_quarter(start_dt)
    end_r   = round_up_quarter(end_dt)
    if end_r <= start_r + timedelta(minutes=59):
        end_r = start_r + timedelta(hours=1)

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {"ppk": {}, "gpslogger": {}}

    config.setdefault("ppk", {})
    config["ppk"]["start_time"] = start_r.strftime("%H:%M")
    config["ppk"]["end_time"]   = end_r.strftime("%H:%M")
    if acq_date:
        config["ppk"]["date"] = acq_date.strftime("%d/%m/%Y")
    if acronyme:
        config["ppk"]["acronyme"] = acronyme

    config_path.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")
    return start_r, end_r

def find_pos_file(root: Path) -> Path | None:
    for p in root.rglob("*.pos"):
        if not p.name.endswith("_events.pos"):
            return p.resolve()
    return None

def parse_pos_from_stdout(stdout: str) -> Path | None:
    m = re.search(r"Result saved to:\s*([^\s]+\.pos)", stdout)
    return Path(m.group(1)).resolve() if m else None

def process_one(ubx_path: Path, pos_dir: Path, gps_dir: Path, args) -> bool:
    config_path  = gps_dir / "config.json"
    correct_sh   = gps_dir / "correct_new.sh"
    rtkpost_conf = gps_dir / "rtkpost.conf"
    rgp_dl_py    = gps_dir / "rgp_station_downloader_from_config.py"

    stem     = ubx_path.stem.replace("trk_", "")
    work_dir = gps_dir / "uploads" / f"session_{stem}"
    corr_dir = work_dir / "correction"
    corr_dir.mkdir(parents=True, exist_ok=True)

    step(f"[{ubx_path.name}] Lecture GGA/RMC")
    start_dt, end_dt, acq_date = extract_gga_timestamps(ubx_path)
    if not (start_dt and end_dt):
        err(f"  Impossible d'extraire les horodatages GGA → fichier ignoré.")
        return False
    info(f"  Plage : {start_dt.strftime('%H:%M:%S')} → {end_dt.strftime('%H:%M:%S')}  |  {acq_date}")

    start_r, end_r = update_config_json(config_path, start_dt, end_dt, acq_date, args.acronyme)
    
    try:
        _cfg = json.loads(config_path.read_text(encoding="utf-8"))
        _cfg.setdefault("gpslogger", {})["DESTINATION_FOLDER"] = str(gps_dir)
        config_path.write_text(json.dumps(_cfg, indent=4, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        warn(f"  Impossible de mettre à jour DESTINATION_FOLDER : {e}")
    ok(f"  config.json → {start_r.strftime('%H:%M')} – {end_r.strftime('%H:%M')}")

    if not args.no_rgp and rgp_dl_py.exists():
        step(f"[{ubx_path.name}] Téléchargement RGP")
        rc, out, err_str = run_cmd(["python3", str(rgp_dl_py)], cwd=gps_dir)
        if args.verbose:
            print(GREY + out + RESET)
        if rc:
            err(f"  Téléchargement RGP échoué :\n{err_str}")
            return False

        try:
            _cfg = json.loads((gps_dir / "config.json").read_text(encoding="utf-8"))
            dest_folder = Path(_cfg.get("gpslogger", {}).get("DESTINATION_FOLDER", "")).expanduser()
        except Exception:
            dest_folder = Path("")

        search_dirs = [gps_dir, Path(".").resolve()]
        if dest_folder and dest_folder.exists() and dest_folder not in search_dirs:
            search_dirs.append(dest_folder)

        zip_candidates = []
        for d in search_dirs:
            zip_candidates.extend(d.glob("*.zip"))

        zip_files = sorted(set(zip_candidates), key=lambda p: p.stat().st_mtime, reverse=True)

        if not zip_files:
            err("  Archive ZIP RGP introuvable.")
            info(f"  Dossiers cherchés : {[str(d) for d in search_dirs]}")
            info(f"  DESTINATION_FOLDER dans config.json : {dest_folder}")
            return False

        zip_path = zip_files[0]
        info(f"  Archive : {zip_path.name}")
        tmp = corr_dir / "__tmp"
        tmp.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp)
        for root, _, files in os.walk(tmp):
            for f in files:
                shutil.move(str(Path(root) / f), corr_dir / f)
        shutil.rmtree(tmp)
        zip_path.unlink()
        ok("  Données RGP extraites.")
    elif args.no_rgp:
        warn("  --no-rgp : téléchargement ignoré.")

    base_files = list(corr_dir.glob("*.??o"))
    if not base_files:
        err("  Aucun fichier base *.??o trouvé dans le dossier correction.")
        return False

    base_obs_file = base_files[0]
    info(f"  Base : {base_obs_file.name}")

    shutil.copy(correct_sh,   corr_dir / "correct_new.sh")
    shutil.copy(rtkpost_conf, corr_dir / "rtkpost.conf")
    shutil.copy(ubx_path,     corr_dir / ubx_path.name)

    step(f"[{ubx_path.name}] Correction PPK (RTKLIB)")
    rc, out, err_str = run_cmd(
        ["bash", "correct_new.sh", ubx_path.name, base_obs_file.name],
        cwd=corr_dir
    )
    if args.verbose or rc != 0:
        print(GREY + out + RESET)
        if err_str:
            print(YELLOW + err_str + RESET)

    if rc != 0:
        err(f"  correct_new.sh a échoué (code {rc}).")
        return False

    pos_path = parse_pos_from_stdout(out)
    if not (pos_path and pos_path.exists()):
        pos_path = find_pos_file(work_dir)
    if not (pos_path and pos_path.exists()):
        err("  Fichier .pos introuvable après correction.")
        return False

    final_pos = pos_dir / pos_path.name
    shutil.copy(str(pos_path), final_pos)
    ok(f"  .pos enregistré → {final_pos}")

    return True

def main():
    parser = argparse.ArgumentParser(
        description="Batch PPK : traite tous les .ubx dans ubx/ → .pos dans pos/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 PPK_Pipeline_piquetee.py
  python3 PPK_Pipeline_piquetee.py --ubx-dir ./ubx --pos-dir ./pos
  python3 PPK_Pipeline_piquetee.py --skip-existing
  python3 PPK_Pipeline_piquetee.py --acronyme BXME --verbose
  python3 PPK_Pipeline_piquetee.py --no-rgp
        """
    )
    parser.add_argument("--ubx-dir",  default=None)
    parser.add_argument("--pos-dir",  default=None)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--no-rgp",   action="store_true")
    parser.add_argument("--acronyme", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")

    args       = parser.parse_args()
    script_dir = Path(__file__).resolve().parent
    gps_dir    = script_dir / "scripts_GPS"

    config_file = script_dir / "pipeline_config.json"
    
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        
        project_root = Path(config["project_root"])
        json_ubx_dir = project_root / config["paths"]["bag_input_dir"]
        json_pos_dir = project_root / config["paths"]["ppk_pos_dir"]
    else:
        err(f"Fichier de configuration introuvable : {config_file}")
        sys.exit(1)

    ubx_dir    = Path(args.ubx_dir).resolve() if args.ubx_dir else json_ubx_dir
    pos_dir    = Path(args.pos_dir).resolve() if args.pos_dir else json_pos_dir

    if not ubx_dir.exists():
        err(f"Dossier UBX introuvable : {ubx_dir}")
        sys.exit(1)

    for dep in ["correct_new.sh", "rtkpost.conf"]:
        if not (gps_dir / dep).exists():
            err(f"Fichier requis manquant : scripts_GPS/{dep}")
            sys.exit(1)

    for tool in ["convbin", "rnx2rtkp"]:
        if shutil.which(tool) is None:
            err(f"Outil RTKLIB '{tool}' introuvable dans le PATH.")
            sys.exit(1)

    pos_dir.mkdir(parents=True, exist_ok=True)

    all_ubx = sorted(ubx_dir.glob("*.ubx"))
    if not all_ubx:
        warn(f"Aucun fichier .ubx trouvé dans : {ubx_dir}")
        sys.exit(0)

    if args.skip_existing:
        existing_pos = {p.stem for p in pos_dir.glob("*.pos")}
        ubx_list = [u for u in all_ubx if u.stem not in existing_pos]
        skipped = len(all_ubx) - len(ubx_list)
        if skipped:
            info(f"{skipped} fichier(s) déjà traité(s) ignoré(s) (--skip-existing).")
    else:
        ubx_list = all_ubx

    if not ubx_list:
        ok("Tous les fichiers sont déjà traités. Rien à faire.")
        sys.exit(0)

    banner(f"Batch PPK — {len(ubx_list)} fichier(s) à traiter")
    info(f"Source : {ubx_dir}")
    info(f"Sortie : {pos_dir}")

    results = {"ok": [], "fail": []}

    for i, ubx_path in enumerate(ubx_list, 1):
        print(f"\n{BOLD}{'─'*50}{RESET}")
        print(f"{BOLD}[{i}/{len(ubx_list)}] {ubx_path.name}{RESET}")
        print(f"{BOLD}{'─'*50}{RESET}")

        success = process_one(ubx_path, pos_dir, gps_dir, args)
        if success:
            results["ok"].append(ubx_path.name)
        else:
            results["fail"].append(ubx_path.name)

    banner("Résumé batch")
    print(f"  {GREEN}✅ Succès  : {len(results['ok'])}{RESET}")
    for name in results["ok"]:
        print(f"      {GREEN}• {name}{RESET}")

    if results["fail"]:
        print(f"\n  {RED}❌ Échecs  : {len(results['fail'])}{RESET}")
        for name in results["fail"]:
            print(f"      {RED}• {name}{RESET}")

    print(f"\n  📁 Fichiers .pos enregistrés dans : {BOLD}{pos_dir}{RESET}\n")

    sys.exit(0 if not results["fail"] else 1)


if __name__ == "__main__":
    main()