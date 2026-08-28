#!/usr/bin/env python3
"""PR 본문·커밋 메시지·제출 파일에서 계좌 금액·보유 주수 유출을 탐지한다.

이 저장소는 비중(%)만 공개하는 것이 원칙이다 (README "왜 금액이 필요 없나" 참고).
그런데 코딩 도구로 PR을 만들면 본문·커밋 메시지에 평가액·매수액·보유 주수 같은
실제 계좌 숫자가 그대로 들어가는 사고가 반복됐다. 이 스크립트는 그런 문장을
기계적으로 걸러 사람이 고치게 만든다.

표준 라이브러리만 쓴다 (다른 tools/*.py 와 동일한 이유 — Actions 에서
pip install 없이 돌리기 위함).

탐지 대상
  (a) 원화 금액 — 숫자 + 원/만원/억/KRW, ₩ + 숫자
  (b) 달러 금액 — $ + 숫자, 숫자/키워드 순서 무관 USD·달러
  (c) 천단위 콤마 숫자(콤마 2개 이상) 또는 7자리 이상 연속 숫자
  (d) 보유 주수 — 숫자 + 주, shares/qty
  (e) 금액 계열 키워드(평가액·매수액·잔고 등)가 숫자와 같은 줄에 있는 경우

오탐 억제(같은 줄에서 먼저 제외 처리 후 탐지)
  종목 코드(005930.KS), 날짜(2026-08-27), 비중 %(31.87%), [skip ci],
  커밋 SHA(a-f 문자가 섞인 7~40자리 16진수), 이메일 안의 숫자, URL.
  단, 주가 하나 정도(예: 252,500)가 같이 걸리는 것은 감수한다 — 금액 형태면
  전부 막는 것이 정책이고, 사람이 이유를 보고 고치면 된다.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 오탐 억제 — 탐지 전에 이 패턴들이 매치된 구간을 같은 길이의 '#' 로 지운다.
# 길이를 유지해야 원문에서 매치 문자열을 그대로 잘라낼 수 있다.
# ---------------------------------------------------------------------------

_EXCLUDE_PATTERNS = [
    re.compile(r"https?://\S+"),                          # URL
    re.compile(r"[\w.+\-\[\]]+@[\w.\-]+\.\w+"),            # 이메일(앞의 숫자 id 포함)
    re.compile(r"\[skip ci\]", re.IGNORECASE),             # 커밋 메시지 관례 태그
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),                  # 날짜 (2026-08-27)
    re.compile(r"\b\d{6}\.(?:KS|KQ)\b", re.IGNORECASE),    # 종목 코드 (005930.KS)
    re.compile(r"\b\d+(?:\.\d+)?%"),                       # 비중 % 표기 (4.92%)
]

# 커밋 SHA — 순수 숫자 금액과 구분하려고 a-f 문자가 하나라도 섞인 것만 제외한다.
_SHA_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")


def _mask_exclusions(line: str) -> str:
    """오탐 유발 구간을 같은 길이의 '#' 로 치환한 문자열을 돌려준다."""
    masked = line
    for pat in _EXCLUDE_PATTERNS:
        masked = pat.sub(lambda m: "#" * len(m.group(0)), masked)

    def _sha_repl(m: re.Match) -> str:
        s = m.group(0)
        return "#" * len(s) if any(c in "abcdefABCDEF" for c in s) else s

    return _SHA_RE.sub(_sha_repl, masked)


# ---------------------------------------------------------------------------
# 탐지 패턴
# ---------------------------------------------------------------------------

# 주의: Python \w 는 한글도 단어 문자로 취급하므로, 숫자 뒤에 조사가 바로 붙는
# 흔한 한국어 문장("20,496,787원이다", "6주를 매수")에서는 \b 가 한글-한글 경계로
# 인식돼 성립하지 않는다. 한글 접미사(원/억/주) 뒤는 \b 대신 (?!\d) 만 걸어
# 숫자끼리만 안 붙게 하고, 영문 접미사(KRW/USD/shares/qty) 뒤에는 \b 를 그대로 둔다.
_WON_RE = re.compile(
    r"(?:₩\s*[\d][\d,\.]*)"
    r"|(?:[\d][\d,\.]*\s*(?:만\s*원|억\s*원|원|억)(?![\d화칙본인천래]))"  # 원화·원칙·원본 등 제외
    r"|(?:[\d][\d,\.]*\s*KRW\b)"
)
_USD_RE = re.compile(
    r"(?:\$\s*[\d][\d,\.]*)"
    r"|(?:[\d][\d,\.]*\s*달러(?!\d))"
    r"|(?:달러\s*[\d][\d,\.]*)"
    r"|(?:[\d][\d,\.]*\s*USD\b)"
    r"|(?:\bUSD\s*[\d][\d,\.]*)",
    re.IGNORECASE,
)
_COMMA_RE = re.compile(r"(?<!\d)\d{1,3}(?:,\d{3}){2,}(?!\d)")  # 1,234,567 (콤마 2개 이상)
_LONGDIGIT_RE = re.compile(r"(?<!\d)\d{7,}(?!\d)")             # 콤마 없이 7자리 이상
_SHARE_RE = re.compile(
    r"(?:(?<!\d)\d+(?:\.\d+)?\s*주(?!\d))"
    r"|(?:\b\d+(?:\.\d+)?\s*(?:shares?|qty)\b)"
    r"|(?:\b(?:shares?|qty)\b\s*[:=]?\s*\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_SIMPLE_DETECTORS = [
    ("원화금액", _WON_RE, "원화 금액으로 보이는 표기"),
    ("달러금액", _USD_RE, "달러 금액으로 보이는 표기"),
    ("천단위콤마숫자", _COMMA_RE, "콤마 2개 이상의 큰 숫자 — 계좌 금액일 가능성"),
    ("7자리이상숫자", _LONGDIGIT_RE, "7자리 이상 연속 숫자 — 계좌 금액일 가능성"),
    ("보유주수", _SHARE_RE, "보유 주수로 보이는 표기"),
]

_KEYWORDS = [
    "평가액", "평가금", "매수액", "매도액", "원금", "총자산", "잔고", "잔액",
    "예수금", "입금", "balance", "amount", "principal", "equity value",
]


@dataclass
class Finding:
    line: int
    pattern: str
    match: str
    reason: str


def _scan_keywords(lineno: int, line: str, masked: str) -> list[Finding]:
    """금액 계열 키워드가 숫자와 같은 줄에 있으면 걸어낸다."""
    out: list[Finding] = []
    for kw in _KEYWORDS:
        km = re.search(re.escape(kw), masked, re.IGNORECASE)
        if not km:
            continue
        # 한 자리 숫자(주로 "현금 0", "입금 0" 같은 잔액 없음 서술)는 제외한다.
        # 실제 금액은 최소 두 자리 이상이라 2자리 미만은 노이즈일 뿐이다.
        num = re.search(r"\d{3,}[\d,\.]*|\d{1,3},\d{3}", masked)  # 08-13 같은 날짜 조각은 제외
        if not num:
            continue
        match_text = f"{line[km.start():km.end()]} ... {line[num.start():num.end()]}"
        out.append(Finding(
            lineno, "금액키워드", match_text,
            f"'{kw}' 키워드와 숫자가 같은 줄에 있음 — 금액 표기 의심",
        ))
    return out


def scan(text: str) -> list[Finding]:
    """텍스트에서 금액·주수로 의심되는 구간을 전부 찾아 돌려준다."""
    findings: list[Finding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        masked = _mask_exclusions(line)
        for name, regex, reason in _SIMPLE_DETECTORS:
            for m in regex.finditer(masked):
                match_text = line[m.start():m.end()]
                if not match_text:
                    continue
                findings.append(Finding(i, name, match_text, reason))
        findings.extend(_scan_keywords(i, line, masked))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_ADVICE = (
    "금액·주수는 어디에도 쓰지 않는다. 비중(%)만 남기고 지운 뒤 "
    "PR 본문/커밋 메시지를 수정하라(`git commit --amend`, PR은 Edit)."
)


def _iter_commit_messages(range_spec: str):
    """base..head 범위의 각 커밋 (sha, 전체 메시지) 를 돌려준다."""
    proc = subprocess.run(
        ["git", "log", "--pretty=format:%H%x1f%B%x1e", range_spec],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"git log 실행 실패: {proc.stderr.strip()}", file=sys.stderr)
        return
    for rec in proc.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, _, msg = rec.partition("\x1f")
        yield sha, msg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="금액·주수 유출 탐지기")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdin", action="store_true", help="표준입력을 검사한다")
    group.add_argument("--file", nargs="+", metavar="PATH", help="파일을 검사한다")
    group.add_argument("--commits", metavar="BASE..HEAD",
                        help="범위 안의 커밋 메시지를 검사한다")
    args = parser.parse_args(argv)

    hits: list[tuple[str, Finding]] = []

    if args.stdin:
        for f in scan(sys.stdin.read()):
            hits.append(("stdin", f))
    elif args.file:
        for path in args.file:
            p = pathlib.Path(path)
            try:
                text = p.read_text(encoding="utf-8")
            except Exception as e:
                print(f"{path} 읽기 실패: {e}", file=sys.stderr)
                continue
            for f in scan(text):
                hits.append((path, f))
    elif args.commits:
        for sha, msg in _iter_commit_messages(args.commits):
            for f in scan(msg):
                hits.append((f"commit {sha[:7]}", f))

    if hits:
        print("금액·주수 노출 의심 항목이 발견됨:\n")
        for loc, f in hits:
            print(f"  [{loc}:{f.line}] ({f.pattern}) {f.match!r} — {f.reason}")
        print()
        print(_ADVICE)
        return 1

    print("통과 — 금액·주수 노출 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
