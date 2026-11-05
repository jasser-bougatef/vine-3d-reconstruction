PIPELINE DE VITICULTURE DE PRÉCISION — RECONSTRUCTION 3D PAR PIED
PRECISION VITICULTURE PIPELINE  —  PER-VINE 3D RECONSTRUCTION

Version : 2.0  |  Mode : Hors-ligne / Offline

  [FR] -> Section française : lignes  20  à  520
  [EN] -> English section   : lines  525  à  1050

///////////////////////////////////////////////////////////////////////////////////////



                        DOCUMENTATION — VERSION FRANÇAISE



━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TABLE DES MATIÈRES (FR)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1.  Présentation générale du projet
  2.  Architecture du projet
  3.  Prérequis et dépendances
  4.  Structure des fichiers d'entrée
  5.  Description des scripts
  6.  pipeline_config.json — Configuration centralisée  
  7.  Lancement — Pipeline complet automatique
  8.  Lancement — Étape par étape (manuel)
  9.  Application Streamlit (app_v3 -> app_v6)          
  10. Structure des sorties
  11. Comportements connus
  12. Dépannage (Troubleshooting)


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. PRÉSENTATION GÉNÉRALE DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Ce projet implémente un pipeline de viticulture de précision permettant de
  reconstruire automatiquement des nuages de points 3D individuels pour chaque
  pied de vigne à partir d'enregistrements vidéo de caméra de profondeur Intel
  RealSense (.bag) synchronisés avec des données GPS RTK.

  PRINCIPE DE FONCTIONNEMENT :
  ┌─────────────────────────────────────────────────────────────────┐
  │  .bag (vidéo profondeur)  +  .ubx (GPS RTK)                     │
  │           ↓                                                     │
  │  Correction PPK (post-traitement cinématique)                   │
  │           ↓                                                     │
  │  Synchronisation GPS <-> Frames vidéo (par timestamp)           │
  │           ↓                                                     │
  │  Assignation spatiale des frames aux pieds (Lambert93 / Y-axe) │
  │           ↓                                                     │
  │  Extraction C++ des PLY (frame par frame)                       │
  │           ↓                                                     │
  │  Reconstruction 3D (filtrage DBSCAN + registration ICP/RANSAC)  │
  │           ↓                                                     │
  │  Nuage 3D final par pied  (.ply)                                │
  │           ↓                                                     │
  │  Visualisation — App Streamlit (app_v3 -> app_v6)     │
  └─────────────────────────────────────────────────────────────────┘

  SYSTÈME DE COORDONNÉES :
  Lambert93 (EPSG:2154) natif dans tout le pipeline, sans conversion ENU.

  STRUCTURE DE PASSAGE :
    • Passage 1 (aller)  : frames avant le demi-tour (Y_L93 maximum)
    • Passage 2 (retour) : frames après le demi-tour


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2. ARCHITECTURE DU PROJET
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Répertoire racine (PROJECT_ROOT) :
  /home/jasser/Bureau/ims/Version_Off_line_pied_with_app/

  ├── realsense_data/                  <- Données brutes d'entrée
  │   ├── RealSense_17-07-2025.bag
  │   └── trk_20250717_0928.ubx
  │
  ├── pos_pied/
  │   └── pos_pied.shp                <- Shapefile centres des pieds (Lambert93)
  │
  ├── scripts_GPS/                    <- Outils PPK (RTKLIB)
  │
  ├── pipeline_config.json            <- CONFIG CENTRALISÉE 
  │
  ├── Extraction_Output/              <- Sorties intermédiaires
  │   ├── PPK_pos/
  │   ├── Timesample_files/
  │   └── <pied_XX_passY_DD-MM-YYYY>/
  │
  ├── final_Output/                   <- Sorties finales
  │   ├── Timesample_synchronised_files/
  │   ├── <pied_XX_passY_DD-MM-YYYY>/
  │   │   └── data_DD-MM-YYYY/
  │   │       └── PointCloud_ply/
  │   │           └── Pied(s)_merged_JJ_MM_AAAA.ply  <- RÉSULTAT FINAL 
  │   └── Piquetée_SSGLR/            <- Recalage inter-pieds 
  │
  ├── build/                          <- Compilation C++
  │
  ├── launch_pipeline.py
  ├── extract_timesample.py
  ├── PPK_Pipeline.py
  ├── syn_code.py
  ├── Generate_pied_jobs_.py
  ├── Run_full_pipeline.py
  ├── all_pipeline_3D.py
  ├── auto_extractor.cpp
  ├── CMakeLists.txt
  │
  ├── app_v3.py                       <- App Streamlit — version de base 
  ├── app_v4.py                       <- App Streamlit — vue par piquetée 
  ├── app_v5.py                       <- App Streamlit — reconstruction à chaud 
  ├── app_v6.py                       <- App Streamlit — SSGLR + comparaison 
  │
  ├── simulate_robot_scan.py          <- Simulation visuelle GPS 
  │
  └── pied_jobs_summary.csv


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  3. PRÉREQUIS ET DÉPENDANCES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  SYSTÈME D'EXPLOITATION :
    Ubuntu 20.04 / 22.04 (recommandé)

  PYTHON (>= 3.10) — installation complète :
    pip install -r requirements.txt

  DÉTAIL DES BIBLIOTHÈQUES PYTHON :
    pyrealsense2>=2.58.1             Lecture des fichiers .bag
    open3d>=0.19.0                   Traitement nuages de points 3D
    opencv-python-headless>=4.10.0   Filtrage couleur HSV 
    numpy>=1.26.0,<2.0.0             Calcul numérique
    scikit-learn>=1.4.0              Clustering DBSCAN
    scipy>=1.13.0                    Lissage Gaussien, KDTree 
    pandas>=2.2.0                    Données tabulaires CSV
    openpyxl>=3.1.5                  Export Excel
    pyproj>=3.6.0                    Projection WGS84 <-> Lambert93
    pyshp>=3.0.12                    Lecture shapefile
    geopandas>=0.14.0                Lecture shapefile vectorielle 
    natsort>=8.4.0                   Tri naturel des fichiers PLY
    streamlit>=1.35.0                Application web de supervision 
    plotly>=5.22.0                   Visualisation 3D interactive 
    matplotlib>=3.9.0                Simulation visuelle GPS 

  C++ — bibliothèques système :
    sudo apt install cmake build-essential libproj-dev libpcl-dev libopencv-dev
    + Intel RealSense SDK 2.0 (librealsense2)

  OUTILS RTKLIB :
    convbin, rnx2rtkp  ->  ./install_rtklib_tools.sh

  VÉRIFICATION :
    python3 -c "import pyrealsense2, open3d, streamlit, plotly, geopandas; print('OK')"


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  4. STRUCTURE DES FICHIERS D'ENTRÉE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Fichiers OBLIGATOIRES avant toute exécution :

  a) Fichiers vidéo profondeur (.bag)
     Convention : RealSense_DD-MM-YYYY.bag  — dans realsense_data/

  b) Fichiers GPS bruts (.ubx)
     Convention : trk_YYYYMMDD_HHMM.ubx    — dans realsense_data/

  c) Shapefile des pieds (Lambert93 EPSG:2154)
     Fichier : pos_pied/pos_pied.shp  (+ .dbf, .shx)

  d) pipeline_config.json  
     Déclarer chaque session dans bag_registry :
       "17-07-2025": {
           "bag_file":  "RealSense_17-07-2025.bag",
           "ubx_file":  "trk_20250717_0928.ubx",
           "rot_y_deg": 180.0
       }


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  5. DESCRIPTION DES SCRIPTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────┬─────────────────────────────────────────────┐
  │ Script                       │ Rôle                                        │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ launch_pipeline.py           │ Orchestrateur principal (4 étapes).         │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ extract_timesample.py        │ Étape 1 : timestamps .bag -> CSV.           │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ PPK_Pipeline.py              │ Étape 2 : correction GPS .ubx -> .pos.      │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ syn_code.py                  │ Étape 3 : synchro GPS/frames + bras de      │
  │                              │ levier + filtre arrêts près des pieds.     │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ Generate_pied_jobs_.py       │ Étape 4a : assignation frames -> pieds.     │
  │                              │ Rayon adaptatif, plus long segment          │
  │                              │ contigu, détection chevauchements.         │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ Run_full_pipeline.py         │ Étape 4b : extraction + reconstruction 3D.  │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ all_pipeline_3D.py           │ Module 3D : DBSCAN, filtre couleur HSV,     │
  │                              │ ICP/RANSAC sliding window.         │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ auto_extractor.cpp           │ Extracteur C++ compile-once/runtime-args.  │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v3.py                    │ App Streamlit — analyse P1/P2 par pied.    │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v4.py                    │ App Streamlit — vue par piquetée.           │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v5.py                    │ App Streamlit — reconstruction à chaud,     │
  │                              │ qualité GPS, aperçu RGB.                    │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v6.py                    │ App Streamlit — SSGLR inter-pieds,          │
  │                              │ comparaison multi-dates, export CSV.        │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ simulate_robot_scan.py       │ Animation GPS + cercles de détection.       │
  └──────────────────────────────┴─────────────────────────────────────────────┘


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  6. PIPELINE_CONFIG.JSON — CONFIGURATION CENTRALISÉE  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Tous les paramètres sont centralisés dans pipeline_config.json.
  Aucune modification de code source n'est nécessaire pour changer de dataset.

  STRUCTURE :
  {
    "project_root": "/home/jasser/Bureau/ims/Version_Off_line_pied_with_app/",
    "paths": {
        "bag_input_dir":     "realsense_data",
        "extraction_output": "Extraction_Output",
        "timesample_files":  "Extraction_Output/Timesample_files",
        "ppk_pos_dir":       "Extraction_Output/PPK_pos",
        "syn_output_dir":    "final_Output/Timesample_synchronised_files",
        "final_output":      "final_Output",
        "build_dir":         "build",
        "shapefile":         "pos_pied/pos_pied.shp",
        "pied_jobs_summary": "pied_jobs_summary.csv"
    },
    "bag_registry": {
        "17-07-2025": {
            "bag_file":  "RealSense_17-07-2025.bag",
            "ubx_file":  "trk_20250717_0928.ubx",
            "rot_y_deg": 180.0
        }
    },
    "params": {
        "default_radius": 0.35,
        "params_extraction": { "z_min": 0.4, "z_max": 1.0, "rot_z_deg": 270.0 },
        "filter_3d": {
            "voxel_size": 0.005,
            "eps": 0.12,
            "min_cluster_size": 500,
            "color_filter": {
                "enabled": true,
                "hue_min": 180,
                "hue_max": 320,
                "sat_min": 40
            }
        },
        "frame_reduction": { "enabled": true, "stride": 2 },
        "registration": { "voxel_size_reg": 0.02, "window_size": 4 }
    }
  }

  CLÉS IMPORTANTES :
    project_root     -> Chemin absolu racine (adapter à votre machine)
    bag_registry     -> Ajouter une entrée par date d'acquisition
    default_radius   -> Fenêtre spatiale Y_L93 par pied (mètres)
    color_filter     -> Filtre HSV pour supprimer piquets/ciel 
    frame_reduction  -> Stride pour réduire les frames extraites 


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  7. LANCEMENT — PIPELINE COMPLET AUTOMATIQUE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  COMMANDE PRINCIPALE :
    python3 launch_pipeline.py

  OPTIONS DISPONIBLES :
    --skip-extract        Passer l'étape 1 (timestamps déjà extraits)
    --skip-ppk            Passer l'étape 2 (.pos déjà disponibles)
    --skip-sync           Passer l'étape 3 (synchronized_*.csv déjà générés)
    --no-run              Générer les jobs sans lancer la reconstruction 3D
    --force-extract       Forcer la ré-extraction des timestamps
    --force-sync          Forcer la re-synchronisation GPS/frames
    --radius <m>          Fenêtre Y_L93 par pied en mètres (défaut : 0.35)
    --pass-select <1|2|both>  Passages à traiter (défaut : both)
    --pieds <n1 n2 ...>   Traiter uniquement certains pieds
    --acronyme <CODE>     Sigle station RGP pour le PPK (ex: BXME)
    --no-rgp              Ne pas télécharger les données IGN
    --simulate            Lancer la simulation visuelle en parallèle 
    --sim-speed <x>       Vitesse de la simulation (défaut : 5) 
    -v / --verbose        Afficher les sorties détaillées

  EXEMPLES :
    # Pipeline complet
    python3 launch_pipeline.py

    # GPS déjà corrigé, reconstruire uniquement
    python3 launch_pipeline.py --skip-extract --skip-ppk

    # Pieds 5-8, aller seulement, avec simulation x10
    python3 launch_pipeline.py --skip-extract --skip-ppk --skip-sync \
        --pieds 5 6 7 8 --pass-select 1 --simulate --sim-speed 10


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  8. LANCEMENT — ÉTAPE PAR ÉTAPE (MANUEL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Étape 1 :  python3 extract_timesample.py
  Étape 2 :  python3 PPK_Pipeline.py --skip-existing --acronyme BXME
  Étape 3 :  python3 syn_code.py --force --max-gap 1.5
  Étape 4a : python3 Generate_pied_jobs_.py --no-run --radius 0.5
  Étape 4b : python3 Run_full_pipeline.py

  Simulation visuelle (optionnel) :
    python3 simulate_robot_scan.py --speed 5
    python3 simulate_robot_scan.py --date 17-07-2025 --save simu.mp4 --loop


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  9. APPLICATION STREAMLIT (app_v3 -> app_v6) 
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  LANCEMENT :
    streamlit run app_v6.py        # version la plus complète (recommandée)

  FONCTIONNALITÉS PAR VERSION :

  app_v3 — Base
    • Matrice de sélection 5 piquetées x 5 pieds
    • Vue 3D Plotly Passage 1 et Passage 2 côte à côte
    • Switcher de date par passage
    • Barre de couverture du rang

  app_v4 — Vue piquetée
    • Tout app_v3 +
    • Vue 5 pieds côte à côte (piquetée complète)
    • Downsampling adaptatif (slider densité voxel)
    • Toggle mode nuit / jour

  app_v5 — Reconstruction à chaud
    • Tout app_v4 +
    • Panneau paramètres reconstruction (C++, 3D, registration)
    • Décalage et nombre d'images forçable (frame_offset, frame_count)
    • Badges qualité GPS (% fixe / float par pied)
    • Aperçu frame RGB du milieu de l'extraction
    • Vue superposée P1/P2 par alignement ICP
    • Comparaison deux dates (delta de croissance par pied)
    • Verrou de reconstruction (.reconstruction.lock)

  app_v6 — Complète (recommandée)
    • Tout app_v5 +
    • SSGLR : homogénéisation inter-pieds d'un piquetée
      (espacement réel shapefile + ICP local borné, sans RANSAC global)
    • Export CSV métriques par piquetée
    • Téléchargement du PLY homogénéisé

  PAGES DE L'APPLICATION :
    Page 1 — Analyse par Pied (Passages 1 & 2)
    Page 2 — Suivi Multi-Temporel (par piquetée)

  ACCÈS : http://localhost:8501


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  10. STRUCTURE DES SORTIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  EXTRACTION (intermédiaire) :
    Extraction_Output/pied05_pass1_17-07-2025/data_17-07-2025/
      ├── PointCloud_ply/        — Nuages bruts frame par frame (.ply)
      ├── RGB_image/             — Images couleur (.png) <- aperçu app 
      ├── Depth_raw/             — Cartes de profondeur 16bit
      ├── camera_intrinsics.json
      ├── frame_timestamps.csv
      └── data_GPS.txt

  RÉSULTAT FINAL 3D :
    final_Output/pied05_pass1_17-07-2025/data_17-07-2025/
      ├── PointCloud_ply/
      │   └── Pied(s)_merged_17_07_2025.ply   <- NUAGE FINAL 
      ├── Transformations/
      │   └── transformation_N.npy
      └── reconstruction_params.json           <- Traçabilité 

  SSGLR (piquetée homogénéisé) :               
    final_Output/Piquetée_SSGLR/piquetée1_17-07-2025/
      ├── Pied(s)_merged_piquetée1_17_07_2025.ply
      └── ssglr_info.json

  RÉCAPITULATIF GLOBAL :
    pied_jobs_summary.csv  — jobs, frames, coordonnées L93, rayon utilisé


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  11. COMPORTEMENTS CONNUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  • Pieds 1 à 4 sans frames : normal, caméra non encore active à ce moment.

  • GPS toujours Q=2 (float, jamais fixe) : bug de config RTKLIB.
    Vérifier dans rtkpost.conf : pos1-frequency=l1+l2, pos2-armode=fix-and-hold.
    L'app affiche un badge rouge "0% fixe" pour signaler ce problème.

  • Compilation unique : auto_extractor compilé une seule fois, paramètres
    passés via argv à chaque job (compile-once / runtime-args). 

  • Sessions déjà traitées ignorées automatiquement.

  • SSGLR collapse évité grâce à l'espacement réel shapefile + ICP borné.
    Ne jamais utiliser RANSAC global entre pieds (troncs trop similaires).


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  12. DÉPANNAGE (TROUBLESHOOTING)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  PROBLÈME : "Aucun job généré"
  SOLUTION : Augmenter --radius 0.6. Vérifier bag_registry dans pipeline_config.json.

  PROBLÈME : CMake build échoué
  SOLUTION : sudo apt install libproj-dev libpcl-dev libopencv-dev librealsense2-dev

  PROBLÈME : "Aucun nuage valide — reconstruction abandonnée"
  SOLUTION : Réduire eps ou augmenter min_cluster_size dans pipeline_config.json.

  PROBLÈME : GPS Q=2 uniquement (jamais fixe)
  SOLUTION : Éditer rtkpost.conf :
               pos1-frequency = l1+l2
               pos1-soltype   = combined
               pos2-armode    = fix-and-hold

  PROBLÈME : "ModuleNotFoundError: streamlit"
  SOLUTION : pip install streamlit plotly geopandas scipy matplotlib

  PROBLÈME : Verrou bloquant (.reconstruction.lock)
  SOLUTION : rm .reconstruction.lock   (si le processus précédent a planté)

  PROBLÈME : SSGLR — tous les pieds se superposent ("boule")
  SOLUTION : Décocher "Affiner par ICP local" dans l'app, ou vérifier que
             pos_pied.shp est bien en EPSG:2154.



///////////////////////////////////////////////////////////////////////////////////////



                          DOCUMENTATION — ENGLISH VERSION



━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TABLE OF CONTENTS (EN)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1.  Project Overview
  2.  Project Architecture
  3.  Prerequisites and Dependencies
  4.  Input File Structure
  5.  Script Descriptions
  6.  pipeline_config.json — Centralized Configuration  
  7.  Launch — Automated Full Pipeline
  8.  Launch — Step-by-Step (Manual)
  9.  Streamlit Application (app_v3 -> app_v6)          
  10. Output Structure
  11. Known Behaviors
  12. Troubleshooting


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. PROJECT OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  This project implements a precision viticulture pipeline designed to
  automatically reconstruct individual 3D point clouds for each grapevine
  stalk using depth video recordings from an Intel RealSense camera (.bag)
  synchronized with RTK GPS data.

  OPERATIONAL PRINCIPLE:
  ┌─────────────────────────────────────────────────────────────────┐
  │  .bag (depth video)  +  .ubx (RTK GPS)                          │
  │            ↓                                                    │
  │  PPK Correction (Post-Processed Kinematic)                      │
  │            ↓                                                    │
  │  GPS <-> Video Frames Synchronization (via timestamp)           │
  │            ↓                                                    │
  │  Spatial Assignment of Frames to Stalks (Lambert93 / Y-axis)    │
  │            ↓                                                    │
  │  C++ Extraction of PLYs (frame by frame)                        │
  │            ↓                                                    │
  │  3D Reconstruction (DBSCAN filtering + ICP/RANSAC registration) │
  │            ↓                                                    │
  │  Final 3D Point Cloud per Stalk (.ply)                          │
  │            ↓                                                    │
  │  Visualization — Streamlit App (app_v3 -> app_v6)              │
  └─────────────────────────────────────────────────────────────────┘

  COORDINATE SYSTEM:
  Native Lambert93 (EPSG:2154) throughout the entire pipeline, without ENU conversion.

  PASS STRUCTURE:
    • Pass 1 (outbound) : frames before the u-turn (Y_L93 maximum)
    • Pass 2 (inbound)  : frames after the u-turn


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2. PROJECT ARCHITECTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Root directory (PROJECT_ROOT):
  /home/jasser/Bureau/ims/Version_Off_line_pied_with_app/

  ├── realsense_data/                 <- Raw input data
  │   ├── RealSense_17-07-2025.bag
  │   └── trk_20250717_0928.ubx
  │
  ├── pos_pied/
  │   └── pos_pied.shp                <- Vine stalk center shapefile (Lambert93)
  │
  ├── scripts_GPS/                    <- PPK Tools (RTKLIB)
  │
  ├── pipeline_config.json            <- CENTRALIZED CONFIG 
  │
  ├── Extraction_Output/              <- Intermediate outputs
  │   ├── PPK_pos/
  │   ├── Timesample_files/
  │   └── <pied_XX_passY_DD-MM-YYYY>/
  │
  ├── final_Output/                   <- Final outputs
  │   ├── Timesample_synchronised_files/
  │   ├── <pied_XX_passY_DD-MM-YYYY>/
  │   │   └── data_DD-MM-YYYY/
  │   │       └── PointCloud_ply/
  │   │           └── Pied(s)_merged_DD_MM_YYYY.ply  <- FINAL RESULT 
  │   └── Piquetée_SSGLR/             <- Inter-stalk alignment 
  │
  ├── build/                          <- C++ Compilation
  │
  ├── launch_pipeline.py
  ├── extract_timesample.py
  ├── PPK_Pipeline.py
  ├── syn_code.py
  ├── Generate_pied_jobs_.py
  ├── Run_full_pipeline.py
  ├── all_pipeline_3D.py
  ├── auto_extractor.cpp
  ├── CMakeLists.txt
  │
  ├── app_v3.py                       <- Streamlit App — Base version 
  ├── app_v4.py                       <- Streamlit App — View by stake row 
  ├── app_v5.py                       <- Streamlit App — On-the-fly reconstruction 
  ├── app_v6.py                       <- Streamlit App — SSGLR + comparison 
  │
  ├── simulate_robot_scan.py          <- Visual GPS simulation 
  │
  └── pied_jobs_summary.csv


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  3. PREREQUISITES AND DEPENDENCIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  OPERATING SYSTEM:
    Ubuntu 20.04 / 22.04 (recommended)

  PYTHON (>= 3.10) — complete installation:
    pip install -r requirements.txt

  DETAILED PYTHON LIBRARIES:
    pyrealsense2>=2.58.1              Reads .bag files
    open3d>=0.19.0                    3D point cloud processing
    opencv-python-headless>=4.10.0    HSV color filtering 
    numpy>=1.26.0,<2.0.0              Numerical calculations
    scikit-learn>=1.4.0               DBSCAN clustering
    scipy>=1.13.0                     Gaussian smoothing, KDTree 
    pandas>=2.2.0                     Tabular CSV data
    openpyxl>=3.1.5                   Excel export
    pyproj>=3.6.0                     WGS84 <-> Lambert93 projection
    pyshp>=3.0.12                     Shapefile reading
    geopandas>=0.14.0                 Vector shapefile processing 
    natsort>=8.4.0                    Natural sorting of PLY files
    streamlit>=1.35.0                 Monitoring web application 
    plotly>=5.22.0                    Interactive 3D visualization 
    matplotlib>=3.9.0                 Visual GPS simulation 

  C++ — SYSTEM LIBRARIES:
    sudo apt install cmake build-essential libproj-dev libpcl-dev libopencv-dev
    + Intel RealSense SDK 2.0 (librealsense2)

  RTKLIB TOOLS:
    convbin, rnx2rtkp  ->  ./install_rtklib_tools.sh

  VERIFICATION:
    python3 -c "import pyrealsense2, open3d, streamlit, plotly, geopandas; print('OK')"


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  4. INPUT FILE STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  MANDATORY files prior to any execution:

  a) Depth video files (.bag)
     Convention: RealSense_DD-MM-YYYY.bag  — inside realsense_data/

  b) Raw GPS files (.ubx)
     Convention: trk_YYYYMMDD_HHMM.ubx    — inside realsense_data/

  c) Stalk shapefile (Lambert93 EPSG:2154)
     File: pos_pied/pos_pied.shp  (+ .dbf, .shx)

  d) pipeline_config.json  
     Declare each session in bag_registry:
       "17-07-2025": {
           "bag_file":  "RealSense_17-07-2025.bag",
           "ubx_file":  "trk_20250717_0928.ubx",
           "rot_y_deg": 180.0
       }


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  5. SCRIPT DESCRIPTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ┌──────────────────────────────┬─────────────────────────────────────────────┐
  │ Script                       │ Role                                        │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ launch_pipeline.py           │ Main orchestrator (4 steps).                │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ extract_timesample.py        │ Step 1: .bag timestamps -> CSV.             │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ PPK_Pipeline.py              │ Step 2: GPS correction .ubx -> .pos.        │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ syn_code.py                  │ Step 3: GPS/frame sync + lever arm +        │
  │                              │ stop filtering near stalks.                 │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ Generate_pied_jobs_.py       │ Step 4a: Frame-to-stalk assignment.         │
  │                              │ Adaptive radius, longest contiguous         │
  │                              │ segment, overlap detection.                 │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ Run_full_pipeline.py         │ Step 4b: Extraction + 3D reconstruction.    │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ all_pipeline_3D.py           │ 3D Module: DBSCAN, HSV color filter,        │
  │                              │ sliding window ICP/RANSAC.                  │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ auto_extractor.cpp           │ Compile-once/runtime-args C++ extractor.    │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v3.py                    │ Streamlit App — P1/P2 analysis per stalk.   │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v4.py                    │ Streamlit App — View by stake row.          │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v5.py                    │ Streamlit App — On-the-fly reconstruction,  │
  │                              │ GPS quality, RGB preview.                   │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ app_v6.py                    │ Streamlit App — Inter-stalk SSGLR,          │
  │                              │ multi-date comparison, CSV export.          │
  ├──────────────────────────────┼─────────────────────────────────────────────┤
  │ simulate_robot_scan.py       │ GPS animation + detection circles.          │
  └──────────────────────────────┴─────────────────────────────────────────────┘


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  6. PIPELINE_CONFIG.JSON — CENTRALIZED CONFIGURATION  
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  All parameters are centralized in pipeline_config.json.
  No source code modifications are necessary when changing datasets.

  STRUCTURE:
  {
    "project_root": "/home/jasser/Bureau/ims/Version_Off_line_pied_with_app/",
    "paths": {
        "bag_input_dir":     "realsense_data",
        "extraction_output": "Extraction_Output",
        "timesample_files":  "Extraction_Output/Timesample_files",
        "ppk_pos_dir":       "Extraction_Output/PPK_pos",
        "syn_output_dir":    "final_Output/Timesample_synchronised_files",
        "final_output":      "final_Output",
        "build_dir":         "build",
        "shapefile":         "pos_pied/pos_pied.shp",
        "pied_jobs_summary": "pied_jobs_summary.csv"
    },
    "bag_registry": {
        "17-07-2025": {
            "bag_file":  "RealSense_17-07-2025.bag",
            "ubx_file":  "trk_20250717_0928.ubx",
            "rot_y_deg": 180.0
        }
    },
    "params": {
        "default_radius": 0.35,
        "params_extraction": { "z_min": 0.4, "z_max": 1.0, "rot_z_deg": 270.0 },
        "filter_3d": {
            "voxel_size": 0.005,
            "eps": 0.12,
            "min_cluster_size": 500,
            "color_filter": {
                "enabled": true,
                "hue_min": 180,
                "hue_max": 320,
                "sat_min": 40
            }
        },
        "frame_reduction": { "enabled": true, "stride": 2 },
        "registration": { "voxel_size_reg": 0.02, "window_size": 4 }
    }
  }

  IMPORTANT KEYS:
    project_root     -> Absolute root path (adapt to your local setup)
    bag_registry     -> Add an entry for each acquisition date
    default_radius   -> Spatial Y_L93 window per stalk (meters)
    color_filter     -> HSV filter to eliminate posts/sky background 
    frame_reduction  -> Stride factor to reduce extracted frame count 


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  7. LAUNCH — AUTOMATED FULL PIPELINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  MAIN COMMAND:
    python3 launch_pipeline.py

  AVAILABLE OPTIONS:
    --skip-extract         Skip Step 1 (timestamps already extracted)
    --skip-ppk             Skip Step 2 (.pos files already available)
    --skip-sync            Skip Step 3 (synchronized_*.csv already generated)
    --no-run               Generate job list without launching 3D reconstruction
    --force-extract        Force re-extraction of timestamps
    --force-sync           Force GPS/frame re-synchronization
    --radius <m>           Y_L93 window per stalk in meters (default: 0.35)
    --pass-select <1|2|both> Passes to process (default: both)
    --pieds <n1 n2 ...>    Process specific stalk IDs only
    --acronyme <CODE>      RGP station code for PPK (e.g., BXME)
    --no-rgp               Do not download IGN data
    --simulate             Run visual simulation in parallel 
    --sim-speed <x>        Simulation speed multiplier (default: 5) 
    -v / --verbose         Display detailed output logs

  EXAMPLES:
    # Full pipeline
    python3 launch_pipeline.py

    # GPS already processed; run 3D reconstruction only
    python3 launch_pipeline.py --skip-extract --skip-ppk

    # Stalks 5 to 8, outbound pass only, with 10x simulation speed
    python3 launch_pipeline.py --skip-extract --skip-ppk --skip-sync \
        --pieds 5 6 7 8 --pass-select 1 --simulate --sim-speed 10


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  8. LAUNCH — STEP-BY-STEP (MANUAL)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Step 1 :  python3 extract_timesample.py
  Step 2 :  python3 PPK_Pipeline.py --skip-existing --acronyme BXME
  Step 3 :  python3 syn_code.py --force --max-gap 1.5
  Step 4a : python3 Generate_pied_jobs_.py --no-run --radius 0.5
  Step 4b : python3 Run_full_pipeline.py

  Visual simulation (optional):
    python3 simulate_robot_scan.py --speed 5
    python3 simulate_robot_scan.py --date 17-07-2025 --save simu.mp4 --loop


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  9. STREAMLIT APPLICATION (app_v3 -> app_v6) 
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  LAUNCH:
    streamlit run app_v6.py        # Most complete version (recommended)

  FEATURES BY VERSION:

  app_v3 — Base
    • 5 stakes x 5 stalks selection matrix
    • Side-by-side Plotly 3D view for Pass 1 and Pass 2
    • Date switcher per pass
    • Row coverage progress bar

  app_v4 — Stake view
    • All features from app_v3 +
    • Side-by-side view of 5 stalks (full stake row)
    • Adaptive downsampling (voxel density slider)
    • Dark/Light mode toggle

  app_v5 — On-the-fly reconstruction
    • All features from app_v4 +
    • Reconstruction parameter control panel (C++, 3D, registration)
    • Overridable frame offset and frame count (frame_offset, frame_count)
    • GPS Quality Badges (% Fix / Float per stalk)
    • RGB frame preview taken from the middle of the extraction window
    • Superimposed P1/P2 view using ICP alignment
    • Dual-date comparison (growth delta per stalk)
    • Reconstruction process lock (.reconstruction.lock)

  app_v6 — Full Version (recommended)
    • All features from app_v5 +
    • SSGLR: Inter-stalk homogenization across a stake row
      (uses real shapefile spacing + bounded local ICP without global RANSAC)
    • Metrics CSV export per stake row
    • Downloadable homogenized PLY file

  APPLICATION PAGES:
    Page 1 — Analysis per Stalk (Passes 1 & 2)
    Page 2 — Multi-Temporal Tracking (per stake row)

  ACCESS URL: http://localhost:8501


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  10. OUTPUT STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  EXTRACTION (intermediate):
    Extraction_Output/pied05_pass1_17-07-2025/data_17-07-2025/
      ├── PointCloud_ply/         — Raw frame-by-frame point clouds (.ply)
      ├── RGB_image/              — Color images (.png) <- app preview 
      ├── Depth_raw/              — 16-bit depth maps
      ├── camera_intrinsics.json
      ├── frame_timestamps.csv
      └── data_GPS.txt

  FINAL 3D RESULT:
    final_Output/pied05_pass1_17-07-2025/data_17-07-2025/
      ├── PointCloud_ply/
      │   └── Pied(s)_merged_17_07_2025.ply    <- FINAL POINT CLOUD 
      ├── Transformations/
      │   └── transformation_N.npy
      └── reconstruction_params.json           <- Traceability log 

  SSGLR (homogenized stake row):                
    final_Output/Piquetée_SSGLR/piquetée1_17-07-2025/
      ├── Pied(s)_merged_piquetée1_17_07_2025.ply
      └── ssglr_info.json

  GLOBAL SUMMARY:
    pied_jobs_summary.csv  — Jobs, frame counts, L93 coordinates, applied radius


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  11. KNOWN BEHAVIORS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  • Stalks 1 to 4 missing frames: Normal behavior; camera was not yet active
    during this segment.

  • GPS permanently Q=2 (Float, never Fix): RTKLIB configuration issue.
    Verify inside rtkpost.conf: pos1-frequency=l1+l2, pos2-armode=fix-and-hold.
    The app displays a red "0% Fix" badge to highlight this issue.

  • Single compilation: auto_extractor compiled only once; arguments passed
    via argv during execution (compile-once / runtime-args). 

  • Processed sessions skipped: Already processed sessions are automatically
    recognized and skipped.

  • SSGLR collapse prevention: Avoided through real shapefile spacing paired
    with bounded local ICP. Never use global RANSAC between stalks (vine trunks
    are visually too similar).


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  12. TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ISSUE: "No jobs generated"
  SOLUTION: Increase search radius with --radius 0.6. Verify bag_registry inside
            pipeline_config.json.

  ISSUE: CMake build failed
  SOLUTION: sudo apt install libproj-dev libpcl-dev libopencv-dev librealsense2-dev

  ISSUE: "No valid point cloud — reconstruction aborted"
  SOLUTION: Decrease eps or increase min_cluster_size in pipeline_config.json.

  ISSUE: GPS stuck on Q=2 (never Fix)
  SOLUTION: Edit rtkpost.conf :
                pos1-frequency = l1+l2
                pos1-soltype   = combined
                pos2-armode    = fix-and-hold

  ISSUE: "ModuleNotFoundError: streamlit"
  SOLUTION: pip install streamlit plotly geopandas scipy matplotlib

  ISSUE: Lock file blocking execution (.reconstruction.lock)
  SOLUTION: rm .reconstruction.lock   (if a previous execution crashed unexpectedly)

  ISSUE: SSGLR — All stalks collapse into a single point ("ball effect")
  SOLUTION: Uncheck "Refine with local ICP" in the application, or verify that
            pos_pied.shp is properly formatted in EPSG:2154.

═══════════════════════════════════════════════════════════════════════════════════
  FIN DU DOCUMENT / END OF DOCUMENT
═══════════════════════════════════════════════════════════════════════════════════
