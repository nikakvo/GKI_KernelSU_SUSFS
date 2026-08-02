#!/bin/bash
# Пуска се СЛЕД като си copy-нал zip/boot.img файловете, които ти трябват.
# Трие versioned build директорията (тежка, специфична за конкретния sub_level),
# запазва споделените репота/toolchain-а за по-бързи бъдещи build-ове.

set -e
WORKSPACE="$HOME/gki-workspace"

echo "=== Преди чистене ==="
du -sh "$WORKSPACE"/* 2>/dev/null

echo ""
echo "Ще изтрия версионните build директории (android*-*-*), запазвам:"
echo "  AnyKernel3, SukiSU_patch, kernel_patches, susfs4ksu, toolchain, mkbootimg, git-repo"
read -p "Продължавам ли? (y/n) " confirm

if [ "$confirm" = "y" ]; then
    find "$WORKSPACE" -maxdepth 1 -type d -regextype posix-extended -regex '.*/android[0-9]+-[0-9.]+-[0-9X]+' -exec rm -rf {} \;
    echo "Готово."
else
    echo "Прекратено, нищо не е изтрито."
fi

echo ""
echo "=== След чистене ==="
du -sh "$WORKSPACE"/* 2>/dev/null
