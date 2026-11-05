#include <iostream>
#include <string>
#include <vector>
#include <map>
#include <fstream>
#include <sstream>
#include <chrono>
#include <iomanip>
#include <filesystem>
#include <cmath>
#include <algorithm>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <atomic>

// Définition de M_PI si non présente (pour la compatibilité)
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// RealSense
#include <librealsense2/rs.hpp>

// OpenCV
#include <opencv2/opencv.hpp>

// PCL
#include <pcl/io/ply_io.h>
#include <pcl/io/pcd_io.h>
#include <pcl/point_types.h>
#include <pcl/common/centroid.h>
#include <pcl/common/transforms.h>
#include <pcl/filters/passthrough.h>

namespace fs = std::filesystem;

// Structure équivalente au dictionnaire de configuration Python
struct Config {
    std::string bag_file;
    std::string output_dir;
    std::string output_created;
    bool save_rgb = false;
    bool save_depth_raw = false;
    bool save_depth_normalized = false;
    bool save_pcd = false;
    bool save_ply = false;
    int rotation_angle = 0; // 0, 90, 180, 270/-90 (rotation Z partagée images + nuage)

    // Rotations propres au nuage de points (en degrés), appliquées dans
    // finalize_cloud. Défauts = correction d'orientation observée sur la sortie.
    double rotation_x = 0.0;
    double rotation_y = 180.0;
    double rotation_z = 180.0;

    // Si true (DÉFAUT) : le nuage sauvegardé reste dans le repère caméra brut
    // (origine = caméra), comme la version Python d'origine. On n'applique ni
    // centrage sur le centroïde ni rotation. Le filtre Z (distance caméra)
    // reste appliqué. Mettre à false pour réactiver le centrage + les rotations
    // configurables (rotation_x/y/z ci-dessus).
    bool keep_camera_frame = true;

    // [PIPELINE VIGNE] Centrage sur le centroïde — DÉSACTIVÉ par défaut.
    // IMPORTANT : le pipeline de reconstruction (SlidingWindowRegistration)
    // recale les frames les unes par rapport aux autres par ICP/RANSAC ; si
    // chaque frame est recentrée sur son propre centroïde, le mouvement
    // relatif caméra/pied entre deux frames consécutives est détruit et la
    // registration échoue. Le centrage est donc désormais un flag séparé et
    // indépendant de la rotation (contrairement à l'ancien auto_extractor.cpp
    // où --no-keep-camera-frame activait les deux en même temps).
    bool center_cloud = false;
    bool generate_timestamps = false;
    bool save_camera_params = false;
    
    // Paramètres pour le traitement PCL du fichier PLY
    double z_min = 0.0;
    double z_max = 1.5;

    // Paramètres de sélection de frames
    int start_frame = 1;     // Frame de début (1-based)
    int end_frame = -1;      // Frame de fin (-1 pour extraire jusqu'à la fin)

    // Nombre de threads workers (0 = automatique, basé sur le nombre de cœurs)
    int num_threads = 0;
};

// ============================================================================
// FILE BORNÉE THREAD-SAFE (producteur unique / consommateurs multiples)
// ============================================================================
template <typename T>
class BoundedQueue {
public:
    explicit BoundedQueue(size_t max_size) : max_size_(max_size) {}

    // Bloque si la file est pleine (backpressure → mémoire maîtrisée)
    void push(T item) {
        std::unique_lock<std::mutex> lock(mutex_);
        not_full_.wait(lock, [this] { return queue_.size() < max_size_; });
        queue_.push(std::move(item));
        not_empty_.notify_one();
    }

    // Renvoie false si la file est fermée ET vide (plus rien à traiter)
    bool pop(T& item) {
        std::unique_lock<std::mutex> lock(mutex_);
        not_empty_.wait(lock, [this] { return !queue_.empty() || closed_; });
        if (queue_.empty()) return false;
        item = std::move(queue_.front());
        queue_.pop();
        not_full_.notify_one();
        return true;
    }

    void close() {
        std::unique_lock<std::mutex> lock(mutex_);
        closed_ = true;
        not_empty_.notify_all();
    }

private:
    std::queue<T> queue_;
    std::mutex mutex_;
    std::condition_variable not_full_;
    std::condition_variable not_empty_;
    size_t max_size_;
    bool closed_ = false;
};

// Données d'une frame, copiées hors des buffers RealSense (qui sont recyclés),
// transmises du thread lecteur aux threads workers.
struct FrameJob {
    int frame_index = 0;
    cv::Mat color_image;     // RGB (vide si non demandé)
    cv::Mat depth_image;     // profondeur brute 16 bits (vide si non demandé)
    cv::Mat depth_colormap;  // profondeur colorisée (vide si non demandé)
    pcl::PointCloud<pcl::PointXYZRGB>::Ptr raw_cloud; // nuage brut (null si non demandé)
};

// ============================================================================
// IMAGE EXTRACTOR CLASS
// ============================================================================
class ImageExtractor {
private:
    Config& config;
    std::map<std::string, std::string> directories;

