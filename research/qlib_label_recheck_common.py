"""标签纠错研究的只读数据与评估辅助；环境为qlib或resonance。"""
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd
from resonance import config

END=pd.Timestamp('2026-09-18')
ANCHORS=['399001.SZ','399303.SZ','000688.SH']
DATA=Path('/home/zxh/projects/6.resonance/data')
OUT=Path('outputs/qlib_label_recheck')


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()


def load_inputs():
    paths={'daily':DATA/'cache/daily_bars.parquet','minute':DATA/'cache/minute5_bars.parquet',
           'catalog':DATA/'concept_catalog.csv'}
    audit={'input_paths':{k:str(v) for k,v in paths.items()},
           'input_sha256':{k:digest(v) for k,v in paths.items()},'cutoff':str(END.date())}
    d=pd.read_parquet(paths['daily']); d['date']=pd.to_datetime(d.date)
    d=d[d.date<=END].copy()
    cal=pd.DatetimeIndex(sorted(d.loc[d.symbol==ANCHORS[0],'date']))
    audit['noncalendar_removed']=int((~d.date.isin(cal)).sum())
    d=d[d.date.isin(cal)]
    catalog=pd.read_csv(paths['catalog']);available=set(d.symbol)
    concepts=[c for c in catalog.code if c in available]
    codes=concepts+ANCHORS+['883957.TI']
    config.assert_no_retired(codes)
    d=d[d.symbol.isin(codes)]
    m=pd.read_parquet(paths['minute']);m['datetime']=pd.to_datetime(m.datetime)
    m=m[(m.datetime<END+pd.Timedelta(days=1))&m.symbol.isin(codes)].copy()
    assert not d.duplicated(['date','symbol']).any()
    assert not m.duplicated(['datetime','symbol']).any()
    audit.update(daily_rows=len(d),minute_rows=len(m),calendar_days=len(cal),concepts=len(concepts))
    return d,m,concepts,audit


def mature_subset(frame, calendar, start, end, horizon=1):
    dates=frame.index.get_level_values('datetime')
    positions=calendar.get_indexer(dates)
    good=(positions>=0)&(positions+horizon<len(calendar))
    maturity=pd.DatetimeIndex([calendar[p+horizon] if ok else pd.NaT for p,ok in zip(positions,good)])
    return frame.loc[good&(dates>=pd.Timestamp(start))&(dates<=pd.Timestamp(end))&
                     (maturity<=pd.Timestamp(end))].copy()


def complete_trades(trades, open_, cost):
    active=None
    rows=[]
    c=cost/10000.
    for row in trades.to_dict('records'):
        date=pd.Timestamp(row['date'])
        if row['type'] in ['switch','exit','stop']:
            assert active is not None and active['code']==row['from']
            exit_price=float(open_.at[date,active['code']])
            assert np.isfinite(exit_price) and exit_price>0
            rows.append({**active,'exit_date':date,'exit_price':exit_price,
                         'net_return':exit_price/active['entry_price']*(1-c)**2-1})
            active=None
        if row['type'] in ['entry','switch']:
            price=float(open_.at[date,row['to']])
            assert np.isfinite(price) and price>0
            active={'code':row['to'],'entry_date':date,'entry_price':price}
    return pd.DataFrame(rows,columns=['code','entry_date','entry_price','exit_date','exit_price','net_return'])


def model_post_rank(pred):
    table={(pd.Timestamp(d),c):float(p) for d,c,p in pred[['date','concept','pred']].itertuples(index=False,name=None)}
    counts={'model_days':0,'model_excluded':0,'model_fallback_sparse':0}
    def rank(ranking,date):
        top=ranking.head(5)
        scored=[(table[(date,c)],c) for c in top.concept if (date,c) in table and np.isfinite(table[(date,c)])]
        counts['model_days']+=1;counts['model_excluded']+=len(top)-len(scored)
        if len(scored)<2:
            counts['model_fallback_sparse']+=1
            return ranking
        scored.sort(key=lambda pair:(-pair[0],pair[1]))
        return pd.DataFrame({'concept':[c for _,c in scored],'score':[p for p,_ in scored]})
    return rank,counts
