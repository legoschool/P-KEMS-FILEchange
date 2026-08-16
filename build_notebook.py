# -*- coding: utf-8 -*-
"""
pkems_converter.py 를 읽어서, 배포용 코랩 노트북(PKEMS_변환기.ipynb)을 생성한다.
엔진을 고친 뒤 이 스크립트를 다시 실행하면 노트북도 최신 상태가 된다.

    python build_notebook.py
"""
import io, os, json, base64

HERE = os.path.dirname(os.path.abspath(__file__))
# 노트북에 함께 실어 보낼 엔진 파일들
ENGINE_FILES = [
    "pkems_converter.py",   # 블로그 백업 PDF -> md
    "pkems_readers.py",     # 한글/워드/PPT/엑셀/HTML 읽기
    "pkems_privacy.py",     # 개인정보 자동 가리기
    "pkems_folder.py",      # 폴더 통째로 변환
    "pkems_gdrive.py",      # 구글 문서 내보내기
]
ENGINE = os.path.join(HERE, "pkems_converter.py")   # 하위호환
OUT = os.path.join(HERE, "PKEMS_변환기.ipynb")


def md(*lines):
    return {"cell_type": "markdown", "metadata": {}, "source": list(lines)}


def code(*lines, form=False):
    meta = {"cellView": "form"} if form else {}
    return {"cell_type": "code", "execution_count": None,
            "metadata": meta, "outputs": [], "source": list(lines)}


def L(text):
    """문자열을 ipynb source 배열(줄바꿈 유지)로 변환"""
    lines = text.split("\n")
    return [l + "\n" for l in lines[:-1]] + [lines[-1]]


# ── 엔진 원본 읽기 ────────────────────────────────────────────
engines = {}
for name in ENGINE_FILES:
    with io.open(os.path.join(HERE, name), encoding="utf-8") as f:
        engines[name] = f.read()
engine_src = engines["pkems_converter.py"]          # 하위호환

cells = []

cells.append(md(*L("""# 📚 내 기록 → 마크다운 변환기

흩어져 있는 내 지식·경험을 **AI가 읽기 좋은 마크다운(.md)** 으로 모읍니다.

> PKEMS(개인지식경험관리체계) 프로젝트

### 이 노트북으로 할 수 있는 것

| | 무엇을 | 어떤 형식 |
|---|---|---|
| **1부** | 네이버 블로그 백업 | `.pdf` (글마다 나눠서 변환) |
| **2부** | 문서 폴더 통째로 | `.hwp` `.hwpx` `.docx` `.pptx` `.xlsx` `.pdf` `.html` `.txt` |
| **3부** | 구글 문서 | 구글 문서·시트·슬라이드 |

---

### 사용 방법

**먼저 아래 '준비하기' 두 칸을 실행**한 뒤, 필요한 부(1·2·3)로 가서
각 칸의 **▶ 버튼**을 순서대로 누르면 됩니다.

- 중간에 끊겨도 다시 누르면 **이어서** 진행됩니다
- 한 파일이 실패해도 나머지는 계속 변환됩니다
- 모든 작업은 **본인 구글 드라이브 안에서만** 이루어집니다

⏱️ 파일 100MB당 대략 1~3분.""")))

# 1단계
cells.append(md(*L("## 🔧 준비하기 (설치 + 구글 드라이브 연결) — 맨 처음 한 번\n\n"
                   "▶ 를 누르면 구글 계정 접근 허용을 물어봅니다. **허용**을 눌러주세요.\n"
                   "내 드라이브 안에서만 작업하며, 파일이 외부로 나가지 않습니다.")))

cells.append(code(*L('''#@title ▶ 눌러서 준비하기 { display-mode: "form" }
import subprocess, sys

print("① 필요한 프로그램 설치 중... (30초쯤 걸립니다)")
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                "pymupdf",          # PDF
                "olefile",          # 한글 .hwp
                "python-docx",      # 워드
                "python-pptx",      # 파워포인트
                "openpyxl",         # 엑셀
                "beautifulsoup4",   # HTML
                ], check=False)

print("② 구글 드라이브 연결 중...")
from google.colab import drive
drive.mount('/content/drive')

print("\\n준비 완료! 다음 칸으로 넘어가세요.")'''), form=True))

