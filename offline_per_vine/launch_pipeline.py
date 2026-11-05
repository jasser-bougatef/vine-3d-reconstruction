#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║        LAUNCH_PIPELINE  —  Orchestrateur complet du pipeline vigne       ║
║                                                                          ║
║  Tous les chemins sont lus depuis pipeline_config.json                   ║
║                                                                          ║
║  Ordre d'exécution :                                                     ║
║    ÉTAPE 1  →  extract_timesample.py   (timestamps .bag → CSV)           ║
║    ÉTAPE 2  →  PPK_Pipeline.py         (.ubx → .pos PPK)                 ║
║    ÉTAPE 3  →  syn_code.py             (synchronisation GPS ↔ frames)    ║
║    ÉTAPE 4  →  Generate_pied_jobs_.py  (assignation pieds + pipeline 3D) ║
║                                                                          ║
║  Usage :                                                                 ║
║    python3 launch_pipeline.py                    # tout automatique      ║
║    python3 launch_pipeline.py --skip-ppk         # PPK déjà fait         ║
║    python3 launch_pipeline.py --skip-extract     # timestamps déjà faits ║
║    python3 launch_pipeline.py --no-run           # sans pipeline 3D      ║
║    python3 launch_pipeline.py --radius 0.6       # fenêtre ±60 cm        ║
║    python3 launch_pipeline.py --pass-select 1    # aller seulement       ║
║    python3 launch_pipeline.py --pieds 5 6 7      # pieds spécifiques     ║
║    python3 launch_pipeline.py --force-sync       # re-synchronise tout   ║
║    python3 launch_pipeline.py --acronyme BXME    # station RGP           ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — CHARGEMENT DE LA CONFIG CENTRALE
# ══════════════════════════════════════════════════════════════════════════════

def _load_config() -> dict:
    cfg_path = Path(__file__).resolve().parent / "pipeline_config.json"
    if not cfg_path.exists():
        sys.exit(f"[ERREUR] pipeline_config.json introuvable : {cfg_path}")
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)

_CFG = _load_config()

PROJECT_ROOT   = Path(_CFG["project_root"])
BAG_INPUT_DIR  = PROJECT_ROOT / _CFG["paths"]["bag_input_dir"]
EXTRACTION_OUT = PROJECT_ROOT / _CFG["paths"]["extraction_output"]
TIMESAMPLE_OUT = PROJECT_ROOT / _CFG["paths"]["timesample_files"]
PPK_POS_DIR    = PROJECT_ROOT / _CFG["paths"]["ppk_pos_dir"]
SYN_OUTPUT_DIR = PROJECT_ROOT / _CFG["paths"]["syn_output_dir"]
FINAL_OUT      = PROJECT_ROOT / _CFG["paths"]["final_output"]

# Chemins des scripts — tous dans PROJECT_ROOT
SCRIPTS = {
    "extract":   PROJECT_ROOT / "extract_timesample.py",
    "ppk":       PROJECT_ROOT / "PPK_Pipeline.py",
    "sync":      PROJECT_ROOT / "syn_code.py",
    "pied_jobs": PROJECT_ROOT / "Generate_pied_jobs_.py",
}


def _get_runtime_cfg() -> dict:
    """Relit pipeline_config.json à chaque appel.
    Garantit que toute modification du JSON (rayon, rotations, z_min/z_max…)
    est prise en compte sans relancer Python."""
    return _load_config()

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES CONSOLE
# ══════════════════════════════════════════════════════════════════════════════

RESET  = "\033[0m";  BOLD   = "\033[1m"
GREEN  = "\033[92m"; CYAN   = "\033[96m"
RED    = "\033[91m"; YELLOW = "\033[93m"

def banner(text, char="═", width=72):
    print(f"\n{BOLD}{char * width}")
    print(f"  {text}")
    print(f"{char * width}{RESET}")

def step_header(n, total, label):
    print(f"\n{BOLD}{CYAN}{'─' * 72}")
    print(f"  ÉTAPE {n}/{total}  —  {label}")
    print(f"{'─' * 72}{RESET}")

