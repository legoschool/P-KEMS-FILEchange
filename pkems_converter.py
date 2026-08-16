# -*- coding: utf-8 -*-
"""
PKEMS 블로그 PDF -> 마크다운 변환 엔진
=========================================
네이버 블로그 백업 PDF(전체보기 인쇄본)를 AI가 읽기 좋은 .md 파일로 변환합니다.

특정 블로그에 종속되지 않도록, PDF 안에서 블로그 주소/푸터 형식을 '자동 감지'합니다.

사용 예:
    from pkems_converter import Converter, Settings

    conv = Converter(Settings(
        pdf_dir  = "/content/drive/MyDrive/블로그백업",
        out_dir  = "/content/drive/MyDrive/블로그백업/md",
        extract_images = True,
    ))
    conv.run()

만든 이: 이운희 · PKEMS(개인지식경험관리체계) 프로젝트
"""

from __future__ import annotations

import os
import io
import re
import json
import time
import collections
from dataclasses import dataclass, field, asdict

try:
    import pymupdf  # PyMuPDF >= 1.24
except ImportError:  # 구버전 호환
    import fitz as pymupdf


# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────
@dataclass
class Settings:
    pdf_dir: str                          # PDF들이 들어있는 폴더
    out_dir: str = ""                     # 결과 md 폴더 (비우면 pdf_dir/md)
    extract_images: bool = True           # 본문 이미지 추출 여부
    min_image_bytes: int = 8000           # 이 크기 미만은 아이콘으로 보고 제외
    image_subdir: str = "images"          # 이미지 저장 하위 폴더명
    skip_existing: bool = True            # 이미 변환된 글은 건너뛰기
    filename_pattern: str = "{date}_{title}"   # md 파일 이름 형식
    max_title_len: int = 80               # 파일명에 쓸 제목 최대 길이
    write_index: bool = True              # _index.json / INDEX.md 생성
    verbose: bool = True

    def resolved_out(self) -> str:
        return self.out_dir or os.path.join(self.pdf_dir, "md")


# ─────────────────────────────────────────────────────────────
# 자동 감지 패턴
# ─────────────────────────────────────────────────────────────
# 글머리: "2015/05/06 20:21" 형태
DATE_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})\s+(\d{1,2}:\d{2})\s*$")
# 네이버 블로그 주소 (아이디 무관)
URL_RE = re.compile(r"^https?://(?:m\.)?blog\.naver\.com/([A-Za-z0-9_.-]+)/(\d+)\s*$")
# 페이지 푸터: "12 · 블로그이름"  (블로그 이름은 자동 감지)
FOOTER_TAIL_RE = re.compile(r"^\d+\s*[·|ㆍ・]\s*(.+?)\s*$")


