# -*- coding: utf-8 -*-
"""
PKEMS 문서 읽기 모듈
=====================
여러 형식의 문서를 '마크다운 본문'으로 읽어들인다.

지원 형식
    .hwp   한글 (구버전, HWP 5.0 바이너리)
    .hwpx  한글 (신버전, ZIP+XML)
    .docx  워드
    .pptx  파워포인트  (슬라이드별 + 발표자 노트)
    .xlsx  엑셀        (시트별 마크다운 표)
    .csv   표 데이터
    .pdf   PDF (텍스트형)
    .html  웹문서
    .txt   일반 텍스트
    .md    마크다운 (그대로 통과)
    .gdoc/.gsheet/.gslides  구글 문서 바로가기 (문서 ID만 읽음)

사용법
    from pkems_readers import read_any, SUPPORTED
    doc = read_any("보고서.hwp")
    print(doc.text)

각 읽기 함수는 ReadResult 를 돌려준다. 실패해도 예외를 던지지 않고
ok=False 와 error 메시지를 담아 돌려주므로, 일괄 변환이 중단되지 않는다.

PKEMS(개인지식경험관리체계) 프로젝트
"""

from __future__ import annotations

import os
import re
import io
import csv
import json
import zlib
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────
@dataclass
class ReadResult:
    ok: bool
    text: str = ""
    kind: str = ""                      # 형식 이름 (한글, 워드 …)
    meta: dict = field(default_factory=dict)
    error: str = ""

    @property
    def chars(self) -> int:
        return len(self.text)


def _clean(paras: list[str]) -> str:
    """빈 줄 정리 후 문단 사이 한 줄 띄우기"""
    out, prev_blank = [], True
    for p in paras:
        s = (p or "").strip()
        if not s:
            prev_blank = True
            continue
        if not prev_blank and out:
            out.append("")
        out.append(s)
        prev_blank = False
    return "\n\n".join(x for x in out if x)


# ─────────────────────────────────────────────────────────────
# 한글 (.hwp) — HWP 5.0 바이너리
# ─────────────────────────────────────────────────────────────
HWPTAG_PARA_TEXT = 0x10 + 51            # 0x43

# 문단 텍스트에 섞인 제어문자: 아래 값들은 '자기 + 6워드 + 자기' = 8워드 블록
_HWP_BLOCK_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15,
                   16, 17, 18, 19, 20, 21, 22, 23}


def _hwp_decode_para(payload: bytes) -> str:
    """HWP 문단 레코드는 UTF-16 '코드 단위' 배열이다.
    이모지 등은 서로게이트 쌍(2워드)으로 들어오므로 합쳐 주어야 한다."""
    n = len(payload) // 2
    if n == 0:
        return ""
    words = struct.unpack_from(f"<{n}H", payload, 0)
    buf, i = [], 0
    while i < n:
        c = words[i]
        if c in _HWP_BLOCK_CTRL:
            i += 8                       # 표·그림 등 제어 블록 건너뛰기
            continue
        if c < 32:
            if c in (10, 13):
                buf.append("\n")
            elif c in (24, 30, 31):
                buf.append(" ")
            i += 1
            continue
        # 서로게이트 쌍 합치기
        if 0xD800 <= c <= 0xDBFF and i + 1 < n and 0xDC00 <= words[i + 1] <= 0xDFFF:
            buf.append(chr(0x10000 + ((c - 0xD800) << 10) + (words[i + 1] - 0xDC00)))
            i += 2
            continue
        if 0xD800 <= c <= 0xDFFF:        # 짝 없는 서로게이트는 버린다
            i += 1
            continue
        buf.append(chr(c))
        i += 1
    return "".join(buf).strip()


