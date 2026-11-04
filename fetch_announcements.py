#!/usr/bin/env python3
"""하위 호환용 진입점 — 실제 구현은 gses_bot 패키지에 있습니다.

    python fetch_announcements.py [--init | --dry-run | --ping | --send-on-first-run]

새 사용법: python -m gses_bot --help
"""

import sys

from gses_bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
