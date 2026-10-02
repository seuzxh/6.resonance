"""conda qlib：按已冻结边界滚动训练，桥文件以外不读取行情。"""
from pathlib import Path
import sys,json,warnings
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import qlib
from sklearn.linear_model import LogisticRegression
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model.gbdt import LGBModel
from qlib.workflow import R
from research.qlib_decision_learning_common import OUT,FEATURES,split_positions
from research.qlib_label_recheck_common import digest


def dataset(train,valid,test,binary):
    frame=pd.concat([train,valid,test]).copy()
    label=frame.target.copy()
    if binary:label=label.gt(0).astype(float).where(label.notna())
    frame=frame[FEATURES].assign(label=label)
    frame.index=pd.MultiIndex.from_arrays([frame.index,np.repeat('proposal',len(frame))],names=['datetime','instrument'])
    frame.columns=pd.MultiIndex.from_tuples([('label' if c=='label' else 'feature',c) for c in frame.columns])
    return DatasetH(handler=DataHandlerLP.from_df(frame.sort_index()),segments={
        'train':(train.index.min(),train.index.max()),'valid':(valid.index.min(),valid.index.max()),
        'test':(test.index.min(),test.index.max())})


def main():
    qlib.init(provider_uri='/tmp',region='cn');R.log_metrics=lambda **kw:None
    from qlib.config import C
    C.kernels=2
    samples=pd.read_parquet(OUT/'samples.parquet').set_index('date').sort_index()
    audit=json.loads((OUT/'preparation_audit.json').read_text())
    assert digest(OUT/'samples.parquet')==audit['sample_sha256']
    assert all(digest(OUT/name)==value for name,value in audit['bridge_sha256'].items())
    cal=pd.DatetimeIndex(pd.read_parquet(OUT/'calendar.parquet').date)
    start=cal.searchsorted('2023-06-01');predictions=[];folds=[]
    (OUT/'models').mkdir(exist_ok=True)
    for fold,s in enumerate(range(start,len(cal),21)):
        ti,vi,pi=split_positions(s,len(cal))
        eligible=samples[samples.feature_valid]
        train=eligible.reindex(cal[ti]).dropna(subset=FEATURES+['target'])
        valid=eligible.reindex(cal[vi]).dropna(subset=FEATURES+['target'])
        test=eligible.reindex(cal[pi]).dropna(subset=FEATURES)
        assert (train.maturity<cal[vi[0]]).all() and (valid.maturity<cal[pi[0]]).all()
        positive=int((train.target>0).sum());negative=len(train)-positive
        status='trained' if len(train)>=120 and min(positive,negative)>=30 and len(valid)>=30 and len(test)>0 else 'insufficient'
        folds.append({'fold':fold,'train_first':str(cal[ti[0]].date()),'train_last':str(cal[ti[-1]].date()),
            'valid_first':str(cal[vi[0]].date()),'valid_last':str(cal[vi[-1]].date()),
            'test_first':str(cal[pi[0]].date()),'test_last':str(cal[pi[-1]].date()),
            'train_rows':len(train),'positive':positive,'negative':negative,'valid_rows':len(valid),
            'test_rows':len(test),'status':status,
            'train_maturity_last':str(train.maturity.max()),'valid_maturity_last':str(valid.maturity.max())})
        all_dates=cal[pi]
        models=['linear','single']+[f'{kind}{leaf}' for kind in ['binary','regression'] for leaf in [3,4,6]]
        for name in models:
            vscore=tscore=None;iterations=None
            if status=='trained':
                if name=='single':vscore=valid.ret5.to_numpy();tscore=pd.Series(test.ret5.to_numpy(),index=test.index)
                elif name=='linear':
                    mean=train[FEATURES].mean();scale=train[FEATURES].std(ddof=1).replace(0,1)
                    model=LogisticRegression(C=1.,max_iter=1000,random_state=0)
                    with warnings.catch_warnings():
                        warnings.filterwarnings('error',category=__import__('sklearn.exceptions',fromlist=['ConvergenceWarning']).ConvergenceWarning)
                        model.fit((train[FEATURES]-mean)/scale,train.target.gt(0).astype(int))
                    vscore=model.predict_proba((valid[FEATURES]-mean)/scale)[:,1]
                    tscore=pd.Series(model.predict_proba((test[FEATURES]-mean)/scale)[:,1],index=test.index)
                    (OUT/'models'/f'fold{fold}_{name}.json').write_text(json.dumps({'mean':mean.to_dict(),
                        'scale':scale.to_dict(),'coef':model.coef_.tolist(),'intercept':model.intercept_.tolist()}))
                else:
                    binary=name.startswith('binary');leaf=int(name[-1]);ds=dataset(train,valid,test,binary)
                    model=LGBModel(loss='binary' if binary else 'mse',early_stopping_rounds=15,num_boost_round=60,
                        learning_rate=.05,num_leaves=leaf,min_data_in_leaf=30,feature_fraction=1.,bagging_fraction=1.,
                        bagging_freq=0,seed=0,num_threads=2,verbose=-1)
                    model.fit(ds,verbose_eval=0)
                    vscore=model.predict(ds,segment='valid').to_numpy()
                    tscore=model.predict(ds,segment='test').droplevel('instrument')
                    iterations=model.model.best_iteration
                    model.model.save_model(str(OUT/'models'/f'fold{fold}_{name}.txt'))
                    (OUT/'models'/f'fold{fold}_{name}.json').write_text(json.dumps(model.model.params,default=str))
            for quantile in [.1,.2,.3]:
                threshold=float(np.quantile(vscore,quantile,method='linear')) if vscore is not None else np.nan
                for date in all_dates:
                    score=float(tscore.get(date,np.nan)) if tscore is not None else np.nan
                    fallback=status if status!='trained' else ('feature_or_candidate_missing' if not np.isfinite(score) else '')
                    predictions.append({'date':date,'variant':f'{name}_q{int(quantile*100)}','fold':fold,
                        'score':score,'threshold':threshold,'allow':bool(score>=threshold) if not fallback else True,
                        'fallback':fallback,'best_iteration':iterations})
        print(f'fold={fold} {folds[-1]}',flush=True)
    pred=pd.DataFrame(predictions)
    assert not pred.duplicated(['date','variant']).any()
    pred.to_parquet(OUT/'predictions.parquet',index=False)
    pd.DataFrame(folds).to_csv(OUT/'folds.csv',index=False)
    assert digest(OUT/'samples.parquet')==audit['sample_sha256']
    assert all(digest(OUT/name)==value for name,value in audit['bridge_sha256'].items())
    (OUT/'training_audit.json').write_text(json.dumps({'bridge_sha256':audit['bridge_sha256'],
        'prediction_sha256':digest(OUT/'predictions.parquet'),'folds_sha256':digest(OUT/'folds.csv')},indent=2))
    print('rolling training complete',pred.shape,flush=True)


if __name__=='__main__':main()
