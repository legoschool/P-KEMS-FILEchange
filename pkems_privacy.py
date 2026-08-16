# -*- coding: utf-8 -*-
"""
PKEMS 개인정보 자동 필터
=========================
문서에서 개인정보를 찾아내 원하는 방식으로 가린다.

    from pkems_privacy import PrivacyFilter, Policy

    pf = PrivacyFilter()                      # 기본 정책
    masked, hits = pf.mask(text)

    pf = PrivacyFilter(Policy(이름="그대로", 전화번호="삭제"))   # 정책 바꾸기

가릴 수 있는 것
    주민등록번호  전화번호  이메일  계좌번호  카드번호
    이름  주소  생년월일  차량번호

가리는 방식(모드)
    "부분가림"  일부만 남긴다   이운희 -> 이**  ·  010-1234-5678 -> 010-****-****
    "가림"      전부 가린다     900101-1234567 -> **************
    "삭제"      아예 지운다
    "그대로"    건드리지 않는다

변환 뒤에는 "무엇이 어디서 어떻게 바뀌었는지" 보고서를 만들 수 있다.

⚠️ 자동 탐지는 완벽하지 않다. 특히 사람 이름은 놓치거나 잘못 잡을 수 있으므로,
   공개 전에는 반드시 사람이 최종 확인해야 한다.

PKEMS(개인지식경험관리체계) 프로젝트
"""

from __future__ import annotations

import re
import os
import io
import json
import collections
from dataclasses import dataclass, field, asdict


# ─────────────────────────────────────────────────────────────
# 정책
# ─────────────────────────────────────────────────────────────
MODES = ("부분가림", "가림", "삭제", "그대로")


@dataclass
class Policy:
    """개인정보 종류별로 어떻게 처리할지 정한다."""
    주민등록번호: str = "가림"
    전화번호: str = "부분가림"
    이메일: str = "부분가림"
    계좌번호: str = "가림"          # 금융정보는 기본을 '전부 가림'으로 둔다
    카드번호: str = "가림"
    이름: str = "부분가림"
    주소: str = "부분가림"
    생년월일: str = "부분가림"
    차량번호: str = "부분가림"

    # 이름 탐지 강도
    #   "라벨만"   성명/강사/담당자 같은 표시 옆에 있는 이름만 (오탐 적음, 놓칠 수 있음)
    #   "보통"     라벨 + 흔한 성씨로 시작하는 2~3글자 (권장)
    #   "적극적"   보통 + 문장 속 이름까지 (오탐 늘어남)
    이름_탐지강도: str = "보통"

    def mode_for(self, kind: str) -> str:
        return getattr(self, kind, "그대로")


# ─────────────────────────────────────────────────────────────
# 탐지 결과
# ─────────────────────────────────────────────────────────────
@dataclass
class Hit:
    kind: str
    original: str
    masked: str
    start: int
    end: int
    context: str = ""
    confidence: str = "보통"      # 확실 / 보통 / 낮음


# ─────────────────────────────────────────────────────────────
# 가리기 도우미
# ─────────────────────────────────────────────────────────────
def _stars(n: int) -> str:
    return "*" * max(n, 1)


