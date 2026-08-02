#!/bin/bash
set -e

REPO_DIR="$HOME/GKI_KernelSU_SUSFS-main/.github/workflows/scripts"
MATRIX_FILE="$REPO_DIR/../config/matrix.json"
WORKSPACE="$HOME/gki-workspace"
LOGFILE="$HOME/build-$(date +%Y%m%d-%H%M%S).log"

# ---- Стойности по подразбиране ----
ANDROID_VERSION="android13"
KERNEL_VERSION="5.15"
SUB_LEVEL="194"
OS_PATCH="2025-12"
KERNEL_TAG="android13-5.15-2025-12_r10"

# ============================================================
#  Проверка / автоматична инсталация на зависимости
# ============================================================
check_dependencies() {
    # Системни apt пакети - базирани на GitHub Actions workflow-ите
    # (kernel-build.yml / build-kernels.yml) + стандартни host build tools
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
        echo "  Липсващи зависимости, инсталирам..."
        echo "========================================"
        echo "  ${missing_apt[*]}"
        echo ""
        sudo apt-get update
        sudo apt-get install -y "${missing_apt[@]}"
        echo ""
    fi

    # Python модул PyYAML (ползва се от matrix_generator.py и др.)
    if ! python3 -c "import yaml" &>/dev/null; then
        echo "Липсва Python модул PyYAML, инсталирам..."
        pip3 install --user PyYAML 2>/dev/null || pip3 install PyYAML
        echo ""
    fi

    if [ ${#missing_apt[@]} -eq 0 ]; then
        echo "Всички зависимости са налични."
        echo ""
    else
        echo "Зависимостите са инсталирани."
        echo ""
    fi
}

check_dependencies

# ============================================================
#  Build меню
# ============================================================
echo "========================================"
echo "  GKI KernelSU SUSFS - Build меню"
echo "========================================"
echo "1) Default (android13 / 5.15 / 194 / 2025-12)"
echo "2) Custom (сам избираш версии)"
echo "3) Всички версии от matrix.json"
echo "========================================"
read -rp "Избери опция [1-3]: " BUILD_OPTION

cd "$REPO_DIR"

if [ "$BUILD_OPTION" == "3" ]; then
    if [ ! -f "$MATRIX_FILE" ]; then
        echo "Не намирам matrix.json на: $MATRIX_FILE"
        exit 1
    fi

    echo ""
    echo "Чета конфигурации от: $MATRIX_FILE"
    echo ""

    # android|kernel|sub_level|os_patch|revision, по 1 ред за всяка конфигурация
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
        echo "matrix.json е празен, няма какво да билдвам."
        exit 1
    fi

    echo "Намерени $TOTAL конфигурации за билдване:"
    for line in "${CONFIGS[@]}"; do
        IFS='|' read -r a k s p r <<< "$line"
        echo "  - $a-$k-$s ($p)"
    done
    echo ""
    read -rp "Продължавам ли с всички $TOTAL билда? (y/n) " confirm
    [ "$confirm" == "y" ] || { echo "Прекратено."; exit 0; }

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
    echo "  Обобщение"
    echo "========================================"
    echo "Общо:    $TOTAL"
    echo "Успешни: $SUCCESS"
    echo "Провалени: $FAILED"
    if [ "$FAILED" -gt 0 ]; then
        echo "Провалени конфигурации:"
        for f in "${FAILED_LIST[@]}"; do
            echo "  - $f"
        done
    fi
    echo "Build log записан в: $LOGFILE"
    exit 0
fi

if [ "$BUILD_OPTION" == "2" ]; then
    echo ""
    echo "Провери наличните версии тук:"
    echo "https://zzh20188.github.io/GKI_KernelSU_SUSFS/index.html"
    echo ""

    read -rp "Android Version (напр. android13): " ANDROID_VERSION
    read -rp "Kernel Version (напр. 5.15): " KERNEL_VERSION
    read -rp "Sublevel (напр. 178): " SUB_LEVEL
    read -rp "Security Patch Level (напр. 2025-03): " OS_PATCH
    echo ""
    echo "Провери точния respin таг тук (по избор, напр. android13-5.15-2025-12_r10):"
    echo "https://android.googlesource.com/kernel/common/+refs"
    read -rp "Kernel Tag (Enter за пропускане - взима последния HEAD на branch-а): " KERNEL_TAG

elif [ "$BUILD_OPTION" != "1" ]; then
    echo "Невалиден избор. Излизам."
    exit 1
fi

echo ""
echo "Ще билдвам с:"
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
echo "Build log записан в: $LOGFILE"
echo "Артефакти в: $WORKSPACE/${ANDROID_VERSION}-${KERNEL_VERSION}-${SUB_LEVEL}/"
