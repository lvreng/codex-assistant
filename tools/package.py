"""Build minimal runtime, complete lossless SD and tracked-source release ZIPs."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
SECRET=re.compile(rb"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{24,})|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
BLOCK=1024*1024


def digest(path):
    with path.open("rb") as source:return hashlib.file_digest(source,"sha256").hexdigest()


class Progress:
    def __init__(self,stage,total):
        self.stage=stage;self.total=total;self.done=0;self.start=time.monotonic();self.last=0
    def step(self,n):
        self.done+=n;now=time.monotonic()
        if now-self.last<2 and self.done!=self.total:return
        elapsed=now-self.start;ratio=self.done/max(1,self.total);bars=round(ratio*20)
        eta=f"{elapsed/self.done*(self.total-self.done):.0f}s" if elapsed>.5 and self.done else "estimating"
        print(f"{self.stage} [{'#'*bars}{'.'*(20-bars)}] {ratio:.1%} | {self.done/2**20:.1f}/{self.total/2**20:.1f} MiB | elapsed {elapsed:.1f}s | ETA {eta}",flush=True);self.last=now


def encoded(data):return (json.dumps(data,ensure_ascii=False,indent=2)+"\n").encode()


def archive(path,entries,prefix):
    if path.exists():raise FileExistsError(path)
    partial=path.with_suffix(".zip.partial");hashes={}
    size=lambda value:len(value) if isinstance(value,bytes) else value.stat().st_size
    progress=Progress(path.stem,sum(map(size,entries.values())))
    try:
        with zipfile.ZipFile(partial,"x",compression=zipfile.ZIP_DEFLATED,compresslevel=3) as bundle:
            for name,value in sorted(entries.items()):
                if Path(name).is_absolute() or ".." in Path(name).parts or "\n" in name:raise ValueError("Unsafe filename")
                content=value if isinstance(value,bytes) else value.read_bytes()
                if SECRET.search(content):raise ValueError(f"Potential credential: {name}")
                if name.endswith((".py",".json",".yml",".md",".txt")) and (str(Path.home())+"/").encode() in content:raise ValueError(f"Personal path: {name}")
                sha=hashlib.sha256();info=zipfile.ZipInfo(prefix+"/"+name,datetime.now().timetuple()[:6]);info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=((0o100644 if isinstance(value,bytes) else value.stat().st_mode)<<16)
                with bundle.open(info,"w",force_zip64=True) as output:
                    for offset in range(0,len(content),BLOCK):
                        block=content[offset:offset+BLOCK];output.write(block);sha.update(block);progress.step(len(block))
                hashes[name]=sha.hexdigest()
            bundle.writestr(prefix+"/MANIFEST.sha256","".join(f"{h}  {n}\n" for n,h in sorted(hashes.items())))
        with zipfile.ZipFile(partial) as bundle:
            progress=Progress("Verify "+path.stem,sum(i.file_size for i in bundle.infolist()))
            for item in bundle.infolist():
                sha=hashlib.sha256()
                with bundle.open(item) as source:
                    while block:=source.read(BLOCK):sha.update(block);progress.step(len(block))
                name=item.filename.removeprefix(prefix+"/")
                if name in hashes and sha.hexdigest()!=hashes[name]:raise ValueError("Archive readback mismatch")
        partial.rename(path)
    except BaseException:
        partial.unlink(missing_ok=True);raise
    print(f"VERIFIED | {path.name} | {len(hashes)} files | {path.stat().st_size/2**20:.2f} MiB",flush=True)
    return dict(file=path.name,bytes=path.stat().st_size,sha256=digest(path),files=len(hashes))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--assets-root",type=Path,required=True);p.add_argument("--output",type=Path,default=ROOT/"dist");p.add_argument("--reuse-sd",action="store_true",help="Reuse an existing SD ZIP only after verifying its previous report and all original inputs");a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=True)
    tracked=subprocess.check_output(["git","ls-files","-z"],cwd=ROOT).decode().split("\0")
    entries={name:ROOT/name for name in tracked if name}
    if not entries:raise SystemExit("Stage the reviewed source files first")
    report=[]
    runtime={name:path for name,path in entries.items() if name.startswith("host/") and not Path(name).name.startswith("test_") and Path(name).name!="art_stream.py"}
    for name,path in entries.items():
        if name in {"LICENSE","THIRD_PARTY_NOTICES.md","SECURITY.md","docs/SETUP.md"} or name.startswith("docs/licenses/"):runtime[name]=path
        if name.startswith("components/") and Path(name).name.lower() in {"license","license.txt","licence.txt"}:runtime[name]=path
    runtime["README.md"]=("# Codex 助手：最小运行包\n\n包含已编译 ESP32-P4 固件与 Linux 上位机。\n\n"
        "- 安装与烧录：[docs/SETUP.md](docs/SETUP.md)\n- 项目主页：https://github.com/lvreng/codex-assistant\n"
        "- 思想者 SD 素材请另外下载 codex-assistant-sd.zip，保持原画质。\n"
        "- 此包不含 Qt 桌面窗口、开发 SDK、源码测试或账户凭据。\n"
        "- 同款桌面窗口及源码构建请下载 source 包。\n\n"
        "先核对开发板与 Flash 分区并备份；不要擦除 NVS 或格式化已有 SD。\n").encode()
    build=ROOT/"build"
    for name in ("codex_pet_p4.bin","bootloader/bootloader.bin","partition_table/partition-table.bin","flasher_args.json"):
        runtime["firmware/"+name]=build/name
    description=json.loads((build/"project_description.json").read_text())
    idf_commit=subprocess.check_output(["git","-C",description["idf_path"],"rev-parse","HEAD"],text=True).strip()
    runtime["firmware/BUILD_INFO.json"]=encoded(dict(target="esp32p4",board="JC4880P443C_I_W",minimum_revision=description["min_rev"],maximum_revision=description["max_rev"],idf=description["git_revision"],idf_commit=idf_commit,application_sha256=digest(build/"codex_pet_p4.bin"),built_from="public source export; independently built before publication",credentials_included=False,source_commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()))
    report.append(archive(a.output/"codex-assistant-runtime.zip",runtime,"codex-assistant"))
    media={}
    for folder,manifest in (("output/sd-media-v1","manifest.json"),("output/sd-camera-v4","camera.json"),("output/astra-layers","layers.json")):
        base=a.assets_root/folder;data=json.loads((base/manifest).read_text())
        for item in data["clips"]:
            name=item["file"]
            if not re.fullmatch(r"[CAT]\d{2,3}\.CJP",name):raise ValueError("Unexpected SD filename")
            source=base/name
            if source.stat().st_size!=item["bytes"] or digest(source)!=item["sha256"]:raise ValueError(f"SD checksum mismatch: {name}")
            if name in media:raise ValueError("Duplicate SD clip")
            media[name]=source
        media[manifest]=base/manifest
    if len([n for n in media if n.endswith(".CJP")])!=39:raise ValueError("Expected 39 original CJP files")
    sys.path.insert(0,str(ROOT/"host"))
    from media_format import inspect_clip
    clips=[(name,path) for name,path in media.items() if name.endswith(".CJP")]
    started=time.monotonic();last=0;landings=0
    for index,(name,path) in enumerate(clips,1):
        with path.open("rb") as source:
            info=inspect_clip(source,verify_frames=True)
            if name.startswith("T"):
                target=media[f"C{info['target_clip']:02d}.CJP"]
                with target.open("rb") as loop:
                    expected=inspect_clip(loop);offset,length,_=expected["entries"][0];loop.seek(offset);first=loop.read(length)
                offset,length,_=info["entries"][-1];source.seek(offset)
                if source.read(length)!=first:raise ValueError(f"Camera/loop endpoint mismatch: {name}")
                landings+=1
        now=time.monotonic()
        if now-last>=2 or index==len(clips):
            elapsed=now-started
            print(f"SD CRC + landing | {index}/{len(clips)} | {index/len(clips):.0%} | elapsed {elapsed:.1f}s | ETA {elapsed/index*(len(clips)-index):.1f}s",flush=True);last=now
    assert landings==27
    media["README.txt"]=("将本目录所有 CJP 文件与三个 JSON 清单放在 FAT32 SD 卡根目录。\n"
        "9 段循环 + 27 段转场 + 3 段独立星光；原始文件字节未改变。\n"
        "先备份，不格式化，不覆盖其他个人文件。安全弹出后将卡插回断电设备。\n"
        "Linux 可在解压目录运行 sha256sum -c MANIFEST.sha256 校验。\n").encode()
    if a.reuse_sd:
        previous=json.loads((a.output/"release-report.json").read_text())
        record=next(r for r in previous if r["file"]=="codex-assistant-sd.zip")
        path=a.output/record["file"]
        if path.stat().st_size!=record["bytes"] or digest(path)!=record["sha256"]:raise ValueError("Existing SD ZIP differs from verified report")
        report.append(record);print("REUSE VERIFIED | SD ZIP and original inputs unchanged",flush=True)
    else:report.append(archive(a.output/"codex-assistant-sd.zip",media,"sd-card"))
    report.append(archive(a.output/"codex-assistant-source.zip",entries,"codex-assistant"))
    (a.output/"SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['file']}\n" for r in report))
    (a.output/"release-report.json").write_bytes(encoded(report))


if __name__=="__main__":main()
