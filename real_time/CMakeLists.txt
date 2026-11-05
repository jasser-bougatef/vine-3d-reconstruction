cmake_minimum_required(VERSION 3.10)
project(AutoExtractor)

# Spécifier le standard C++
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED TRUE)

# Trouver les bibliothèques requises
find_package(realsense2 REQUIRED)
find_package(OpenCV REQUIRED)
find_package(PCL REQUIRED)

# Pour PROJ (Conversion GPS)
find_package(PkgConfig REQUIRED)
pkg_check_modules(PROJ REQUIRED proj)

# Inclure les répertoires d'en-têtes
include_directories(${PCL_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS} ${PROJ_INCLUDE_DIRS})
link_directories(${PCL_LIBRARY_DIRS} ${PROJ_LIBRARY_DIRS})
add_definitions(${PCL_DEFINITIONS})

# Créer l'exécutable à partir de ton fichier auto_extraction.cpp
add_executable(auto_extractor auto_extractor.cpp)

# Lier toutes les bibliothèques à l'exécutable
target_link_libraries(auto_extractor
    realsense2::realsense2
    ${OpenCV_LIBS}
    ${PCL_LIBRARIES}
    ${PROJ_LIBRARIES}
)
