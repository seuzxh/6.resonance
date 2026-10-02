"""候选预测覆盖、逐日相关和真实排序改动的描述性审计。"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.qlib_candidate_rerank_common import OUT


def main():
    pred=pd.read_parquet(OUT/'predictions.parquet');rows=[];daily=[]
    for (model,date),g in pred.groupby(['model','date']):
        valid=g[['score','target']].dropna();corr=np.nan
        if len(valid)>=2 and valid.score.nunique()>1 and valid.target.nunique()>1:
            corr=valid.score.corr(valid.target,method='spearman')
        daily.append({'model':model,'date':date,'status':g.status.iloc[0],'candidates':len(g),
            'scored':int(g.score.notna().sum()),'matured':len(valid),'rank_corr':corr,
            'constant':bool(len(valid)>=2 and valid.score.nunique()==1)})
    daily=pd.DataFrame(daily);daily.to_csv(OUT/'prediction_daily.csv',index=False)
    for period,(start,end) in {'main':('2023-06-01','2026-09-18'),'early':('2023-06-01','2024-12-31'),
                                'recent':('2025-01-02','2026-09-18')}.items():
        for model,g in daily[daily.date.between(start,end)].groupby('model'):
            rows.append({'window':period,'model':model,'candidate_dates':len(g),
                'trained_dates':int(g.status.eq('trained').sum()),'valid_corr_dates':int(g.rank_corr.notna().sum()),
                'mean_rank_corr':g.rank_corr.mean(),'constant_dates':int(g.constant.sum())})
    pd.DataFrame(rows).to_csv(OUT/'prediction_summary.csv',index=False)
    r=pd.read_csv(OUT/'results.csv');countcols=[c for c in r if c.startswith(('rerank_','fallback_'))]
    r[countcols]=r[countcols].fillna(0);r.to_csv(OUT/'results.csv',index=False)
    r.groupby(['cost','window','variant']).median(numeric_only=True).to_csv(OUT/'medians.csv')
    print(pd.DataFrame(rows).query("window=='main'").to_string(index=False))


if __name__=='__main__':main()
