# utilities.py
import json
from datetime import datetime, date, timedelta
import re
import zipfile
import os
import shutil

def round_down_to_quarter(dt: datetime) -> datetime:
    minute = (dt.minute // 15) * 15
    return dt.replace(minute=minute, second=0, microsecond=0)

def round_up_to_quarter(dt: datetime) -> datetime:
    minute = ((dt.minute + 14) // 15) * 15
    if minute == 60:
        dt = dt.replace(hour=dt.hour, minute=0) + timedelta(hours=1)
    else:
        dt = dt.replace(minute=minute)
    return dt.replace(second=0, microsecond=0)

def update_config_json(config_path: str, start_time: datetime, end_time: datetime, acquisition_date: date | None):
    """
    Met à jour le fichier config.json avec la date, l'heure de début et de fin,
    en arrondissant aux quarts d'heure et en imposant une durée minimale d'une heure.
    """
    # Round times
    start_time_rounded = round_down_to_quarter(start_time)
    end_time_rounded = round_up_to_quarter(end_time)

    # Ensure at least 1 hour of interval
    if end_time_rounded <= start_time_rounded + timedelta(minutes=59):
        end_time_rounded = start_time_rounded + timedelta(hours=1)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        config = {"ppk": {}, "gpslogger": {}}

    # Vérifier que la clé 'ppk' existe
    if "ppk" not in config:
        config["ppk"] = {}

    config["ppk"]['start_time'] = start_time_rounded.strftime("%H:%M")
    config["ppk"]['end_time'] = end_time_rounded.strftime("%H:%M")

    if acquisition_date:
        config["ppk"]['date'] = acquisition_date.strftime("%d/%m/%Y")

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    print("✅ config.json updated with rounded times.")



def extract_gga_timestamps(file) -> tuple[datetime, datetime, date | None]:
    data = file.read()
    text = data.decode(errors="ignore")

    # Recherche des trames GGA
    gga_sentences = re.findall(r"\$(?:G[NP]GGA),[^\r\n]*", text)
    timestamps = []

    # Recherche des heures dans les trames GGA
    for sentence in gga_sentences:
        parts = sentence.split(",")
        if len(parts) > 1:
            try:
                time_str = parts[1]  # Format: hhmmss.ss
                hour = int(time_str[0:2])
                minute = int(time_str[2:4])
                second = int(time_str[4:6])
                timestamps.append((hour, minute, second))
            except:
                continue

    # Recherche de la date dans une trame RMC
    rmc_sentences = re.findall(r"\$(?:G[NP]RMC),[^\r\n]*", text)
    acquisition_date = None
    for rmc in rmc_sentences:
        parts = rmc.split(",")
        if len(parts) > 9 and parts[9]:
            try:
                raw_date = parts[9]  # Format: ddmmyy
                day = int(raw_date[0:2])
                month = int(raw_date[2:4])
                year = int(raw_date[4:6]) + 2000
                acquisition_date = date(year, month, day)
                break
            except:
                continue

    if not timestamps:
        return None, None, None

    h1, m1, s1 = timestamps[0]
    h2, m2, s2 = timestamps[-1]

    # Création des objets datetime
    if acquisition_date:
        start_dt = datetime(acquisition_date.year, acquisition_date.month, acquisition_date.day, h1, m1, s1)
        end_dt = datetime(acquisition_date.year, acquisition_date.month, acquisition_date.day, h2, m2, s2)
    else:
        today = date.today()
        start_dt = datetime.combine(today, datetime.min.time()).replace(hour=h1, minute=m1, second=s1)
        end_dt = datetime.combine(today, datetime.min.time()).replace(hour=h2, minute=m2, second=s2)

    return start_dt, end_dt, acquisition_date



def extraire_et_regrouper(archive_zip: str, dossier_correction: str = "correction"):
    # 1. Créer un dossier temporaire pour l'extraction
    dossier_temp = "__temp_extract"
    os.makedirs(dossier_temp, exist_ok=True)  # crée le dossier s'il n'existe pas :contentReference[oaicite:0]{index=0}

    # 2. Ouvrir l'archive et tout extraire dans le dossier temporaire
    with zipfile.ZipFile(archive_zip, 'r') as zip_ref:
        zip_ref.extractall(dossier_temp)       # utilise extractall du module zipfile :contentReference[oaicite:1]{index=1}

    # 3. Créer le dossier de correction
    os.makedirs(dossier_correction, exist_ok=True)  # idem, exist_ok évite l'erreur si le dossier existe :contentReference[oaicite:2]{index=2}

    # 4. Parcourir le dossier temporaire et déplacer chaque fichier dans 'correction'
    for root, _, files in os.walk(dossier_temp):
        for fichier in files:
            chemin_src = os.path.join(root, fichier)
            chemin_dest = os.path.join(dossier_correction, fichier)
            shutil.move(chemin_src, chemin_dest)  # déplace le fichier :contentReference[oaicite:3]{index=3}

    # 5. Nettoyer : supprimer le dossier temporaire et son contenu
    shutil.rmtree(dossier_temp)  # supprime récursivement le dossier temporaire :contentReference[oaicite:4]{index=4}
