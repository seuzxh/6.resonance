"""第二阶段中性收缩：仅复用冻结切片，无网络；conda resonance。"""
from pathlib import Path
import sys,json,shutil
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.constituent_breadth_common import BASE_FEATURES,shrunken_breadth_pair,all_candidates_valid
from research.constituent_breadth_inputs import digest
BASE=Path('outputs/constituent_breadth');OUT=Path('outputs/constituent_shrinkage')

def main():
 OUT.mkdir(exist_ok=True)
 for name in ['samples_base.parquet','rankings.parquet','ranking_counts.parquet','calendar.parquet','member_jobs.csv','member_manifest.json','stock_source_manifest.json']:shutil.copy2(BASE/name,OUT/name)
 samples=pd.read_parquet(BASE/'samples.parquet');cal=pd.DatetimeIndex(pd.read_parquet(BASE/'calendar.parquet').date);codes=pd.read_csv(BASE/'stock_codes.csv').code.tolist();idx={c:i for i,c in enumerate(codes)};rets=np.load(BASE/'stock_returns.npy',mmap_mode='r');snapshots={}
 manifest=json.loads((BASE/'member_manifest.json').read_text())
 for name,h in manifest.items():
  p=BASE/'members'/name;assert digest(p)==h;x=json.loads(p.read_text());members=[c for t in x['response'].get('tables') or [] for c in t['table']['p03473_f002'] if c.endswith(('.SH','.SZ'))];snapshots[x['code'],pd.Timestamp(x['date'])]=members
 rows=[]
 for row in samples.itertuples(index=False):
  i=cal.get_loc(row.date);member=snapshots.get((row.concept,row.snapshot),[]);anchor=set(snapshots.get((row.anchor,row.snapshot),[]));arr=rets[max(0,i-4):i+1,[idx[c] for c in member]]
  full=shrunken_breadth_pair(arr,[True]*len(member));exclusive=shrunken_breadth_pair(arr,[c not in anchor for c in member]) if anchor else (np.nan,np.nan)
  rows.append([*full,*exclusive])
 samples[['breadth1','breadth5','exclusive1','exclusive5']]=np.array(rows);samples['common_valid']=all_candidates_valid(samples,BASE_FEATURES+['breadth1','breadth5','exclusive1','exclusive5']);samples.to_parquet(OUT/'samples.parquet',index=False)
 rates={}
 for name,start,end in [('all','2021-12-01','2026-09-18'),('early','2023-06-01','2024-12-31'),('recent','2025-01-02','2026-09-18')]:
  s=samples[samples.date.between(start,end)];base=s.loc[s.base_valid,'date'].nunique();common=s.loc[s.common_valid,'date'].nunique();rates[name]={'base_dates':base,'common_dates':common,'fraction':common/base if base else 0}
 audit=json.loads((BASE/'preparation_audit.json').read_text());audit['bridge_sha256']['samples.parquet']=digest(OUT/'samples.parquet');audit['breadth_coverage']=rates;audit['breadth_data_gate']=all(v['fraction']>=.6 for v in rates.values());audit['shrinkage_strength']=20;audit['stock_returns_sha256']=digest(BASE/'stock_returns.npy');audit['stock_returns_path']=str(BASE/'stock_returns.npy')
 (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps({'coverage':rates,'passed':audit['breadth_data_gate']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
