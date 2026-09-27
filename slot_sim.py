"""3릴 x 3행 슬롯머신 RNG 시뮬레이터 (와일드 · 스캐터 · 프리스핀).

릴 스트립, 페이테이블, 페이라인을 정의하고
1) 모든 릴 정지 조합을 전수 계산해서 '이론 RTP / 적중률 / 표준편차'를 구하고
2) NumPy로 수천만 번 스핀해서 실측값을 구한 뒤 둘을 비교하고
3) 결과 그래프를 PNG로 저장한다.

사용법:
    pip install numpy matplotlib koreanize-matplotlib
    python slot_sim.py                        # 기본 10,000,000 스핀 + 그래프
    python slot_sim.py --spins 100000000 --seed 42
    python slot_sim.py --no-plots
"""

import argparse
import itertools
import math
import random
import time
from collections import Counter

import numpy as np

# ---------------------------------------------------------------------------
# 1. 게임 정의
# ---------------------------------------------------------------------------

# 릴 스트립: 각 릴이 멈출 수 있는 칸을 순서대로 적은 것.
# 같은 심볼을 여러 칸에 넣으면 그만큼 '가중치'가 커진다.
# (실제 기계의 '가상 릴(virtual reel)'이 이 방식)
REEL_STRIP = (
    ["7"] * 1
    + ["WILD"] * 1
    + ["SCATTER"] * 2
    + ["BAR"] * 3
    + ["BELL"] * 5
    + ["CHERRY"] * 6
    + ["LEMON"] * 7
    + ["BLANK"] * 7
)
REELS = [REEL_STRIP[:] for _ in range(3)]
# 릴마다 순서를 섞어 두면 심볼이 한곳에 몰리지 않는다(고정 시드 = 항상 같은 릴).
for i, reel in enumerate(REELS):
    random.Random(1000 + i).shuffle(reel)
REEL_SIZES = [len(r) for r in REELS]

ROWS = 3

# 페이라인: 각 릴에서 몇 번째 행(0=위, 1=가운데, 2=아래)을 읽을지.
PAYLINES = [
    (1, 1, 1),  # 가운데 가로
    (0, 0, 0),  # 위 가로
    (2, 2, 2),  # 아래 가로
    (0, 1, 2),  # 대각선 ↘
    (2, 1, 0),  # 대각선 ↗
]

# 페이테이블: 라인당 베팅 1 기준 배당. WILD 3개는 자체 배당.
# (이 값 + 아래 스캐터/프리스핀 설정으로 이론 RTP 약 96.3%)
PAYTABLE_3OAK = {
    "WILD": 1000,
    "7": 500,
    "BAR": 75,
    "BELL": 18,
    "CHERRY": 7,
    "LEMON": 3,
}
# 체리는 왼쪽부터 1개, 2개만 나와도 조금 준다.
CHERRY_LEFT = {1: 1, 2: 2}

# 스캐터: 페이라인과 무관하게, 스캐터가 보이는 '릴 수'로 판정. 총 베팅 기준 배당.
SCATTER_PAY = {2: 1, 3: 5}
FREE_SPINS_TRIGGER = 3   # 스캐터 3개 → 프리스핀
FREE_SPINS_AWARD = 10    # 프리스핀 횟수
FREE_SPINS_MULT = 2      # 프리스핀 중 당첨금 배수 (프리스핀 중 재트리거 가능)

BET_PER_LINE = 1
TOTAL_BET = BET_PER_LINE * len(PAYLINES)


# ---------------------------------------------------------------------------
# 2. 한 번의 스핀을 평가하는 로직 (순수 파이썬, 명확성 우선)
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
    """한 줄(심볼 3개)의 당첨금. WILD는 스캐터를 제외한 모든 배당 심볼을 대신한다."""
    best = 0
    others = {s for s in symbols if s != "WILD"}
    if not others:
        best = PAYTABLE_3OAK["WILD"]
    elif len(others) == 1:
        (sym,) = others
        best = PAYTABLE_3OAK.get(sym, 0)  # SCATTER/BLANK는 표에 없으므로 0
    # 체리 왼쪽 연속 개수 (WILD는 체리로 치지 않음)
    count = 0
    for s in symbols:
        if s != "CHERRY":
            break
        count += 1
    best = max(best, CHERRY_LEFT.get(count, 0))
    return best * BET_PER_LINE


