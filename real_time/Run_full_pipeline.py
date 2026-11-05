#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║      FULL PIPELINE LAUNCHER   —  Extraction + 3D Reconstruction          ║
║                                                                          ║
║  Deux modes de lancement :                                               ║
║                                                                          ║
║  MODE 1 — Automatique (recommandé) :                                     ║
║      python3 generate_pied_jobs.py                                       ║
║      → BAG_JOBS injecté automatiquement, main() appelé directement       ║
║      → Aucune modification de ce fichier nécessaire                      ║
║                                                                          ║
║  MODE 2 — Autonome (BAG_JOBS statique) :                                 ║
║      python3 Run_full_pipeline.py                                        ║
║      → Utilise la liste BAG_JOBS définie en SECTION 2                    ║
║                                                                          ║
║  Pour chaque job :                                                       ║
║    ① Génère auto_extractor.cpp paramétré → compile → extrait PLY        ║
║    ② Lance all_pipeline_3D.py → nuage 3D final par pied                 ║
║                                                                          ║
║  Sorties :                                                               ║
║    Extraction_Output/<job_name>/data_DD-MM-YYYY/PointCloud_ply/          ║
║    final_Output/<job_name>/data_DD-MM-YYYY/PointCloud_ply/               ║
║                               Pied_merged_DD_MM_YYYY.ply  ← résultat     ║
╚══════════════════════════════════════════════════════════════════════════╝
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1 — CHEMINS RACINES  (adapter à votre machine)
# ══════════════════════════════════════════════════════════════════════════════

# ── Lecture pipeline_config.json ────────────────────────────────────────────
_RUN_DIR     = Path(__file__).resolve().parent
_CONFIG_PATH = _RUN_DIR / "pipeline_config.json"

def _load_cfg() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "r", encoding="utf-8") as _f:
            return json.load(_f)
    print("[WARN] pipeline_config.json introuvable → chemins par défaut")
    return {}

_CFG = _load_cfg()
def _p(section, key, default):
    return _CFG.get(section, {}).get(key, default)

PROJECT_ROOT           = _RUN_DIR
BAG_INPUT_DIR          = PROJECT_ROOT / _p("paths", "bag_input_dir",      "realsense_data")
EXTRACTION_OUTPUT_BASE = PROJECT_ROOT / _p("paths", "extraction_output",  "Extraction_Output")
FINAL_OUTPUT_BASE      = PROJECT_ROOT / _p("paths", "final_output",       "final_Output")
BUILD_DIR              = PROJECT_ROOT / _p("paths", "build_dir",          "build")

_cpp = _CFG.get("extractor_cpp", {})
DEFAULT_CPP_Z_MIN = _cpp.get("z_min",           0.1)
DEFAULT_CPP_Z_MAX = _cpp.get("z_max",           0.5)
DEFAULT_CPP_ROT_X = _cpp.get("rot_x_deg",       0.0)
DEFAULT_CPP_ROT_Z = _cpp.get("rot_z_deg",       270.0)
ROT_Y_PASS2       = _cpp.get("rot_y_deg_pass2", 180.0)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2 — BAG_JOBS STATIQUE
#
#  En mode automatique (generate_pied_jobs_.py), cette liste est REMPLACÉE
# ══════════════════════════════════════════════════════════════════════════════

BAG_JOBS = [
    # Exemple 
    {
        "job_name":           "exemple_statique",
        "bag_file":           str(BAG_INPUT_DIR / "RealSense_17-07-2025.bag"),
        "ubx_file":           str(BAG_INPUT_DIR / "trk_20250717_0928.ubx"),
        "z_min":              0.0,
        "z_max":              1.5,
        "start_frame":        670,
        "end_frame":          715,
        "rot_x_deg":          0.0,
        "rot_y_deg":          0.0,
        "rot_z_deg":          270.0,
        "min_distance":       0.4,
        "max_distance":       1.5,
        "filter_mode":        "z",
        "rotate_output":      True,
        "voxel_size":         0.005,
        "use_statistical":    False,
        "use_radius_filter":  True,
        "eps":                0.12,
        "min_points":         10,
        "min_cluster_size":   500,
        "nb_neighbors":       10,
        "std_ratio":          1.5,
        "radius":             0.1,
        "min_radius_points":  10,
        "voxel_size_reg":     0.02,
        "window_size":        4,
        "voxel_size_merge":   0.005,
        "sliding_voxel_size": 0.01,
    },
]

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3 — TEMPLATE C++
# ══════════════════════════════════════════════════════════════════════════════

