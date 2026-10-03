"""候选学习重排冻结工件与排序融合。"""
from pathlib import Path
import numpy as np
import pandas as pd
OUT=Path('outputs/qlib_candidate_rerank')


def fuse_ranking(original,scores,weight):
    n=len(original)
    values=np.array([scores.get(c,np.nan) for c in original.concept],float)
    if n<2 or not np.isfinite(values).all():return original
    units=int(round(weight*4));assert units/4==weight
    model2=np.rint(2*(n-pd.Series(values).rank(ascending=False,method='average').to_numpy())).astype(int)
    prior2=2*np.arange(n-1,-1,-1)
    key=units*model2+(4-units)*prior2
    out=original.copy();out['score']=key/(8*(n-1));out['_exact_order']=key
    return out.sort_values('_exact_order',ascending=False,kind='stable').drop(columns='_exact_order').reset_index(drop=True)


def hook(pred,weight):
    scores={pd.Timestamp(day):dict(zip(g.concept,g.score)) for day,g in pred.groupby('date')}
    reasons={pd.Timestamp(day):g.status.iloc[0] for day,g in pred.groupby('date')}
    counters={'rerank_calls':0,'rerank_fallback':0,'rerank_changed':0,'rerank_first_changed':0}
    def apply(original,date):
        counters['rerank_calls']+=1
        result=fuse_ranking(original,scores.get(date,{}),weight)
        if result is original:
            counters['rerank_fallback']+=1
            reason=reasons.get(date,'missing')
            key='fallback_'+('sparse' if reason=='trained' else reason)
            counters[key]=counters.get(key,0)+1
        elif result.concept.tolist()!=original.concept.tolist():
            counters['rerank_changed']+=1
            counters['rerank_first_changed']+=int(result.concept.iloc[0]!=original.concept.iloc[0])
        return result
    return apply,counters
