"""제출 ZIP 생성. 허용한 파일만 담고, 개발 키가 섞여 있으면 중단한다.

  python make_zip.py            → build/submission.zip (run.py, requirements.txt, nova_sdk.py, agent/)
  python make_zip.py --save v0.9 → 위 ZIP을 submissions/2026-10-13_v0.9.zip 으로도 복사(팀 공유용, 커밋 대상)
"""
import datetime
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INCLUDE = ['run.py', 'requirements.txt', 'nova_sdk.py', 'agent']
ALLOWED_SUFFIXES = {'.py', '.txt', '.json'}
SECRET = re.compile(r'nova_[A-Za-z0-9]{8,}|nvr_[A-Za-z0-9]{8,}')
LIMIT = 50_000_000


def collect(src):
    files = []
    for name in INCLUDE:
        path = src / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files += [p for p in path.rglob('*') if p.is_file() and p.suffix in ALLOWED_SUFFIXES
                      and '__pycache__' not in p.parts]
    return files


def main(src, save_tag=None):
    src = Path(src).resolve()
    if not (src / 'run.py').exists() or not (src / 'requirements.txt').exists():
        sys.exit(f'{src}에 run.py와 requirements.txt가 있어야 합니다.')
    files = collect(src)
    leaks = [p for p in files if SECRET.search(p.read_text(encoding='utf-8', errors='ignore'))]
    if leaks:
        sys.exit('개발 키로 보이는 문자열이 있어 중단합니다: ' + ', '.join(str(p.relative_to(src)) for p in leaks))
    if (src / 'requirements.txt').read_bytes().startswith(b'\xef\xbb\xbf'):
        sys.exit('requirements.txt에 UTF-8 BOM이 있습니다. BOM 없이 저장하세요.')
    out = ROOT / 'build' / 'submission.zip'
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
    if save_tag:
        with zipfile.ZipFile(out) as zf:  # 복사 직전 ZIP 안에서도 키 패턴을 한 번 더 확인
            for info in zf.infolist():
                if SECRET.search(zf.read(info).decode('utf-8', errors='ignore')):
                    sys.exit(f'ZIP 안 {info.filename}에 개발 키로 보이는 문자열이 있어 저장하지 않습니다.')
        saved = ROOT / 'submissions' / f'{datetime.date.today():%Y-%m-%d}_{save_tag}.zip'
        saved.parent.mkdir(exist_ok=True)
        shutil.copyfile(out, saved)
        print(f'saved → {saved.relative_to(ROOT).as_posix()}')


if __name__ == '__main__':
    args = sys.argv[1:]
    tag = None
    if '--save' in args:
        i = args.index('--save')
        tag = args[i + 1] if i + 1 < len(args) else sys.exit('--save 뒤에 버전 이름을 적어 주세요(예: v0.9)')
        del args[i:i + 2]
    main(args[0] if args else ROOT, tag)