def evaluate(stops):
    """정지 위치 조합 하나 → (라인 당첨금, 스캐터 당첨금, 프리스핀 트리거 여부)."""
    window = visible_window(stops)
    lines = sum(
        line_win([window[r][row] for r, row in enumerate(line)])
        for line in PAYLINES
    )
    scatters = sum("SCATTER" in col for col in window)
    scatter = SCATTER_PAY.get(scatters, 0) * TOTAL_BET
    return lines, scatter, scatters >= FREE_SPINS_TRIGGER


def build_tables():
    """모든 정지 조합(32^3 = 32,768가지)을 한 번씩 평가해 조회 테이블을 만든다.
    이후 시뮬레이션은 난수로 인덱스만 뽑아서 이 테이블을 읽는다."""
    shape = tuple(REEL_SIZES)
    line_t = np.zeros(shape, dtype=np.int64)
    scat_t = np.zeros(shape, dtype=np.int64)
    trig_t = np.zeros(shape, dtype=bool)
    for stops in itertools.product(*(range(n) for n in REEL_SIZES)):
        line_t[stops], scat_t[stops], trig_t[stops] = evaluate(stops)
    return line_t.ravel(), scat_t.ravel(), trig_t.ravel()


LINE_T, SCAT_T, TRIG_T = build_tables()
WIN_T = LINE_T + SCAT_T
COMBOS = WIN_T.size


# ---------------------------------------------------------------------------
# 3. 이론값: 전수 계산 + 프리스핀 기대값 공식
# ---------------------------------------------------------------------------

def exact_stats():
    """베이스 스핀 한 번의 당첨금 w, 트리거 t(0/1)의 분포는 전수 계산으로 정확히 안다.

    프리스핀 한 세트의 총 당첨금을 V라 하면, 한 프리스핀의 당첨금은
        Y = M*w + t*V'     (M = 배수, V' = 재트리거로 생긴 새 세트)
    이고 V는 Y를 K번(K = 프리스핀 수) 더한 것이므로
        E[V]   = K*(M*E[w] + P*E[V])           → E[V] = K*M*E[w] / (1 - K*P)
        E[V^2] = K*E[Y^2] + K(K-1)*E[Y]^2       (같은 식으로 E[V^2]에 대해 풂)
    유료 스핀 한 번의 총 당첨금 T = w + t*V.
    """
    K, M = FREE_SPINS_AWARD, FREE_SPINS_MULT
    w = WIN_T.astype(float)
    t = TRIG_T.astype(float)
    Ew, Ew2, Ewt, P = w.mean(), (w * w).mean(), (w * t).mean(), t.mean()
    if K * P >= 1:
        raise ValueError("재트리거 확률이 너무 높아 프리스핀 기대값이 발산합니다.")

    EV = K * M * Ew / (1 - K * P)
    EY = M * Ew + P * EV
    # E[Y^2] = M^2 E[w^2] + 2M E[wt] E[V] + P E[V^2]
    # E[V^2] = K E[Y^2] + K(K-1) EY^2  →  E[V^2] 에 대해 정리
    EV2 = (K * (M * M * Ew2 + 2 * M * Ewt * EV) + K * (K - 1) * EY ** 2) / (1 - K * P)

    ET = Ew + P * EV
    ET2 = Ew2 + 2 * Ewt * EV + P * EV2
    return {
        "rtp": ET / TOTAL_BET,
        "rtp_line": LINE_T.mean() / TOTAL_BET,
        "rtp_scatter": SCAT_T.mean() / TOTAL_BET,
        "rtp_free": P * EV / TOTAL_BET,
        "hit_freq": (WIN_T > 0).mean(),
        "trigger_p": P,
        "feature_ev": EV / TOTAL_BET,
        "sd": math.sqrt(ET2 - ET ** 2) / TOTAL_BET,
    }


# ---------------------------------------------------------------------------
# 4. NumPy 몬테카를로 시뮬레이션
# ---------------------------------------------------------------------------

_STRIDES = np.array(
    [REEL_SIZES[1] * REEL_SIZES[2], REEL_SIZES[2], 1], dtype=np.int64
)


def draw(rng, n):
    """RNG가 하는 일은 이게 전부: 각 릴의 정지 위치를 균등하게 뽑는다."""
    stops = rng.integers(0, REEL_SIZES, size=(n, 3))
    return stops @ _STRIDES  # (릴1, 릴2, 릴3) → 조회 테이블 인덱스