# 엔진 셀
# %%writefile 은 셀 첫 줄이어야 해서 #@title 과 같이 못 쓴다.
# 엔진 원본들을 base64 로 실어 보내고 파일로 풀어쓰는 방식이 가장 안전하다.
def b64_block(src: str, indent: str = "    ") -> str:
    b = base64.b64encode(src.encode("utf-8")).decode("ascii")
    return "\n".join(f'{indent}"{b[i:i+100]}"' for i in range(0, len(b), 100))


engine_entries = "\n".join(
    f'  "{name}": (\n{b64_block(src, "    ")}\n  ),'
    for name, src in engines.items()
)

cells.append(md(*L("### (자동) 변환 엔진 불러오기 — 이 칸도 ▶ 눌러주세요")))
cells.append(code(*L('''#@title ▶ 눌러서 엔진 불러오기 { display-mode: "form" }
import base64, pathlib, importlib, sys

_ENGINES = {
''' + engine_entries + '''
}

for _name, _b64 in _ENGINES.items():
    pathlib.Path(_name).write_bytes(base64.b64decode(_b64))
sys.path.insert(0, ".")

import pkems_converter, pkems_readers, pkems_privacy, pkems_folder
for _m in (pkems_converter, pkems_readers, pkems_privacy, pkems_folder):
    importlib.reload(_m)
from pkems_converter import Converter, Settings, inspect
from pkems_readers import read_any, SUPPORTED
from pkems_privacy import PrivacyFilter, Policy, preview as 개인정보_미리보기
from pkems_folder import FolderConverter, FolderSettings

print("엔진 준비 완료!")
print("다룰 수 있는 형식:", " ".join(SUPPORTED))'''), form=True))

# ══════════════════════════════════════════════════════════════
# 1부 · 블로그 백업 PDF
# ══════════════════════════════════════════════════════════════
cells.append(md(*L("""---
---

# 📝 1부 · 네이버 블로그 백업 PDF 변환

블로그 글이 **한 편씩 따로** 마크다운 파일이 됩니다. (제목·날짜·카테고리·원문주소 포함)

### 미리 준비할 것

1. **블로그를 PDF로 백업**
   블로그 관리 → 글 전체보기 → 인쇄 → 대상을 **'PDF로 저장'**
   (100편 정도씩 나눠 저장하면 안정적입니다)
2. 구글 드라이브에 폴더를 만들고 PDF 넣기

*(블로그가 없으면 이 부는 건너뛰고 2부로 가세요)*""")))

# 폴더 지정
cells.append(md(*L("""## 1-① PDF가 들어있는 폴더 알려주기

아래 **세 가지 방법 중 아무거나** 하나만 채우면 됩니다.

| 방법 | 예시 |
|------|------|
| 폴더 **링크** 붙여넣기 | `https://drive.google.com/drive/folders/1AbC...` |
| 폴더 **ID**만 붙여넣기 | `1AbCdEfGhIjK...` |
| 내 드라이브 안 **경로** | `블로그백업` 또는 `기록/블로그백업` |""")))

