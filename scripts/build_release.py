#!/usr/bin/env python3
"""Validate the complete repository and create a deterministic skill.zip."""
from pathlib import Path
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ['SKILL.md', 'README.md', 'CHANGELOG.md', 'record-schema.json', 'requirements-optional.txt']
DIRECTORIES = ['agents', 'examples', 'references', 'scripts', 'tests', 'web']


def package_files():
    files = [ROOT/p for p in ROOT_FILES]
    for directory in DIRECTORIES:
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.tmp'})
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def validate():
    node = shutil.which('node')
    if not node: raise RuntimeError('需要 Node 检查 Web JavaScript')
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for i, path in enumerate(ROOT.rglob('*.py')):
            if '.git' in path.parts: continue
            py_compile.compile(str(path), cfile=str(tmp/f'{i}.pyc'), doraise=True)
        count = 0
        for path in sorted((ROOT/'web').iterdir()):
            sources = [path.read_text(encoding='utf-8')] if path.suffix == '.js' else re.findall(r'<script\b[^>]*>(.*?)</script>', path.read_text(encoding='utf-8'), re.S | re.I) if path.suffix == '.html' else []
            for source in sources:
                if not source.strip(): continue
                js = tmp/f'check-{count}.js'; js.write_text(source, encoding='utf-8')
                subprocess.run([node, '--check', str(js)], check=True)
                count += 1
        print(f'Python syntax OK; {count} Web JavaScript blocks/files OK', flush=True)


def build():
    target = ROOT/'skill.zip'
    staging = ROOT/'skill.zip.tmp'
    with zipfile.ZipFile(staging, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in package_files():
            member = zipfile.ZipInfo('drama-dossier/'+p.relative_to(ROOT).as_posix(), date_time=(2026, 11, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            z.writestr(member, p.read_bytes(), compresslevel=9)
    with zipfile.ZipFile(staging) as z:
        if z.testzip() is not None: raise RuntimeError('ZIP 完整性检查失败')
        count = len(z.namelist())
    staging.replace(target)
    print(f'skill.zip: {count} files; {target.stat().st_size} bytes; integrity OK', flush=True)

if __name__ == '__main__':
    validate()
    build()
