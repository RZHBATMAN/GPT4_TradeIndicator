#!/usr/bin/env python3
"""Simple backtest engine for SPX overnight iron condor signals.

Replays historical SPX + VIX1D data through Factors 1 and 2 (deterministic),
stubs Factor 3 (GPT) with a configurable fixed score, generates signals,
and compares against actual next-day SPX open moves.

Limitations:
  - Factor 3 (GPT/news, 50% weight) is stubbed — cannot replay historical news.
  - VIX1D history may be limited depending on Polygon plan.
  - Does not account for term structure (VIX1D vs VIX) in historical data.

Usage:
  python backtest.py                             # last 60 trading days, GPT=4
  python backtest.py --days 120                  # last 120 trading days
  python backtest.py --gpt-score 6               # assume elevated GPT score
  python backtest.py --start 2025-06-01 --end 2025-12-31  # specific range
  python backtest.py --sweep                     # test GPT scores 2-8
  python backtest.py --trade-days Mon,Tue,Wed,Thu # skip Fridays
"""
import argparse
import math
import sys
import time as time_module
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
import pytz

from config.loader import get_config
from signals.iv_rv_ratio import analyze_iv_rv_ratio
from signals.market_trend import analyze_market_trend
from signal_engine import calculate_composite_score, generate_signal, detect_contradictions

ET_TZ = pytz.timezone('US/Eastern')

# Same thresholds as validate_outcomes.py
MOVE_THRESHOLDS = {
    'TRADE_AGGRESSIVE': 1.00,
    'TRADE_NORMAL': 0.90,
    'TRADE_CONSERVATIVE': 0.80,
    'SKIP': 0.80,
}


def _fetch_historical_bars(ticker: str, start: str, end: str, api_key: str) -> Optional[List[dict]]:
    """Fetch daily bars from Polygon for a ticker over a date range."""
    url = (
        f"https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end}?adjusted=true&sort=asc&limit=5000&apiKey={api_key}"
    )
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('results', [])
            if resp.status_code == 429:
                time_module.sleep(2 ** attempt)
                continue
            print(f"  [Polygon] HTTP {resp.status_code} for {ticker}")
            return None
        except Exception as e:
            print(f"  [Polygon] Error fetching {ticker}: {e}")
            if attempt < 2:
                time_module.sleep(2)
    return None


def _bars_to_date_map(bars: List[dict]) -> Dict[str, dict]:
    """Convert Polygon bars to a {date_str: bar} dict."""
    result = {}
    for bar in bars:
        ts_ms = bar.get('t', 0)
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=pytz.UTC).astimezone(ET_TZ)
        date_str = dt.strftime('%Y-%m-%d')
        result[date_str] = bar
    return result


def _build_spx_data(closes_window: List[float], bar: dict) -> dict:
    """Build the spx_data dict expected by analyze_market_trend and analyze_iv_rv_ratio."""
    return {
        'current': bar['c'],
        'high_today': bar['h'],
        'low_today': bar['l'],
        'open_today': bar['o'],
        'previous_close': closes_window[0] if closes_window else bar['o'],
        'history_closes': closes_window,
    }


def _build_vix1d_data(vix1d_bar: dict) -> dict:
    """Build the vix1d_data dict expected by analyze_iv_rv_ratio."""
    return {
        'current': vix1d_bar['c'],
    }


def _stub_gpt(score: int) -> dict:
    """Create a stub GPT result with a fixed score."""
    if score <= 2:
        cat = 'VERY_QUIET'
    elif score <= 4:
        cat = 'QUIET'
    elif score <= 6:
        cat = 'MODERATE'
    elif score <= 8:
        cat = 'ELEVATED'
    else:
        cat = 'EXTREME'
    return {
        'score': score,
        'raw_score': score,
        'category': cat,
        'reasoning': f'Stubbed at {score} for backtest',
        'direction_risk': 'UNKNOWN',
        'key_risk': 'Backtest stub',
        'duplicates_found': 'N/A',
    }


