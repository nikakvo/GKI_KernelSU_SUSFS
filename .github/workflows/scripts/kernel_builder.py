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

# === Wireguard Config (forced on regardless of the branch's own
# gki_defconfig default, since not every branch ships it enabled) ===
CONFIG_WIREGUARD=y

# === NTSync Config (NT sync primitives, e.g. for Winlator/Wine) ===
CONFIG_NTSYNC=y
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
        self.detected_respin = None
        # Tracks the outcome of every patch/feature step so it can be
        # surfaced in a per-build report and, across the whole matrix, in
        # a single summary table in the GitHub Actions job summary -
        # instead of that only being visible by digging through each
        # build's raw log. Values: "applied", "skipped" (not requested /
        # not applicable to this branch), or "failed" (attempted but did
        # not apply cleanly - build may still continue depending on the
        # step).
        self.patch_status: dict = {}
        self._setup_env()

    def _mark(self, name: str, status: str, detail: str = ""):
        self.patch_status[name] = {"status": status, "detail": detail}
        icon = {"applied": "OK", "skipped": "SKIP", "failed": "FAIL"}.get(status, status)
        logger.info(f"[patch-status] {name}: {icon}" + (f" ({detail})" if detail else ""))

    def _write_patch_status(self):
        """Writes PATCH_STATUS.json into work_dir - picked up by the
        workflow's 'Record build result' step and later aggregated across
        the whole matrix into one summary table."""
        import json as _json
        report_path = self.work_dir / "PATCH_STATUS.json"
        data = {
            "config": self.config.config_name,
            "android_version": self.config.android_version,
            "kernel_version": self.config.kernel_version,
            "sub_level": self.config.sub_level,
            "os_patch_level": self.config.os_patch_level,
            "kernel_respin": self.detected_respin or "",
            "is_lts": self.is_lts_build,
            "patches": self.patch_status,
        }
        report_path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
        logger.info(f"Patch status written to: {report_path}")

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
                result = self._run_cmd(cmd, check=False)
                if name == "SUSFS" and result.returncode != 0:
                    raise RuntimeError(
                        f"Failed to clone SUSFS branch '{branch}' from {url} "
                        f"(git clone exit code {result.returncode}).\n"
                        f"This branch may not exist yet on this fork - susfs4ksu "
                        f"forks can lag behind upstream for newer Android/kernel "
                        f"combos (e.g. ShirkNeko's fork didn't have "
                        f"gki-android16-6.12 for a while after it existed "
                        f"upstream). Check: {url.replace('.git', '')}/branches\n"
                        f"Failing here instead of continuing into a long kernel "
                        f"repo sync that would fail later anyway."
                    )
            else:
                logger.info(f"{name} already exists, skipping clone")
                if branch:
                    # This clone is reused across builds (cleanup-workspace.sh
                    # keeps it on purpose for speed), but the branch this
                    # build needs (e.g. SUSFS's branch depends on the
                    # android/kernel combo) may differ from whatever branch
                    # a previous build last left it on. Make sure it's on
                    # the right one before continuing, instead of silently
                    # using whatever happens to be checked out.
                    self._chdir(repo_dir)
                    fetch_result = self._run_cmd(f"git fetch origin {branch}", check=False)
                    self._run_cmd(f"git checkout {branch}", check=False)
                    self._run_cmd(f"git reset --hard origin/{branch}", check=False)
                    self._chdir(self.workspace)
                    if name == "SUSFS" and fetch_result.returncode != 0:
                        raise RuntimeError(
                            f"Failed to fetch SUSFS branch '{branch}' from {url} "
                            f"(git fetch exit code {fetch_result.returncode}).\n"
                            f"This branch may not exist yet on this fork - susfs4ksu "
                            f"forks can lag behind upstream for newer Android/kernel "
                            f"combos (e.g. ShirkNeko's fork didn't have "
                            f"gki-android16-6.12 for a while after it existed "
                            f"upstream). Check: {url.replace('.git', '')}/branches\n"
                            f"Failing here instead of continuing into a long kernel "
                            f"repo sync that would fail later anyway."
                        )
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
        """Pin kernel/common to a specific respin (e.g.
        android13-5.15-2025-12_r10) OR a raw commit SHA. repo sync runs
        with --no-tags, so a tag must be fetched explicitly; a SHA is
        fetched directly since it's not a ref at all.

        SHA support exists because Google sometimes lands an LTS-merge
        commit (e.g. "Merge 5.15.211 into android13-5.15-lts") on the
        branch well before cutting the corresponding official
        "androidX-Y.YY.NNN_r00" tag for it - a SHA lets a build pin that
        exact commit immediately instead of waiting on the tag.

        Fails the build loudly if the tag/SHA doesn't exist upstream or
        the checkout otherwise fails - silently falling through here
        would leave the source on whatever repo sync's moving-HEAD
        checkout happened to be (a real, differently-numbered sub_level),
        while every downstream filename/version string still confidently
        labels the build with the WRONG, requested sub_level. A build
        that silently compiles the wrong kernel and calls it the right
        one is much worse than a build that fails clearly.
        """
        ref = self.config.kernel_tag
        self._chdir(common_dir)

        if self._is_commit_sha(ref):
            logger.info(f"=== Pinning kernel source to commit SHA: {ref} ===")
            fetch_result = self._run_cmd(
                f"git fetch --depth=1 https://android.googlesource.com/kernel/common {ref}",
                check=False)
            if fetch_result.returncode != 0:
                self._chdir(self.work_dir)
                raise RuntimeError(
                    f"kernel_tag '{ref}' looks like a commit SHA but could "
                    f"not be fetched from android.googlesource.com/kernel/common "
                    f"- it may not exist, be too short/ambiguous, or not yet "
                    f"be reachable from a branch the server exposes for "
                    f"direct SHA fetch. Refusing to silently continue on "
                    f"the moving branch HEAD."
                )
            result = self._run_cmd("git checkout FETCH_HEAD", check=False)
            self._chdir(self.work_dir)
            if result.returncode != 0:
                raise RuntimeError(
                    f"commit SHA '{ref}' was fetched but 'git checkout "
                    f"FETCH_HEAD' failed. Refusing to silently continue "
                    f"on the moving branch HEAD."
                )
            return result

        tag = ref
        logger.info(f"=== Pinning kernel source to tag: {tag} ===")
        fetch_result = self._run_cmd(
            f"git fetch --depth=1 https://android.googlesource.com/kernel/common "
            f"refs/tags/{tag}:refs/tags/{tag}", check=False)
        if fetch_result.returncode != 0:
            self._chdir(self.work_dir)
            raise RuntimeError(
                f"kernel_tag '{tag}' could not be fetched from "
                f"android.googlesource.com/kernel/common - it likely "
                f"doesn't exist upstream (typo, not published yet, or "
                f"only landed as a commit so far without an official "
                f"tag - in that case pass the commit SHA instead). "
                f"Refusing to silently continue on the moving branch "
                f"HEAD, which would compile a DIFFERENT, real sub_level "
                f"while every artifact filename and on-device version "
                f"string still claims to be '{self.config.sub_level}'. "
                f"Verify the tag at https://android.googlesource.com/kernel/common/+refs "
                f"before retrying."
            )
        result = self._run_cmd(f"git checkout {tag}", check=False)
        self._chdir(self.work_dir)
        if result.returncode != 0:
            raise RuntimeError(
                f"kernel_tag '{tag}' was fetched but 'git checkout {tag}' "
                f"failed - the ref may be corrupt or ambiguous. Refusing "
                f"to silently continue on the moving branch HEAD."
            )
        return result

    @staticmethod
    def _is_commit_sha(value: str) -> bool:
        """True for a bare git commit SHA (short or full, 7-40 hex
        chars) - as opposed to a tag name like
        'android13-5.15-2026-06_r4' or 'android13-5.15.211_r00', which
        always contain non-hex characters ('android', '-', '_')."""
        return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", value))

    # Matches the newer per-sublevel LTS-merge tag scheme
    # (android13-5.15.209_r00) - as opposed to the older date-based
    # scheme (android13-5.15-2026-06_r4). The dot right before the
    # sub_level number is what distinguishes the two; the dash scheme
    # never has a literal dot there.
    _LTS_DOT_TAG_RE = re.compile(r'^android1[2-7]-\d+\.\d+\.\d+_r\d+$')

    @property
    def is_lts_build(self) -> bool:
        """Whether this build is sourced from an LTS-merge respin,
        for filename/on-device-version purposes (the "-lts" marker).

        Auto-detected from the kernel_tag's own format whenever
        possible - a raw commit SHA or a dot-style tag can ONLY come
        from the LTS scheme, so inferring it here means correctness
        doesn't depend on a human also remembering to flip a separate
        --lts flag/matrix.json field in sync with kernel_tag (which is
        exactly what caused two real builds to come out unmarked -
        --lts / matrix.json's "lts" was left at its default while only
        kernel_tag got updated). config.is_lts_build (the explicit
        flag) is still honored as an override for edge cases, but is no
        longer load-bearing for the common case.
        """
        if self.config.is_lts_build:
            return True
        tag = self.config.kernel_tag or ""
        if self._is_commit_sha(tag):
            return True
        if self._LTS_DOT_TAG_RE.match(tag):
            return True
        return False

    def _write_scmversion(self):
        """Writes the exact desired release suffix directly into
        .scmversion. setlocalversion uses this file's content verbatim
        instead of calling `git describe`, giving full, predictable
        control over the final kernel release string (e.g.
        "5.15.194-android13-r10") regardless of build system (legacy
        build.sh vs Bazel/Kleaf) - the separate CONFIG_LOCALVERSION/
        custom_version mechanisms elsewhere in this file are gated
        inconsistently between the two build paths, so this is the one
        reliable, universal way to control it.

        Runs for every build, not just ones with an explicit
        --kernel-tag: self.detected_respin is populated by
        _detect_kernel_respin() either way (pinned tag or a remote
        lookup against the moving branch HEAD), so a build that just
        tracks HEAD still gets a clean "-r10"-style suffix instead of
        git's default raw commit-hash suffix (e.g. "-8-gd37b0095da55")."""
        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            return
        if not self.detected_respin and not self.is_lts_build:
            return
        respin_suffix = f"-{self.config.android_version}"
        if self.detected_respin:
            respin_suffix += f"-{self.detected_respin}"
        if self.is_lts_build:
            respin_suffix += "-lts"
        (common_dir / ".scmversion").write_text(respin_suffix)
        logger.info(f".scmversion written: {respin_suffix}")

    def _detect_kernel_respin(self):
        """Determines which respin (e.g. 'r10') the checked-out
        kernel/common commit actually corresponds to, so it can be
        reflected in the final artifact filenames. This matters because
        the same sub_level/os_patch_level combo can get rebuilt weeks
        later against a newer respin without any other visible
        difference in the filename otherwise - users downloading from
        GitHub currently have no way to tell those two builds apart.

        Works whether the build pinned an explicit --kernel-tag or is
        tracking the moving branch HEAD. For the latter case, local git
        tags are NOT available here (init_and_sync_kernel() runs repo
        sync with --no-tags for speed), so instead we ask the *remote*
        directly which respin tag(s) exist for this exact android/
        kernel/os_patch_level combo, and compare their commit SHAs
        against our checked-out HEAD - a single narrow ls-remote query,
        not a full tag fetch."""
        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            return

        if self.config.kernel_tag:
            if self._is_commit_sha(self.config.kernel_tag):
                # No official respin tag exists yet for a raw-SHA pin by
                # definition, so there's no clean "rNN" number to show.
                # Leave detected_respin unset rather than stuffing the
                # commit hash into every filename and on-device version
                # string - respin_suffix/_write_scmversion already fall
                # back to just "-lts" (still is_lts_build=True) when
                # this is None, which is short and honest without being
                # unreadable. The commit itself is still logged here for
                # anyone who needs to trace exactly what was built.
                logger.info(
                    f"Kernel pinned to commit SHA {self.config.kernel_tag} "
                    f"(no official respin tag yet) - filenames will use "
                    f"just the sub_level/os_patch/-lts, no respin number"
                )
                return
            m = re.search(r'_(r\d+)$', self.config.kernel_tag)
            if m:
                self.detected_respin = m.group(1)
                logger.info(f"Kernel respin (from pinned tag): {self.detected_respin}")
                return

        head_result = subprocess.run(
            "git rev-parse HEAD", shell=True, cwd=common_dir,
            capture_output=True, text=True
        )
        if head_result.returncode != 0 or not head_result.stdout.strip():
            logger.warning("Could not determine kernel respin - artifact filenames will omit it")
            return
        head_sha = head_result.stdout.strip()

        tag_prefix = f"{self.config.android_version}-{self.config.kernel_version}-{self.config.os_patch_level}_r"
        ls_result = subprocess.run(
            f"git ls-remote --tags https://android.googlesource.com/kernel/common '{tag_prefix}*'",
            shell=True, capture_output=True, text=True, timeout=30
        )
        if ls_result.returncode == 0 and ls_result.stdout:
            # Build tag -> commit SHA, preferring the dereferenced
            # (^{}) line when present (the true commit for annotated
            # tags), falling back to the plain line for lightweight tags.
            tag_shas = {}
            for line in ls_result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) != 2:
                    continue
                sha, ref = parts
                prefix = "refs/tags/"
                if not ref.startswith(prefix + tag_prefix):
                    continue
                name = ref[len(prefix):]
                is_deref = name.endswith("^{}")
                if is_deref:
                    name = name[:-3]
                if is_deref or name not in tag_shas:
                    tag_shas[name] = sha

            for name, sha in tag_shas.items():
                if sha == head_sha:
                    m = re.match(rf'^{re.escape(tag_prefix)}(\d+)$', name)
                    if m:
                        self.detected_respin = f"r{m.group(1)}"
                        logger.info(f"Kernel respin (matched via remote tag lookup): {self.detected_respin}")
                        return

        logger.warning("Could not determine kernel respin - artifact filenames will omit it")

    @property
    def respin_suffix(self) -> str:
        suffix = f"-{self.detected_respin}" if self.detected_respin else ""
        if self.is_lts_build:
            suffix += "-lts"
        return suffix


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
        """Pulls in SukiSU-Ultra's kernel-side driver source.

        self.config.kernelsu_commit is a git ref - a tag (e.g. 'v4.1.3'),
        branch, or commit hash all work identically here, since it's
        used directly in a raw.githubusercontent.com URL and a plain
        'git checkout'. Note this pins the driver SOURCE only - it has
        no bearing on the Manager APP's displayed version, which is a
        separate artifact (see the README's "Pinning SukiSU-Ultra"
        section for why 'Manager version (40856-2)' specifically can't
        be pinned this way).

        Fails loudly if the ref doesn't exist, for the same reason
        _checkout_kernel_tag() does: silently falling back to whatever
        setup.sh's default branch happens to be would build a real, but
        DIFFERENT, KernelSU/SukiSU-Ultra version while the build still
        claims to be the one that was asked for.
        """
        logger.info("=== Adding KernelSU ===")
        self._chdir(self.work_dir)
        setup_url = (f"https://raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra/{self.config.kernelsu_commit}/kernel/setup.sh"
                    if self.config.kernelsu_commit else KSU_REPO_CONFIG["setup_script"])
        result = self._run_cmd(f"curl -LSsf {setup_url} | bash -s builtin", check=False)
        if self.config.kernelsu_commit and result.returncode != 0:
            raise RuntimeError(
                f"kernelsu_commit '{self.config.kernelsu_commit}' could not be "
                f"fetched from raw.githubusercontent.com/SukiSU-Ultra/SukiSU-Ultra "
                f"- it likely doesn't exist (typo, or not a real tag/branch/commit). "
                f"Refusing to silently fall back to the default setup script. "
                f"Check available tags at https://github.com/SukiSU-Ultra/SukiSU-Ultra/tags"
            )
        if self.config.kernelsu_commit:
            ksu_dir = self.work_dir / "KernelSU"
            if not ksu_dir.exists():
                raise RuntimeError(
                    f"kernelsu_commit '{self.config.kernelsu_commit}' was set, but "
                    f"the setup script didn't produce a KernelSU/ directory to pin "
                    f"- can't verify the checkout landed on the right ref."
                )
            self._chdir(ksu_dir)
            checkout_result = self._run_cmd(f"git checkout {self.config.kernelsu_commit}", check=False)
            self._chdir(self.work_dir)
            if checkout_result.returncode != 0:
                raise RuntimeError(
                    f"kernelsu_commit '{self.config.kernelsu_commit}' was fetched "
                    f"via setup.sh but 'git checkout {self.config.kernelsu_commit}' "
                    f"failed inside KernelSU/ - the ref may not be reachable from "
                    f"the clone setup.sh made. Refusing to silently continue on "
                    f"whatever ref setup.sh left it on."
                )

    # Upstream Linux changed ptrace_notify()'s signature in 5.16 to pass
    # the event message as an explicit argument instead of stashing it in
    # current->ptrace_message before the tracer is actually notified -
    # closing a race where that field (e.g. a forked child's PID) is
    # briefly visible to other readers before/after the real notify.
    # Kernels below 5.16 (5.10, 5.15) don't have this fix upstream, so we
    # backport it here. This is a no-op skip - not a failure - on kernels
    # that already have it natively.
    def apply_ptrace_leak_fix(self):
        if self.config.kernel_version not in ("5.10", "5.15"):
            logger.info("Skipping ptrace leak fix - already upstream on this kernel version")
            self._mark("ptrace_leak_fix", "skipped", "already upstream on this kernel version")
            return
        logger.info("=== Applying ptrace leak fix (kernels < 5.16) ===")
        patch_file = Path(__file__).parent / "patches" / "gki_ptrace.patch"
        if not patch_file.exists():
            logger.warning(f"ptrace leak fix patch not found at {patch_file} - skipping")
            self._mark("ptrace_leak_fix", "failed", "patch file missing")
            return
        common_dir = self.work_dir / "common"
        self._chdir(common_dir)
        result = self._run_cmd(f"patch -p1 -F 3 < {patch_file}", check=False)
        self._chdir(self.work_dir)
        if result.returncode != 0:
            logger.warning(
                "ptrace leak fix did not apply cleanly - kernel source may "
                "have diverged from what this patch expects, continuing "
                "without it"
            )
            self._mark("ptrace_leak_fix", "failed", "did not apply cleanly")
        else:
            self._mark("ptrace_leak_fix", "applied")

    # NTSync (drivers/misc/ntsync.c) emulates Windows NT synchronization
    # primitives in-kernel - useful for Winlator/Wine. It's mainline as of
    # Linux 6.14, so this backports it via two patches from kernel_patches:
    # a base driver patch (same for every branch) plus a small per-branch
    # compat patch that wires it into that branch's Kconfig/Makefile.
    # Skips (doesn't fail the build) if no compat patch exists yet for
    # this specific android/kernel combo.
    def apply_ntsync_patches(self):
        logger.info("=== Applying NTSync driver patches ===")
        patches_dir = Path(__file__).parent / "patches"
        base_patch = patches_dir / "ntsync_base.patch"
        compat_patch = patches_dir / f"ntsync_compat_{self.config.android_version}-{self.config.kernel_version}.patch"
        if not base_patch.exists() or not compat_patch.exists():
            logger.warning(
                f"NTSync patches not found for "
                f"{self.config.android_version}-{self.config.kernel_version} "
                f"(looked for {compat_patch.name}) - skipping"
            )
            self._mark("ntsync", "skipped", "no compat patch for this branch")
            return
        common_dir = self.work_dir / "common"
        self._chdir(common_dir)
        for patch_file, label in [(base_patch, "driver source"), (compat_patch, "Kconfig/Makefile wiring")]:
            result = self._run_cmd(f"patch -p1 -F 3 < {patch_file}", check=False)
            if result.returncode != 0:
                logger.warning(f"NTSync {label} patch failed to apply cleanly - continuing without NTSync")
                self._mark("ntsync", "failed", f"{label} did not apply cleanly")
                self._chdir(self.work_dir)
                return
        self._chdir(self.work_dir)
        self._mark("ntsync", "applied")

    def add_bbg(self):
        if not self.config.use_bbg:
            self._mark("baseband_guard", "skipped", "not requested")
            return
        logger.info("=== Adding Baseband-guard ===")
        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            self._mark("baseband_guard", "failed", "common/ missing")
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
        self._mark("baseband_guard", "applied")

    def apply_droidspaces_support(self):
        """Enables Droidspaces (github.com/ravindu644/Droidspaces-OSS)
        container-runtime support: real Linux namespace isolation
        (PID/IPC/Mount) so a full Linux distro can run its own init
        system (systemd/OpenRC) as an actual isolated container,
        instead of a plain chroot that just shares the host's process
        tree.

        GKI enforces a strict kABI checksum on struct layouts. Just
        flipping on CONFIG_SYSVIPC/CONFIG_IPC_NS/CONFIG_POSIX_MQUEUE in
        defconfig shifts struct task_struct's layout and causes an
        IMMEDIATE BOOTLOOP - these patches instead move the relevant
        fields into Android's already-reserved kABI padding slots
        (ANDROID_KABI_RESERVE(N) -> ANDROID_KABI_USE(N, ...)) so the
        checksum doesn't move.

        Upstream ships 3 variants of the SYSVIPC patch, each claiming a
        different trio of reserve slots (6/7/8, 3/4/5, or 1/2/3) -
        because which slots are still free (not already claimed by
        something else on a given branch) isn't the same across every
        respin. Rather than hardcoding a guess, this tries all 3 in
        order (dry-run first, strict context matching - fuzzy matching
        here risks a false-positive "clean" apply against the WRONG
        slots, which would silently break kABI in a different way) and
        applies the first one that actually fits this exact source tree.

        Only wired up for kernel_version < 6.12 (android12-5.10,
        android13-5.15, android14-6.1 - the branches this project
        currently builds). 6.12+ needs a structurally different patch
        (extra EXPORT_SYMBOL_GPL calls instead of kABI reserve slots)
        that isn't vendored here yet.
        """
        if not self.config.use_droidspaces:
            self._mark("droidspaces", "skipped", "not requested")
            return
        if self.config.kernel_version not in ("5.10", "5.15", "6.1"):
            logger.warning(
                f"Droidspaces support requested but not implemented yet for "
                f"kernel {self.config.kernel_version} (only 5.10/5.15/6.1 are "
                f"wired up) - skipping"
            )
            self._mark("droidspaces", "skipped", f"kernel {self.config.kernel_version} not supported yet")
            return

        logger.info("=== Applying Droidspaces container-runtime support ===")
        common_dir = self.work_dir / "common"
        if not common_dir.exists():
            self._mark("droidspaces", "failed", "common/ missing")
            return

        patch_dir = Path(__file__).parent / "patches"
        sysvipc_variants = [
            "droidspaces_sysvipc_kabi_slots678.patch",  # upstream's default choice for every below-6.12 branch - tried first
            "droidspaces_sysvipc_kabi_slots345.patch",
            "droidspaces_sysvipc_kabi_slots123.patch",
        ]
        applied_variant = None
        self._chdir(common_dir)
        for variant in sysvipc_variants:
            patch_file = patch_dir / variant
            if not patch_file.exists():
                continue
            # -F 0: no fuzz. A fuzzy "clean" apply here could silently
            # land on the wrong reserve slots rather than genuinely
            # matching this tree's layout - for a kABI patch that's
            # worse than just failing outright.
            dry_run = self._run_cmd(f"patch -p1 -F 0 --dry-run < {patch_file}", check=False)
            if dry_run.returncode == 0:
                self._run_cmd(f"patch -p1 -F 0 < {patch_file}", check=False)
                applied_variant = variant
                break
        self._chdir(self.work_dir)

        if not applied_variant:
            logger.warning(
                "Droidspaces SYSVIPC kABI patch did not cleanly match any "
                "of the 3 known ANDROID_KABI_RESERVE slot layouts on this "
                "branch - task_struct may have diverged further upstream. "
                "Continuing WITHOUT Droidspaces support (defconfig options "
                "are not added either, to avoid a bootloop from enabling "
                "CONFIG_SYSVIPC without the matching kABI fix)."
            )
            self._mark("droidspaces", "failed", "no sysvipc kabi variant applied cleanly")
            return
        logger.info(f"Droidspaces SYSVIPC kABI patch applied ({applied_variant})")

        # 5.10 needs one more patch for POSIX_MQUEUE specifically (a
        # different struct - user_struct, not task_struct - so no slot
        # conflict with the patch above).
        mqueue_detail = ""
        if self.config.kernel_version == "5.10":
            self._chdir(common_dir)
            mqueue_patch = patch_dir / "droidspaces_posix_mqueue_5_10.patch"
            result = self._run_cmd(f"patch -p1 -F 0 < {mqueue_patch}", check=False)
            self._chdir(self.work_dir)
            if result.returncode != 0:
                logger.warning(
                    "Droidspaces POSIX_MQUEUE kABI patch (5.10-specific) "
                    "did not apply cleanly - POSIX_MQUEUE may still break "
                    "kABI on this branch. Continuing anyway since the main "
                    "SYSVIPC patch succeeded; POSIX_MQUEUE just won't be "
                    "safely enabled."
                )
                mqueue_detail = "; posix_mqueue patch failed"

        # Required + recommended defconfig options - see
        # https://github.com/ravindu644/Droidspaces-OSS/blob/main/Documentation/Kernel-Configuration.md
        config_file = common_dir / "arch/arm64/configs/gki_defconfig"
        if not config_file.exists():
            logger.warning(f"gki_defconfig not found at {config_file} - Droidspaces configs not added")
            self._mark("droidspaces", "failed", f"sysvipc variant {applied_variant} applied, but gki_defconfig missing")
            return

        droidspaces_configs = [
            "# Droidspaces container runtime support",
            "CONFIG_SYSVIPC=y",
            "CONFIG_POSIX_MQUEUE=y",
            "CONFIG_IPC_NS=y",
            "CONFIG_PID_NS=y",
            "CONFIG_DEVTMPFS=y",
            "CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y",
            "CONFIG_USER_NS=y",
            "CONFIG_NETFILTER_XT_TARGET_REJECT=y",
            "CONFIG_NETFILTER_XT_TARGET_LOG=y",
            "CONFIG_NETFILTER_XT_MATCH_RECENT=y",
            "CONFIG_IP_SET=y",
            "CONFIG_IP_SET_HASH_IP=y",
            "CONFIG_IP_SET_HASH_NET=y",
            "CONFIG_NETFILTER_XT_SET=y",
            "CONFIG_TMPFS_POSIX_ACL=y",
            "CONFIG_TMPFS_XATTR=y",
            "CONFIG_BINFMT_MISC=y",
            "CONFIG_BINFMT_SCRIPT=y",
            "CONFIG_BINFMT_ELF=y",
        ]
        with open(config_file, "a") as f:
            f.write("\n" + "\n".join(droidspaces_configs) + "\n")
        self._mark("droidspaces", "applied", f"sysvipc variant: {applied_variant}{mqueue_detail}")

    # fs/namespace.c: on SOME specific branches/sub_levels, the SUSFS
    # patch was written against a version of this file that lacks
    # "#include <trace/hooks/blk.h>" (Google added it later), so the
    # patch's context window doesn't match unless we remove it first and
    # restore it after. This must stay gated to the exact
    # android/sub_level combinations where that's true (confirmed against
    # WildKernels/GKI_KernelSU_SUSFS's own build pipeline) - on other
    # branches (e.g. android15-6.6) the file already matches the patch's
    # expected context as-is, and removing the include only breaks a
    # hunk that would otherwise apply cleanly.
    _NAMESPACE_C_BLK_INCLUDE_FIX_RANGES = {
        ("android13", "5.15"): 197,
        ("android14", "6.1"): 157,
    }

    def _namespace_c_blk_include_fix_applies(self) -> bool:
        threshold = self._NAMESPACE_C_BLK_INCLUDE_FIX_RANGES.get(
            (self.config.android_version, self.config.kernel_version)
        )
        if threshold is None:
            return False
        sub_level = self.config.get_sub_level_int()
        return sub_level is not None and sub_level >= threshold

    def _preprocess_namespace_c_susfs_include(self) -> bool:
        if not self._namespace_c_blk_include_fix_applies():
            return False
        namespace_c = self.work_dir / "common/fs/namespace.c"
        if not namespace_c.exists():
            return False
        content = namespace_c.read_text()
        include_line = "#include <trace/hooks/blk.h>\n"
        if include_line not in content:
            return False
        namespace_c.write_text(content.replace(include_line, "", 1))
        logger.info(
            "fs/namespace.c: temporarily removed 'trace/hooks/blk.h' include "
            "so the SUSFS patch context matches (restored after patching)"
        )
        return True

    def _restore_namespace_c_susfs_include(self):
        namespace_c = self.work_dir / "common/fs/namespace.c"
        if not namespace_c.exists():
            return
        content = namespace_c.read_text()
        if "#include <trace/hooks/blk.h>" in content:
            return
        anchor = '#include "internal.h"\n'
        if anchor in content:
            namespace_c.write_text(content.replace(anchor, anchor + "#include <trace/hooks/blk.h>\n", 1))
            logger.info("fs/namespace.c: restored 'trace/hooks/blk.h' include after SUSFS patch")

    # fs/namei.c: the SUSFS patch adds set_nameidata(nd, old_dfd,
    # fake_filename, NULL) - 4 args - unconditionally, but only 5.10
    # kernels (android12-5.10, android13-5.10) still have the 3-param
    # set_nameidata(p, dfd, name) - there's no 4th/root param on that
    # branch. On android13-5.15+ set_nameidata legitimately HAS a 4th
    # param, so the same call text is correct there and must NOT be
    # touched - this must stay gated to exactly the 5.10 branches
    # (confirmed against WildKernels/GKI_KernelSU_SUSFS's own build
    # pipeline, which gates it the same way) rather than a blind
    # string-match across all kernel versions.
    def _fix_namei_c_set_nameidata_arity(self):
        if not (self.config.kernel_version == "5.10"
                and self.config.android_version in ("android12", "android13")):
            return
        namei_c = self.work_dir / "common/fs/namei.c"
        if not namei_c.exists():
            return
        content = namei_c.read_text()
        broken_call = "set_nameidata(nd, old_dfd, fake_filename, NULL)"
        fixed_call = "set_nameidata(nd, old_dfd, fake_filename)"
        if broken_call not in content:
            return
        count = content.count(broken_call)
        namei_c.write_text(content.replace(broken_call, fixed_call))
        logger.info(
            f"fs/namei.c: fixed {count} set_nameidata() call(s) with a stray "
            f"4th argument the function doesn't declare on 5.10 kernels"
        )

    # android16-6.12: two known source-vs-patch drift issues, confirmed
    # against WildKernels/GKI_KernelSU_SUSFS's own build pipeline. Same
    # remove-before/restore-after pattern as the namespace.c blk.h fix
    # above - gated to the exact sub_level thresholds where each is
    # needed, not applied blindly to every android16-6.12 build.
    def _preprocess_android16_fake_patches(self) -> dict:
        applied = {"exec_dma_buf": False, "task_mmu_vma_rename": False}
        if not (self.config.android_version == "android16" and self.config.kernel_version == "6.12"):
            return applied
        sub_level = self.config.get_sub_level_int()
        if sub_level is None:
            return applied

        if sub_level >= 58:
            exec_c = self.work_dir / "common/fs/exec.c"
            if exec_c.exists():
                content = exec_c.read_text()
                include_line = "#include <linux/dma-buf.h>\n"
                if include_line in content:
                    exec_c.write_text(content.replace(include_line, "", 1))
                    applied["exec_dma_buf"] = True
                    logger.info(
                        "fs/exec.c: temporarily removed 'linux/dma-buf.h' include "
                        "(android16-6.12 >=58, restored after patching)"
                    )

        if sub_level >= 69:
            task_mmu_c = self.work_dir / "common/fs/proc/task_mmu.c"
            if task_mmu_c.exists():
                content = task_mmu_c.read_text()
                if "vma_data_pages" in content:
                    task_mmu_c.write_text(content.replace("vma_data_pages", "vma_pages"))
                    applied["task_mmu_vma_rename"] = True
                    logger.info(
                        "fs/proc/task_mmu.c: temporarily renamed vma_data_pages -> "
                        "vma_pages (android16-6.12 >=69, restored after patching)"
                    )

        return applied

    def _restore_android16_fake_patches(self, applied: dict):
        if applied.get("exec_dma_buf"):
            exec_c = self.work_dir / "common/fs/exec.c"
            if exec_c.exists():
                content = exec_c.read_text()
                if "#include <linux/dma-buf.h>" not in content:
                    head, sep, rest = content.partition("#include ")
                    if sep:
                        line_end = rest.find("\n") + 1
                        content = head + sep + rest[:line_end] + "#include <linux/dma-buf.h>\n" + rest[line_end:]
                        exec_c.write_text(content)
                        logger.info("fs/exec.c: restored 'linux/dma-buf.h' include")

        if applied.get("task_mmu_vma_rename"):
            task_mmu_c = self.work_dir / "common/fs/proc/task_mmu.c"
            if task_mmu_c.exists():
                content = task_mmu_c.read_text()
                task_mmu_c.write_text(content.replace("vma_pages", "vma_data_pages"))
                logger.info("fs/proc/task_mmu.c: restored vma_pages -> vma_data_pages")

    # mm/mmap.c: some SUSFS patch hunks call vm_flags_clear() - a VMA
    # helper Google added to kernel/common at different os_patch_levels
    # per branch (same story as VMA_PAD_START/page-size-migration: a
    # later Google addition that older os_patch_levels in our build
    # matrix predate). When missing, this fails with "implicit
    # declaration of function 'vm_flags_clear'". We fall back to a
    # direct vm_flags &= ~flags definition, but only if it's genuinely
    # not declared anywhere upstream (checked broadly across include/,
    # not just one hardcoded header) to avoid a redefinition error on
    # sub_levels where it already exists.
    def _fix_vm_flags_clear_compat(self):
        common_dir = self.work_dir / "common"
        mmap_c = common_dir / "mm/mmap.c"
        if not mmap_c.exists():
            return
        content = mmap_c.read_text()
        if "vm_flags_clear(" not in content or "VM_FLAGS_CLEAR_COMPAT_DEFINED" in content:
            return

        include_dir = common_dir / "include"
        if include_dir.exists():
            for header in include_dir.rglob("*.h"):
                try:
                    if "vm_flags_clear" in header.read_text(errors="ignore"):
                        return  # already declared upstream, nothing to do
                except OSError:
                    continue

        fallback = (
            "\n#ifndef VM_FLAGS_CLEAR_COMPAT_DEFINED\n"
            "#define VM_FLAGS_CLEAR_COMPAT_DEFINED\n"
            "static inline void vm_flags_clear(struct vm_area_struct *vma, unsigned long flags)\n"
            "{\n"
            "\tvma->vm_flags &= ~flags;\n"
            "}\n"
            "#endif\n"
        )
        lines = content.split("\n")
        include_indices = [i for i, l in enumerate(lines) if l.startswith("#include")]
        insert_at = (max(include_indices) + 1) if include_indices else 0
        lines.insert(insert_at, fallback)
        mmap_c.write_text("\n".join(lines))
        logger.info(
            "mm/mmap.c: added vm_flags_clear() compat fallback (not "
            "declared upstream for this sub_level)"
        )

    def apply_susfs_patches(self):
        logger.info("=== Applying SUSFS patches ===")
        self._chdir(self.work_dir)
        common_dir = self.work_dir / "common"
        susfs_patch = self.susfs_dir / "kernel_patches" / self.config.get_susfs_patch_filename()
        if not susfs_patch.exists():
            raise RuntimeError(
                f"SUSFS patch file not found: {susfs_patch}\n"
                f"The susfs4ksu checkout (at {self.susfs_dir}) may be on the "
                f"wrong branch (expected '{self.config.kernel_branch}'), or "
                f"susfs4ksu has renamed/moved this file upstream. Check: "
                f"https://github.com/ShirkNeko/susfs4ksu/tree/{self.config.kernel_branch}/kernel_patches\n"
                f"This is a hard stop - continuing without this patch produces "
                f"a kernel that fails to link (undefined susfs_* symbols)."
            )
        self._run_cmd(f"cp {susfs_patch} {common_dir}/", check=False)
        for src, dst in [
            (self.susfs_dir / "kernel_patches/fs", common_dir / "fs/"),
            (self.susfs_dir / "kernel_patches/include/linux", common_dir / "include/linux/"),
        ]:
            if src.exists():
                self._run_cmd(f"cp -r {src}/* {dst}", check=False)

        removed_blk_include = self._preprocess_namespace_c_susfs_include()
        android16_applied = self._preprocess_android16_fake_patches()

        patch_file = common_dir / self.config.get_susfs_patch_filename()
        self._chdir(common_dir)
        result = self._run_cmd(f"patch -p1 --fuzz=3 < {patch_file}", check=False)
        self._chdir(self.work_dir)

        if removed_blk_include:
            self._restore_namespace_c_susfs_include()
        self._restore_android16_fake_patches(android16_applied)

        self._fix_namei_c_set_nameidata_arity()
        self._fix_vm_flags_clear_compat()

        if result.returncode != 0:
            self._mark("susfs", "failed", f"patch exit code {result.returncode}")
            raise RuntimeError(
                f"SUSFS patch failed to apply cleanly: {patch_file} "
                f"(patch exit code {result.returncode}). The kernel source "
                f"may have diverged from what this SUSFS patch expects - "
                f"check the build log above for rejected hunks."
            )
        self._mark("susfs", "applied")

    def apply_sukisu_patches(self):
        logger.info("=== Applying SukiSU patches ===")
        self._chdir(self.work_dir / "common")
        hooks_patch = self.sukisu_patch_dir / "69_hide_stuff.patch"
        if not hooks_patch.exists():
            self._mark("sukisu_hide_stuff", "skipped", "69_hide_stuff.patch not found")
            return
        result = self._run_cmd(f"cp {hooks_patch} . && patch -p1 -F 3 < 69_hide_stuff.patch", check=False)
        self._mark("sukisu_hide_stuff", "applied" if result.returncode == 0 else "failed")

    def apply_zram_patches(self):
        if not self.config.use_zram:
            self._mark("zram_lz4kd", "skipped", "not requested")
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
        zram_ok = True
        zram_found_any = False
        for patch in ["lz4kd.patch", "lz4k_oplus.patch"]:
            p = zram_patch_dir / patch
            if p.exists():
                zram_found_any = True
                result = self._run_cmd(f"patch -p1 -F 3 < {p}", check=False)
                if result.returncode != 0:
                    zram_ok = False
        if not zram_found_any:
            self._mark("zram_lz4kd", "failed", "no zram patch files found for this kernel version")
        else:
            self._mark("zram_lz4kd", "applied" if zram_ok else "failed")

    def apply_task_mmu_fixes(self):
        logger.info("=== Applying task_mmu.c fixes ===")
        self._chdir(self.work_dir / "common")
        task_mmu = Path("fs/proc/task_mmu.c")
        if not task_mmu.exists():
            self._mark("task_mmu_fixes", "skipped", "fs/proc/task_mmu.c not found")
            return

        changed = False
        fb = f"{self.config.android_version}-{self.config.kernel_version}"
        with open(task_mmu, "r") as f:
            content = f.read()

        if fb == "android15-6.6" and "unsigned int nr_subpages" not in content:
            self._fix_base_c_header()
            changed = True
        elif fb == "android14-6.1" and "if (!vma_pages(vma))" not in content:
            self._fix_base_c_header()
            changed = True
            if "goto show_pad;" in content:
                content = content.replace("goto show_pad;", "return 0;")
                with open(task_mmu, "w") as f:
                    f.write(content)
        elif fb in ["android12-5.10", "android13-5.10", "android13-5.15"] and "if (!vma_pages(vma))" not in content:
            if "goto show_pad;" in content:
                content = content.replace("goto show_pad;", "return 0;")
                with open(task_mmu, "w") as f:
                    f.write(content)
                changed = True

        with open(task_mmu, "r") as f:
            content = f.read()
        if "struct dentry *dentry;\n" in content:
            content = content.replace("struct dentry *dentry;\n", "struct dentry *dentry = NULL;\n")
            with open(task_mmu, "w") as f:
                f.write(content)
            changed = True

        self._mark("task_mmu_fixes", "applied" if changed else "skipped",
                    "" if changed else "not needed on this branch")

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

        if self.config.enable_ksm:
            with open(config_file, "a") as f:
                f.write("# === KSM (Kernel Samepage Merging) Config ===\n")
                f.write("CONFIG_KSM=y\n")

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
            "BBR": "BBR",  # substring match: real symbols are CONFIG_TCP_CONG_BBR
                            # and CONFIG_DEFAULT_BBR - neither actually starts
                            # with "CONFIG_BBR", so this can't use a prefix match
            "CONFIG_ZRAM": "ZRAM",
            "BFQ": "BFQ I/O Scheduler",
            "CONFIG_KSM": "KSM",
            "F2FS_FS_": "F2FS Compression",
        }
        
        logger.info("Key config status:")
        for prefix, name in key_configs.items():
            found = [c for c in config_lines if prefix in c]
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

    @property
    def artifact_suffix(self) -> str:
        """LTO is always thin - no fallback mode exists anymore, so this
        is kept only so callers referencing it (prepare_boot_images,
        create_anykernel_zips) don't need to change."""
        return ""

    def _run_build_command(self, cmd: str) -> tuple:
        """Runs a (potentially very long) build command. Streams output
        live exactly as before, while also capturing it so build_kernel()
        can check it for known failure signatures afterwards."""
        lines = []

        def _capture(line: str):
            lines.append(line)
            # flush=True: without it, stdout is fully block-buffered once
            # piped through tee (build-kernel.sh does `2>&1 | tee log`),
            # while the logger's stderr writes are not - so these lines
            # could show up in the log file out of chronological order
            # relative to logger.info/warning/error calls (e.g. a WARNING
            # about a retry appearing before the failure output that
            # triggered it). Flushing immediately keeps the log readable
            # top-to-bottom.
            print(line, flush=True)

        try:
            self.shell.run_with_callback(cmd, callback=_capture)
            return True, "\n".join(lines)
        except RuntimeError:
            return False, "\n".join(lines)

    def _write_build_report(self, success: bool, build_seconds: float, is_legacy: bool):
        """Writes a short, human-readable summary of how this kernel was
        actually built - so this doesn't have to be dug out of the full
        build log."""
        from datetime import datetime
        report_path = self.work_dir / "BUILD_REPORT.txt"
        lines = [
            "GKI Kernel Build Report",
            "=" * 40,
            f"Config:        {self.config.config_name}",
            f"Kernel respin: {self.detected_respin or '(unknown - could not be determined)'}",
            f"Timestamp:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Build method:  {'Legacy build.sh' if is_legacy else 'Bazel (Kleaf)'}",
            "LTO mode used: thin",
            f"Result:        {'SUCCESS' if success else 'FAILED'}",
            f"Build time:    {build_seconds:.1f}s",
        ]
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"Build report written to: {report_path}")

    def remove_protected_exports(self):
        """Removes ABI protected-exports enforcement for Bazel builds.
        Google's official abi_gki_protected_exports_* lists (and the
        protected_exports_list/protected_modules wiring in BUILD.bazel/
        modules.bzl) exist so *Google's own* GKI doesn't accidentally
        break symbols vendor modules already depend on. A custom
        KernelSU/SUSFS kernel isn't trying to stay protected-export
        compatible with stock GKI, so this enforcement only gets in the
        way. Confirmed against WildKernels/GKI_KernelSU_SUSFS's own build
        pipeline, which does the exact same removal. This is separate
        from (and complementary to) --nokmi_symbol_list_strict_mode in
        build_kernel() - that covers the KMI symbol list check, this
        covers protected-exports enforcement. No-op for the legacy
        build.sh path (that build system doesn't have this concept)."""
        if (self.work_dir / "build/build.sh").exists():
            return
        common_dir = self.work_dir / "common"
        logger.info("=== Removing protected exports (Bazel build) ===")
        self._run_cmd(f"rm -rf {common_dir}/android/abi_gki_protected_exports_*", check=False)

        build_bazel = common_dir / "BUILD.bazel"
        if build_bazel.exists():
            content = build_bazel.read_text()
            new_content = re.sub(
                r'^\s*"protected_exports_list"\s*:\s*"android/abi_gki_protected_exports_aarch64",\s*\n',
                '', content, flags=re.MULTILINE
            )
            new_content = re.sub(
                r'^\s*protected_module_names_list\s*=\s*":gki_(?:aarch64|x86_64)_protected_module_names",\s*\n',
                '', new_content, flags=re.MULTILINE
            )
            if new_content != content:
                build_bazel.write_text(new_content)
                logger.info("common/BUILD.bazel: removed protected_exports_list / protected_module_names_list references")

        modules_bzl = common_dir / "modules.bzl"
        if modules_bzl.exists():
            content = modules_bzl.read_text()
            new_content = re.sub(r'protected_modules\s*=\s*\[.*?\]', 'protected_modules = []', content, flags=re.DOTALL)
            if new_content != content:
                modules_bzl.write_text(new_content)
                logger.info("common/modules.bzl: cleared protected_modules")

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

        import time
        start_time = time.time()
        is_legacy = (self.work_dir / "build/build.sh").exists()
        bazel_cache = Path.home() / ".cache" / "bazel"

        # Known LLVM/ThinLTO verifier bug on Bazel/Kleaf branches (6.6 and
        # up so far): cross-module inlining under ThinLTO can produce an
        # inlined call in a function with debug info that's missing a
        # !dbg location, which trips the IR verifier ("Broken module
        # found, compilation aborted"). This isn't a LTO=thin-vs-none
        # question - a known-working reference build on this same branch
        # doesn't pass --lto at all and lets the Bazel target's own
        # default win, and that avoids the bug while still getting real
        # LTO (not a no-LTO fallback). So: force thin explicitly where
        # it's proven to work, and just omit --lto on branches affected
        # by this bug so Bazel's target default applies instead.
        _THIN_LTO_BUG_KERNEL_VERSIONS = ("6.6", "6.12", "6.18")

        def _build_cmd() -> str:
            if is_legacy:
                return "LTO=thin BUILD_CONFIG=common/build.config.gki.aarch64 build/build.sh CC=\"/usr/bin/ccache clang\""
            # Building //common:kernel_aarch64/Image directly (instead of
            # the full //common:kernel_aarch64_dist target) matches
            # WildKernels/GKI_KernelSU_SUSFS's own build-kernel action.
            # The _dist target also runs GKI certification/ABI-validation
            # actions we don't need for a custom KernelSU/SUSFS kernel;
            # building the bare Image skips that dependency chain
            # entirely - which is likely also why WildKernels doesn't need
            # any KMI-strict-mode workaround. The Image lands in the same
            # bazel-bin/common/kernel_aarch64/ path our artifact-gathering
            # code already expects, so no other changes are needed here.
            # --nokmi_symbol_list_strict_mode kept as a defensive no-op in
            # case this target still runs that check on some branch.
            lto_flag = "" if self.config.kernel_version in _THIN_LTO_BUG_KERNEL_VERSIONS else "--lto=thin "
            return (f"tools/bazel build --disk_cache={bazel_cache} --config=fast "
                    f"{lto_flag}--nokmi_symbol_list_strict_mode "
                    f"--nokmi_symbol_list_violations_check //common:kernel_aarch64/Image")

        try:
            if is_legacy:
                logger.info("Using legacy build method...")
            else:
                logger.info("Using Bazel build method...")
                self._canonicalize_defconfig()
                self.remove_protected_exports()
                bazel_cache.mkdir(parents=True, exist_ok=True)

            # No fallback retry on failure - if the build fails, it fails,
            # full stop. The LTO mode itself is chosen upfront per branch
            # above (thin where proven, Bazel default where thin is known
            # to hit the verifier bug), not switched after a failed attempt.
            success, output = self._run_build_command(_build_cmd())

            build_seconds = time.time() - start_time
            self._write_build_report(success, build_seconds, is_legacy)

            if success:
                logger.info("=== Kernel compilation succeeded ===")
                return True
            logger.error("Kernel compilation failed")
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

        for image_name in ["Image"]:
            src = image_source / image_name
            if src.exists():
                self._run_cmd(f"cp {src} {bootimgs_dir}/ && cp {src} {self.work_dir}/", check=False)

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

        # Only the plain boot.img is packaged/uploaded - boot-gz.img and
        # boot-lz4.img variants are intentionally not produced.
        for kernel_file, output_file in [("Image", "boot.img")]:
            kernel_path = bootimgs_dir / kernel_file
            if not kernel_path.exists():
                continue
            cmd = f"$MKBOOTIMG --header_version 4 --kernel {kernel_file} --output {output_file}"
            if has_ramdisk:
                cmd += f" --ramdisk out/ramdisk --os_version 12.0.0 --os_patch_level {self.config.os_patch_level}"
            self._run_cmd(cmd, check=False)
            self._run_cmd(f"$AVBTOOL add_hash_footer --partition_name boot --partition_size $((64 * 1024 * 1024)) --image {output_file} --algorithm SHA256_RSA2048 --key $BOOT_SIGN_KEY_PATH", check=False)
            dest = self.work_dir / f"{self.config.android_version}-{self.config.kernel_version}.{self.config.sub_level}-{self.config.os_patch_level}{self.artifact_suffix}{self.respin_suffix}-{output_file}"
            self._run_cmd(f"cp {output_file} {dest}", check=False)
            artifacts.append(str(dest))

    def create_anykernel_zips(self) -> list:
        logger.info("=== Creating AnyKernel3 ZIP files ===")
        self._chdir(self.work_dir)
        artifacts = []
        ak3_dir = self.anykernel_dir

        # Only the plain AnyKernel3.zip is packaged/uploaded - the
        # -lz4/-gz zip variants are intentionally not produced.
        for suffix in [""]:
            image_file = f"Image{suffix}"
            image_path = self.work_dir / image_file
            if not image_path.exists():
                continue
            zip_name = f"{self.config.android_version}-{self.config.kernel_version}.{self.config.sub_level}-{self.config.os_patch_level}{self.artifact_suffix}-AnyKernel3{self.respin_suffix}{suffix}.zip"
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
            self._mark("safemode_disable", "failed", "ksud.c not found")
            return

        target = target_files[0]
        patch_src = Path(__file__).parent / "patches" / "disable-safemode-full.patch"
        if not patch_src.exists():
            logger.warning(f"Safe-mode patch file not found at {patch_src} - skipping")
            self._mark("safemode_disable", "failed", "patch file missing")
            return

        result = self._run_cmd(f"patch {target} < {patch_src}", check=False)
        if result.returncode == 0:
            logger.info(f"Safe mode disabled successfully: {target}")
            self._mark("safemode_disable", "applied")
        else:
            logger.warning(f"Safe-mode patch did not apply cleanly to {target} - "
                          "ksud.c may have changed upstream, continuing without it")
            self._mark("safemode_disable", "failed", "did not apply cleanly")

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
            self._detect_kernel_respin()
            self._write_scmversion()
            self.apply_ptrace_leak_fix()
            self.apply_ntsync_patches()
            self.add_kernel_supatch()
            self.add_kernelsu()
            if self.config.disable_safemode:
                self.apply_safemode_patch()
            else:
                self._mark("safemode_disable", "skipped", "not requested")
            self.add_bbg()
            self.apply_susfs_patches()
            self.apply_sukisu_patches()
            self.apply_zram_patches()
            self.apply_task_mmu_fixes()
            # Applied last, deliberately after SUSFS/SukiSU: Droidspaces'
            # kABI patch and SUSFS both potentially touch the same
            # ANDROID_KABI_RESERVE slots in include/linux/sched.h. If
            # Droidspaces ran first and claimed a slot SUSFS's patch
            # expects to find untouched, SUSFS - the core, non-optional
            # functionality of this whole project - could fail to apply.
            # Running Droidspaces last means if there IS a conflict, it's
            # this new optional feature that fails/gets skipped, never
            # SUSFS.
            self.apply_droidspaces_support()
            self._write_patch_status()
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
            # Best-effort: persist whatever patch statuses were recorded
            # before the failure (e.g. a hard-stop SUSFS patch failure),
            # so the summary table still shows what did/didn't apply.
            try:
                self._write_patch_status()
            except Exception:
                pass
            return BuildResult(success=False, config=self.config, message=str(e), build_time=time.time() - start_time)
