#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import copy
import cv2
import numpy as np
import open3d as o3d
import argparse
from pathlib import Path
from natsort import natsorted
from sklearn.cluster import DBSCAN

# ══════════════════════════════════════════════════════════════════════════════
#  CHARGEMENT DE LA CONFIG CENTRALE
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    """Cherche pipeline_config.json depuis le dossier du script ou PROJECT_ROOT."""
    search_paths = [
        Path(__file__).resolve().parent / "pipeline_config.json",
    ]
    for p in search_paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(
        "pipeline_config.json introuvable. "
        "Placez-le dans le même dossier que ce script."
    )

_CFG = load_config()
_F3D = _CFG["params"]["filter_3d"]
_REG = _CFG["params"]["registration"]
_CF  = _F3D.get("color_filter", {})
_FRD = _CFG["params"].get("frame_reduction", {})


class PointCloudFilter:
    """
    Denoising et segmentation DBSCAN.
    Le filtrage background (z_min / z_max) est entièrement délégué au C++
    (auto_extractor) — cette classe ne fait plus de coupure de profondeur.
    Inclut un filtre HSV optionnel pour supprimer les points bleus, violets, 
    blancs (piquets, ciel, artefacts métalliques, etc.).
    """

    def __init__(self,
                 voxel_size        = _F3D["voxel_size"],
                 use_statistical   = _F3D["use_statistical"],
                 use_radius_filter = _F3D["use_radius_filter"],
                 eps               = _F3D["eps"],
                 min_points        = _F3D["min_points"],
                 min_cluster_size  = _F3D["min_cluster_size"],
                 nb_neighbors      = _F3D["nb_neighbors"],
                 std_ratio         = _F3D["std_ratio"],
                 radius            = _F3D["radius"],
                 min_radius_points = _F3D["min_radius_points"],
                 # ── filtres de couleurs ────────────────────────────────────
                 color_filter_enabled = _CF.get("enabled", False),
                 hue_min              = _CF.get("hue_min", 180),
                 hue_max              = _CF.get("hue_max", 320),
                 sat_min              = _CF.get("sat_min", 40),
                 val_min              = _CF.get("val_min", 30),
                 white_sat_max        = _CF.get("white_sat_max", 40),
                 white_val_min        = _CF.get("white_val_min", 180),
                 # ── sous-échantillonnage post-extraction (par paires) ──────
                 #    N'affecte pas l'extraction C++ : s'applique juste avant
                 #    le filtrage, sur la liste des PLY déjà extraits.
                 frame_reduction_enabled = _FRD.get("enabled", False),
                 frame_stride            = _FRD.get("stride", 2)):

        self.voxel_size        = voxel_size
        self.use_statistical   = use_statistical
        self.use_radius_filter = use_radius_filter
        self.eps               = eps
        self.min_points        = min_points
        self.min_cluster_size  = min_cluster_size
        self.nb_neighbors      = nb_neighbors
        self.std_ratio         = std_ratio
        self.radius            = radius
        self.min_radius_points = min_radius_points
        
        # Paramètres filtres couleur
        self.color_filter_enabled = color_filter_enabled
        self.hue_min              = hue_min
        self.hue_max              = hue_max
        self.sat_min              = sat_min
        self.val_min              = val_min
        self.white_sat_max        = white_sat_max
        self.white_val_min        = white_val_min

        # sous-échantillonnage par paires
        self.frame_reduction_enabled = frame_reduction_enabled
        self.frame_stride            = max(1, int(frame_stride))

    # ── Filtre de couleurs (HSV) ──────────────────────────────────────────

    def _apply_color_filters(self, pcd):
        """
        Supprime les points dont la couleur tombe dans :
        1. La plage HSV Bleue et Violette.
        2. La plage HSV Blanche (faible saturation, haute luminosité).
        """
        if not pcd.has_colors():
            return pcd

        # open3d stocke les couleurs en float [0,1] → uint8
        colors_rgb = (np.asarray(pcd.colors) * 255).astype(np.uint8)  # (N, 3)

        # cv2.cvtColor attend BGR et une image 3D : reshape en (1, N, 3)
        img_bgr = colors_rgb[:, ::-1].reshape(1, -1, 3)               # RGB→BGR
        img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)            # H: 0-179
        hsv     = img_hsv.reshape(-1, 3)

        H = hsv[:, 0].astype(np.int32)  # 0-179
        S = hsv[:, 1]                   # 0-255
        V = hsv[:, 2]                   # 0-255

        # Conversion degrés réels → échelle OpenCV (÷2)
        h_min = self.hue_min // 2
        h_max = self.hue_max // 2

        # Masque 1 : Bleu et Violet
        blue_purple_mask = (H >= h_min) & (H <= h_max) & (S >= self.sat_min) & (V >= self.val_min)
        
        # Masque 2 : Blanc (très peu de couleur, très lumineux)
        white_mask = (S <= self.white_sat_max) & (V >= self.white_val_min)

        # Combinaison des masques de suppression
        remove_mask = blue_purple_mask | white_mask
        keep_mask = ~remove_mask

        n_removed = int(remove_mask.sum())
        if n_removed > 0:
            print(f"     -> Filtres couleur (Bleu/Violet/Blanc) : {n_removed} points supprimés "
                  f"({n_removed * 100 / len(remove_mask):.1f}%)")

        return pcd.select_by_index(np.where(keep_mask)[0])

    # ── pipeline de filtrage d'un seul nuage ──────────────────────────────

    def filter_single_cloud(self, pcd):
        # Step 1: Voxel Downsampling
        if self.voxel_size > 0:
            pcd = pcd.voxel_down_sample(self.voxel_size)

        # Step 1b: Color filter — suppression bleus, violets et blancs
        if self.color_filter_enabled:
            pcd = self._apply_color_filters(pcd)
            if len(pcd.points) == 0:
                return pcd

        # Step 2: Outlier Denoising
        if self.use_statistical:
            pcd, _ = pcd.remove_statistical_outlier(
                nb_neighbors=self.nb_neighbors, std_ratio=self.std_ratio)
        elif self.use_radius_filter:
            pcd, _ = pcd.remove_radius_outlier(
                nb_points=self.min_radius_points, radius=self.radius)

        # Step 3: DBSCAN Clustering
        points = np.asarray(pcd.points)
        if len(points) == 0:
            return pcd

        clustering = DBSCAN(eps=self.eps, min_samples=self.min_points, n_jobs=-1).fit(points)
        labels = clustering.labels_

        valid_mask = labels >= 0
        if not np.any(valid_mask):
            return pcd

        counts = np.bincount(labels[valid_mask])
        large_clusters = np.where(counts > self.min_cluster_size)[0]
        mask = np.isin(labels, large_clusters)

        return pcd.select_by_index(np.where(mask)[0])

    def _apply_frame_reduction(self, list_files):
        """
        NOUVELLE STRATÉGIE — sous-échantillonnage post-extraction.

        Ne garde qu'une frame sur `frame_stride` (stride=2 -> une frame
        sur deux, "par paires") parmi les PLY déjà extraits par le C++.
        L'extraction elle-même reste toujours contiguë et complète ;
        cette réduction n'intervient que juste avant le filtrage/DBSCAN/
        registration, pour réduire le volume de données à traiter tout
        en gardant une couverture régulière de la fenêtre du piquet.
        """
        if not self.frame_reduction_enabled or self.frame_stride <= 1:
            return list_files

        reduced = list_files[::self.frame_stride]
        print(
            f"[IO] Sous-échantillonnage par paires (stride={self.frame_stride}) : "
            f"{len(list_files)} frames extraites -> {len(reduced)} frames conservées "
            f"pour le filtrage/registration."
        )
        return reduced

    def process_folder(self, folder_path):
        filtered_clouds = []
        list_files = natsorted(os.listdir(folder_path))
        list_files = [f for f in list_files if f.endswith((".ply", ".pcd"))]
        list_files = self._apply_frame_reduction(list_files)

        for file_name in list_files:
            if file_name.endswith((".ply", ".pcd")):
                file_path = os.path.join(folder_path, file_name)
                print(f"[IO] Preprocessing: {file_name}")

                raw_pcd = o3d.io.read_point_cloud(file_path)
                original_count = len(raw_pcd.points)

                clean_pcd = self.filter_single_cloud(raw_pcd)
                final_count = len(clean_pcd.points)

                if final_count > 0:
                    filtered_clouds.append(clean_pcd)
                    print(f"     -> Reduction: {original_count} points -> {final_count} clean points.")
                else:
                    print(f"     -> [Warning] Nuage vide après filtrage. Frame ignorée.")

        return filtered_clouds


