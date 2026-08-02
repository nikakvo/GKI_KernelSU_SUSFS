# GKI SukiSU-Ultra + SUSFS Build System

### An automated build system for GKI kernels with SukiSU-Ultra and SUSFS

> Does not support OnePlus ColorOS 14/15 or non-GKI devices

> If this is your first time using it, please **read the following carefully** — don't waste other people's time out of laziness!

> Python-assisted build system with automatic GKI respin tracking, exact source pinning, and dependency auto-installation for local builds.

---

## Credits & Origin

This project builds on the work of others in the GKI/KernelSU ecosystem:

- **[ShirkNeko/GKI_KernelSU_SUSFS](https://github.com/ShirkNeko/GKI_KernelSU_SUSFS)** — the original build system this project was originally forked from. The overall build.py/kernel_builder.py architecture traces back to this project.
- **[SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra)** — the KernelSU implementation this build system compiles into every kernel.
- **[susfs4ksu](https://gitlab.com/simonpunk/susfs4ksu)** and **[ShirkNeko/SukiSU_patch](https://github.com/ShirkNeko/SukiSU_patch)** — SUSFS kernel patches and supplementary SukiSU-Ultra patches (ZRAM, hooks).

This repository has since diverged significantly from the original fork (exact GKI respin pinning, automatic matrix updates, local-build tooling, AVB signing, safe-mode removal, and more — see below), and is now maintained as an independent project.

---

## Quick Start

### Local build (recommended)

The fastest way to build — handles dependency installation automatically, no manual setup needed.

```bash
git clone https://github.com/nikakvo/GKI_KernelSU_SUSFS
cd GKI_KernelSU_SUSFS
chmod +x build-kernel.sh cleanup-workspace.sh
./build-kernel.sh
```

You'll get a menu:
```
1) Default (android13 / 5.15 / 194 / 2025-12)
2) Custom (choose your own versions)
3) All versions from matrix.json
```

- **Option 1** builds the latest known-good default configuration.
- **Option 2** lets you pick any Android/kernel/sub_level/os_patch combination, plus optionally pin an exact GKI respin tag (see [Exact Source Pinning](#exact-source-pinning-kernel-tag) below).
- **Option 3** builds every `"enabled": true` entry in `matrix.json` sequentially, with a per-build pass/fail summary at the end.

The script auto-installs everything it needs on first run (git, build-essential, ccache, PyYAML, etc.) — no separate setup step required, even on a clean Ubuntu/WSL install.

When you're done, reclaim disk space with:
```bash
./cleanup-workspace.sh
```
(wipes only the per-version build directories — `.repo/`, `common/`, `prebuilts/` — keeps shared repos and the AVB signing key intact)

### GitHub Actions

#### Method 1: Build a single version
1. Go to the **Actions** tab
2. Select **Kernel Build**
3. Click **Run workflow**
4. Choose the Android version, kernel version, and build options

#### Method 2: Build all matrix versions
1. Select **Build Kernels**
2. Click **Run workflow**
3. Set the global options (KSU version, ZRAM, KPM, BBR, etc.)

### Command-line (manual)

```bash
cd .github/workflows/scripts
pip install PyYAML

# Build a single version
python3 build.py --android android13 --kernel 5.15 --sub-level 194 --os-patch 2025-12 --zram --bbr-version bbr1

# Pin an exact GKI respin (recommended - see below)
python3 build.py --android android13 --kernel 5.15 --sub-level 194 --os-patch 2025-12 --kernel-tag android13-5.15-2025-12_r10

# List supported Android/Kernel combinations
python3 build.py --list-configs
```

There is no `--matrix`/`--all` flag anymore — see [Build Matrix](#build-matrix) below for why.

---

## Exact Source Pinning (`--kernel-tag`)

Google's GKI branches (e.g. `android13-5.15-2025-12`) are **moving branches**, not fixed points — Google periodically pushes new commits to the same branch and tags each snapshot as a numbered "respin" (`_r1`, `_r2`, ... `_r10`, ...). Building without pinning a tag just grabs whatever the branch HEAD happens to be at sync time, which is **not reproducible** and may be several respins behind the latest security fixes.

`--kernel-tag` fetches and checks out an **exact** respin tag instead, guaranteeing byte-for-byte the same source Google certified for that specific release:

```bash
python3 build.py --android android13 --kernel 5.15 --sub-level 194 --os-patch 2025-12 \
    --kernel-tag android13-5.15-2025-12_r10
```

Find the latest respin tag for your target branch at:
- https://android.googlesource.com/kernel/common/+refs (all tags, all branches)
- https://source.android.com/docs/core/architecture/kernel/gki-android13-5_15-release-builds (release notes per branch)

The resulting kernel release string reflects the pinned respin (e.g. `5.15.194-android13-r10`) instead of an ambiguous moving-HEAD version.

---

## Build Matrix

`matrix.json` (`.github/workflows/config/matrix.json`) is the single source of truth for which `sub_level`/`os_patch_level`/`kernel_tag` combinations exist. It is **not maintained by hand** — run:

```bash
cd .github/workflows/scripts
python3 update_matrix.py --dry-run   # preview changes
python3 update_matrix.py             # apply
```

This queries Google's `kernel/common` repository directly (`git ls-remote --tags`) for every tracked Android/kernel family, finds the latest respin per month, resolves the real `sub_level` from each tag's `Makefile`, and updates `matrix.json` — adding new months as `"enabled": false` (opt-in) and refreshing the `kernel_tag`/`sub_level` on existing entries without touching your `enabled` choices.

Tracked families: `android12-5.10`, `android13-5.15`, `android14-6.1`, `android15-6.6`, `android16-6.12`, `android17-6.18`.

> **Note on android16/17:** SukiSU-Ultra does not yet fully support kernel 6.12+ — see [SukiSU-Ultra#921](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/921) (`netlink_kernel_cfg`/`security_add_hooks` API breakage). These entries are tracked for when upstream support lands, but currently fail to compile. Leave them `"enabled": false` until that issue is resolved.

Each entry looks like:
```json
{"sub_level": "194", "os_patch_level": "2025-12", "kernel_tag": "android13-5.15-2025-12_r10", "enabled": true}
```

---

## Command-Line Arguments

| Argument | Description | Default |
|------|------|--------|
| `--android`, `-a` | Android version (android12–android17) | android14 |
| `--kernel`, `-k` | Kernel version (5.10/5.15/6.1/6.6/6.12/6.18) | 6.1 |
| `--sub-level`, `-s` | Sub level version or X (LTS) | 124 |
| `--os-patch` | OS Patch Level | 2025-02 |
| `--kernel-tag` | Pin an exact GKI respin tag instead of the moving branch HEAD | - |
| `--revision` | Android 12 revision (used for certified-boot reference downloads) | - |
| `--ksu-version` | SukiSU-Ultra version (Stable/Dev) | Stable |
| `--ksu-commit` | Specify a SukiSU-Ultra commit hash | latest |
| `--susfs-commit` | Specify a SUSFS commit (hash or HEAD~N) | latest |
| `--zram` | Enable ZRAM (LZ4KD) | False |
| `--no-kpm` | Disable KPM | False |
| `--bbg` | Enable Baseband-guard | False |
| `--op8e` | Enable OnePlus 8E support | False |
| `--bbr-version` | Congestion control: `none` or `bbr1` (sets as system default) | bbr1 |
| `--disable-safemode` | Permanently disable KernelSU/SukiSU volume-key safe mode detection (most users rely on [YABP](https://github.com/Magisk-Modules-Repo/YetAnotherBootloopProtector) instead) | False |
| `--no-release` | Don't create a GitHub Release | False |
| `--custom-version` | Custom `CONFIG_LOCALVERSION` string | - |
| `--list-configs` | List supported Android/Kernel combinations | - |
| `--dry-run` | Only validate the configuration, don't build | - |
| `--workspace`, `-w` | Working directory | /tmp/gki-build |

---

## Downloads

1. **AnyKernel3.zip** — ready to flash!
   - Use a flashing tool such as [HorizonKernelFlasher](https://github.com/libxzr/HorizonKernelFlasher/releases) to flash the kernel

2. **boot.img** — download the format matching your kernel
   - Flash via `fastboot flash boot_ab <filename>`
   - Every boot image is AVB-signed with a canonical key kept in sync between local and CI builds (see [AVB Signing](#avb-signing) below).

---

## Supported Features

| Feature | Description |
|------|------|
| [KernelSU / SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) | Kernel-level root solution |
| [SUSFS4](https://gitlab.com/simonpunk/susfs4ksu) | Kernel-level patches that assist KSU in hiding root |
| BBR v1 | TCP congestion control algorithm |
| [LZ4KD](https://github.com/ShirkNeko/SukiSU_patch/tree/main/other) | ZRAM compression algorithm sourced from Huawei's codebase |
| [KPM](https://github.com/bmax121/KernelPatch) | Kernel module support |
| [Baseband-guard](https://github.com/vc-teahouse/Baseband-guard) | Baseband security protection |
| MGLRU / PSI | Modern memory reclaim + pressure metrics for smarter LMKD decisions |
| IP Set / CAKE | Netfilter IP grouping and bufferbloat-reducing queue discipline |
| Wireguard | Native in-kernel VPN support |
| Safe Mode Removal | Volume-key safe-mode detection permanently patched out (opt out by omitting `--disable-safemode`) |

<details>
<summary>Supported ZRAM algorithms (switchable in Scene)</summary>

LZ4K, LZ4HC, deflate, 842, lz4k_oplus

</details>

---

## AVB Signing

Every `boot.img` is signed with `avbtool` using a canonical RSA key so that images are consistently signed across local and CI (GitHub Actions) builds:

- **Locally:** if no key is found, one is auto-generated once at `<workspace>/boot_sign_key.pem` and reused for every subsequent build.
- **CI:** the same key content should be set as the `BOOT_SIGN_KEY` repository secret.

For most users on an unlocked bootloader this is a formality (AVB verification is typically bypassed).

---

## Emergency Recovery Guide

> **Trigger condition**
> Use this if the device fails to boot due to a bad or incompatible kernel flash

1. Enter Fastboot mode
   - Physical button combo: Power + Volume Down
   - Or ADB command: `adb reboot bootloader`

2. Run the flash command
```bash
fastboot flash boot_ab <full_boot.img_filename>
```

---

## Kernel Version Compatibility Notes

### 1. Cross sub-version flashing rules

If your phone's main GKI version is 5.10.x (e.g. 5.10.168), you can flash a kernel with a higher sub-version under the same major version (e.g. 5.10.198).

### 2. Kernel version spoofing method

Run the following in an MT Manager terminal:
```bash
uname -r | sed 's/^[^-]*//'
```
Copy the resulting version string and paste it into the build panel to spoof the kernel version.

---

## Build System Architecture

```
.github/workflows/
├── config/
│   ├── matrix.json           # Build matrix - kept current by update_matrix.py
│   └── update_matrix.py      # Refreshes matrix.json from Google's kernel/common tags
├── scripts/
│   ├── build.py               # Main build script (CLI entry point)
│   ├── kernel_builder.py      # Core kernel build class
│   ├── config.py              # Configuration definitions and validation
│   ├── matrix_generator.py    # GitHub Actions matrix generator
│   ├── release_generator.py   # Release notes generator
│   ├── extract_artifacts.py   # Collects build artifacts for release
│   ├── telegram_notify.py     # Optional Telegram build notifications
│   └── patches/
│       └── disable-safemode-full.patch
├── kernel-build.yml           # Single-version build workflow
└── build-kernels.yml          # Full matrix build workflow

build-kernel.sh                # Local interactive build menu (recommended entry point)
cleanup-workspace.sh           # Reclaims disk space between builds
```

### Core Components

| Component | Function |
|------|------|
| `KernelBuilder` | Core kernel build class — handles cloning source, applying patches, compiling, and packaging |
| `BuildConfig` | Build configuration data class containing all build parameters |
| `update_matrix.py` | Queries Google's kernel/common tags directly and keeps matrix.json current |
| `matrix_generator.py` | Generates the build matrix for the GitHub Actions matrix build |
| `release_generator.py` | Automatically generates Release notes |

### Repository Dependencies

| Repository | Purpose |
|------|------|
| [SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) | SukiSU-Ultra source code and setup script |
| [susfs4ksu](https://gitlab.com/simonpunk/susfs4ksu) | SUSFS kernel patches |
| [SukiSU_patch](https://github.com/ShirkNeko/SukiSU_patch) | Additional SukiSU-Ultra patches (ZRAM, hooks) |
| [AnyKernel3](https://github.com/WildPlusKernel/AnyKernel3) | Generic flashable package template |
| [kernel_patches](https://github.com/Tools-cx-app/kernel_patches) | Kernel patch collection |
| [Baseband-guard](https://github.com/vc-teahouse/Baseband-guard) | Baseband security protection |

---
