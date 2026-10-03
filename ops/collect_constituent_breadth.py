"""预注册成分数据采集入口；conda resonance，支持逐请求续跑。"""
from pathlib import Path
import sys,json,time,datetime,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from resonance import config
from resonance.ifind import _post
OUT=Path('outputs/constituent_breadth')

def main():
 jobs=pd.read_csv(OUT/'member_jobs.csv',dtype=str);assert len(jobs)<=2000
 config.assert_no_retired(jobs.code.unique());folder=OUT/'members';folder.mkdir(exist_ok=True)
 calls=empty=0;start=time.monotonic()
 for i,row in enumerate(jobs.itertuples(index=False)):
  path=folder/f'{row.code}_{row.date}.json'
  if path.exists():continue
  try:
   data=_post(config.IFIND_DATAPOOL_URL,{'reportname':'p03473','functionpara':{'iv_date':row.date,'iv_zsdm':row.code},'outputpara':'p03473_f001,p03473_f002,p03473_f003'},timeout=30)
  except Exception as e:
   if 'errorcode=-4001' in str(e) and 'no data' in str(e):data={'errorcode':-4001,'errmsg':'no data.','tables':[]};empty+=1
   else:
    (OUT/'collection_error.json').write_text(json.dumps({'code':row.code,'date':row.date,'error':str(e),'position':i},ensure_ascii=False));raise
  payload={'code':row.code,'date':row.date,'fetched_at':datetime.datetime.now().isoformat(),'response':data}
  tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(payload,ensure_ascii=False));tmp.replace(path);calls+=1
  if (i+1)%25==0:print(f'{i+1}/{len(jobs)} new={calls} empty={empty} seconds={time.monotonic()-start:.1f}',flush=True)
 manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('*.json'))}
 assert len(manifest)==len(jobs)
 (OUT/'member_manifest.json').write_text(json.dumps(manifest,indent=2));print('collection complete',len(manifest),flush=True)

if __name__=='__main__':main()
