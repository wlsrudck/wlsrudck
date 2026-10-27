"""키워드 자동 채우기: keywords.csv 에 쓸 키워드가 떨어지면 프로그램이 알아서 골라 넣는다.

- 평생 키워드(꾸준히 검색되는 것)와 지금 뜨는 키워드를 반반 섞는다 (설정: [autofill] mix)
  mix 에 season 을 넣으면 이번 달·다음 달 시즌 키워드도 섞는다 (고객센터 블로그 기본: 내 블로그 빈틈 1 + 평생 1 + 시즌 1)
  mix 에 gaps 를 넣으면 이미 쓴 글 전체를 보고 이어 쓸 글·같은 업종 안 쓴 회사를 고른다
  mix 에 news 를 넣으면 네이버 뉴스 많이 본·댓글 많은 기사에서 고른 생활·돈 글감도 섞는다
- S·A 등급, 이미 쓴 글과 겹치지 않는 것, 가사·쇼핑·이슈성 말이 없는 것만
- 지금 뜨는 키워드는 연예 쪽보다 생활·경제·IT 쪽을 먼저 고른다
- 발행은 하지 않는다. 고른 키워드로 글을 써서 임시저장까지만 (발행은 사람이 확인 후)

따로 실행: python autofill.py  (13_autofill_keywords.bat)
"""

from evergreen_keywords import ISSUE_WORDS, find_until_enough, random_plan, seeds_from
from keyword_finder import load_keys
from trending_keywords import kw_key

ENTERTAINMENT = "연예"


def _ok(r: dict) -> bool:
    return r.get("grade") in ("S", "A") and not r.get("written") and not ISSUE_WORDS.search(r["keyword"])


def evergreen_candidates(cfg: dict, keys: dict) -> list[dict]:
    for _ in range(2):  # 씨앗을 바꿔 가며 (한 번에 여러 차례 더 찾는다)
        try:
            seeds = seeds_from(cfg)
            rows = find_until_enough(cfg, keys, seeds, random_plan(seeds))
        except Exception as e:
            print(f"  (평생 키워드 찾기 실패: {str(e).splitlines()[0][:80]})")
            return []
        good = [r for r in rows if _ok(r) and r["kind"] in ("평생", "평생?")]
        if not good:  # S·A 가 없으면 '빈 손' 대신 B 중에서 글이 가장 덜 쓰인 것 (검색 1건당 글 수가 적은 순)
            good = sorted((r for r in rows if r.get("grade") == "B" and not r.get("written")
                           and not ISSUE_WORDS.search(r["keyword"]) and r["kind"] in ("평생", "평생?", "시즌")),
                          key=lambda r: (r.get("docs") or 10 ** 9) / max(r["volume"], 1))[:10]
            if good:
                print(f"  S·A 등급이 없어서 B 등급 중 글이 가장 덜 쓰인 {len(good)}개를 후보로 써요")
        if good:
            from evergreen_keywords import cs_rank, is_cs
            if is_cs(cfg):  # 고객센터: '회사+고객센터' 틀은 빼고(이미 넘침), 업종이 번갈아 나오게
                good = spread([r for r in good if cs_rank(r) < 2]) or good
            return good
    return []


def season_candidates(cfg: dict, keys: dict) -> list[dict]:
    """이번 달·다음 달에 검색이 몰리는 시즌 키워드 (고객센터 블로그는 문의·신청 위주). 지금 검색이 조금은 있는 것만"""
    import datetime as dt

    from evergreen_keywords import is_cs
    from keyword_finder import blog_doc_count, grade, monthly_volumes, season_seeds
    from trending_keywords import already_written, written_titles
    seeds = season_seeds(dt.date.today(), is_cs(cfg))
    try:
        vols, _ = monthly_volumes(seeds, keys)
    except Exception as e:
        print(f"  (시즌 키워드 검색량 확인 실패: {str(e).splitlines()[0][:60]})")
        return []
    titles = written_titles(cfg["naver"]["blog_id"])
    out = []
    for k in seeds:
        vol, comp = vols.get(k) or (None, "")
        if vol is None or vol < 100 or comp == "높음":
            continue
        docs = None
        if keys.get("search_client_id"):
            try:
                docs = blog_doc_count(k, keys)
            except Exception:
                docs = None
        g = grade(vol, docs, comp)
        g = g if g in ("S", "A") else "시즌"  # 시즌 전에는 검색량이 낮게 나오므로 B·C 도 '시즌'으로 받는다
        if not already_written(k, titles) and not (docs and docs > vol * 30):  # 글이 너무 많은 것은 뺀다
            out.append({"keyword": k, "volume": vol, "comp": comp, "grade": g, "kind": "시즌", "category": "", "seed": k})
    return out


