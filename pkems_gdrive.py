# -*- coding: utf-8 -*-
"""
PKEMS 구글 문서 가져오기
=========================
구글 문서·시트·슬라이드는 '내 컴퓨터에 실체가 없는' 온라인 문서라서
파일로는 읽을 수 없다. Drive API 로 내보내기(export) 해야 한다.

코랩에서 쓰는 것을 전제로 한다 (별도 인증 설정 없이 본인 계정으로 동작).

    from pkems_gdrive import GoogleDocs

    g = GoogleDocs()                     # 인증
    g.list_folder("1AbC...")             # 폴더 안 구글 문서 목록
    g.export_folder("1AbC...", "/content/drive/MyDrive/PKEMS/구글문서")

PKEMS(개인지식경험관리체계) 프로젝트
"""

from __future__ import annotations

import os
import io
import re
import json
import time

# 구글 문서 종류 -> 내보낼 형식
EXPORT_AS = {
    "application/vnd.google-apps.document":     ("text/markdown", ".md", "구글문서"),
    "application/vnd.google-apps.spreadsheet":  ("text/csv", ".csv", "구글시트"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt", "구글슬라이드"),
}

FOLDER_MIME = "application/vnd.google-apps.folder"

_INVALID = re.compile(r'[\\/:*?"<>|]')


def safe_name(name: str, maxlen: int = 90) -> str:
    return (_INVALID.sub("_", name).strip()[:maxlen].rstrip(" .")) or "무제"


def folder_id_from(text: str) -> str | None:
    """폴더 링크 또는 ID 문자열에서 ID만 뽑아낸다."""
    text = (text or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", text)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Za-z0-9_-]{10,})$", text)
    return m.group(1) if m else None


class GoogleDocs:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.svc = self._connect()

    def log(self, *a):
        if self.verbose:
            print(*a, flush=True)

    def _connect(self):
        try:
            from google.colab import auth
            auth.authenticate_user()
        except ImportError:
            pass                       # 코랩이 아니면 기본 자격증명을 사용
        from googleapiclient.discovery import build
        return build("drive", "v3")

    # ── 폴더 안 항목 나열 (하위 폴더까지)
    def walk(self, folder_id: str, recursive: bool = True,
             _prefix: str = "") -> list[dict]:
        items, token = [], None
        while True:
            resp = self.svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id,name,mimeType,modifiedTime)",
                pageSize=200, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                f["path"] = os.path.join(_prefix, safe_name(f["name"]))
                if f["mimeType"] == FOLDER_MIME:
                    if recursive:
                        items += self.walk(f["id"], True, f["path"])
                else:
                    items.append(f)
            token = resp.get("nextPageToken")
            if not token:
                break
        return items

    # ── 목록 보기
    def list_folder(self, folder_or_link: str, recursive: bool = True) -> list[dict]:
        fid = folder_id_from(folder_or_link)
        if not fid:
            self.log("폴더 링크 또는 ID 형식이 아닙니다.")
            return []
        files = self.walk(fid, recursive)
        google = [f for f in files if f["mimeType"] in EXPORT_AS]
        other = [f for f in files if f["mimeType"] not in EXPORT_AS]

        self.log(f"전체 {len(files)}개")
        self.log(f"  가져올 수 있는 구글 문서 : {len(google)}개")
        counts = {}
        for f in google:
            k = EXPORT_AS[f["mimeType"]][2]
            counts[k] = counts.get(k, 0) + 1
        for k, n in counts.items():
            self.log(f"     {k:12} {n}개")
        self.log(f"  일반 파일(별도 변환 필요) : {len(other)}개")
        return google

    # ── 한 개 내보내기
    def export_one(self, file_id: str, mime: str, dst_noext: str) -> str | None:
        target, ext, _kind = EXPORT_AS[mime]
        try:
            data = self.svc.files().export(fileId=file_id, mimeType=target).execute()
        except Exception as e:
            # 마크다운 내보내기를 지원하지 않으면 일반 텍스트로 후퇴
            if target == "text/markdown":
                try:
                    data = self.svc.files().export(fileId=file_id,
                                                   mimeType="text/plain").execute()
                    ext = ".md"
                except Exception as e2:
                    raise e2
            else:
                raise e
        path = dst_noext + ext
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return path

    # ── 폴더 통째로 내보내기
    def export_folder(self, folder_or_link: str, out_dir: str,
                      recursive: bool = True, skip_existing: bool = True) -> dict:
        t0 = time.time()
        files = self.list_folder(folder_or_link, recursive)
        if not files:
            return {"done": 0, "failed": 0}

        self.log(f"\n{out_dir} 로 가져옵니다.\n")
        os.makedirs(out_dir, exist_ok=True)
        done = skipped = failed = 0
        records, errors = [], []

        for i, f in enumerate(files, 1):
            kind = EXPORT_AS[f["mimeType"]][2]
            dst_noext = os.path.join(out_dir, f["path"])
            guess = dst_noext + EXPORT_AS[f["mimeType"]][1]
            if skip_existing and os.path.exists(guess):
                skipped += 1
                continue
            try:
                path = self.export_one(f["id"], f["mimeType"], dst_noext)
                self._add_front_matter(path, f, kind)
                records.append({"title": f["name"], "kind": kind,
                                "file": os.path.relpath(path, out_dir).replace("\\", "/"),
                                "date": f.get("modifiedTime", "")[:10],
                                "url": f"https://drive.google.com/open?id={f['id']}"})
                done += 1
            except Exception as e:
                errors.append({"name": f["name"], "error": str(e)[:200]})
                failed += 1
            if done and done % 20 == 0:
                self.log(f"  … {done}개 ({i}/{len(files)})")

        self._write_index(out_dir, records, errors)
        secs = int(time.time() - t0)
        self.log(f"\n완료! 가져옴 {done}개 · 건너뜀 {skipped}개 · 실패 {failed}개 "
                 f"· {secs // 60}분 {secs % 60}초")
        return {"done": done, "skipped": skipped, "failed": failed}

    # ── 내보낸 파일 앞에 정보 붙이기
    def _add_front_matter(self, path: str, f: dict, kind: str):
        if not path.endswith(".md"):
            return                                   # csv/txt 는 건드리지 않는다
        try:
            with io.open(path, encoding="utf-8") as fh:
                body = fh.read()
        except Exception:
            return
        title = f["name"].replace('"', "'")
        head = ("---\n"
                f'title: "{title}"\n'
                f'date: {f.get("modifiedTime","")[:10]}\n'
                f'kind: "{kind}"\n'
                f'url: https://drive.google.com/open?id={f["id"]}\n'
                "---\n\n"
                f"# {title}\n\n")
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(head + body)

    def _write_index(self, out_dir: str, records: list, errors: list):
        if records:
            with io.open(os.path.join(out_dir, "_files.json"), "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=1)
            L = ["# 📄 가져온 구글 문서", "", f"전체 **{len(records)}개**", ""]
            for r in sorted(records, key=lambda x: x["file"]):
                L.append(f"- [{r['title']}]({r['file']}) · {r['kind']} · {r['date']}")
            with io.open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(L))
        if errors:
            L = ["# ⚠ 가져오지 못한 문서", "", "| 문서 | 이유 |", "|------|------|"]
            for e in errors:
                L.append(f"| {e['name'].replace('|','／')} | {e['error']} |")
            with io.open(os.path.join(out_dir, "_오류.md"), "w", encoding="utf-8") as f:
                f.write("\n".join(L))