cells.append(code(*L('''#@title ▶ 폴더 지정하기 { display-mode: "form" }
#@markdown ### 폴더 링크 또는 ID (둘 중 하나, 없으면 비워두세요)
드라이브_링크_또는_ID = ""  #@param {type:"string"}
#@markdown ### 또는, 내 드라이브 안의 폴더 경로
내_드라이브_경로 = "블로그백업"  #@param {type:"string"}

import os, re

MYDRIVE = "/content/drive/MyDrive"


def _folder_path_from_id(folder_id):
    """드라이브 폴더 ID -> 마운트된 실제 경로"""
    from google.colab import auth
    from googleapiclient.discovery import build
    auth.authenticate_user()
    svc = build("drive", "v3")
    parts = []
    cur = folder_id
    for _ in range(20):
        info = svc.files().get(fileId=cur, fields="id,name,parents").execute()
        parts.append(info["name"])
        parents = info.get("parents")
        if not parents:
            break
        cur = parents[0]
    parts.reverse()
    # 최상위(내 드라이브) 이름은 버리고 이어붙인다
    return os.path.join(MYDRIVE, *parts[1:]) if len(parts) > 1 else MYDRIVE


PDF_DIR = None
raw = 드라이브_링크_또는_ID.strip()

if raw:
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", raw) or re.match(r"^([A-Za-z0-9_-]{10,})$", raw)
    if not m:
        print("링크/ID 형식을 알아보지 못했습니다. 폴더 주소를 그대로 붙여넣어 보세요.")
    else:
        try:
            PDF_DIR = _folder_path_from_id(m.group(1))
            print(f"폴더를 찾았습니다: {PDF_DIR}")
        except Exception as e:
            print(f"ID로 찾기 실패({e}). 아래 '경로' 방식을 써주세요.")

if PDF_DIR is None:
    PDF_DIR = os.path.join(MYDRIVE, 내_드라이브_경로.strip().strip("/"))

print()
if os.path.isdir(PDF_DIR):
    pdfs = sorted(n for n in os.listdir(PDF_DIR) if n.lower().endswith(".pdf"))
    print(f"경로 : {PDF_DIR}")
    print(f"PDF  : {len(pdfs)}개 발견")
    for n in pdfs[:15]:
        mb = os.path.getsize(os.path.join(PDF_DIR, n)) / 1024 / 1024
        print(f"   - {n}  ({mb:.1f} MB)")
    if len(pdfs) > 15:
        print(f"   … 외 {len(pdfs)-15}개")
    if not pdfs:
        print("\\n⚠ 이 폴더에 PDF가 없습니다. 폴더를 다시 확인해주세요.")
else:
    print(f"⚠ 폴더를 찾을 수 없습니다: {PDF_DIR}")
    print("   왼쪽 파일 탐색기(📁)에서 실제 폴더 이름을 확인해보세요.")'''), form=True))

# 3단계
cells.append(md(*L("""## 1-② 미리 확인하기 (권장)

변환하기 전에, PDF가 올바른 형식인지 **미리 훑어봅니다.**
글이 몇 편 들어있는지 여기서 확인할 수 있어요.""")))

cells.append(code(*L('''#@title ▶ 미리 확인하기 { display-mode: "form" }
import os

pdfs = sorted(n for n in os.listdir(PDF_DIR) if n.lower().endswith(".pdf"))
총합 = 0
for n in pdfs:
    r = inspect(os.path.join(PDF_DIR, n))
    총합 += r["posts"]
    print("-" * 46)
print(f"\\n예상 변환 결과: 전체 약 {총합}편")'''), form=True))

# 4단계
cells.append(md(*L("""## 1-③ 변환 실행

설정을 확인하고 ▶ 를 누르세요. PDF 양에 따라 몇 분 걸립니다.""")))

cells.append(code(*L('''#@title ▶ 변환 시작 { display-mode: "form" }
#@markdown ### 결과를 저장할 폴더 이름 (PDF 폴더 안에 생깁니다)
저장폴더 = "md"  #@param {type:"string"}
#@markdown ### 본문 사진도 함께 저장할까요?
사진_저장 = True  #@param {type:"boolean"}
#@markdown ### 이미 변환한 글은 건너뛸까요? (다시 돌려도 안전)
중복_건너뛰기 = True  #@param {type:"boolean"}
#@markdown ### 파일 이름 형식
파일이름형식 = "{date}_{title}"  #@param ["{date}_{title}", "{title}", "{date}"]

import os

OUT_DIR = os.path.join(PDF_DIR, 저장폴더.strip() or "md")

conv = Converter(Settings(
    pdf_dir          = PDF_DIR,
    out_dir          = OUT_DIR,
    extract_images   = 사진_저장,
    skip_existing    = 중복_건너뛰기,
    filename_pattern = 파일이름형식,
))
결과 = conv.run()

print()
print("저장 위치:", OUT_DIR)
print("구글 드라이브에 반영되기까지 잠시 걸릴 수 있습니다.")'''), form=True))