CPP_TEMPLATE = r"""
#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <chrono>
#include <iomanip>
#include <filesystem>
#include <regex>
#include <cmath>

#include <librealsense2/rs.hpp>
#include <opencv2/opencv.hpp>
#include <pcl/point_types.h>
#include <pcl/point_cloud.h>
#include <pcl/io/ply_io.h>
#include <pcl/common/transforms.h>
#include <pcl/filters/passthrough.h>
#include <proj.h>

namespace fs = std::filesystem;

class AutoExtractor {{
private:
    std::string bag_file;
    std::string ubx_file;
    std::string base_output_dir;
    std::string output_dir;

    double z_min, z_max;
    int start_frame, end_frame;
    float rot_x_deg, rot_y_deg, rot_z_deg;

    pcl::PointCloud<pcl::PointXYZRGB>::Ptr rsPointsToPCL(const rs2::points& points, const rs2::video_frame& color) {{
        pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
        auto sp = points.get_profile().as<rs2::video_stream_profile>();
        cloud->width  = sp.width();
        cloud->height = sp.height();
        cloud->is_dense = false;
        cloud->points.resize(points.size());
        auto vertices   = points.get_vertices();
        auto tex_coords = points.get_texture_coordinates();
        const uint8_t* color_data = reinterpret_cast<const uint8_t*>(color.get_data());
        for (int i = 0; i < (int)points.size(); ++i) {{
            cloud->points[i].x = vertices[i].x;
            cloud->points[i].y = vertices[i].y;
            cloud->points[i].z = vertices[i].z;
            int u = std::min(std::max(int(tex_coords[i].u * color.get_width()),  0), color.get_width()  - 1);
            int v = std::min(std::max(int(tex_coords[i].v * color.get_height()), 0), color.get_height() - 1);
            int idx = (v * color.get_width() + u) * 3;
            cloud->points[i].r = color_data[idx];
            cloud->points[i].g = color_data[idx + 1];
            cloud->points[i].b = color_data[idx + 2];
        }}
        return cloud;
    }}

public:
    AutoExtractor(std::string bag, std::string ubx, std::string out_dir,
                  double z_min, double z_max, int sf, int ef,
                  float rx, float ry, float rz)
        : bag_file(bag), ubx_file(ubx), base_output_dir(out_dir),
          z_min(z_min), z_max(z_max), start_frame(sf), end_frame(ef),
          rot_x_deg(rx), rot_y_deg(ry), rot_z_deg(rz) {{}}

    void run() {{
        std::cout << "[INFO] Demarrage du pipeline C++..." << std::endl;

        std::string bag_filename = fs::path(bag_file).stem().string();
        std::regex date_regex(R"(\d{{2,4}}-\d{{2}}-\d{{2,4}})");
        std::smatch match;
        std::string date_str = "date_inconnue";

        if (std::regex_search(bag_filename, match, date_regex)) {{
            date_str = match.str(0);
        }} else {{
            date_str = bag_filename;
        }}

        std::string session_name = "data_" + date_str;
        output_dir = base_output_dir + "/" + session_name;

        fs::create_directories(output_dir + "/RGB_image");
        fs::create_directories(output_dir + "/Depth_raw");
        fs::create_directories(output_dir + "/PointCloud_ply");

        // Signal pour Python : nom de la session créée
        std::ofstream session_file(base_output_dir + "/.last_session");
        session_file << session_name << std::endl;
        session_file.close();

        processBagStream();
        processGPSData();
        std::cout << "[INFO] Pipeline termine avec succes." << std::endl;
    }}

private:
    void processBagStream() {{
        rs2::pipeline pipe;
        rs2::config cfg;
        cfg.enable_device_from_file(bag_file, false);
        rs2::pipeline_profile profile = pipe.start(cfg);
        rs2::device dev = profile.get_device();
        if (dev.is<rs2::playback>())
            dev.as<rs2::playback>().set_real_time(false);

        rs2::align align_to(RS2_STREAM_COLOR);
        rs2::pointcloud pc;
        auto stream = profile.get_stream(RS2_STREAM_COLOR).as<rs2::video_stream_profile>();
        saveCameraParams(stream.get_intrinsics());

        std::vector<double> timestamps;
        int global_frame_count = 0;
        std::cout << "[INFO] Traitement du fichier .bag en cours..." << std::endl;

        while (true) {{
            rs2::frameset frames;
            try {{
                if (!pipe.poll_for_frames(&frames))
                    frames = pipe.wait_for_frames(5000);
            }} catch (...) {{
                std::cout << "[INFO] Fin du fichier .bag atteinte." << std::endl;
                break;
            }}
            global_frame_count++;
            if (end_frame != -1 && global_frame_count > end_frame) {{
                std::cout << "[INFO] Frame de fin atteinte (" << end_frame << ")." << std::endl;
                break;
            }}
            if (global_frame_count < start_frame) continue;

            auto aligned_frames  = align_to.process(frames);
            rs2::video_frame color_frame = aligned_frames.get_color_frame();
            rs2::depth_frame depth_frame = aligned_frames.get_depth_frame();
            if (!color_frame || !depth_frame) continue;

            timestamps.push_back(depth_frame.get_timestamp());

            cv::Mat color_mat(cv::Size(color_frame.get_width(), color_frame.get_height()),
                              CV_8UC3, (void*)color_frame.get_data(), cv::Mat::AUTO_STEP);
            cv::Mat bgr_mat;
            cv::cvtColor(color_mat, bgr_mat, cv::COLOR_RGB2BGR);
            cv::imwrite(output_dir + "/RGB_image/frame_" + std::to_string(global_frame_count) + "_rgb.png", bgr_mat);

            cv::Mat depth_mat(cv::Size(depth_frame.get_width(), depth_frame.get_height()),
                              CV_16UC1, (void*)depth_frame.get_data(), cv::Mat::AUTO_STEP);
            cv::imwrite(output_dir + "/Depth_raw/frame_" + std::to_string(global_frame_count) + "_depth.png", depth_mat);

            pc.map_to(color_frame);
            rs2::points points = pc.calculate(depth_frame);
            auto cloud = rsPointsToPCL(points, color_frame);

            pcl::PointCloud<pcl::PointXYZRGB>::Ptr filtered_cloud(new pcl::PointCloud<pcl::PointXYZRGB>());
            pcl::PassThrough<pcl::PointXYZRGB> pass;
            pass.setInputCloud(cloud);
            pass.setFilterFieldName("z");
            pass.setFilterLimits(z_min, z_max);
            pass.filter(*filtered_cloud);

            pcl::PointCloud<pcl::PointXYZRGB>::Ptr transformed_cloud(new pcl::PointCloud<pcl::PointXYZRGB>());
            Eigen::Affine3f transform = Eigen::Affine3f::Identity();
            transform.rotate(Eigen::AngleAxisf(rot_x_deg * M_PI / 180.0f, Eigen::Vector3f::UnitX()));
            transform.rotate(Eigen::AngleAxisf(rot_y_deg * M_PI / 180.0f, Eigen::Vector3f::UnitY()));
            transform.rotate(Eigen::AngleAxisf(rot_z_deg * M_PI / 180.0f, Eigen::Vector3f::UnitZ()));
            pcl::transformPointCloud(*filtered_cloud, *transformed_cloud, transform);

            pcl::io::savePLYFileBinary(
                output_dir + "/PointCloud_ply/frame_" + std::to_string(global_frame_count) + ".ply",
                *transformed_cloud);
            std::cout << " -> Frame extraite : " << global_frame_count << std::endl;
        }}
        pipe.stop();
        saveTimestampsCSV(timestamps, start_frame);
    }}

    void saveCameraParams(const rs2_intrinsics& intrin) {{
        std::ofstream f(output_dir + "/camera_intrinsics.json");
        f << "{{\n  \"width\": " << intrin.width  << ",\n  \"height\": " << intrin.height << ",\n"
          << "  \"fx\": " << intrin.fx << ",\n  \"fy\": " << intrin.fy << ",\n"
          << "  \"ppx\": " << intrin.ppx << ",\n  \"ppy\": " << intrin.ppy << "\n}}\n";
    }}

    void saveTimestampsCSV(const std::vector<double>& ts, int start_idx) {{
        std::ofstream f(output_dir + "/frame_timestamps.csv");
        f << "Frame Number,Timestamp (ms)\n";
        for (size_t i = 0; i < ts.size(); ++i)
            f << (start_idx + i) << "," << std::fixed << std::setprecision(2) << ts[i] << "\n";
    }}

    void processGPSData() {{
        std::cout << "[INFO] Extraction GPS..." << std::endl;
        std::ifstream file(ubx_file, std::ios::binary);
        if (!file.is_open()) {{ std::cerr << "[ERREUR] Impossible d'ouvrir le .ubx" << std::endl; return; }}
        std::string content((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
        file.close();

        PJ_CONTEXT* C = proj_context_create();
        PJ* P = proj_create_crs_to_crs(C, "EPSG:4326", "EPSG:2154", NULL);
        if (!P) {{ std::cerr << "[ERREUR] PROJ echouee." << std::endl; return; }}
        P = proj_normalize_for_visualization(C, P);

        std::ofstream out(output_dir + "/data_GPS.txt");
        out << "Timestamp\tLatitude\tLongitude\tX_Lambert93\tY_Lambert93\n";
        std::regex gga_regex(R"(\$(GP|GN)GGA,(\d+\.\d+),(\d+\.\d+),([NS]),(\d+\.\d+),([EW]).*)");
        for (auto it = std::sregex_iterator(content.begin(), content.end(), gga_regex);
             it != std::sregex_iterator(); ++it) {{
            auto& m = *it;
            double raw_lat = std::stod(m[3]), raw_lon = std::stod(m[5]);
            double lat_deg = std::floor(raw_lat / 100.0), lon_deg = std::floor(raw_lon / 100.0);
            double lat = lat_deg + (raw_lat - lat_deg * 100.0) / 60.0;
            double lon = lon_deg + (raw_lon - lon_deg * 100.0) / 60.0;
            if (m[4] == "S") lat = -lat;
            if (m[6] == "W") lon = -lon;
            PJ_COORD c = proj_trans(P, PJ_FWD, proj_coord(lon, lat, 0, 0));
            out << m[2] << "\t" << std::fixed << std::setprecision(8) << lat << "\t" << lon
                << "\t" << std::setprecision(3) << c.xy.x << "\t" << c.xy.y << "\n";
        }}
        proj_destroy(P); proj_context_destroy(C);
        std::cout << "[INFO] GPS termine." << std::endl;
    }}
}};

int main() {{
    AutoExtractor pipeline(
        "{bag_file}", "{ubx_file}", "{out_path}",
        {z_min}, {z_max}, {start_frame}, {end_frame},
        {rot_x_deg}f, {rot_y_deg}f, {rot_z_deg}f
    );
    pipeline.run();
    return 0;
}}
"""

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4 — UTILITAIRES CONSOLE
# ══════════════════════════════════════════════════════════════════════════════

