"""3릴 x 3행 슬롯머신 RNG 시뮬레이터.

릴 스트립, 페이테이블, 페이라인을 정의하고
1) 모든 릴 정지 조합을 전수 계산해서 '이론 RTP'를 구하고
2) 난수로 N번 스핀해서 '실측 RTP', 적중률, 변동성을 구한 뒤 둘을 비교한다.

사용법:
    python slot_sim.py                  # 기본 1,000,000 스핀
    python slot_sim.py --spins 5000000 --seed 42
"""

import argparse
import itertools
import math
import random
import time
from collections import Counter

# ---------------------------------------------------------------------------
# 1. 게임 정의
# ---------------------------------------------------------------------------

# 릴 스트립: 각 릴이 멈출 수 있는 칸을 순서대로 적은 것.
# 같은 심볼을 여러 칸에 넣으면 그만큼 '가중치'가 커진다.
# (실제 기계의 '가상 릴(virtual reel)'이 이 방식)
REEL_STRIP = (
    ["7"] * 1
    + ["BAR"] * 3
    + ["BELL"] * 5
    + ["CHERRY"] * 6
    + ["LEMON"] * 8
    + ["BLANK"] * 9
)
REELS = [REEL_STRIP[:] for _ in range(3)]
# 릴마다 순서를 섞어 두면 심볼이 한곳에 몰리지 않는다(고정 시드 = 항상 같은 릴).
for i, reel in enumerate(REELS):
    random.Random(1000 + i).shuffle(reel)

ROWS = 3

# 페이라인: 각 릴에서 몇 번째 행(0=위, 1=가운데, 2=아래)을 읽을지.
PAYLINES = [
    (1, 1, 1),  # 가운데 가로
    (0, 0, 0),  # 위 가로
    (2, 2, 2),  # 아래 가로
    (0, 1, 2),  # 대각선 ↘
    (2, 1, 0),  # 대각선 ↗
]

# 페이테이블: 라인당 베팅 1 기준 배당. (이 값으로 이론 RTP 약 95.2%)
PAYTABLE_3OAK = {
    "7": 2000,
    "BAR": 200,
    "BELL": 45,
    "CHERRY": 20,
    "LEMON": 10,
}
# 체리는 왼쪽부터 1개, 2개만 나와도 조금 준다.
CHERRY_LEFT = {1: 1, 2: 4}

BET_PER_LINE = 1
TOTAL_BET = BET_PER_LINE * len(PAYLINES)


# ---------------------------------------------------------------------------
# 2. 한 번의 스핀을 평가하는 로직
# ---------------------------------------------------------------------------

def visible_window(stops):
    """각 릴의 정지 위치(stops)로 화면에 보이는 3x3 그리드를 만든다.
    window[reel][row]"""
    window = []
    for reel, stop in zip(REELS, stops):
        n = len(reel)
        window.append([reel[(stop + row - 1) % n] for row in range(ROWS)])
    return window


def line_win(symbols):
    """한 줄(심볼 3개)의 당첨금을 계산한다."""
    a, b, c = symbols
    if a == b == c and a in PAYTABLE_3OAK:
        return PAYTABLE_3OAK[a] * BET_PER_LINE
    # 체리 왼쪽 연속 개수
    count = 0
    for s in symbols:
        if s != "CHERRY":
            break
        count += 1
    return CHERRY_LEFT.get(count, 0) * BET_PER_LINE


def spin_win(stops):
    """정지 위치 조합 하나에 대한 총 당첨금."""
    window = visible_window(stops)
    total = 0
    for line in PAYLINES:
        total += line_win([window[r][row] for r, row in enumerate(line)])
    return total


# ---------------------------------------------------------------------------
# 3. 이론값: 모든 정지 조합을 전수 계산
# ---------------------------------------------------------------------------

def exact_stats():
    """릴 정지 위치는 각각 균등분포이므로, 모든 조합을 다 돌려 보면
    정확한 RTP/적중률/분산을 얻을 수 있다. (32^3 = 32,768가지)"""
    sizes = [len(r) for r in REELS]
    combos = math.prod(sizes)
    total = total_sq = hits = 0
    dist = Counter()
    for stops in itertools.product(*(range(n) for n in sizes)):
        w = spin_win(stops)
        total += w
        total_sq += w * w
        dist[w] += 1
        if w > 0:
            hits += 1
    mean = total / combos
    var = total_sq / combos - mean ** 2
    return {
        "combos": combos,
        "rtp": mean / TOTAL_BET,
        "hit_freq": hits / combos,
        "sd": math.sqrt(var) / TOTAL_BET,  # 베팅 1단위당 표준편차
        "dist": dist,
    }


