#!/usr/bin/env python3
import argparse
import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from config import BuildConfig, AndroidVersion, KernelVersion, ANDROID_KERNEL_MAP, KSUVersion
from kernel_builder import KernelBuilder, BuildResult

logging.basicConfig(
    level=logging.INFO,
    format='\033[92m[%(levelname)s]\033[0m %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# NOTE: there used to be a DEFAULT_BUILD_MATRIX dict here, plus --matrix/
# --all/--list-matrix flags that built from it. It was a second, unsynced
# source of truth, separate from matrix.json (which update_matrix.py keeps
# current from Google's own tags) and never actually used by build-kernel.sh,
# kernel-build.yml, or build-kernels.yml - all of them always pass explicit
# --android/--kernel/--sub-level/--os-patch/--kernel-tag. Removed to avoid
# it silently going stale again (e.g. missing android16/17, old respins).
# matrix.json is now the one and only source of truth for "which versions
# exist to build" - see update_matrix.py.


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GKI Kernel Build System")

    parser.add_argument("--android", "-a", choices=[v.value for v in AndroidVersion])
    parser.add_argument("--kernel", "-k", choices=[v.value for v in KernelVersion])
    parser.add_argument("--sub-level", "-s")
    parser.add_argument("--os-patch")
    parser.add_argument("--ksu-version", choices=[v.value for v in KSUVersion], default=KSUVersion.STABLE.value)
    parser.add_argument("--ksu-commit", default=None)
    parser.add_argument("--susfs-commit", default=None)
    parser.add_argument("--zram", action="store_true")
    parser.add_argument("--no-kpm", action="store_true")
    parser.add_argument("--bbg", action="store_true")
    parser.add_argument("--op8e", action="store_true")
    parser.add_argument("--ksm", action="store_true", help="Enable KSM (Kernel Samepage Merging)")
    parser.add_argument("--bbr-version", choices=["none", "bbr1", "bbr3"], default="bbr1")
    parser.add_argument("--no-release", action="store_true")
    parser.add_argument("--custom-version", dest="custom_version", default=None)
    parser.add_argument("--revision")
    parser.add_argument("--kernel-tag", default=None,
                        help="Pin kernel/common to a specific respin tag (e.g. android13-5.15-2025-12_r10) "
                             "instead of the moving branch HEAD")
    parser.add_argument("--disable-safemode", action="store_true",
                        help="Permanently disable KernelSU/SukiSU volume-key safe-mode detection "
                             "(most users rely on Yet Another Bootloop Protector instead)")
    parser.add_argument("--lts", action="store_true",
                        help="Mark this build as sourced from an LTS-merge respin tag "
                             "(e.g. android13-5.15.209_r00) - adds a '-lts' marker to the "
                             "output filenames so downstream users can tell it apart from a "
                             "regular date-based respin at a glance")
    parser.add_argument("--list-configs", action="store_true")
    parser.add_argument("--workspace", "-w", default=os.environ.get("GKI_WORKSPACE", "/tmp/gki-build"))
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--output-json")
    parser.add_argument("--dry-run", action="store_true")

    return parser.parse_args()


def create_build_config(args: argparse.Namespace) -> BuildConfig:
    return BuildConfig(
        android_version=args.android or "android14",
        kernel_version=args.kernel or "6.1",
        sub_level=args.sub_level or "124",
        os_patch_level=args.os_patch or "2025-02",
        kernelsu_version=args.ksu_version,
        kernelsu_commit=args.ksu_commit,
        susfs_commit=args.susfs_commit,
        use_zram=args.zram,
        use_kpm=not args.no_kpm,
        use_bbg=args.bbg,
        support_op8e=args.op8e,
        enable_ksm=args.ksm,
        bbr_version=args.bbr_version,
        make_release=not args.no_release,
        custom_version=args.custom_version,
        revision=args.revision,
        kernel_tag=args.kernel_tag,
        disable_safemode=args.disable_safemode,
        is_lts_build=args.lts,
    )


def list_configs():
    print("\n" + "=" * 60)
    print("Supported Android/Kernel combinations")
    print("=" * 60)
    for android, kernels in ANDROID_KERNEL_MAP.items():
        print(f"  {android.value}: {', '.join(k.value for k in kernels)}")
    print("\nFor the actual sub_level/os_patch_level/kernel_tag matrix, see")
    print("matrix.json (kept current by update_matrix.py) - this list only")
    print("shows which android/kernel combinations config.py accepts.")
    print("\n" + "=" * 60)
    print("KernelSU version options")
    print("=" * 60)
    for v in KSUVersion:
        print(f"  - {v.value}")


def build_single(config: BuildConfig, workspace: str, dry_run: bool = False) -> BuildResult:
    if dry_run:
        logger.info(f"[DRY RUN] Validating config: {config.config_name}")
        return BuildResult(success=True, config=config, message="Config validation passed")

    builder = KernelBuilder(config, workspace)
    return builder.build()


def print_summary(results: list, output_json: str = None):
    total = len(results)
    success = sum(1 for r in results if r.success)

    print("\n" + "=" * 60)
    print("Build Summary")
    print("=" * 60)
    print(f"Total: {total}")
    print(f"Success: \033[92m{success}\033[0m")
    print(f"Failed: \033[91m{total - success}\033[0m")

    if success > 0:
        avg_time = sum(r.build_time or 0 for r in results if r.success) / success
        print(f"Average build time: {avg_time:.2f} sec")

    failed = total - success
    if failed > 0:
        print("\nFailed configs:")
        for r in results:
            if not r.success:
                print(f"  - {r.config.config_name}: {r.message}")
    print("=" * 60)

    if output_json:
        json_data = {
            "timestamp": datetime.now().isoformat(),
            "total": total,
            "success": success,
            "failed": failed,
            "results": [{"config": r.config.to_dict(), "success": r.success, "message": r.message,
                       "artifacts": r.artifacts, "build_time": r.build_time} for r in results]
        }
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to: {output_json}")


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_configs:
        list_configs()
        return 0

    if not args.android:
        logger.error("Please specify --android (with --kernel, --sub-level, --os-patch)")
        return 1

    workspace = args.workspace
    logger.info(f"Workspace: {workspace}")
    os.makedirs(workspace, exist_ok=True)

    results = []

    try:
        config = create_build_config(args)
        result = build_single(config, workspace, args.dry_run)
        results.append(result)
    except Exception as e:
        logger.error(f"Config error: {e}")
        return 1

    if results:
        print_summary(results, args.output_json)

    if results and all(r.success for r in results):
        return 0
    elif results and any(r.success for r in results):
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
