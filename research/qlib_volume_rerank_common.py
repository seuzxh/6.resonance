"""量价增强实验：四项因果特征。"""
from pathlib import Path
import numpy as np
OUT=Path('outputs/qlib_volume_rerank')
EXTRA=['volume_ratio','signed_volume','close_location','day_night']


def volume_features(wide):
    c,o,h,l,v=[wide[k] for k in ['close','open','high','low','volume']]
    valid=np.isfinite(c)&np.isfinite(o)&np.isfinite(h)&np.isfinite(l)&(c>0)&(o>0)&(h>0)&(l>0)&(h>=l)&(c>=l)&(c<=h)
    c=c.where(valid);o=o.where(valid)
    v=v.where(np.isfinite(v)&(v>0))
    location=((2*c-h-l)/(h-l)).where(h!=l,0.).where(valid)
    return {'volume_ratio':v.rolling(5).mean()/v.rolling(20).mean()-1,
            'signed_volume':(np.sign(c.pct_change(fill_method=None))*v).rolling(5).sum()/v.rolling(5).sum(),
            'close_location':location.rolling(5).mean(),
            'day_night':(np.log(c/o)-np.log(o/c.shift(1))).rolling(5).sum()}
