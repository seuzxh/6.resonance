"""conda resonance：原引擎连续回放候选重排矩阵。"""
from pathlib import Path
import sys,json,copy,types
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Backtester,MinuteBarProvider
from resonance.backtest import perf_stats
from research.constituent_breadth_inputs import load_inputs,complete_trades,digest,ANCHORS,END
from research.qlib_decision_learning_common import EntryChecks
from research.qlib_candidate_rerank_common import hook
from research.constituent_breadth_common import OUT


def cached_engine(base,ranks,counts,gate,cost):
    bt=copy.copy(base);bt.p=base.p.with_(cost_bp=cost)
    bt._gate_idx=set(gate.index);bt._gate_blocked=EntryChecks(gate.index[~gate])
    empty=base.sig.ranking(0)
    def ranking(self,i,date,st):
        for key,value in counts.get(date,{}).items():st[key]=st.get(key,0)+int(value)
        return ranks.get(date,empty)
    bt._final_ranking=types.MethodType(ranking,bt)
    return bt


def main():
    d,m,concepts,audit=load_inputs()
    prep=json.loads((OUT/'preparation_audit.json').read_text())
    assert audit['input_sha256']==prep['input_sha256']
    assert all(digest(OUT/name)==value for name,value in prep['bridge_sha256'].items())
    training=json.loads((OUT/'training_audit.json').read_text())
    assert training['bridge_sha256']==prep['bridge_sha256']
    assert digest(OUT/'predictions.parquet')==training['prediction_sha256']
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    opens=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
    mb=MinuteBarProvider(m.pivot(index='datetime',columns='symbol',values='close').sort_index())
    base=V3Backtester(close,concepts,ANCHORS,params=V3Params(hl_source='leader',exec_price='open',daily_top=5,cost_bp=10),
                      minute_bars_provider=mb,open_all=opens)
    rawranks=pd.read_parquet(OUT/'rankings.parquet')
    ranks={pd.Timestamp(date):g.sort_values('order').drop(columns=['date','order']).reset_index(drop=True) for date,g in rawranks.groupby('date')}
    counts=pd.read_parquet(OUT/'ranking_counts.parquet').set_index('date').to_dict('index')
    pred=pd.read_parquet(OUT/'predictions.parquet')
    tables={f'{name}_w{weight}':(g,weight/100) for name,g in pred.groupby('model') for weight in [25,50,75]}
    alltrue=pd.Series(True,index=close.index)
    original=base.run('2023-06-01',END)
    replay=cached_engine(base,ranks,counts,alltrue,10).run('2023-06-01',END)
    for key in ['nav_curve','holdings','trades','stops']:
        if isinstance(original[key],pd.Series):pd.testing.assert_series_equal(original[key],replay[key])
        else:pd.testing.assert_frame_equal(original[key],replay[key])
    # Cache records all defined minute counters, including zero-valued ones.
    for key in set(original['stats'])|set(replay['stats']):
        assert original['stats'].get(key,0)==replay['stats'].get(key,0),(key,original['stats'].get(key),replay['stats'].get(key))
    audit['cached_replay_exact']=True;print('cached replay exact',flush=True)
    rows=[];navs=[];holdings=[];trades=[];closed_all=[]
    windows={'main':('2023-06-01',END),'early':('2023-06-01',pd.Timestamp('2024-12-31')),
             'recent':('2025-01-02',END)}
    for cost in [10,0,30]:
        for window,(first,last) in windows.items():
            for phase,start in enumerate(close.index[close.index>=first][:5],1):
                for tag in ['baseline']+list(tables):
                    table=tables.get(tag)
                    gate=alltrue
                    bt=cached_engine(base,ranks,counts,gate,cost)
                    extra={}
                    if table is not None:bt.post_rank,extra=hook(*table)
                    result=bt.run(start,last)
                    nav=result['nav_curve'];h=result['holdings'];closed=complete_trades(result['trades'],opens,cost)
                    for date,code in h.holding.items():
                        if pd.notna(code):assert np.isfinite(close.at[date,code]) and close.at[date,code]>0
                    checks=[day for day in bt._gate_blocked.checked if day<last and day in ranks and len(ranks[day])>0]
                    permitted=sum(bool(gate.at[day]) for day in checks)
                    metrics=perf_stats(nav);positives=closed.net_return[closed.net_return>0];negatives=closed.net_return[closed.net_return<0]
                    row={'cost':cost,'window':window,'phase':phase,'variant':tag,'start':str(start.date()),'end':str(last.date()),
                        'total':metrics['total_return'],'dd':metrics['max_drawdown'],
                        'win_rate':float((closed.net_return>0).mean()),'completed_trades':len(closed),
                        'zero_trades':int((closed.net_return==0).sum()),'unclosed':int(pd.notna(h.holding.iloc[-1])),
                        'payoff_ratio':positives.mean()/(-negatives.mean()),'mean_trade':closed.net_return.mean(),
                        'exposure':float(h.holding.notna().mean()),'entry_opportunities':len(checks),'allowed':permitted,
                        'retention':permitted/len(checks) if checks else np.nan,
                        **{k:v for k,v in result['stats'].items() if isinstance(v,(int,float))}}
                    row.update(extra)
                    rows.append(row)
                    if cost==10:
                        ids={'window':window,'phase':phase,'variant':tag}
                        navs.append(nav.rename('nav').reset_index().assign(**ids))
                        holdings.append(h[['holding']].reset_index().assign(**ids))
                        trades.append(result['trades'].assign(**ids))
                        closed_all.append(closed.assign(**ids))
            print(f'cost={cost} window={window} complete',flush=True)
    results=pd.DataFrame(rows)
    results.to_csv(OUT/'results.csv',index=False)
    results.groupby(['cost','window','variant']).median(numeric_only=True).to_csv(OUT/'medians.csv')
    for name,frames in [('navs',navs),('holdings',holdings),('trades',trades),('closed',closed_all)]:
        pd.concat(frames,ignore_index=True).to_parquet(OUT/f'{name}.parquet',index=False)
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert len(results)==training["expected_replays"] and all(audit['input_unchanged'].values())
    assert all(digest(OUT/name)==value for name,value in prep['bridge_sha256'].items())
    assert digest(OUT/'predictions.parquet')==training['prediction_sha256']
    (OUT/'evaluation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(len(results),'runs completed',flush=True)


if __name__=='__main__':main()
