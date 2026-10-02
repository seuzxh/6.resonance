"""固定双锚/三锚池交互复验；conda resonance；规则见补充预注册。"""
from pathlib import Path
import json
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.backtest import perf_stats
from resonance.v3 import V3Params, V3Backtester, yearly_returns
from research.resonance_alternatives_core import (
    RankingBank, ConsensusBank, raw_momentum, best_schedule, capped_schedule,
    adjusted_momentum, occupancy_stats,
)
from research.resonance_alternatives_run import (
    ANCHORS, BASE_POOLS, WINDOWS, END, load_data, digest, pool_schedule,
    assert_executable, normalize_counts, paired, converge, trade_hash,
)


def main():
    out = Path("outputs/resonance_alternatives/pool_interaction")
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    close, open_, concepts, provider, audit = load_data(Path("/home/zxh/projects/6.resonance/data"))
    original_results = pd.read_csv(out.parent / "results.csv")
    rows, years, selections, contributions, convergence = [], [], [], [], []
    audit["equivalence"] = []
    for mode in ["production_shape", "daily_only"]:
        p = V3Params(daily_top=5 if mode == "production_shape" else 0, hl_source="leader",
                     topk=3, cost_bp=10, exec_price="open")
        engines = {c: V3Backtester(close, concepts, [c], params=p,
                                   minute_bars_provider=provider, open_all=open_) for c in ANCHORS}
        bank = RankingBank(engines)
        daily = {c: [engine.sig.ranking(i) for i in range(len(close))] for c, engine in engines.items()}
        schedules, consensus = {}, {}
        for pool in ["dual", "trio"]:
            codes = BASE_POOLS[pool]
            primary = V3Backtester(close, concepts, codes, params=p,
                                   minute_bars_provider=provider, open_all=open_)
            schedules[pool] = pool_schedule(primary)
            for w in ([10, 20, 40, 60] if mode == "production_shape" else [20]):
                schedules[f"{pool}_risk{w}"] = best_schedule(adjusted_momentum(close[codes], w))
            for cap in ([.4, .6, .8] if mode == "production_shape" else [.6]):
                selected = capped_schedule(raw_momentum(close[codes]), cap)
                assert all(selected.eq(c).rolling(60, min_periods=1).sum().max() <= cap * 60 + 1e-10
                           for c in codes)
                schedules[f"{pool}_cap{int(100*cap)}"] = selected
            ks = [2] if pool == "dual" else ([2, 3] if mode == "production_shape" else [3])
            for k in ks:
                consensus[f"{pool}_consensus{k}"] = ConsensusBank(
                    {c: engines[c] for c in codes}, primary, k, daily)
            original = primary.run("2022-06-01", END)
            replay = bank.run(schedules[pool], "2022-06-01", END, 10)
            pd.testing.assert_series_equal(original["nav_curve"], replay["nav_curve"])
            pd.testing.assert_frame_equal(original["trades"], replay["trades"])
            audit["equivalence"].append(f"{mode}:{pool}")
        for tag, selected in schedules.items():
            selections.append(pd.DataFrame({"date": selected.index, "anchor": selected,
                                             "variant": tag, "mode": mode}))
        for tag, instance in consensus.items():
            weights = instance.weights.copy().reindex(columns=ANCHORS, fill_value=0.)
            weights["variant"], weights["mode"] = tag, mode
            contributions.append(weights.rename_axis("date").reset_index())
        tags = list(schedules) + list(consensus)
        trades = {tag: [] for tag in tags}
        for cost in ([10, 0, 30] if mode == "production_shape" else [10]):
            for window, (begin, end) in WINDOWS.items():
                for phase, start in enumerate(close.index[close.index >= begin][:5], start=1):
                    for tag in tags:
                        if tag in consensus:
                            result = consensus[tag].run(start, end, cost)
                            concentration = consensus[tag].contribution_stats(start, end)
                        else:
                            selected = schedules[tag]
                            result = bank.run(selected, start, end, cost)
                            concentration = occupancy_stats(selected.loc[start:end])
                        assert_executable(result, close, open_)
                        curve = result["nav_curve"]
                        metrics = perf_stats(curve)
                        row = {"mode": mode, "window": window, "phase": phase, "cost": cost,
                               "variant": tag, "start": str(start.date()), "end": str(end.date()),
                               "total": metrics["total_return"], "dd": metrics["max_drawdown"],
                               "sharpe": metrics["sharpe"], "trade_hash": trade_hash(result["trades"]),
                               "cash_days": int(result["holdings"].holding.isna().sum()), **concentration}
                        row.update({k: v for k, v in result["stats"].items() if isinstance(v, (int, float))})
                        if tag in BASE_POOLS:
                            prior = original_results[(original_results["mode"] == mode) &
                                (original_results.window == window) & (original_results.phase == phase) &
                                (original_results.cost == cost) & (original_results.variant == tag)]
                            if len(prior):
                                np.testing.assert_allclose([row["total"], row["dd"]],
                                    prior[["total", "dd"]].iloc[0].to_numpy(float), rtol=1e-12, atol=1e-12)
                        rows.append(row)
                        for year, value in yearly_returns(curve).items():
                            years.append({"mode": mode, "window": window, "cost": cost, "phase": phase,
                                          "variant": tag, "year": year, "total": value})
                        if cost == 10:
                            ident = f"{mode}_{window}_{tag}_phase{phase}"
                            curve.to_csv(out / f"nav_{ident}.csv")
                            result["trades"].to_csv(out / f"trades_{ident}.csv", index=False)
                            if window == "main":
                                trades[tag].append(result["trades"])
                print(f"{len(rows)}/975 {mode} cost={cost} {window}, {time.monotonic()-started:.0f}s", flush=True)
            normalize_counts(pd.DataFrame(rows)).to_csv(out / "results.csv", index=False)
        for tag, frames in trades.items():
            convergence.append({"mode": mode, "variant": tag, "last_trade_disagreement": converge(frames)})
    results = normalize_counts(pd.DataFrame(rows))
    assert len(results) == 975
    results.to_csv(out / "results.csv", index=False)
    results.groupby(["mode", "window", "cost", "variant"]).median(numeric_only=True).to_csv(out / "medians.csv")
    pairs = paired(results)
    # Repeat the same pairing calculation using trio as the named baseline, without changing results.
    trio_comparison = results[results.variant.str.startswith("trio")].copy()
    trio_comparison["variant"] = trio_comparison.variant.replace({"trio": "dual"})
    own_pairs = paired(trio_comparison)
    own_pairs["baseline"] = "trio"
    pairs = pd.concat([pairs, own_pairs], ignore_index=True)
    pairs.to_csv(out / "paired_tests.csv", index=False)
    pd.DataFrame(years).to_csv(out / "years.csv", index=False)
    pd.DataFrame(convergence).to_csv(out / "phase_convergence.csv", index=False)
    pd.concat(selections, ignore_index=True).to_csv(out / "selections.csv", index=False)
    pd.concat(contributions, ignore_index=True).to_csv(out / "contributions.csv", index=False)
    audit["actual_runs"] = len(rows)
    audit["elapsed_seconds"] = time.monotonic() - started
    audit["input_unchanged"] = {k: digest(Path(p)) == audit["input_sha256"][k]
                                 for k, p in audit["input_paths"].items()}
    assert all(audit["input_unchanged"].values())
    (out / "data_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    print(pairs[(pairs.cost == 10) & (pairs.window == "main") & (pairs.baseline == "dual")].to_string(index=False))


if __name__ == "__main__":
    main()