WEEKDAY_MAP = {
    'mon': 0, 'monday': 0,
    'tue': 1, 'tuesday': 1,
    'wed': 2, 'wednesday': 2,
    'thu': 3, 'thursday': 3,
    'fri': 4, 'friday': 4,
}


def parse_trade_days(spec: str) -> set:
    """Parse a comma-separated weekday spec like 'Mon,Tue,Wed,Thu' into a set of ints (0=Mon..4=Fri)."""
    days = set()
    for token in spec.split(','):
        token = token.strip().lower()
        if token in WEEKDAY_MAP:
            days.add(WEEKDAY_MAP[token])
        else:
            raise ValueError(f"Unknown weekday: '{token}'. Use Mon,Tue,Wed,Thu,Fri")
    return days


def run_backtest(
    start_date: str,
    end_date: str,
    gpt_score: int = 4,
    api_key: str = '',
    verbose: bool = False,
    trade_days: Optional[set] = None,
) -> List[dict]:
    """Run backtest over a date range.

    Args:
        trade_days: Set of weekday ints to trade on (0=Mon..4=Fri). None = all weekdays.

    Returns list of daily result dicts.
    """
    if trade_days is None:
        trade_days = {0, 1, 2, 3, 4}  # Mon-Fri
    # Fetch extra days before start to have 25-day lookback for RV calc
    start_dt = datetime.strptime(start_date, '%Y-%m-%d')
    fetch_start = (start_dt - timedelta(days=50)).strftime('%Y-%m-%d')

    print(f"\nFetching SPX data {fetch_start} → {end_date} ...")
    spx_bars = _fetch_historical_bars('I:SPX', fetch_start, end_date, api_key)
    if not spx_bars:
        print("ERROR: Could not fetch SPX data")
        return []

    print(f"Fetching VIX1D data {fetch_start} → {end_date} ...")
    vix1d_bars = _fetch_historical_bars('I:VIX1D', fetch_start, end_date, api_key)
    if not vix1d_bars:
        print("ERROR: Could not fetch VIX1D data")
        return []

    spx_map = _bars_to_date_map(spx_bars)
    vix1d_map = _bars_to_date_map(vix1d_bars)

    # Sort all SPX dates
    all_spx_dates = sorted(spx_map.keys())

    # Find the index range for the backtest window
    bt_start_idx = None
    for i, d in enumerate(all_spx_dates):
        if d >= start_date:
            bt_start_idx = i
            break
    if bt_start_idx is None:
        print(f"ERROR: No SPX data on or after {start_date}")
        return []

    bt_end_idx = None
    for i in range(len(all_spx_dates) - 1, -1, -1):
        if all_spx_dates[i] <= end_date:
            bt_end_idx = i
            break
    if bt_end_idx is None:
        print(f"ERROR: No SPX data on or before {end_date}")
        return []

    results = []
    gpt_stub = _stub_gpt(gpt_score)

    day_names = {0: 'Mon', 1: 'Tue', 2: 'Wed', 3: 'Thu', 4: 'Fri'}
    trade_days_label = ','.join(day_names[d] for d in sorted(trade_days))

    print(f"\nRunning backtest: {all_spx_dates[bt_start_idx]} → {all_spx_dates[bt_end_idx]}")
    print(f"GPT score stubbed at: {gpt_score}")
    print(f"Trade days: {trade_days_label}")
    print(f"Days to simulate: {bt_end_idx - bt_start_idx + 1}")
    print("-" * 80)

    for idx in range(bt_start_idx, bt_end_idx + 1):
        date = all_spx_dates[idx]
        bar = spx_map[date]

        # Skip days not in the trading weekday set
        date_dt = datetime.strptime(date, '%Y-%m-%d')
        if date_dt.weekday() not in trade_days:
            if verbose:
                print(f"  {date}: {day_names.get(date_dt.weekday(), '?')} — not a trade day, skipping")
            continue

        # Build closes window (up to 25 days lookback)
        lookback_start = max(0, idx - 24)
        closes_window = [spx_map[all_spx_dates[j]]['c'] for j in range(idx, lookback_start - 1, -1)]
        # closes_window is now [today, yesterday, ..., 25 days ago] — most recent first

        if len(closes_window) < 12:
            # Not enough history for RV calc
            continue

        spx_data = _build_spx_data(closes_window, bar)

        # VIX1D for this day
        if date not in vix1d_map:
            if verbose:
                print(f"  {date}: No VIX1D data, skipping")
            continue
        vix1d_data = _build_vix1d_data(vix1d_map[date])

        # Run Factor 1 and Factor 2
        try:
            iv_rv = analyze_iv_rv_ratio(spx_data, vix1d_data)
        except Exception as e:
            if verbose:
                print(f"  {date}: IV/RV error: {e}")
            continue

        try:
            trend = analyze_market_trend(spx_data)
        except Exception as e:
            if verbose:
                print(f"  {date}: Trend error: {e}")
            continue

        indicators = {'iv_rv': iv_rv, 'trend': trend, 'gpt': gpt_stub}

        # Contradiction detection
        contradiction = detect_contradictions(indicators)

        # Composite score
        composite = calculate_composite_score(indicators, contradiction)

        # Signal
        signal = generate_signal(composite['score'], contradiction)

        # Next-day outcome
        next_day_date = None
        overnight_move_pct = None
        outcome = None
        if idx + 1 < len(all_spx_dates):
            next_date = all_spx_dates[idx + 1]
            next_bar = spx_map[next_date]
            spx_close = bar['c']
            spx_next_open = next_bar['o']
            overnight_move_pct = abs((spx_next_open - spx_close) / spx_close) * 100
            next_day_date = next_date

            threshold = MOVE_THRESHOLDS.get(signal['signal'], 0.80)
            if signal['signal'] == 'SKIP':
                outcome = 'CORRECT_SKIP' if overnight_move_pct >= threshold else 'WRONG_SKIP'
            else:
                outcome = 'CORRECT_TRADE' if overnight_move_pct < threshold else 'WRONG_TRADE'

        day_result = {
            'date': date,
            'spx_close': bar['c'],
            'vix1d': vix1d_data['current'],
            'iv_rv_score': iv_rv['score'],
            'iv_rv_ratio': iv_rv.get('iv_rv_ratio', 0),
            'trend_score': trend['score'],
            'change_5d': trend.get('change_5d', 0),
            'gpt_score': gpt_score,
            'composite': composite['score'],
            'category': composite['category'],
            'signal': signal['signal'],
            'should_trade': signal['should_trade'],
            'contradiction_flags': contradiction.get('contradiction_flags', []),
            'next_day': next_day_date,
            'overnight_move_pct': overnight_move_pct,
            'outcome': outcome,
        }
        results.append(day_result)

        if verbose:
            flag_str = f" [CONTRADICT: {', '.join(contradiction['contradiction_flags'])}]" if contradiction['contradiction_flags'] else ""
            move_str = f" → Move={overnight_move_pct:.3f}% {outcome}" if outcome else ""
            print(f"  {date} | SPX={bar['c']:.0f} VIX1D={vix1d_data['current']:.1f} | "
                  f"IV/RV={iv_rv['score']} Trend={trend['score']} GPT={gpt_score} | "
                  f"Comp={composite['score']:.1f} → {signal['signal']}{flag_str}{move_str}")

    return results