    // Transformations caméra -> nuage sauvegardé, collectées par les workers.
    // (frame_index, matrice 4x4). Protégées par un mutex car remplies en parallèle.
    std::mutex transforms_mutex_;
    std::vector<std::pair<int, Eigen::Matrix4f>> transforms_;

public:
    ImageExtractor(Config& cfg) : config(cfg) {}

    // Valide l'existence du fichier source
    void validate() {
        if (!fs::exists(config.bag_file) || !fs::is_regular_file(config.bag_file)) {
            throw std::runtime_error("Bag file not found: " + config.bag_file);
        }
        if (!config.output_created.empty() && !fs::exists(config.output_created)) {
            fs::create_directories(config.output_created);
        }
    }

    // Prépare l'arborescence des dossiers de sortie
    void setup_directories() {
        if (config.save_rgb) {
            directories["rgb"] = (fs::path(config.output_created) / "RGB_image").string();
            fs::create_directories(directories["rgb"]);
        }
        if (config.save_depth_raw) {
            directories["depth_raw"] = (fs::path(config.output_created) / "Depth_raw").string();
            fs::create_directories(directories["depth_raw"]);
        }
        if (config.save_depth_normalized) {
            directories["depth_normalized"] = (fs::path(config.output_created) / "Depth_image").string();
            fs::create_directories(directories["depth_normalized"]);
        }
        if (config.save_ply) {
            directories["ply"] = (fs::path(config.output_created) / "PointCloud_ply").string();
            fs::create_directories(directories["ply"]);
        }
        if (config.save_pcd) {
            directories["pcd"] = (fs::path(config.output_created) / "PointCloud_pcd").string();
            fs::create_directories(directories["pcd"]);
        }
    }

    void extract_camera_parameters(const rs2::pipeline_profile& pipeline_profile) {
        if (!config.save_camera_params) return;

        auto rgb_stream = pipeline_profile.get_stream(RS2_STREAM_COLOR).as<rs2::video_stream_profile>();
        auto intrinsics = rgb_stream.get_intrinsics();

        std::string param_file = (fs::path(config.output_created) / "camera_intrinsics.json").string();
        std::ofstream f(param_file);
        if (f.is_open()) {
            f << "{\n"
              << "    \"fx\": " << intrinsics.fx << ",\n"
              << "    \"fy\": " << intrinsics.fy << ",\n"
              << "    \"cx\": " << intrinsics.ppx << ",\n"
              << "    \"cy\": " << intrinsics.ppy << ",\n"
              << "    \"width\": " << intrinsics.width << ",\n"
              << "    \"height\": " << intrinsics.height << "\n"
              << "}";
            f.close();
            std::cout << "Camera parameters saved to " << param_file << std::endl;
        }
    }

    void save_frame_timestamps(const std::vector<double>& timestamps) {
        if (!config.generate_timestamps) return;

        std::string timestamp_file = (fs::path(config.output_created) / "frame_timestamps.csv").string();
        std::ofstream f(timestamp_file);
        if (f.is_open()) {
            f << "Frame Number,Timestamp (ms)\n";
            for (size_t i = 0; i < timestamps.size(); ++i) {
                f << (i + 1) << "," << std::fixed << std::setprecision(3) << timestamps[i] << "\n";
            }
            f.close();
            std::cout << "Timestamps saved to " << timestamp_file << std::endl;
        }
    }

    // Écrit frame_transforms.csv : une ligne par frame avec la matrice 4x4
    // (caméra -> nuage sauvegardé), en ordre ligne par ligne (m00..m33).
    // Généré automatiquement avec les nuages (.ply/.pcd) ; rien à écrire si
    // aucun nuage n'a été produit. Reprojection : X_cam = M^-1 * X_nuage.
    void save_frame_transforms() {
        std::lock_guard<std::mutex> lock(transforms_mutex_);
        if (transforms_.empty()) return; // pas de nuage généré → pas de CSV
        std::sort(transforms_.begin(), transforms_.end(),
                  [](const auto& a, const auto& b) { return a.first < b.first; });

        std::string path = (fs::path(config.output_created) / "frame_transforms.csv").string();
        std::ofstream f(path);
        if (!f.is_open()) return;

        // En-tête : identifiant de frame + 16 coefficients de la matrice 4x4
        f << "frame";
        for (int r = 0; r < 4; ++r)
            for (int c = 0; c < 4; ++c)
                f << ",m" << r << c;
        f << "\n";

        f << std::setprecision(9);
        for (const auto& [idx, M] : transforms_) {
            std::stringstream ss;
            ss << std::setfill('0') << std::setw(4) << idx;
            f << "frame_" << ss.str();
            for (int r = 0; r < 4; ++r)
                for (int c = 0; c < 4; ++c)
                    f << "," << M(r, c);
            f << "\n";
        }
        f.close();
        std::cout << "Transforms saved to " << path << std::endl;
    }

