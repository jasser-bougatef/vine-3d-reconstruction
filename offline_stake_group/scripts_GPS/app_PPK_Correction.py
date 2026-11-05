from __future__ import annotations
import re, shutil, subprocess, json
from pathlib import Path
from datetime import datetime
import streamlit as st
from utilities import update_config_json, extract_gga_timestamps, extraire_et_regrouper

# ───────────────────────── Config globale ──────────────────────────
BASE_UPLOADS_DIR = Path("uploads")
BASE_UPLOADS_DIR.mkdir(exist_ok=True)

BASE_DIR   = Path(__file__).resolve().parent
CORRECT_SH = BASE_DIR / "correct_new.sh"
GPSYNC_PY  = BASE_DIR / "gpssync.py"
RGP_DL_PY  = BASE_DIR / "rgp_station_downloader_from_config.py"
CONFIG     = BASE_DIR / "rtkpost.conf"

for p in (CORRECT_SH, GPSYNC_PY, RGP_DL_PY):
    if not p.exists():
        st.error(f"Script manquant : {p}")
        st.stop()

# ─────────────────────── Helpers génériques ────────────────────────
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(txt: str) -> str:
    return ANSI_RE.sub("", txt)

def run_cmd(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return p.returncode, strip_ansi(p.stdout), strip_ansi(p.stderr)

def parse_pos_from_stdout(stdout: str) -> Path | None:
    m = re.search(r"Result saved to:\s*([^\s]+\.pos)", stdout)
    return Path(m.group(1)).resolve() if m else None

def find_pos_file(root: Path) -> Path | None:
    for p in root.rglob("*.pos"):
        if not p.name.endswith("_events.pos"):
            return p.resolve()
    return None

def create_session_dir(ubx_name: str) -> dict:
    sess_id = f"session_{ubx_name.replace('trk_', '').replace('.ubx', '')}"
    sess = BASE_UPLOADS_DIR / sess_id
    paths = {"session": sess, "correction": sess / "correction", "output": sess / "output"}
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths

# ────────────────────────── UI Streamlit ───────────────────────────
st.set_page_config(page_title="PPK Correction with Centipede", page_icon="🛰️", layout="wide")

# State
ss = st.session_state
ss.setdefault("ppk_started", False)
ss.setdefault("ppk_done", False)       
ss.setdefault("gpssync_started", False)
ss.setdefault("gpssync_done", False)
ss.setdefault("pos_file", None)
ss.setdefault("session_paths", {})
ss.setdefault("stdout", "")
ss.setdefault("stderr", "")
ss.setdefault("nav_pattern", "forward_turn_forward")

# ─────────── Sidebar : Stepper ───────────
def step_badge(label: str, state: str) -> str:
    # state: todo|run|ok
    color = {"todo": "#9aa0a6", "run": "#1e88e5", "ok": "#2e7d32"}[state]
    emoji = {"todo": "○", "run": "◔", "ok": "●"}[state]
    return f"""
<div style="display:flex;align-items:center;margin:6px 0;">
  <div style="width:22px;height:22px;border-radius:50%;background:{color};color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700">{emoji}</div>
  <div style="margin-left:10px;color:{color};font-weight:600">{label}</div>
</div>
"""


# gris (“todo”) au repos
# bleu (“run”) dès que l’étape a commencé
# vert (“ok”) quand l’étape est terminée
sidebar_ph = st.sidebar.empty()

def render_stepper():
    # calcule les états à partir de ss.*
    if ss.get("ppk_done"):
        ppk_state = "ok"
    elif ss.get("ppk_started"):
        ppk_state = "run"
    else:
        ppk_state = "todo"

    if ss.get("gpssync_done"):
        sync_state = "ok"
    elif ss.get("gpssync_started"):
        sync_state = "run"
    else:
        sync_state = "todo" if not ss.get("ppk_done") else "todo"

    with sidebar_ph.container():
        st.markdown("## ⚙️ Étapes")
        st.markdown(step_badge("1. PPK", ppk_state), unsafe_allow_html=True)
        st.markdown(step_badge("2. Synchronisation (gpssync)", sync_state), unsafe_allow_html=True)




with st.sidebar:
    render_stepper()
    st.markdown("---")
    if st.button("🧹 Réinitialiser la session"):
        try:
            if ss.get("session_paths", {}).get("session"):
                shutil.rmtree(ss.session_paths["session"], ignore_errors=True)
        except Exception:
            pass
        for k in list(ss.keys()):
            del ss[k]
        st.rerun()

# ─────────── En-tête + résumé ───────────
st.title("🛰️ PPK Correction (Centipede)")
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    c1.metric("État PPK", "Terminé" if ss.get("ppk_done") else "En attente")
    c2.metric("Fichier .pos", Path(ss.pos_file).name if ss.get("pos_file") else "—")
    c3.metric("Session", Path(ss.session_paths["session"]).name if ss.get("session_paths") else "—")

# ───────────────────────── Étape 1 : PPK ───────────────────────────
tab1, tab2 = st.tabs(["① PPK", "② Synchronisation"])

with tab1:
    ubx_upload = st.file_uploader("🗂️ Chargez votre fichier `.ubx`", type="ubx")

    if ubx_upload and not ss.ppk_done:
        if not ss.ppk_started:
            ss.ppk_started = True
            st.rerun()
        paths = create_session_dir(ubx_upload.name)
        ss.session_paths = paths

        ubx_path = paths["session"] / ubx_upload.name
        ubx_path.write_bytes(ubx_upload.getbuffer())
        st.success("✅ UBX enregistré.")

        start_dt, end_dt, acq_date = extract_gga_timestamps(ubx_upload)
        if not (start_dt and end_dt):
            st.error("❌ Impossible d’extraire les trames GGA.")
            st.stop()
        st.write(f"Heure début : `{start_dt.time()}` → fin : `{end_dt.time()}`")
        update_config_json("config.json", start_dt, end_dt, acq_date)

        with st.spinner("⬇️ Téléchargement des données RGP…"):
            rc, out, err = run_cmd(["python3", str(RGP_DL_PY)])
        if rc:
            st.error("❌ Téléchargement RGP échoué :\n" + err)
            st.stop()

        zip_files = sorted(Path(".").glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zip_files:
            st.error("❌ Archive ZIP introuvable.")
            st.stop()
        zip_path = zip_files[0]
        extraire_et_regrouper(str(zip_path), str(paths["correction"]))
        zip_path.unlink()
        st.success("✅ Données RGP extraites (archive supprimée).")

        shutil.copy(CORRECT_SH, paths["correction"])
        shutil.copy(CONFIG, paths["correction"])

        base_files = list(paths["correction"].glob("*.??o"))
        if not base_files:
            st.error("❌ Aucune base *.??o trouvée.")
            st.stop()

        shutil.copy(ubx_path, paths["correction"] / ubx_path.name)

        rc, out, err = run_cmd(
            ["bash", "correct_new.sh", ubx_path.name, base_files[0].name],
            cwd=paths["correction"]
        )
        ss.stdout, ss.stderr = out, err
        if rc:
            st.error("❌ correct_new.sh a échoué :\n" + err)
            st.stop()

        pos_path = parse_pos_from_stdout(out)
        if not (pos_path and pos_path.exists()):
            pos_path = find_pos_file(paths["session"])
        if not (pos_path and pos_path.exists()):
            st.error("❌ Fichier .pos introuvable.")
            st.stop()

        final_pos = paths["output"] / pos_path.name
        shutil.move(pos_path, final_pos)

        ss.pos_file, ss.ppk_done = str(final_pos), True
        st.success(f"🎯 Résultat .pos : `{final_pos.name}`")
        st.toast("PPK terminé ✅")

        ss.gpssync_started = True
        ss.gpssync_done = False
        render_stepper() 


# ─────────────────── Étape 2 : Synchronisation caméra ──────────────
with tab2:
    if not (ss.get("pos_file") and ss.get("session_paths")):
        st.info("Charge et corrige d'abord un fichier UBX dans l’onglet **① PPK**.")
    else:
        st.header("🚜 Appariement caméra ↔ GPS (gpssync)")

        with st.expander("ℹ️ Pourquoi ce choix impacte la correction"):
            st.markdown(
                """
                - **U (demi-tour, toujours marche avant)** : heading ≈ +180° en bout ; on compose `(x_arm, y_arm)` avec le heading courant.  
                - **Retour en marche arrière** : heading quasi stable mais **sens inversé** → **inverser `x_arm`** au retour.
                """
            )

        # mode = st.radio(
        #     "Schéma de navigation du robot :",
        #     ["Aller-retour (U : demi-tour, toujours marche avant)",
        #      "Aller-retour (retour en marche arrière)"]
        # )
        # nav_pattern = "forward_and_reverse" if "marche arrière" in mode else "forward_turn_forward"
        # ss.nav_pattern = nav_pattern

        # need_arm = st.checkbox("La caméra n’est pas au point de l’antenne (bras, déport, mât…)", value=True)
        # if need_arm:
        #     x_arm = st.number_input("Décalage bras X (m)", -10.0, 10.0, 0.00, 0.01,
        #                             help="Avant du robot = X > 0 ; arrière = X < 0.")
        #     y_arm = st.number_input("Décalage bras Y (m)", -10.0, 10.0, 0.00, 0.01,
        #                             help="Latéral (gauche/droite).")
        # else:
        #     x_arm, y_arm = 0.0, 0.0
        # après avoir déterminé le mode et nav_pattern
        mode = st.radio(
            "Schéma de navigation du robot :",
            ["Aller-retour (U : demi-tour, toujours marche avant)",
            "Aller-retour (retour en marche arrière)"]
        )
        nav_pattern = "forward_and_reverse" if "marche arrière" in mode else "forward_turn_forward"
        ss.nav_pattern = nav_pattern  # déjà présent dans ton code

        # — Option de déport caméra
        need_arm = st.checkbox("La caméra n’est pas au point de l’antenne (bras, déport, mât…)", value=True)

        # ⚠️ Interdire le déport si retour en marche arrière
        if ss.nav_pattern == "forward_and_reverse" and need_arm:
            st.error("En retour **marche arrière**, les déports caméra sont invalides. "
                    "Décoche l’option ou place la caméra au point de l’antenne.")
            # on neutralise les déports pour éviter des valeurs erronées
            need_arm = False

        if need_arm:
            x_arm = st.number_input("Décalage bras X (m)", -10.0, 10.0, 0.00, 0.01)
            y_arm = st.number_input("Décalage bras Y (m)", -10.0, 10.0, 0.00, 0.01)
        else:
            x_arm, y_arm = 0.0, 0.0
            st.caption("Déports neutralisés (caméra au point de l’antenne).")


        # ——— Paramètres caméras (hors submit pour rerender immédiat)
        st.markdown("### 📷 Fichiers caméra")
        n_cams = int(st.number_input("Nombre de caméras", 1, 12, 2, 1, key="n_cams"))
        same_files_all = st.checkbox("Même nombre de fichiers pour toutes les caméras", value=False,
                                     help="Décoche pour définir un nombre différent par caméra.")
        if same_files_all:
            default_files_per_cam = int(st.number_input("Nombre de fichiers par caméra", 1, 20, 1, 1, key="all_files"))

        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        cam_entries: list[dict] = []

        has_any_upload = any(
            (f is not None) for cam in cam_entries for f in cam["files"]
        )
        if ss.ppk_done and not ss.gpssync_done and not ss.gpssync_started and has_any_upload:
            ss.gpssync_started = True
            render_stepper()


        for i in range(n_cams):
            st.markdown(f"#### 🎥 Caméra {i+1}")
            with st.container(border=True):
                cam_letter = letters[i % len(letters)]
                default_name = f"proxycam{cam_letter}"
                c1, c2 = st.columns([2, 1])
                with c1:
                    cam_name = st.text_input(f"Nom caméra {i+1}", value=default_name, key=f"cam_name_{i}").strip() or default_name
                with c2:
                    if same_files_all:
                        n_files = default_files_per_cam
                        st.caption(f"Fichiers attendus : {n_files}")
                    else:
                        n_files = int(st.number_input(f"Fichiers pour caméra {i+1}", 1, 20, 1, 1, key=f"cam_nfiles_{i}"))

                # Uploaders ordonnés
                ordered_files = []
                if n_files == 1:
                    f = st.file_uploader(f"Fichier .txt caméra {i+1}", type="txt",
                                         accept_multiple_files=False, key=f"cam_file_{i}_1")
                    ordered_files = [f] if f is not None else [None]
                else:
                    st.caption("Un champ par fichier pour garantir l’ordre (1, 2, …).")
                    for k in range(1, n_files + 1):
                        f = st.file_uploader(f"Fichier {k} · caméra {i+1}", type="txt",
                                             accept_multiple_files=False, key=f"cam_file_{i}_{k}")
                        ordered_files.append(f)

                missing = sum(1 for f in ordered_files if f is None)
                if missing:
                    st.warning(f"{missing} fichier(s) manquant(s) sur {n_files}.")

                cam_entries.append({"name": cam_name, "expected": n_files, "files": ordered_files})

        # Ready check
        ready = all(all(f is not None for f in cam["files"]) for cam in cam_entries)
        st.button("🚀 Lancer gpssync", key="launch_gpssync", disabled=not ready)

        if not ready:
            st.info("Complète tous les fichiers attendus pour activer le bouton.")

        # Lancement
        if "launch_gpssync" in st.session_state and st.session_state.launch_gpssync and ready:
            gpssync_abs = GPSYNC_PY.resolve()
            if not gpssync_abs.exists():
                st.error(f"❌ Script gpssync.py introuvable : {gpssync_abs}")
                st.stop()

            out_dir = ss.session_paths["output"]
            all_cam_paths: list[str] = []

            for cam in cam_entries:
                if cam["expected"] == 1:
                    dest = out_dir / f"{cam['name']}.txt"
                    dest.write_bytes(cam["files"][0].getbuffer())
                    all_cam_paths.append(dest.name)
                    st.success(f"{cam['files'][0].name} → {dest.name}")
                else:
                    for k, f in enumerate(cam["files"], start=1):
                        dest = out_dir / f"{cam['name']}_{k}.txt"
                        dest.write_bytes(f.getbuffer())
                        all_cam_paths.append(dest.name)
                        st.success(f"{f.name} → {dest.name}")

            pos_name = Path(ss.pos_file).name
            rc, out, err = run_cmd(
                [
                    "python3", str(gpssync_abs),
                    "--xarm", str(x_arm),
                    "--yarm", str(y_arm),
                    pos_name, *all_cam_paths
                ],
                cwd=out_dir
            )

            ss.stdout, ss.stderr = out, err

            if rc:
                st.error("❌ gpssync a échoué :\n" + err)
            else:
                st.success("✅ gpssync terminé.")
                ss.gpssync_done = True
                render_stepper()

                try:
                    shutil.rmtree(ss.session_paths["correction"])
                    st.info("📁 Dossier 'correction' supprimé après exécution.")
                except Exception as e:
                    st.warning(f"⚠️ Impossible de supprimer le dossier 'correction' : {e}")

                try:
                    meta = {
                        "nav_pattern": ss.nav_pattern,
                        "x_arm": x_arm, "y_arm": y_arm,
                        "pos_file": pos_name,
                        "cam_files": all_cam_paths
                    }
                    (out_dir / "gpssync_config.json").write_text(json.dumps(meta, indent=2))
                    st.download_button("⬇️ gpssync_config.json", data=json.dumps(meta, indent=2),
                                       file_name="gpssync_config.json", mime="application/json")
                except Exception as e:
                    st.warning(f"⚠️ Impossible d’écrire gpssync_config.json : {e}")

                # Proposer les logs
                st.download_button("⬇️ stdout.txt", data=ss.stdout or "", file_name="gpssync_stdout.txt")
                st.download_button("⬇️ stderr.txt", data=ss.stderr or "", file_name="gpssync_stderr.txt")
