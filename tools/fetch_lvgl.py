"""Fetch verified LVGL 9.2.2 for the optional desktop build without ESP-IDF."""
import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
URL="https://codeload.github.com/lvgl/lvgl/tar.gz/refs/tags/v9.2.2"
SHA256="129b4e00e06639fa79d7e8a6cab3c1ecce2445b1a246652ccd34f22e7b17ad6f"


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--archive",type=Path)
    args=parser.parse_args();target=ROOT/"managed_components/lvgl__lvgl"
    if target.exists():
        header=target/"lv_version.h"
        if not header.exists():raise SystemExit("Existing LVGL directory is incomplete; inspect it manually")
        import re
        version=tuple(int(re.search(rf"#define\s+LVGL_VERSION_{key}\s+(\d+)",header.read_text())[1]) for key in ("MAJOR","MINOR","PATCH"))
        if version!=(9,2,2):raise SystemExit("Existing LVGL is not 9.2.2; refusing to replace it")
        print("LVGL 9.2.2 already present; no files changed.");return
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lvgl-",dir=target.parent) as temporary:
        temporary=Path(temporary);archive=args.archive
        if archive is None:
            archive=temporary/"lvgl.tar.gz";started=time.monotonic();done=0;last=0
            with urllib.request.urlopen(URL,timeout=60) as response,archive.open("wb") as output:
                total=int(response.headers.get("Content-Length",0))
                while chunk:=response.read(1024*1024):
                    output.write(chunk);done+=len(chunk);now=time.monotonic()
                    if now-last>=2:
                        elapsed=now-started
                        eta=f"{elapsed/done*(total-done):.0f}s" if total else "unknown"
                        fraction=f"{done/total:.0%}" if total else "total unknown"
                        print(f"LVGL download | {done/2**20:.1f} MiB | {fraction} | elapsed {elapsed:.0f}s | ETA {eta}",flush=True);last=now
        with archive.open("rb") as source:
            if hashlib.file_digest(source,"sha256").hexdigest()!=SHA256:raise SystemExit("LVGL archive SHA256 mismatch")
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                path=PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():raise SystemExit("Unsafe archive member")
            bundle.extractall(temporary,filter="data")
        (temporary/"lvgl-9.2.2").rename(target)
    print("Installed LVGL 9.2.2; archive SHA256 verified.")


if __name__=="__main__":main()