    // Construit le nuage brut à partir des buffers RealSense.
    // DOIT être appelé sur le thread lecteur (copie les données du pool RealSense).
    pcl::PointCloud<pcl::PointXYZRGB>::Ptr build_raw_cloud(const rs2::points& points, const rs2::video_frame& color_frame) {
        pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
        cloud->is_dense = false;
        cloud->points.reserve(points.size());

        auto vertices = points.get_vertices();
        auto tex_coords = points.get_texture_coordinates();
        const uint8_t* color_data = reinterpret_cast<const uint8_t*>(color_frame.get_data());
        int color_width = color_frame.get_width();
        int color_height = color_frame.get_height();

        // Conversion et filtrage des points invalides (Z=0)
        for (size_t i = 0; i < points.size(); ++i) {
            if (vertices[i].z <= 0) continue; // Skip les points sans profondeur

            pcl::PointXYZRGB p;
            p.x = vertices[i].x;
            p.y = vertices[i].y;
            p.z = vertices[i].z;

            int u = static_cast<int>(tex_coords[i].u * color_width);
            int v = static_cast<int>(tex_coords[i].v * color_height);

            if (u >= 0 && u < color_width && v >= 0 && v < color_height) {
                int idx = (v * color_width + u) * 3;
                p.r = color_data[idx];
                p.g = color_data[idx + 1];
                p.b = color_data[idx + 2];
            } else {
                p.r = 0; p.g = 0; p.b = 0;
            }
            cloud->points.push_back(p);
        }
        return cloud;
    }

    // Filtrage Z + centrage + rotation. S'exécute sur les threads workers
    // (n'utilise que de la mémoire propre, donc thread-safe).
    pcl::PointCloud<pcl::PointXYZRGB>::Ptr finalize_cloud(const pcl::PointCloud<pcl::PointXYZRGB>::Ptr& cloud,
                                                          Eigen::Matrix4f& out_transform) {
        // Transformation caméra -> nuage sauvegardé. Identité par défaut
        // (cas repère caméra brut, ou nuage vide/non transformé).
        out_transform = Eigen::Matrix4f::Identity();
        if (cloud->empty()) return cloud;

        // 2. Traitement PCL : Filtrage PassThrough sur l'axe Z (on filtre AVANT de centrer)
        pcl::PointCloud<pcl::PointXYZRGB>::Ptr filtered_cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
        pcl::PassThrough<pcl::PointXYZRGB> pass;
        pass.setInputCloud(cloud);
        pass.setFilterFieldName("z");
        pass.setFilterLimits(config.z_min, config.z_max);
        pass.filter(*filtered_cloud);

        if (filtered_cloud->empty()) return filtered_cloud;

        // Repère caméra brut demandé : on renvoie le nuage filtré tel quel,
        // sans centrage ni rotation. L'origine reste la caméra. Utilisé pour
        // les usages génériques de l'outil ; le pipeline vigne appelle
        // toujours avec --no-keep-camera-frame pour bénéficier de la rotation.
        if (config.keep_camera_frame) return filtered_cloud;

        // 3. Centrage sur le centroïde — [PIPELINE VIGNE] DÉSACTIVÉ par défaut
        // (config.center_cloud == false), car il détruirait le mouvement
        // relatif entre frames consécutives utilisé par la registration
        // ICP/RANSAC. Activable indépendamment via --center si besoin.
        pcl::PointCloud<pcl::PointXYZRGB>::Ptr working_cloud = filtered_cloud;
        Eigen::Affine3f transform_center = Eigen::Affine3f::Identity();
        if (config.center_cloud) {
            Eigen::Vector4f centroid;
            pcl::compute3DCentroid(*filtered_cloud, centroid);
            transform_center.translation() << -centroid[0], -centroid[1], -centroid[2];
            pcl::PointCloud<pcl::PointXYZRGB>::Ptr centered_cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
            pcl::transformPointCloud(*filtered_cloud, *centered_cloud, transform_center);
            working_cloud = centered_cloud;
        }

        // 4. Rotation : rotation Z optionnelle partagée avec les images
        //    (config.rotation_angle), puis rotations configurables propres au
        //    nuage de points autour de Z, Y et X (défauts : Z=180°, Y=180°).
        //    Appliquée indépendamment du centrage (voir note ci-dessus).
        const float deg2rad = static_cast<float>(M_PI) / 180.0f;
        bool has_rotation = (config.rotation_angle != 0) || (config.rotation_z != 0.0) ||
                             (config.rotation_y != 0.0) || (config.rotation_x != 0.0);

        if (!has_rotation) {
            out_transform = transform_center.matrix();
            return working_cloud;
        }

        pcl::PointCloud<pcl::PointXYZRGB>::Ptr rotated_cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
        Eigen::Affine3f transform_rotate = Eigen::Affine3f::Identity();

        if (config.rotation_angle != 0)
            transform_rotate.rotate(Eigen::AngleAxisf(config.rotation_angle * deg2rad, Eigen::Vector3f::UnitZ()));
        if (config.rotation_z != 0.0)
            transform_rotate.rotate(Eigen::AngleAxisf(static_cast<float>(config.rotation_z) * deg2rad, Eigen::Vector3f::UnitZ()));
        if (config.rotation_y != 0.0)
            transform_rotate.rotate(Eigen::AngleAxisf(static_cast<float>(config.rotation_y) * deg2rad, Eigen::Vector3f::UnitY()));
        if (config.rotation_x != 0.0)
            transform_rotate.rotate(Eigen::AngleAxisf(static_cast<float>(config.rotation_x) * deg2rad, Eigen::Vector3f::UnitX()));

        pcl::transformPointCloud(*working_cloud, *rotated_cloud, transform_rotate);

        // Matrice totale caméra -> nuage sauvegardé : X_nuage = R * (X_cam [- centroïde]).
        // L'inverse (X_cam = M^-1 * X_nuage) permet de reprojeter via les intrinsèques.
        out_transform = (transform_rotate * transform_center).matrix();
        return rotated_cloud;
    }