def _gaps(cfg: dict, keys: dict) -> list[dict]:
    """내 블로그 빈틈: 이미 쓴 글 전체를 보고 이어 쓸 글·아직 안 다룬 같은 업종 회사 (검색량 S·A 만)"""
    try:
        from keyword_finder import gap_candidates
        return gap_candidates(cfg, keys)
    except Exception as e:
        print(f"  (빈틈 찾기 실패: {str(e).splitlines()[0][:80]})")
        return []


def spread(rows: list[dict]) -> list[dict]:
    """같은 씨앗(업종)끼리 연달아 오지 않게 번갈아 세운다 (은행 → 택배 → 렌탈 → 은행 …)"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("seed") or r.get("category") or "", []).append(r)
    out = []
    while any(groups.values()):
        for g in list(groups):
            if groups[g]:
                out.append(groups[g].pop(0))
    return out


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
    from keyword_finder import money_rank
    return sorted(good, key=lambda r: money_rank(r["keyword"]))  # 같은 '지금 뜨는' 안에서 돈이 걸린 것 먼저


def news_candidates(cfg: dict) -> list[dict]:
    """네이버 뉴스 많이 본·댓글 많은 기사에서 고른 생활·돈 글감 (하루 한 번 읽고 저장해 둔 것을 쓴다)"""
    try:
        from news_trends import find_news
        from keyword_finder import money_rank
        return sorted([r for r in find_news(cfg) if r["grade"] == "A" and not r["written"]],
                      key=lambda r: money_rank(r["keyword"]))
    except Exception as e:
        print(f"  (뉴스 글감 읽기 실패: {str(e).splitlines()[0][:80]})")
        return []


def hot_issue(cfg: dict) -> str:
    """이슈 속도전: 오늘 뉴스에서 뜬 생활·돈 글감 하나를 키워드 목록 '맨 앞'에 넣는다 (매인 블로그, 하루 1개).
    지원금·요금·제도 변화 같은 이슈는 먼저 쓴 글이 홈판을 가져가므로, 쌓아 둔 키워드가 다 떨어질 때까지 기다리지 않는다.
    끄려면 config.toml [autofill] 에 hot_issue = false"""
    import datetime as dt
    import json
    from pathlib import Path

    from evergreen_keywords import is_cs
    if is_cs(cfg) or cfg.get("shopping", {}).get("enabled") or not cfg.get("autofill", {}).get("hot_issue", True):
        return ""
    mark = Path(__file__).parent / "output" / "이슈_우선.json"
    today = str(dt.date.today())
    try:
        if json.loads(mark.read_text(encoding="utf-8")).get("date") == today:
            return ""  # 오늘은 이미 하나 넣었다
    except Exception:
        pass
    from main import load_rows, save_rows
    rows = load_rows()
    cands = news_candidates(cfg)
    if not cands:
        return ""
    from trending_keywords import covered_by_posts
    judged = covered_by_posts([r["keyword"] for r in cands[:10]], cfg)
    have = {kw_key(r["keyword"]) for r in rows}
    pick = next((r for r in cands if r["keyword"] not in judged and kw_key(r["keyword"]) not in have), None)
    if not pick:
        return ""
    save_rows([{"keyword": pick["keyword"], "memo": question_memo(pick["keyword"]), "status": "",
                "category": pick.get("category", "")}] + rows)
    mark.parent.mkdir(exist_ok=True)
    mark.write_text(json.dumps({"date": today, "keyword": pick["keyword"]}, ensure_ascii=False), encoding="utf-8")
    print(f"⚡ 오늘의 이슈를 맨 앞에 넣었어요: {pick['keyword']} ({pick.get('why', '')}) — 먼저 쓴 글이 홈판을 가져가요")
    return pick["keyword"]


def question_memo(keyword: str) -> str:
    """메모 칸에 '궁금한 점: ...' 을 적는다. 네이버 자동완성(사람들이 실제로 이어서 검색하는 말)에서 3개.
    경험이 아니라 질문이므로 글에 경험처럼 쓰이지 않는다. 직접 겪은 일이 있으면 이 메모를 지우고 적으면 된다."""
    from generate import QUESTION_PREFIX, naver_suggest
    try:
        base = keyword.replace(" ", "")
        qs = [q for q in naver_suggest(keyword, 10) if q.replace(" ", "") != base][:3]
    except Exception:
        qs = []
    return f"{QUESTION_PREFIX} " + ", ".join(qs) if qs else ""


def fill(cfg: dict, need: int) -> list[str]:
    """need 개를 골라 keywords.csv 에 넣고, 넣은 키워드 목록을 돌려준다"""
    from main import load_rows, save_rows
    acfg = cfg.get("autofill", {})
    from evergreen_keywords import is_cs
    raw = cfg.get("cs", {}).get("mix", "gaps,evergreen,season") if is_cs(cfg) else acfg.get("mix", "evergreen,trending")
    mix = [m.strip() for m in str(raw).split(",") if m.strip()]
    # 매인 블로그: 설정 파일의 비율에 '내 블로그 빈틈'을 하나 더 섞는다 (잘 된 글의 후속 글이 자동으로 나오게)
    # 끄려면 config.toml [autofill] 에 gaps = false
    if not is_cs(cfg) and acfg.get("gaps", True) and "gaps" not in mix:
        mix.append("gaps")
    keys = load_keys()
    if not (keys.get("ad_access_license") and keys.get("ad_secret_key") and keys.get("ad_customer_id")):
        print("  키워드 자동 채우기: naver_keys.txt 에 검색광고 API 키가 없어서 건너뛰어요.")
        return []
    print(f"\n키워드가 떨어져서 {need}개를 자동으로 골라요 ({' + '.join(mix)})")
    # 각 출처에서 한 번씩만 찾아 두고, 번갈아 하나씩 뽑는다 (한쪽이 모자라면 다른 쪽에서 채운다)
    pools = {}
    for m in dict.fromkeys(mix):  # "trending,trending,evergreen" 처럼 같은 걸 두 번 적으면 그 비율(2:1)로 섞는다
        pools[m] = (evergreen_candidates(cfg, keys) if m == "evergreen" else news_candidates(cfg) if m == "news"
                    else season_candidates(cfg, keys) if m == "season" else _gaps(cfg, keys) if m == "gaps"
                    else trending_candidates(cfg))
    # 글자 규칙이 놓친 것까지: 내 글 제목 전체를 보고 '이미 답한 키워드'를 사람처럼 한 번 더 거른다
    from trending_keywords import covered_by_posts
    judged = covered_by_posts([r["keyword"] for rs in pools.values() for r in rs[:15]], cfg)
    if judged:
        print(f"  이미 쓴 글이 답하는 키워드 {len(judged)}개를 더 뺐어요 (예: "
              + ", ".join(f"{k}→{v[:15]}" for k, v in list(judged.items())[:3]) + ")")
        pools = {m: [r for r in rs if r["keyword"] not in judged] for m, rs in pools.items()}
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
    save_rows(load_rows() + [{"keyword": r["keyword"], "memo": question_memo(r["keyword"]), "status": "",
                              "category": r.get("category", "")} for r in picked])
    for r in picked:
        kind = r.get("kind") or "지금 뜨는"
        print(f"  + [{r['grade']}] [{kind}] {r['keyword']} → {r.get('category') or '기본 카테고리'}")
    print("  (메모 칸에는 사람들이 이어서 검색하는 '궁금한 점'을 적어 뒀어요. 직접 겪은 일이 있으면 바꿔 적어 주세요)")
    return [r["keyword"] for r in picked]


def add_question_memos() -> int:
    """아직 안 쓴 줄 중 메모가 빈 곳에 '궁금한 점'을 채운다"""
    from main import load_rows, save_rows
    rows, n = load_rows(), 0
    for r in rows:
        if not r["status"].strip() and not r["memo"].strip():
            r["memo"] = question_memo(r["keyword"])
            n += bool(r["memo"])
    if n:
        save_rows(rows)
    return n


if __name__ == "__main__":
    from main import load_config, load_rows
    from shop_categories import broadcast_overrides
    cfg = broadcast_overrides(load_config())
    n = min(int(cfg.get("autofill", {}).get("count", 0) or cfg["publish"].get("max_posts_per_day", 2)), 5)
    pending = [r for r in load_rows() if not r["status"].strip()]
    if len(pending) < n:
        got = fill(cfg, n - len(pending))
        if got:
            print(f"\n{len(got)}개를 keywords.csv 에 넣었어요.")
    else:
        print(f"아직 안 쓴 키워드가 {len(pending)}개 있어서 새로 채우지 않았어요.")
    m = add_question_memos()
    if m:
        print(f"메모가 빈 키워드 {m}개에 '궁금한 점'을 적었어요.")