# ---------------------------------------------------------------------------
# 4. 몬테카를로 시뮬레이션
# ---------------------------------------------------------------------------

def simulate(spins, seed):
    rng = random.Random(seed)  # 재현 가능하도록 시드 고정 (Mersenne Twister)
    sizes = [len(r) for r in REELS]
    total_bet = total_win = total_sq = hits = 0
    max_win = 0
    for _ in range(spins):
        stops = [rng.randrange(n) for n in sizes]  # ← RNG가 하는 일은 이게 전부
        w = spin_win(stops)
        total_bet += TOTAL_BET
        total_win += w
        total_sq += w * w
        if w > 0:
            hits += 1
        if w > max_win:
            max_win = w
    mean = total_win / spins
    var = total_sq / spins - mean ** 2
    return {
        "rtp": total_win / total_bet,
        "hit_freq": hits / spins,
        "sd": math.sqrt(var) / TOTAL_BET,
        "max_win": max_win,
    }


def session_outcomes(spins_per_session, sessions, seed, start_credits=500):
    """플레이어 관점: 500크레딧으로 N스핀 했을 때 결과 분포."""
    rng = random.Random(seed)
    sizes = [len(r) for r in REELS]
    ahead = busted = 0
    finals = []
    for _ in range(sessions):
        credits = start_credits
        for _ in range(spins_per_session):
            if credits < TOTAL_BET:
                busted += 1
                break
            credits -= TOTAL_BET
            credits += spin_win([rng.randrange(n) for n in sizes])
        finals.append(credits)
        if credits > start_credits:
            ahead += 1
    finals.sort()
    return {
        "ahead": ahead / sessions,
        "busted": busted / sessions,
        "median": finals[len(finals) // 2],
    }


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="슬롯머신 RNG 시뮬레이터")
    p.add_argument("--spins", type=int, default=1_000_000)
    p.add_argument("--seed", type=int, default=2026)
    args = p.parse_args()

    print("=== 게임 구성 ===")
    print(f"릴 길이: {[len(r) for r in REELS]}, 페이라인: {len(PAYLINES)}개, "
          f"스핀당 베팅: {TOTAL_BET}")
    weights = Counter(REEL_STRIP)
    for sym, cnt in weights.most_common():
        print(f"  {sym:<7} {cnt:>2}/{len(REEL_STRIP)} "
              f"(한 칸에 나올 확률 {cnt / len(REEL_STRIP):.3%})")

    ex = exact_stats()
    print(f"\n=== 이론값 (전수 계산 {ex['combos']:,}가지) ===")
    print(f"RTP            : {ex['rtp']:.4%}")
    print(f"하우스 엣지     : {1 - ex['rtp']:.4%}")
    print(f"적중률          : {ex['hit_freq']:.4%} (약 {1 / ex['hit_freq']:.1f}스핀에 1번)")
    print(f"표준편차(베팅당): {ex['sd']:.3f}")
    print("당첨금 분포 (상위):")
    for w, c in sorted(ex["dist"].items(), reverse=True)[:6]:
        print(f"  {w:>5} 크레딧 : 1/{ex['combos'] / c:,.0f}")

    print(f"\n=== 시뮬레이션 ({args.spins:,}스핀, seed={args.seed}) ===")
    t = time.time()
    sim = simulate(args.spins, args.seed)
    ci = 1.96 * ex["sd"] / math.sqrt(args.spins)
    print(f"RTP            : {sim['rtp']:.4%}  (95% 신뢰구간 ±{ci:.2%})")
    print(f"적중률          : {sim['hit_freq']:.4%}")
    print(f"표준편차(베팅당): {sim['sd']:.3f}")
    print(f"최대 당첨       : {sim['max_win']} 크레딧")
    print(f"소요 시간       : {time.time() - t:.1f}s")

    print("\n=== 플레이어 세션 (500크레딧 시작, 각 2,000세션) ===")
    for n in (100, 1_000, 10_000):
        s = session_outcomes(n, 2_000, args.seed + n)
        print(f"{n:>6}스핀 후: 이득 {s['ahead']:.1%}, 파산 {s['busted']:.1%}, "
              f"중앙값 {s['median']} 크레딧")


if __name__ == "__main__":
    main()