    // Traite un job (rotation, encodage, écriture disque). Exécuté sur un thread worker.
    // Lecture seule de `config` et `directories` (partagés) → thread-safe.
    void process_job(const FrameJob& job) {
        // Génération du suffixe d'index de frame (ex: 0001)
        std::stringstream ss;
        ss << std::setfill('0') << std::setw(4) << job.frame_index;
        std::string frame_str = ss.str();

        cv::Mat color_image = job.color_image;
        cv::Mat depth_image = job.depth_image;
        cv::Mat depth_colormap = job.depth_colormap;

        // Rotation des images OpenCV si nécessaire
        if (config.rotation_angle != 0) {
            int cv_rotation = -1;
            if (config.rotation_angle == 90) cv_rotation = cv::ROTATE_90_COUNTERCLOCKWISE;
            else if (config.rotation_angle == 180) cv_rotation = cv::ROTATE_180;
            else if (config.rotation_angle == 270 || config.rotation_angle == -90) cv_rotation = cv::ROTATE_90_CLOCKWISE;

            if (cv_rotation != -1) {
                if (!depth_image.empty())    cv::rotate(depth_image, depth_image, cv_rotation);
                if (!color_image.empty())    cv::rotate(color_image, color_image, cv_rotation);
                if (!depth_colormap.empty()) cv::rotate(depth_colormap, depth_colormap, cv_rotation);
            }
        }

        // Sauvegarde RGB (RealSense fournit du RGB, OpenCV écrit du BGR → conversion)
        if (config.save_rgb && !color_image.empty()) {
            std::string rgb_path = (fs::path(directories.at("rgb")) / ("frame_" + frame_str + "_rgb.png")).string();
            cv::Mat bgr_image;
            cv::cvtColor(color_image, bgr_image, cv::COLOR_RGB2BGR);
            cv::imwrite(rgb_path, bgr_image);
        }

        // Sauvegarde Depth Raw
        if (config.save_depth_raw && !depth_image.empty()) {
            std::string depth_raw_path = (fs::path(directories.at("depth_raw")) / ("frame_" + frame_str + "_depth_raw.png")).string();
            cv::imwrite(depth_raw_path, depth_image);
        }

        // Sauvegarde Depth Normalisée
        if (config.save_depth_normalized && !depth_colormap.empty()) {
            std::string depth_norm_path = (fs::path(directories.at("depth_normalized")) / ("frame_" + frame_str + "_depth_normalized.png")).string();
            cv::Mat bgr_colormap;
            cv::cvtColor(depth_colormap, bgr_colormap, cv::COLOR_RGB2BGR);
            cv::imwrite(depth_norm_path, bgr_colormap);
        }

        // Filtrage/centrage/rotation puis sauvegarde des nuages de points (PCD / PLY)
        if ((config.save_ply || config.save_pcd) && job.raw_cloud) {
            Eigen::Matrix4f cloud_transform;
            pcl::PointCloud<pcl::PointXYZRGB>::Ptr processed_cloud = finalize_cloud(job.raw_cloud, cloud_transform);

            if (processed_cloud && !processed_cloud->empty()) {
                if (config.save_ply) {
                    std::string ply_path = (fs::path(directories.at("ply")) / ("frame_" + frame_str + ".ply")).string();
                    pcl::io::savePLYFileBinary(ply_path, *processed_cloud);
                }
                if (config.save_pcd) {
                    std::string pcd_path = (fs::path(directories.at("pcd")) / ("frame_" + frame_str + ".pcd")).string();
                    pcl::io::savePCDFileBinary(pcd_path, *processed_cloud);
                }
                // La transformation accompagne toujours le nuage sauvegardé
                // (identité en mode repère caméra brut).
                {
                    std::lock_guard<std::mutex> lock(transforms_mutex_);
                    transforms_.emplace_back(job.frame_index, cloud_transform);
                }
            }
        }
    }

