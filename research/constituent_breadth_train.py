"""qlib 0.9.7滚动训练，研究数据只经桥文件输入。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import qlib
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model.gbdt import LGBModel
from qlib.workflow import R
from research.constituent_breadth_common import OUT,BASE_FEATURES
from research.constituent_breadth_inputs import digest
from research.qlib_decision_learning_common import split_positions


def dataset(train,valid,test,features):
 f=pd.concat([train,valid,test]);f=f[features].assign(label=f.relative_target);f.columns=pd.MultiIndex.from_tuples([('label' if c=='label' else 'feature',c) for c in f.columns])
 return DatasetH(handler=DataHandlerLP.from_df(f.sort_index()),segments={k:(part.index.get_level_values('datetime').min(),part.index.get_level_values('datetime').max()) for k,part in [('train',train),('valid',valid),('test',test)]})


def main():
 assert qlib.__version__=='0.9.7',qlib.__version__
 qlib.init(provider_uri='/tmp',region='cn');R.log_metrics=lambda **kw:None
 from qlib.config import C
 C.kernels=2
 audit=json.loads((OUT/'preparation_audit.json').read_text());assert all(digest(OUT/n)==h for n,h in audit['bridge_sha256'].items())
 samples=pd.read_parquet(OUT/'samples.parquet').set_index(['date','concept']).sort_index();samples.index.names=['datetime','instrument'];cal=pd.DatetimeIndex(pd.read_parquet(OUT/'calendar.parquet').date)
 groups={'priceall':(BASE_FEATURES,'base_valid')}
 if audit['breadth_data_gate']:groups.update({'matched':(BASE_FEATURES,'common_valid'),'full':(BASE_FEATURES+['breadth1','breadth5'],'common_valid'),'exclusive':(BASE_FEATURES+['exclusive1','exclusive5'],'common_valid')})
 predictions=[];folds=[];(OUT/'models').mkdir(exist_ok=True)
 for fold,s in enumerate(range(cal.searchsorted('2023-06-01'),len(cal),21)):
  ti,vi,pi=split_positions(s,len(cal));dates=samples.index.get_level_values('datetime');fulltest=samples.loc[dates.isin(cal[pi])]
  for group,(features,mask) in groups.items():
   train=samples.loc[dates.isin(cal[ti])&samples[mask]&samples.label_valid];valid=samples.loc[dates.isin(cal[vi])&samples[mask]&samples.label_valid];test=samples.loc[dates.isin(cal[pi])&samples[mask]]
   assert (train.maturity<cal[vi[0]]).all() and (valid.maturity<cal[pi[0]]).all()
   nt=train.index.get_level_values('datetime').nunique();nv=valid.index.get_level_values('datetime').nunique();status='trained' if nt>=120 and nv>=30 and len(test) else 'insufficient'
   folds.append({'fold':fold,'group':group,'train_dates':nt,'valid_dates':nv,'test_rows':len(test),'status':status,'train_first':str(cal[ti[0]]),'train_last':str(cal[ti[-1]]),'valid_first':str(cal[vi[0]]),'valid_last':str(cal[vi[-1]]),'test_first':str(cal[pi[0]]),'test_last':str(cal[pi[-1]]),'train_maturity_last':str(train.maturity.max()),'valid_maturity_last':str(valid.maturity.max())})
   names=[group+'_tree'+str(n) for n in [3,4,6]]
   if group=='priceall':names+=['allmomentum','allreversal']
   if group=='matched':names+=['momentum','reversal','directfull','directexclusive']
   for name in names:
    prediction=pd.Series(dtype=float);iteration=None
    if status=='trained':
     if name in ['momentum','allmomentum']:prediction=test.ret5
     elif name in ['reversal','allreversal']:prediction=-test.ret5
     elif name=='directfull':prediction=test.breadth1
     elif name=='directexclusive':prediction=test.exclusive1
     else:
      ds=dataset(train,valid,test,features);model=LGBModel(loss='mse',early_stopping_rounds=15,num_boost_round=60,learning_rate=.05,num_leaves=int(name[-1]),min_data_in_leaf=30,feature_fraction=1.,bagging_fraction=1.,bagging_freq=0,seed=0,num_threads=2,verbose=-1)
      model.fit(ds,verbose_eval=0);prediction=model.predict(ds,segment='test');iteration=model.model.best_iteration;model.model.save_model(str(OUT/'models'/f'fold{fold}_{name}.txt'))
      (OUT/'models'/f'fold{fold}_{name}.json').write_text(json.dumps({'features':features,'params':model.model.params},default=str))
    o=fulltest[['target','relative_target']].copy();o['feature_valid']=fulltest[mask];o['score']=prediction.reindex(o.index);o['model']=name;o['fold']=fold;o['status']=status;o['best_iteration']=iteration
    if status=='trained':o.loc[~o.feature_valid,'status']='feature_invalid'
    assert o.loc[o.status=='trained','score'].notna().all();predictions.append(o.reset_index().rename(columns={'datetime':'date','instrument':'concept'}))
  print('fold',fold,'groups',[(x['group'],x['train_dates'],x['valid_dates'],x['status']) for x in folds if x['fold']==fold],flush=True)
 pred=pd.concat(predictions,ignore_index=True);assert not pred.duplicated(['date','concept','model']).any();pred.to_parquet(OUT/'predictions.parquet',index=False);pd.DataFrame(folds).to_csv(OUT/'folds.csv',index=False)
 assert all(digest(OUT/n)==h for n,h in audit['bridge_sha256'].items())
 (OUT/'training_audit.json').write_text(json.dumps({'qlib_version':qlib.__version__,'bridge_sha256':audit['bridge_sha256'],'prediction_sha256':digest(OUT/'predictions.parquet'),'models':sorted(pred.model.unique()),'expected_replays':(pred.model.nunique()*3+1)*45},indent=2));print('training done',pred.shape,flush=True)

if __name__=='__main__':main()
