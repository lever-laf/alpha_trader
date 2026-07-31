#!/usr/bin/env python3
"""종가를 받아 멤버별 누적 지수를 계산하고 data/results.json 에 쓴다.

표준 라이브러리만 쓴다 (Actions 에서 pip install 없이 돌리기 위함).

계산 규칙
  - 스냅샷 A(d1) → B(d2) 구간은 A 의 비중을 유지했다고 본다.
  - 구간 수익률 = Σ(비중 × 종목 원화 수익률). 현금은 0.
  - 누적 지수 = 구간 수익률을 chain-link. 각 멤버의 첫 스냅샷이 100.0.
  - 배당은 adjclose(배당 재투자 반영)로 자동 포함된다. 세금은 반영하지 않는다.
  - 미국 종목은 그날 USD/KRW 종가로 환산한다. 환차익이 성과에 포함된다.
  - 휴장일은 직전 종가로 채운다(forward fill).
"""

import json, pathlib, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?period1={}&period2={}&interval=1d&events=div"
UA = {"User-Agent": "Mozilla/5.0 (compatible; alpha-trader/1.0)"}
FX = "USDKRW=X"
LEV_HINT = ("3x", "2x", "lev", "bull", "bear", "ultra", "레버리지", "인버스")

warnings: list[str] = []


def fetch(ticker: str, start: date, end: date, tries: int = 3):
    """(통화, {날짜: 배당조정 종가}) 반환. 실패하면 None."""
    p1 = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
    p2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp())
    url = CHART.format(urllib.parse.quote(ticker), p1, p2)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                d = json.load(r)["chart"]
            if d.get("error"):
                return None
            res = d["result"][0]
            ind = res["indicators"]
            closes = (ind.get("adjclose", [{}])[0].get("adjclose")
                      or ind["quote"][0]["close"])
            out = {}
            for ts, c in zip(res["timestamp"], closes):
                if c is not None:
                    out[datetime.fromtimestamp(ts, timezone.utc).date().isoformat()] = float(c)
            meta = res.get("meta", {})
            return {"ccy": meta.get("currency", "USD"),
                    "name": meta.get("longName") or meta.get("shortName") or ticker,
                    "px": out}
        except Exception:
            if attempt == tries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def ffill(px: dict, days: list[str]) -> dict:
    """휴장일을 직전 종가로 채운다."""
    out, last = {}, None
    for d in days:
        if d in px:
            last = px[d]
        if last is not None:
            out[d] = last
    return out