def mask_rrn(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "부분가림":                       # 900101-*******
        head = s.split("-")[0] if "-" in s else s[:6]
        return f"{head}-*******"
    return _stars(len(s.replace("-", ""))) if "-" not in s else "******-*******"


def mask_phone(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    digits = re.sub(r"\D", "", s)
    if mode == "가림":
        return _stars(len(digits))
    # 부분가림 — 앞자리(010, 02, 지역번호)만 남긴다
    if digits.startswith("02"):
        head, rest = "02", digits[2:]
    elif len(digits) >= 10:
        head, rest = digits[:3], digits[3:]
    else:
        head, rest = digits[:3], digits[3:]
    if len(rest) >= 8:
        return f"{head}-****-****"
    return f"{head}-***-****"


def mask_email(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "가림":
        return _stars(len(s))
    user, _, domain = s.partition("@")
    keep = user[:2] if len(user) > 2 else user[:1]
    return f"{keep}{_stars(max(len(user) - len(keep), 3))}@{domain}"


def mask_account(s: str, mode: str) -> str:
    """계좌번호는 원래 모양(-)을 살리고 끝 3자리만 남긴다.
    352-1234-5678-93 -> ***-****-****-93"""
    if mode == "삭제":
        return ""
    if mode == "가림":
        return "".join("*" if c.isdigit() else c for c in s)
    out, kept = [], 0
    for c in reversed(s):
        if c.isdigit() and kept < 3:
            out.append(c)
            kept += 1
        elif c.isdigit():
            out.append("*")
        else:
            out.append(c)
    return "".join(reversed(out))


def mask_card(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "부분가림":
        digits = re.sub(r"\D", "", s)
        return f"****-****-****-{digits[-4:]}"
    return "****-****-****-****"


def mask_name(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "가림":
        return _stars(len(s))
    # 부분가림 — 성만 남긴다.  이운희 -> 이**   남궁민수 -> 남궁**
    surname_len = 2 if s[:2] in COMPOUND_SURNAMES else 1
    return s[:surname_len] + _stars(len(s) - surname_len)


def mask_address(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "가림":
        return _stars(len(s))
    # 부분가림 — 시/군/구 까지만 남기고 상세주소를 가린다
    m = re.match(r"^(.*?[시군구])\s", s + " ")
    return (m.group(1) + " ****") if m else s[:6] + " ****"


def mask_birth(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "가림":
        return _stars(len(s))
    m = re.match(r"^(\d{4})", s)
    return f"{m.group(1)}년 **월 **일" if m else _stars(len(s))


def mask_car(s: str, mode: str) -> str:
    if mode == "삭제":
        return ""
    if mode == "가림":
        return _stars(len(s))
    return re.sub(r"\d{4}$", "****", s)


# ─────────────────────────────────────────────────────────────
# 한국 성씨 (흔한 것 위주)
# ─────────────────────────────────────────────────────────────
SURNAMES = set(
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구"
    "민진지엄채원천방공현함변염양변여추도소석선설마길위표명기반라왕금옥육인맹제모장남"
)
COMPOUND_SURNAMES = {"남궁", "황보", "제갈", "사공", "선우", "서문", "독고", "동방"}

# 이름으로 오해하기 쉬운 낱말 (오탐 방지)
NAME_STOPWORDS = {
    "김치", "이번", "이것", "이후", "이전", "이상", "이하", "이내", "이때", "이런", "이날",
    "정도", "정리", "정보", "조사", "조치", "장소", "장면", "한국", "한번", "한글", "한다",
    "고등", "고민", "문의", "문제", "양식", "양성", "손님", "백지", "허용", "유지", "유의",
    "남녀", "심리", "하나", "하기", "성적", "성장", "성과", "차이", "차시", "주요", "주제",
    "우리", "구성", "구분", "민원", "진행", "지도", "지원", "엄격", "원인", "원격", "천천",
    "방법", "방과", "공고", "공유", "현재", "현장", "함께", "변경", "여기", "추가", "도움",
    "소개", "석식", "선택", "설명", "마련", "길이", "위해", "위한", "표시", "명단", "기록",
    "반드", "라도", "왕성", "금지", "옥상", "육성", "인원", "제출", "제작", "모두", "모집",
    "장기", "박수", "최고", "최종", "강사", "강의", "학생", "교사", "학교", "교육", "연수",
    # 서식(양식)에 흔히 쓰이는 칸 이름 — 이름이 아니다
    "성명", "이름", "직위", "직급", "소속", "주소", "전화", "번호", "연락", "생년",
    "월일", "계좌", "은행", "예금", "서명", "날인", "구분", "비고", "합계", "금액",
    "기간", "장소", "대상", "내용", "제목", "담당", "확인", "신청", "동의", "수집",
    # 학교 문서에 자주 나오는 낱말
    "현황", "장학사", "차담회", "위원장", "지정", "기능", "진의", "명단", "결과",
    "계획", "운영", "평가", "지침", "예산", "실적", "추진", "협의", "심의", "보고",
    "부서", "학년", "학급", "교시", "차시", "단원", "영역", "과목", "학기", "연차",
    "참석", "출장", "복무", "근무", "휴가", "연가", "조퇴", "출석", "결석", "지각",
    "제공", "활용", "적용", "구축", "개선", "강화", "확대", "지속", "완료", "예정",
    "안내", "안내장", "이동", "도박", "선물", "노트", "우선", "여러분", "주도성",
    "선발", "설문", "만족", "협조", "관찰", "배치", "발송", "게시", "작품", "교실",
}


# ─────────────────────────────────────────────────────────────
# 탐지 규칙
# ─────────────────────────────────────────────────────────────
RE_RRN = re.compile(r"(?<![\d-])(\d{6})[-\s]?([1-8]\d{6})(?![\d-])")
RE_PHONE = re.compile(
    r"(?<![\d-])(?:0(?:1[016789]|2|[3-6]\d)[-.\s]?\d{3,4}[-.\s]?\d{4})(?![\d-])")
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_CARD = re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")
# 계좌번호는 '계좌·입금·송금·예금' 표시가 가까이 있을 때만 인정한다.
# 표시 없이 숫자-숫자-숫자 꼴을 모두 잡으면 날짜(2024-01-01)까지 걸린다.
RE_ACCOUNT = re.compile(
    r"(?:계좌\s*(?:번호)?|입금\s*계좌|송금\s*계좌|예금\s*주?|account)"
    r"\s*[:：]?\s*[(\[]?\s*"
    r"(\d[\d-]{7,20}\d)", re.IGNORECASE)
RE_BIRTH = re.compile(
    r"\b(\d{4})[.\-/년]\s?(0?[1-9]|1[0-2])[.\-/월]\s?(0?[1-9]|[12]\d|3[01])일?\b")
RE_CAR = re.compile(r"\b\d{2,3}[가-힣]\s?\d{4}\b")

# 이 해보다 나중이면 '생년월일'이 아니라 문서 날짜로 본다 (라벨이 없을 때만 적용)
_BIRTH_YEAR_MAX = 2015
# 주소 뒤쪽(상세주소)은 줄바꿈을 넘지 않도록 한다.
# 넘어가면 다음 줄의 전화번호 등과 겹쳐서 통째로 버려진다.
RE_ADDRESS = re.compile(
    r"(?:[가-힣]+(?:특별시|광역시|특별자치시|특별자치도)[ \t]*)?"
    r"[가-힣]{2,10}(?:시|군|구)[ \t]+[가-힣0-9]{2,15}(?:로|길|동|읍|면|리)[ \t]*"
    r"[0-9][0-9-]{0,9}[가-힣0-9 \t,()-]{0,40}")

# 이름 앞에 붙는 표시 — 믿을 만한 정도에 따라 둘로 나눈다.
#   강한 표시 : 뒤에 오는 것이 사람 이름일 가능성이 매우 높다 (2~3글자 허용)
#   약한 표시 : 일반 낱말이 뒤에 오는 일도 흔하다 ('학부모 안내', '학생 도박')
#              → 3글자 이름만 인정해서 오탐을 줄인다
STRONG_LABELS = (
    "성명", "성 명", "성  명", "이름", "예금주", "신청인", "작성자",
    "대표자", "담당자", "책임자", "인솔자", "지도교사",
)
WEAK_LABELS = (
    "담당", "강사", "교사", "학생", "선생님", "보호자", "학부모",
    "참가자", "수강생", "발표자", "위원", "부장",
)

# 라벨과 이름 사이에는 반드시 구분(공백·콜론 등)이 있어야 한다.
# 없으면 '학생현황' 같은 한 낱말이 '학생'+'현황'으로 쪼개져 오탐이 된다.
# 이름은 두 가지 모양만 인정한다.
#   ① 붙여쓴 이름            홍길동
#   ② 글자마다 띄어쓴 이름    홍 길 동
# '이제 곧' 처럼 2글자+1글자로 섞인 것은 이름이 아니다.
_NAME_BODY = r"([가-힣]{2,3}|[가-힣](?:[ \t][가-힣]){1,3})(?![가-힣])"
_SEP = r"[ \t]*[:：]?[ \t]*[)\]]?[ \t\n]+"

RE_NAME_STRONG = re.compile(
    r"(?:" + "|".join(re.escape(x) for x in STRONG_LABELS) + r")" + _SEP + _NAME_BODY)
RE_NAME_WEAK = re.compile(
    r"(?:" + "|".join(re.escape(x) for x in WEAK_LABELS) + r")" + _SEP + _NAME_BODY)
RE_NAME_BARE = re.compile(r"(?<![가-힣])([가-힣]{2,4})(?![가-힣])")

NAME_LABELS = STRONG_LABELS + WEAK_LABELS       # 하위호환
RE_NAME_LABELED = RE_NAME_STRONG                # 하위호환


def _valid_rrn(digits: str) -> bool:
    """주민등록번호 검증(체크섬). 날짜처럼 생긴 숫자의 오탐을 줄인다."""
    if len(digits) != 13:
        return False
    mm, dd = int(digits[2:4]), int(digits[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    w = [2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5]
    total = sum(int(d) * x for d, x in zip(digits[:12], w))
    return (11 - total % 11) % 10 == int(digits[12])


def _looks_like_date(s: str) -> bool:
    """2024-01-01 처럼 날짜로 보이는지"""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s.strip())
    if not m:
        return False
    y, mo, d = map(int, m.groups())
    return 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31


def _looks_like_name(s: str) -> bool:
    """사람 이름처럼 보이는지. 한국 이름은 보통 2~3글자(성1 + 이름1~2)."""
    if len(s) < 2 or len(s) > 4:
        return False
    if s in NAME_STOPWORDS:
        return False
    if _has_particle_tail(s):                 # '성장을', '반응과' 같은 말
        return False
    if s[:2] in COMPOUND_SURNAMES:            # 남궁·황보 등 두 글자 성
        return 3 <= len(s) <= 4
    if len(s) == 4:                           # 두 글자 성이 아니면 4글자는 이름이 아니다
        return False
    return s[0] in SURNAMES


# 낱말 끝에 붙는 조사 — 이게 붙어 있으면 사람 이름이 아니다
_PARTICLES = ("을", "를", "은", "는", "이", "가", "의", "에", "도", "만",
              "과", "와", "로", "며", "고", "서", "요", "다", "죠", "함",
              # 일반 낱말의 끝에 흔한 글자 ('지정및', '조치후', '이름꼭')
              "및", "후", "꼭", "님", "등", "외", "내", "별", "용", "측", "간")


def _has_particle_tail(s: str) -> bool:
    """'김치를', '지정및' 처럼 조사·꼬리말로 끝나는지 본다."""
    return len(s) >= 3 and s[-1] in _PARTICLES


# ─────────────────────────────────────────────────────────────
# 필터
# ─────────────────────────────────────────────────────────────
class PrivacyFilter:
    def __init__(self, policy: Policy | None = None):
        self.p = policy or Policy()

    # ── 찾기만 (바꾸지 않음)
    def find(self, text: str) -> list[Hit]:
        hits: list[Hit] = []
        taken: list[tuple[int, int]] = []

        def overlaps(a: int, b: int) -> bool:
            return any(a < e and b > s for s, e in taken)

        def add(kind, m, original, masked, conf="보통", g=0):
            s, e = m.span(g)
            if overlaps(s, e):
                return
            taken.append((s, e))
            hits.append(Hit(kind, original, masked, s, e,
                            text[max(0, s - 18):e + 18].replace("\n", " ").strip(), conf))

        # 1) 주민등록번호
        #    검증식(체크섬)으로 '걸러내지' 않는다 — 오타가 있는 실제 번호를
        #    놓치는 쪽이 훨씬 위험하므로, 검증은 확신도 표시에만 쓴다.
        for m in RE_RRN.finditer(text):
            digits = m.group(1) + m.group(2)
            mm, dd = int(digits[2:4]), int(digits[4:6])
            if not (1 <= mm <= 12 and 1 <= dd <= 31):
                continue                     # 날짜조차 아니면 번호가 아니다
            conf = "확실" if _valid_rrn(digits) else "보통"
            add("주민등록번호", m, m.group(0),
                mask_rrn(m.group(0), self.p.주민등록번호), conf)

        # 2) 카드번호
        for m in RE_CARD.finditer(text):
            add("카드번호", m, m.group(0), mask_card(m.group(0), self.p.카드번호))

        # 3) 전화번호
        for m in RE_PHONE.finditer(text):
            add("전화번호", m, m.group(0),
                mask_phone(m.group(0), self.p.전화번호), "확실")

        # 4) 이메일
        for m in RE_EMAIL.finditer(text):
            add("이메일", m, m.group(0),
                mask_email(m.group(0), self.p.이메일), "확실")

        # 5) 계좌번호
        for m in RE_ACCOUNT.finditer(text):
            val = m.group(1)
            if not val or _looks_like_date(val):
                continue
            if len(re.sub(r"\D", "", val)) < 9:      # 계좌번호는 보통 9자리 이상
                continue
            add("계좌번호", m, val, mask_account(val, self.p.계좌번호), "확실", 1)

        # 6) 주소
        for m in RE_ADDRESS.finditer(text):
            add("주소", m, m.group(0).strip(),
                mask_address(m.group(0).strip(), self.p.주소))

        # 7) 생년월일
        #    문서 작성일(2025.5.20 등)까지 가리면 기록이 망가진다.
        #    '생년월일' 표시가 가까이 있거나, 태어난 해로 볼 만한 연도만 인정한다.
        for m in RE_BIRTH.finditer(text):
            year = int(m.group(1))
            before = text[max(0, m.start() - 20):m.start()]
            labeled = bool(re.search(r"생년월일|생 년 월 일|생일|출생", before))
            if not labeled and not (1900 <= year <= _BIRTH_YEAR_MAX):
                continue
            add("생년월일", m, m.group(0),
                mask_birth(m.group(0), self.p.생년월일),
                "확실" if labeled else "낮음")

        # 8) 차량번호
        for m in RE_CAR.finditer(text):
            add("차량번호", m, m.group(0), mask_car(m.group(0), self.p.차량번호), "낮음")

        # 9) 이름
        #    ① 라벨(성명·강사·예금주…) 옆에 있는 이름 → 가장 믿을 만하다
        #    ② 그렇게 확인된 이름이 문서 다른 곳에도 나오면 같이 가린다
        #    ③ '적극적'일 때만 성씨 추정까지 (오탐 각오)
        강도 = self.p.이름_탐지강도
        확인된_이름: set[str] = set()

        for rex, 최소길이 in ((RE_NAME_STRONG, 2), (RE_NAME_WEAK, 3)):
            for m in rex.finditer(text):
                raw = m.group(1)
                nm = re.sub(r"[ \t]+", "", raw)   # '홍 길 동' -> '홍길동'
                if len(nm) < 최소길이:
                    continue
                if not _looks_like_name(nm):      # '승낙서' 같은 낱말 걸러내기
                    continue
                확인된_이름.add(nm)
                add("이름", m, raw, mask_name(nm, self.p.이름), "확실", 1)

        if 강도 in ("보통", "적극적") and 확인된_이름:
            # 공문서는 '홍 길 동' 처럼 글자 사이를 띄우는 일이 많다.
            # 확인된 이름은 띄어쓴 형태까지 함께 찾는다.
            alts = "|".join(
                r"[ \t]*".join(re.escape(ch) for ch in n)
                for n in sorted(확인된_이름, key=len, reverse=True)
            )
            pat = re.compile(r"(?<![가-힣])(" + alts + r")(?![가-힣])")
            for m in pat.finditer(text):
                raw = m.group(1)
                nm = re.sub(r"[ \t]+", "", raw)
                add("이름", m, raw, mask_name(nm, self.p.이름), "확실", 1)

        if 강도 == "적극적":
            for m in RE_NAME_BARE.finditer(text):
                nm = m.group(1)
                if len(nm) != 3 or not _looks_like_name(nm):
                    continue
                if _has_particle_tail(nm):        # '김치를', '방법을' 같은 말 제외
                    continue
                add("이름", m, nm, mask_name(nm, self.p.이름), "낮음", 1)

        hits.sort(key=lambda h: h.start)
        return hits

    # ── 찾아서 바꾸기
    def mask(self, text: str) -> tuple[str, list[Hit]]:
        hits = self.find(text)
        keep = [h for h in hits if self.p.mode_for(h.kind) != "그대로"]
        out, last = [], 0
        for h in keep:
            out.append(text[last:h.start])
            out.append(h.masked)
            last = h.end
        out.append(text[last:])
        return "".join(out), keep


# ─────────────────────────────────────────────────────────────
# 보고서
# ─────────────────────────────────────────────────────────────
class PrivacyReport:
    """여러 파일에서 무엇이 어떻게 바뀌었는지 모아서 기록한다."""

    def __init__(self, show_original: bool = True):
        self.show_original = show_original
        self.rows: list[dict] = []
        self.counter = collections.Counter()

    def add(self, file_rel: str, hits: list[Hit]):
        for h in hits:
            self.rows.append({
                "file": file_rel, "kind": h.kind,
                "original": h.original, "masked": h.masked,
                "confidence": h.confidence, "context": h.context,
            })
            self.counter[h.kind] += 1

    @property
    def files(self) -> int:
        return len({r["file"] for r in self.rows})

    # ── 화면 요약
    def summary(self) -> str:
        if not self.rows:
            return "개인정보로 보이는 내용을 찾지 못했습니다."
        L = [f"개인정보 {len(self.rows)}건을 {self.files}개 파일에서 가렸습니다.", "",
             "  종류별"]
        for k, n in self.counter.most_common():
            L.append(f"    {k:10} {n:5}건")
        return "\n".join(L)

    # ── 파일로 저장
    def write(self, out_dir: str, filename: str = "_개인정보_보고서.md") -> str | None:
        if not self.rows:
            return None
        path = os.path.join(out_dir, filename)

        L = ["# 🔒 개인정보 처리 보고서", ""]
        if self.show_original:
            L += ["> ⚠️ **이 파일에는 가리기 전의 원본 개인정보가 그대로 들어 있습니다.**",
                  "> 확인이 끝나면 삭제하거나, 절대 공유·게시하지 마세요.", ""]
        else:
            L += ["> 원본 값은 표시하지 않았습니다. (건수와 위치만 기록)", ""]

        L += [f"전체 **{len(self.rows)}건** · **{self.files}개 파일**", "",
              "| 종류 | 건수 |", "|------|-----:|"]
        for k, n in self.counter.most_common():
            L.append(f"| {k} | {n} |")
        L += ["", "---", ""]

        by_file = collections.defaultdict(list)
        for r in self.rows:
            by_file[r["file"]].append(r)

        for fn in sorted(by_file):
            rows = by_file[fn]
            L += [f"## {fn}", "", f"{len(rows)}건", ""]
            if self.show_original:
                L += ["| 종류 | 원본 | 바뀐 값 | 확신도 | 주변 문맥 |",
                      "|------|------|---------|--------|-----------|"]
                for r in rows:
                    ctx = r["context"].replace("|", "／")[:50]
                    L.append(f"| {r['kind']} | `{r['original']}` | `{r['masked']}` "
                             f"| {r['confidence']} | {ctx} |")
            else:
                L += ["| 종류 | 바뀐 값 | 확신도 |", "|------|---------|--------|"]
                for r in rows:
                    L.append(f"| {r['kind']} | `{r['masked']}` | {r['confidence']} |")
            L.append("")

        L += ["---", "",
              "### 확인이 필요한 이유", "",
              "자동 탐지는 완벽하지 않습니다.", "",
              "- **놓칠 수 있습니다** — 특이한 형식이나 문장 속 이름",
              "- **잘못 잡을 수 있습니다** — 사람 이름처럼 보이는 낱말",
              "",
              "확신도가 `낮음`인 항목은 특히 눈으로 확인해 주세요.",
              "공개 전에는 반드시 사람이 최종 점검해야 합니다."]

        os.makedirs(out_dir, exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(L))

        with io.open(os.path.join(out_dir, "_개인정보.json"), "w", encoding="utf-8") as f:
            json.dump({"total": len(self.rows), "files": self.files,
                       "by_kind": dict(self.counter),
                       "rows": self.rows if self.show_original else
                               [{k: v for k, v in r.items() if k != "original"}
                                for r in self.rows]},
                      f, ensure_ascii=False, indent=1)
        return path


# ─────────────────────────────────────────────────────────────
# 미리보기 — 바꾸기 전에 무엇이 걸리는지 확인
# ─────────────────────────────────────────────────────────────
def preview(text: str, policy: Policy | None = None, limit: int = 30):
    pf = PrivacyFilter(policy)
    hits = pf.find(text)
    if not hits:
        print("개인정보로 보이는 내용이 없습니다.")
        return hits
    cnt = collections.Counter(h.kind for h in hits)
    print(f"{len(hits)}건 발견")
    for k, n in cnt.most_common():
        print(f"   {k:10} {n:4}건")
    print()
    for h in hits[:limit]:
        print(f"   [{h.kind:6}·{h.confidence:2}] {h.original}  ->  {h.masked}")
    if len(hits) > limit:
        print(f"   … 외 {len(hits)-limit}건")
    return hits
