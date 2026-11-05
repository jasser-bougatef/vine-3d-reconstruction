// ╔══════════════════════════════════════════════════════════════════════════╗
// ║  auto_extractor.cpp  —  Version argc/argv (compile-once / runtime-args)  ║
// ║                                                                          ║
// ║  Tous les paramètres sont passés en ligne de commande :                  ║
// ║    --bag    <path>   fichier .bag ou .db3 RealSense                      ║
// ║    --ubx    <path>   fichier .ubx GPS                                    ║
// ║    --out    <path>   dossier de sortie racine                            ║
// ║    --start  <int>    frame de début (défaut 1)                           ║
// ║    --end    <int>    frame de fin  (-1 = jusqu'à la fin)                 ║
// ║    --z-min  <float>  filtre profondeur min (défaut 0.0)                  ║
// ║    --z-max  <float>  filtre profondeur max (défaut 1.5)                  ║
// ║    --rot-x  <float>  rotation X degrés (défaut 0.0)                      ║
// ║    --rot-y  <float>  rotation Y degrés (défaut 0.0)                      ║
// ║    --rot-z  <float>  rotation Z degrés (défaut 270.0)                    ║
// ║                                                                          ║
// ║  Exemple :                                                               ║
// ║    ./build/auto_extractor                                                ║
// ║        --bag  realsense_data/RealSense_30-06-2026.db3                    ║
// ║        --ubx  realsense_data/trk_20260630_0928.ubx                       ║
// ║        --out  Extraction_Output/pied05_pass1_30-06-2026                  ║
// ║        --start 312 --end 358                                             ║
// ║        --z-min 0.0 --z-max 1.5 --rot-z 270.0                             ║
// ╚══════════════════════════════════════════════════════════════════════════╝

#include <iostream>
#include <fstream>
#include <string>
#include <vector>
#include <chrono>
#include <iomanip>
#include <filesystem>
#include <regex>
#include <cmath>
#include <cstring>
#include <cstdlib>

#include <librealsense2/rs.hpp>
#include <opencv2/opencv.hpp>
#include <pcl/point_types.h>
#include <pcl/point_cloud.h>
#include <pcl/io/ply_io.h>
#include <pcl/common/transforms.h>
#include <pcl/filters/passthrough.h>
#include <proj.h>

namespace fs = std::filesystem;

// ══════════════════════════════════════════════════════════════════════════════
//  PARSING DES ARGUMENTS
// ══════════════════════════════════════════════════════════════════════════════

struct Args {
    std::string bag_file;
    std::string ubx_file;
    std::string out_dir;
    int    start_frame = 1;
    int    end_frame   = -1;
    double z_min       = 0.0;
    double z_max       = 1.5;
    float  rot_x_deg   = 0.0f;
    float  rot_y_deg   = 0.0f;
    float  rot_z_deg   = 270.0f;
};

static void print_usage(const char* prog) {
    std::cout
        << "\nUsage: " << prog << " [options]\n\n"
        << "  --bag    <path>   Fichier .bag ou .db3  [REQUIS]\n"
        << "  --ubx    <path>   Fichier .ubx GPS      [REQUIS]\n"
        << "  --out    <path>   Dossier de sortie     [REQUIS]\n"
        << "  --start  <int>    Frame de debut        [defaut: 1]\n"
        << "  --end    <int>    Frame de fin (-1=tout)[defaut: -1]\n"
        << "  --z-min  <float>  Filtre Z min          [defaut: 0.0]\n"
        << "  --z-max  <float>  Filtre Z max          [defaut: 1.5]\n"
        << "  --rot-x  <float>  Rotation X (degres)   [defaut: 0.0]\n"
        << "  --rot-y  <float>  Rotation Y (degres)   [defaut: 0.0]\n"
        << "  --rot-z  <float>  Rotation Z (degres)   [defaut: 270.0]\n"
        << "  --help            Affiche cette aide\n\n";
}