def read_hwp(path: str) -> ReadResult:
    try:
        import olefile
    except ImportError:
        return ReadResult(False, kind="한글", error="olefile 설치 필요 (pip install olefile)")
    try:
        f = olefile.OleFileIO(path)
    except Exception as e:
        return ReadResult(False, kind="한글", error=f"파일 열기 실패: {e}")

    try:
        dirs = f.listdir()
        header = f.openstream("FileHeader").read()
        compressed = bool(header[36] & 1)

        sections = sorted(
            (d for d in dirs if d and d[0] == "BodyText" and d[1].startswith("Section")),
            key=lambda d: int(re.sub(r"\D", "", d[1]) or 0),
        )
        if not sections:
            return ReadResult(False, kind="한글", error="본문(BodyText)이 없습니다")

        paras = []
        for sec in sections:
            data = f.openstream(sec).read()
            if compressed:
                try:
                    data = zlib.decompress(data, -15)
                except zlib.error:
                    continue
            i, n = 0, len(data)
            while i < n - 4:
                (word,) = struct.unpack_from("<I", data, i)
                tag = word & 0x3FF
                size = (word >> 20) & 0xFFF
                i += 4
                if size == 0xFFF:
                    (size,) = struct.unpack_from("<I", data, i)
                    i += 4
                payload = data[i:i + size]
                i += size
                if tag == HWPTAG_PARA_TEXT:
                    paras.append(_hwp_decode_para(payload))
        return ReadResult(True, _clean(paras), "한글",
                          {"섹션": len(sections), "문단": len(paras)})
    except Exception as e:
        return ReadResult(False, kind="한글", error=f"본문 해석 실패: {e}")
    finally:
        try:
            f.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 한글 (.hwpx) — ZIP + XML (표준 라이브러리만 사용)
# ─────────────────────────────────────────────────────────────
def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_hwpx(path: str) -> ReadResult:
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return ReadResult(False, kind="한글", error=f"파일 열기 실패: {e}")
    try:
        secs = sorted(n for n in z.namelist()
                      if re.match(r"Contents/section\d+\.xml$", n))
        if not secs:
            return ReadResult(False, kind="한글", error="section XML을 찾지 못했습니다")

        paras = []
        for s in secs:
            root = ET.fromstring(z.read(s))
            for el in root.iter():
                if _localname(el.tag) != "p":
                    continue
                txt = "".join(t.text or "" for t in el.iter()
                              if _localname(t.tag) == "t")
                paras.append(txt.strip())
        return ReadResult(True, _clean(paras), "한글",
                          {"섹션": len(secs), "문단": len(paras)})
    except Exception as e:
        return ReadResult(False, kind="한글", error=f"본문 해석 실패: {e}")
    finally:
        z.close()


# ─────────────────────────────────────────────────────────────
# 워드 (.docx)
# ─────────────────────────────────────────────────────────────
def read_docx(path: str) -> ReadResult:
    try:
        import docx
    except ImportError:
        return ReadResult(False, kind="워드", error="python-docx 설치 필요")
    try:
        d = docx.Document(path)
        out = []
        for p in d.paragraphs:
            s = p.text.strip()
            if not s:
                continue
            style = (p.style.name or "").lower()
            m = re.search(r"heading (\d)", style)
            out.append(("#" * min(int(m.group(1)), 6) + " " + s) if m else s)
        for t in d.tables:
            out.append(_rows_to_md([[c.text.strip() for c in r.cells] for r in t.rows]))
        return ReadResult(True, _clean(out), "워드",
                          {"문단": len(d.paragraphs), "표": len(d.tables)})
    except Exception as e:
        return ReadResult(False, kind="워드", error=str(e))


# ─────────────────────────────────────────────────────────────
# 파워포인트 (.pptx)
# ─────────────────────────────────────────────────────────────
def read_pptx(path: str) -> ReadResult:
    try:
        from pptx import Presentation
    except ImportError:
        return ReadResult(False, kind="파워포인트", error="python-pptx 설치 필요")
    try:
        prs = Presentation(path)
        out = []
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"## 슬라이드 {i}")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        s = "".join(r.text for r in para.runs).strip()
                        if s:
                            out.append(s)
                if getattr(shape, "has_table", False):
                    out.append(_rows_to_md(
                        [[c.text.strip() for c in r.cells] for r in shape.table.rows]))
            try:
                if slide.has_notes_slide:
                    note = slide.notes_slide.notes_text_frame.text.strip()
                    if note:
                        out += ["> **발표자 노트**", "> " + note.replace("\n", "\n> ")]
            except Exception:
                pass
        return ReadResult(True, _clean(out), "파워포인트",
                          {"슬라이드": len(prs.slides)})
    except Exception as e:
        return ReadResult(False, kind="파워포인트", error=str(e))


