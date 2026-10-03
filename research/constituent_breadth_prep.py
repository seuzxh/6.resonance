"""双锚独立生成原榜和12特征；conda resonance。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Backtester,MinuteBarProvider
from research.constituent_breadth_inputs import load_inputs,digest,ANCHORS
from research.qlib_decision_learning_common import open_target
from research.constituent_breadth_common import OUT,BASE_FEATURES,membership_date,all_candidates_valid


def main():
 OUT.mkdir(parents=True,exist_ok=True)
 d,m,concepts,audit=load_inputs();close=d.pivot(index='date',columns='symbol',values='close').sort_index();opens=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
 bt=V3Backtester(close,concepts,ANCHORS,params=V3Params(hl_source='leader',exec_price='open',daily_top=5),minute_bars_provider=MinuteBarProvider(m.pivot(index='datetime',columns='symbol',values='close').sort_index()),open_all=opens)
 returns={n:close.pct_change(n,fill_method=None).where(close.rolling(n+1).count()==n+1) for n in [3,5,10,20]}
 vol=close.pct_change(fill_method=None).rolling(20).std(ddof=1);dd=close.rolling(11).apply(lambda a:-np.min(a/np.maximum.accumulate(a)-1),raw=True)
 target=open_target(opens);rows=[];ranks=[];counts=[];jobs=set()
 for i,date in enumerate(close.index):
  daily=bt.sig.ranking(i);st={};ranking=bt._final_ranking(i,date,st);counts.append({'date':date,**st})
  if ranking.empty:continue
  ranks.append(ranking.assign(date=date,order=np.arange(len(ranking))));anchor=ANCHORS[bt.sig.leader_idx[i]];lookup=daily.set_index('concept');snap=membership_date(close.index,date)
  for concept in ranking.concept:
   selected=lookup.loc[concept]
   rows.append({'date':date,'concept':concept,'anchor':anchor,'snapshot':snap,'candidate_count':len(ranking),'ret5':returns[5].at[date,concept],'ret20':returns[20].at[date,concept],'relative10':returns[10].at[date,concept]-returns[10].at[date,anchor],'vol20':vol.at[date,concept],'dd10':dd.at[date,concept],'anchor3':returns[3].at[date,anchor],'anchor10':returns[10].at[date,anchor],'anchor_dd10':dd.at[date,anchor],'score':selected.score,'sync':selected.sync,'capture':np.clip(selected.capture,0,2),'score_gap':daily.iloc[0].score-daily.iloc[1].score if len(daily)>1 else np.nan,'target':target.at[date,concept],'maturity':close.index[i+6] if i+6<len(close) else pd.NaT})
   if pd.notna(snap):jobs.update([(concept,snap.strftime('%Y%m%d')),(anchor,snap.strftime('%Y%m%d'))])
 frame=pd.DataFrame(rows);frame['feature_valid']=all_candidates_valid(frame,BASE_FEATURES);frame['label_valid']=frame.groupby('date').target.transform(lambda x:np.isfinite(x).all());frame['relative_target']=(frame.target-frame.groupby('date').target.transform('mean')).where(frame.label_valid)
 frame.to_parquet(OUT/'samples_base.parquet',index=False);pd.concat(ranks,ignore_index=True).to_parquet(OUT/'rankings.parquet',index=False);pd.DataFrame(counts).fillna(0).to_parquet(OUT/'ranking_counts.parquet',index=False);pd.DataFrame({'date':close.index}).to_parquet(OUT/'calendar.parquet',index=False)
 pd.DataFrame(sorted(jobs),columns=['code','date']).to_csv(OUT/'member_jobs.csv',index=False)
 audit.update(rows=len(frame),dates=frame.date.nunique(),feature_dates=frame.loc[frame.feature_valid,'date'].nunique(),member_jobs=len(jobs),anchors=ANCHORS)
 audit['bridge_sha256']={n:digest(OUT/n) for n in ['samples_base.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet','member_jobs.csv']}
 assert all(digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items())
 (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps(audit,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
