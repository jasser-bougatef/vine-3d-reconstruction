cmake_minimum_required(VERSION 3.10)
# LANGUAGES CXX C : le "C" est requis même si tout le code est en C++.
# La PCL installée ici (via apt) est liée à une VTK compilée avec un module
# MPI optionnel (VTK::mpi -> MPI::MPI_C). Cette cible MPI::MPI_C n'est créée
# par find_package(MPI) QUE si le langage C est activé dans le projet CMake.
# Sans ça : "CMake Error ... VTK::mpi contains MPI::MPI_C but the target was
# not found." même si aucun code MPI n'est utilisé ici.
project(auto_extractor LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O3")

find_package(realsense2 REQUIRED)
find_package(OpenCV REQUIRED)
find_package(PCL REQUIRED COMPONENTS common io filters)
# NOTE : plus de dépendance PROJ — le nouveau extracteur ne fait plus
# l'extraction/conversion GPS (déléguée à PPK_Pipeline.py / syn_code.py).

find_package(Threads REQUIRED)

add_definitions(${PCL_DEFINITIONS})
include_directories(${PCL_INCLUDE_DIRS} ${OpenCV_INCLUDE_DIRS})
link_directories(${PCL_LIBRARY_DIRS})

add_executable(auto_extractor auto_extractor.cpp)

target_link_libraries(auto_extractor
    ${realsense2_LIBRARY}
    ${OpenCV_LIBS}
    ${PCL_LIBRARIES}
    Threads::Threads
)

# Binaire compilé UNE SEULE FOIS (contrairement à l'ancienne version qui
# régénérait + recompilait un .cpp par job). Les paramètres par job
# (start/end frame, z_min/z_max, rotations, out dir...) sont désormais
# passés en arguments CLI à l'exécution — voir Run_full_pipeline.py.