# ─────────────────────────────────────────────────────────────
# 엑셀 (.xlsx) / csv
# ─────────────────────────────────────────────────────────────
def _rows_to_md(rows: list[list[str]], max_rows: int = 300) -> str:
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return ""
    cut = rows[:max_rows]
    width = max(len(r) for r in cut)
    def fix(r):
        r = list(r) + [""] * (width - len(r))
        return [str(c or "").replace("|", "／").replace("\n", " ").strip() for c in r]
    head = fix(cut[0])
    lines = ["| " + " | ".join(head) + " |",
             "|" + "|".join(["---"] * width) + "|"]
    for r in cut[1:]:
        lines.append("| " + " | ".join(fix(r)) + " |")
    if len(rows) > max_rows:
        lines.append(f"\n*(전체 {len(rows)}행 중 {max_rows}행만 표시)*")
    return "\n".join(lines)


def read_xlsx(path: str) -> ReadResult:
    try:
        import openpyxl
    except ImportError:
        return ReadResult(False, kind="엑셀", error="openpyxl 설치 필요")
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out = []
        for ws in wb.worksheets:
            rows = [[("" if c is None else str(c)) for c in row]
                    for row in ws.iter_rows(values_only=True)]
            table = _rows_to_md(rows)
            if table:
                out += [f"## {ws.title}", table]
        wb.close()
        return ReadResult(True, _clean(out), "엑셀", {"시트": len(wb.worksheets)})
    except Exception as e:
        return ReadResult(False, kind="엑셀", error=str(e))


def read_csv(path: str) -> ReadResult:
    for enc in ("utf-8-sig", "cp949", "utf-8"):
        try:
            with io.open(path, encoding=enc, newline="") as f:
                rows = list(csv.reader(f))
            return ReadResult(True, _rows_to_md(rows), "표", {"행": len(rows)})
        except UnicodeDecodeError:
            continue
        except Exception as e:
            return ReadResult(False, kind="표", error=str(e))
    return ReadResult(False, kind="표", error="문자 인코딩을 알 수 없습니다")


# ─────────────────────────────────────────────────────────────
# HTML / 텍스트
# ─────────────────────────────────────────────────────────────
def read_html(path: str) -> ReadResult:
    raw = None
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            with io.open(path, encoding=enc) as f:
                raw = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if raw is None:
        return ReadResult(False, kind="웹문서", error="문자 인코딩을 알 수 없습니다")
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        title = (soup.title.string or "").strip() if soup.title else ""
        parts = []
        for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
            s = el.get_text(" ", strip=True)
            if not s:
                continue
            if el.name.startswith("h"):
                parts.append("#" * int(el.name[1]) + " " + s)
            elif el.name == "li":
                parts.append("- " + s)
            else:
                parts.append(s)
        return ReadResult(True, _clean(parts), "웹문서", {"제목": title})
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", raw)
        return ReadResult(True, _clean(text.split("\n")), "웹문서", {})
    except Exception as e:
        return ReadResult(False, kind="웹문서", error=str(e))


def read_text(path: str) -> ReadResult:
    for enc in ("utf-8", "cp949", "euc-kr", "utf-16"):
        try:
            with io.open(path, encoding=enc) as f:
                return ReadResult(True, f.read().strip(), "텍스트", {"인코딩": enc})
        except (UnicodeDecodeError, LookupError):
            continue
        except Exception as e:
            return ReadResult(False, kind="텍스트", error=str(e))
    return ReadResult(False, kind="텍스트", error="문자 인코딩을 알 수 없습니다")


