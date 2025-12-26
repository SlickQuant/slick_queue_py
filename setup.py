from setuptools import setup, Extension
import sys
import re

# Read version from slick_queue_py.py
def get_version():
    with open('slick_queue_py.py', 'r') as f:
        content = f.read()
        match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", content, re.M)
        if match:
            return match.group(1)
        raise RuntimeError("Unable to find version string.")

# Define the C++ extension module (uses std::atomic for cross-platform atomic ops)
atomic_ops_ext = Extension(
    'atomic_ops_ext',
    sources=['atomic_ops_ext.cpp'],
    include_dirs=[],
    libraries=[],
    extra_compile_args=['/std:c++11', '/O2'] if sys.platform == 'win32' else ['-std=c++11', '-O2'],
)

setup(
    name='slick_queue_py',
    version=get_version(),
    description='Python binding for slick::SlickQueue with atomic operations',
    ext_modules=[atomic_ops_ext],
    py_modules=['slick_queue_py', 'atomic_ops'],
    python_requires='>=3.8',
)
