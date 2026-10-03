"""只读成员与本地后复权价格，生成增强特征；conda resonance。"""
from pathlib import Path
import sys,json,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.constituent_breadth_common import OUT,BASE_FEATURES,breadth_pair,all_candidates_valid
from research.constituent_breadth_inputs import digest
ROOT=Path('/home/zxh/.qlib/qlib_data/cn_data')


def main():
 manifest=json.loads((OUT/'member_manifest.json').read_text());snapshots={};member_audit=[];allcodes=set()
 for name,h in manifest.items():
  p=OUT/'members'/name;assert digest(p)==h;x=json.loads(p.read_text());date=pd.Timestamp(x['date']);members=[]
  for t in x['response'].get('tables') or []:
   z=t['table'];assert len(z['p03473_f001'])==len(z['p03473_f002'])==len(z['p03473_f003'])
   assert all(pd.Timestamp(d)==date for d in z['p03473_f001']);members+=z['p03473_f002']
  assert len(members)==len(set(members));assert all(members)
  codes=sorted(c for c in members if c.endswith(('.SZ','.SH')))
  snapshots[x['code'],date]=codes;allcodes.update(codes)
  member_audit.append({'code':x['code'],'date':date,'total':len(members),'sh_sz':len(codes),'excluded_other_market':len(members)-len(codes),'errorcode':x['response']['errorcode']})
 pd.DataFrame(member_audit).to_csv(OUT/'member_coverage.csv',index=False)
 samples=pd.read_parquet(OUT/'samples_base.parquet');cal=pd.DatetimeIndex(pd.read_parquet(OUT/'calendar.parquet').date);stockcal=pd.DatetimeIndex(pd.to_datetime((ROOT/'calendars/day.txt').read_text().splitlines()));positions=stockcal.get_indexer(cal);assert (positions>=0).all()
 codes=sorted(allcodes);idx={c:i for i,c in enumerate(codes)};rets=np.full((len(cal),len(codes)),np.nan,dtype=np.float32);files={};quality=[]
 instruments={}
 for line in (ROOT/'instruments/all.txt').read_text().splitlines():
  code,s,e=line.split();instruments[code[2:]+'.'+code[:2].upper()]=pd.Timestamp(s)
 def read(code,field):
  p=ROOT/'features'/(code.split('.')[1]+code.split('.')[0]).lower()/(field+'.day.bin');result=np.full(len(cal),np.nan)
  if not p.exists():return result
  content=p.read_bytes();files[str(p)]=hashlib.sha256(content).hexdigest();a=np.frombuffer(content,dtype='<f4')
  if len(a)<2:return result
  at=positions-int(a[0])+1;good=(at>=1)&(at<len(a));result[good]=a[at[good]];return result
 for c,j in idx.items():
  close=read(c,'close');factor=read(c,'factor');volume=read(c,'volume');valid=np.isfinite(close)&(close>0)&np.isfinite(factor)&(factor>0)&np.isfinite(volume)&(volume>0)
  with np.errstate(divide='ignore',invalid='ignore'):r=close[1:]/close[:-1]-1
  usable=valid[1:]&valid[:-1]&np.isfinite(r)&(np.abs(r)<=.35)
  # First local date is not treated as an actual listing date; only a data boundary.
  if c in instruments:usable &= cal[1:]>=instruments[c]
  rets[1:,j]=np.where(usable,r,np.nan)
  quality.append({'code':c,'valid_return_days':int(usable.sum()),'extreme_return_days':int((np.abs(r)>.35).sum()),'local_start':str(instruments.get(c,pd.NaT))})
  if j%1000==0:print('stock loaded',j,'/',len(codes),flush=True)
 np.save(OUT/'stock_returns.npy',rets);pd.DataFrame({'code':codes}).to_csv(OUT/'stock_codes.csv',index=False);pd.DataFrame(quality).to_csv(OUT/'stock_quality.csv',index=False)
 (OUT/'stock_source_manifest.json').write_text(json.dumps(files,indent=2))
 rows=[]
 for row in samples.itertuples(index=False):
  i=cal.get_loc(row.date);member=snapshots.get((row.concept,row.snapshot),[]);anchor=set(snapshots.get((row.anchor,row.snapshot),[]));kept=[c not in anchor for c in member]
  assert pd.isna(row.snapshot) or row.snapshot<row.date.to_period('M').start_time
  arr=rets[max(0,i-4):i+1,[idx[c] for c in member]]
  full=breadth_pair(arr,[True]*len(member));exclusive=breadth_pair(arr,kept) if anchor else (np.nan,np.nan)
  rows.append({'breadth1':full[0],'breadth5':full[1],'exclusive1':exclusive[0],'exclusive5':exclusive[1],'members':len(member),'exclusive_members':sum(kept),'anchor_members':len(anchor),'member_coverage_min':float(np.isfinite(arr).mean(axis=1).min()) if len(member) else 0,'exclusive_coverage_min':float(np.isfinite(arr[:,kept]).mean(axis=1).min()) if sum(kept) else 0})
 samples=pd.concat([samples.reset_index(drop=True),pd.DataFrame(rows)],axis=1)
 samples['base_valid']=samples.feature_valid;samples['common_valid']=all_candidates_valid(samples,BASE_FEATURES+['breadth1','breadth5','exclusive1','exclusive5'])
 rates={}
 for name,start,end in [('all','2021-12-01','2026-09-18'),('early','2023-06-01','2024-12-31'),('recent','2025-01-02','2026-09-18')]:
  s=samples[samples.date.between(start,end)];base=s.loc[s.base_valid,'date'].nunique();common=s.loc[s.common_valid,'date'].nunique();rates[name]={'base_dates':base,'common_dates':common,'fraction':common/base if base else 0}
 passed=all(v['fraction']>=.6 for v in rates.values());samples.to_parquet(OUT/'samples.parquet',index=False)
 audit=json.loads((OUT/'preparation_audit.json').read_text());audit['bridge_sha256']['samples.parquet']=digest(OUT/'samples.parquet');audit['member_manifest_sha256']=digest(OUT/'member_manifest.json');audit['stock_manifest_sha256']=digest(OUT/'stock_source_manifest.json');audit['breadth_coverage']=rates;audit['breadth_data_gate']=passed;audit['stocks']=len(codes)
 (OUT/'preparation_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps({'coverage':rates,'passed':passed,'stocks':len(codes)},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