    std::vector<double> process_frames(rs2::pipeline& pipeline, rs2::align& align, rs2::colorizer& colorizer, rs2::pointcloud& pc) {
        std::vector<double> timestamps;
        int frame_count = 0;
        int emitted = 0; // nombre de frames effectivement envoyées au traitement (pour la progression)

        // Détermination du nombre de threads workers
        unsigned int num_threads = (config.num_threads > 0)
            ? static_cast<unsigned int>(config.num_threads)
            : std::thread::hardware_concurrency();
        if (num_threads == 0) num_threads = 2; // repli si la détection échoue

        // File bornée : le lecteur applique une backpressure si les workers prennent du retard
        BoundedQueue<FrameJob> queue(num_threads * 2);

        // Lancement du pool de workers (consommateurs)
        std::vector<std::thread> workers;
        for (unsigned int t = 0; t < num_threads; ++t) {
            workers.emplace_back([this, &queue] {
                FrameJob job;
                while (queue.pop(job)) {
                    try {
                        process_job(job);
                    } catch (const std::exception& e) {
                        std::cerr << "[WORKER] Erreur frame " << job.frame_index << " : " << e.what() << std::endl;
                    }
                }
            });
        }

        std::cout << "Traitement parallèle : " << num_threads << " threads workers." << std::endl;

        bool need_cloud = config.save_ply || config.save_pcd;

        // Thread lecteur (producteur) : lecture séquentielle du .bag
        while (true) {
            rs2::frameset frames;
            // wait_for_frames est plus robuste pour la lecture de fichiers .bag
            if (!pipeline.try_wait_for_frames(&frames, 5000)) {
                break; // Fin du fichier ou timeout
            }

            // On vérifie la validité sur le frameset brut (sans alignement, opération chère)
            if (!frames.get_depth_frame() || !frames.get_color_frame()) {
                continue;
            }

            frame_count++;

            // Logique de sélection de la plage de frames AVANT l'alignement :
            // inutile d'aligner les frames qu'on va ignorer.
            if (frame_count < config.start_frame) {
                continue; // On passe les frames avant le début
            }
            if (config.end_frame != -1 && frame_count > config.end_frame) {
                break; // On arrête après la frame de fin
            }

            // Alignement (coûteux) uniquement pour les frames effectivement conservées
            rs2::frameset aligned_frames = align.process(frames);
            rs2::depth_frame depth_frame = aligned_frames.get_depth_frame();
            rs2::video_frame color_frame = aligned_frames.get_color_frame();
            if (!depth_frame || !color_frame) {
                continue;
            }

            timestamps.push_back(depth_frame.get_timestamp());

            // Préparation du job : on COPIE les données hors des buffers RealSense
            // (recyclés à l'itération suivante) avant de déléguer aux workers.
            FrameJob job;
            job.frame_index = frame_count;

            if (config.save_rgb) {
                cv::Mat color_image(cv::Size(color_frame.get_width(), color_frame.get_height()), CV_8UC3, (void*)color_frame.get_data(), cv::Mat::AUTO_STEP);
                job.color_image = color_image.clone();
            }
            if (config.save_depth_raw) {
                cv::Mat depth_image(cv::Size(depth_frame.get_width(), depth_frame.get_height()), CV_16UC1, (void*)depth_frame.get_data(), cv::Mat::AUTO_STEP);
                job.depth_image = depth_image.clone();
            }
            if (config.save_depth_normalized) {
                rs2::frame depth_colorized = colorizer.colorize(depth_frame);
                cv::Mat depth_colormap(cv::Size(depth_colorized.as<rs2::video_frame>().get_width(), depth_colorized.as<rs2::video_frame>().get_height()), CV_8UC3, (void*)depth_colorized.get_data(), cv::Mat::AUTO_STEP);
                job.depth_colormap = depth_colormap.clone();
            }
            if (need_cloud) {
                pc.map_to(color_frame);
                rs2::points points = pc.calculate(depth_frame);
                job.raw_cloud = build_raw_cloud(points, color_frame);
            }

            queue.push(std::move(job));

            // Ligne de progression lisible par le GUI (Streamlit la parse).
            // Format stable : "[PROGRESS] <n>" où n = frames envoyées au traitement.
            ++emitted;
            std::cout << "[PROGRESS] " << emitted << std::endl;

            // Condition de sortie manuelle si la boucle de lecture du fichier bag est terminée
            auto playback = pipeline.get_active_profile().get_device().as<rs2::playback>();
            if (playback && playback.current_status() == RS2_PLAYBACK_STATUS_STOPPED) {
                break;
            }
        }

        // Fermeture de la file et attente de la fin des workers
        queue.close();
        for (auto& w : workers) {
            w.join();
        }

        return timestamps;
    }

    void run() {
        validate();
        setup_directories();

        rs2::pipeline pipeline;
        rs2::config cfg;
        cfg.enable_device_from_file(config.bag_file, false);

        rs2::pipeline_profile pipeline_profile = pipeline.start(cfg);
        auto playback = pipeline_profile.get_device().as<rs2::playback>();
        playback.set_real_time(false); // Mode non-temps réel pour traiter chaque frame sans en sauter

        rs2::align align(RS2_STREAM_COLOR);
        rs2::colorizer colorizer;
        rs2::pointcloud pc;

        extract_camera_parameters(pipeline_profile);
        std::vector<double> timestamps = process_frames(pipeline, align, colorizer, pc);
        save_frame_timestamps(timestamps);
        save_frame_transforms();

        pipeline.stop();
        std::cout << "Image extraction completed." << std::endl;
    }
};