def banner(text, char="═", width=70):
    print("\n" + char * width)
    print(f"  {text}")
    print(char * width)

def step(msg):  print(f"\n  ▶  {msg}")
def ok(msg):    print(f"  ✔  {msg}")
def err(msg):   print(f"  ✘  {msg}", file=sys.stderr)

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 5 — COMPILATION C++
# ══════════════════════════════════════════════════════════════════════════════

def patch_cmakelists(project_root: Path):
    cmake_path = project_root / "CMakeLists.txt"
    if not cmake_path.exists():
        err(f"CMakeLists.txt introuvable : {cmake_path}")
        sys.exit(1)
    content = cmake_path.read_text()
    fixed = re.sub(
        r"(add_executable\s*\(\s*auto_extractor\s+)[^\)]+(\))",
        r"\1auto_extractor.cpp\2",
        content,
    )
    if fixed != content:
        cmake_path.write_text(fixed)
        ok("CMakeLists.txt patché → auto_extractor.cpp")
    else:
        ok("CMakeLists.txt déjà correct")


def compile_extractor(build_dir: Path) -> Path:
    """Compile auto_extractor.cpp avec CMake. Retourne le chemin de l'exécutable."""
    build_dir.mkdir(parents=True, exist_ok=True)
    patch_cmakelists(PROJECT_ROOT)

    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        cache.unlink()

    step("CMake configure...")
    r = subprocess.run(
        ["cmake", str(PROJECT_ROOT), "-DCMAKE_BUILD_TYPE=Release", "-Wno-dev"],
        cwd=str(build_dir), capture_output=True, text=True,
    )
    if r.returncode != 0:
        err("CMake configure échoué :")
        print(r.stderr)
        sys.exit(1)

    step("CMake build...")
    r = subprocess.run(
        ["cmake", "--build", ".", "--parallel"],
        cwd=str(build_dir), capture_output=True, text=True,
    )
    if r.returncode != 0:
        err("CMake build échoué :")
        print(r.stdout + r.stderr)
        sys.exit(1)

    exe = build_dir / "auto_extractor"
    if not exe.exists():
        err(f"Exécutable introuvable : {exe}")
        sys.exit(1)

    ok(f"Compilation réussie → {exe}")
    return exe


