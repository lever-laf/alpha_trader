#!/usr/bin/env python3
"""guard.py 단위 테스트.

실제로 유출됐던 문장은 반드시 걸리고, PR #3 본문 같은 정상 문장은
반드시 통과해야 한다는 두 축을 확인한다.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from guard import scan  # noqa: E402


class 실제유출문장은_걸린다(unittest.TestCase):

    def test_평가액_원화(self):
        findings = scan("토스 계좌 평가액 20,496,787원")
        self.assertTrue(findings, "평가액 + 원화 금액이 걸리지 않음")

    def test_매수액_원화(self):
        findings = scan("매수액 5,708,760원")
        self.assertTrue(findings, "매수액 + 원화 금액이 걸리지 않음")

    def test_보유주수와_매입단가(self):
        findings = scan("TSLA 6주(주당 464,403원)")
        self.assertTrue(findings, "보유 주수·매입단가가 걸리지 않음")
        patterns = {f.pattern for f in findings}
        self.assertIn("보유주수", patterns)

    def test_소수점_주수(self):
        findings = scan("USFR 34.281424주")
        self.assertTrue(findings, "소수점 보유 주수가 걸리지 않음")
        self.assertTrue(any(f.pattern == "보유주수" for f in findings))

    def test_잔고_키워드_단독(self):
        findings = scan("현재 잔고는 12,345,678원 수준이다")
        self.assertTrue(findings)

    def test_달러_표기(self):
        findings = scan("이번 매수는 $3,200 규모였다")
        self.assertTrue(findings)

    def test_shares_표기(self):
        findings = scan("Bought 12.5 shares of QQQ this week")
        self.assertTrue(findings)

    def test_원화금액_뒤에_조사가_붙어도_걸린다(self):
        # 한글 조사가 숫자 접미사 바로 뒤에 붙으면 \b 가 성립하지 않는 회귀 케이스.
        findings = scan("토스 계좌 평가액 20,496,787원이다")
        patterns = {f.pattern for f in findings}
        self.assertIn("원화금액", patterns)
        self.assertIn("천단위콤마숫자", patterns)

    def test_보유주수_뒤에_조사가_붙어도_걸린다(self):
        findings = scan("TSLA를 6주를 매수했다")
        self.assertTrue(any(f.pattern == "보유주수" for f in findings))


class 정상문장은_통과한다(unittest.TestCase):

    def test_비중_나열(self):
        text = "CASH 31.87 TSM 9.45 NVDA 20.10 448300.KS 1.33"
        self.assertEqual(scan(text), [])

    def test_날짜_기준(self):
        self.assertEqual(scan("2026-08-27 기준 포트폴리오 스냅샷"), [])

    def test_스냅샷_요약(self):
        self.assertEqual(scan("스냅샷 7회차 • 제출 13건"), [])

    def test_환율_설명(self):
        self.assertEqual(scan("adjclose × 당일 USD/KRW 종가로 환산한다"), [])

    def test_종목_등락률(self):
        self.assertEqual(scan("000660.KS +11.8%"), [])
        self.assertEqual(scan("비중은 7/30 제출분에 7/30→8/10 원화 등락을 곱해 산출했다"), [])
        self.assertEqual(scan("계좌 평가액을 모르므로 08-13 매수분이 현금 전액이었다고 봤다"), [])

    def test_pr3_본문_예시(self):
        text = (
            "## 무엇이 바뀌었나\n"
            "- marry 2026-08-27 제출: SCHD·나스닥100(H) 추가 매수\n"
            "- CASH 31.87 TSM 9.45 448300.KS 1.33 005930.KS 4.92%\n"
            "\n"
            "## 검증\n"
            "python3 tools/validate.py 통과 확인\n"
        )
        self.assertEqual(scan(text), [])

    def test_커밋_메시지와_스킵태그(self):
        text = "시세 갱신 2026-08-27 [skip ci]"
        self.assertEqual(scan(text), [])

    def test_github_actions_봇_이메일(self):
        text = "41898282+github-actions[bot]@users.noreply.github.com"
        self.assertEqual(scan(text), [])

    def test_커밋_sha(self):
        text = "efb9c09 Merge pull request #3 from lever-laf/marry/2026-08-27"
        self.assertEqual(scan(text), [])

    def test_url(self):
        self.assertEqual(scan("대시보드: https://lever-laf.github.io/alpha_trader/"), [])


if __name__ == "__main__":
    unittest.main()
