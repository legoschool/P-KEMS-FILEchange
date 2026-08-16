# -*- coding: utf-8 -*-
"""
PKEMS 폴더 일괄 변환기
=======================
폴더 하나를 통째로 훑어서, 안에 있는 모든 문서를 마크다운(.md)으로 바꾼다.
한글(.hwp/.hwpx), 워드, 파워포인트, 엑셀, PDF, HTML, 텍스트를 모두 다룬다.

    from pkems_folder import FolderConverter, FolderSettings

    fc = FolderConverter(FolderSettings(
        src_dir = "/content/drive/MyDrive/01_학교",
        out_dir = "/content/drive/MyDrive/PKEMS/변환결과",
    ))
    fc.scan()      # 먼저 무엇이 몇 개 있는지 확인
    fc.run()       # 변환

특징
    - 원래 폴더 구조를 그대로 유지한다
    - 이미 변환한 파일은 건너뛴다 (중간에 끊겨도 이어서 진행)
    - 한 파일이 실패해도 전체가 멈추지 않는다 (오류는 따로 기록)
    - 변환 결과 목록(INDEX.md, _files.json)을 만든다

PKEMS(개인지식경험관리체계) 프로젝트
"""

from __future__ import annotations

import os
import io
import re
import json
import time
import collections
from dataclasses import dataclass, field

from pkems_readers import read_any, READERS, ReadResult
from pkems_privacy import PrivacyFilter, Policy, PrivacyReport


# ─────────────────────────────────────────────────────────────
@dataclass
class FolderSettings:
    src_dir: str                                   # 훑을 원본 폴더
    out_dir: str = ""                              # 결과 폴더 (비우면 src_dir/_md)
    include: tuple = tuple(READERS)                # 다룰 확장자
    exclude_dirs: tuple = (".git", ".obsidian", "__pycache__",
                           "node_modules", "images", "_md")
    skip_existing: bool = True                     # 이미 있는 md 는 건너뛰기
    min_chars: int = 10                            # 이보다 짧으면 '내용 없음'으로 기록
    max_mb: float = 200.0                          # 이보다 큰 파일은 건너뛰기
    keep_tree: bool = True                         # 원본 폴더 구조 유지
    verbose: bool = True

    # ── 개인정보 처리 ──
    개인정보_가리기: bool = True                    # 끄면 원문 그대로 저장
    개인정보_정책: Policy = field(default_factory=Policy)
    보고서_원본표시: bool = True                    # 보고서에 가리기 전 값을 남길지

    def resolved_out(self) -> str:
        return self.out_dir or os.path.join(self.src_dir, "_md")


_INVALID = re.compile(r'[\\/:*?"<>|]')


def safe_name(name: str, maxlen: int = 90) -> str:
    s = _INVALID.sub("_", name).strip()
    return s[:maxlen].rstrip(" .") or "무제"


