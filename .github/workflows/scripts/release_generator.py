#!/usr/bin/env python3
import json
import urllib.request
import ssl
import sys
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from config import KERNEL_VERSION


class ReleaseGenerator:
    def __init__(self):
        self.matrix_path = Path(__file__).parent.parent / "config" / "matrix.json"
        self.ssl_ctx = ssl.create_default_context()
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    def load_matrix(self) -> dict:
        with open(self.matrix_path, 'r') as f:
            return json.load(f)

    def _fetch_json(self, url: str) -> dict:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Python'})
            with urllib.request.urlopen(req, context=self.ssl_ctx) as response:
                return json.loads(response.read())
        except Exception:
            return {}

    def get_ksu_info(self) -> tuple:
        ksu_tag, ksu_commit = "latest", "unknown"
        tags = self._fetch_json("https://api.github.com/repos/SukiSU-Ultra/SukiSU-Ultra/git/refs/tags")
        if tags:
            ksu_tag = tags[-1]['ref'].split('/')[-1]
        ref = self._fetch_json("https://api.github.com/repos/SukiSU-Ultra/SukiSU-Ultra/git/ref/heads/main")
        if ref:
            ksu_commit = ref['object']['sha'][:7]
        return ksu_tag, ksu_commit

    def generate_body(self) -> str:
        return f"""## Features
- SUSFS v2.2.0
- Manual Syscall Hooks
- Magic Mount Support
- BBR v1 Support
- ZRAM Support
- LZ4KD Compression Support
- MGLRU Support (Multi-Gen LRU, enabled by default)
- PSI Support (Pressure Stall Information)
- IP Set Support (netfilter IP/network grouping)
- CAKE Queue Discipline Support
- Wireguard Support
- NTSync Support (Winlator/Wine NT synchronization primitives)
- Ptrace Leak Fix (kernels < 5.16)
- Safe Mode Permanently Disabled
- Thin LTO

## Detailed explanation

- **SUSFS v2.2.0** — Addon for hiding root using kernel-level patches combined with a userspace module (hides suspicious paths, mount points, spoofs kernel stats/uname/cmdline, and more).

- **Manual Syscall Hooks** — Low-level syscall interception method used for root management and detection evasion, offering finer control than standard hooking approaches.

- **Magic Mount Support** — Overlay-based mounting system that lets root modules modify the filesystem without altering the underlying partitions directly, improving compatibility and reducing detection surface.

- **BBR v1 Support** — TCP congestion control algorithm developed by Google, providing better throughput and lower latency than traditional algorithms (e.g. CUBIC) on modern networks, especially with packet loss or variable bandwidth.
  ```
  su -c cat /proc/sys/net/ipv4/tcp_congestion_control
  ```
  Active if output is `bbr`.

- **LZ4KD Support** — Enhanced LZ4 compression algorithm for ZRAM, offering better compression ratios with minimal CPU overhead — improves effective RAM capacity by compressing swapped-out memory pages.
  ```
  su -c cat /sys/block/zram0/comp_algorithm
  ```
  Active if `[lz4kd]` appears in brackets.

- **MGLRU Support (Multi-Gen LRU, enabled by default)** — Modern memory reclaim algorithm that replaces the traditional active/inactive LRU lists with multiple generations based on page access recency. Results in more accurate reclaim decisions, fewer background apps being killed under memory pressure, and smoother multitasking.
  ```
  su -c cat /sys/kernel/mm/lru_gen/enabled
  ```
  Active if the value is non-zero (e.g. `0x0003`), not `0x0000`.

- **PSI Support (Pressure Stall Information)** — Kernel subsystem that reports real-time memory, CPU, and I/O pressure metrics (`/proc/pressure/*`). Allows the Low Memory Killer Daemon (LMKD) to make smarter kill decisions based on actual system pressure instead of coarse thresholds. Works in tandem with MGLRU.
  ```
  su -c cat /proc/pressure/memory
  ```
  Active if it prints `avg10=... avg60=... avg300=... total=...` instead of an error.

- **IP Set Support (netfilter IP/network grouping)** — Kernel-level support for `ipset`, allowing IP addresses, networks, and ports to be grouped into named sets for fast, efficient `iptables`/`ip6tables` matching. Enables O(1) hash-based lookups instead of linear rule scanning, and dynamic set updates without reloading the full firewall ruleset. *(Requires a separate userspace `ipset` binary — see [ipset-arm64](https://github.com/nikakvo/ipset-arm64), not bundled with this kernel.)*
  ```
  su -c "ipset create test hash:ip && ipset destroy test"
  ```
  Active if it runs with no "Kernel module not found" error.

- **CAKE Queue Discipline Support** — Modern queue management algorithm (`sch_cake`) that reduces bufferbloat and improves latency under load by combining fair queuing, active queue management, and traffic shaping in a single, easy-to-configure qdisc.
  ```
  su -c "tc qdisc add dev lo root cake && tc qdisc show dev lo && tc qdisc del dev lo root"
  ```
  Active if `qdisc show` lists `qdisc cake ...`.

- **Wireguard Support** — Built-in kernel-level support for the WireGuard VPN protocol, offering a lightweight, high-performance, and modern alternative to OpenVPN/IPsec.
  ```
  su -c "zcat /proc/config.gz | grep CONFIG_WIREGUARD"
  ```
  Active if it shows `CONFIG_WIREGUARD=y`.

- **NTSync Support (Winlator/Wine NT synchronization primitives)** — Kernel-level driver (`/dev/ntsync`) emulating Windows NT synchronization primitives (semaphores, mutexes, events) natively, instead of userspace emulation over futex. Improves compatibility and reduces overhead for Wine-based Windows app/game layers such as Winlator. Only available on branches with a compatible backport for that specific kernel version — not every branch/sub_level is guaranteed to have it.
  ```
  su -c ls -la /dev/ntsync
  su -c "zcat /proc/config.gz | grep CONFIG_NTSYNC"
  ```
  Active if `/dev/ntsync` exists (as a character device) and `CONFIG_NTSYNC=y` is shown.

- **Ptrace Leak Fix (kernels < 5.16)** — Backports an upstream Linux 5.16 hardening fix that closes a race where `ptrace_message` (e.g. a forked child's PID during a ptrace event) was briefly visible to other readers before the tracer was actually notified, or left stale after detach. Relevant on kernel 5.10/5.15 branches, where this isn't present natively; on 6.1+ branches it's already upstream, so nothing is patched there. There's no `/proc` or `/sys` flag to check this directly — it's a kernel-internal timing/security fix, not a toggle.

- **Safe Mode Permanently Disabled** — KernelSU/SukiSU's volume-key safe-mode detection (holding Vol Up/Down during boot to temporarily disable root) is permanently patched out at the kernel level. Most users already rely on [Yet Another Bootloop Protector](https://github.com/Magisk-Modules-Alt-Repo/YetAnotherBootloopProtector/releases) for this purpose, and the volume-key combo can trigger by accident during normal use. There's no `/proc` or `/sys` flag to check this directly — verify behaviorally: holding the volume keys during boot should no longer trigger safe mode. If you need emergency root disable, use YABP instead.

- **Thin LTO** — LLVM Thin Link-Time Optimization (LTO) performs optimization across translation units while keeping the build process more parallel and memory-efficient than full LTO. It can improve kernel performance and code generation with lower build-time and memory overhead than full LTO.
  ```
  su -c "zcat /proc/config.gz | grep CONFIG_LTO_CLANG_THIN"
  ```
  Active if it shows `CONFIG_LTO_CLANG_THIN=y`
"""

    def save_body(self, output_path: str = "RELEASE_BODY.md"):
        body = self.generate_body()
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(body)
        print(body)


if __name__ == '__main__':
    ReleaseGenerator().save_body(sys.argv[1] if len(sys.argv) > 1 else "RELEASE_BODY.md")