class SlidingWindowRegistration:

    def __init__(self,
                 output_ply_dir,
                 output_trans_dir,
                 voxel_size_reg     = _REG["voxel_size_reg"],
                 voxel_size_merge   = _REG["voxel_size_merge"],
                 window_size        = _REG["window_size"],
                 sliding_voxel_size = _REG["sliding_voxel_size"]):

        self.output_ply_dir     = output_ply_dir
        self.output_trans_dir   = output_trans_dir
        self.voxel_size_reg     = voxel_size_reg
        self.voxel_size_merge   = voxel_size_merge
        self.window_size        = window_size
        self.sliding_voxel_size = sliding_voxel_size

    def _preprocess_features(self, pcd):
        pcd_down = pcd.voxel_down_sample(self.voxel_size_reg)
        radius_normal = self.voxel_size_reg * 2
        pcd_down.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius_normal, max_nn=30))
        radius_feature = self.voxel_size_reg * 5
        pcd_fpfh = o3d.pipelines.registration.compute_fpfh_feature(
            pcd_down,
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))
        return pcd_down, pcd_fpfh

    def _prepare_dataset(self, source, target):
        source.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=self.voxel_size_reg * 2, max_nn=30))
        target.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=self.voxel_size_reg * 2, max_nn=30))
        src_down, src_fpfh = self._preprocess_features(source)
        tgt_down, tgt_fpfh = self._preprocess_features(target)
        return src_down, tgt_down, src_fpfh, tgt_fpfh

    def _execute_global_registration(self, src_down, tgt_down, src_fpfh, tgt_fpfh):
        dist_thresh = self.voxel_size_reg * 1.5
        criteria = o3d.pipelines.registration.RANSACConvergenceCriteria(100000, 0.999)
        result = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            src_down, tgt_down, src_fpfh, tgt_fpfh, True, dist_thresh,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
            [
                o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
                o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(dist_thresh),
            ], criteria)
        return result

    def _refine_registration(self, source, target, init_trans):
        dist_thresh = self.voxel_size_reg * 0.4
        return o3d.pipelines.registration.registration_icp(
            source, target, dist_thresh, init_trans,
            o3d.pipelines.registration.TransformationEstimationPointToPlane())

    @staticmethod
    def _merge_cloud_list(clouds):
        merged = o3d.geometry.PointCloud()
        for c in clouds:
            merged += c
        return merged

    def run_registration(self, point_clouds):
        if not point_clouds:
            return

        target = point_clouds[0]
        registered_pc_list = [target]
        sliding_clouds = []

        for i in range(1, len(point_clouds)):
            print(f"[REGISTRATION] Tracking Map Progress: Frame {i} / {len(point_clouds)-1}")
            source = point_clouds[i]

            src_down, tgt_down, src_fpfh, tgt_fpfh = self._prepare_dataset(source, target)
            res_global = self._execute_global_registration(src_down, tgt_down, src_fpfh, tgt_fpfh)
            res_local  = self._refine_registration(source, target, res_global.transformation)
            transformation = res_local.transformation

            trans_path = os.path.join(self.output_trans_dir, f"transformation_{i-1}.npy")
            np.save(trans_path, transformation)

            source_aligned = copy.deepcopy(source).transform(transformation)
            registered_pc_list.append(source_aligned)

            if len(registered_pc_list) > self.window_size:
                registered_pc_list.pop(0)

            target = self._merge_cloud_list(registered_pc_list)
            target = target.voxel_down_sample(self.voxel_size_merge)
            sliding_clouds.append(copy.deepcopy(target))
            target = copy.deepcopy(target)

        print("[IO] Writing full global workspace composition...")
        global_map = self._merge_cloud_list(sliding_clouds)
        global_map = global_map.voxel_down_sample(self.sliding_voxel_size)

        # Nommage dynamique (format data_JJ-MM-AAAA ou data_AAAA-MM-JJ)
        match_nouveau = re.search(r"data_(\d{2})-(\d{2})-(\d{4})", self.output_ply_dir)
        match_ancien  = re.search(r"data_(\d{4})-(\d{2})-(\d{2})", self.output_ply_dir)

        if match_nouveau:
            jour, mois, annee = match_nouveau.groups()
            nom_fichier = f"Piquet(s)_merged_{jour}_{mois}_{annee}.ply"
        elif match_ancien:
            annee, mois, jour = match_ancien.groups()
            nom_fichier = f"Piquet(s)_merged_{jour}_{mois}_{annee}.ply"
        else:
            nom_fichier = "Piquet(s)_merged_date_inconnue.ply"

        final_out_path = os.path.join(self.output_ply_dir, nom_fichier)
        o3d.io.write_point_cloud(final_out_path, global_map)
        print(f"Full Pipeline Execution Finished! Final Map Output: {final_out_path}")