static Args parse_args(int argc, char** argv) {
    Args a;
    if (argc == 1) { print_usage(argv[0]); exit(0); }

    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        if (key == "--help" || key == "-h") { print_usage(argv[0]); exit(0); }

        auto next = [&]() -> std::string {
            if (i + 1 >= argc) {
                std::cerr << "[ERREUR] Argument manquant apres " << key << "\n";
                exit(1);
            }
            return argv[++i];
        };

        if      (key == "--bag")   a.bag_file   = next();
        else if (key == "--ubx")   a.ubx_file   = next();
        else if (key == "--out")   a.out_dir     = next();
        else if (key == "--start") a.start_frame = std::stoi(next());
        else if (key == "--end")   a.end_frame   = std::stoi(next());
        else if (key == "--z-min") a.z_min       = std::stod(next());
        else if (key == "--z-max") a.z_max       = std::stod(next());
        else if (key == "--rot-x") a.rot_x_deg   = std::stof(next());
        else if (key == "--rot-y") a.rot_y_deg   = std::stof(next());
        else if (key == "--rot-z") a.rot_z_deg   = std::stof(next());
        else {
            std::cerr << "[ERREUR] Argument inconnu : " << key << "\n";
            print_usage(argv[0]);
            exit(1);
        }
    }

    bool ok = true;
    if (a.bag_file.empty()) { std::cerr << "[ERREUR] --bag requis\n"; ok = false; }
    if (a.ubx_file.empty()) { std::cerr << "[ERREUR] --ubx requis\n"; ok = false; }
    if (a.out_dir.empty())  { std::cerr << "[ERREUR] --out requis\n"; ok = false; }
    if (!ok) { print_usage(argv[0]); exit(1); }
    return a;
}

// ══════════════════════════════════════════════════════════════════════════════
//  CLASSE PRINCIPALE
// ══════════════════════════════════════════════════════════════════════════════

class AutoExtractor {
private:
    std::string bag_file, ubx_file, base_output_dir, output_dir;
    double z_min, z_max;
    int    start_frame, end_frame;
    float  rot_x_deg, rot_y_deg, rot_z_deg;

    pcl::PointCloud<pcl::PointXYZRGB>::Ptr rsPointsToPCL(
            const rs2::points& points, const rs2::video_frame& color) {

        pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud(
            new pcl::PointCloud<pcl::PointXYZRGB>);
        auto sp = points.get_profile().as<rs2::video_stream_profile>();
        cloud->width    = sp.width();
        cloud->height   = sp.height();
        cloud->is_dense = false;
        cloud->points.resize(points.size());
        auto verts      = points.get_vertices();
        auto tex        = points.get_texture_coordinates();
        const uint8_t* cdata = reinterpret_cast<const uint8_t*>(color.get_data());
        for (int i = 0; i < (int)points.size(); ++i) {
            cloud->points[i].x = verts[i].x;
            cloud->points[i].y = verts[i].y;
            cloud->points[i].z = verts[i].z;
            int u = std::min(std::max(int(tex[i].u * color.get_width()),  0), color.get_width()  - 1);
            int v = std::min(std::max(int(tex[i].v * color.get_height()), 0), color.get_height() - 1);
            int idx = (v * color.get_width() + u) * 3;
            cloud->points[i].r = cdata[idx];
            cloud->points[i].g = cdata[idx + 1];
            cloud->points[i].b = cdata[idx + 2];
        }
        return cloud;
    }

public:
    AutoExtractor(const Args& a)
        : bag_file(a.bag_file), ubx_file(a.ubx_file),
          base_output_dir(a.out_dir),
          z_min(a.z_min), z_max(a.z_max),
          start_frame(a.start_frame), end_frame(a.end_frame),
          rot_x_deg(a.rot_x_deg), rot_y_deg(a.rot_y_deg), rot_z_deg(a.rot_z_deg) {}

