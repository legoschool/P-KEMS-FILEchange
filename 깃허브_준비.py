# -*- coding: utf-8 -*-
"""
깃허브 배포 준비 스크립트
==========================
README 의 'Open In Colab' 배지 주소를 실제 저장소 주소로 바꿔주고,
드라이브 밖에 깨끗한 저장소 폴더를 만들어 준다.

    python 깃허브_준비.py <깃허브아이디> <저장소이름> [만들_위치]

예)
    python 깃허브_준비.py woonhee pkems-converter
    python 깃허브_준비.py woonhee pkems-converter "C:/Users/나/Documents"

왜 드라이브 밖에 만드나
    구글 드라이브 동기화 폴더 안에서 git 을 쓰면 .git 내부 파일이
    동기화와 충돌해 저장소가 깨지는 일이 있다. 밖에서 작업하는 편이 안전하다.
"""

import io
import os
import re
import sys
import shutil
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))

# 저장소에 포함할 것 (이 목록에 없는 것은 올라가지 않는다)
INCLUDE = [
    "PKEMS_변환기.ipynb",
    "pkems_converter.py",
    "pkems_readers.py",
    "pkems_privacy.py",
    "pkems_folder.py",
    "pkems_gdrive.py",
    "build_notebook.py",
    "깃허브_준비.py",
    "README.md",
    "LICENSE",
    ".gitignore",
    "requirements.txt",
]

NOTEBOOK = "PKEMS_변환기.ipynb"


def make_badge(user: str, repo: str, branch: str = "main") -> str:
    # 한글 파일명은 주소에 넣을 때 반드시 인코딩해야 한다
    path = quote(NOTEBOOK)
    return (f"https://colab.research.google.com/github/"
            f"{user}/{repo}/blob/{branch}/{path}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    user, repo = sys.argv[1].strip(), sys.argv[2].strip()
    dest_root = sys.argv[3].strip() if len(sys.argv) > 3 else \
        os.path.join(os.path.expanduser("~"), "Documents")
    dest = os.path.join(dest_root, repo)

    print(f"깃허브   : {user}/{repo}")
    print(f"만들 위치 : {dest}\n")

    # ── 1) 파일 복사
    os.makedirs(dest, exist_ok=True)
    copied, missing = [], []
    for name in INCLUDE:
        src = os.path.join(HERE, name)
        if not os.path.exists(src):
            missing.append(name)
            continue
        shutil.copy2(src, os.path.join(dest, name))
        copied.append(name)

    for n in copied:
        print(f"  복사  {n}")
    for n in missing:
        print(f"  없음  {n}   <-- 확인 필요")

    # ── 2) README 배지 주소 채우기
    readme = os.path.join(dest, "README.md")
    if os.path.exists(readme):
        with io.open(readme, encoding="utf-8") as f:
            text = f.read()
        badge = make_badge(user, repo)
        text = re.sub(
            r"https://colab\.research\.google\.com/github/[^)\s]+",
            badge, text)
        text = text.replace("GITHUB_USER", user).replace("GITHUB_REPO", repo)
        with io.open(readme, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\n  배지 주소 반영: {badge}")

    # ── 3) 안내
    print(f"""
─────────────────────────────────────────────
다음 순서로 올리면 됩니다.

 1) 깃허브에서 빈 저장소 만들기
    https://github.com/new
      - Repository name : {repo}
      - Public 선택
      - README/​.gitignore/License 는 **추가하지 않기** (이미 있음)

 2) 아래 명령을 차례대로 실행

    cd "{dest}"
    git init
    git add .
    git commit -m "첫 공개: 내 기록 마크다운 변환기"
    git branch -M main
    git remote add origin https://github.com/{user}/{repo}.git
    git push -u origin main

 3) 올린 뒤 README 의 배지를 눌러 코랩이 열리는지 확인

올리기 전 확인
    - 변환 결과(md/·images/·_개인정보_보고서.md)가 섞여 있지 않은지
      → .gitignore 가 막아주지만, git status 로 눈으로 확인할 것
─────────────────────────────────────────────""")


if __name__ == "__main__":
    main()