# ─────────────────────────────────────────────────────────────
class FolderConverter:
    def __init__(self, settings: FolderSettings):
        self.s = settings
        self.out = settings.resolved_out()
        self.records: list[dict] = []
        self.errors: list[dict] = []
        self.stats = collections.Counter()
        self.privacy = PrivacyFilter(settings.개인정보_정책) \
            if settings.개인정보_가리기 else None
        self.report = PrivacyReport(show_original=settings.보고서_원본표시)

    def log(self, *a):
        if self.s.verbose:
            print(*a, flush=True)

    # ── 대상 파일 수집
    def collect(self) -> list[str]:
        found = []
        exts = {e.lower() for e in self.s.include}
        out_abs = os.path.abspath(self.out)
        for dp, dns, fns in os.walk(self.s.src_dir):
            dns[:] = [d for d in dns
                      if d not in self.s.exclude_dirs and not d.startswith(".")]
            if os.path.abspath(dp).startswith(out_abs):
                continue                                   # 결과 폴더는 제외
            for fn in fns:
                if fn.startswith("~$") or fn.startswith("."):
                    continue
                if os.path.splitext(fn)[1].lower() in exts:
                    found.append(os.path.join(dp, fn))
        return sorted(found)

    # ── 훑어보기 (변환 없이 현황만)
    def scan(self) -> dict:
        files = self.collect()
        by_ext = collections.Counter(os.path.splitext(f)[1].lower() for f in files)
        total_mb = 0.0
        for f in files:
            try:
                total_mb += os.path.getsize(f) / 1024 / 1024
            except OSError:
                pass

        self.log(f"대상 폴더 : {self.s.src_dir}")
        self.log(f"찾은 파일 : {len(files)}개 ({total_mb:.0f} MB)\n")
        self.log("  형식별")
        for e, n in by_ext.most_common():
            self.log(f"    {e:8} {n:5}개")
        self.log(f"\n  저장 위치 : {self.out}")
        return {"files": len(files), "by_ext": dict(by_ext), "mb": round(total_mb)}

    # ── 결과 md 경로 정하기
    def md_path_for(self, src: str) -> str:
        rel = os.path.relpath(src, self.s.src_dir)
        head, fn = os.path.split(rel)
        stem, ext = os.path.splitext(fn)
        name = safe_name(f"{stem}{ext.replace('.', '_')}") + ".md"
        if self.s.keep_tree and head and head != ".":
            head = os.path.join(*[safe_name(p) for p in head.split(os.sep)])
            return os.path.join(self.out, head, name)
        return os.path.join(self.out, name)

    # ── 머리말 만들기
    def front_matter(self, src: str, res: ReadResult) -> str:
        st = os.stat(src)
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        rel = os.path.relpath(src, self.s.src_dir).replace("\\", "/")
        title = os.path.splitext(os.path.basename(src))[0].replace('"', "'")
        extra = ""
        if res.meta:
            bits = " · ".join(f"{k} {v}" for k, v in res.meta.items()
                              if k not in ("url", "doc_id"))
            if bits:
                extra = f"info: {bits}\n"
        return (
            "---\n"
            f'title: "{title}"\n'
            f"date: {mtime}\n"
            f'source: "{rel}"\n'
            f'kind: "{res.kind}"\n'
            f"{extra}"
            "---\n\n"
            f"# {title}\n\n"
            f"*원본: `{rel}` · 수정 {mtime}*\n\n"
        )

    # ── 실행
    def run(self, limit: int | None = None) -> dict:
        t0 = time.time()
        os.makedirs(self.out, exist_ok=True)
        files = self.collect()
        if limit:
            files = files[:limit]
        total = len(files)
        self.log(f"파일 {total}개를 변환합니다.\n")

        done = skipped = failed = 0
        for i, src in enumerate(files, 1):
            dst = self.md_path_for(src)

            if self.s.skip_existing and os.path.exists(dst):
                skipped += 1
                continue
            try:
                mb = os.path.getsize(src) / 1024 / 1024
            except OSError:
                mb = 0
            if mb > self.s.max_mb:
                self.errors.append({"file": src, "error": f"너무 큼 ({mb:.0f}MB)"})
                failed += 1
                continue

            res = read_any(src)
            if not res.ok:
                self.errors.append({"file": src, "error": res.error})
                self.stats[f"실패:{res.kind}"] += 1
                failed += 1
                continue

            body = res.text
            rel = os.path.relpath(src, self.s.src_dir).replace("\\", "/")

            개인정보_건수 = 0
            if self.privacy is not None:
                body, hits = self.privacy.mask(body)
                if hits:
                    self.report.add(rel, hits)
                    개인정보_건수 = len(hits)
                    self.stats["개인정보가림"] += len(hits)

            note = ""
            if len(body) < self.s.min_chars:
                note = ("\n> ⚠ 글자를 거의 찾지 못했습니다. 그림 위주이거나 스캔한 문서일 수 "
                        "있습니다. 이 도구는 글자 인식(OCR)을 하지 않습니다.\n")
                self.stats["내용거의없음"] += 1

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with io.open(dst, "w", encoding="utf-8") as f:
                f.write(self.front_matter(src, res) + note + body + "\n")

            self.records.append({
                "title": os.path.splitext(os.path.basename(src))[0],
                "kind": res.kind,
                "chars": res.chars,
                "src": rel,
                "md": os.path.relpath(dst, self.out).replace("\\", "/"),
                "date": time.strftime("%Y-%m-%d", time.localtime(os.stat(src).st_mtime)),
                "개인정보": 개인정보_건수,
            })
            self.stats[res.kind] += 1
            done += 1

            if done and done % 50 == 0:
                self.log(f"  … {done}개 변환 ({i}/{total})")

        self.write_index()
        보고서 = self.report.write(self.out) if self.privacy is not None else None

        secs = int(time.time() - t0)
        self.log(f"\n완료! 변환 {done}개 · 건너뜀 {skipped}개 · 실패 {failed}개 "
                 f"· {secs // 60}분 {secs % 60}초")
        if self.stats:
            self.log("\n  형식별 결과")
            for k, n in self.stats.most_common():
                self.log(f"    {k:14} {n:5}개")
        if self.errors:
            self.log(f"\n  실패 목록 : {os.path.join(self.out, '_오류.md')}")

        if self.privacy is not None:
            self.log("\n" + "─" * 46)
            self.log(self.report.summary())
            if 보고서:
                self.log(f"\n  자세한 내역 : {보고서}")
                if self.s.보고서_원본표시:
                    self.log("  ⚠ 이 보고서에는 가리기 전 원본이 들어 있습니다. 공유하지 마세요.")

        return {"done": done, "skipped": skipped, "failed": failed,
                "stats": dict(self.stats),
                "개인정보": len(self.report.rows)}

    # ── 목차/오류 기록
    def write_index(self):
        if self.records:
            with io.open(os.path.join(self.out, "_files.json"), "w", encoding="utf-8") as f:
                json.dump(self.records, f, ensure_ascii=False, indent=1)

            by_kind = collections.defaultdict(list)
            for r in self.records:
                by_kind[r["kind"]].append(r)

            L = ["# 📂 변환된 문서 목차", "",
                 f"전체 **{len(self.records)}개** · 자동 생성", "",
                 "| 형식 | 개수 |", "|------|-----:|"]
            for k in sorted(by_kind, key=lambda x: -len(by_kind[x])):
                L.append(f"| {k} | {len(by_kind[k])} |")
            L.append("")
            for k in sorted(by_kind, key=lambda x: -len(by_kind[x])):
                L += [f"## {k} ({len(by_kind[k])}개)", ""]
                for r in sorted(by_kind[k], key=lambda x: x["src"]):
                    L.append(f"- [{r['title']}]({r['md']}) "
                             f"· {r['chars']:,}자 · `{r['src']}`")
                L.append("")
            with io.open(os.path.join(self.out, "INDEX.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(L))

        if self.errors:
            L = ["# ⚠ 변환하지 못한 파일", "",
                 f"{len(self.errors)}개", "",
                 "| 파일 | 이유와 해결 방법 |", "|------|------------------|"]
            for e in self.errors:
                name = os.path.basename(e["file"]).replace("|", "／")
                L.append(f"| {name} | {e['error'].replace('|', '／')} |")
            L += ["", "---", "",
                  "### 자주 나오는 경우", "",
                  "**옛 오피스 형식(`.xls` `.ppt` `.doc`)**",
                  "해당 프로그램에서 열어 **다른 이름으로 저장** → "
                  "`.xlsx` `.pptx` `.docx` 로 바꾼 뒤 다시 변환하세요.",
                  "확장자만 바꿔 쓴 파일도 같은 오류가 납니다.",
                  "",
                  "**글자가 없는 PDF**",
                  "종이를 스캔하거나 사진으로 만든 문서입니다. "
                  "이 도구는 **글자 인식(OCR)을 하지 않으므로** 변환할 수 없습니다.",
                  "원본 문서 파일이 있으면 그것을 변환하세요."]
            with io.open(os.path.join(self.out, "_오류.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(L))
