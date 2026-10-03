"""预注册成员时点和上涨比例；conda resonance。"""
from pathlib import Path
import numpy as np
import pandas as pd
OUT=Path('outputs/constituent_breadth')
BASE_FEATURES=['ret5','ret20','relative10','vol20','dd10','anchor3','anchor10','anchor_dd10','score','sync','capture','score_gap']


def membership_date(calendar,date):
    prior=calendar[calendar<pd.Timestamp(date).to_period('M').start_time]
    return prior[-1] if len(prior) else pd.NaT


def breadth_pair(returns,keep,min_members=20,min_coverage=.9):
    a=np.asarray(returns,float)[:,np.asarray(keep,bool)]
    if a.shape[1]<min_members or a.shape[0]!=5:return np.nan,np.nan
    valid=np.isfinite(a);n=valid.sum(axis=1)
    if (n/a.shape[1]<min_coverage).any():return np.nan,np.nan
    ratio=((a>0)&valid).sum(axis=1)/n
    return float(ratio[-1]),float(ratio.mean())


def all_candidates_valid(frame,features):
    mask=pd.Series(np.isfinite(frame[features].to_numpy(float)).all(axis=1),index=frame.index)
    return mask.groupby(frame.date).transform('all') & frame.groupby('date').date.transform('size').ge(2)
