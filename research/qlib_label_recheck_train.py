"""标签纠错受控重训；conda qlib；先读 docs/research/qlib-label-recheck.md。"""
from pathlib import Path
import sys,json,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.qlib_label_recheck_common import load_inputs,mature_subset,digest,OUT,END,ANCHORS
from research.qlib_route_a.qlib_provider import ParquetData,init_qlib_parquet
from research.qlib_route_a.qlib_pipeline import build_features,daily_rank_ic
from research.qlib_route_a.qlib_factor_export import export_factors
from research.qlib_route_a.alpha158_verify_prep import EXPR
from resonance.v3 import V3Params


def main():
    started=time.monotonic();OUT.mkdir(parents=True,exist_ok=True)
    d,m,concepts,audit=load_inputs()
    close=d.pivot(index='date',columns='symbol',values='close').sort_index()
    audit['daily_factor_half_life']='leader'
    factors=export_factors(close,concepts,ANCHORS,V3Params(hl_source='leader'))
    factors.to_parquet(OUT/'daily_factors.parquet',index=False)
    print('capped inputs and daily features prepared',flush=True)
    data=ParquetData(d,m,{'all':list(close.columns),'concept':concepts})
    init_qlib_parquet(data,provider_uri='/tmp')
    from qlib.config import C
    C.kernels=2
    from qlib.data import D
    from qlib.data.dataset import DatasetH
    from qlib.data.dataset.handler import DataHandlerLP
    from qlib.contrib.model.gbdt import LGBModel
    # Disable experiment tracking writes; preserve explicit artifacts below.
    from qlib.workflow import R
    R.log_metrics=lambda **kwargs: None
    available=set(m.symbol)
    covered=[c for c in concepts if c in available]
    feats12=build_features(data,covered,'2025-09-22','2026-09-18',OUT/'daily_factors.parquet')
    print(f'12 features: {feats12.shape}',flush=True)
    f3=D.features(covered,list(EXPR.values()),'2025-09-26 00:00','2026-09-18 23:59',freq='5min',disk_cache=0)
    f3.columns=list(EXPR)
    dates=pd.Index([pd.Timestamp(t).normalize() for t in f3.index.get_level_values('datetime')])
    f3=f3.groupby([dates,f3.index.get_level_values('instrument')],sort=True).last()
    f3.index.names=['datetime','instrument']
    forward=(close[covered].shift(-1)/close[covered]-1).stack(future_stack=True)
    forward.index.names=['datetime','instrument'];forward.name='label'
    backward=(close[covered]/close[covered].shift(-1)-1).stack(future_stack=True)
    backward.index.names=['datetime','instrument'];backward.name='label'
    summary=[]
    for family,features in [('minute12',feats12),('technical3',f3)]:
        features=features.sort_index()
        audit[family+'_inf_count']=int(np.isinf(features.to_numpy(float)).sum())
        features=features.replace([np.inf,-np.inf],np.nan)
        features.to_parquet(OUT/f'features_{family}.parquet')
        for direction,target in [('inverted',backward),('forward',forward)]:
            full=features.join(target)
            train=mature_subset(full,close.index,'2025-09-22','2026-05-29').dropna(subset=['label'])
            valid=mature_subset(full,close.index,'2026-06-01','2026-07-31').dropna(subset=['label'])
            ds_dates=full.index.get_level_values('datetime')
            test=full.loc[(ds_dates>=pd.Timestamp('2026-08-03'))&(ds_dates<=END)]
            frame=pd.concat([train,valid,test]).sort_index()
            assert not frame.index.has_duplicates
            frame.columns=pd.MultiIndex.from_tuples([('label' if c=='label' else 'feature',c) for c in frame.columns])
            ds=DatasetH(handler=DataHandlerLP.from_df(frame),segments={
                'train':('2025-09-22','2026-05-29'),'valid':('2026-06-01','2026-07-31'),
                'test':('2026-08-03','2026-09-18')})
            model=LGBModel(loss='mse',early_stopping_rounds=30,num_boost_round=60,
                           learning_rate=.05,num_leaves=8,seed=0,num_threads=2,verbose=-1)
            model.fit(ds,verbose_eval=0)
            tag=f'{family}_{direction}'
            model.model.save_model(str(OUT/f'model_{tag}.txt'))
            pred=model.predict(ds,segment='test').rename('pred')
            saved=pred.reset_index().rename(columns={'datetime':'date','instrument':'concept'})
            saved.to_parquet(OUT/f'pred_{tag}.parquet',index=False)
            truth=forward.reindex(pred.index)
            ic,t,n=daily_rank_ic(pred,truth)
            summary.append({'variant':tag,'train_rows':len(train),'valid_rows':len(valid),
                'train_dates':train.index.get_level_values('datetime').nunique(),
                'valid_dates':valid.index.get_level_values('datetime').nunique(),
                'last_train_signal':str(train.index.get_level_values('datetime').max().date()),
                'last_valid_signal':str(valid.index.get_level_values('datetime').max().date()),
                'best_iteration':model.model.best_iteration,'rank_ic_forward':ic,'ic_t':t,'ic_days':n,
                'prediction_rows':len(pred),'prediction_last':str(saved.date.max().date())})
            print(summary[-1],flush=True)
    pd.DataFrame(summary).to_csv(OUT/'training_audit.csv',index=False)
    audit['elapsed_seconds']=time.monotonic()-started
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values())
    (OUT/'training_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print('four-model training complete',flush=True)


if __name__=='__main__':main()
