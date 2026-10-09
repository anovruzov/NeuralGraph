#!/bin/sh
# Build llama-server (CPU only) from the llama.cpp vendored in the llama-cpp-python 0.3.16 sdist.
set -eu
SRC=/root/mycelic-live/src/llama-cpp-python-0.3.16/vendor/llama.cpp
BLD=/root/mycelic-live/build
nice -n 15 cmake -S "$SRC" -B "$BLD" -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DGGML_METAL=OFF \
  -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_SERVER=ON -DBUILD_SHARED_LIBS=OFF
nice -n 15 cmake --build "$BLD" --config Release -j 3 --target llama-server llama-bench
