""".env를 환경변수로 읽은 뒤 스크립트를 실행한다 (개발 키를 코드에 넣지 않기 위함).

  python dev.py nova-starter/sample.py --list
  python dev.py nova-starter/sample.py --case <UUID>
"""
import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path=ROOT / '.env'):
    if not path.exists():
        sys.exit('.env가 없습니다. .env.example을 복사해 NOVA_DEV_KEY를 채우세요.')
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip())


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    load_env()
    script = Path(sys.argv[1]).resolve()
    sys.argv = [str(script), *sys.argv[2:]]
    sys.path.insert(0, str(script.parent))
    sys.stdout.reconfigure(encoding='utf-8')
    runpy.run_path(str(script), run_name='__main__')