def print_backtest_report(results: List[dict], gpt_score: int):
    """Print a summary report of backtest results."""
    if not results:
        print("No results to report.")
        return

    # Filter to results with outcomes
    with_outcome = [r for r in results if r['outcome'] is not None]
    if not with_outcome:
        print("No outcome data available (need next-day data).")
        return

    print("\n" + "=" * 70)
    print(f"  BACKTEST REPORT  |  GPT stub = {gpt_score}")
    print(f"  Period: {results[0]['date']} → {results[-1]['date']}")
    print(f"  Trading days: {len(results)}  |  Days with outcomes: {len(with_outcome)}")
    print("=" * 70)

    # Overall
    total_correct = sum(1 for r in with_outcome if 'CORRECT' in r['outcome'])
    total = len(with_outcome)
    print(f"\n  Overall Accuracy: {total_correct}/{total} ({total_correct/total*100:.1f}%)")

    # By signal tier
    tiers = {}
    for r in with_outcome:
        sig = r['signal']
        if sig not in tiers:
            tiers[sig] = []
        tiers[sig].append(r)

    for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP']:
        entries = tiers.get(tier, [])
        if not entries:
            print(f"\n  {tier}: No occurrences")
            continue

        correct = sum(1 for e in entries if 'CORRECT' in e['outcome'])
        n = len(entries)
        moves = [e['overnight_move_pct'] for e in entries if e['overnight_move_pct'] is not None]
        avg_move = sum(moves) / len(moves) if moves else 0
        max_move = max(moves) if moves else 0

        print(f"\n  {tier}: {correct}/{n} correct ({correct/n*100:.1f}%)")
        print(f"    Threshold: {MOVE_THRESHOLDS.get(tier, '?')}%")
        print(f"    Avg overnight move: {avg_move:.3f}%")
        print(f"    Max overnight move: {max_move:.3f}%")
        pct = n / total * 100
        print(f"    Frequency: {n}/{total} ({pct:.0f}%)")

    # Trade survival
    trade_entries = [r for r in with_outcome if r['signal'] != 'SKIP']
    if trade_entries:
        survived = sum(1 for r in trade_entries if 'CORRECT' in r['outcome'])
        blown = len(trade_entries) - survived
        print(f"\n  Trade Survival Rate: {survived}/{len(trade_entries)} "
              f"({survived/len(trade_entries)*100:.1f}%)")
        print(f"  Blown Trades: {blown}")

        # Average composite for wins vs losses
        wins = [r for r in trade_entries if 'CORRECT' in r['outcome']]
        losses = [r for r in trade_entries if 'WRONG' in r['outcome']]
        if wins:
            avg_comp_win = sum(r['composite'] for r in wins) / len(wins)
            print(f"  Avg composite (wins): {avg_comp_win:.1f}")
        if losses:
            avg_comp_loss = sum(r['composite'] for r in losses) / len(losses)
            print(f"  Avg composite (losses): {avg_comp_loss:.1f}")

    # Contradiction frequency
    contradict_days = sum(1 for r in results if r['contradiction_flags'])
    print(f"\n  Contradiction days: {contradict_days}/{len(results)} "
          f"({contradict_days/len(results)*100:.1f}%)")

    # Score distribution
    print(f"\n  Composite Score Distribution:")
    buckets = {'<3.0': 0, '3.0-4.9': 0, '5.0-6.4': 0, '6.5-7.4': 0, '>=7.5': 0}
    for r in results:
        c = r['composite']
        if c < 3.0:
            buckets['<3.0'] += 1
        elif c < 5.0:
            buckets['3.0-4.9'] += 1
        elif c < 6.5:
            buckets['5.0-6.4'] += 1
        elif c < 7.5:
            buckets['6.5-7.4'] += 1
        else:
            buckets['>=7.5'] += 1
    for label, count in buckets.items():
        pct = count / len(results) * 100
        bar = '#' * int(pct / 2)
        print(f"    {label:>8}: {count:3d} ({pct:4.1f}%) {bar}")

    print("\n" + "=" * 70)