def play_features(rng, n):
    """프리스핀 n세트를 동시에 진행하고 세트별 총 당첨금을 돌려준다(재트리거 포함)."""
    total = np.zeros(n, dtype=np.int64)
    remaining = np.full(n, FREE_SPINS_AWARD, dtype=np.int64)
    active = np.arange(n)
    while active.size:
        idx = draw(rng, active.size)
        total[active] += FREE_SPINS_MULT * WIN_T[idx]
        remaining[active] += FREE_SPINS_AWARD * TRIG_T[idx] - 1
        active = active[remaining[active] > 0]
    return total


def play(rng, n):
    """유료 스핀 n번 → 스핀별 총 당첨금(프리스핀 보너스 포함)."""
    idx = draw(rng, n)
    wins = WIN_T[idx].copy()
    trig = np.flatnonzero(TRIG_T[idx])
    if trig.size:
        wins[trig] += play_features(rng, trig.size)
    return wins, trig.size


# 당첨금 구간 (베팅 대비 배수)
BUCKETS = [(0, 0), (0, 1), (1, 2), (2, 5), (5, 20), (20, 100), (100, math.inf)]
BUCKET_LABELS = ["꽝\n(0배)", "0~1배\n(손해)", "1~2배", "2~5배", "5~20배", "20~100배", "100배+"]


def bucket_index(mult):
    """0 → 0번 구간, (lo, hi] → 나머지 구간."""
    edges = np.array([0, 1, 2, 5, 20, 100], dtype=float)
    return np.where(mult <= 0, 0, np.searchsorted(edges, mult, side="left"))


def simulate(spins, seed, chunk=5_000_000):
    rng = np.random.default_rng(seed)  # PCG64
    total_win = total_sq = hits = features = 0
    max_win = 0
    bucket_n = np.zeros(len(BUCKETS), dtype=np.int64)
    bucket_w = np.zeros(len(BUCKETS), dtype=np.int64)
    # 수렴 그래프용: 로그 간격 체크포인트에서의 누적 RTP
    checkpoints = np.unique(np.logspace(2, math.log10(spins), 200).astype(np.int64))
    curve = []
    done = 0
    while done < spins:
        n = min(chunk, spins - done)
        wins, feat = play(rng, n)
        cum = np.cumsum(wins) + total_win
        in_chunk = checkpoints[(checkpoints > done) & (checkpoints <= done + n)]
        curve += [(c, cum[c - done - 1] / (c * TOTAL_BET)) for c in in_chunk]

        total_win += int(wins.sum())
        total_sq += float((wins.astype(float) ** 2).sum())
        hits += int((wins > 0).sum())
        features += feat
        max_win = max(max_win, int(wins.max()))
        b = bucket_index(wins / TOTAL_BET)
        bucket_n += np.bincount(b, minlength=len(BUCKETS))
        bucket_w += np.bincount(b, weights=wins, minlength=len(BUCKETS)).astype(np.int64)
        done += n

    mean = total_win / spins
    return {
        "rtp": total_win / (spins * TOTAL_BET),
        "hit_freq": hits / spins,
        "sd": math.sqrt(total_sq / spins - mean ** 2) / TOTAL_BET,
        "max_win": max_win,
        "features": features,
        "curve": np.array(curve),
        "bucket_p": bucket_n / spins,
        "bucket_rtp": bucket_w / (spins * TOTAL_BET),
    }


def sessions(spins_per_session, n_sessions, seed, start_credits=500):
    """플레이어 관점: start_credits로 시작해 최대 N스핀. 베팅할 돈이 없으면 파산.
    반환: (세션 × 스핀) 잔고 행렬 (파산 이후는 그 값으로 고정)."""
    rng = np.random.default_rng(seed)
    wins, _ = play(rng, spins_per_session * n_sessions)
    net = wins.reshape(n_sessions, spins_per_session) - TOTAL_BET
    bal = start_credits + np.cumsum(net, axis=1)
    broke = bal < TOTAL_BET
    busted = broke.any(axis=1)
    first = np.where(busted, broke.argmax(axis=1), spins_per_session - 1)
    # 파산한 시점 이후 잔고를 고정
    cols = np.arange(spins_per_session)
    freeze = cols[None, :] > first[:, None]
    bal = np.where(freeze, bal[np.arange(n_sessions), first][:, None], bal)
    return bal, busted


def session_summary(bal, busted, start_credits=500):
    final = bal[:, -1]
    return {
        "ahead": (final > start_credits).mean(),
        "busted": busted.mean(),
        "median": int(np.median(final)),
    }