# 5단계
cells.append(md(*L("## 1-④ 결과 살펴보기")))

cells.append(code(*L('''#@title ▶ 결과 요약 보기 { display-mode: "form" }
import os, io, json, collections

idx_path = os.path.join(OUT_DIR, "_index.json")
with io.open(idx_path, encoding="utf-8") as f:
    idx = json.load(f)

print(f"전체 {len(idx)}편\\n")

years = collections.Counter(e["date"][:4] for e in idx)
print("연도별")
for y in sorted(years):
    print(f"   {y} : {years[y]:4}편  " + "█" * min(40, years[y] // 3))

print("\\n카테고리 상위 10")
for c, n in collections.Counter(e.get("category", "") for e in idx).most_common(10):
    print(f"   {n:4}편  {c or '(없음)'}")

print(f"\\n기간 : {min(e['date'] for e in idx)} ~ {max(e['date'] for e in idx)}")
print(f"목차 : {os.path.join(OUT_DIR, 'INDEX.md')}")'''), form=True))

# ══════════════════════════════════════════════════════════════
# 2부 · 문서 폴더 통째로 변환
# ══════════════════════════════════════════════════════════════
cells.append(md(*L("""---
---

# 📂 2부 · 문서 폴더 통째로 변환

블로그 PDF 말고도, **폴더 하나를 통째로** 마크다운으로 바꿀 수 있습니다.

| 다루는 형식 | |
|---|---|
| 한글 | `.hwp` `.hwpx` |
| 오피스 | `.docx` `.pptx` `.xlsx` |
| 그 외 | `.pdf` `.html` `.txt` `.csv` |

원래 폴더 구조를 그대로 유지하며, 한 파일이 실패해도 나머지는 계속 진행됩니다.

> ⚠️ **개인정보 주의** — 업무 문서에는 이름·연락처·계좌 같은 정보가 들어있을 수 있습니다.
> 변환 결과를 웹에 올릴 때는 **반드시 선별**하세요.""")))

cells.append(code(*L('''#@title ▶ 폴더 훑어보기 (변환 없이 현황만) { display-mode: "form" }
#@markdown ### 변환할 폴더 (내 드라이브 안 경로)
문서폴더 = "01_학교"  #@param {type:"string"}
#@markdown ### 결과를 저장할 폴더
결과폴더 = "PKEMS/변환결과"  #@param {type:"string"}

import os
SRC_DIR = os.path.join("/content/drive/MyDrive", 문서폴더.strip().strip("/"))
DST_DIR = os.path.join("/content/drive/MyDrive", 결과폴더.strip().strip("/"))

if not os.path.isdir(SRC_DIR):
    print(f"⚠ 폴더를 찾을 수 없습니다: {SRC_DIR}")
else:
    fc = FolderConverter(FolderSettings(src_dir=SRC_DIR, out_dir=DST_DIR))
    fc.scan()'''), form=True))

cells.append(md(*L("""## 2-② 개인정보를 어떻게 가릴지 정하기

종류마다 처리 방식을 고를 수 있습니다.

| 방식 | 뜻 | 예시 |
|------|-----|------|
| **부분가림** | 일부만 남김 | 이운희 → `이**` · 010-1234-5678 → `010-****-****` |
| **가림** | 전부 가림 | 900101-1234567 → `******-*******` |
| **삭제** | 아예 지움 | (빈칸) |
| **그대로** | 건드리지 않음 | 이운희 |""")))