def run_gpt_sweep(start_date: str, end_date: str, api_key: str, verbose: bool = False,
                   trade_days: Optional[set] = None):
    """Sweep GPT scores 2-8 and compare accuracy."""
    print("\n" + "=" * 70)
    print("  GPT SCORE SWEEP")
    print(f"  Period: {start_date} → {end_date}")
    print("=" * 70)

    sweep_results = {}
    for gpt in range(2, 9):
        print(f"\n--- GPT={gpt} ---")
        results = run_backtest(start_date, end_date, gpt_score=gpt, api_key=api_key,
                               verbose=False, trade_days=trade_days)
        with_outcome = [r for r in results if r['outcome'] is not None]

        if not with_outcome:
            continue

        total_correct = sum(1 for r in with_outcome if 'CORRECT' in r['outcome'])
        total = len(with_outcome)
        trade_entries = [r for r in with_outcome if r['signal'] != 'SKIP']
        skip_entries = [r for r in with_outcome if r['signal'] == 'SKIP']
        trade_survived = sum(1 for r in trade_entries if 'CORRECT' in r['outcome']) if trade_entries else 0

        sweep_results[gpt] = {
            'accuracy': total_correct / total * 100,
            'total': total,
            'trades': len(trade_entries),
            'skips': len(skip_entries),
            'trade_survival': (trade_survived / len(trade_entries) * 100) if trade_entries else 0,
        }

    print("\n" + "-" * 70)
    print(f"  {'GPT':>4} | {'Accuracy':>8} | {'Trades':>6} | {'Skips':>5} | {'Trade Surv':>10} |")
    print("-" * 70)
    for gpt in sorted(sweep_results.keys()):
        r = sweep_results[gpt]
        print(f"  {gpt:>4} | {r['accuracy']:>7.1f}% | {r['trades']:>6} | {r['skips']:>5} | {r['trade_survival']:>9.1f}% |")
    print("-" * 70)
    print("\n  Interpretation:")
    print("  - Higher GPT stub → fewer trades, more skips (more conservative)")
    print("  - Lower GPT stub → more trades, higher survival rate needed")
    print("  - The 'right' GPT level depends on how often real news creates overnight risk")
    print("  - If Trade Survival is high even at GPT=2, the quantitative factors alone are strong")


