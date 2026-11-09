#!/usr/bin/env bash
set -euo pipefail
# Run only within an owned OAR compute-node allocation.
: "${OAR_JOB_ID:?An owned OAR allocation is required}"
case "$(hostname -s)" in fnancy*|flyon*) exit 2 ;; esac
module load boost
export CPLEX_INCLUDE="$HOME/cplex1210/cplex/include"
export CPLEX_BIN="$HOME/cplex1210/cplex/lib/x86-64_linux/static_pic/libcplex.a"
export BOOST_INCLUDE="$CMAKE_PREFIX_PATH/include"
export BOOST_BIN="$CMAKE_PREFIX_PATH/lib/libboost_graph.so"
upstream_root="${1:?Pass upstream source root}"
upstream_build="${2:?Pass build directory}"
cmake -S "$upstream_root/code" -B "$upstream_build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$upstream_build" --target bp -j 4
sha256sum "$upstream_build/bp" "$CPLEX_BIN" "$CPLEX_INCLUDE/ilcplex/cplex.h"
