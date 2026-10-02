"""真实决策子集、代理目标与降级的描述性诊断。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.qlib_episode_learning_common import OUT


def main():
    decisions=pd.read_parquet(OUT/'decisions.parquet');rows=[];fallback=[]
    for (window,phase,tag),g in decisions.groupby(['window','phase','variant']):
        for allow,sub in g.groupby('allow'):
            y=sub.target.dropna();score=sub.loc[y.index,'model_score'].dropna()
            yy=sub.loc[score.index,'target'];label=yy.gt(0);npos=int(label.sum());nneg=len(label)-npos
            auc=(score.rank().loc[label].sum()-npos*(npos+1)/2)/(npos*nneg) if npos and nneg else np.nan
            corr=score.corr(yy,method='spearman') if score.nunique()>1 and yy.nunique()>1 else np.nan
            rows.append({'window':window,'phase':phase,'variant':tag,'allow':allow,'opportunities':len(sub),
                'matured_labels':len(y),'proxy_win':y.gt(0).mean(),'proxy_mean':y.mean(),
                'score_label_rank_corr':corr,'auc':auc,'scored_matured':len(score)})
        if tag!='baseline':
            for reason,n in g.fallback.fillna('missing').value_counts().items():
                fallback.append({'window':window,'phase':phase,'variant':tag,'reason':reason or 'scored','count':int(n)})
    pd.DataFrame(rows).to_csv(OUT/'decision_diagnostics.csv',index=False)
    pd.DataFrame(fallback).to_csv(OUT/'fallback_actual.csv',index=False)
    print(pd.DataFrame(rows).query("window=='main' and variant=='binary4_q20'").to_string(index=False))


if __name__=='__main__':main()