def main():
    parser = argparse.ArgumentParser(description='Backtest SPX overnight iron condor signals')
    parser.add_argument('--days', type=int, default=60, help='Number of trading days to backtest (default: 60)')
    parser.add_argument('--start', type=str, help='Start date YYYY-MM-DD (overrides --days)')
    parser.add_argument('--end', type=str, help='End date YYYY-MM-DD (default: today)')
    parser.add_argument('--gpt-score', type=int, default=4, help='Fixed GPT score to stub (default: 4)')
    parser.add_argument('--sweep', action='store_true', help='Sweep GPT scores 2-8 and compare')
    parser.add_argument('--trade-days', type=str, default='Mon,Tue,Wed,Thu,Fri',
                        help='Comma-separated weekdays to trade on (default: Mon,Tue,Wed,Thu,Fri)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Print each day')
    args = parser.parse_args()

    config = get_config()
    api_key = config.get('POLYGON_API_KEY')
    if not api_key:
        print("ERROR: POLYGON_API_KEY not configured")
        sys.exit(1)

    # Parse trade days
    try:
        trade_days = parse_trade_days(args.trade_days)
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Determine date range
    if args.end:
        end_date = args.end
    else:
        end_date = datetime.now(ET_TZ).strftime('%Y-%m-%d')

    if args.start:
        start_date = args.start
    else:
        # Approximate trading days: ~1.4 calendar days per trading day
        cal_days = int(args.days * 1.4) + 10
        start_dt = datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=cal_days)
        start_date = start_dt.strftime('%Y-%m-%d')

    if args.sweep:
        run_gpt_sweep(start_date, end_date, api_key, verbose=args.verbose, trade_days=trade_days)
    else:
        results = run_backtest(
            start_date, end_date,
            gpt_score=args.gpt_score,
            api_key=api_key,
            verbose=args.verbose,
            trade_days=trade_days,
        )
        print_backtest_report(results, args.gpt_score)


if __name__ == '__main__':
    main()
