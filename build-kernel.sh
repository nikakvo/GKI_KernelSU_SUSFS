#!/bin/bash
set -e

# Locates the repo automatically - works whether this script sits inside
# the repo itself, or next to it (e.g. in $HOME alongside
# GKI_KernelSU_SUSFS, GKI_KernelSU_SUSFS-main, or any other folder name;
# git clone vs GitHub's "Download ZIP" produce different folder names).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_repo_dir() {
    # 1) Maybe the script is already sitting inside the repo.
    if [ -d "$SCRIPT_DIR/.github/workflows/scripts" ]; then
        echo "$SCRIPT_DIR"
        return 0
    fi

    # 2) Otherwise scan subdirectories next to the script (depth 1-3) for
    #    the first one that contains .github/workflows/scripts.
    local match
    match="$(find "$SCRIPT_DIR" -maxdepth 3 -type d -path '*/.github/workflows/scripts' 2>/dev/null | head -n1)"
    if [ -n "$match" ]; then
        # strip the trailing /.github/workflows/scripts to get the repo root
        echo "${match%/.github/workflows/scripts}"
        return 0
    fi

    return 1
}

REPO_ROOT="$(find_repo_dir)" || {
    echo "Could not find the GKI_KernelSU_SUSFS repo (looked for a"
    echo "'.github/workflows/scripts' folder under: $SCRIPT_DIR"
    echo "Place build-kernel.sh either inside the repo, or in a parent"
    echo "folder that contains the repo as a subfolder."
    exit 1
}

REPO_DIR="$REPO_ROOT/.github/workflows/scripts"
MATRIX_FILE="$REPO_DIR/../config/matrix.json"
WORKSPACE="$HOME/gki-workspace"
LOGFILE="$HOME/build-$(date +%Y%m%d-%H%M%S).log"

# ---- Default values ----
ANDROID_VERSION="android13"
KERNEL_VERSION="5.15"
SUB_LEVEL="206"
OS_PATCH="2026-06"
KERNEL_TAG="android13-5.15-2026-06_r4"

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
echo "1) Default (android13 / 5.15 / 206 / 2026-06)"
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

    # android|kernel|sub_level|os_patch|revision|kernel_tag, one line per configuration
    mapfile -t CONFIGS < <(python3 -c "
import json
with open('$MATRIX_FILE') as f:
    data = json.load(f)
for key, entries in data.items():
    android, kernel = key.split('-', 1)
    for e in entries:
        if not e.get('enabled', True):
            continue
        print(f\"{android}|{kernel}|{e['sub_level']}|{e['os_patch_level']}|{e.get('revision', '')}|{e.get('kernel_tag', '')}\")
")

    TOTAL=${#CONFIGS[@]}
    if [ "$TOTAL" -eq 0 ]; then
        echo "matrix.json is empty, nothing to build."
        exit 1
    fi

    echo "Found $TOTAL configuration(s) to build:"
    for line in "${CONFIGS[@]}"; do
        IFS='|' read -r a k s p r t <<< "$line"
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
        IFS='|' read -r a k s p r t <<< "$line"
        N=$((N + 1))
        echo ""
        echo "========================================"
        echo "  [$N/$TOTAL] $a-$k-$s ($p)"
        echo "========================================"

        EXTRA_ARGS=()
        [ -n "$r" ] && EXTRA_ARGS+=(--revision "$r")
        # Pin kernel/common to the exact respin tag update_matrix.py recorded
        # for this sub_level/os_patch (instead of the moving branch HEAD),
        # so this matches the known-working respin the matrix was updated
        # from - not whatever Google has pushed to the branch since.
        [ -n "$t" ] && EXTRA_ARGS+=(--kernel-tag "$t")

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
    read -rp "Sublevel (e.g. 206): " SUB_LEVEL
    read -rp "Security Patch Level (e.g. 2026-06): " OS_PATCH
    echo ""
    echo "Check the exact respin tag here (optional, e.g. android13-5.15-2026-06_r4):"
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
