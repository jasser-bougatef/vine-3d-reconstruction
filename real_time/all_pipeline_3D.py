#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import copy
import numpy as np
import open3d as o3d
import argparse
from natsort import natsorted
from sklearn.cluster import DBSCAN

class PointCloudFilter:
    """Handles background removal, orientation corrections, denoising, and DBSCAN clustering."""
    
    def __init__(self, min_distance=0.4, max_distance=1.5, filter_mode="z", rotate_output=True,
                 voxel_size=0.01, use_statistical=False, use_radius_filter=True,
                 eps=0.12, min_points=10, min_cluster_size=500, nb_neighbors=10,
                 std_ratio=1.5, radius=0.1, min_radius_points=10):
        # Background & Rotation Parameters
        self.min_distance = min_distance
        self.max_distance = max_distance
        self.filter_mode = filter_mode
        self.rotate_output = rotate_output
        
        # Spatial Clustering & Denoising Parameters
        self.voxel_size = voxel_size
        self.use_statistical = use_statistical
        self.use_radius_filter = use_radius_filter
        self.eps = eps
        self.min_points = min_points
        self.min_cluster_size = min_cluster_size
        self.nb_neighbors = nb_neighbors
        self.std_ratio = std_ratio
        self.radius = radius
        self.min_radius_points = min_radius_points

    def _filter_background(self, pcd):
        """Isolates the target area by removing points outside the specified distance window."""
        points = np.asarray(pcd.points)
        if len(points) == 0:
            return pcd

        # Strip out non-finite entries
        valid_mask = np.isfinite(points).all(axis=1)

        # Apply spatial clipping mask
        if self.filter_mode == "z":
            depth = np.abs(points[:, 2])
            distance_mask = (depth > self.min_distance) & (depth < self.max_distance)
        elif self.filter_mode == "norm":
            distance = np.linalg.norm(points, axis=1)
            distance_mask = (distance > self.min_distance) & (distance < self.max_distance)
        else:
            raise ValueError("filter_mode must be either 'z' or 'norm'")

        combined_mask = valid_mask & distance_mask
        
        # Construct the filtered point cloud geometry
        filtered_pcd = o3d.geometry.PointCloud()
        filtered_pcd.points = o3d.utility.Vector3dVector(points[combined_mask])

        if pcd.has_colors():
            colors = np.asarray(pcd.colors)
            filtered_pcd.colors = o3d.utility.Vector3dVector(colors[combined_mask])

        return filtered_pcd

    def _rotate_point_cloud(self, pcd):
        """Applies coordinate frame rotation to realign the camera axes."""
        points = np.asarray(pcd.points)
        if len(points) == 0:
            return pcd

        rotated_points = np.zeros(points.shape)
        rotated_points[:, 0] = points[:, 1]
        rotated_points[:, 1] = -points[:, 0]
        rotated_points[:, 2] = points[:, 2]

        rotated_pcd = o3d.geometry.PointCloud()
        rotated_pcd.points = o3d.utility.Vector3dVector(rotated_points)

        if pcd.has_colors():
            rotated_pcd.colors = pcd.colors

        return rotated_pcd

    def filter_single_cloud(self, pcd):

        # # Step 1: Remove background elements
        #  pcd = self._filter_background(pcd)
        
        # # Step 2: Correct spatial rotation if enabled
        # if self.rotate_output:
        #     pcd = self._rotate_point_cloud(pcd)
            
        # Step 3: Voxel Downsampling to manage memory load
        if self.voxel_size > 0:
            pcd = pcd.voxel_down_sample(self.voxel_size)
        
        # Step 4: Outlier Denoising Filtering
        if self.use_statistical:
            pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=self.nb_neighbors, std_ratio=self.std_ratio)
        elif self.use_radius_filter:
            pcd, _ = pcd.remove_radius_outlier(nb_points=self.min_radius_points, radius=self.radius)
        
        # Step 5: DBSCAN Clustering Segmentation
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

    def process_folder(self, folder_path):
        filtered_clouds = []
        list_files = natsorted(os.listdir(folder_path))

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
                    print(f"     -> [Warning] Frame contains no points within criteria window. Skipping.")
                    
        return filtered_clouds