class ViticultureReconstructionPipeline:

    def __init__(self, input_dir, output_ply_dir, output_trans_dir):
        self.input_dir        = input_dir
        self.output_ply_dir   = output_ply_dir
        self.output_trans_dir = output_trans_dir

        self.filter_module = PointCloudFilter()   # paramètres depuis pipeline_config.json
        self.registration_module = SlidingWindowRegistration(
            output_ply_dir   = self.output_ply_dir,
            output_trans_dir = self.output_trans_dir,
        )

    def execute(self):
        os.makedirs(self.output_ply_dir,   exist_ok=True)
        os.makedirs(self.output_trans_dir, exist_ok=True)

        print(" PHASE 1: DENOISING & DBSCAN FILTERING")
        memory_clouds = self.filter_module.process_folder(self.input_dir)

        if not memory_clouds:
            print("!!! Pipeline Terminated: No structures passed pre-filtering configurations.")
            return

        print("\n PHASE 2: SLIDING WINDOW GLOBAL-TO-LOCAL ALIGNMENT")
        self.registration_module.run_registration(memory_clouds)


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN (usage autonome : python3 all_pipeline_3D.py --input ... --output ...)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  required=True, help="Dossier PLY bruts")
    parser.add_argument("--output", required=True, help="Dossier de sortie")
    args = parser.parse_args()

    pipeline = ViticultureReconstructionPipeline(
        input_dir        = args.input,
        output_ply_dir   = args.output,
        output_trans_dir = args.output,
    )
    pipeline.execute()