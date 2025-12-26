cmake_minimum_required(VERSION 3.14)
project(slick_queue_py_interop_tests)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

option(SLICK_QUEUE_PY_BUILD_TESTS "Build tests" ON)

if (SLICK_QUEUE_PY_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()