def generate_cpp_for_job(job: dict, out_path: Path):
    """Écrit auto_extractor.cpp avec les paramètres du job courant."""
    code = CPP_TEMPLATE.format(
        bag_file    = job["bag_file"],
        ubx_file    = job["ubx_file"],
        out_path    = str(out_path),
        z_min       = job["z_min"],
        z_max       = job["z_max"],
        start_frame = job["start_frame"],
        end_frame   = job["end_frame"],
        rot_x_deg   = job["rot_x_deg"],
        rot_y_deg   = job["rot_y_deg"],
        rot_z_deg   = job["rot_z_deg"],
    )
    (PROJECT_ROOT / "auto_extractor.cpp").write_text(code)


def get_last_session_name(extraction_base: Path) -> str:
    """Lit .last_session écrit par le C++ (attente max 30 s)."""
    sentinel = extraction_base / ".last_session"
    for _ in range(30):
        if sentinel.exists():
            name = sentinel.read_text().strip()
            sentinel.unlink()
            return name
        time.sleep(1)
    err("Fichier .last_session introuvable.")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 6 — RECONSTRUCTION 3D
# ══════════════════════════════════════════════════════════════════════════════

def run_3d_reconstruction(job: dict, ply_input_dir: Path, output_dir: Path):
    """Charge all_pipeline_3D.py dynamiquement et reconstruit le nuage du pied."""
    spec = importlib.util.spec_from_file_location(
        "pipeline3d", str(PROJECT_ROOT / "all_pipeline_3D.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    ply_out   = output_dir / "PointCloud_ply"
    trans_out = output_dir / "Transformations"
    ply_out.mkdir(parents=True, exist_ok=True)
    trans_out.mkdir(parents=True, exist_ok=True)

    filter_mod = mod.PointCloudFilter(
        min_distance      = job["min_distance"],
        max_distance      = job["max_distance"],
        filter_mode       = job["filter_mode"],
        rotate_output     = job["rotate_output"],
        voxel_size        = job["voxel_size"],
        use_statistical   = job["use_statistical"],
        use_radius_filter = job["use_radius_filter"],
        eps               = job["eps"],
        min_points        = job["min_points"],
        min_cluster_size  = job["min_cluster_size"],
        nb_neighbors      = job["nb_neighbors"],
        std_ratio         = job["std_ratio"],
        radius            = job["radius"],
        min_radius_points = job["min_radius_points"],
    )
    reg_mod = mod.SlidingWindowRegistration(
        output_ply_dir     = str(ply_out),
        output_trans_dir   = str(trans_out),
        voxel_size_reg     = job["voxel_size_reg"],
        window_size        = job["window_size"],
        voxel_size_merge   = job["voxel_size_merge"],
        sliding_voxel_size = job["sliding_voxel_size"],
    )

    print("\n  PHASE 1 : FILTRAGE & NETTOYAGE DES NUAGES DE POINTS")
    clouds = filter_mod.process_folder(str(ply_input_dir))

    if not clouds:
        err("Aucun nuage valide — reconstruction abandonnée pour ce job.")
        return

    print("\n  PHASE 2 : ALIGNEMENT SLIDING WINDOW GLOBAL → LOCAL")
    reg_mod.run_registration(clouds)
    ok(f"Reconstruction 3D terminée → {ply_out}")

# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 7 — BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

def main():
    """
    Point d'entrée. BAG_JOBS peut être :
      • La liste statique (SECTION 2) si lancé directement
      • La liste injectée par generate_pied_jobs.py (mode automatique)
    """
    banner("PIPELINE COMPLET  —  Extraction + Reconstruction 3D par pied", "═")
    print(f"  Projet  : {PROJECT_ROOT}")
    print(f"  Nb jobs : {len(BAG_JOBS)}")

    last_bag_compiled = None   # évite de recompiler pour le même .bag
    executable = None

    for job_idx, job in enumerate(BAG_JOBS):
        job_name = job["job_name"]
        banner(f"JOB {job_idx + 1}/{len(BAG_JOBS)}  —  {job_name}", "─")

        # ── Dossiers de sortie (un dossier par job = par pied) ────────────────
        extraction_job_out = EXTRACTION_OUTPUT_BASE / job_name
        final_job_out      = FINAL_OUTPUT_BASE      / job_name
        extraction_job_out.mkdir(parents=True, exist_ok=True)
        final_job_out.mkdir(parents=True, exist_ok=True)

        # ── ÉTAPE 1 : génération du .cpp paramétré ────────────────────────────
        step(f"[1/4] Génération C++ (frames {job['start_frame']} → {job['end_frame']})")
        generate_cpp_for_job(job, extraction_job_out)
        ok("auto_extractor.cpp généré")

        # ── ÉTAPE 2 : compilation (mutualisée si même .bag) ───────────────────
        if job["bag_file"] != last_bag_compiled:
            step("[2/4] Compilation C++")
            executable = compile_extractor(BUILD_DIR)
            last_bag_compiled = job["bag_file"]
        else:
            exe = compile_extractor(BUILD_DIR)

        # ── ÉTAPE 3 : extraction C++ ──────────────────────────────────────────
        step(
            f"[3/4] Extraction  "
            f"({job['start_frame']} → {job['end_frame']})  "
            f".bag : {Path(job['bag_file']).name}"
        )
        result = subprocess.run(
            [str(executable)],
            cwd=str(PROJECT_ROOT),
            capture_output=False,
        )
        if result.returncode != 0:
            err(f"Extracteur C++ échoué pour '{job_name}' → job ignoré.")
            continue

        session_name = get_last_session_name(extraction_job_out)
        ok(f"Session : {session_name}")

        ply_input_dir = extraction_job_out / session_name / "PointCloud_ply"
        if not ply_input_dir.exists():
            err(f"PointCloud_ply introuvable : {ply_input_dir}")
            continue

        # ── ÉTAPE 4 : reconstruction 3D ───────────────────────────────────────
        final_session_out = final_job_out / session_name
        final_session_out.mkdir(parents=True, exist_ok=True)

        step(f"[4/4] Reconstruction 3D → {final_session_out}")
        run_3d_reconstruction(job, ply_input_dir, final_session_out)

        banner(f"✔  JOB '{job_name}' TERMINÉ", "─")
        print(f"  PLY bruts   → {extraction_job_out / session_name}")
        print(f"  Nuage final → {final_session_out}")

    # ── Résumé global ─────────────────────────────────────────────────────────
    banner("TOUS LES JOBS SONT TERMINÉS", "═")
    print(f"""
  Structure de sortie :

  Extraction_Output/
  └── <pied_XX_passY_DD-MM-YYYY>/
      └── data_DD-MM-YYYY/
          ├── PointCloud_ply/     ← PLY bruts extraits du .bag
          ├── RGB_image/
          ├── Depth_raw/
          ├── camera_intrinsics.json
          ├── frame_timestamps.csv
          └── data_GPS.txt

  final_Output/
  └── <pied_XX_passY_DD-MM-YYYY>/
      └── data_DD-MM-YYYY/
          ├── PointCloud_ply/
          │   └── Pied_merged_DD_MM_YYYY.ply   ← nuage 3D final du pied
          └── Transformations/
              └── transformation_N.npy
    """)


if __name__ == "__main__":
    main()