    void run() {
        std::cout << "[INFO] ════════════════════════════════════════════\n";
        std::cout << "[INFO]  AutoExtractor — parametres runtime\n";
        std::cout << "[INFO]  bag    : " << bag_file   << "\n";
        std::cout << "[INFO]  ubx    : " << ubx_file   << "\n";
        std::cout << "[INFO]  out    : " << base_output_dir << "\n";
        std::cout << "[INFO]  frames : " << start_frame
                  << " -> " << (end_frame == -1 ? "fin" : std::to_string(end_frame)) << "\n";
        std::cout << "[INFO]  z      : [" << z_min << ", " << z_max << "]\n";
        std::cout << "[INFO]  rot    : X=" << rot_x_deg
                  << " Y=" << rot_y_deg << " Z=" << rot_z_deg << "\n";
        std::cout << "[INFO] ════════════════════════════════════════════\n";

        // Détecter la date depuis le nom du fichier
        std::string stem     = fs::path(bag_file).stem().string();
        std::regex  date_re(R"(\d{2,4}-\d{2}-\d{2,4})");
        std::smatch m;
        std::string date_str = std::regex_search(stem, m, date_re)
                               ? m.str(0) : stem;

        std::string session_name = "data_" + date_str;
        output_dir = base_output_dir + "/" + session_name;

        fs::create_directories(output_dir + "/RGB_image");
        fs::create_directories(output_dir + "/Depth_raw");
        fs::create_directories(output_dir + "/PointCloud_ply");

        // Signal .last_session pour Python
        std::ofstream sf(base_output_dir + "/.last_session");
        sf << session_name << "\n";
        sf.close();

        std::cout << "[INFO] Session : " << session_name << "\n";
        processBagStream();
        processGPSData();
        std::cout << "[INFO] OK — pipeline termine.\n";
    }

private:
    void processBagStream() {
        rs2::pipeline      pipe;
        rs2::config        cfg;
        cfg.enable_device_from_file(bag_file, false);
        rs2::pipeline_profile profile = pipe.start(cfg);

        rs2::device dev = profile.get_device();
        if (dev.is<rs2::playback>())
            dev.as<rs2::playback>().set_real_time(false);

        rs2::align      align_to(RS2_STREAM_COLOR);
        rs2::pointcloud pc;

        auto stream = profile.get_stream(RS2_STREAM_COLOR)
                             .as<rs2::video_stream_profile>();
        saveCameraParams(stream.get_intrinsics());

        std::vector<double> timestamps;
        int global = 0, extracted = 0;
        std::cout << "[INFO] Traitement frames " << start_frame
                  << " -> " << (end_frame == -1 ? "fin" : std::to_string(end_frame))
                  << "\n";

        while (true) {
            rs2::frameset frames;
            try {
                if (!pipe.poll_for_frames(&frames))
                    frames = pipe.wait_for_frames(5000);
            } catch (...) {
                std::cout << "[INFO] Fin du fichier atteinte.\n";
                break;
            }
            global++;
            if (end_frame != -1 && global > end_frame) break;
            if (global < start_frame) continue;

            auto aligned = align_to.process(frames);
            rs2::video_frame color_frame = aligned.get_color_frame();
            rs2::depth_frame depth_frame = aligned.get_depth_frame();
            if (!color_frame || !depth_frame) continue;

            timestamps.push_back(depth_frame.get_timestamp());

            // RGB
            cv::Mat col_mat(cv::Size(color_frame.get_width(), color_frame.get_height()),
                            CV_8UC3, (void*)color_frame.get_data(), cv::Mat::AUTO_STEP);
            cv::Mat bgr;
            cv::cvtColor(col_mat, bgr, cv::COLOR_RGB2BGR);
            cv::imwrite(output_dir + "/RGB_image/frame_" +
                        std::to_string(global) + "_rgb.png", bgr);

            // Depth
            cv::Mat dep_mat(cv::Size(depth_frame.get_width(), depth_frame.get_height()),
                            CV_16UC1, (void*)depth_frame.get_data(), cv::Mat::AUTO_STEP);
            cv::imwrite(output_dir + "/Depth_raw/frame_" +
                        std::to_string(global) + "_depth.png", dep_mat);

            // PLY
            pc.map_to(color_frame);
            rs2::points pts = pc.calculate(depth_frame);
            auto cloud = rsPointsToPCL(pts, color_frame);

            // Filtre Z
            pcl::PointCloud<pcl::PointXYZRGB>::Ptr filtered(
                new pcl::PointCloud<pcl::PointXYZRGB>());
            pcl::PassThrough<pcl::PointXYZRGB> pass;
            pass.setInputCloud(cloud);
            pass.setFilterFieldName("z");
            pass.setFilterLimits((float)z_min, (float)z_max);
            pass.filter(*filtered);

            // Rotation
            pcl::PointCloud<pcl::PointXYZRGB>::Ptr rotated(
                new pcl::PointCloud<pcl::PointXYZRGB>());
            Eigen::Affine3f T = Eigen::Affine3f::Identity();
            T.rotate(Eigen::AngleAxisf(rot_x_deg * M_PI / 180.0f, Eigen::Vector3f::UnitX()));
            T.rotate(Eigen::AngleAxisf(rot_y_deg * M_PI / 180.0f, Eigen::Vector3f::UnitY()));
            T.rotate(Eigen::AngleAxisf(rot_z_deg * M_PI / 180.0f, Eigen::Vector3f::UnitZ()));
            pcl::transformPointCloud(*filtered, *rotated, T);

            pcl::io::savePLYFileBinary(
                output_dir + "/PointCloud_ply/frame_" +
                std::to_string(global) + ".ply", *rotated);

            extracted++;
            std::cout << " -> Frame " << global
                      << "  (" << rotated->size() << " pts)\n";
        }

        pipe.stop();
        saveTimestampsCSV(timestamps, start_frame);
        std::cout << "[INFO] " << extracted << " frames extraites.\n";
    }

