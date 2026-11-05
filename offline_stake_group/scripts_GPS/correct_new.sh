#!/bin/bash

# ===== Vérification des arguments =====
if [[ "$#" -ne 2 ]]; then
    echo -e "\033[1;31mUsage:\033[0m ./convert.sh <file.ubx> <base.??o>"
    exit 1
fi

# ===== Variables de base =====
ubxfile="$1"
obsfile="${ubxfile%.ubx}.obs"
ppkfile="${ubxfile%.ubx}.pos"
baseobs="$2"

# ===== Déduction de l'année à partir de l'extension (ex: .25o -> 25) =====
year_prefix="${baseobs##*.}"        # extrait "25o"
year_number="${year_prefix%o}"      # supprime le 'o' → "25"
base_prefix="${baseobs%.*}"         # supprime l’extension → bxme097z

# Fichiers associés : .YYn, .YYg, etc.
basenav="${base_prefix}.${year_number}n"
baseglo="${base_prefix}.${year_number}g"

conf_file="rtkpost.conf"

# ===== Vérification des exécutables =====
command -v convbin >/dev/null 2>&1 || { echo >&2 "❌ convbin not found. Please install it first."; exit 1; }
command -v rnx2rtkp >/dev/null 2>&1 || { echo >&2 "❌ rnx2rtkp not found. Please install it first."; exit 1; }

# ===== Affichage d'information =====
echo -e "\033[1m\n📡 PPK Conversion Utility for UBX Files"
echo -e "(C) 2025 Mohamed MABROUK / IMS Bordeaux\033[0m"

# ===== Décodage UBX → RINEX OBS =====
echo -e "\n🔄 Decoding UBX file: \033[1m$ubxfile\033[0m → \033[1m$obsfile\033[0m"
convbin -r ubx -o "$obsfile" "$ubxfile"

# ===== Vérification du fichier de config =====
if [[ ! -f "$conf_file" ]]; then
    echo -e "\033[1;31m❌ Missing configuration file:\033[0m $conf_file"
    exit 1
fi

# ===== Lancement de la correction PPK =====
echo -e "\n⚙️  Running PPK correction using:"
echo -e "   🛰️  Rover : $obsfile"
echo -e "   🏠 Base  : $baseobs"
echo -e "   📡 Nav   : $basenav"
echo -e "   🛰️  GLO   : $baseglo"
echo -e "   ⚙️  Conf  : $conf_file\n"

rnx2rtkp -k "$conf_file" -o "$ppkfile" "$obsfile" "$baseobs" "$basenav" "$baseglo"

# ===== Fin =====
echo -e "\n✅ Done! Result saved to: \033[1m$ppkfile\033[0m"
