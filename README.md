# GKI SukiSU-Ultra + SUSFS Build System

### This is an automated repository for building GKI kernels

> Does not support OnePlus ColorOS 14/15 or non-GKI devices

> If this is your first time using it, please **read the following carefully** — don't waste other people's time out of laziness!

> Uses a Python-assisted build system, supports building against specific SukiSU-Ultra/SUSFS commit versions

---

## Quick Start

### GitHub Actions

#### Method 1: Build a single version
1. Go to the **Actions** tab
2. Select **Kernel Build**
3. Click **Run workflow**
4. Choose the Android version, kernel version, and build options
5. Optional: specify a SukiSU-Ultra or SUSFS commit hash

#### Method 2: Build all versions
1. Select **Build Kernels**
2. Click **Run workflow**
3. Set the global options (KSU version, ZRAM, KPM, etc.)
4. Optional: specify commit versions

### Command-line local build

```bash
# Enter the build directory
cd .github/workflows/scripts

# Install dependencies
pip install PyYAML

# Build a single version
python build.py --android android14 --kernel 6.1 --sub-level 124 --os-patch 2025-02

# Build an entire matrix entry
python build.py --matrix android14-6.1

# Build all versions
python build.py --all

# Specify commit versions
python build.py --all --ksu-commit abc1234 --susfs-commit HEAD~1

# List all supported configurations
python build.py --list-configs

# List predefined build matrices
python build.py --list-matrix
```

---

## Build Matrix

Loaded from `matrix.json`:

| Android | Kernel | Sub Levels | OS Patch |
|---------|--------|------------|----------|
| 12 | 5.10 | 136, 198, 209, 236, X (LTS) | 2022-11 ~ 2025-05 |
| 13 | 5.15 | 74, 123, 148, 170, 178, 180 | 2023-01 ~ 2025-05 |
| 14 | 6.1 | 78, 90, 99, 124, 145 | 2024-06 ~ 2025-09 |
| 15 | 6.6 | 50, 66, 102 | 2024-10 ~ 2025-10 |

**19 version combinations** total

---

## Command-Line Arguments

| Argument | Description | Default |
|------|------|--------|
| `--android`, `-a` | Android version (android12/13/14/15) | android14 |
| `--kernel`, `-k` | Kernel version (5.10/5.15/6.1/6.6) | 6.1 |
| `--sub-level`, `-s` | Sub level version or X (LTS) | 124 |
| `--os-patch` | OS Patch Level | 2025-02 |
| `--revision` | Android 12 Revision | - |
| `--ksu-version` | SukiSU-Ultra version (Stable/Dev) | Stable |
| `--ksu-commit` | Specify a SukiSU-Ultra commit hash | latest |
| `--susfs-commit` | Specify a SUSFS commit (hash or HEAD~N) | latest |
| `--zram` | Enable ZRAM (LZ4KD) | False |
| `--no-kpm` | Disable KPM | False |
| `--bbg` | Enable Baseband-guard | False |
| `--op8e` | Enable OnePlus 8E support | False |
| `--bbr` | Set BBR as the default congestion control algorithm | False |
| `--no-release` | Don't create a GitHub Release | False |
| `--custom-version` | Custom version name | - |
| `--matrix`, `-m` | Use a predefined matrix entry | - |
| `--all` | Build all configurations | - |
| `--list-configs` | List all supported configurations | - |
| `--list-matrix` | List all predefined matrices | - |
| `--dry-run` | Only validate the configuration | - |
| `--workspace`, `-w` | Working directory | /tmp/gki-build |

---

## Downloads

