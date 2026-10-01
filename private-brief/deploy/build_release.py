"""ARM64 CI packager. Copies an allowlist; never runtime files or private fixtures."""
import argparse
import json
import platform
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    assert platform.machine() in ('aarch64','arm64')
    root=Path(__file__).resolve().parents[1];out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage=Path(tmp)
        for name in ('brief','templates','assets'):
            shutil.copytree(root/name,stage/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        shutil.copyfile(root/'requirements-release.txt',stage/'requirements.txt')
        subprocess.run(['python','-m','pip','wheel','--wheel-dir',str(stage/'wheels'),'-r',str(stage/'requirements.txt')],check=True)
        (stage/'BUILD.json').write_text(json.dumps({'commit':a.commit,'architecture':'aarch64','python':'3.13'}))
        with tarfile.open(out/'brief-arm64.tar.gz','w:gz') as tar:
            for child in sorted(stage.iterdir()): tar.add(child,arcname=child.name)

if __name__=='__main__':main()
