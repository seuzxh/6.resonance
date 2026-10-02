"""研究环境：原榜每个候选的12项特征与完整日目标。"""
from pathlib import Path
import sys,json,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Signals
from research.qlib_label_recheck_common import load_inputs,digest,ANCHORS
from research.qlib_decision_learning_common import OUT as PRIOR,FEATURES,open_target
from research.qlib_candidate_rerank_common import OUT


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,m,concepts,audit=load_inputs();old=json.loads((PRIOR/'preparation_audit.json').read_text())
    assert old['input_sha256']==audit['input_sha256']
    assert all(digest(PRIOR/name)==value for name,value in old['bridge_sha256'].items())
    for name in ['rankings.parquet','ranking_counts.parquet','calendar.parquet']:shutil.copy2(PRIOR/name,OUT/name)
    ranks=pd.read_parquet(OUT/'rankings.parquet')
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    opens=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
    sig=V3Signals(close,concepts,ANCHORS,'883957.TI',V3Params(hl_source='leader'))
    returns={n:close.pct_change(n,fill_method=None).where(close.rolling(n+1).count()==n+1) for n in [3,5,10,20]}
    vol=close.pct_change(fill_method=None).rolling(20).std(ddof=1)
    dd=close.rolling(11).apply(lambda a: -np.min(a/np.maximum.accumulate(a)-1),raw=True)
    target=open_target(opens);rows=[]
    for date,g in ranks.groupby('date'):
        i=close.index.get_loc(date);daily=sig.ranking(i);lookup=daily.set_index('concept');anchor=ANCHORS[sig.leader_idx[i]]
        for concept in g.sort_values('order').concept:
            selected=lookup.loc[concept]
            rows.append({'date':date,'concept':concept,'anchor':anchor,'candidate_count':len(g),
                'ret5':returns[5].at[date,concept],'ret20':returns[20].at[date,concept],
                'relative10':returns[10].at[date,concept]-returns[10].at[date,anchor],
                'vol20':vol.at[date,concept],'dd10':dd.at[date,concept],
                'anchor3':returns[3].at[date,anchor],'anchor10':returns[10].at[date,anchor],
                'anchor_dd10':dd.at[date,anchor],'score':selected.score,'sync':selected.sync,
                'capture':np.clip(selected.capture,0,2),
                'score_gap':daily.iloc[0].score-daily.iloc[1].score if len(daily)>1 else np.nan,
                'target':target.at[date,concept],'maturity':close.index[i+6] if i+6<len(close) else pd.NaT})
    frame=pd.DataFrame(rows)
    frame['feature_valid']=np.isfinite(frame[FEATURES].to_numpy(float)).all(axis=1)&frame.candidate_count.ge(2)
    frame['feature_valid']=frame.groupby('date').feature_valid.transform('all')
    frame['label_valid']=np.isfinite(frame.target)
    frame['label_valid']=frame.groupby('date').label_valid.transform('all')
    frame['relative_target']=(frame.target-frame.groupby('date').target.transform('mean')).where(frame.label_valid)
    frame.to_parquet(OUT/'samples.parquet',index=False)
    audit.update(rows=len(frame),dates=frame.date.nunique(),feature_dates=frame.loc[frame.feature_valid,'date'].nunique(),
        candidates_per_day=frame.groupby('date').size().value_counts().to_dict())
    audit['bridge_sha256']={name:digest(OUT/name) for name in ['samples.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet']}
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values())
    (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(audit,flush=True)


if __name__=='__main__':main()
