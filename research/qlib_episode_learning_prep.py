"""研究环境：逐日起始的第一笔原规则交易，禁止尾部强制平仓。"""
from pathlib import Path
import sys,json,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from resonance.v3 import V3Params,V3Backtester,MinuteBarProvider
from research.qlib_label_recheck_common import load_inputs,digest,complete_trades,ANCHORS,END
from research.qlib_decision_learning_common import OUT as PRIOR
from research.qlib_decision_learning_eval import cached_engine
from research.qlib_episode_learning_common import OUT


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,m,concepts,audit=load_inputs();previous=json.loads((PRIOR/'preparation_audit.json').read_text())
    assert audit['input_sha256']==previous['input_sha256']
    assert all(digest(PRIOR/name)==value for name,value in previous['bridge_sha256'].items())
    for name in ['rankings.parquet','ranking_counts.parquet','calendar.parquet']:shutil.copy2(PRIOR/name,OUT/name)
    sample=pd.read_parquet(PRIOR/'samples.parquet').set_index('date')
    sample['fixed5_target']=sample.target;sample['fixed5_maturity']=sample.maturity
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    opens=d.pivot(index='date',columns='symbol',values='open').reindex_like(close)
    mb=MinuteBarProvider(m.pivot(index='datetime',columns='symbol',values='close').sort_index())
    base=V3Backtester(close,concepts,ANCHORS,params=V3Params(hl_source='leader',exec_price='open',daily_top=5,cost_bp=10),
        minute_bars_provider=mb,open_all=opens)
    raw=pd.read_parquet(OUT/'rankings.parquet')
    ranks={pd.Timestamp(day):g.sort_values('order').drop(columns=['date','order']).reset_index(drop=True) for day,g in raw.groupby('date')}
    counts=pd.read_parquet(OUT/'ranking_counts.parquet').set_index('date').to_dict('index')
    alltrue=pd.Series(True,index=close.index);rows=[];verified=0
    for n,(date,row) in enumerate(sample.iterrows()):
        bt=cached_engine(base,ranks,counts,alltrue,10);result=bt.run(date,END)
        closed=complete_trades(result['trades'],opens,10)
        if n in [50,150,300,450,600]:
            direct=base.run(date,END)
            pd.testing.assert_frame_equal(direct['trades'],result['trades'])
            pd.testing.assert_series_equal(direct['nav_curve'],result['nav_curve']);verified+=1
        if len(result['trades']):
            first=result['trades'].iloc[0]
            assert first['type']=='entry' and first['to']==row.concept
            assert pd.Timestamp(first.date)==close.index[close.index.get_loc(date)+1]
        if closed.empty:
            rows.append({'date':date,'target':np.nan,'maturity':pd.NaT,'duration':np.nan,'exit_type':None})
        else:
            first=closed.iloc[0];assert first.code==row.concept
            rows.append({'date':date,'target':first.net_return,'maturity':first.exit_date,
                'duration':close.index.get_loc(first.exit_date)-close.index.get_loc(first.entry_date),
                'exit_type':result['trades'].iloc[1]['type']})
        if n%100==0:print('labels completed',n,flush=True)
    targets=pd.DataFrame(rows).set_index('date')
    sample=sample.drop(columns=['target','maturity']).join(targets)
    sample.reset_index().to_parquet(OUT/'samples.parquet',index=False)
    valid=sample.dropna(subset=['target','fixed5_target'])
    audit.update(conditional_labels=int(sample.target.notna().sum()),unfinished_labels=int(sample.target.isna().sum()),
        verified_original_paths=verified,sign_disagreement=int((valid.target.gt(0)!=valid.fixed5_target.gt(0)).sum()),
        common_targets=len(valid),target_correlation=float(valid.target.corr(valid.fixed5_target)),
        duration_quantiles=sample.duration.quantile([0,.25,.5,.75,1]).to_dict(),sample_sha256=digest(OUT/'samples.parquet'))
    audit['bridge_sha256']={name:digest(OUT/name) for name in ['samples.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet']}
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values())
    (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(audit,flush=True)


if __name__=='__main__':main()
