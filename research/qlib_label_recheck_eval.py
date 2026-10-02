"""标签纠错策略复验；conda resonance；研究代码不依赖运行层。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Backtester,MinuteBarProvider
from resonance.backtest import perf_stats
from research.qlib_label_recheck_common import load_inputs,complete_trades,model_post_rank,digest,OUT,END,ANCHORS


def main():
    d,m,concepts,audit=load_inputs()
    previous=json.loads((OUT/'training_audit.json').read_text())
    assert audit['input_sha256']==previous['input_sha256']
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    open_=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
    provider=MinuteBarProvider(m.pivot(index='datetime',columns='symbol',values='close').sort_index())
    predictions={f'{family}_{direction}':pd.read_parquet(OUT/f'pred_{family}_{direction}.parquet')
        for family in ['minute12','technical3'] for direction in ['inverted','forward']}
    rows=[]
    for cost in [10,0,30]:
        for phase,start in enumerate(close.index[close.index>='2026-08-03'][:5],start=1):
            for tag in ['baseline']+list(predictions):
                hook,counts=(None,{}) if tag=='baseline' else model_post_rank(predictions[tag])
                p=V3Params(topk=3,hl_source='leader',exec_price='open',cost_bp=cost,
                           daily_top=5 if tag=='baseline' else 0)
                bt=V3Backtester(close,concepts,ANCHORS,params=p,minute_bars_provider=provider,
                                post_rank=hook,open_all=open_)
                result=bt.run(start,END)
                metrics=perf_stats(result['nav_curve']);closed=complete_trades(result['trades'],open_,cost)
                rows.append({'variant':tag,'cost':cost,'phase':phase,'start':str(start.date()),
                    'end':str(END.date()),'total':metrics['total_return'],'dd':metrics['max_drawdown'],
                    'win_rate':float((closed.net_return>0).mean()) if len(closed) else np.nan,
                    'completed_trades':len(closed),'mean_trade':closed.net_return.mean(),
                    'cash_days':int(result['holdings'].holding.isna().sum()),
                    **counts,**{k:v for k,v in result['stats'].items() if isinstance(v,(int,float))}})
                if cost==10:
                    ident=f'{tag}_phase{phase}'
                    result['nav_curve'].to_csv(OUT/f'nav_{ident}.csv')
                    result['trades'].to_csv(OUT/f'trades_{ident}.csv',index=False)
                    closed.to_csv(OUT/f'closed_{ident}.csv',index=False)
        print(f'cost={cost} complete',flush=True)
    r=pd.DataFrame(rows)
    countcols=[c for c in r if c.startswith(('model_','minute_'))]
    r[countcols]=r[countcols].fillna(0)
    r.to_csv(OUT/'results.csv',index=False)
    r.groupby(['cost','variant']).median(numeric_only=True).to_csv(OUT/'medians.csv')
    pairs=[]
    for cost,sub in r.groupby('cost'):
        for tag in predictions:
            baselines=['baseline']+([tag.replace('forward','inverted')] if tag.endswith('forward') else [])
            for base in baselines:
                a=sub[sub.variant==tag].set_index('phase');b=sub[sub.variant==base].set_index('phase')
                delta=a.total-b.total
                pairs.append({'cost':cost,'candidate':tag,'baseline':base,'delta':delta.median(),
                    'wins':int((delta>0).sum()),'dd_delta':(a.dd-b.dd).median(),
                    'win_rate_delta':(a.win_rate-b.win_rate).median()})
    pd.DataFrame(pairs).to_csv(OUT/'paired_tests.csv',index=False)
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values()) and len(r)==75
    (OUT/'evaluation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(r.groupby(['cost','variant'])[['total','dd','win_rate','completed_trades']].median().to_string())
    print(pd.DataFrame(pairs).to_string(index=False))


if __name__=='__main__':main()
