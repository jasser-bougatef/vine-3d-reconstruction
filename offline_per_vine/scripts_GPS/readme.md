# PPK & Synchronisation Caméra–GPS

Application Streamlit pour :
- corriger un **log GNSS UBX en PPK** avec des bases RGP (Centipède) via RTKLIB
- **apparier** les horodatages **caméra ↔ GPS** avec prise en compte du déport caméra

L’outil crée une **session isolée** par acquisition et garde les artefacts `.pos`, fichiers corrigés et journaux.

---

## Vue d’ensemble

- **UI principale** : `app_PPK_Correction.py` — deux onglets : **① PPK** puis **② Synchronisation**    
- **Algorithme de synchro** : `gpssync.py` — interpolation temporelle entre échantillons PPK, projection Lambert‑93, application des offsets `(x_arm, y_arm)`  
- **Utilitaires** : `utilities.py` — lecture GGA/RMC dans le `.ubx`, mise à jour de `config.json`, extraction d’archives RGP  
- **Téléchargement RGP** : `rgp_station_downloader_from_config.py` — automatise la page RGP IGN en lisant la config structurée (voir _Schéma de configuration_)   

---

## Fonctionnalités clés

- **PPK automatisé** : copie du `.ubx`, téléchargement des bases RGP sur la plage horaire détectée, exécution de `correct_new.sh` (RTKLIB) et détection du `.pos` généré. L’UI affiche `stdout` et `stderr` et conserve le fichier `.pos` dans `uploads/<session>/output/`.    
- **Stepper d’état** : progression par étape — **gris** en attente, **bleu** en cours, **vert** terminé.    
- **Synchronisation caméra** : interpolation sur la timeline PPK, projection via `pyproj` (EPSG:4326 ↔ EPSG:2154), application des déports `(x_arm, y_arm)` selon le **heading** instantané. Sortie en `_2.txt` avec colonnes géo enrichies.  
- **Téléchargement RGP** : glisseur horaire, options GLONASS, cadence, sélection station, téléchargement ZIP et extraction.   

---

## Dépendances

- Python 3.10+ recommandé
- **RTKLIB** installé et accessible (`rtkpost`, `convbin`), par exemple via `install_rtklib_tools.sh` ou votre gestionnaire de paquets
- Navigateur **Chrome/Chromium** présent pour Selenium
- Paquets Python listés dans `requirements.txt`

---

## Installation rapide

```bash
# Depuis la racine du projet
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Installez RTKLIB si nécessaire puis vérifiez que `rtkpost` est accessible dans votre PATH.

---

## Schéma de configuration

Deux formats sont possibles. Le **format structuré** est recommandé et utilisé par le downloader RGP.

### Format structuré (recommandé)
Utilisé par `rgp_station_downloader_from_config.py` :
```json
{
  "ppk": {
    "date": "07/04/2025",
    "start_time": "07:15",
    "end_time": "09:15",
    "acronyme": "BXME"
  },
  "gpslogger": {
    "DESTINATION_FOLDER": "/chemin/vers/sorties"
  }
}
```
  

### Format plat (hérité)
Peut être mis à jour automatiquement par l’UI lors de l’extraction GGA :
```json
{
  "date": "07/04/2025",
  "start_time": "07:15",
  "end_time": "09:15"
}
```
La fonction `update_config_json` arrondit aux quarts d’heure et impose au minimum **1 h** de fenêtre.  

---

## Lancer l’application

```bash
source .venv/bin/activate
streamlit run app_PPK_Correction.py
```

Ouvrez l’URL locale indiquée dans le terminal.

---

## Mode d’emploi

### ① PPK
1. **Charger** le fichier `.ubx`. L’UI détecte la fenêtre via GGA/RMC et met à jour `config.json`.  
2. **Télécharger** automatiquement les données RGP sur la période détectée et **extraire** l’archive dans `uploads/<session>/correction/`.    
3. **Lancer** la correction avec `correct_new.sh` et `rtkpost.conf`. Le `.pos` final est déplacé dans `uploads/<session>/output/`.    

### ② Synchronisation caméra
1. **Choisir** le schéma de navigation
   - **U (toujours marche avant)** : le robot se retourne en bout de rang et repart en marche avant
   - **Aller marche avant, retour en marche arrière** : pas de retournement, sens inverse au retour  
   L’offset `(x_arm, y_arm)` est appliqué dans le cadre du **heading** instantané (sin/cos) au moment de la prise.  
2. **Offsets caméra** : si la caméra n’est pas co‑localisée avec l’antenne, renseigner `x_arm` (avant/arrière) et `y_arm` (latéral). L’UI peut neutraliser les offsets si le mode choisi ne permet pas une correction fiable.    
3. **Téléverser** les fichiers temps caméra `.txt` dans l’ordre. L’outil génère des fichiers corrigés suffixés `_2.txt` avec lat/lon corrigés et coordonnées Lambert‑93.  

---

## Arborescence

```
.
├── app_PPK_Correction.py                 # UI Streamlit (PPK + Synchronisation)    
├── gpssync.py                            # Algorithme synchro caméra ↔ GPS  
├── utilities.py                          # GGA/RMC, update_config_json, extract/merge ZIP RGP  
├── rgp_station_downloader_from_config.py # Téléchargement RGP auto   
├── correct_new.sh                        # Enveloppe RTKLIB (rtkpost)
├── rtkpost.conf                          # Configuration RTKLIB
├── start.sh                              # Lancement rapide
├── requirements.txt
├── config.json                           # Config plat ou structuré
└── uploads/
    └── session_<horodatage>_<nom.ubx>/
        ├── correction/
        └── output/
```

---

## Sorties

- `uploads/<session>/output/<nom>.pos` : trajectoire corrigée PPK
- `uploads/<session>/output/<cam>[_k]_2.txt` : fichiers caméra corrigés avec colonnes Lat, Lon, X_Lambert, Y_Lambert
- `uploads/<session>/output/gpssync_config.json` : paramètres de synchro et liste des fichiers traités 

---

## Résolution de problèmes

- **RTKLIB introuvable** : vérifier l’installation et le PATH, ou utiliser `install_rtklib_tools.sh`
- **ZIP RGP manquant** : contrôler l’accès réseau et le contenu de `config.json` (date et heures) 
- **gpssync échoue** : vérifier le format des fichiers caméra et la cohérence des offsets `(x_arm, y_arm)` avec le schéma de navigation. Consulter `stdout` et `stderr` affichés dans l’UI.
- **États du stepper** : le passage gris → bleu → vert dépend des drapeaux `ppk_started`, `ppk_done`, `gpssync_started`, `gpssync_done` gérés par l’UI.

---

## Sécurité et bonnes pratiques

- Conserver le `.ubx`, le ZIP RGP, le `.pos` et les fichiers corrigés pour la traçabilité
- Versionner `rtkpost.conf` et garder une copie du `config.json` associé à chaque session
- Ne pas exposer publiquement vos chemins locaux ou identifiants

---
