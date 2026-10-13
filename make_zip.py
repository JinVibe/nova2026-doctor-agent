"""제출 ZIP 생성. 허용한 파일만 담고, 개발 키가 섞여 있으면 중단한다.

  python make_zip.py nova-starter        → build/submission.zip
"""
import re
import sys
import zipfile
from pathlib import Path

ALLOWED_SUFFIXES = {'.py', '.txt', '.json', '.yaml', '.yml', '.md'}
EXCLUDED_NAMES = {'sample.py', 'local.Dockerfile', 'README.md'}
SECRET = re.compile(r'nova_[A-Za-z0-9]{8,}|nvr_[A-Za-z0-9]{8,}')
LIMIT = 50_000_000


def main(src):
    src = Path(src).resolve()
    if not (src / 'run.py').exists() or not (src / 'requirements.txt').exists():
        sys.exit(f'{src}에 run.py와 requirements.txt가 있어야 합니다.')
    files = [p for p in src.rglob('*')
             if p.is_file() and p.suffix in ALLOWED_SUFFIXES and p.name not in EXCLUDED_NAMES
             and not p.name.startswith('.env') and '__pycache__' not in p.parts]
    leaks = [p for p in files if SECRET.search(p.read_text(encoding='utf-8', errors='ignore'))]
    if leaks:
        sys.exit('개발 키로 보이는 문자열이 있어 중단합니다: ' + ', '.join(str(p.relative_to(src)) for p in leaks))
    out = Path(__file__).resolve().parent / 'build' / 'submission.zip'
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(files):
            zf.write(p, p.relative_to(src).as_posix())
    size = out.stat().st_size
    print(f'{out} ({size:,} bytes, {len(files)} files)')
    for p in sorted(files):
        print('  ', p.relative_to(src).as_posix())
    if size > LIMIT:
        sys.exit('50MB 초과')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '.')