class SlidingWindowRegistration:
    
    def __init__(self, output_ply_dir, output_trans_dir, voxel_size_reg=0.02,
                 voxel_size_merge=0.005, window_size=4, sliding_voxel_size=0.01):
        self.output_ply_dir = output_ply_dir
        self.output_trans_dir = output_trans_dir
        self.voxel_size_reg = voxel_size_reg
        self.voxel_size_merge = voxel_size_merge
        self.window_size = window_size
        self.sliding_voxel_size = sliding_voxel_size

    def _preprocess_features(self, pcd):
        pcd_down = pcd.voxel_down_sample(self.voxel_size_reg)
        radius_normal = self.voxel_size_reg * 2
        pcd_down.estimate_normals(
            o3d.geometry.KDTreeSearchParamHybrid(radius=radius_normal, max_nn=30))

        radius_feature = self.voxel_size_reg * 5
        pcd_fpfh = o3d.pipelines.registration.compute_fpfh_feature(
            pcd_down, o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))
        return pcd_down, pcd_fpfh

    def _prepare_dataset(self, source, target):
        source.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=self.voxel_size_reg * 2, max_nn=30))
        target.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=self.voxel_size_reg * 2, max_nn=30))
        
        src_down, src_fpfh = self._preprocess_features(source)
        tgt_down, tgt_fpfh = self._preprocess_features(target)
        return src_down, tgt_down, src_fpfh, tgt_fpfh

    def _execute_global_registration(self, src_down, tgt_down, src_fpfh, tgt_fpfh):
        dist_thresh = self.voxel_size_reg * 1.5
        criteria = o3d.pipelines.registration.RANSACConvergenceCriteria(10000, 0.999)
        
        result = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
            src_down, tgt_down, src_fpfh, tgt_fpfh, True, dist_thresh,
            o3d.pipelines.registration.TransformationEstimationPointToPoint(False), 3,
            [
                o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
                o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(dist_thresh)
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
            
            # Feature extraction pairs
            src_down, tgt_down, src_fpfh, tgt_fpfh = self._prepare_dataset(source, target)
            
            # Global coarse pass
            res_global = self._execute_global_registration(src_down, tgt_down, src_fpfh, tgt_fpfh)
            
            # Local fine tuning pass
            res_local = self._refine_registration(source, target, res_global.transformation)
            transformation = res_local.transformation
            
            # Save matrix outputs
            trans_path = os.path.join(self.output_trans_dir, f"transformation_{i-1}.npy")
            np.save(trans_path, transformation)
            
            # Sliding memory calculation window tracking updates
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
        
        # ---------------- NOMMAGE DYNAMIQUE (DOUBLE FORMAT) ----------------
        # On cherche d'abord le format data_JJ-MM-AAAA
        match_nouveau = re.search(r"data_(\d{2})-(\d{2})-(\d{4})", self.output_ply_dir)
        # On cherche ensuite le format data_AAAA-MM-JJ
        match_ancien = re.search(r"data_(\d{4})-(\d{2})-(\d{2})", self.output_ply_dir)

        if match_nouveau:
            jour, mois, annee = match_nouveau.groups()
            nom_fichier = f"Pied(s)_merged_{jour}_{mois}_{annee}.ply"
        elif match_ancien:
            annee, mois, jour = match_ancien.groups()
            nom_fichier = f"Pied(s)_merged_{jour}_{mois}_{annee}.ply"
        else:
            nom_fichier = "Pied(s)_merged_date_inconnue.ply"
            
        final_out_path = os.path.join(self.output_ply_dir, nom_fichier)
        o3d.io.write_point_cloud(final_out_path, global_map)
        print(f"Full Pipeline Execution Finished! Final Map Output: {final_out_path}")
        # -------------------------------------------------------------------


class ViticultureReconstructionPipeline:
    
    def __init__(self, input_dir, output_ply_dir, output_trans_dir):
        self.input_dir = input_dir
        self.output_ply_dir = output_ply_dir
        self.output_trans_dir = output_trans_dir
        
        # Instantiate system module classes with designated variables
        self.filter_module = PointCloudFilter(
            min_distance=0.4,       # Your distance thresholds
            max_distance=1.5,
            filter_mode="z",        # Filter depth axis from camera
            rotate_output=True,     # Reorient axes before registration
            voxel_size=0.005,       
            use_statistical=False, 
            use_radius_filter=True
        )
        self.registration_module = SlidingWindowRegistration(
            output_ply_dir=self.output_ply_dir,
            output_trans_dir=self.output_trans_dir,
            voxel_size_reg=0.02,
            window_size=4
        )
        
    def execute(self):
        os.makedirs(self.output_ply_dir, exist_ok=True)
        os.makedirs(self.output_trans_dir, exist_ok=True)

        print(" PHASE 1: BACKGROUND CLEANING & EXTRACTION FILTERING")

        memory_clouds = self.filter_module.process_folder(self.input_dir)
        
        if not memory_clouds:
            print("!!!!!!!!! Pipeline Terminated: No structures passed pre-filtering configurations.")
            return
            
        print("\n")
        print(" PHASE 2: SLIDING WINDOW GLOBAL-TO-LOCAL ALIGNMENT")

        self.registration_module.run_registration(memory_clouds)


######### Main_fun ############


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    pipeline = ViticultureReconstructionPipeline(
        input_dir=args.input,
        output_ply_dir=args.output,
        output_trans_dir=args.output
    )

    pipeline.execute()