"""qlib环境：同日候选的绝对/相对开盘收益滚动回归。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import qlib
from sklearn.linear_model import Ridge
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model.gbdt import LGBModel
from qlib.workflow import R
from research.qlib_decision_learning_common import FEATURES as BASE_FEATURES,split_positions
from research.qlib_volume_rerank_common import OUT,EXTRA
from research.qlib_label_recheck_common import digest


def dataset(train,valid,test,label,features):
    frame=pd.concat([train,valid,test]);frame=frame[features].assign(label=frame[label])
    frame.columns=pd.MultiIndex.from_tuples([('label' if c=='label' else 'feature',c) for c in frame.columns])
    return DatasetH(handler=DataHandlerLP.from_df(frame.sort_index()),segments={
        key:(part.index.get_level_values('datetime').min(),part.index.get_level_values('datetime').max())
        for key,part in [('train',train),('valid',valid),('test',test)]})


def main():
    qlib.init(provider_uri='/tmp',region='cn');R.log_metrics=lambda **kw:None
    from qlib.config import C
    C.kernels=2
    audit=json.loads((OUT/'preparation_audit.json').read_text())
    assert all(digest(OUT/name)==value for name,value in audit['bridge_sha256'].items())
    samples=pd.read_parquet(OUT/'samples.parquet').set_index(['date','concept']).sort_index()
    samples.index.names=['datetime','instrument']
    cal=pd.DatetimeIndex(pd.read_parquet(OUT/'calendar.parquet').date)
    first=cal.searchsorted('2023-06-01');predictions=[];folds=[];diagnostics=[]
    (OUT/'models').mkdir(exist_ok=True)
    for fold,s in enumerate(range(first,len(cal),21)):
        ti,vi,pi=split_positions(s,len(cal));dates=samples.index.get_level_values('datetime')
        train=samples.loc[dates.isin(cal[ti])&samples.feature_valid&samples.label_valid]
        valid=samples.loc[dates.isin(cal[vi])&samples.feature_valid&samples.label_valid]
        test=samples.loc[dates.isin(cal[pi])&samples.feature_valid]
        fulltest=samples.loc[dates.isin(cal[pi])]
        assert (train.maturity<cal[vi[0]]).all() and (valid.maturity<cal[pi[0]]).all()
        nt=train.index.get_level_values('datetime').nunique();nv=valid.index.get_level_values('datetime').nunique()
        status='trained' if nt>=120 and nv>=30 and len(test) else 'insufficient'
        folds.append({'fold':fold,'train_first':str(cal[ti[0]]),'train_last':str(cal[ti[-1]]),
            'valid_first':str(cal[vi[0]]),'valid_last':str(cal[vi[-1]]),'test_first':str(cal[pi[0]]),'test_last':str(cal[pi[-1]]),
            'train_dates':nt,'valid_dates':nv,'train_rows':len(train),'valid_rows':len(valid),'test_rows':len(test),
            'train_maturity_last':str(train.maturity.max()),'valid_maturity_last':str(valid.maturity.max()),'status':status})
        names=[f'{kind}_{model}' for kind in ['base12','enhanced16'] for model in ['linear','tree3','tree4','tree6']]+['momentum','reversal']
        for name in names:
            prediction=pd.Series(dtype=float);iteration=None
            features=BASE_FEATURES+EXTRA if name.startswith('enhanced16') else BASE_FEATURES
            if status=='trained':
                if name in ['momentum','reversal']:prediction=test.ret5*(1 if name=='momentum' else -1)
                else:
                    kind,algorithm=name.split('_');label='relative_target'
                    if algorithm=='linear':
                        mean=train[features].mean();scale=train[features].std(ddof=1).replace(0,1)
                        model=Ridge(alpha=1.)
                        model.fit((train[features]-mean)/scale,train[label])
                        prediction=pd.Series(model.predict((test[features]-mean)/scale),index=test.index)
                        (OUT/'models'/f'fold{fold}_{name}.json').write_text(json.dumps({'mean':mean.to_dict(),
                            'scale':scale.to_dict(),'coef':model.coef_.tolist(),'intercept':float(model.intercept_)}))
                    else:
                        ds=dataset(train,valid,test,label,features)
                        model=LGBModel(loss='mse',early_stopping_rounds=15,num_boost_round=60,learning_rate=.05,
                            num_leaves=int(algorithm[-1]),min_data_in_leaf=30,feature_fraction=1.,bagging_fraction=1.,
                            bagging_freq=0,seed=0,num_threads=2,verbose=-1)
                        model.fit(ds,verbose_eval=0);prediction=model.predict(ds,segment='test');iteration=model.model.best_iteration
                        model.model.save_model(str(OUT/'models'/f'fold{fold}_{name}.txt'))
                        (OUT/'models'/f'fold{fold}_{name}.json').write_text(json.dumps(model.model.params,default=str))
            out=fulltest[['target','relative_target','feature_valid']].copy()
            out['score']=prediction.reindex(out.index);out['model']=name;out['fold']=fold;out['best_iteration']=iteration
            out['status']=status
            if status=='trained':out.loc[~out.feature_valid,'status']='feature_invalid'
            assert out.loc[out.status=='trained','score'].notna().all()
            out=out.reset_index().rename(columns={'datetime':'date','instrument':'concept'})
            predictions.append(out)
        print(f'fold {fold}: {folds[-1]}',flush=True)
    pred=pd.concat(predictions,ignore_index=True)
    assert not pred.duplicated(['date','concept','model']).any()
    pred.to_parquet(OUT/'predictions.parquet',index=False);pd.DataFrame(folds).to_csv(OUT/'folds.csv',index=False)
    assert all(digest(OUT/name)==value for name,value in audit['bridge_sha256'].items())
    (OUT/'training_audit.json').write_text(json.dumps({'bridge_sha256':audit['bridge_sha256'],
        'prediction_sha256':digest(OUT/'predictions.parquet'),'folds_sha256':digest(OUT/'folds.csv')},indent=2))
    print('candidate training complete',pred.shape,flush=True)


if __name__=='__main__':main()
