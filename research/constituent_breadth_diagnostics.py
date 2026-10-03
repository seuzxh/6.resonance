"""覆盖、同覆盖增量与固定中心图；conda resonance。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.constituent_breadth_common import OUT
from research.constituent_breadth_inputs import digest


def main():
 prep=json.loads((OUT/'preparation_audit.json').read_text());s=pd.read_parquet(OUT/'samples.parquet');a=s[s.base_valid];reasons=[]
 for name,flag in {'zero_members':a.members.eq(0),'members_under20':a.members.lt(20),'exclusive_under20':a.exclusive_members.lt(20),'full_price_coverage_under90':a.member_coverage_min.lt(.9),'exclusive_price_coverage_under90':a.exclusive_coverage_min.lt(.9),'anchor_unavailable':a.anchor_members.eq(0)}.items():
  reasons.append({'reason':name,'rows':int(flag.sum()),'dates':a.loc[flag,'date'].nunique()})
 pd.DataFrame(reasons).to_csv(OUT/'coverage_reasons.csv',index=False)
 pred=pd.read_parquet(OUT/'predictions.parquet');corr=[]
 for model,p in pred.groupby('model'):
  vals=[]
  for date,g in p[p.status=='trained'].dropna(subset=['relative_target','score']).groupby('date'):
   if len(g)>=2 and g.score.nunique()>1 and g.relative_target.nunique()>1:vals.append(g.score.rank().corr(g.relative_target.rank()))
  corr.append({'model':model,'scored_dates':p.loc[p.score.notna(),'date'].nunique(),'rank_correlation_dates':len(vals),'rank_correlation_mean':float(np.mean(vals)) if vals else None,'unknown_target_predictions':int((p.target.isna()&p.score.notna()).sum())})
 pd.DataFrame(corr).to_csv(OUT/'prediction_diagnostics.csv',index=False)
 r=pd.read_csv(OUT/'results.csv');main=r[(r.cost==10)&(r.window=='main')];centers=['baseline','priceall_tree4_w50','matched_tree4_w50','full_tree4_w50','exclusive_tree4_w50'];med=r[r.cost==10].groupby(['window','variant']).median(numeric_only=True);med.to_csv(OUT/'report_medians.csv')
 diagnostics={'member_data_unchanged':digest(OUT/'member_manifest.json')==prep['member_manifest_sha256'],'bridge_unchanged':{n:digest(OUT/n)==h for n,h in prep['bridge_sha256'].items()},'raw_input_unchanged':{k:digest(v)==prep['input_sha256'][k] for k,v in prep['input_paths'].items()},'center_main':main[main.variant.isin(centers)].groupby('variant').median(numeric_only=True).to_dict('index')}
 if OUT.name.startswith('constituent_shrinkage'):
  old=pd.read_csv('outputs/constituent_breadth/results.csv');common=['baseline']+[v for v in old.variant.unique() if v.startswith(('priceall','allmomentum','allreversal'))]
  for v in common:
   x=old[old.variant==v].sort_values(['cost','window','phase']).reset_index(drop=True);y=r[r.variant==v].sort_values(['cost','window','phase']).reset_index(drop=True)
   for col in ['total','dd','win_rate','completed_trades']:np.testing.assert_allclose(x[col],y[col],equal_nan=True,rtol=0,atol=1e-12)
  diagnostics['phase1_price_models_exactly_reproduced']=True
  folds=pd.read_csv(OUT/'folds.csv')
  for group in ['full','exclusive']:
   x=folds[folds.group=='matched'].set_index('fold');y=folds[folds.group==group].set_index('fold')
   pd.testing.assert_frame_equal(x.drop(columns='group'),y.drop(columns='group'))
  diagnostics['enhanced_folds_match_original_coverage']=True
 assert diagnostics['member_data_unchanged'] and all(diagnostics['bridge_unchanged'].values()) and all(diagnostics['raw_input_unchanged'].values())
 (OUT/'diagnostics.json').write_text(json.dumps(diagnostics,ensure_ascii=False,indent=2,default=str))
 import matplotlib
 matplotlib.use('Agg')
 import matplotlib.pyplot as plt
 plt.rcParams['font.sans-serif']=['WenQuanYi Zen Hei'];plt.rcParams['axes.unicode_minus']=False
 nav=pd.read_parquet(OUT/'navs.parquet');fig,axes=plt.subplots(1,2,figsize=(15,5))
 labels={'baseline':'原双锚','priceall_tree4_w50':'原12特征·原覆盖','matched_tree4_w50':'原12特征·共同覆盖','full_tree4_w50':'完整成员增强','exclusive_tree4_w50':'去除共同成员增强'}
 for ax,window in zip(axes,['main','recent']):
  for tag in centers:
   f=nav[(nav.window==window)&(nav.variant==tag)]
   if f.empty:continue
   n=f.pivot(index='date',columns='phase',values='nav').dropna().median(axis=1);ax.plot(n.index,n,label=labels[tag],linewidth=1.4)
  ax.set_yscale('log');ax.grid(alpha=.2);ax.legend(fontsize=9);ax.set_title(('2023-06起' if window=='main' else '2025-01起')+'至2026-09-18');ax.tick_params(axis='x',rotation=25)
 fig.suptitle('双锚学习固定中心：4叶、50%排序融合；次日开盘、单边10个基点、5相位中位')
 fig.text(.5,.015,'样本内上界；概念指数不可直接交易，成本为统一压力假设；存活目录有幸存者偏差，中位曲线不是账户。',ha='center',fontsize=9)
 fig.tight_layout(rect=[0,.05,1,.93]);fig.savefig(OUT/'nav_centers.png',dpi=160);plt.close(fig)
 print('diagnostics and fixed-center chart complete',flush=True)

if __name__=='__main__':main()
