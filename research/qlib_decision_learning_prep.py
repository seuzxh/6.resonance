"""conda resonance 导出原榜、12项日线特征及可成交标签。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Backtester,MinuteBarProvider
from research.qlib_label_recheck_common import load_inputs,digest,ANCHORS,END
from research.qlib_decision_learning_common import OUT,FEATURES,open_target


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,m,concepts,audit=load_inputs()
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    opens=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
    provider=MinuteBarProvider(m.pivot(index='datetime',columns='symbol',values='close').sort_index())
    bt=V3Backtester(close,concepts,ANCHORS,params=V3Params(hl_source='leader',exec_price='open',daily_top=5),
                    minute_bars_provider=provider,open_all=opens)
    returns={n:close.pct_change(n,fill_method=None).where(close.rolling(n+1).count()==n+1) for n in [3,5,10,20]}
    vol=close.pct_change(fill_method=None).rolling(20).std(ddof=1)
    dd=close.rolling(11).apply(lambda a: -np.min(a/np.maximum.accumulate(a)-1),raw=True)
    target=open_target(opens);rows=[];ranks=[];counts=[]
    for i,date in enumerate(close.index):
        daily=bt.sig.ranking(i);st={};ranking=bt._final_ranking(i,date,st)
        counts.append({'date':date,**st})
        if not ranking.empty:ranks.append(ranking.assign(date=date,order=np.arange(len(ranking))))
        if ranking.empty:continue
        concept=ranking.iloc[0].concept;anchor=bt.broad[bt.sig.leader_idx[i]]
        selected=daily.set_index('concept').loc[concept]
        row={'date':date,'concept':concept,'anchor':anchor,'daily_count':len(daily),
             'ret5':returns[5].at[date,concept],'ret20':returns[20].at[date,concept],
             'relative10':returns[10].at[date,concept]-returns[10].at[date,anchor],
             'vol20':vol.at[date,concept],'dd10':dd.at[date,concept],
             'anchor3':returns[3].at[date,anchor],'anchor10':returns[10].at[date,anchor],
             'anchor_dd10':dd.at[date,anchor],'score':selected.score,'sync':selected.sync,
             'capture':np.clip(selected.capture,0,2),
             'score_gap':daily.iloc[0].score-daily.iloc[1].score if len(daily)>1 else np.nan,
             'target':target.at[date,concept],
             'maturity':close.index[i+6] if i+6<len(close) else pd.NaT}
        rows.append(row)
    frame=pd.DataFrame(rows)
    frame['feature_valid']=np.isfinite(frame[FEATURES].to_numpy(float)).all(axis=1)&(frame.daily_count>=2)
    frame.to_parquet(OUT/'samples.parquet',index=False)
    pd.concat(ranks,ignore_index=True).to_parquet(OUT/'rankings.parquet',index=False)
    pd.DataFrame(counts).fillna(0).to_parquet(OUT/'ranking_counts.parquet',index=False)
    pd.DataFrame({'date':close.index}).to_parquet(OUT/'calendar.parquet',index=False)
    audit.update(eligible_dates=len(frame),complete_features=int(frame.feature_valid.sum()),
                 labeled_dates=int(frame.target.notna().sum()),feature_names=FEATURES,
                 sample_sha256=digest(OUT/'samples.parquet'))
    audit['bridge_sha256']={name:digest(OUT/name) for name in
        ['samples.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet']}
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values())
    (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(audit,flush=True)


if __name__=='__main__':main()
