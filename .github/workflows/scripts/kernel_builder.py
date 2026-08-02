import os
import subprocess
import logging
import re
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass, field

from config import (BuildConfig, KSU_REPO_CONFIG, SUSFS_REPO_CONFIG, SUKISU_PATCH_REPO_CONFIG,
                   ANYKERNEL_CONFIG, KERNEL_PATCHES_CONFIG, BBG_CONFIG, TOOLCHAIN_CONFIG,
                   LEGACY_FIXES, OP8E_PATCH_URL, KPM_PATCH_URL)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    success: bool
    config: BuildConfig
    message: str = ""
    artifacts: list = field(default_factory=list)
    build_time: Optional[float] = None


class ShellCommand:
    def __init__(self, cwd: Optional[str] = None, env: Optional[dict] = None):
        self.cwd = cwd
        self.env = env or os.environ.copy()

    def run(self, cmd: str, check: bool = True, capture_output: bool = False,
            shell: bool = True, timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        logger.info(f"Executing command: {cmd}")
        try:
            return subprocess.run(cmd, shell=shell, cwd=self.cwd, env=self.env,
                                capture_output=capture_output, text=True, timeout=timeout, check=check)
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {e.stderr or str(e)}")
            raise
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {cmd}")
            raise

    def run_with_callback(self, cmd: str, callback: Optional[Callable] = None) -> str:
        logger.info(f"Executing command: {cmd}")
        process = subprocess.Popen(cmd, shell=True, cwd=self.cwd, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        output_lines = []
        for line in process.stdout:
            line = line.rstrip()
            output_lines.append(line)
            if callback:
                callback(line)
        process.wait()
        if process.returncode != 0:
            raise RuntimeError(f"Command failed")
        return "\n".join(output_lines)


class KernelBuilder:
    KERNEL_CONFIG_TEMPLATE = """
# === KernelSU Config ===
CONFIG_KSU=y
CONFIG_KPM=y
CONFIG_KSU_SUSFS_SUS_SU=n

# === TMPFS Config ===
CONFIG_TMPFS_XATTR=y
CONFIG_TMPFS_POSIX_ACL=y

# === Network Config ===
CONFIG_IP_NF_TARGET_TTL=y
CONFIG_IP6_NF_TARGET_HL=y
CONFIG_IP6_NF_MATCH_HL=y

# === BBR Config ===
CONFIG_TCP_CONG_ADVANCED=y
CONFIG_TCP_CONG_BBR=y
CONFIG_NET_SCH_FQ=y
CONFIG_TCP_CONG_BIC=n
CONFIG_TCP_CONG_WESTWOOD=n
CONFIG_TCP_CONG_HTCP=n

# === Networking Improvements (IP Set / connmark / CAKE / fq_codel) ===
CONFIG_IP_SET=y
CONFIG_IP_SET_MAX=256
CONFIG_IP_SET_BITMAP_IP=y
CONFIG_IP_SET_BITMAP_IPMAC=y
CONFIG_IP_SET_BITMAP_PORT=y
CONFIG_IP_SET_HASH_IP=y
CONFIG_IP_SET_HASH_IPMARK=y
CONFIG_IP_SET_HASH_IPPORT=y
CONFIG_IP_SET_HASH_IPPORTIP=y
CONFIG_IP_SET_HASH_IPPORTNET=y
CONFIG_IP_SET_HASH_NET=y
CONFIG_IP_SET_HASH_NETPORT=y
CONFIG_IP_SET_HASH_NETIFACE=y
CONFIG_IP_SET_LIST_SET=y
CONFIG_NETFILTER_XT_SET=y
CONFIG_NF_CONNTRACK_MARK=y
CONFIG_NETFILTER_XT_TARGET_CONNMARK=y
CONFIG_NETFILTER_XT_MATCH_CONNMARK=y
CONFIG_NET_SCH_CAKE=y
CONFIG_NET_SCH_FQ_CODEL=y

# === SUSFS Config ===
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_SUS_MAP=y
CONFIG_KSU_SUSFS_SUS_MOUNT=y
CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT=y
CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT=y
CONFIG_KSU_SUSFS_SUS_KSTAT=y
CONFIG_KSU_SUSFS_TRY_UMOUNT=y
CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT=y
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
CONFIG_KSU_SUSFS_ENABLE_LOG=y
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
CONFIG_KSU_SUSFS_OPEN_REDIRECT=y

# === MGLRU (Multi-Gen LRU) Config ===
CONFIG_LRU_GEN=y
CONFIG_LRU_GEN_ENABLED=y

# === PSI (Pressure Stall Information) Config ===
CONFIG_PSI=y
"""

    ZRAM_CONFIG_5_10 = "CONFIG_ZSMALLOC=y\nCONFIG_ZRAM=y\nCONFIG_MODULE_SIG=n\nCONFIG_CRYPTO_LZO=y\nCONFIG_ZRAM_DEF_COMP_LZ4KD=y\n"
    ZRAM_CONFIG_COMMON = "CONFIG_CRYPTO_LZ4HC=y\nCONFIG_CRYPTO_LZ4K=y\nCONFIG_CRYPTO_LZ4KD=y\nCONFIG_CRYPTO_842=y\nCONFIG_CRYPTO_LZ4K_OPLUS=y\nCONFIG_ZRAM_WRITEBACK=y\n"

    def __init__(self, config: BuildConfig, workspace: str):
        self.config = config
        self.workspace = Path(workspace)
        self.shell = ShellCommand(cwd=workspace)
        self.env = os.environ.copy()
        self.work_dir = self.workspace / config.config_name
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.susfs_dir = self.workspace / "susfs4ksu"
        self.sukisu_patch_dir = self.workspace / "SukiSU_patch"
        self.anykernel_dir = self.workspace / "AnyKernel3"
        self.kernel_patches_dir = self.workspace / "kernel_patches"
        self.toolchain_dir = self.workspace / "toolchain"
        self.mkbootimg_dir = self.workspace / "mkbootimg"
        self._setup_env()

    def _setup_env(self):
        self.env["CONFIG"] = self.config.config_name
        self.env["CCACHE_COMPILERCHECK"] = "%compiler% -dumpmachine; %compiler% -dumpversion"
        self.env["CCACHE_NOHASHDIR"] = "true"
        self.env["CCACHE_HARDLINK"] = "true"
        self.shell.env = self.env

    def _run_cmd(self, cmd: str, **kwargs) -> subprocess.CompletedProcess:
        return self.shell.run(cmd, **kwargs)

    def _chdir(self, path: Path):
        os.chdir(path)
        self.shell.cwd = str(path)

    def _apply_susfs_commit(self):
        if not self.config.susfs_commit or not self.susfs_dir.exists():
            return
        self._chdir(self.susfs_dir)
        if self.config.susfs_commit.startswith("HEAD~"):
            self._run_cmd("git fetch origin", check=False)
            self._run_cmd(f"git reset --hard {self.config.susfs_commit}", check=False)
        else:
            self._run_cmd("git fetch origin", check=False)
            self._run_cmd(f"git checkout {self.config.susfs_commit}", check=False)
        self._chdir(self.workspace)

    def clone_repositories(self):
        logger.info("=== Cloning repositories ===")
        for name, repo_dir, url, branch in [
            ("SUSFS", self.susfs_dir, SUSFS_REPO_CONFIG['repo_url'], self.config.kernel_branch),
            ("SukiSU Patch", self.sukisu_patch_dir, SUKISU_PATCH_REPO_CONFIG['repo_url'], None),
            ("AnyKernel3", self.anykernel_dir, ANYKERNEL_CONFIG['repo_url'], ANYKERNEL_CONFIG['branch']),
            ("Kernel Patches", self.kernel_patches_dir, KERNEL_PATCHES_CONFIG['repo_url'], None),
        ]:
            if not repo_dir.exists():
                cmd = f"git clone {url}"
                if branch:
                    cmd += f" -b {branch}"
                logger.info(f"Cloning {name}...")
                self._run_cmd(cmd, check=False)
            else:
                logger.info(f"{name} already exists, skipping")
        self._apply_susfs_commit()
        logger.info("=== Repository cloning complete ===")

    def clone_toolchain(self):
        logger.info("=== Cloning toolchain ===")
        if not self.toolchain_dir.exists():
            self._run_cmd(f"git clone {TOOLCHAIN_CONFIG['aosp_mirror']}/kernel/prebuilts/build-tools "
                         f"-b {TOOLCHAIN_CONFIG['build_tools_branch']} --depth 1 {self.toolchain_dir}", check=False)
        if not self.mkbootimg_dir.exists():
            self._run_cmd(f"git clone {TOOLCHAIN_CONFIG['aosp_mirror']}/platform/system/tools/mkbootimg "
                         f"-b {TOOLCHAIN_CONFIG['mkbootimg_branch']} --depth 1 {self.mkbootimg_dir}", check=False)
        self.env["AVBTOOL"] = str(self.toolchain_dir / "linux-x86/bin/avbtool")
        self.env["MKBOOTIMG"] = str(self.mkbootimg_dir / "mkbootimg.py")
        self.env["UNPACK_BOOTIMG"] = str(self.mkbootimg_dir / "unpack_bootimg.py")
        if "BOOT_SIGN_KEY_PATH" in os.environ:
            self.env["BOOT_SIGN_KEY_PATH"] = os.environ["BOOT_SIGN_KEY_PATH"]
        else:
            self.env["BOOT_SIGN_KEY_PATH"] = str(self._ensure_local_avb_key())
        self.shell.env = self.env
        logger.info("=== Toolchain ready ===")

    def _ensure_local_avb_key(self) -> Path:
        """Reuse a canonical local AVB RSA key for boot.img signing when no
        BOOT_SIGN_KEY_PATH is provided (e.g. local builds outside CI, where
        the GitHub Actions secret BOOT_SIGN_KEY doesn't exist). If the
        canonical key (workspace/boot_sign_key.pem) hasn't been placed there
        yet, generate a throwaway one as a safety net so the build doesn't
        fail - but for consistency with GitHub releases, copy the same key
        you use as the BOOT_SIGN_KEY secret into this exact path once."""
        key_path = self.workspace / "boot_sign_key.pem"
        if not key_path.exists():
            logger.info(f"No BOOT_SIGN_KEY_PATH set and no canonical key found - generating one: {key_path}")
            logger.info("For consistency with GitHub releases, replace this file with your canonical BOOT_SIGN_KEY.")
            self._run_cmd(f"openssl genrsa -out {key_path} 2048", check=False)
        return key_path

    def setup_repo_tool(self):
        logger.info("=== Installing repo tool ===")
        repo_dir = self.workspace / "git-repo"
        repo_dir.mkdir(exist_ok=True)
        repo_path = repo_dir / "repo"
        if not repo_path.exists():
            self._run_cmd(f"curl https://storage.googleapis.com/git-repo-downloads/repo > {repo_path}", check=False)
            self._run_cmd(f"chmod a+rx {repo_path}", check=False)
        self.env["REPO"] = str(repo_path)
        self.shell.env = self.env

    def init_and_sync_kernel(self):
        logger.info("=== Initializing and syncing kernel source ===")
        self._chdir(self.work_dir)
        formatted_branch = self.config.formatted_branch

        self._run_cmd(f"$REPO init --depth=1 -u https://android.googlesource.com/kernel/manifest "
                     f"-b common-{formatted_branch} --repo-rev=v2.16", check=False)

        remote = subprocess.run(f"git ls-remote https://android.googlesource.com/kernel/common {formatted_branch}",
                               shell=True, capture_output=True, text=True).stdout.strip()
        if "deprecated" in remote:
            manifest_path = self.work_dir / ".repo/manifests/default.xml"
            with open(manifest_path, "r") as f:
                content = f.read()
            content = content.replace(f'"{formatted_branch}"', f'"deprecated/{formatted_branch}"')
            with open(manifest_path, "w") as f:
                f.write(content)

        self.env["REMOTE_BRANCH"] = remote
        logger.info("Syncing kernel source...")
        self._run_cmd("$REPO --trace sync -c -j$(nproc --all) --no-tags --fail-fast", check=False)

        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            raise RuntimeError("repo sync failed, common directory does not exist")
        self._apply_legacy_fixes(remote)

        if self.config.kernel_tag:
            self._checkout_kernel_tag(common_dir)

        logger.info("=== Kernel source sync complete ===")

    def _checkout_kernel_tag(self, common_dir: Path):
        """Pin kernel/common to a specific respin tag (e.g.
        android13-5.15-2025-12_r10) instead of the moving branch HEAD.
        repo sync runs with --no-tags, so the tag must be fetched explicitly."""
        tag = self.config.kernel_tag
        logger.info(f"=== Pinning kernel source to tag: {tag} ===")
        self._chdir(common_dir)
        self._run_cmd(
            f"git fetch --depth=1 https://android.googlesource.com/kernel/common "
            f"refs/tags/{tag}:refs/tags/{tag}", check=False)
        result = self._run_cmd(f"git checkout {tag}", check=False)

        # Write the exact desired release suffix directly into .scmversion.
        # setlocalversion uses this file's content verbatim instead of
        # calling `git describe`, giving full, predictable control over the
        # final kernel release string (e.g. "5.15.194-android13-r10")
        # regardless of build system (legacy build.sh vs Bazel/Kleaf) - the
        # separate CONFIG_LOCALVERSION/custom_version mechanisms elsewhere
        # in this file are gated inconsistently between the two build paths,
        # so this is the one reliable, universal way to control it.
        m = re.search(r'_r(\d+)$', tag)
        respin_suffix = f"-{self.config.android_version}-r{m.group(1)}" if m else ""
        (common_dir / ".scmversion").write_text(respin_suffix)

        self._chdir(self.work_dir)
        return result

    def _apply_legacy_fixes(self, remote_branch: str = ""):
        av, kv = self.config.android_version, self.config.kernel_version
        sub = self.config.get_sub_level_int()
        is_deprecated = "deprecated" in remote_branch

        if is_deprecated and av == "android13" and kv == "5.15" and sub and sub < 123:
            common_dir = self.work_dir / "common"
            self._chdir(common_dir)
            self._run_cmd(f"curl -LSs {LEGACY_FIXES['android13-5.15-below-123']['url']} -o fix.patch && patch -p1 < fix.patch", check=False)
            self._chdir(self.work_dir)

        if av == "android12" and kv == "5.10" and sub and sub < 136:
            common_dir = self.work_dir / "common"
            self._chdir(common_dir)
            self._run_cmd(f"curl -LSs {LEGACY_FIXES['android12-5.10-below-136']['url']} | patch -p1", check=False)
            self._chdir(self.work_dir)

    def add_kernel_supatch(self):
        if not self.config.support_op8e:
            return
        logger.info("=== Adding OnePlus 8E support patch ===")
        drivers_dir = self.work_dir / "common/drivers"
        if not drivers_dir.exists():
            return
        self._chdir(drivers_dir)
        self._run_cmd(f"curl -LSs {OP8E_PATCH_URL} -o hmbird_patch.c", check=False)
        if (drivers_dir / "hmbird_patch.c").exists():
            with open(drivers_dir / "Makefile", "a") as f:
                f.write("obj-y += hmbird_patch.o\n")

    def add_kernelsu(self):
        logger.info("=== Adding KernelSU ===")
        self._chdir(self.work_dir)
        setup_url = (f"https://raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra/{self.config.kernelsu_commit}/kernel/setup.sh"
                    if self.config.kernelsu_commit else KSU_REPO_CONFIG["setup_script"])
        self._run_cmd(f"curl -LSs {setup_url} | bash -s builtin", check=False)
        if self.config.kernelsu_commit:
            ksu_dir = self.work_dir / "KernelSU"
            if ksu_dir.exists():
                self._chdir(ksu_dir)
                self._run_cmd(f"git checkout {self.config.kernelsu_commit}", check=False)
                self._chdir(self.work_dir)

    def add_bbg(self):
        if not self.config.use_bbg:
            return
        logger.info("=== Adding Baseband-guard ===")
        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            return
        self._chdir(common_dir)
        self._run_cmd(f"wget -O- {BBG_CONFIG['setup_script']} | bash", check=False)
        config_file = common_dir / "arch/arm64/configs/gki_defconfig"
        if config_file.exists():
            with open(config_file, "a") as f:
                f.write("CONFIG_BBG=y\n")
        kconfig_file = common_dir / "security/Kconfig"
        if kconfig_file.exists():
            with open(kconfig_file, "r") as f:
                content = f.read()
            content = re.sub(r'(config LSM.*?)(default .*)(\n.*?help)',
                           lambda m: m.group(1) + ('lockdown,baseband_guard' if 'lockdown' in m.group(2) and 'baseband_guard' not in m.group(2) else m.group(2)) + m.group(3),
                           content, flags=re.DOTALL)
            with open(kconfig_file, "w") as f:
                f.write(content)

    def apply_susfs_patches(self):
        logger.info("=== Applying SUSFS patches ===")
        self._chdir(self.work_dir)
        common_dir = self.work_dir / "common"
        susfs_patch = self.susfs_dir / "kernel_patches" / self.config.get_susfs_patch_filename()
        if susfs_patch.exists():
            self._run_cmd(f"cp {susfs_patch} {common_dir}/", check=False)
        for src, dst in [
            (self.susfs_dir / "kernel_patches/fs", common_dir / "fs/"),
            (self.susfs_dir / "kernel_patches/include/linux", common_dir / "include/linux/"),
        ]:
            if src.exists():
                self._run_cmd(f"cp -r {src}/* {dst}", check=False)
        if susfs_patch.exists():
            patch_file = common_dir / self.config.get_susfs_patch_filename()
            if patch_file.exists():
                self._chdir(common_dir)
                self._run_cmd(f"patch -p1 --fuzz=3 < {patch_file}", check=False)
                self._chdir(self.work_dir)

    def apply_sukisu_patches(self):
        logger.info("=== Applying SukiSU patches ===")
        self._chdir(self.work_dir / "common")
        hooks_patch = self.sukisu_patch_dir / "69_hide_stuff.patch"
        if hooks_patch.exists():
            self._run_cmd(f"cp {hooks_patch} . && patch -p1 -F 3 < 69_hide_stuff.patch", check=False)

    def apply_zram_patches(self):
        if not self.config.use_zram:
            return
        logger.info("=== Applying ZRAM (LZ4KD) patches ===")
        self._chdir(self.work_dir / "common")
        for src in [
            (self.sukisu_patch_dir / "other/zram/lz4k/include/linux", "include/linux/"),
            (self.sukisu_patch_dir / "other/zram/lz4k/lib", "lib/"),
            (self.sukisu_patch_dir / "other/zram/lz4k/crypto", "crypto/"),
        ]:
            if src[0].exists():
                self._run_cmd(
                    f"find {src[0]} -mindepth 1 -maxdepth 1 ! -name Kconfig ! -name Makefile -exec cp -r {{}} {src[1]} \\;",
                    check=False,
                )
        oplus_src = self.sukisu_patch_dir / "other/zram/lz4k_oplus"
        if oplus_src.exists():
            self._run_cmd("mkdir -p lib/lz4k_oplus", check=False)
            self._run_cmd(f"cp -r {oplus_src}/* lib/lz4k_oplus/", check=False)
        zram_patch_dir = self.sukisu_patch_dir / f"other/zram/zram_patch/{self.config.kernel_version}"
        for patch in ["lz4kd.patch", "lz4k_oplus.patch"]:
            p = zram_patch_dir / patch
            if p.exists():
                self._run_cmd(f"patch -p1 -F 3 < {p}", check=False)

    def apply_task_mmu_fixes(self):
        logger.info("=== Applying task_mmu.c fixes ===")
        self._chdir(self.work_dir / "common")
        task_mmu = Path("fs/proc/task_mmu.c")
        if not task_mmu.exists():
            return

        fb = f"{self.config.android_version}-{self.config.kernel_version}"
        with open(task_mmu, "r") as f:
            content = f.read()

        if fb == "android15-6.6" and "unsigned int nr_subpages" not in content:
            self._fix_base_c_header()
        elif fb == "android14-6.1" and "if (!vma_pages(vma))" not in content:
            self._fix_base_c_header()
            if "goto show_pad;" in content:
                content = content.replace("goto show_pad;", "return 0;")
                with open(task_mmu, "w") as f:
                    f.write(content)
        elif fb in ["android12-5.10", "android13-5.10", "android13-5.15"] and "if (!vma_pages(vma))" not in content:
            if "goto show_pad;" in content:
                content = content.replace("goto show_pad;", "return 0;")
                with open(task_mmu, "w") as f:
                    f.write(content)

        with open(task_mmu, "r") as f:
            content = f.read()
        if "struct dentry *dentry;\n" in content:
            content = content.replace("struct dentry *dentry;\n", "struct dentry *dentry = NULL;\n")
            with open(task_mmu, "w") as f:
                f.write(content)

    def _fix_base_c_header(self):
        base_c = self.work_dir / "common/fs/proc/base.c"
        if not base_c.exists():
            return
        with open(base_c, "r") as f:
            content = f.read()
        if "#include <linux/dma-buf.h>" not in content:
            content = content.replace("#include <linux/cpufreq_times.h>",
                                    "#include <linux/cpufreq_times.h>\n#include <linux/dma-buf.h>")
            with open(base_c, "w") as f:
                f.write(content)

    def configure_kernel(self):
        logger.info("=== Configuring kernel ===")
        self._chdir(self.work_dir)
        config_file = self.work_dir / "common/arch/arm64/configs/gki_defconfig"
        if not config_file.exists():
            logger.warning(f"Config file does not exist: {config_file}")
            return

        with open(config_file, "a") as f:
            f.write(self.KERNEL_CONFIG_TEMPLATE)
            if self.config.kernel_version != "6.6":
                f.write("CONFIG_KSU_SUSFS_SUS_PATH=y\n")
            else:
                f.write("CONFIG_KSU_SUSFS_SUS_PATH=n\n")

        if self.config.use_zram:
            self._configure_zram()
            self._configure_bazel()

        if self.config.bbr_version == "bbr1":
            with open(config_file, "a") as f:
                f.write("CONFIG_DEFAULT_BBR=y\n")

        build_config = self.work_dir / "common/build.config.gki"
        if build_config.exists():
            with open(build_config, "r") as f:
                content = f.read()
            content = content.replace("check_defconfig", "")
            with open(build_config, "w") as f:
                f.write(content)

    def _configure_zram(self):
        config_file = self.work_dir / "common/arch/arm64/configs/gki_defconfig"
        with open(config_file, "r") as f:
            content = f.read()
        kv = self.config.kernel_version
        if kv == "5.10":
            with open(config_file, "a") as f:
                f.write(self.ZRAM_CONFIG_5_10)
        else:
            content = content.replace("CONFIG_ZRAM=m", "CONFIG_ZRAM=y")
            with open(config_file, "w") as f:
                f.write(content)
            with open(config_file, "a") as f:
                f.write("CONFIG_ZSMALLOC=y\n")
        with open(config_file, "a") as f:
            f.write(self.ZRAM_CONFIG_COMMON)

    def _configure_bazel(self):
        modules_bzl = self.work_dir / "common/modules.bzl"
        if modules_bzl.exists():
            with open(modules_bzl, "r") as f:
                content = f.read()
            modified = False
            for old in ['"drivers/block/zram/zram.ko",\n', '"drivers/block/zram/zram.ko",',
                       '"mm/zsmalloc.ko",\n', '"mm/zsmalloc.ko",']:
                if old in content:
                    content = content.replace(old, '')
                    modified = True
            if modified:
                with open(modules_bzl, "w") as f:
                    f.write(content)
        config_file = self.work_dir / "common/arch/arm64/configs/gki_defconfig"
        with open(config_file, "a") as f:
            f.write("CONFIG_MODULE_SIG_FORCE=n\n")

    def configure_kernel_name(self):
        logger.info("=== Configuring kernel name ===")
        self._chdir(self.work_dir)
        MAX_CUSTOM_LEN = 48
        safe_custom_version = ""
        if self.config.custom_version:
            safe_custom_version = self.config.custom_version.rstrip('-')[:MAX_CUSTOM_LEN]

        setlocalversion = self.work_dir / "common/scripts/setlocalversion"
        if setlocalversion.exists():
            with open(setlocalversion, "r") as f:
                content = f.read()
            if safe_custom_version:
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if 'echo "$res"' in line and not line.strip().startswith('#'):
                        lines[i] = f'\techo "{safe_custom_version}$res"'
                        break
                with open(setlocalversion, "w") as f:
                    f.write('\n'.join(lines))
            if "-dirty" in content:
                content = content.replace("-dirty", "")
                with open(setlocalversion, "w") as f:
                    f.write(content)

        import datetime
        current_time = datetime.datetime.utcnow().strftime("%a %b %d %H:%M:%S UTC %Y")
        mkcompile_h = self.work_dir / "common/scripts/mkcompile_h"
        if mkcompile_h.exists():
            with open(mkcompile_h, "r") as f:
                content = f.read()
            content = content.replace('UTS_VERSION="$(echo $UTS_VERSION $CONFIG_FLAGS $TIMESTAMP | cut -b -$UTS_LEN)"',
                                    f'UTS_VERSION="#1 SMP PREEMPT {current_time}"')
            with open(mkcompile_h, "w") as f:
                f.write(content)

        if self.config.kernel_version in ["6.1", "6.6"]:
            init_makefile = self.work_dir / "common/init/Makefile"
            if init_makefile.exists():
                with open(init_makefile, "r") as f:
                    content = f.read()
                content = content.replace('$(preempt-flag-y) "$(build-timestamp)"', f'$(preempt-flag-y) "{current_time}"')
                with open(init_makefile, "w") as f:
                    f.write(content)

        if not (self.work_dir / "build/build.sh").exists():
            bazel_build = self.work_dir / "common/BUILD.bazel"
            if bazel_build.exists():
                with open(bazel_build, "r") as f:
                    content = f.read()
                lines = [l for l in content.split('\n') if '"protected_exports_list"' not in l or 'android/abi_gki_protected_exports_aarch64' not in l]
                with open(bazel_build, "w") as f:
                    f.write('\n'.join(lines))

            abi_path = self.work_dir / "common/android/abi_gki_protected_exports_aarch64"
            if abi_path.exists():
                import shutil
                try:
                    if abi_path.is_dir():
                        shutil.rmtree(abi_path)
                    else:
                        abi_path.unlink()
                except Exception:
                    pass

            stamp_bzl = self.work_dir / "build/kernel/kleaf/impl/stamp.bzl"
            if stamp_bzl.exists():
                with open(stamp_bzl, "r") as f:
                    content = f.read()
                content = content.replace("-maybe-dirty", "")
                with open(stamp_bzl, "w") as f:
                    f.write(content)

            if self.config.custom_version:
                config_file = self.work_dir / "common/arch/arm64/configs/gki_defconfig"
                if config_file.exists():
                    with open(config_file, "r") as f:
                        content = f.read()
                    content = re.sub(r'^CONFIG_LOCALVERSION=".*"$', f'CONFIG_LOCALVERSION="{self.config.custom_version}"', content, flags=re.MULTILINE)
                    with open(config_file, "w") as f:
                        f.write(content)
                else:
                    logger.warning(f"Config file does not exist, skipping custom_version setting: {config_file}")

    def show_kernel_config(self):
        logger.info("=== Displaying kernel config list ===")
        self._chdir(self.work_dir)
        config_file = self.work_dir / "common/arch/arm64/configs/gki_defconfig"
        
        if not config_file.exists():
            logger.warning(f"Config file does not exist: {config_file}")
            return
        
        with open(config_file, "r") as f:
            lines = f.readlines()
        
        config_lines = [line.strip() for line in lines if line.strip().startswith("CONFIG_")]
        
        key_configs = {
            "CONFIG_KSU": "KernelSU",
            "CONFIG_KPM": "KPM",
            "CONFIG_KSU_SUSFS": "SUSFS",
            "CONFIG_BBG": "Baseband-guard",
            "CONFIG_BBR": "BBR",
            "CONFIG_ZRAM": "ZRAM",
        }
        
        logger.info("Key config status:")
        for prefix, name in key_configs.items():
            found = [c for c in config_lines if c.startswith(prefix)]
            if found:
                status = "enabled"
            else:
                status = "not configured"
            logger.info(f"  [{status}] {name}")
            if found:
                for f in sorted(found):
                    logger.info(f"      -> {f}")
        
        # Show ZRAM related config
        if self.config.use_zram:
            zram_configs = [c for c in config_lines if any(x in c for x in ["ZRAM", "ZSMALLOC", "LZ4", "LZ4KD", "CRYPTO_LZ4", "MODULE_SIG"])]
            if zram_configs:
                logger.info("ZRAM related config:")
                for zc in sorted(zram_configs):
                    logger.info(f"  -> {zc}")
        
        logger.info("-" * 60)

    def _canonicalize_defconfig(self):
        """Bazel/Kleaf's kernel_config rule strictly requires
        gki_defconfig to be in canonical `make savedefconfig` form
        (minimal, sorted, no lines matching Kconfig defaults) - it fails
        the build with 'savedefconfig does not match ...' otherwise.
        Our custom CONFIG_ additions (KSU, SUSFS, ZRAM, BBR, etc.) are
        appended as plain text, so after every change we regenerate the
        canonical form ourselves using the kernel's own host Kconfig
        tooling (no cross-compiler needed for this step) and write it
        back, so Bazel's check passes."""
        import tempfile
        logger.info("=== Canonicalizing gki_defconfig for Bazel (savedefconfig) ===")
        common_dir = self.work_dir / "common"
        defconfig_path = common_dir / "arch/arm64/configs/gki_defconfig"
        if not defconfig_path.exists():
            logger.warning(f"gki_defconfig not found at {defconfig_path}, skipping canonicalization")
            return

        with tempfile.TemporaryDirectory(prefix="savedefconfig_") as tmpdir:
            self._chdir(common_dir)
            expand = self._run_cmd(f"make ARCH=arm64 O={tmpdir} gki_defconfig", check=False)
            if expand.returncode != 0:
                logger.warning("Failed to expand gki_defconfig for canonicalization, leaving as-is")
                self._chdir(self.work_dir)
                return
            save = self._run_cmd(f"make ARCH=arm64 O={tmpdir} savedefconfig", check=False)
            self._chdir(self.work_dir)
            if save.returncode != 0:
                logger.warning("savedefconfig failed, leaving gki_defconfig as-is")
                return

            canonical = Path(tmpdir) / "defconfig"
            if canonical.exists():
                canonical_content = canonical.read_text()
                defconfig_path.write_text(canonical_content)
                logger.info("gki_defconfig canonicalized successfully")
            else:
                logger.warning("savedefconfig did not produce an output file, leaving gki_defconfig as-is")

    def build_kernel(self) -> bool:
        logger.info("=== Starting kernel compilation ===")
        self._chdir(self.work_dir)

        build_config = self.work_dir / "common/build.config.gki.aarch64"
        if build_config.exists():
            with open(build_config, "r") as f:
                content = f.read()
            content = content.replace("BUILD_SYSTEM_DLKM=1", "BUILD_SYSTEM_DLKM=0")
            lines = [l for l in content.split('\n') if 'MODULES_ORDER=android/gki_aarch64_modules' not in l and 'KMI_SYMBOL_LIST_STRICT_MODE' not in l]
            with open(build_config, "w") as f:
                f.write('\n'.join(lines))

        try:
            if (self.work_dir / "build/build.sh").exists():
                logger.info("Using legacy build method...")
                result = self._run_cmd("LTO=thin BUILD_CONFIG=common/build.config.gki.aarch64 build/build.sh CC=\"/usr/bin/ccache clang\"", check=False)
            else:
                logger.info("Using Bazel build method...")
                self._canonicalize_defconfig()
                bazel_cache = Path.home() / ".cache" / "bazel"
                bazel_cache.mkdir(parents=True, exist_ok=True)
                result = self._run_cmd(
                    f"tools/bazel build --disk_cache={bazel_cache} --config=fast --lto=thin //common:kernel_aarch64_dist",
                    check=False)

            if result.returncode == 0:
                logger.info("=== Kernel compilation succeeded ===")
                return True
            logger.error(f"Kernel compilation failed: {result.stderr if result.stderr else 'Unknown error'}")
            return False
        except Exception as e:
            logger.error(f"Error during compilation: {e}")
            return False

    def patch_kpm_image(self):
        if not self.config.use_kpm or self.config.kernel_version == "6.6":
            return
        logger.info("=== Patching Image file (KPM) ===")
        self._chdir(self.work_dir)

        if self.config.android_version in ["android12", "android13"]:
            image_dir = self.work_dir / f"out/{self.config.android_version}-{self.config.kernel_version}/dist"
        else:
            image_dir = self.work_dir / "bazel-bin/common/kernel_aarch64"

        if not image_dir.exists():
            return
        self._chdir(image_dir)
        self._run_cmd(f"curl -LSs {KPM_PATCH_URL} -o patch && chmod 777 patch && ./patch", check=False)
        if (image_dir / "oImage").exists():
            self._run_cmd("mv oImage Image", check=False)

    def prepare_boot_images(self) -> list:
        logger.info("=== Preparing boot images ===")
        self._chdir(self.work_dir)
        bootimgs_dir = self.work_dir / "bootimgs"
        bootimgs_dir.mkdir(exist_ok=True)
        artifacts = []

        if self.config.android_version in ["android12", "android13"]:
            image_source = self.work_dir / f"out/{self.config.android_version}-{self.config.kernel_version}/dist"
        else:
            image_source = self.work_dir / "bazel-bin/common/kernel_aarch64"

        for image_name in ["Image", "Image.lz4"]:
            src = image_source / image_name
            if src.exists():
                self._run_cmd(f"cp {src} {bootimgs_dir}/ && cp {src} {self.work_dir}/", check=False)

        if (self.work_dir / "Image").exists():
            self._run_cmd("gzip -n -k -f -9 Image", check=False)

        if self.config.android_version == "android12":
            self._prepare_android12_boot_images(bootimgs_dir, artifacts)
        else:
            self._prepare_boot_images_generic(bootimgs_dir, artifacts)
        return artifacts

    def _prepare_android12_boot_images(self, bootimgs_dir: Path, artifacts: list):
        self._chdir(bootimgs_dir)
        gki_url = f"https://dl.google.com/android/gki/gki-certified-boot-android12-5.10-{self.config.os_patch_level}_{self.config.revision}.zip"
        fallback_url = "https://dl.google.com/android/gki/gki-certified-boot-android12-5.10-2023-01_r1.zip"
        result = subprocess.run(f"curl -sL -w '%{{http_code}}' {gki_url} -o /dev/null", shell=True, capture_output=True, text=True)
        url = gki_url if "200" in result.stdout else fallback_url
        self._run_cmd(f"curl -Lo gki-kernel.zip {url} && unzip -o gki-kernel.zip && rm gki-kernel.zip", check=False)
        boot_img_path = bootimgs_dir / "boot-5.10.img"
        if boot_img_path.exists():
            self._run_cmd(f"$UNPACK_BOOTIMG --boot_img={boot_img_path}", check=False)
        self._create_boot_image_variants(bootimgs_dir, artifacts, has_ramdisk=True)

    def _prepare_boot_images_generic(self, bootimgs_dir: Path, artifacts: list):
        self._chdir(bootimgs_dir)
        self._create_boot_image_variants(bootimgs_dir, artifacts, has_ramdisk=False)

    def _create_boot_image_variants(self, bootimgs_dir: Path, artifacts: list, has_ramdisk: bool = False):
        self._chdir(bootimgs_dir)
        if (bootimgs_dir / "Image").exists():
            self._run_cmd("gzip -n -k -f -9 Image", check=False)

        for kernel_file, output_file in [("Image", "boot.img"), ("Image.gz", "boot-gz.img"), ("Image.lz4", "boot-lz4.img")]:
            kernel_path = bootimgs_dir / kernel_file
            if not kernel_path.exists():
                continue
            cmd = f"$MKBOOTIMG --header_version 4 --kernel {kernel_file} --output {output_file}"
            if has_ramdisk:
                cmd += f" --ramdisk out/ramdisk --os_version 12.0.0 --os_patch_level {self.config.os_patch_level}"
            self._run_cmd(cmd, check=False)
            self._run_cmd(f"$AVBTOOL add_hash_footer --partition_name boot --partition_size $((64 * 1024 * 1024)) --image {output_file} --algorithm SHA256_RSA2048 --key $BOOT_SIGN_KEY_PATH", check=False)
            dest = self.work_dir / f"{self.config.android_version}-{self.config.kernel_version}.{self.config.sub_level}-{self.config.os_patch_level}-{output_file}"
            self._run_cmd(f"cp {output_file} {dest}", check=False)
            artifacts.append(str(dest))

    def create_anykernel_zips(self) -> list:
        logger.info("=== Creating AnyKernel3 ZIP files ===")
        self._chdir(self.work_dir)
        artifacts = []
        ak3_dir = self.anykernel_dir

        for suffix in ["", "-lz4", "-gz"]:
            image_file = f"Image{suffix}"
            image_path = self.work_dir / image_file
            if not image_path.exists():
                continue
            zip_name = f"{self.config.android_version}-{self.config.kernel_version}.{self.config.sub_level}-{self.config.os_patch_level}-AnyKernel3{suffix}.zip"
            self._run_cmd(f"cp {image_path} {ak3_dir}/", check=False)
            self._chdir(ak3_dir)
            self._run_cmd(f"zip -r ../{zip_name} ./*", check=False)
            self._run_cmd(f"rm {ak3_dir}/{image_file}", check=False)
            artifacts.append(str(self.work_dir / zip_name))
            self._chdir(self.work_dir)
        return artifacts

    def apply_safemode_patch(self):
        """Permanently disable KernelSU/SukiSU volume-key safe-mode
        detection (ksud.c). Most users rely on Yet Another Bootloop
        Protector instead, and the volume-key combo can trigger by
        accident. Locates ksud.c dynamically instead of assuming a fixed
        path, since SukiSU-Ultra's internal source layout isn't something
        we control."""
        logger.info("=== Disabling safe mode (ksud.c) ===")
        find_result = self._run_cmd(
            f"find {self.work_dir} -path '*/runtime/ksud.c' -type f",
            check=False, capture_output=True)
        target_files = [l.strip() for l in (find_result.stdout or "").splitlines() if l.strip()]
        if not target_files:
            logger.warning("Could not find ksud.c - skipping safe-mode patch")
            return

        target = target_files[0]
        patch_src = Path(__file__).parent / "patches" / "disable-safemode-full.patch"
        if not patch_src.exists():
            logger.warning(f"Safe-mode patch file not found at {patch_src} - skipping")
            return

        result = self._run_cmd(f"patch {target} < {patch_src}", check=False)
        if result.returncode == 0:
            logger.info(f"Safe mode disabled successfully: {target}")
        else:
            logger.warning(f"Safe-mode patch did not apply cleanly to {target} - "
                          "ksud.c may have changed upstream, continuing without it")

    def build(self) -> BuildResult:
        import time
        start_time = time.time()
        logger.info("=" * 50)
        logger.info(f"Starting GKI Kernel build - {self.config.config_name}")
        logger.info("=" * 50)

        try:
            self.clone_repositories()
            self.clone_toolchain()
            self.setup_repo_tool()
            self.init_and_sync_kernel()
            self.add_kernel_supatch()
            self.add_kernelsu()
            if self.config.disable_safemode:
                self.apply_safemode_patch()
            self.add_bbg()
            self.apply_susfs_patches()
            self.apply_sukisu_patches()
            self.apply_zram_patches()
            self.apply_task_mmu_fixes()
            self.configure_kernel()
            self.configure_kernel_name()
            self.show_kernel_config()

            if not self.build_kernel():
                return BuildResult(success=False, config=self.config, message="Kernel compilation failed", build_time=time.time() - start_time)

            self.patch_kpm_image()
            artifacts = []
            artifacts.extend(self.prepare_boot_images())
            artifacts.extend(self.create_anykernel_zips())

            build_time = time.time() - start_time
            logger.info(f"Build succeeded! Time: {build_time:.2f}s, generated {len(artifacts)} artifact(s)")
            return BuildResult(success=True, config=self.config, message="Build succeeded", artifacts=artifacts, build_time=build_time)
        except Exception as e:
            logger.error(f"Error during build: {e}")
            return BuildResult(success=False, config=self.config, message=str(e), build_time=time.time() - start_time)
