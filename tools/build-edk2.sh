#!/usr/bin/env bash
# Rebuild tools/efi/BOOTX64.EFI (EDK2 UEFI Shell) and tools/efi/CapsuleApp.efi from source.
# Linux (tested: Ubuntu 24.04, gcc 13). Needs: git gcc g++ make nasm python3 uuid-dev.
# The build directory ends up in the binaries (debug path, source file names). For neutral
# paths, build in a container, see README ("Rebuilding the EDK2 tools").
set -euo pipefail
TAG=${1:-edk2-stable202605}
REPO=$(cd "$(dirname "$0")/.." && pwd)
# EDK2_WORK: absolute build directory (e.g. /build in a container). Only $EDK2_WORK/edk2 is created and removed.
WORK=${EDK2_WORK:-$(mktemp -d)}
case "$WORK" in /*) ;; *) echo "EDK2_WORK must be an absolute path" >&2; exit 1 ;; esac
if [ -e "$WORK/edk2" ]; then echo "$WORK/edk2 already exists, remove it first" >&2; exit 1; fi
mkdir -p "$WORK"
git -c advice.detachedHead=false clone --depth 1 --branch "$TAG" https://github.com/tianocore/edk2.git "$WORK/edk2"
cd "$WORK/edk2"
git submodule update --init --depth 1 \
  BaseTools/Source/C/BrotliCompress/brotli \
  MdeModulePkg/Library/BrotliCustomDecompressLib/brotli \
  MdePkg/Library/MipiSysTLib/mipisyst \
  MdePkg/Library/BaseFdtLib/libfdt
make -C BaseTools -j"$(nproc)"
export WORKSPACE=$PWD PACKAGES_PATH=$PWD
set +u; source edksetup.sh; set -u
build -a X64 -t GCC -b RELEASE -p MdeModulePkg/MdeModulePkg.dsc -m MdeModulePkg/Application/CapsuleApp/CapsuleApp.inf
build -a X64 -t GCC -b RELEASE -p ShellPkg/ShellPkg.dsc -m ShellPkg/Application/Shell/Shell.inf
cp Build/MdeModule/RELEASE_GCC/X64/CapsuleApp.efi "$REPO/tools/efi/CapsuleApp.efi"
# full shell with all built-in commands (dmpstore etc.)
cp Build/Shell/RELEASE_GCC/X64/ShellPkg/Application/Shell/EA4BB293-2D7F-4456-A681-1F22F42CD0BC/OUTPUT/Shell.efi "$REPO/tools/efi/BOOTX64.EFI"
echo "EDK2 $TAG commit $(git rev-parse HEAD)"
sha256sum "$REPO/tools/efi/"*
rm -rf "$WORK/edk2"
if [ -z "${EDK2_WORK:-}" ]; then rmdir "$WORK"; fi