def detect_blog_name(doc, sample_pages: int = 40) -> str | None:
    """페이지 하단에 반복되는 '숫자 · 블로그명' 에서 블로그명을 찾아낸다."""
    counter = collections.Counter()
    total = min(doc.page_count, sample_pages)
    for i in range(total):
        lines = [l.strip() for l in doc[i].get_text().split("\n") if l.strip()]
        for l in lines[-3:]:                      # 페이지 끝 3줄만 확인
            m = FOOTER_TAIL_RE.match(l)
            if m:
                counter[m.group(1)] += 1
    if not counter:
        return None
    name, hits = counter.most_common(1)[0]
    # 표본 페이지의 절반 이상에서 반복되어야 진짜 푸터로 인정
    return name if hits >= max(3, total // 2) else None


def make_footer_re(blog_name: str | None):
    if not blog_name:
        return None
    return re.compile(r"^\d+\s*[·|ㆍ・]\s*" + re.escape(blog_name) + r"\s*$")


# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────
_INVALID = re.compile(r'[\\/:*?"<>|#\[\]]')


def slugify(date: str, title: str, pattern: str, maxlen: int) -> str:
    t = _INVALID.sub("", title.strip())
    t = re.sub(r"\s+", "_", t).strip("._")
    if len(t) > maxlen:
        t = t[:maxlen].rstrip("._")
    if not t:
        t = "제목없음"
    return pattern.format(date=date, title=t)


# ─────────────────────────────────────────────────────────────
# 변환기
# ─────────────────────────────────────────────────────────────
class Converter:
    def __init__(self, settings: Settings):
        self.s = settings
        self.out = settings.resolved_out()
        self.imgroot = os.path.join(self.out, settings.image_subdir)
        self.index: list[dict] = []
        self.known: set[str] = set()
        self.stats = collections.Counter()

    # ── 로그
    def log(self, *a):
        if self.s.verbose:
            print(*a, flush=True)

    # ── 기존 결과 이어받기
    def load_index(self):
        path = os.path.join(self.out, "_index.json")
        if os.path.exists(path):
            try:
                with io.open(path, encoding="utf-8") as f:
                    self.index = json.load(f)
                self.known = {e["url"].rsplit("/", 1)[-1] for e in self.index if e.get("url")}
                self.log(f"  기존 변환본 {len(self.index)}편을 인식했습니다 (이어서 진행)")
            except Exception:
                self.index, self.known = [], set()

    # ── PDF 한 개 파싱
    def parse_pdf(self, path: str) -> list[dict]:
        doc = pymupdf.open(path)
        blog_name = detect_blog_name(doc)
        footer_re = make_footer_re(blog_name)
        if blog_name:
            self.log(f"  블로그명 자동 감지: '{blog_name}'")

        posts, cur = [], None
        for pno in range(doc.page_count):
            lines = [l.rstrip() for l in doc[pno].get_text().split("\n")]
            if footer_re:
                lines = [l for l in lines if not footer_re.match(l.strip())]

            started = False
            for i, raw in enumerate(lines):
                m = DATE_RE.match(raw.strip())
                if not m or i + 1 >= len(lines):
                    continue
                um = URL_RE.match(lines[i + 1].strip())
                if not um:
                    continue

                y, mo, d, tm = m.groups()
                rest = [l for l in lines[i + 2:] if l.strip()]
                title = rest[0].strip() if rest else "제목없음"

                # 네이버 PDF는 제목/카테고리가 각각 2번씩 반복되는 경우가 많다
                j = 1
                if j < len(rest) and rest[j].strip() == title:
                    j += 1
                category = rest[j].strip() if j < len(rest) else ""
                if j + 1 < len(rest) and rest[j + 1].strip() == category:
                    j += 2
                else:
                    j += 1

                cur = {
                    "date": f"{y}-{mo}-{d}",
                    "time": tm if len(tm) == 5 else "0" + tm,
                    "title": title,
                    "category": category,
                    "blog_id": um.group(1),
                    "postid": um.group(2),
                    "url": f"http://blog.naver.com/{um.group(1)}/{um.group(2)}",
                    "body": list(rest[j:]),
                    "pages": [pno],
                    "src": os.path.basename(path),
                    "startpage": pno + 1,
                }
                posts.append(cur)
                started = True
                break

            if not started and cur is not None:
                cur["body"].extend([l for l in lines if l.strip()])
                cur["pages"].append(pno)

        for p in posts:
            p["endpage"] = max(p["pages"]) + 1
        doc.close()
        return posts

    # ── 이미지 추출
    def extract_images(self, pdfpath: str, post: dict, outdir: str) -> list[str]:
        if not self.s.extract_images:
            return []
        doc = pymupdf.open(pdfpath)
        saved, seen, n = [], set(), 0
        for pno in post["pages"]:
            for info in doc[pno].get_images(full=True):
                xref = info[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                data, ext = base["image"], base["ext"]
                if len(data) < self.s.min_image_bytes:
                    continue
                n += 1
                fn = f"img_{n:03d}.{ext}"
                os.makedirs(outdir, exist_ok=True)
                with open(os.path.join(outdir, fn), "wb") as f:
                    f.write(data)
                saved.append(fn)
        doc.close()
        return saved

    # ── 마크다운 본문 생성
    def render_md(self, post: dict, slug: str, images: list[str]) -> str:
        L = [
            "---",
            f'title: "{post["title"]}"',
            f'date: {post["date"]} {post["time"]}',
            f'source: {post["src"]} (p.{post["startpage"]}-{post["endpage"]})',
            f'category: "{post["category"]}"',
            f'url: {post["url"]}',
            "---",
            "",
            f'# {post["title"]}',
            "",
            f'*{post["date"]} {post["time"]}*',
            "",
            f'원문: {post["url"]}',
            "",
        ]
        for line in post["body"]:
            s = line.strip()
            if s:
                L += [s, ""]
        for im in images:
            L += [f"![]({self.s.image_subdir}/{slug}/{im})", ""]
        return "\n".join(L)

    # ── 전체 실행
    def run(self, pdf_names: list[str] | None = None) -> dict:
        t0 = time.time()
        os.makedirs(self.out, exist_ok=True)
        self.load_index()

        if pdf_names is None:
            pdf_names = sorted(
                n for n in os.listdir(self.s.pdf_dir) if n.lower().endswith(".pdf")
            )
        if not pdf_names:
            self.log("PDF 파일을 찾지 못했습니다. pdf_dir 경로를 확인하세요.")
            return {"added": 0, "total": len(self.index)}

        self.log(f"PDF {len(pdf_names)}개를 변환합니다.\n")
        added = 0

        for name in pdf_names:
            path = os.path.join(self.s.pdf_dir, name)
            self.log(f"[{name}]")
            try:
                posts = self.parse_pdf(path)
            except Exception as e:
                self.log(f"  !! 읽기 실패: {e}")
                self.stats["실패PDF"] += 1
                continue

            self.log(f"  글 {len(posts)}편 발견")
            if not posts:
                self.log("  (글머리 패턴을 찾지 못했습니다 — 네이버 블로그 백업 PDF가 맞는지 확인하세요)")

            for post in posts:
                if self.s.skip_existing and post["postid"] in self.known:
                    self.stats["중복건너뜀"] += 1
                    continue
                slug = slugify(post["date"], post["title"],
                               self.s.filename_pattern, self.s.max_title_len)
                mdpath = os.path.join(self.out, slug + ".md")
                if self.s.skip_existing and os.path.exists(mdpath):
                    self.known.add(post["postid"])
                    self.stats["중복건너뜀"] += 1
                    continue

                imgs = self.extract_images(path, post, os.path.join(self.imgroot, slug))
                with io.open(mdpath, "w", encoding="utf-8") as f:
                    f.write(self.render_md(post, slug, imgs))

                self.index.append({
                    "date": post["date"], "time": post["time"],
                    "title": post["title"], "category": post["category"],
                    "file": slug + ".md", "images": len(imgs),
                    "pages": len(post["pages"]), "src": post["src"],
                    "url": post["url"],
                })
                self.known.add(post["postid"])
                added += 1
                self.stats["이미지"] += len(imgs)
                if added % 25 == 0:
                    self.log(f"    … {added}편 변환")
            self.log("")

        self.index.sort(key=lambda e: (e.get("date", ""), e.get("time", "")))
        if self.s.write_index:
            self.write_index_files()

        secs = int(time.time() - t0)
        self.log(f"완료! 새로 변환 {added}편 / 전체 {len(self.index)}편 "
                 f"/ 이미지 {self.stats['이미지']}장 / 중복 건너뜀 {self.stats['중복건너뜀']}편 "
                 f"/ {secs // 60}분 {secs % 60}초")
        return {"added": added, "total": len(self.index), "stats": dict(self.stats)}

    # ── 목차 파일
    def write_index_files(self):
        with io.open(os.path.join(self.out, "_index.json"), "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=1)

        by_year = collections.defaultdict(list)
        for e in self.index:
            by_year[e["date"][:4]].append(e)

        L = ["# 📚 블로그 기록 목차", "",
             f"전체 **{len(self.index)}편** · 자동 생성", ""]
        L += ["| 연도 | 편수 |", "|------|-----:|"]
        for y in sorted(by_year, reverse=True):
            L.append(f"| {y} | {len(by_year[y])} |")
        L.append("")
        for y in sorted(by_year, reverse=True):
            L += [f"## {y}년 ({len(by_year[y])}편)", ""]
            for e in sorted(by_year[y], key=lambda x: x["date"], reverse=True):
                cat = f" · {e['category']}" if e.get("category") else ""
                L.append(f"- {e['date']} · [{e['title']}]({e['file']}){cat}")
            L.append("")
        with io.open(os.path.join(self.out, "INDEX.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(L))


# ─────────────────────────────────────────────────────────────
# 진단 도구 — 변환 전에 PDF가 맞는 형식인지 미리 확인
# ─────────────────────────────────────────────────────────────
def inspect(pdf_path: str, show: int = 5) -> dict:
    """변환하지 않고 PDF 구조만 훑어본다."""
    doc = pymupdf.open(pdf_path)
    pages = doc.page_count
    name = detect_blog_name(doc)
    footer_re = make_footer_re(name)
    found = []
    for pno in range(doc.page_count):
        lines = [l.rstrip() for l in doc[pno].get_text().split("\n")]
        if footer_re:
            lines = [l for l in lines if not footer_re.match(l.strip())]
        for i, raw in enumerate(lines[:-1]):
            if DATE_RE.match(raw.strip()) and URL_RE.match(lines[i + 1].strip()):
                rest = [l for l in lines[i + 2:] if l.strip()]
                found.append((pno + 1, raw.strip(), rest[0] if rest else ""))
                break
    print(f"파일       : {os.path.basename(pdf_path)}")
    print(f"페이지     : {pages}")
    print(f"블로그명   : {name or '(감지 실패)'}")
    print(f"발견한 글  : {len(found)}편")
    if found:
        print("\n  앞부분 미리보기")
        for p, d, t in found[:show]:
            print(f"   p.{p:<4} {d}  {t[:40]}")
    else:
        print("\n  ⚠ 글머리(날짜+주소)를 찾지 못했습니다.")
        print("    네이버 블로그 '전체보기 → 인쇄 → PDF로 저장' 방식의 백업본인지 확인하세요.")
    doc.close()
    return {"pages": pages, "blog": name, "posts": len(found)}
