"""研究环境：同覆盖的12/16特征桥文件。"""
from pathlib import Path
import sys,json,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.qlib_label_recheck_common import load_inputs,digest
from research.qlib_decision_learning_common import FEATURES
from research.qlib_candidate_rerank_common import OUT as PRIOR
from research.qlib_volume_rerank_common import OUT,EXTRA,volume_features


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    d,m,concepts,audit=load_inputs();old=json.loads((PRIOR/'preparation_audit.json').read_text())
    assert audit['input_sha256']==old['input_sha256']
    assert all(digest(PRIOR/name)==value for name,value in old['bridge_sha256'].items())
    for name in ['rankings.parquet','ranking_counts.parquet','calendar.parquet']:shutil.copy2(PRIOR/name,OUT/name)
    frame=pd.read_parquet(PRIOR/'samples.parquet');frame['base_feature_valid']=frame.feature_valid
    wide={k:d.pivot(index='date',columns='symbol',values=k).sort_index() for k in ['close','open','high','low','volume']}
    factors=volume_features(wide)
    for key,value in factors.items():frame[key]=[value.at[date,code] for date,code in frame[['date','concept']].itertuples(index=False,name=None)]
    frame['feature_valid']=frame.base_feature_valid & np.isfinite(frame[EXTRA]).all(axis=1)
    frame['feature_valid']=frame.groupby('date').feature_valid.transform('all')
    frame.to_parquet(OUT/'samples.parquet',index=False)
    audit.update(rows=len(frame),dates=frame.date.nunique(),base_feature_dates=frame.loc[frame.base_feature_valid,'date'].nunique(),
        matched_feature_dates=frame.loc[frame.feature_valid,'date'].nunique(),new_features=EXTRA)
    audit['bridge_sha256']={name:digest(OUT/name) for name in ['samples.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet']}
    audit['input_unchanged']={k:digest(v)==audit['input_sha256'][k] for k,v in audit['input_paths'].items()}
    assert all(audit['input_unchanged'].values())
    (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
    print(audit,flush=True)


if __name__=='__main__':main()