# ─────────────────────────────────────────────────────────────
# PDF (일반 문서용 · 블로그 백업은 pkems_converter 를 사용)
# ─────────────────────────────────────────────────────────────
def read_pdf(path: str) -> ReadResult:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return ReadResult(False, kind="PDF", error="pymupdf 설치 필요")
    try:
        doc = pymupdf.open(path)
        pages = doc.page_count
        out = []
        for i in range(pages):
            t = doc[i].get_text().strip()
            if t:
                out.append(t)
        doc.close()
        text = _clean(out)
        if len(text) < 20 and pages > 0:
            return ReadResult(True, text, "PDF",
                              {"쪽": pages, "경고": "글자가 거의 없습니다 — 스캔본이면 OCR이 필요합니다"})
        return ReadResult(True, text, "PDF", {"쪽": pages})
    except Exception as e:
        return ReadResult(False, kind="PDF", error=str(e))


# ─────────────────────────────────────────────────────────────
# 구글 문서 바로가기 (.gdoc / .gsheet / .gslides)
# ─────────────────────────────────────────────────────────────
_G_KIND = {".gdoc": "구글문서", ".gsheet": "구글시트", ".gslides": "구글슬라이드"}


def read_gshortcut(path: str) -> ReadResult:
    """구글 드라이브 바로가기 파일에서 문서 ID/주소만 읽는다.

    구글 문서·시트·슬라이드는 '내 컴퓨터에 실체가 없는' 온라인 문서다.
    윈도우 드라이브 앱에서는 파일로 열리지 않는 경우가 많으므로(가상 파일),
    실제 내용은 Drive API 로 내보내야 한다(pkems_gdrive.export_google_doc).
    """
    ext = os.path.splitext(path)[1].lower()
    kind = _G_KIND.get(ext, "구글문서")
    guide = (f"> 구글 {kind}입니다. 내용이 온라인에만 있어 파일로는 읽을 수 없습니다.\n"
             f"> 코랩에서 'Drive API 내보내기'로 가져와야 합니다.")
    try:
        with io.open(path, encoding="utf-8") as f:
            info = json.load(f)
        doc_id = info.get("doc_id") or info.get("resource_id", "").split(":")[-1]
        url = info.get("url", "")
        return ReadResult(True, f"{guide}\n\n{url}", kind,
                          {"doc_id": doc_id, "url": url, "needs_api": True})
    except OSError:
        # 드라이브 앱의 가상 파일 — 열람 자체가 불가
        return ReadResult(True, guide, kind,
                          {"needs_api": True, "note": "가상 파일이라 로컬에서 열 수 없음"})
    except Exception as e:
        return ReadResult(False, kind=kind, error=str(e))


# ─────────────────────────────────────────────────────────────
# 등록표
# ─────────────────────────────────────────────────────────────
READERS = {
    ".hwp": read_hwp,
    ".hwpx": read_hwpx,
    ".docx": read_docx,
    ".pptx": read_pptx,
    ".xlsx": read_xlsx,
    ".xlsm": read_xlsx,
    ".csv": read_csv,
    ".tsv": read_csv,
    ".pdf": read_pdf,
    ".html": read_html,
    ".htm": read_html,
    ".txt": read_text,
    ".md": read_text,
    ".gdoc": read_gshortcut,
    ".gsheet": read_gshortcut,
    ".gslides": read_gshortcut,
}

SUPPORTED = sorted(READERS)


def sanitize(text: str) -> str:
    """파일로 저장할 수 없는 글자(짝 없는 서로게이트 등)를 걸러낸다."""
    if not text:
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-8", "ignore").decode("utf-8", "ignore")


def read_any(path: str) -> ReadResult:
    """확장자를 보고 알맞은 읽기 함수를 고른다."""
    ext = os.path.splitext(path)[1].lower()
    fn = READERS.get(ext)
    if fn is None:
        return ReadResult(False, kind=ext or "?", error="지원하지 않는 형식")
    if not os.path.exists(path):
        return ReadResult(False, kind=ext, error="파일이 없습니다")
    try:
        res = fn(path)
    except Exception as e:                       # 어떤 경우에도 죽지 않게
        return ReadResult(False, kind=ext, error=f"예기치 못한 오류: {e}")
    res.text = sanitize(res.text)
    return res
