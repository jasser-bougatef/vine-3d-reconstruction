import os
import time
import requests
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.keys import Keys
import json
import numpy as np
import re

# Chemin absolu vers config.json, même si le script est appelé depuis un autre dossier
_SCRIPT_DIR = Path(__file__).resolve().parent
with open(_SCRIPT_DIR / "config.json") as f:
    config = json.load(f)
    
DATE = config["ppk"]["date"]
START_TIME = config["ppk"]["start_time"]
END_TIME = config["ppk"]["end_time"]
ACRONYME_CIBLE = config["ppk"]["acronyme"]


OUTPUT_FILE = f"data_{ACRONYME_CIBLE}_{DATE.replace('/', '-')}_{START_TIME}_{END_TIME}.zip"
DESTINATION_FOLDER = config["gpslogger"]["DESTINATION_FOLDER"]
URL = "https://rgp.ign.fr/DONNEES/diffusion/"
SAVE_PATH = os.path.join(DESTINATION_FOLDER, OUTPUT_FILE)


# === OUTILS GÉNÉRIQUES ===

def step(description, func, *args, **kwargs):
    print(f"\n➡️ {description}...")
    try:
        func(*args, **kwargs)
        print(f"✅ {description} terminée.")
    except Exception as e:
        print(f"❌ Erreur dans '{description}': {e}")
        driver.quit()
        exit(1)

def ensure_folder(path):
    os.makedirs(path, exist_ok=True)

def setup_driver():
    options = Options()
    options.add_argument("--headless=new")  # Active ce mode pour exécuter sans interface
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def download_zip(url, path):
    print(f"\n⬇️ Téléchargement en cours : {url}")
    response = requests.get(url, stream=True)
    if response.status_code == 200:
        with open(path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"✅ Fichier téléchargé : {path}")
    else:
        print(f"❌ Échec du téléchargement. Code HTTP : {response.status_code}")


# === GESTION DU SLIDER ===


def to_percent(hhmm):
    h, m = map(int, hhmm.split(":"))
    return round(((h * 60 + m) / 1440) * 100, 2)


def get_hour_minutes_from_text(text):
    match = re.match(r"de (\d+)h(?:([0-9]{1,2}))?\s*à\s*(\d+)h(?:([0-9]{1,2}))?", text)
    if match:
        h_start, m_start, h_end, m_end = match.groups()
        # Set minutes to 0 if they are None
        m_start = int(m_start) if m_start else 0
        m_end = int(m_end) if m_end else 0
        h_start, h_end = int(h_start), int(h_end)
        return h_start, m_start, h_end, m_end

def drag_slider_handle(driver, handle_start,handle_end, target_time_start,target_time_end):
    on=True
    plage_horaire=driver.find_elements(By.ID, "plage_horaire")[0]
    actual_time_range = plage_horaire.text.strip()  # example: "de 3h45 à 13h_start5"
    h_start, m_start, h_end, m_end= get_hour_minutes_from_text(actual_time_range)
    
    

    # Convert times to number of 15-minute intervals from 00:00
    actual_time_start_intervals = (h_start * 60 + m_start) // 15
    actual_time_end_intervals = (h_end * 60 + m_end) // 15

    h_start, m_start = map(int, target_time_start.split(":"))
    target_time_start_intervals=(h_start * 60 + m_start) // 15

    h_end, m_end = map(int, target_time_end.split(":"))
    target_time_end_intervals=(h_end * 60 + m_end) // 15

    diff_start=target_time_start_intervals - actual_time_start_intervals
    diff_end=target_time_end_intervals - actual_time_end_intervals

    for i in range(np.abs(diff_start)):
        if diff_start>0:
            handle_start.send_keys(Keys.ARROW_RIGHT)
        else:
            handle_start.send_keys(Keys.ARROW_LEFT)
    for i in range(np.abs(diff_end)):
        if diff_end>0:
            handle_end.send_keys(Keys.ARROW_RIGHT)
        else:
            handle_end.send_keys(Keys.ARROW_LEFT)
    time.sleep(1)
    plage_horaire=driver.find_elements(By.ID, "plage_horaire")[0]
    actual_time_range = plage_horaire.text.strip()  # example: "de 3h45 à 13h_start5"
    h_start, m_start, h_end, m_end= get_hour_minutes_from_text(actual_time_range)
    final_time_start=f"{h_start:02d}:{m_start:02d}"
    final_time_end=f"{h_end:02d}:{m_end:02d}"
    print(f"final_time_start={final_time_start}")
    print(f"final_time_end={final_time_end}")
    return final_time_start,final_time_end

def set_time_slider(driver, start_time, end_time):
    global START_TIME, END_TIME, OUTPUT_FILE, SAVE_PATH
    start_percent = to_percent(start_time)
    end_percent = to_percent(end_time)

    WebDriverWait(driver, 10).until(
        EC.presence_of_all_elements_located((By.CLASS_NAME, "ui-slider-handle"))
    )

    handles = driver.find_elements(By.CLASS_NAME, "ui-slider-handle")
    if len(handles) >= 2:
        START_TIME, END_TIME=drag_slider_handle(driver, handles[0],handles[1], start_time, end_time)
        OUTPUT_FILE = f"data_{ACRONYME_CIBLE}_{DATE.replace('/', '-')}_{START_TIME}_{END_TIME}.zip"
        SAVE_PATH = os.path.join(DESTINATION_FOLDER, OUTPUT_FILE)
        print(f"SAVE_PATH={SAVE_PATH}")
    else:
        raise Exception("Poignées du slider non trouvées.")



