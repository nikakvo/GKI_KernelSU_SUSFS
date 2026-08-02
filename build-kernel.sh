#!/bin/bash
set -e

# The script locates itself - works regardless of what the containing folder
# is named (GKI_KernelSU_SUSFS, GKI_KernelSU_SUSFS-main, or anything else;
# git clone vs GitHub's "Download ZIP" produce different folder names).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR/.github/workflows/scripts"
MATRIX_FILE="$REPO_DIR/../config/matrix.json"
WORKSPACE="$HOME/gki-workspace"
LOGFILE="$HOME/build-$(date +%Y%m%d-%H%M%S).log"

# ---- Default values ----
ANDROID_VERSION="android13"
KERNEL_VERSION="5.15"
SUB_LEVEL="194"
OS_PATCH="2025-12"
KERNEL_TAG="android13-5.15-2025-12_r10"

# ============================================================
#  Dependency check / auto-install
# ============================================================
check_dependencies() {
    # System apt packages - based on the GitHub Actions workflows
    # (kernel-build.yml / build-kernels.yml) + standard host build tools
    local apt_packages=(
        git curl wget zip unzip xz-utils openssl pixz
        ccache python3 python3-pip
        build-essential bc bison flex
        libssl-dev libelf-dev rsync
    )
    local missing_apt=()

    for pkg in "${apt_packages[@]}"; do
        dpkg -s "$pkg" &>/dev/null || missing_apt+=("$pkg")
    done

    if [ ${#missing_apt[@]} -gt 0 ]; then
        echo "========================================"
        echo "  Missing dependencies, installing..."
        echo "========================================"
        echo "  ${missing_apt[*]}"
        echo ""
        sudo apt-get update
        sudo apt-get install -y "${missing_apt[@]}"
        echo ""
    fi

    # Python PyYAML module (used by matrix_generator.py and others)
    if ! python3 -c "import yaml" &>/dev/null; then
        echo "Missing Python module PyYAML, installing..."
        pip3 install --user PyYAML 2>/dev/null || pip3 install PyYAML
        echo ""
    fi

    if [ ${#missing_apt[@]} -eq 0 ]; then
        echo "All dependencies are present."
        echo ""
    else
        echo "Dependencies installed."
        echo ""
    fi
}

check_dependencies

# ============================================================
#  Build menu
# ============================================================
echo "========================================"
echo "  GKI KernelSU SUSFS - Build Menu"
echo "========================================"
echo "1) Default (android13 / 5.15 / 194 / 2025-12)"
echo "2) Custom (choose your own versions)"
echo "3) All versions from matrix.json"
echo "========================================"
read -rp "Choose an option [1-3]: " BUILD_OPTION

cd "$REPO_DIR"

if [ "$BUILD_OPTION" == "3" ]; then
    if [ ! -f "$MATRIX_FILE" ]; then
        echo "Could not find matrix.json at: $MATRIX_FILE"
        exit 1
    fi

    echo ""
    echo "Reading configurations from: $MATRIX_FILE"
    echo ""

    # android|kernel|sub_level|os_patch|revision, one line per configuration
    mapfile -t CONFIGS < <(python3 -c "
import json
with open('$MATRIX_FILE') as f:
    data = json.load(f)
for key, entries in data.items():
    android, kernel = key.split('-', 1)
    for e in entries:
        if not e.get('enabled', True):
            continue
        print(f\"{android}|{kernel}|{e['sub_level']}|{e['os_patch_level']}|{e.get('revision', '')}\")
")

    TOTAL=${#CONFIGS[@]}
    if [ "$TOTAL" -eq 0 ]; then
        echo "matrix.json is empty, nothing to build."
        exit 1
    fi

    echo "Found $TOTAL configuration(s) to build:"
    for line in "${CONFIGS[@]}"; do
        IFS='|' read -r a k s p r <<< "$line"
        echo "  - $a-$k-$s ($p)"
    done
    echo ""
    read -rp "Continue with all $TOTAL build(s)? (y/n) " confirm
    [ "$confirm" == "y" ] || { echo "Cancelled."; exit 0; }

    SUCCESS=0
    FAILED=0
    FAILED_LIST=()
    N=0

    for line in "${CONFIGS[@]}"; do
        IFS='|' read -r a k s p r <<< "$line"
        N=$((N + 1))
        echo ""
        echo "========================================"
        echo "  [$N/$TOTAL] $a-$k-$s ($p)"
        echo "========================================"

        EXTRA_ARGS=()
        [ -n "$r" ] && EXTRA_ARGS+=(--revision "$r")

        if python3 build.py \
            --android "$a" \
            --kernel "$k" \
            --sub-level "$s" \
            --os-patch "$p" \
            --bbr-version bbr1 \
            --zram \
            --disable-safemode \
            --workspace "$WORKSPACE" \
            "${EXTRA_ARGS[@]}" \
            2>&1 | tee -a "$LOGFILE"; then
            SUCCESS=$((SUCCESS + 1))
        else
            FAILED=$((FAILED + 1))
            FAILED_LIST+=("$a-$k-$s")
        fi
    done

    echo ""
    echo "========================================"
    echo "  Summary"
    echo "========================================"
    echo "Total:   $TOTAL"
    echo "Success: $SUCCESS"
    echo "Failed:  $FAILED"
    if [ "$FAILED" -gt 0 ]; then
        echo "Failed configurations:"
        for f in "${FAILED_LIST[@]}"; do
            echo "  - $f"
        done
    fi
    echo "Build log saved to: $LOGFILE"
    exit 0
fi

if [ "$BUILD_OPTION" == "2" ]; then
    echo ""
    echo "Check available versions here:"
    echo "https://zzh20188.github.io/GKI_KernelSU_SUSFS/index.html"
    echo ""

    read -rp "Android Version (e.g. android13): " ANDROID_VERSION
    read -rp "Kernel Version (e.g. 5.15): " KERNEL_VERSION
    read -rp "Sublevel (e.g. 178): " SUB_LEVEL
    read -rp "Security Patch Level (e.g. 2025-03): " OS_PATCH
    echo ""
    echo "Check the exact respin tag here (optional, e.g. android13-5.15-2025-12_r10):"
    echo "https://android.googlesource.com/kernel/common/+refs"
    read -rp "Kernel Tag (Enter to skip - uses the branch's latest HEAD): " KERNEL_TAG

elif [ "$BUILD_OPTION" != "1" ]; then
    echo "Invalid choice. Exiting."
    exit 1
fi

echo ""
echo "Building with:"
echo "  Android:  $ANDROID_VERSION"
echo "  Kernel:   $KERNEL_VERSION"
echo "  Sublevel: $SUB_LEVEL"
echo "  OS Patch: $OS_PATCH"
[ -n "$KERNEL_TAG" ] && echo "  Kernel Tag: $KERNEL_TAG"
echo ""

EXTRA_ARGS=()
[ -n "$KERNEL_TAG" ] && EXTRA_ARGS+=(--kernel-tag "$KERNEL_TAG")

python3 build.py \
    --android "$ANDROID_VERSION" \
    --kernel "$KERNEL_VERSION" \
    --sub-level "$SUB_LEVEL" \
    --os-patch "$OS_PATCH" \
    --bbr-version bbr1 \
    --zram \
    --disable-safemode \
    --workspace "$WORKSPACE" \
    "${EXTRA_ARGS[@]}" \
    2>&1 | tee "$LOGFILE"

echo ""
echo "Build log saved to: $LOGFILE"
echo "Artifacts in: $WORKSPACE/${ANDROID_VERSION}-${KERNEL_VERSION}-${SUB_LEVEL}/"
