#!/usr/bin/env python3
"""Collect ONE pilot campaign into a shareable zip (no .env or Docker secrets)."""
import argparse
import zipfile
from pathlib import Path


def package(folder,output,max_mb=350):
    folder=Path(folder).resolve()
    output=Path(output).resolve()
    if not (folder/'plan.json').is_file():
        raise ValueError('Not a campaign folder: missing plan.json')
    files=[p for p in folder.rglob('*') if p.is_file() and not p.is_symlink() and p.resolve()!=output]
    total=sum(p.stat().st_size for p in files)
    if total>max_mb*1024**2:
        raise ValueError(f'Data exceed {max_mb} MiB: inspect before sharing ({total} bytes)')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f,arcname=str(Path(folder.name)/f.relative_to(folder)))
    return len(files)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('campaign',type=Path)
    p.add_argument('--output',type=Path,default=Path('IoTJ_pilot_results.zip'))
    args=p.parse_args()
    n=package(args.campaign,args.output)
    print(f'Creado {args.output.resolve()} con {n} archivos. Revise contenido antes de compartir.')

if __name__=='__main__':main()
