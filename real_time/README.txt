================================================================================
                    PIPELINE 3D VIGNE TEMPS REEL
            IMS Bordeaux / Equipe MOTIVE — Stage M1 2026
================================================================================

Reconstruction 3D haute precision de pieds de vigne combinant imagerie RGB-D
(Intel RealSense D435i) et geolocalisation centimetrique (GPS RTK/PPK).

Flux global :
  RealSense (.db3) -> Extracteur C++ -> DBSCAN + ICP -> Webapp / Viewer 3D

--------------------------------------------------------------------------------
1. INSTALLATION RAPIDE
--------------------------------------------------------------------------------

[A] Dependances systeme (Ubuntu) :
    sudo apt update && sudo apt install -y \
        cmake build-essential \
        libopencv-dev libproj-dev proj-bin \
        librealsense2-dev librealsense2-utils libpcl-dev

[B] Dependances Python :
    pip install -r requirements.txt --break-system-packages

[C] Compilation C++ (Extracteur) :
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build --parallel $(nproc)

[D] Initialisation des repertoires :
    mkdir -p realsense_data Extraction_Output final_Output logs webapp


--------------------------------------------------------------------------------
2. LANCEMENT DU PROJET
--------------------------------------------------------------------------------

A. TEST BUREAU (Sans materiel / Simulation) :
   Ouvrez 3 terminaux :
   - Terminal 1 : python3 rt_streamer.py
   - Terminal 2 : python3 main_rt_controller.py --mode manual --sim-ply
   - Terminal 3 : streamlit run app_RT.py --server.port 8501

B. MODE TERRAIN (RealSense + GPS U-blox F9P connectes) :
   - Terminal 1 : python3 rt_streamer.py &
   - Terminal 2 : python3 main_rt_controller.py --mode manual

C. REJOUER UNE CAPTURE EXISTANTE :
   python3 main_rt_controller.py --mode manual --in-bag realsense_data/fichier.db3


--------------------------------------------------------------------------------
3. COMMANDES CLAVIER (MODE MANUEL)
--------------------------------------------------------------------------------

  [ S ]       Demarrer / Arreter l'enregistrement de la passe active
  [ B ]       Basculer entre Passe 1 (aller) et Passe 2 (retour)
  [ R ]       Lancer la reconstruction 3D (Extraction + Filtrage + ICP)
  [ N / -> ]  Passer au pied suivant
  [ P / <- ]  Revenir au pied precedent
  [ Q / Esc ] Quitter le programme

Deroulement type :
  1. Choisir le pied (touches N / P)
  2. Passe 1 : Presser S pour filmer -> S pour arreter
  3. Presser B pour basculer sur la Passe 2
  4. Passe 2 : Presser S pour filmer -> S pour arreter
  5. Presser R pour lancer la reconstruction 3D en arriere-plan
  6. Presser N pour passer au cep suivant


--------------------------------------------------------------------------------
4. INTERFACES & PORTS RESEAU
--------------------------------------------------------------------------------

- Dashboard Live  : http://localhost:8080/webapp/index.html
                    (Suivi FSM, etat du robot, alertes, coordonnees GPS)

- Viewer 3D       : http://localhost:8080/webapp/viewer.html
                    (Visualisation Three.js des nuages de points .ply)

- Streamlit       : http://localhost:8501
                    (Analyse statistique multi-temporelle et suivi de rang)

- Serveur WS      : ws://localhost:8765
                    (Flux telemetrique temps reel)


--------------------------------------------------------------------------------
5. CONFIGURATION UNIQUE (pipeline_config.json)
--------------------------------------------------------------------------------

Tous les reglages sont centralises dans "pipeline_config.json" :
- paths               : Emplacements des dossiers et binaires.
- camera              : Resolution, framerate (FPS), format (.db3).
- extractor_cpp       : Bornes de profondeur (z_min, z_max) et rotations.
- pipeline_3d.filter  : Parametres DBSCAN (eps, min_points) et taille voxel.
- pipeline_3d.reg     : Parametres ICP (fenetre glissante, seuils).
- gps                 : Port serie (/dev/ttyACM0), rayon d'activation pied.


--------------------------------------------------------------------------------
6. ARBORESCENCE DU PROJET
--------------------------------------------------------------------------------

.
|-- pipeline_config.json     # Configuration centrale unique
|-- main_rt_controller.py    # Orchestrateur central (modes: manual, sim, real)
|-- rt_streamer.py           # Serveur WebSocket (8765) + HTTP (8080)
|-- all_pipeline_3D.py       # Traitement du nuage de points (DBSCAN + ICP)
|-- app_RT.py                # Dashboard Streamlit d'analyse
|-- auto_extractor.cpp       # Extracteur haute performance C++
|-- pos_pied.shp             # Shapefile des positions de ceps (Lambert-93)
|-- webapp/                  # Client Web (index.html, viewer.html)
`-- final_Output/            # Nuages de points finaux assembles (.ply)