    void saveCameraParams(const rs2_intrinsics& in) {
        std::ofstream f(output_dir + "/camera_intrinsics.json");
        f << "{\n"
          << "  \"width\":"  << in.width  << ",\n"
          << "  \"height\":" << in.height << ",\n"
          << "  \"fx\":"     << in.fx     << ",\n"
          << "  \"fy\":"     << in.fy     << ",\n"
          << "  \"ppx\":"    << in.ppx    << ",\n"
          << "  \"ppy\":"    << in.ppy    << "\n}\n";
    }

    void saveTimestampsCSV(const std::vector<double>& ts, int start_idx) {
        std::ofstream f(output_dir + "/frame_timestamps.csv");
        f << "Frame Number,Timestamp (ms)\n";
        for (size_t i = 0; i < ts.size(); ++i)
            f << (start_idx + (int)i) << ","
              << std::fixed << std::setprecision(2) << ts[i] << "\n";
    }

    void processGPSData() {
        std::cout << "[INFO] Extraction GPS depuis " << ubx_file << "\n";
        std::ifstream file(ubx_file, std::ios::binary);
        if (!file.is_open()) {
            std::cerr << "[ERREUR] Impossible d'ouvrir : " << ubx_file << "\n";
            return;
        }
        std::string content((std::istreambuf_iterator<char>(file)),
                             std::istreambuf_iterator<char>());
        file.close();

        PJ_CONTEXT* C = proj_context_create();
        PJ* P = proj_create_crs_to_crs(C, "EPSG:4326", "EPSG:2154", nullptr);
        if (!P) {
            std::cerr << "[ERREUR] Projection PROJ echouee.\n";
            proj_context_destroy(C);
            return;
        }
        P = proj_normalize_for_visualization(C, P);

        std::ofstream out(output_dir + "/data_GPS.txt");
        out << "Timestamp\tLatitude\tLongitude\tX_Lambert93\tY_Lambert93\n";

        std::regex gga_re(R"(\$(GP|GN)GGA,(\d+\.\d+),(\d+\.\d+),([NS]),(\d+\.\d+),([EW]).*)");
        int cnt = 0;
        for (auto it = std::sregex_iterator(content.begin(), content.end(), gga_re);
             it != std::sregex_iterator(); ++it, ++cnt) {
            auto& m     = *it;
            double rl   = std::stod(m[3]), rlo = std::stod(m[5]);
            double ld   = std::floor(rl  / 100), lod = std::floor(rlo / 100);
            double lat  = ld  + (rl  - ld  * 100) / 60;
            double lon  = lod + (rlo - lod * 100) / 60;
            if (m[4] == "S") lat = -lat;
            if (m[6] == "W") lon = -lon;
            PJ_COORD c  = proj_trans(P, PJ_FWD, proj_coord(lon, lat, 0, 0));
            out << m[2] << "\t"
                << std::fixed << std::setprecision(8) << lat << "\t" << lon << "\t"
                << std::setprecision(3) << c.xy.x << "\t" << c.xy.y << "\n";
        }

        proj_destroy(P);
        proj_context_destroy(C);
        std::cout << "[INFO] " << cnt << " points GPS extraits.\n";
    }
};

// ══════════════════════════════════════════════════════════════════════════════
//  POINT D'ENTRÉE
// ══════════════════════════════════════════════════════════════════════════════

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);
    AutoExtractor extractor(args);
    extractor.run();
    return 0;
}