cells.append(code(*L('''#@title ▶ 개인정보 설정 { display-mode: "form" }
#@markdown ### 개인정보를 가릴까요?
개인정보_가리기 = True  #@param {type:"boolean"}
#@markdown ---
#@markdown ### 종류별 처리 방식
주민등록번호 = "가림"      #@param ["가림", "부분가림", "삭제", "그대로"]
전화번호 = "부분가림"      #@param ["부분가림", "가림", "삭제", "그대로"]
이름 = "부분가림"          #@param ["부분가림", "가림", "삭제", "그대로"]
계좌번호 = "가림"          #@param ["가림", "부분가림", "삭제", "그대로"]
카드번호 = "가림"          #@param ["가림", "부분가림", "삭제", "그대로"]
이메일 = "부분가림"        #@param ["부분가림", "가림", "삭제", "그대로"]
주소 = "부분가림"          #@param ["부분가림", "가림", "삭제", "그대로"]
생년월일 = "부분가림"      #@param ["부분가림", "가림", "삭제", "그대로"]
차량번호 = "부분가림"      #@param ["부분가림", "가림", "삭제", "그대로"]
#@markdown ---
#@markdown ### 이름 찾는 강도
#@markdown `라벨만`=성명·담당자 옆의 이름만 · `보통`=+문서 안 반복 등장(권장) · `적극적`=+성씨 추정(오탐 늘어남)
이름_탐지강도 = "보통"  #@param ["보통", "라벨만", "적극적"]
#@markdown ### 보고서에 가리기 전 원본을 남길까요?
#@markdown 켜면 무엇이 바뀌었는지 대조할 수 있지만, **보고서 자체가 개인정보 덩어리**가 됩니다.
보고서_원본표시 = True  #@param {type:"boolean"}

정책 = Policy(
    주민등록번호=주민등록번호, 전화번호=전화번호, 이름=이름,
    계좌번호=계좌번호, 카드번호=카드번호, 이메일=이메일,
    주소=주소, 생년월일=생년월일, 차량번호=차량번호,
    이름_탐지강도=이름_탐지강도,
)
print("설정 완료")
for k, v in vars(정책).items():
    print(f"   {k:12} {v}")'''), form=True))

cells.append(md(*L("""### 미리보기 — 파일 하나로 시험해보기 (권장)

전체를 돌리기 전에 **파일 한 개**로 어떻게 가려지는지 확인해보세요.""")))

cells.append(code(*L('''#@title ▶ 파일 하나로 미리보기 { display-mode: "form" }
#@markdown ### 확인할 파일 (내 드라이브 안 경로, 비우면 폴더에서 자동 선택)
확인할_파일 = ""  #@param {type:"string"}

import os

경로 = os.path.join("/content/drive/MyDrive", 확인할_파일.strip().strip("/")) \\
       if 확인할_파일.strip() else None

if 경로 is None:
    fc0 = FolderConverter(FolderSettings(src_dir=SRC_DIR, out_dir=DST_DIR))
    후보 = fc0.collect()
    경로 = 후보[0] if 후보 else None

if not 경로:
    print("확인할 파일을 찾지 못했습니다.")
else:
    print("파일 :", os.path.basename(경로), "\\n")
    _r = read_any(경로)
    if not _r.ok:
        print("읽기 실패:", _r.error)
    else:
        개인정보_미리보기(_r.text, 정책)'''), form=True))

cells.append(md(*L("## 2-③ 폴더 변환 시작")))

