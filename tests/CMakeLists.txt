include(FetchContent)

# Fetch slick_queue from GitHub
set(BUILD_SLICK_QUEUE_TESTS OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
    slick_queue
    GIT_REPOSITORY https://github.com/SlickQuant/slick_queue.git
    GIT_TAG main
)
FetchContent_MakeAvailable(slick_queue)

set(SLICK_SHM_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(SLICK_SHM_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(SLICK_SHM_INSTALL OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
    slick_shm
    GIT_REPOSITORY https://github.com/SlickQuant/slick_shm.git
    GIT_TAG main
)
FetchContent_MakeAvailable(slick_shm)

# Find Python
find_package(Python3 COMPONENTS Interpreter Development REQUIRED)

# Create interop test executables
add_executable(cpp_producer cpp_producer.cpp)
target_link_libraries(cpp_producer PRIVATE slick_queue)

add_executable(cpp_consumer cpp_consumer.cpp)
target_link_libraries(cpp_consumer PRIVATE slick_queue)

add_executable(cpp_work_stealing_consumer cpp_work_stealing_consumer.cpp)
target_link_libraries(cpp_work_stealing_consumer PRIVATE slick_queue slick_shm)

add_executable(cpp_multi_producer cpp_multi_producer.cpp)
target_link_libraries(cpp_multi_producer PRIVATE slick_queue)

# Platform-specific linking and compilation flags
if(UNIX)
    target_link_libraries(cpp_producer PRIVATE pthread rt)
    target_link_libraries(cpp_consumer PRIVATE pthread rt)
    target_link_libraries(cpp_work_stealing_consumer PRIVATE pthread rt)
    target_link_libraries(cpp_multi_producer PRIVATE pthread rt)
endif()

# Add Python interop tests
add_test(
    NAME atomic_ops_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_atomic_ops.py
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME atomic_cursor_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_atomic_cursor.py
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME local_mode_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_local_mode.py
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME shm_mode_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_queue.py
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME multi_producer_test
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_multi_producer.py
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME python_producer_cpp_consumer
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_interop.py --test python_producer_cpp_consumer
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME cpp_producer_python_consumer
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_interop.py --test cpp_producer_python_consumer
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)

add_test(
    NAME multi_producer_interop
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_SOURCE_DIR}/tests/test_interop.py --test multi_producer_interop
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
)