// ============================================================================
// PIPELINE MANAGER CLASS
// ============================================================================
class PipelineManager {
private:
    Config& config;

    void log_info(const std::string& message) {
        auto now = std::chrono::system_clock::now();
        auto in_time_t = std::chrono::system_clock::to_time_t(now);
        std::cout << std::put_time(std::localtime(&in_time_t), "%Y-%m-%d %H:%M:%S") << " - INFO - " << message << std::endl;
    }

    void log_error(const std::string& message) {
        auto now = std::chrono::system_clock::now();
        auto in_time_t = std::chrono::system_clock::to_time_t(now);
        std::cerr << std::put_time(std::localtime(&in_time_t), "%Y-%m-%d %H:%M:%S") << " - ERROR - " << message << std::endl;
    }

public:
    PipelineManager(Config& cfg) : config(cfg) {}

    void create_output_directory(double first_frame_timestamp) {
        // En RealSense, les timestamps des bag sont souvent basés sur l'epoch en millisecondes
        std::time_t tt = static_cast<std::time_t>(first_frame_timestamp / 1000.0);
        std::tm* time_ptr = std::localtime(&tt);

        std::stringstream ss;
        ss << std::put_time(time_ptr, "%Y-%m-%d_%H-%M-%S");
        std::string formatted_time = ss.str();

        std::string session_name = "data_" + formatted_time;
        config.output_created = (fs::path(config.output_dir) / session_name).string();
        fs::create_directories(config.output_created);
        log_info("Répertoire de sortie créé : " + config.output_created);

        // [PIPELINE VIGNE] Compatibilité avec Run_full_pipeline.py /
        // app_v5.py / app_v6.py : ces scripts lisaient ".last_session"
        // (écrit par l'ancien auto_extractor.cpp) pour retrouver le nom du
        // sous-dossier data_* venant d'être créé. On le réécrit ici pour ne
        // rien casser côté Python. Les nouveaux scripts peuvent aussi se
        // contenter d'un glob("data_*") — voir get_last_session_name() dans
        // le Run_full_pipeline.py mis à jour.
        std::ofstream session_file((fs::path(config.output_dir) / ".last_session").string());
        if (session_file.is_open()) {
            session_file << session_name << std::endl;
            session_file.close();
        }
    }

    void run_image_extraction() {
        if (config.save_rgb || config.save_depth_raw || config.save_depth_normalized ||
            config.save_pcd || config.save_ply || config.generate_timestamps || config.save_camera_params) {
            
            log_info("Démarrage de l'extraction d'images...");
            ImageExtractor extractor(config);
            extractor.run();
            log_info("Extraction d'images terminée.");
        }
    }

    void run() {
        try {
            auto start_time = std::chrono::high_resolution_clock::now();

            log_info("Démarrage du pipeline...");
            
            // Étape 1 : Initialisation minimale pour le dossier de sortie
            rs2::pipeline pipe;
            rs2::config cfg;
            cfg.enable_device_from_file(config.bag_file, false);
            rs2::pipeline_profile profile = pipe.start(cfg);
            profile.get_device().as<rs2::playback>().set_real_time(false);
            
            rs2::frameset f = pipe.wait_for_frames();
            double first_frame_timestamp = f.get_timestamp();
            pipe.stop(); // On ferme ce pipe temporaire

            create_output_directory(first_frame_timestamp);

            // Étape 2 : Extraction & traitement
            run_image_extraction();

            auto end_time = std::chrono::high_resolution_clock::now();
            std::chrono::duration<double> elapsed = end_time - start_time;

            std::stringstream ss;
            ss << std::fixed << std::setprecision(2) << elapsed.count();
            log_info("Pipeline terminé avec succès en " + ss.str() + " secondes.");
        } 
        catch (const std::exception& e) {
            log_error("Erreur dans le pipeline : " + std::string(e.what()));
        }
    }
};

// ============================================================================
// CHARGEMENT D'UN FICHIER DE CONFIGURATION (format clé=valeur)
// Permet de modifier le comportement sans recompiler.
// ============================================================================
namespace {
    std::string trim(const std::string& s) {
        size_t start = s.find_first_not_of(" \t\r\n");
        if (start == std::string::npos) return "";
        size_t end = s.find_last_not_of(" \t\r\n");
        return s.substr(start, end - start + 1);
    }

    bool parse_bool(const std::string& v) {
        std::string s = v;
        std::transform(s.begin(), s.end(), s.begin(), ::tolower);
        return (s == "1" || s == "true" || s == "yes" || s == "on" || s == "oui");
    }
}

