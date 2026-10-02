"""冻结判据汇总；不搜索新参数。"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from research.qlib_candidate_rerank_common import OUT


def bootstrap(months):
    x=np.asarray(months,float);n=len(x);rng=np.random.default_rng(0)
    starts=rng.integers(0,n,size=(2000,(n+2)//3))
    indices=(starts[:,:,None]+np.arange(3))%n
    means=x[indices.reshape(2000,-1)[:,:n]].mean(axis=1)
    return np.quantile(means,[.025,.975])


def main():
    r=pd.read_csv(OUT/'results.csv');nav=pd.read_parquet(OUT/'navs.parquet');holds=pd.read_parquet(OUT/'holdings.parquet')
    pairs=[];monthly=[];annual=[];convergence=[]
    for (cost,window),g in r.groupby(['cost','window']):
        base=g[g.variant=='baseline'].set_index('phase')
        for tag,a in g.groupby('variant'):
            a=a.set_index('phase');delta=a.total-base.total
            pairs.append({'cost':cost,'window':window,'variant':tag,'return_delta':delta.median(),
                'wins':int(delta.gt(0).sum()),'dd_delta':(a.dd-base.dd).median(),
                'win_delta':(a.win_rate-base.win_rate).median(),'min_trades':int(a.completed_trades.min()),
                'retention_min':a.retention.min(),'retention_median':a.retention.median()})
    paired=pd.DataFrame(pairs);paired.to_csv(OUT/'paired.csv',index=False)
    for (window,phase,tag),g in nav.groupby(['window','phase','variant']):
        s=g.set_index('date').nav.sort_index();daily=np.log(s).diff();daily.iloc[0]=np.log(s.iloc[0])
        for month,value in daily.resample('ME').sum().items():
            monthly.append({'window':window,'phase':phase,'variant':tag,'month':month,'log_return':value})
        for year,value in daily.groupby(daily.index.year).sum().items():
            annual.append({'window':window,'phase':phase,'variant':tag,'year':year,'return':np.expm1(value)})
    monthly=pd.DataFrame(monthly);monthly.to_csv(OUT/'monthly.csv',index=False)
    pd.DataFrame(annual).to_csv(OUT/'annual.csv',index=False)
    for (window,tag),g in holds.groupby(['window','variant']):
        pivot=g.assign(holding=g.holding.fillna('__CASH__')).pivot(index='date',columns='phase',values='holding').dropna()
        same=pivot.nunique(axis=1).eq(1);different=pivot.index[~same]
        after=pivot.index[pivot.index>different.max()] if len(different) else pivot.index
        convergence.append({'window':window,'variant':tag,'first_equal':str(pivot.index[same].min()),
            'permanent_equal':str(after.min()) if len(after) else None,'equal_days':int(same.sum()),'common_days':len(same)})
    pd.DataFrame(convergence).to_csv(OUT/'phase_convergence.csv',index=False)
    results=[]
    for tag in sorted(set(r.variant)-{'baseline'}):
        p=paired[paired.variant==tag].set_index(['cost','window'])
        main=p.loc[(10,'main')];early=p.loc[(10,'early')];recent=p.loc[(10,'recent')]
        m=monthly[(monthly.window=='main')&(monthly.variant==tag)].pivot(index='month',columns='phase',values='log_return')
        b=monthly[(monthly.window=='main')&(monthly.variant=='baseline')].pivot(index='month',columns='phase',values='log_return')
        excess=(m-b).median(axis=1);lo,hi=bootstrap(excess)
        remain=excess.sum()-excess.max()
        weight=tag.rsplit('_w',1)[1]
        actual=r[(r.cost==10)&(r.window=='main')&(r.variant==tag)].set_index('phase')
        comparisons=[]
        for control_name in ['momentum_w'+weight,'reversal_w'+weight]:
            control=r[(r.cost==10)&(r.window=='main')&(r.variant==control_name)].set_index('phase')
            comparisons.append(((actual.total-control.total).median(),(actual.win_rate-control.win_rate).median()))
        control_gain=min(x[0] for x in comparisons);control_win=min(x[1] for x in comparisons)
        checks={'return_ok':main.return_delta>=.02,'phase_ok':main.wins>=4,'risk_ok':main.dd_delta>=-.01,
            'win_ok':main.win_delta>=.03,'early_ok':early.return_delta>=-.02,'recent_ok':recent.return_delta>=-.02,
            'cost_ok':p.loc[(30,'main')].return_delta>0,
            'trade_count_ok':main.min_trades>=50 and early.min_trades>=15 and recent.min_trades>=15,
            'retention_ok':main.retention_min>=.7,'month_ok':remain>0,
            'control_ok':control_gain>0 and control_win>=0}
        results.append({'variant':tag,**main.to_dict(),'early_delta':early.return_delta,'recent_delta':recent.return_delta,
            'cost30_delta':p.loc[(30,'main')].return_delta,'log_excess_without_best_month':remain,
            'monthly_log_low95':lo,'monthly_log_high95':hi,'control_gain':control_gain,'control_win':control_win,
            **checks,'point_pass':all(checks.values())})
    result=pd.DataFrame(results).set_index('variant');result['neighbor_pass']=False
    for tag in result.index:
        name,q=tag.rsplit('_w',1);q=int(q);neighbors=[]
        for other in result.index:
            oname,oq=other.rsplit('_w',1);oq=int(oq)
            if name==oname and abs(q-oq)==25:neighbors.append(other)
            elif q==oq and '_tree' in name and oname[:-1]==name[:-1] and sorted([int(name[-1]),int(oname[-1])]) in [[3,4],[4,6]]:neighbors.append(other)
        result.at[tag,'neighbor_pass']=bool(result.loc[neighbors,'point_pass'].any()) if neighbors else False
    result['adopt_candidate']=result.point_pass & result.neighbor_pass
    result.to_csv(OUT/'assessment.csv')
    print(result[['return_delta','win_delta','dd_delta','early_delta','recent_delta','retention_min','min_trades','point_pass','adopt_candidate']].to_string())
    print('candidates:',result.index[result.adopt_candidate].tolist(),flush=True)


if __name__=='__main__':main()
