import json
import os
import glob
import csv
from datetime import datetime
import pyrealsense2 as rs

def batch_extract_timestamps(input_folder, output_base_folder):
    # 1. Chercher tous les fichiers .bag dans le dossier d'entrée
    # Chercher .bag ET .db3 (pyrealsense2 >= 2.56 utilise .db3)
    bag_files = (glob.glob(os.path.join(input_folder, "*.bag")) +
                 glob.glob(os.path.join(input_folder, "*.db3")))
    
    if not bag_files:
        print(f" Aucun fichier .bag trouvé dans : {input_folder}")
        return
        
    print(f" {len(bag_files)} fichier(s) .bag trouvé(s). Début de l'extraction par lots...\n")
    
    # 2. Boucle sur chaque fichier .bag trouvé
    for bag_path in bag_files:
        nom_fichier = os.path.basename(bag_path)
        print(f"Traitement en cours : {nom_fichier}")
        
        # Configuration du pipeline RealSense
        pipeline = rs.pipeline()
        config = rs.config()
        rs.config.enable_device_from_file(config, bag_path, repeat_playback=False)
        
        try:
            profile = pipeline.start(config)
            
            # Attendre la première frame pour avoir la date d'acquisition
            frames = pipeline.wait_for_frames()
            first_timestamp_ms = frames.get_timestamp()
            
            # Convertir le timestamp en Date lisible (Format: JJ-MM-AAAA)
            date_obj = datetime.fromtimestamp(first_timestamp_ms / 1000.0)
            date_str = date_obj.strftime("%d-%m-%Y")
            
            # 3. Créer un dossier de sortie unique avec cette date
            dossier_sortie_specifique = os.path.join(output_base_folder, f"data_{date_str}")
            os.makedirs(dossier_sortie_specifique, exist_ok=True)
            
            # Nom du fichier CSV strictement fixé à "frame_timestamps.csv"
            csv_path = os.path.join(dossier_sortie_specifique, "frame_timestamps.csv")
            
            print(f"  -> Création du dossier : data_{date_str}")
            
            # 4. Extraire toutes les frames dans le CSV
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Frame_Number", "Timestamp_ms"])
                
                # On écrit la première frame (déjà lue)
                writer.writerow([0, first_timestamp_ms])
                frame_count = 1
                
                # Boucle pour lire le reste du fichier
                while True:
                    try:
                        frames = pipeline.wait_for_frames()
                        timestamp = frames.get_timestamp()
                        writer.writerow([frame_count, timestamp])
                        frame_count += 1
                    except RuntimeError:
                        # Fin du fichier .bag atteinte
                        break
                        
            print(f"  -> Terminé. {frame_count} frames extraites dans frame_timestamps.csv.\n")
            
        except Exception as e:
            print(f"  ->  Erreur avec le fichier {nom_fichier} : {e}\n")
            
        finally:
            pipeline.stop()

    print(" Traitement par lots (Batch) complètement terminé !")

# ==========================================
# CONFIGURATION DES DOSSIERS VIA JSON
# ==========================================
def load_paths_from_config(config_path="pipeline_config.json"):
    """
    Lit pipeline_config.json et retourne (dossier_entree, dossier_sortie).
    Compatible avec la structure centralisée du projet.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_config_path = os.path.join(base_dir, config_path)

    with open(full_config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Racine du projet = dossier où se trouve ce script
    project_root = base_dir

    paths        = config.get("paths", {})
    bag_dir      = paths.get("bag_input_dir",    "realsense_data")
    extract_out  = paths.get("extraction_output", "Extraction_Output")

    dossier_entree = os.path.join(project_root, bag_dir)
    # Les timestamps vont dans Extraction_Output/Timesample_files/
    dossier_sortie = os.path.join(project_root, extract_out, "Timesample_files")

    return dossier_entree, dossier_sortie

if __name__ == "__main__":
    try:
        DOSSIER_ENTREE, DOSSIER_SORTIE = load_paths_from_config()
        # Sécurité : Créer le dossier de sortie s'il n'existe pas
        os.makedirs(DOSSIER_SORTIE, exist_ok=True)
        batch_extract_timestamps(DOSSIER_ENTREE, DOSSIER_SORTIE)
    except FileNotFoundError:
        print("❌ Erreur : Le fichier pipeline_config.json est introuvable.")