# === ÉTAPES D'ACTION ===

def set_date(driver, wait, date):
    wait.until(EC.presence_of_element_located((By.ID, "jour_debut"))).clear()
    driver.find_element(By.ID, "jour_debut").send_keys(date)
    driver.find_element(By.ID, "jour_fin").clear()
    driver.find_element(By.ID, "jour_fin").send_keys(date)
    print(f"📅 Date définie : {date}")

def cocher_options(driver):
    for opt in ["GLONASS", "C2", "5s", "2-11"]:
        checkbox = driver.find_element(By.ID, opt)
        if not checkbox.is_selected():
            checkbox.click()
        print(f"☑️ Option cochée : {opt}")

def valider_filtres(driver, wait):
    btn = wait.until(EC.element_to_be_clickable((By.ID, "maj_button")))
    btn.click()
    wait.until(EC.invisibility_of_element_located((By.ID, "chargementAffichage")))
    print("📊 Filtres appliqués.")

def selectionner_station(driver, wait, acronyme):
    wait.until(EC.element_to_be_clickable((By.ID, "Mode_liste"))).click()
    acronymes = driver.find_elements(By.CLASS_NAME, "rowAcronyme")
    for a in acronymes:
        if a.text == acronyme:
            row = a.find_element(By.XPATH, "./ancestor::tr")
            checkbox = row.find_element(By.XPATH, ".//input[@type='checkbox']")
            if not checkbox.is_selected():
                checkbox.click()
            print(f"📍 Station sélectionnée : {acronyme}")
            return
    raise Exception(f"Station {acronyme} non trouvée.")

def ajouter_et_telecharger(driver, wait):
    wait.until(EC.element_to_be_clickable((By.ID, "Ajout_Panier"))).click()
    print("🛒 Station ajoutée au panier.")
    time.sleep(1)

    wait.until(EC.element_to_be_clickable((By.ID, "Telecharger_Panier"))).click()
    print("📦 Téléchargement en préparation (attente génération ZIP côté serveur)...")

    # Le serveur peut mettre jusqu'à 60s à générer le ZIP — on attend plus longtemps
    long_wait = WebDriverWait(driver, 90)

    try:
        # Tentative 1 : attendre la dialog normalement
        dialog = long_wait.until(EC.visibility_of_element_located((By.ID, "dialogTelechargementOK-confirm")))
    except Exception:
        # Tentative 2 : la dialog existe mais n'est pas "visible" selon Selenium (headless quirk)
        # → on force sa visibilité via JS et on réessaie
        print("⚠️  Dialog non visible, tentative JS...")
        try:
            driver.execute_script(
                "var d = document.getElementById('dialogTelechargementOK-confirm');"
                "if(d){ d.style.display='block'; d.style.visibility='visible'; }"
            )
            time.sleep(2)
            dialog = driver.find_element(By.ID, "dialogTelechargementOK-confirm")
        except Exception as e2:
            # Sauvegarde screenshot + HTML pour debug
            debug_dir = _SCRIPT_DIR / "debug_selenium"
            debug_dir.mkdir(exist_ok=True)
            driver.save_screenshot(str(debug_dir / "timeout_screenshot.png"))
            (debug_dir / "timeout_page.html").write_text(driver.page_source, encoding="utf-8")
            print(f"📸 Screenshot et HTML sauvegardés dans : {debug_dir}")
            raise

    # Chercher le lien de téléchargement (texte FR ou EN)
    try:
        link = dialog.find_element(By.XPATH, ".//a[contains(text(), 'Télécharger vos Données')]")
    except Exception:
        # Fallback : n'importe quel lien dans la dialog
        link = dialog.find_element(By.XPATH, ".//a[@href]")

    href = link.get_attribute("href")
    print(f"🔗 Lien ZIP : {href}")
    return href


# === PROGRAMME PRINCIPAL ===

def main():
    ensure_folder(DESTINATION_FOLDER)
    global driver
    driver = setup_driver()
    wait = WebDriverWait(driver, 60)   # 60s au lieu de 30s

    try:
        driver.get(URL)
        print("🌐 Page chargée : accès à RGP.IGN")
        step("Définir les heures", set_time_slider, driver, START_TIME, END_TIME)
        step("Définir les dates", set_date, driver, wait, DATE)
        step("Cocher les options", cocher_options, driver)
        step("Valider les filtres", valider_filtres, driver, wait)
        time.sleep(3)   # laisser la liste des stations se recharger
        step("Sélectionner la station", selectionner_station, driver, wait, ACRONYME_CIBLE)
        print("\n📥 Téléchargement en préparation...")
        url = ajouter_et_telecharger(driver, wait)
        download_zip(url, SAVE_PATH)

    finally:
        driver.quit()
        print("🧹 Navigateur fermé.")

if __name__ == "__main__":
    main()