def main() -> int:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    master = json.loads((DATA / "instruments.json").read_text(encoding="utf-8"))
    INSTR, ALIAS = master["instruments"], master.get("alias", {})
    key = lambda t: ALIAS.get(str(t).strip(), str(t).strip())

    # ── 멤버별 스냅샷 수집 ──
    members: dict[str, list] = {}
    for snap in manifest["snapshots"]:
        for m in snap["members"]:
            f = json.loads((DATA / "members" / snap["date"] / m["file"]).read_text(encoding="utf-8"))
            members.setdefault(f.get("member", m["id"]), []).append({
                "date": f.get("date", snap["date"]),
                "w": {key(h["ticker"]): float(h["weight"]) for h in f["holdings"]},
            })
    for v in members.values():
        v.sort(key=lambda s: s["date"])
    if not members:
        print("멤버 없음"); return 1

    tickers = sorted({t for v in members.values() for s in v for t in s["w"] if t != "CASH"})
    first = min(s["date"] for v in members.values() for s in v)
    start = date.fromisoformat(first) - timedelta(days=10)
    today = datetime.now(timezone.utc).date()

    # ── 시세 조회 ──
    fx = fetch(FX, start, today)
    if not fx:
        print("환율(USDKRW=X) 조회 실패 — 중단"); return 1

    series, missing, newly = {}, [], []
    for t in tickers:
        r = fetch(t, start, today)
        if not r:
            missing.append(t); continue
        series[t] = r
        if t not in INSTR:                      # 마스터에 없는 신규 종목 → 잠정 등록
            INSTR[t] = {"n": r["name"], "th": "기타", "v": 1}
            newly.append(f"{t} — {r['name']}")
        nm = (INSTR[t].get("n", "") + " " + r["name"]).lower()
        if INSTR[t].get("lev", 1) == 1 and any(h in nm for h in LEV_HINT):
            warnings.append(f"{t} — 이름에 레버리지 표현이 있는데 배수가 1로 잡혀 있음: {r['name']}")
        time.sleep(0.25)

    # 시세를 못 받은 종목이 있으면 그 종목을 가진 멤버만 보류한다.
    # 조용히 0으로 처리하면 비중 합이 깨져 수익률이 왜곡되므로 계산 자체를 하지 않는다.
    if missing:
        print("시세 조회 실패: " + ", ".join(missing))

    # ── 원화 일별 시계열 ──
    # 장이 열려 있는 당일은 종가가 아니라 장중 가격이 온다. 조회 시각마다 값이 달라지므로
    # 한·미 양쪽 장이 모두 마감된 날짜까지만 쓴다.
    # (KST 08:00 기준으로 전일은 한국 15:30 마감·미국 전일 종가 확정이 모두 끝난 상태)
    kst = datetime.now(timezone(timedelta(hours=9)))
    cutoff = (kst - timedelta(days=1 if kst.hour >= 6 else 2)).date().isoformat()

    days = sorted({d for r in series.values() for d in r["px"]} | set(fx["px"]))
    days = [d for d in days if first <= d <= cutoff]
    if not days:
        print("가격 데이터 없음"); return 1
    fxk = ffill(fx["px"], days)
    krw = {}
    for t, r in series.items():
        p = ffill(r["px"], days)
        krw[t] = ({d: v * fxk[d] for d, v in p.items() if d in fxk}
                  if r["ccy"] != "KRW" else p)

    # ── 멤버별 chain-link ──
    out, held = {}, {}
    for mid, snaps in members.items():
        bad = sorted({t for s in snaps for t in s["w"] if t in missing})
        if bad:
            held[mid] = bad
            continue
        pts, idx = [], 100.0
        for i, s in enumerate(snaps):
            d0 = s["date"]
            d1 = snaps[i + 1]["date"] if i + 1 < len(snaps) else None
            span = [d for d in days if d >= d0 and (d1 is None or d <= d1)]
            if not span:
                continue
            base = span[0]
            for d in span:
                r = 0.0
                for t, w in s["w"].items():
                    if t == "CASH":
                        continue
                    p0, p1 = krw.get(t, {}).get(base), krw.get(t, {}).get(d)
                    if not p0 or not p1:
                        continue
                    r += (w / 100.0) * (p1 / p0 - 1.0)
                pts.append([d, round(idx * (1 + r), 4)])
            idx = pts[-1][1]                     # 다음 구간의 출발점
        # 같은 날짜 중복(구간 경계) 정리 — 뒤엣것을 남긴다
        dedup = {d: v for d, v in pts}
        ser = [[d, v] for d, v in sorted(dedup.items())]
        out[mid] = {"index": ser[-1][1] if ser else 100.0, "series": ser}

    results = {
        "asOf": days[-1],
        "cutoff": cutoff,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "basis": "세전 총수익(배당 재투자 반영) · 원화 환산",
        "members": out,
        "newInstruments": newly,
        "onHold": held,
        "warnings": warnings,
    }
    (DATA / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n",
                                       encoding="utf-8")
    if newly:
        (DATA / "instruments.json").write_text(
            json.dumps(master, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"기준일 {days[-1]} (미체결 세션 제외) · 종목 {len(series)} · 멤버 {len(out)}")
    for mid, v in sorted(out.items(), key=lambda x: -x[1]["index"]):
        print(f"  {mid:<8} {v['index']:8.2f}  ({v['index'] - 100:+.2f}%)  {len(v['series'])}일")
    for mid, bad in held.items():
        print(f"  {mid:<8} 계산 보류 — 시세 없음: {', '.join(bad)}")
    if newly:
        print("신규 등록(테마 분류 필요): " + " / ".join(newly))
    for w in warnings:
        print("경고: " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
