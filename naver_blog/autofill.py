"""키워드 자동 채우기: keywords.csv 에 쓸 키워드가 떨어지면 프로그램이 알아서 골라 넣는다.

- 평생 키워드(꾸준히 검색되는 것)와 지금 뜨는 키워드를 반반 섞는다 (설정: [autofill] mix)
- S·A 등급, 이미 쓴 글과 겹치지 않는 것, 가사·쇼핑·이슈성 말이 없는 것만
- 지금 뜨는 키워드는 연예 쪽보다 생활·경제·IT 쪽을 먼저 고른다
- 발행은 하지 않는다. 고른 키워드로 글을 써서 임시저장까지만 (발행은 사람이 확인 후)

따로 실행: python autofill.py  (13_autofill_keywords.bat)
"""

from evergreen_keywords import ISSUE_WORDS, find_evergreen, random_plan, seeds_from
from keyword_finder import load_keys

ENTERTAINMENT = "연예"


def _ok(r: dict) -> bool:
    return r.get("grade") in ("S", "A") and not r.get("written") and not ISSUE_WORDS.search(r["keyword"])


def evergreen_candidates(cfg: dict, keys: dict) -> list[dict]:
    for _ in range(2):  # 씨앗을 바꿔 가며 두 번까지
        try:
            rows = find_evergreen(cfg, keys, random_plan(seeds_from(cfg)))
        except Exception as e:
            print(f"  (평생 키워드 찾기 실패: {str(e).splitlines()[0][:80]})")
            return []
        good = [r for r in rows if _ok(r) and r["kind"] in ("평생", "평생?")]
        if good:
            return good
    return []


def trending_candidates(cfg: dict) -> list[dict]:
    try:
        from trending_keywords import find_trending
        rows = find_trending(cfg) or []
    except Exception as e:
        print(f"  (지금 뜨는 키워드 읽기 실패: {str(e).splitlines()[0][:80]})")
        return []
    good = [r for r in rows if _ok(r)]
    if not cfg.get("autofill", {}).get("entertainment", False):  # 연예·사람 이슈는 금방 식고 사실 확인이 어려워 기본은 뺀다
        good = [r for r in good if ENTERTAINMENT not in (r.get("category") or "")]
    return good


def fill(cfg: dict, need: int) -> list[str]:
    """need 개를 골라 keywords.csv 에 넣고, 넣은 키워드 목록을 돌려준다"""
    from main import load_rows, save_rows
    acfg = cfg.get("autofill", {})
    mix = [m.strip() for m in str(acfg.get("mix", "evergreen,trending")).split(",") if m.strip()]
    keys = load_keys()
    if not (keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id")):
        print("  키워드 자동 채우기: naver_keys.txt 에 검색광고 API 키가 없어서 건너뛰어요.")
        return []
    print(f"\n키워드가 떨어져서 {need}개를 자동으로 골라요 ({' + '.join(mix)})")
    # 각 출처에서 한 번씩만 찾아 두고, 번갈아 하나씩 뽑는다 (한쪽이 모자라면 다른 쪽에서 채운다)
    pools = {}
    for m in mix:
        pools[m] = evergreen_candidates(cfg, keys) if m == "evergreen" else trending_candidates(cfg)
    picked: list[dict] = []
    taken: set = set()
    while len(picked) < need and any(pools.values()):
        for m in mix:
            while pools[m] and pools[m][0]["keyword"].replace(" ", "") in taken:
                pools[m].pop(0)
            if pools[m] and len(picked) < need:
                r = pools[m].pop(0)
                taken.add(r["keyword"].replace(" ", ""))
                picked.append(r)
    if not picked:
        print("  자동으로 고를 만한 키워드를 찾지 못했어요. 11·12번으로 직접 찾아 주세요.")
        return []
    save_rows(load_rows() + [{"keyword": r["keyword"], "memo": "", "status": "", "category": r.get("category", "")}
                             for r in picked])
    for r in picked:
        kind = r.get("kind") or "지금 뜨는"
        print(f"  + [{r['grade']}] [{kind}] {r['keyword']} → {r.get('category') or '기본 카테고리'}")
    return [r["keyword"] for r in picked]


if __name__ == "__main__":
    from main import load_config
    cfg = load_config()
    n = min(int(cfg.get("autofill", {}).get("count", 0) or cfg["publish"].get("max_posts_per_day", 2)), 5)
    got = fill(cfg, n)
    if got:
        print(f"\n{len(got)}개를 keywords.csv 에 넣었어요. 메모 칸에 직접 겪은 일을 적어 두면 글이 더 좋아져요.")