# ---------------------------------------------------------------------------
# 5. 그래프
# ---------------------------------------------------------------------------

# 차트 팔레트 (라이트 모드, 검증된 범주형 1~3번 슬롯)
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
GRID = "#e4e3df"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
AQUA = "#1baf7a"


def _style(ax, title, subtitle=None):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", color=TEXT, fontsize=13, fontweight="bold", pad=22)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes, color=TEXT_2, fontsize=9.5)


def make_plots(ex, sim, bal, out_dir, spins):
    import os

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, PercentFormatter
    try:
        import koreanize_matplotlib  # noqa: F401  (한글 폰트)
    except ImportError:
        print("  (koreanize-matplotlib 미설치: 한글이 깨질 수 있음)")

    os.makedirs(out_dir, exist_ok=True)
    paths = []

    def save(fig, name):
        path = os.path.join(out_dir, name)
        fig.savefig(path, dpi=150, facecolor=SURFACE, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)

    # (1) RTP 수렴: 스핀 수가 늘면 실측 RTP가 이론값으로 모인다
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=SURFACE)
    n = sim["curve"][:, 0]
    band = 1.96 * ex["sd"] / np.sqrt(n)
    ax.fill_between(n, ex["rtp"] - band, ex["rtp"] + band, color=BLUE, alpha=0.12,
                    linewidth=0, label="95% 범위 (이론)")
    ax.axhline(ex["rtp"], color=TEXT_2, linestyle="--", linewidth=1.2,
               label=f"이론 RTP {ex['rtp']:.2%}")
    ax.plot(n, sim["curve"][:, 1], color=BLUE, linewidth=2, label="실측 누적 RTP")
    ax.set_xscale("log")
    ax.set_ylim(max(0, ex["rtp"] - 0.6), ex["rtp"] + 0.6)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("누적 스핀 수 (로그 스케일)", color=TEXT_2)
    _style(ax, "스핀을 많이 돌릴수록 RTP는 이론값으로 수렴한다",
           f"{spins:,}스핀 시뮬레이션 · 초반 수백~수천 스핀은 운이 지배")
    ax.legend(frameon=False, labelcolor=TEXT_2, fontsize=9, loc="upper right")
    save(fig, "rtp_convergence.png")

    # (2) 당첨금 분포: 스핀 대부분은 손해, RTP는 드문 큰 당첨이 만든다
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=SURFACE)
    x = np.arange(len(BUCKETS))
    wdt = 0.38
    bars1 = ax.bar(x - wdt / 2 - 0.01, sim["bucket_p"], wdt, color=BLUE, label="스핀 비율")
    bars2 = ax.bar(x + wdt / 2 + 0.01, sim["bucket_rtp"], wdt, color=ORANGE,
                   label="RTP에 기여한 몫 (베팅 대비)")
    for bars in (bars1, bars2):
        for b in bars:
            h = b.get_height()
            if h >= 0.03:
                ax.text(b.get_x() + b.get_width() / 2, h + 0.008, f"{h:.0%}",
                        ha="center", va="bottom", fontsize=8.5, color=TEXT)
    ax.set_xticks(x, BUCKET_LABELS)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("스핀 한 번의 당첨금 (베팅 대비 배수, 프리스핀 포함)", color=TEXT_2)
    _style(ax, "대부분의 스핀은 잃고, RTP는 드문 큰 당첨이 채운다",
           f"꽝+손해 스핀 {sim['bucket_p'][:2].sum():.0%} · "
           f"20배 이상은 스핀의 {sim['bucket_p'][5:].sum():.2%}지만 "
           f"RTP의 {sim['bucket_rtp'][5:].sum() / sim['rtp']:.0%}를 차지")
    ax.legend(frameon=False, labelcolor=TEXT_2, fontsize=9, loc="upper right")
    save(fig, "win_distribution.png")

    # (3) 플레이어 잔고: 중앙값과 10~90% 범위
    fig, ax = plt.subplots(figsize=(9, 5), facecolor=SURFACE)
    steps = np.arange(1, bal.shape[1] + 1)
    p10, p50, p90 = np.percentile(bal, [10, 50, 90], axis=0)
    start = 500
    ax.fill_between(steps, p10, p90, color=BLUE, alpha=0.15, linewidth=0,
                    label="플레이어 80% 범위 (10~90%)")
    ax.plot(steps, p50, color=BLUE, linewidth=2, label="중앙값")
    ax.axhline(start, color=TEXT_2, linestyle="--", linewidth=1.2, label="시작 잔고 500")
    ax.set_xlim(1, steps[-1])
    ax.set_ylim(0, None)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.set_xlabel("스핀 수 (스핀당 5크레딧)", color=TEXT_2)
    ax.set_ylabel("잔고 (크레딧)", color=TEXT_2)
    _style(ax, "시간이 지날수록 거의 모든 플레이어의 잔고가 0으로 간다",
           f"{bal.shape[0]:,}명 시뮬레이션 · {bal.shape[1]:,}스핀 후 "
           f"이득 {(bal[:, -1] > start).mean():.1%}, 파산 {(bal[:, -1] < TOTAL_BET).mean():.1%}")
    ax.legend(frameon=False, labelcolor=TEXT_2, fontsize=9, loc="upper right")
    save(fig, "bankroll.png")

    return paths


# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="슬롯머신 RNG 시뮬레이터")
    p.add_argument("--spins", type=int, default=10_000_000)
    p.add_argument("--seed", type=int, default=2026)
    p.add_argument("--plots", default="plots", help="그래프 저장 폴더")
    p.add_argument("--no-plots", action="store_true")
    args = p.parse_args()

    print("=== 게임 구성 ===")
    print(f"릴 길이: {REEL_SIZES}, 페이라인: {len(PAYLINES)}개, 스핀당 베팅: {TOTAL_BET}")
    for sym, cnt in Counter(REEL_STRIP).most_common():
        print(f"  {sym:<8}{cnt:>2}/{len(REEL_STRIP)} (한 칸 {cnt / len(REEL_STRIP):6.2%})")
    print(f"스캐터 {FREE_SPINS_TRIGGER}개 → 프리스핀 {FREE_SPINS_AWARD}회 "
          f"(당첨금 x{FREE_SPINS_MULT}, 재트리거 가능)")

    ex = exact_stats()
    print(f"\n=== 이론값 (전수 계산 {COMBOS:,}가지 + 프리스핀 공식) ===")
    print(f"RTP             : {ex['rtp']:.4%}  (하우스 엣지 {1 - ex['rtp']:.4%})")
    print(f"  ├ 라인 당첨   : {ex['rtp_line']:.4%}")
    print(f"  ├ 스캐터 당첨 : {ex['rtp_scatter']:.4%}")
    print(f"  └ 프리스핀    : {ex['rtp_free']:.4%}")
    print(f"적중률          : {ex['hit_freq']:.4%} (약 {1 / ex['hit_freq']:.1f}스핀에 1번)")
    print(f"프리스핀 발동   : 1/{1 / ex['trigger_p']:,.0f}스핀, "
          f"평균 {ex['feature_ev']:.1f}배 지급")
    print(f"표준편차(베팅당): {ex['sd']:.3f}")

    print(f"\n=== 시뮬레이션 ({args.spins:,}스핀, seed={args.seed}) ===")
    t = time.time()
    sim = simulate(args.spins, args.seed)
    elapsed = time.time() - t
    ci = 1.96 * ex["sd"] / math.sqrt(args.spins)
    print(f"RTP             : {sim['rtp']:.4%}  (이론값 ±{ci:.2%} 안이면 정상)")
    print(f"적중률          : {sim['hit_freq']:.4%}")
    print(f"프리스핀 발동   : {sim['features']:,}회 (1/{args.spins / max(sim['features'], 1):,.0f})")
    print(f"표준편차(베팅당): {sim['sd']:.3f}")
    print(f"최대 당첨       : {sim['max_win']:,} 크레딧 ({sim['max_win'] / TOTAL_BET:,.0f}배)")
    print(f"소요 시간       : {elapsed:.1f}s ({args.spins / elapsed / 1e6:.1f}M 스핀/초)")

    print("\n=== 플레이어 세션 (500크레딧 시작, 각 2,000명) ===")
    long_bal = None
    for n in (100, 1_000, 10_000):
        bal, busted = sessions(n, 2_000, args.seed + n)
        s = session_summary(bal, busted)
        print(f"{n:>6}스핀 후: 이득 {s['ahead']:5.1%}, 파산 {s['busted']:5.1%}, "
              f"중앙값 {s['median']} 크레딧")
        long_bal = bal

    if not args.no_plots:
        print("\n=== 그래프 ===")
        for path in make_plots(ex, sim, long_bal, args.plots, args.spins):
            print(f"  {path}")


if __name__ == "__main__":
    main()