1. **AnyKernel3.zip** — ready to flash!
   - Use a flashing tool such as [HorizonKernelFlasher](https://github.com/libxzr/HorizonKernelFlasher/releases) to flash the kernel

2. **boot.img** — download the format matching your kernel (uncompressed, gz, lz4)
   - Flash using [Fastboot](https://magiskcn.com/)

---

## Supported Features

| Feature | Description |
|------|------|
| [KernelSU](https://kernelsu.org/) | SukiSU kernel-level root solution |
| [SUSFS4](https://gitlab.com/simonpunk/susfs4ksu) | Kernel-level patches that assist KSU in hiding root |
| [BBR](https://blog.thinkin.top/archives/ke-pu-bbrdao-di-shi-shi-me) | TCP congestion control algorithm |
| [LZ4KD](https://github.com/ShirkNeko/SukiSU_patch/tree/main/other) | A ZRAM compression algorithm sourced from Huawei's codebase |
| [KPM](https://github.com/bmax121/KernelPatch) | Kernel module support |
| [Baseband-guard](https://github.com/vc-teahouse/Baseband-guard) | Baseband security protection |

<details>
<summary>Supported ZRAM algorithms (switchable in Scene)</summary>

LZ4K, LZ4HC, deflate, 842, lz4k_oplus

</details>

---

## KSU Manager

After the build completes, the latest manager APK is generated.

---

## Emergency Recovery Guide

> **Trigger condition**
> Use this if the device fails to boot due to a bad or incompatible kernel flash

1. Enter Fastboot mode
   - Physical button combo: Power + Volume Down
   - Or ADB command: `adb reboot bootloader`

2. Run the flash command
```bash
fastboot flash boot <full_boot.img_filename>
```

---

## Kernel Version Compatibility Notes

### 1. Cross sub-version flashing rules

If your phone's main GKI version is 5.10.x (e.g. 5.10.168), you can flash a kernel with a higher sub-version under the same major version (e.g. 5.10.198).

Regarding the **X-lts** version, using `android12-5.10.X-lts-AnyKernel3.zip` as an example:
- **X-lts** denotes the Long Term Support build (the highest sub-version number, 5.10.236 in this example)
- As the GKI source updates, the LTS build number keeps incrementing
- ⚠️ Note: LTS being the newest doesn't mean it's the most stable (e.g. 6.6.x has a known auto-reboot bug)

### 2. Kernel version spoofing method

Run the following in an MT Manager terminal:
```bash
uname -r | sed 's/^[^-]*//'
```
Copy the resulting version string and paste it into the Action's build panel to spoof the kernel version.

### 3. Customizing the build matrix

Edit `.github/workflows/config/matrix.json` to add or modify build versions:
```json
{
  "android14-6.1": [
    {"sub_level": "124", "os_patch_level": "2025-02"},
    {"sub_level": "145", "os_patch_level": "2025-09"}
  ]
}
```

---

## Build System Architecture

```
.github/workflows/
├── config/
│   └── matrix.json          # Build matrix configuration
├── scripts/
│   ├── build.py             # Main build script (CLI entry point)
│   ├── kernel_builder.py    # Core kernel build class
│   ├── config.py            # Configuration definitions and validation
│   ├── matrix_generator.py  # GitHub Actions matrix generator
│   ├── release_generator.py # Release notes generator
│   └── cache_manager.py     # Build cache management
├── kernel-build.yml         # Single-version build workflow
└── build-kernels.yml        # Full build workflow
```

### Core Components

| Component | Function |
|------|------|
| `KernelBuilder` | Core kernel build class — handles cloning source, applying patches, compiling, and packaging |
| `BuildConfig` | Build configuration data class containing all build parameters |
| `CacheManager` | Manages ccache and build cache, supports cross-branch reuse |
| `matrix_generator.py` | Generates the build matrix for GitHub Actions |
| `release_generator.py` | Automatically generates Release notes |

### Repository Dependencies

| Repository | Purpose |
|------|------|
| [SukiSU-Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) | SukiSU-Ultra source code and setup script |
| [susfs4ksu](https://github.com/ShirkNeko/susfs4ksu) | SUSFS kernel patches |
| [SukiSU_patch](https://github.com/ShirkNeko/SukiSU_patch) | Additional SukiSU-Ultra patches (ZRAM, etc.) |
| [AnyKernel3](https://github.com/WildPlusKernel/AnyKernel3) | Generic flashable package template |
| [kernel_patches](https://github.com/Tools-cx-app/kernel_patches) | Kernel patch collection |
| [Baseband-guard](https://github.com/vc-teahouse/Baseband-guard) | Baseband security protection |

---
