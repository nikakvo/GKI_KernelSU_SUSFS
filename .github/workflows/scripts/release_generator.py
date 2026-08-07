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
- SUSFS {KERNEL_VERSION}
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
- Safe Mode Permanently Disabled"""

    def save_body(self, output_path: str = "RELEASE_BODY.md"):
        body = self.generate_body()
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            f.write(body)
        print(body)


if __name__ == '__main__':
    ReleaseGenerator().save_body(sys.argv[1] if len(sys.argv) > 1 else "RELEASE_BODY.md")
