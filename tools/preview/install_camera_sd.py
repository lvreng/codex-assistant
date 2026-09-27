"""Install only the new camera files, with backup, readback CRC and SHA256."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'host'))
from media_format import CLIPS,TRANSITIONS,clip_name,transition_name,inspect_clip


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    target=args.destination.resolve()
    if not target.is_mount():
        parser.error('Destination must be an explicitly mounted SD card')
    old={clip_name(spec[0]):(target/clip_name(spec[0])).stat() for spec in CLIPS}
    original={name:(stat.st_size,stat.st_mtime_ns) for name,stat in old.items()}
    manifest=json.loads((args.source/'camera.json').read_text())
    clips=manifest['clips']
    t2=any(s.get('target_clip') is not None for s in clips)
    expected=({(s,('sol','terra','luna','astra').index(key)+1,c)
               for c,key,_,_ in CLIPS for s in range(1,5)
               if s != ('sol','terra','luna','astra').index(key)+1}
              if t2 else {(s,t,None) for s,t in TRANSITIONS})
    actual={(s['source'],s['target'],s.get('target_clip')) for s in clips}
    if actual != expected or len(clips) != len(expected):
        raise ValueError('Manifest must contain all required camera clips')
    total=sum(s['bytes'] for s in manifest['clips'])
    if shutil.disk_usage(target).free<total*2:
        raise ValueError('Insufficient free space for atomic SD installation')
    backup=ROOT/'output/sd-camera-v2'/('card-backup-'+time.strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=False)
    started=time.monotonic();done=0
    for number,spec in enumerate(manifest['clips'],1):
        name=transition_name(spec['source'],spec['target'],target_clip=spec.get('target_clip'))
        if name!=spec['file']:
            raise ValueError('Unexpected filename in manifest')
        source=args.source/name;destination=target/name
        if digest(source)!=spec['sha256']:
            raise ValueError(f'Source SHA256 mismatch: {name}')
        if destination.exists():
            shutil.copy2(destination,backup/name)
        temporary=destination.with_suffix('.CJP.NEW')
        if temporary.exists():
            shutil.copy2(temporary,backup/temporary.name)
        with source.open('rb') as input,temporary.open('wb') as output:
            shutil.copyfileobj(input,output,1024*1024)
            output.flush();os.fsync(output.fileno())
        with temporary.open('rb') as input:
            info=inspect_clip(input,verify_frames=True)
        if ((info['source'],info['key'],info.get('target_clip')) !=
                (spec['source'],spec['target'],spec.get('target_clip')) or
                info.get('entry_frame', 0) != spec.get('entry_frame', 0) or digest(temporary)!=spec['sha256']):
            raise ValueError(f'SD readback mismatch: {name}')
        if t2:
            with (target/clip_name(spec['target_clip'])).open('rb') as loop, temporary.open('rb') as camera:
                loop_info=inspect_clip(loop)
                offset,length,_=loop_info['entries'][0]
                loop.seek(offset);first=loop.read(length)
                offset,length,_=info['entries'][-1]
                camera.seek(offset)
                if camera.read(length)!=first:
                    raise ValueError(f'SD target loop endpoint differs: {name}')
        temporary.replace(destination)
        done+=spec['bytes'];elapsed=time.monotonic()-started
        print(f'SD write [{"#"*number}{"."*(len(clips)-number)}] {done/total:.1%} | '
              f'{done}/{total} bytes | {number}/{len(clips)} files | elapsed {elapsed:.1f}s | '
              f'ETA {elapsed/done*(total-done):.1f}s | SHA256 verified',flush=True)
    manifest_path=target/'camera.json'
    if manifest_path.exists():shutil.copy2(manifest_path,backup/'camera.json')
    with (target/'camera.NEW').open('wb') as output:
        output.write((args.source/'camera.json').read_bytes());output.flush();os.fsync(output.fileno())
    (target/'camera.NEW').replace(manifest_path)
    for name,identity in original.items():
        current=(target/name).stat()
        if (current.st_size,current.st_mtime_ns)!=identity:
            raise RuntimeError(f'Original loop unexpectedly changed: {name}')
    report={'target':str(target),'original_loops_unchanged':list(original),'new_files':manifest['clips']}
    (args.source/'installed.json').write_text(json.dumps(report,indent=2)+'\n')
    print(f'SD install complete: {len(clips)} camera clips verified; 9 original loops unchanged.',flush=True)


if __name__=='__main__':
    main()