def ok(msg):   print(f"{GREEN}  ✔  {msg}{RESET}")
def err(msg):  print(f"{RED}  ✘  {msg}{RESET}", file=sys.stderr)
def warn(msg): print(f"{YELLOW}  ⚠  {msg}{RESET}")
def info(msg): print(f"{CYAN}  ℹ  {msg}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
#  VÉRIFICATIONS PRÉ-LANCEMENT
# ══════════════════════════════════════════════════════════════════════════════

def preflight_check(args):
    """Vérifie que les prérequis sont présents avant de démarrer."""
    errors = []

    if not PROJECT_ROOT.exists():
        errors.append(f"PROJECT_ROOT introuvable : {PROJECT_ROOT}")

    for name, path in SCRIPTS.items():
        if not path.exists():
            errors.append(f"Script manquant [{name}] : {path}")

    if not BAG_INPUT_DIR.exists():
        errors.append(f"Dossier .bag introuvable : {BAG_INPUT_DIR}")

    bag_files = list(BAG_INPUT_DIR.glob("*.bag"))
    if not bag_files:
        errors.append(f"Aucun fichier .bag dans : {BAG_INPUT_DIR}")

    ubx_files = list(BAG_INPUT_DIR.glob("*.ubx"))
    if not ubx_files and not args.skip_ppk:
        errors.append(f"Aucun fichier .ubx dans : {BAG_INPUT_DIR}")

    if errors:
        banner("ERREURS DE CONFIGURATION", "✘")
        for e in errors:
            err(e)
        sys.exit(1)

    info(f"Projet     : {PROJECT_ROOT}")
    info(f"Config     : pipeline_config.json")
    info(f"Fichiers   : {len(bag_files)} .bag, {len(ubx_files)} .ubx")
    ok("Prérequis OK")


# ══════════════════════════════════════════════════════════════════════════════
#  CHARGEMENT DYNAMIQUE D'UN MODULE
# ══════════════════════════════════════════════════════════════════════════════

def load_module(name: str, path: Path):
    """Charge un script Python comme module sans l'exécuter."""
    if not path.exists():
        err(f"Script introuvable : {path}")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 1  —  EXTRACTION DES TIMESTAMPS
# ══════════════════════════════════════════════════════════════════════════════

def run_extract_timestamps(args):
    """
    Appelle batch_extract_timestamps() depuis extract_timesample.py.
    Entrée  : BAG_INPUT_DIR  (*.bag)
    Sortie  : TIMESAMPLE_OUT / data_DD-MM-YYYY / frame_timestamps.csv
    """
    step_header(1, 4, "Extraction des timestamps (.bag → frame_timestamps.csv)")

    existing = list(TIMESAMPLE_OUT.glob("data_*/frame_timestamps.csv"))
    if existing and not args.force_extract:
        warn(f"{len(existing)} session(s) déjà extraite(s).")
        info("Utilisez --force-extract pour tout re-extraire.")
        info("Seuls les nouveaux .bag seront traités automatiquement si absents.")

    TIMESAMPLE_OUT.mkdir(parents=True, exist_ok=True)

    mod = load_module("extract_timesample", SCRIPTS["extract"])
    mod.batch_extract_timestamps(str(BAG_INPUT_DIR), str(TIMESAMPLE_OUT))

    produced = list(TIMESAMPLE_OUT.glob("data_*/frame_timestamps.csv"))
    if not produced:
        err("Aucun frame_timestamps.csv produit — vérifiez les .bag.")
        sys.exit(1)

    ok(f"{len(produced)} fichier(s) frame_timestamps.csv disponibles.")
    return produced


# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 2  —  PPK PIPELINE  (.ubx → .pos)
# ══════════════════════════════════════════════════════════════════════════════

def run_ppk_pipeline(args):
    """
    Appelle process_one() de PPK_Pipeline.py pour chaque .ubx non encore traité.
    Court-circuite main() pour éviter son sys.exit().
    """
    step_header(2, 4, "Correction PPK (.ubx → .pos)")

    PPK_POS_DIR.mkdir(parents=True, exist_ok=True)

    ppk_args = argparse.Namespace(
        ubx_dir       = str(BAG_INPUT_DIR),
        pos_dir       = str(PPK_POS_DIR),
        skip_existing = True,
        no_rgp        = args.no_rgp,
        acronyme      = args.acronyme,
        verbose       = args.verbose,
    )

    mod     = load_module("ppk_pipeline", SCRIPTS["ppk"])
    gps_dir = SCRIPTS["ppk"].parent / "scripts_GPS"

    for tool in ["convbin", "rnx2rtkp"]:
        if shutil.which(tool) is None:
            err(f"Outil RTKLIB '{tool}' introuvable dans le PATH.")
            info("Lancez install_rtklib_tools.sh pour l'installer.")
            sys.exit(1)

    all_ubx = sorted(BAG_INPUT_DIR.glob("*.ubx"))
    if not all_ubx:
        warn(f"Aucun .ubx dans {BAG_INPUT_DIR} — étape PPK ignorée.")
        return

    existing_pos = {p.stem for p in PPK_POS_DIR.glob("*.pos")}
    ubx_list = [u for u in all_ubx if u.stem not in existing_pos]

    if not ubx_list:
        ok(f"Tous les .ubx sont déjà traités ({len(all_ubx)} .pos disponibles).")
        return

    info(f"{len(ubx_list)} .ubx à traiter ({len(all_ubx) - len(ubx_list)} déjà faits).")

    results = {"ok": [], "fail": []}
    for ubx_path in ubx_list:
        print(f"\n  ▶  {ubx_path.name}")
        success = mod.process_one(ubx_path, PPK_POS_DIR, gps_dir, ppk_args)
        (results["ok"] if success else results["fail"]).append(ubx_path.name)

    ok(f"PPK terminé : {len(results['ok'])} succès, {len(results['fail'])} échec(s).")

    if results["fail"]:
        warn(f"Fichiers en échec : {results['fail']}")
        err("Certains .ubx n'ont pas pu être traités. Vérifiez les logs ci-dessus.")
        if not args.continue_on_error:
            sys.exit(1)

    pos_files = list(PPK_POS_DIR.glob("*.pos"))
    if not pos_files:
        err("Aucun .pos disponible après le PPK — pipeline interrompu.")
        sys.exit(1)

    ok(f"{len(pos_files)} fichier(s) .pos disponibles dans {PPK_POS_DIR}.")


# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 3  —  SYNCHRONISATION GPS ↔ FRAMES
# ══════════════════════════════════════════════════════════════════════════════

def run_synchronisation(args):
    """
    Appelle process_session() de syn_code.py pour chaque paire (pos, frames).
    Produit les synchronized_DD-MM-YYYY.csv dans SYN_OUTPUT_DIR.
    """
    step_header(3, 4, "Synchronisation GPS ↔ Timestamps (→ synchronized_*.csv)")

    SYN_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    mod      = load_module("syn_code", SCRIPTS["sync"])
    sessions = mod.find_sessions(PPK_POS_DIR, TIMESAMPLE_OUT, SYN_OUTPUT_DIR, force=args.force_sync)

    to_process = [s for s in sessions if not s["already_done"]]
    already_ok = [s for s in sessions if     s["already_done"]]

    if not to_process:
        if already_ok:
            ok(f"Toutes les sessions ({len(already_ok)}) déjà synchronisées.")
            info("Utilisez --force-sync pour resynchroniser.")
        else:
            err("Aucune paire (pos + frames) trouvée avec la même date.")
            info(
                "Vérifiez :\n"
                f"  • .pos dans  : {PPK_POS_DIR}\n"
                f"  • CSV dans   : {TIMESAMPLE_OUT}"
            )
            sys.exit(1)
        return

    info(f"{len(to_process)} session(s) à synchroniser.")

    shp_path = PROJECT_ROOT / _CFG["paths"]["shapefile"]

    results = []
    for session in to_process:
        ok_flag = mod.process_session(
            session,
            max_gap_s        = args.max_gap,
            quality_filter   = args.quality,
            enu_ref_override = None,
            shp_path         = shp_path  
        )
        results.append((session["label"], ok_flag))

    n_ok  = sum(1 for _, f in results if f)
    n_err = sum(1 for _, f in results if not f)
    ok(f"Synchronisation : {n_ok} succès, {n_err} erreur(s).")

    if n_err and not args.continue_on_error:
        err("Erreurs de synchronisation — pipeline interrompu.")
        sys.exit(1)

    syn_files = list(SYN_OUTPUT_DIR.glob("synchronized_*.csv"))
    if not syn_files:
        err("Aucun synchronized_*.csv produit.")
        sys.exit(1)

    ok(f"{len(syn_files)} fichier(s) synchronisés disponibles.")


# ══════════════════════════════════════════════════════════════════════════════
#  ÉTAPE 4  —  GÉNÉRATION DES JOBS PIEDS + PIPELINE 3D
# ══════════════════════════════════════════════════════════════════════════════

def run_pied_jobs(args):
    """
    Appelle Generate_pied_jobs_.py :
      - lit le shapefile Lambert93 (chemin depuis pipeline_config.json)
      - convertit les CSV synchronisés en Lambert93
      - assigne les frames aux pieds
      - exporte le CSV récapitulatif
      - lance Run_full_pipeline.py si --no-run n'est pas activé
    """
    step_header(4, 4, "Assignation pieds → frames + Reconstruction 3D")

    mod = load_module("generate_pied_jobs", SCRIPTS["pied_jobs"])

    # Le shapefile est lu depuis pipeline_config.json par le module lui-même
    shp_path = mod.DEFAULT_SHP
    if not shp_path.exists():
        err(f"Shapefile introuvable : {shp_path}")
        info("Vérifiez 'paths.shapefile' dans pipeline_config.json")
        sys.exit(1)

    print(f"\n  Lecture du shapefile : {shp_path}")
    pieds_l93 = mod.load_shapefile(shp_path)

    print(f"\n  Conversion WGS84 → Lambert93 : {SYN_OUTPUT_DIR}")
    sync_data = mod.load_sync_files(SYN_OUTPUT_DIR)

    print(f"\n  Assignation  (rayon Y_L93 = ±{args.radius} m, passes = {args.pass_select})")
    jobs = mod.build_bag_jobs(
        pieds_l93   = pieds_l93,
        sync_data   = sync_data,
        radius      = args.radius,
        pass_select = args.pass_select,
        pied_filter = args.pieds,
    )

    if not jobs:
        warn(
            "Aucun job généré.\n"
            "    → Essayez --radius 0.6 ou --radius 1.0\n"
            "    → Vérifiez 'bag_registry' dans pipeline_config.json"
        )
        return

    print(f"\n  {'─' * 68}")
    print(f"  {'JOB NAME':<44} {'FRAMES':>12}   {'N FR':>6}")
    print(f"  {'─' * 68}")
    for j in jobs:
        print(
            f"  {j['job_name']:<44} "
            f"{j['start_frame']:>5} → {j['end_frame']:<5}  "
            f"{j['_n_frames']:>5} fr"
        )
    print(f"  {'─' * 68}")
    print(f"  Total : {len(jobs)} job(s)  |  rayon = ±{args.radius} m\n")

    out_csv = PROJECT_ROOT / _get_runtime_cfg()["paths"]["pied_jobs_summary"]
    mod.export_summary_csv(jobs, out_csv)

    if args.no_run:
        warn("--no-run activé : pipeline 3D non lancé.")
        info(f"Résumé exporté → {out_csv}")
        return

    mod.launch_pipeline(jobs, mod.RUN_PIPELINE_SCRIPT)


# ══════════════════════════════════════════════════════════════════════════════
#  POINT D'ENTRÉE
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Orchestrateur du pipeline complet viticulture :\n"
            "  1. Extraction timestamps  →  2. PPK  →  3. Sync  →  4. Pieds 3D\n"
            "  Tous les chemins sont lus depuis pipeline_config.json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 launch_pipeline.py                        # pipeline complet
  python3 launch_pipeline.py --skip-extract         # timestamps déjà faits
  python3 launch_pipeline.py --skip-ppk             # .pos déjà disponibles
  python3 launch_pipeline.py --skip-sync            # synchronized_*.csv déjà faits
  python3 launch_pipeline.py --no-run               # sans reconstruction 3D
  python3 launch_pipeline.py --radius 0.6 --pass-select 1
  python3 launch_pipeline.py --pieds 5 6 7 8
  python3 launch_pipeline.py --force-sync --force-extract
  python3 launch_pipeline.py --acronyme BXME --verbose
        """
    )

    # ── Contrôle des étapes ───────────────────────────────────────────────────
    grp_skip = parser.add_argument_group("Contrôle des étapes")
    grp_skip.add_argument("--skip-extract",  action="store_true",
                          help="Passe l'étape 1 (timestamps déjà extraits)")
    grp_skip.add_argument("--skip-ppk",      action="store_true",
                          help="Passe l'étape 2 (.pos déjà disponibles)")
    grp_skip.add_argument("--skip-sync",     action="store_true",
                          help="Passe l'étape 3 (synchronized_*.csv déjà générés)")
    grp_skip.add_argument("--no-run",        action="store_true",
                          help="Étape 4 : génère les jobs mais sans reconstruction 3D")
    grp_skip.add_argument("--force-extract", action="store_true",
                          help="Force la ré-extraction des timestamps")
    grp_skip.add_argument("--force-sync",    action="store_true",
                          help="Force la re-synchronisation GPS/frames")

    # ── Paramètres PPK ────────────────────────────────────────────────────────
    grp_ppk = parser.add_argument_group("PPK")
    grp_ppk.add_argument("--acronyme", default=None,
                         help="Sigle de la station RGP (ex: BXME)")
    grp_ppk.add_argument("--no-rgp",   action="store_true",
                         help="Ne pas télécharger les données RGP IGN")
    grp_ppk.add_argument("--verbose", "-v", action="store_true",
                         help="Afficher les sorties complètes des sous-processus")

    # ── Paramètres synchronisation ────────────────────────────────────────────
    grp_sync = parser.add_argument_group("Synchronisation GPS")
    grp_sync.add_argument("--max-gap", type=float, default=2.0,
                          help="Gap max (s) pour marquer une frame 'reliable' (défaut : 2.0)")
    grp_sync.add_argument("--quality", type=int, nargs="+", default=None,
                          help="Valeurs Q GPS : 1=fixe 2=float 5=seul (défaut : toutes)")

    # ── Paramètres pieds / 3D ─────────────────────────────────────────────────
    grp_pieds = parser.add_argument_group("Pieds & Reconstruction 3D")
    # Relire le JSON ici pour que le défaut --radius reflète le JSON actuel
    _rt = _get_runtime_cfg()
    _current_radius = _rt["params"]["default_radius"]
    grp_pieds.add_argument("--radius", type=float,
                           default=_current_radius,
                           help=f"Demi-fenêtre Y_L93 en mètres "
                                f"(défaut depuis config : {_current_radius} m)")
    grp_pieds.add_argument("--pass-select", choices=["1", "2", "both"], default="both",
                           help="Passages : aller (1), retour (2), les deux (both, défaut)")
    grp_pieds.add_argument("--pieds", type=int, nargs="+", default=None,
                           help="Numéros de pieds à traiter (défaut : tous)")
    
    #------------simulation 
    grp_pieds.add_argument("--simulate", action="store_true",
                       help="Ouvre la simulation visuelle du scan en parallèle de l'étape 4")
    grp_pieds.add_argument("--sim-speed", type=float, default=5.0,
                       help="Vitesse de la simulation (défaut : x5)")

    # ── Comportement en cas d'erreur ──────────────────────────────────────────
    parser.add_argument("--continue-on-error", action="store_true",
                        help="Continue même si une étape a des erreurs partielles")

    args = parser.parse_args()

    # ── Bannière d'accueil ────────────────────────────────────────────────────
    banner("LAUNCH_PIPELINE  —  Pipeline complet Viticulture 3D")
    print(f"  Projet      : {PROJECT_ROOT}")
    print(f"  Config      : pipeline_config.json")
    print(f"  Étapes      : "
          f"{'[SKIP] ' if args.skip_extract else ''}1.Extract  "
          f"{'[SKIP] ' if args.skip_ppk     else ''}2.PPK  "
          f"{'[SKIP] ' if args.skip_sync    else ''}3.Sync  "
          f"{'[--no-run] ' if args.no_run   else ''}4.Pieds3D")
    print(f"  Rayon pieds : ±{args.radius} m  |  Passes : {args.pass_select}")
    if args.pieds:
        print(f"  Pieds ciblés: {args.pieds}")

    t_global_start = time.time()

    preflight_check(args)

    if args.skip_extract:
        warn("Étape 1 passée (--skip-extract).")
    else:
        run_extract_timestamps(args)

    if args.skip_ppk:
        warn("Étape 2 passée (--skip-ppk).")
    else:
        run_ppk_pipeline(args)

    if args.skip_sync:
        warn("Étape 3 passée (--skip-sync).")
    else:
        run_synchronisation(args)

    # La simulation visuelle est optionnelle et ne doit pas conditionner
    # l'étape 4 — BUG CORRIGÉ : run_pied_jobs() était auparavant imbriqué
    # dans `if args.simulate:`, donc l'étape 4 (assignation pieds +
    # reconstruction 3D) était silencieusement sautée dès que --simulate
    # n'était pas passé en argument.
    if args.simulate:
        sim_script = PROJECT_ROOT / "simulate_robot_scan.py"
        if sim_script.exists():
            info("Simulation visuelle lancée en parallèle...")
            subprocess.Popen([sys.executable, str(sim_script), "--speed", str(args.sim_speed)])
        else:
            warn("simulate_robot_scan.py introuvable — simulation ignorée.")

    run_pied_jobs(args)

    elapsed = time.time() - t_global_start
    h, rem  = divmod(int(elapsed), 3600)
    m, s    = divmod(rem, 60)
    banner(f"PIPELINE TERMINÉ  —  Durée totale : {h:02d}h {m:02d}m {s:02d}s")
    _rt_final = _get_runtime_cfg()
    print(f"""
  Sorties :
    frame_timestamps.csv  → {TIMESAMPLE_OUT}
    fichiers .pos PPK     → {PPK_POS_DIR}
    synchronized_*.csv    → {SYN_OUTPUT_DIR}
    pied_jobs_summary.csv → {PROJECT_ROOT / _rt_final["paths"]["pied_jobs_summary"]}
    nuages 3D finaux      → {FINAL_OUT}
    """)


if __name__ == "__main__":
    main()