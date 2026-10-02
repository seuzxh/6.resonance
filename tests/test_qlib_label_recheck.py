import numpy as np
import pandas as pd
from research.qlib_label_recheck_common import mature_subset, complete_trades


def test_label_maturity_excludes_last_signal_before_boundary():
    cal=pd.bdate_range('2026-01-05',periods=8)
    idx=pd.MultiIndex.from_product([cal,['A']],names=['datetime','instrument'])
    frame=pd.DataFrame({'label':np.arange(8.)},index=idx)
    got=mature_subset(frame,cal,cal[0],cal[4],horizon=2)
    assert got.index.get_level_values('datetime').tolist()==list(cal[:3])
    assert mature_subset(frame,cal,cal[-1],cal[-1],horizon=2).empty


def test_switch_closes_old_asset_at_its_open_not_new_asset_price():
    cal=pd.bdate_range('2026-01-05',periods=3)
    opens=pd.DataFrame({'A':[100.,110.,120.],'B':[190.,200.,180.]},index=cal)
    trades=pd.DataFrame([
        {'date':cal[0],'type':'entry','from':None,'to':'A','price':100.},
        {'date':cal[1],'type':'switch','from':'A','to':'B','price':200.},
        {'date':cal[2],'type':'exit','from':'B','to':None,'price':180.}])
    out=complete_trades(trades,opens,10)
    assert out.code.tolist()==['A','B']
    np.testing.assert_allclose(out.net_return,[1.1*.999**2-1,.9*.999**2-1])
    assert complete_trades(trades.iloc[:1],opens,10).empty