// Applique une paire clé/valeur sur la configuration.
// Utilisé à la fois par le fichier de config et (indirectement) par la CLI.
void apply_setting(Config& config, const std::string& key, const std::string& value) {
    if (key == "bag_file")                  config.bag_file = value;
    else if (key == "output_dir")           config.output_dir = value;
    else if (key == "start_frame")          config.start_frame = std::stoi(value);
    else if (key == "end_frame")            config.end_frame = std::stoi(value);
    else if (key == "num_threads" || key == "jobs") config.num_threads = std::stoi(value);
    else if (key == "z_min" || key == "zmin") config.z_min = std::stod(value);
    else if (key == "z_max" || key == "zmax") config.z_max = std::stod(value);
    else if (key == "rotation_angle" || key == "rotate") config.rotation_angle = std::stoi(value);
    else if (key == "rotation_x" || key == "rot_x") config.rotation_x = std::stod(value);
    else if (key == "rotation_y" || key == "rot_y") config.rotation_y = std::stod(value);
    else if (key == "rotation_z" || key == "rot_z") config.rotation_z = std::stod(value);
    else if (key == "keep_camera_frame") config.keep_camera_frame = parse_bool(value);
    else if (key == "center_cloud" || key == "center") config.center_cloud = parse_bool(value);
    else if (key == "save_rgb")             config.save_rgb = parse_bool(value);
    else if (key == "save_depth_raw")       config.save_depth_raw = parse_bool(value);
    else if (key == "save_depth_normalized") config.save_depth_normalized = parse_bool(value);
    else if (key == "save_ply")             config.save_ply = parse_bool(value);
    else if (key == "save_pcd")             config.save_pcd = parse_bool(value);
    else if (key == "save_camera_params")   config.save_camera_params = parse_bool(value);
    else if (key == "generate_timestamps")  config.generate_timestamps = parse_bool(value);
    else std::cerr << "[CONFIG] Clé inconnue ignorée : " << key << std::endl;
}

// Charge un fichier de config. Lignes : "clé = valeur". '#' et ';' = commentaires.
void load_config_file(const std::string& path, Config& config) {
    std::ifstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Fichier de configuration introuvable : " + path);
    }
    std::string line;
    int line_no = 0;
    while (std::getline(f, line)) {
        line_no++;
        line = trim(line);
        if (line.empty() || line[0] == '#' || line[0] == ';') continue;

        size_t eq = line.find('=');
        if (eq == std::string::npos) {
            std::cerr << "[CONFIG] Ligne " << line_no << " ignorée (pas de '=') : " << line << std::endl;
            continue;
        }
        std::string key = trim(line.substr(0, eq));
        std::string value = trim(line.substr(eq + 1));
        if (!key.empty()) apply_setting(config, key, value);
    }
    std::cout << "Configuration chargée depuis : " << path << std::endl;
}

// ============================================================================
// HELP / USAGE
// ============================================================================
void print_help() {
    std::cout << "Usage: ./extract_frames <file.bag> [options]\n"
              << "       ./extract_frames --config <fichier.conf> [options]\n"
              << "Options:\n"
              << "  --config <fichier>   Charger les paramètres depuis un fichier (clé=valeur)\n"
              << "  --out <dir>          Dossier de sortie (default: ./output)\n"
              << "  --jobs <num>         Nombre de threads workers (default: auto = nb de cœurs)\n"
              << "  --start <num>        Frame de début (default: 1)\n"
              << "  --end <num>          Frame de fin (default: -1, fin du fichier)\n"
              << "  --zmin <float>       Filtre Z minimum (default: 0.0)\n"
              << "  --zmax <float>       Filtre Z maximum (default: 1.5)\n"
              << "  --rgb                Sauvegarder les images RGB\n"
              << "  --depth-raw          Sauvegarder la profondeur brute (16bit)\n"
              << "  --depth-norm         Sauvegarder la profondeur colorisée\n"
              << "  --ply                Générer les nuages de points .ply (filtrés/centrés)\n"
              << "  --pcd                Générer les nuages de points .pcd\n"
              << "  --rotate <angle>     Rotation images+nuage: 0, 90, 180, 270 (default: 0)\n"
              << "  --rot-x <deg>        Rotation nuage autour de X (default: 0)\n"
              << "  --rot-y <deg>        Rotation nuage autour de Y (default: 180)\n"
              << "  --rot-z <deg>        Rotation nuage autour de Z (default: 180)\n"
              << "  --keep-camera-frame    Nuage dans le repere camera brut, sans rotation (DEFAUT)\n"
              << "  --no-keep-camera-frame Active les rotations configurables (rot-x/y/z, rotate)\n"
              << "  --center               Centre le nuage sur son centroide (independant de la rotation ;\n"
              << "                         DECONSEILLE pour le pipeline vigne, casse la registration ICP)\n"
              << "  --no-center            Pas de centrage (DEFAUT)\n"
              << "  --params             Sauvegarder les paramètres intrinsèques\n"
              << "  --timestamps         Générer le fichier CSV des timestamps\n"
              << "  --help               Afficher cette aide\n";
}

