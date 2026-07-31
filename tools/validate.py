#!/usr/bin/env python3
"""제출 파일을 검증하고 data/manifest.json 을 다시 만든다.

틀린 값이 들어오는 경로를 제출 시점에서 끊는 것이 목적이다.
  - 구조: member·date·holdings 존재, date 와 폴더명 일치
  - 비중: 합계 100 (±0.5). scopes 도 각각 100
  - 티커: 마스터에 없는 티커는 Yahoo 에서 실제로 조회되는지 확인한다.
          조회되지 않으면 오타이거나 존재하지 않는 종목이므로 반려한다.
  - 레버리지: 신규 티커 이름에 레버리지 표현이 있으면 배수 등록을 요구한다.
              배수를 1로 두면 실효 익스포저가 실제보다 낮게 나온다.

하나라도 걸리면 종료 코드 1 을 돌려 커밋 체크를 실패시킨다.
manifest 는 구조가 온전한 파일까지는 반영해 두어 나머지 멤버가 막히지 않게 한다.
"""

import json, pathlib, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UA = {"User-Agent": "Mozilla/5.0 (compatible; alpha-trader/1.0)"}
SEARCH = "https://finance.yahoo.com/quote/{}"
LEV_HINT = ("3x", "2x", "lev", "bull", "bear", "ultra", "레버리지", "인버스")

errors: list[str] = []
notes: list[str] = []


def yahoo_name(ticker: str):
    """Yahoo 에 실재하는 티커면 종목명을, 아니면 None 을 돌려준다."""
    u = ("https://query1.finance.yahoo.com/v8/finance/chart/"
         + urllib.parse.quote(ticker) + "?range=5d&interval=1d")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=20) as r:
                d = json.load(r)["chart"]
            if d.get("error") or not d.get("result"):
                return None
            m = d["result"][0].get("meta", {})
            return m.get("longName") or m.get("shortName") or ticker
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None      # 계속 실패하면 보수적으로 반려한다


def naver_codes(name: str):
    """한글 종목명을 네이버 자동완성으로 조회해 코드 후보를 돌려준다.
    자동 교체는 하지 않는다 — (H)·커버드콜 등 비슷한 이름이 많아 오선택 위험이 있다."""
    try:
        u = "https://ac.stock.naver.com/ac?" + urllib.parse.urlencode(
            {"q": name, "target": "stock,index"})
        req = urllib.request.Request(u, headers={**UA, "Referer": "https://finance.naver.com/"})
        with urllib.request.urlopen(req, timeout=15) as r:
            items = json.load(r).get("items", [])
        out = []
        for it in items[:3]:
            for x in (it if isinstance(it, list) else [it]):
                c, n = x.get("code"), x.get("name")
                if c and n:
                    out.append(f"{c}.KS ({n})")
        return out
    except Exception:
        return []


def main() -> int:
    master = json.loads((DATA / "instruments.json").read_text(encoding="utf-8"))
    INSTR, ALIAS = master["instruments"], master.get("alias", {})
    key = lambda t: ALIAS.get(str(t).strip(), str(t).strip())

    snaps: dict[str, list] = {}
    checked: dict[str, str | None] = {}

    # 마스터 무결성 — 등록된 키가 전부 Yahoo 심볼이어야 한다.
    # 이름을 키로 넣어두면 "등록됨 = 확인됨" 으로 통과해 버려 시세를 못 받는다.
    for k in INSTR:
        if k == "CASH":
            continue
        if any("\uac00" <= c <= "\ud7a3" for c in k):
            errors.append(f"instruments.json — '{k}' 는 티커가 아니라 이름임. "
                          f"실제 심볼로 등록하고 이름은 alias 로 연결할 것")

    for f in sorted((DATA / "members").glob("*/*.json")):
        folder, rel = f.parent.name, f.relative_to(ROOT)
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"{rel} — JSON 문법 오류: {e}")
            continue

        mid = d.get("member")
        if not mid:
            errors.append(f"{rel} — member 항목이 없음"); continue
        if d.get("date") and d["date"] != folder:
            errors.append(f"{rel} — date({d['date']})가 폴더명({folder})과 다름. "
                          f"둘 다 실제 매매한 날로 맞출 것"); continue

        groups = [("holdings", d.get("holdings") or [])]
        groups += [(f"scopes[{s.get('name')}]", s.get("holdings") or []) for s in d.get("scopes") or []]
        ok = True
        for label, hs in groups:
            if not hs:
                errors.append(f"{rel} — {label} 가 비어 있음"); ok = False; continue
            try:
                tot = sum(float(h["weight"]) for h in hs)
            except Exception as e:
                errors.append(f"{rel} — {label} weight 읽기 실패: {e}"); ok = False; continue
            if abs(tot - 100) > 0.5:
                errors.append(f"{rel} — {label} weight 합이 {tot:.2f}. 100 이어야 함 "
                              f"(현금을 CASH 로 넣었는지 확인)"); ok = False

            for h in hs:
                raw = str(h.get("ticker", "")).strip()
                t = key(raw)
                if not t or t == "CASH":
                    continue
                if t in INSTR:
                    continue                       # 이미 등록된 종목은 확인 완료로 본다
                if t not in checked:
                    checked[t] = yahoo_name(t)
                    time.sleep(0.2)
                name = checked[t]
                if name is None:
                    hint = ""
                    if any("\uac00" <= c <= "\ud7a3" for c in raw):
                        cands = naver_codes(raw)
                        hint = (" 이 이름으로 검색된 코드: " + " / ".join(cands) +
                                " — 맞는 것을 골라 티커 자리에 넣을 것") if cands else ""
                    errors.append(
                        f"{rel} — 티커 '{raw}' 를 Yahoo Finance 에서 찾을 수 없음. "
                        f"오타이거나 한글 이름을 적었을 수 있음 (예: BRK.B→BRK-B, 국내는 005930.KS)."
                        + (hint or " https://finance.yahoo.com 에서 조회되는 심볼로 적을 것"))
                    ok = False
                else:
                    notes.append(f"신규 종목 {t} — {name}")
                    if any(x in name.lower() for x in LEV_HINT):
                        errors.append(
                            f"{rel} — '{t}' 는 레버리지 상품으로 보임({name}). "
                            f"배수를 모른 채 두면 실효 익스포저가 실제보다 낮게 계산됨. "
                            f"data/instruments.json 에 배수를 등록한 뒤 다시 올릴 것")
                        ok = False
        if ok or mid:
            snaps.setdefault(folder, []).append({"id": mid, "file": f.name})

    out = {"club": "투자 스터디", "baseCurrency": "KRW",
           "snapshots": [{"date": k, "members": sorted(v, key=lambda x: x["file"])}
                         for k, v in sorted(snaps.items())]}
    (DATA / "manifest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")

    n = sum(len(v) for v in snaps.values())
    lines = [f"스냅샷 {len(snaps)}회차 · 제출 {n}건"]
    if notes:
        lines += ["", "### 새로 확인된 종목", *[f"- {x}" for x in dict.fromkeys(notes)]]
    if errors:
        lines += ["", "### 고쳐야 할 것", *[f"- {e}" for e in errors]]
    else:
        lines += ["", "검증 통과."]
    report = "\n".join(lines)
    print(report)
    pathlib.Path(ROOT / ".summary.md").write_text(report, encoding="utf-8")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
