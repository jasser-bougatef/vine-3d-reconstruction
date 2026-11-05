import json
import os
import glob
import csv
from datetime import datetime
import pyrealsense2 as rs

def batch_extract_timestamps(input_folder, output_base_folder):
    chemin_recherche = os.path.join(input_folder, "*.bag")
    bag_files = glob.glob(chemin_recherche)
    
    if not bag_files:
        print(f" Aucun fichier .bag trouvé dans : {input_folder}")
        return
        
    print(f" {len(bag_files)} fichier(s) .bag trouvé(s). Début de l'extraction par lots...\n")
    
    for bag_path in bag_files:
        nom_fichier = os.path.basename(bag_path)
        print(f"Traitement en cours : {nom_fichier}")
        
        pipeline = rs.pipeline()
        config = rs.config()
        rs.config.enable_device_from_file(config, bag_path, repeat_playback=False)
        
        try:
            profile = pipeline.start(config)
            # Lecture non bloquante en mode fichier (best-effort ; certaines
            # versions de pyrealsense2 exposent l'API différemment, donc on
            # ne bloque jamais le script si ça échoue).
            try:
                dev = profile.get_device()
                if dev.is_playback():
                    dev.as_playback().set_real_time(False)
            except Exception:
                pass

            frames = pipeline.wait_for_frames()
            first_timestamp_ms = frames.get_timestamp()
            
            date_obj = datetime.fromtimestamp(first_timestamp_ms / 1000.0)
            date_str = date_obj.strftime("%d-%m-%Y_%H-%M-%S")
            
            dossier_session = os.path.join(output_base_folder, f"data_{date_str}")
            os.makedirs(dossier_session, exist_ok=True)
            
            fichier_csv = os.path.join(dossier_session, "frame_timestamps.csv")
            
            with open(fichier_csv, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['frame_number', 'timestamp_ms'])

                
                frame_count = 0
                first_frame_written = False

                # La toute première frame (déjà lue ci-dessus pour la date)
                # doit aussi être comptée si elle est valide (depth+color).
                if frames.get_depth_frame() and frames.get_color_frame():
                    frame_count = 1
                    writer.writerow([frame_count, first_timestamp_ms])
                    first_frame_written = True

                while True:
                    try:
                        frames = pipeline.wait_for_frames(5000)
                    except RuntimeError:
                        # Fin du fichier .bag atteinte (ou timeout)
                        break

                    # Même critère de validité que auto_extractor.cpp :
                    # la frame ne compte QUE si depth ET color sont présents.
                    if not frames.get_depth_frame() or not frames.get_color_frame():
                        continue

                    frame_count += 1
                    timestamp = frames.get_timestamp()
                    writer.writerow([frame_count, timestamp])

            print(f"  -> Terminé. {frame_count} frames (numérotation séquentielle, "
                  f"alignée sur auto_extractor.cpp) extraites dans frame_timestamps.csv.\n")
            
        except Exception as e:
            print(f"  ->  Erreur avec le fichier {nom_fichier} : {e}\n")
            
        finally:
            pipeline.stop()

    print(" Traitement par lots (Batch) complètement terminé !")

def load_paths_from_config(config_path="pipeline_config.json"):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    full_config_path = os.path.join(base_dir, config_path)
    
    with open(full_config_path, "r") as f:
        config = json.load(f)
        
    project_root = config["project_root"]
    
    dossier_entree = os.path.join(project_root, config["paths"]["bag_input_dir"])
    dossier_sortie = os.path.join(project_root, config["paths"]["timesample_files"])
    
    return dossier_entree, dossier_sortie

if __name__ == "__main__":
    try:
        DOSSIER_ENTREE, DOSSIER_SORTIE = load_paths_from_config()
        os.makedirs(DOSSIER_SORTIE, exist_ok=True)
        batch_extract_timestamps(DOSSIER_ENTREE, DOSSIER_SORTIE)
    except Exception as e:
        print(f"Erreur : {e}")