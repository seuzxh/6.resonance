import numpy as np
import pandas as pd
from research.qlib_volume_rerank_common import volume_features


def inputs():
    index=pd.bdate_range('2026-01-01',periods=25)
    return {k:pd.DataFrame({'A':np.arange(1.,26.) if k=='volume' else np.full(25,v)},index=index)
            for k,v in [('close',100.),('open',90.),('high',110.),('low',90.),('volume',0.)]}


def test_volume_and_intraday_features_match_hand_calculation():
    x=inputs();f=volume_features(x)
    assert np.isclose(f['volume_ratio'].iloc[-1,0],23/15.5-1)
    assert f['signed_volume'].iloc[-1,0]==0
    assert f['close_location'].iloc[-1,0]==0
    assert np.isclose(f['day_night'].iloc[-1,0],10*np.log(100/90))
    x['volume'].iloc[-1,0]=0
    assert np.isnan(volume_features(x)['volume_ratio'].iloc[-1,0])


def test_future_prices_and_volume_cannot_change_earlier_features():
    x=inputs();before=volume_features(x)
    for frame in x.values():frame.iloc[20:]*=3
    after=volume_features(x)
    for name in before:pd.testing.assert_frame_equal(before[name].iloc[:20],after[name].iloc[:20])