cells.append(code(*L('''#@title ▶ 폴더 변환 시작 { display-mode: "form" }
#@markdown ### 먼저 몇 개만 시험해볼까요? (0 = 전부)
시험_개수 = 30  #@param {type:"integer"}
#@markdown ### 이미 변환한 파일은 건너뛸까요?
중복_건너뛰기 = True  #@param {type:"boolean"}
#@markdown ### 원본 폴더 구조를 유지할까요?
폴더구조_유지 = True  #@param {type:"boolean"}

fc = FolderConverter(FolderSettings(
    src_dir        = SRC_DIR,
    out_dir        = DST_DIR,
    skip_existing  = 중복_건너뛰기,
    keep_tree      = 폴더구조_유지,
    개인정보_가리기 = 개인정보_가리기,
    개인정보_정책   = 정책,
    보고서_원본표시 = 보고서_원본표시,
))
결과 = fc.run(limit=(시험_개수 or None))

print()
print("저장 위치   :", DST_DIR)
print("목차        :", os.path.join(DST_DIR, "INDEX.md"))
print("개인정보보고서:", os.path.join(DST_DIR, "_개인정보_보고서.md"))'''), form=True))

# ══════════════════════════════════════════════════════════════
# 3부 · 구글 문서 가져오기
# ══════════════════════════════════════════════════════════════
cells.append(md(*L("""---
---

# 📄 3부 · 구글 문서·시트·슬라이드 가져오기

구글 문서는 **내 컴퓨터에 실체가 없는 온라인 문서**라서, 파일로는 읽을 수 없습니다.
Drive API 로 **내보내기(export)** 해야 합니다. (구글 문서 → 마크다운, 시트 → CSV)

처음 실행하면 계정 접근 허용을 한 번 더 물어봅니다.""")))

cells.append(code(*L('''#@title ▶ ① 구글 문서 목록 보기 { display-mode: "form" }
#@markdown ### 폴더 링크 또는 ID
구글_폴더 = ""  #@param {type:"string"}
#@markdown ### 하위 폴더까지 찾을까요?
하위폴더_포함 = True  #@param {type:"boolean"}

import importlib, pkems_gdrive
importlib.reload(pkems_gdrive)
from pkems_gdrive import GoogleDocs

if not 구글_폴더.strip():
    print("폴더 링크나 ID를 입력해주세요.")
else:
    gd = GoogleDocs()
    문서목록 = gd.list_folder(구글_폴더, recursive=하위폴더_포함)'''), form=True))

cells.append(code(*L('''#@title ▶ ② 구글 문서 가져오기 { display-mode: "form" }
#@markdown ### 저장할 폴더 (내 드라이브 안 경로)
구글_저장폴더 = "PKEMS/구글문서"  #@param {type:"string"}

import os
G_OUT = os.path.join("/content/drive/MyDrive", 구글_저장폴더.strip().strip("/"))
결과 = gd.export_folder(구글_폴더, G_OUT, recursive=하위폴더_포함)
print()
print("저장 위치 :", G_OUT)'''), form=True))

cells.append(md(*L("""---

### 잘 안 될 때

| 증상 | 해결 |
|------|------|
| 폴더를 찾을 수 없다 | 왼쪽 📁 아이콘 → `drive/MyDrive` 에서 실제 폴더명 확인 |
| 글을 0편 발견 | 네이버 블로그 **인쇄 → PDF 저장** 방식의 백업인지 확인 |
| 중간에 멈춤 | 코랩 연결이 끊긴 것. 다시 ▶ 누르면 **이어서** 진행됩니다 |
| 사진이 너무 많다 | `사진_저장`을 끄고 다시 실행 |

### 다음 단계

변환된 `.md` 파일들은 그대로 **나만의 지식창고**가 됩니다.
Claude·ChatGPT 같은 AI에게 폴더째 물어보거나, 웹 뷰어로 만들어 검색할 수 있습니다.

*PKEMS · 개인지식경험관리체계*""")))

nb = {
    "nbformat": 4,
    "nbformat_minor": 0,
    "metadata": {
        "colab": {"name": "PKEMS_변환기.ipynb", "provenance": [], "toc_visible": True},
        "kernelspec": {"name": "python3", "display_name": "Python 3"},
        "language_info": {"name": "python"},
    },
    "cells": cells,
}

with io.open(OUT, "w", encoding="utf-8") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)

print(f"생성 완료: {OUT}")
print(f"셀 {len(cells)}개 · 엔진 {len(engine_src.splitlines())}줄 포함")
