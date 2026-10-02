"""固定规则的统计汇总与展示；conda resonance，不增加实验。"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path('outputs/resonance_alternatives')
CENTERS = {
    'risk20': ['risk10', 'risk40'], 'consensus3': ['consensus2', 'consensus4'],
    'cap60': ['cap40', 'cap80'],
    'dual_risk20': ['dual_risk10', 'dual_risk40'], 'dual_consensus2': [],
    'dual_cap60': ['dual_cap40', 'dual_cap80'],
    'trio_risk20': ['trio_risk10', 'trio_risk40'], 'trio_consensus3': ['trio_consensus2'],
    'trio_cap60': ['trio_cap40', 'trio_cap80'],
}


def summarize():
    verdicts, checks = [], {}
    first_inputs = json.loads((ROOT / 'data_audit.json').read_text())['input_sha256']
    for subdir, count in [('', 735), ('pool_interaction', 975)]:
        path = ROOT / subdir
        r = pd.read_csv(path / 'results.csv')
        p = pd.read_csv(path / 'paired_tests.csv')
        audit = json.loads((path / 'data_audit.json').read_text())
        assert len(r) == count and not r.duplicated(['mode','window','cost','phase','variant']).any()
        assert r.groupby(['mode','window','cost','variant']).size().eq(5).all()
        assert np.isfinite(r[['total','dd']]).all().all()
        assert all(audit['input_unchanged'].values())
        assert audit['input_sha256'] == first_inputs
        tags = [t for t in r.variant.unique() if t not in ['dual','trio','single','momentum5']]
        d = p[(p['mode']=='production_shape') & (p.baseline=='dual')]
        for tag in tags:
            rows = d[d.candidate == tag].set_index(['window','cost'])
            main = rows.loc[('main',10)]
            gates = {
                'return_pass': bool(main.delta >= .02), 'phase_pass': bool(main.wins >= 4),
                'drawdown_pass': bool(main.dd_delta >= -.01),
                'early_pass': bool(rows.loc[('early',10)].delta >= -.02),
                'recent_pass': bool(rows.loc[('recent',10)].delta >= -.02),
                'cost_pass': bool(rows.loc[('main',30)].delta > 0),
            }
            own_base = 'dual' if tag.startswith('dual_') else ('trio' if tag.startswith('trio_') else 'momentum5')
            own = p[(p['mode']=='production_shape') & (p.window=='main') & (p.cost==10)
                    & (p.candidate==tag) & (p.baseline==own_base)].iloc[0]
            concentration_pass = bool(own.occupancy_median_delta <= -.10 + 1e-12) if 'consensus' not in tag else None
            verdicts.append({'variant': tag, 'center': tag in CENTERS, **gates,
                             'performance_pass': all(gates.values()), 'main_delta': main.delta,
                             'main_wins': main.wins, 'main_dd_delta': main.dd_delta,
                             'occupancy_improvement_pass': concentration_pass})
        for tag in [t for t in r.variant.unique() if 'cap' in t]:
            cap = int(tag.split('cap')[-1])/100
            assert r.loc[r.variant.eq(tag), 'max_rolling_occupancy'].max() <= cap + 1e-12
        checks[subdir or 'first_round'] = {'rows': count, 'equivalence': audit['equivalence'],
                                         'input_unchanged': audit['input_unchanged']}
    v = pd.DataFrame(verdicts).set_index('variant')
    for tag, neighbors in CENTERS.items():
        v.loc[tag, 'neighbor_pass'] = any(bool(v.loc[n, 'performance_pass']) for n in neighbors)
        v.loc[tag, 'eligible_for_next_validation'] = bool(v.loc[tag, 'performance_pass']) and bool(
            v.loc[tag, 'neighbor_pass'])
        if 'consensus' not in tag:
            v.loc[tag, 'eligible_for_next_validation'] = bool(v.loc[tag, 'eligible_for_next_validation']) and bool(
                v.loc[tag, 'occupancy_improvement_pass'])
    v.to_csv(ROOT / 'verdicts.csv')
    checks['rule_variants'] = len(v)
    checks['centers'] = len(CENTERS)
    checks['passing_centers'] = int(v.eligible_for_next_validation.fillna(False).astype(bool).sum())
    (ROOT / 'verification.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2))
    print(v.to_string())


def selection_effect():
    """事后解释性账单核对，不用来重新选参数。"""
    path = ROOT / 'pool_interaction'
    s = pd.read_csv(path / 'selections.csv', parse_dates=['date'])
    s = s[s['mode']=='production_shape'].pivot(index='date', columns='variant', values='anchor')
    s = s.loc['2022-06-06':'2026-09-18']
    source = json.loads((ROOT / 'data_audit.json').read_text())['input_paths']['daily']
    bars = pd.read_parquet(source, columns=['date', 'symbol', 'close'])
    bars['date'] = pd.to_datetime(bars.date)
    bars = bars[bars.symbol.isin(['399001.SZ','000852.SH']) & (bars.date <= pd.Timestamp('2026-09-18'))]
    calendar = sorted(bars.loc[bars.symbol=='399001.SZ','date'])
    close = bars.pivot(index='date', columns='symbol', values='close').reindex(calendar)
    gates = (close.pct_change(3, fill_method=None)>0).reindex(s.index)
    rows = []
    def trades(tag):
        t = pd.read_csv(path/f'trades_production_shape_main_{tag}_phase3.csv').fillna('')
        return {x['date']: tuple(x.get(k) for k in ['type','from','to','price']) for x in t.to_dict('records')}
    original = trades('dual')
    for tag in ['dual_cap40','dual_cap60','dual_cap80','dual_risk20']:
        different = s[tag].fillna('') != s['dual'].fillna('')
        def gate(series):
            return pd.Series([bool(gates.at[d,c]) if pd.notna(c) else False for d,c in series.items()], index=s.index)
        old, new = gate(s['dual']), gate(s[tag])
        changed = trades(tag)
        rows.append({'variant':tag, 'different_selection_days':int(different.sum()),
                     'both_gate_closed':int((different & ~old & ~new).sum()),
                     'either_gate_open':int((different & (old | new)).sum()),
                     'no_anchor':int(s[tag].isna().sum()),
                     'different_trade_dates':sum(original.get(d)!=changed.get(d) for d in set(original)|set(changed))})
    pd.DataFrame(rows).to_csv(path/'selection_effect_phase3.csv',index=False)


def plot():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.ticker import FuncFormatter
    for font in font_manager.fontManager.ttflist:
        if 'WenQuanYi' in font.name or 'CJK' in font.name:
            plt.rcParams['font.family'] = font.name
            plt.rcParams['font.weight'] = 500
            break
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(3,1, figsize=(14,12), sharex=True,
                             gridspec_kw={'height_ratios':[2.5,2.5,1.4]})
    groups = [('', [('dual','原双锚','#d39924'),('momentum5','原五指数动选','#aaaaaa'),
                    ('risk20','五指数：风险调整动量20日','#397bc5'),
                    ('consensus3','五指数：最多3锚共同排序','#9670bd'),
                    ('cap60','五指数：60%硬上限','#ce5e68')]),
              ('pool_interaction', [('dual','原双锚','#d39924'),
                    ('dual_risk20','双锚：风险调整动量20日','#397bc5'),
                    ('dual_consensus2','双锚：共同排序','#9670bd'),
                    ('dual_cap60','双锚：60%硬上限','#ce5e68')])]
    for ax, (folder, variants) in zip(axes[:2], groups):
        for tag, label, color in variants:
            frame = pd.read_csv(ROOT / folder / f'nav_production_shape_main_{tag}_phase3.csv', index_col=0, parse_dates=True)
            ax.plot(frame.index,frame.iloc[:,0],label=label,color=color,lw=1.4)
        ax.set_yscale('log')
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x,_:f'{x:g}'))
        ax.yaxis.set_minor_formatter(FuncFormatter(lambda x,_:f'{x:g}'))
        ax.set_ylabel('净值（对数）')
        ax.legend(ncol=2,fontsize=9)
    selections = pd.read_csv(ROOT / 'pool_interaction/selections.csv', parse_dates=['date'])
    for tag, label, color in [('dual','原双锚','#d39924'),('dual_cap60','双锚60%硬上限','#ce5e68')]:
        s = selections[(selections['mode']=='production_shape')&(selections.variant==tag)].set_index('date').anchor
        s = s.loc['2022-06-06':'2026-09-18']
        occ = pd.DataFrame({c:s.eq(c).rolling(60,min_periods=1).sum()/60 for c in s.dropna().unique()}).max(axis=1)
        axes[2].plot(occ.index,occ,label=label,color=color)
    axes[2].axhline(.6,color='#777777',ls='--',lw=.8)
    axes[2].set_ylabel('当日最大\n60日锚占用率')
    axes[2].legend(ncol=2,fontsize=9)
    for ax in axes: ax.grid(alpha=.18)
    fig.suptitle('替代机制验证｜预定第3相位：2022-06-06至2026-09-18\n'
                 '次日开盘、单边10个基点、样本内上界；正式判据使用5相位配对', fontsize=13)
    fig.text(.5,.012,'概念指数不可直接交易；成本为统一压力假设；存活概念目录存在幸存者偏差。',ha='center',fontsize=10)
    fig.tight_layout(rect=(0,.035,1,.95))
    fig.savefig(ROOT/'nav_comparison.png',dpi=150)
    plt.close(fig)


if __name__ == '__main__':
    summarize()
    selection_effect()
    plot()