// ============================================================================
// MAIN ENTRY POINT (Remplace interface.py / main.py execution)
// ============================================================================
int main(int argc, char** argv) {
    if (argc < 2) {
        print_help();
        return 0;
    }

    Config config;
    config.output_dir = "./output";

    try {
        // --- Passe 1 : --help et --config (la config est appliquée AVANT les autres args) ---
        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--help" || arg == "-h") {
                print_help();
                return 0;
            }
            if (arg == "--config" && i + 1 < argc) {
                load_config_file(argv[i + 1], config);
            }
        }

        // --- Passe 2 : arguments CLI (priorité sur le fichier de config) ---
        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];
            if (arg == "--config") { ++i; continue; }            // déjà traité
            else if (arg == "--out" && i + 1 < argc)    config.output_dir = argv[++i];
            else if (arg == "--jobs" && i + 1 < argc)   config.num_threads = std::stoi(argv[++i]);
            else if (arg == "--start" && i + 1 < argc)  config.start_frame = std::stoi(argv[++i]);
            else if (arg == "--end" && i + 1 < argc)    config.end_frame = std::stoi(argv[++i]);
            else if (arg == "--zmin" && i + 1 < argc)   config.z_min = std::stod(argv[++i]);
            else if (arg == "--zmax" && i + 1 < argc)   config.z_max = std::stod(argv[++i]);
            else if (arg == "--rgb")                    config.save_rgb = true;
            else if (arg == "--depth-raw")              config.save_depth_raw = true;
            else if (arg == "--depth-norm")             config.save_depth_normalized = true;
            else if (arg == "--ply")                    config.save_ply = true;
            else if (arg == "--pcd")                    config.save_pcd = true;
            else if (arg == "--rotate" && i + 1 < argc) config.rotation_angle = std::stoi(argv[++i]);
            else if (arg == "--rot-x" && i + 1 < argc)  config.rotation_x = std::stod(argv[++i]);
            else if (arg == "--rot-y" && i + 1 < argc)  config.rotation_y = std::stod(argv[++i]);
            else if (arg == "--rot-z" && i + 1 < argc)  config.rotation_z = std::stod(argv[++i]);
            else if (arg == "--keep-camera-frame")      config.keep_camera_frame = true;
            else if (arg == "--no-keep-camera-frame")   config.keep_camera_frame = false;
            else if (arg == "--center")                 config.center_cloud = true;
            else if (arg == "--no-center")               config.center_cloud = false;
            else if (arg == "--params")                 config.save_camera_params = true;
            else if (arg == "--timestamps")             config.generate_timestamps = true;
            else if (!arg.empty() && arg[0] != '-')     config.bag_file = arg; // argument positionnel
            else std::cerr << "[ATTENTION] Argument inconnu ignoré : " << arg << std::endl;
        }
    }
    catch (const std::exception& e) {
        std::cerr << "[ERREUR] Lecture des arguments/configuration : " << e.what() << std::endl;
        return -1;
    }

    // --- Validation ---
    if (config.bag_file.empty()) {
        std::cerr << "[ERREUR] Aucun fichier .bag spécifié (en argument ou via --config).\n" << std::endl;
        print_help();
        return -1;
    }

    if (config.rotation_angle != 0 && config.rotation_angle != 90 &&
        config.rotation_angle != 180 && config.rotation_angle != 270 &&
        config.rotation_angle != -90) {
        std::cerr << "[ATTENTION] Angle de rotation invalide (" << config.rotation_angle
                  << "). Valeurs acceptées : 0, 90, 180, 270, -90. Rotation désactivée." << std::endl;
        config.rotation_angle = 0;
    }

    if (config.z_min > config.z_max) {
        std::cerr << "[ATTENTION] z_min (" << config.z_min << ") > z_max (" << config.z_max
                  << "). Valeurs échangées." << std::endl;
        std::swap(config.z_min, config.z_max);
    }

    std::cout << "=======================================================" << std::endl;
    std::cout << "   EXTRACTEUR REALSENSE C++\n";
    std::cout << "   FICHIER : " << fs::path(config.bag_file).filename() << std::endl;
    std::cout << "   PLAGE : " << config.start_frame << " -> " << (config.end_frame == -1 ? "FIN" : std::to_string(config.end_frame)) << std::endl;
    std::cout << "   ROTATION IMAGES : " << config.rotation_angle << " deg" << std::endl;
    if (config.keep_camera_frame) {
        std::cout << "   NUAGE           : repere camera brut (ni centrage ni rotation)" << std::endl;
    } else {
        std::cout << "   NUAGE           : centre + rotation X=" << config.rotation_x
                  << " Y=" << config.rotation_y << " Z=" << config.rotation_z << " deg" << std::endl;
    }
    std::cout << "=======================================================" << std::endl;
    
    try {
        PipelineManager pipeline_manager(config);
        pipeline_manager.run();
    } 
    catch (const std::exception& e) {
        std::cerr << "[ERREUR FATALE] " << e.what() << std::endl;
        return -1;
    }

    return 0;
}