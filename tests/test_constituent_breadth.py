import numpy as np
import pandas as pd
from research.constituent_breadth_common import membership_date, breadth_pair, all_candidates_valid


def test_membership_strictly_before_signal_month():
    cal=pd.to_datetime(['2024-01-30','2024-01-31','2024-02-01','2024-02-02'])
    assert membership_date(cal,pd.Timestamp('2024-02-01'))==pd.Timestamp('2024-01-31')
    assert membership_date(cal,pd.Timestamp('2024-02-02'))==pd.Timestamp('2024-01-31')
    assert pd.isna(membership_date(cal,pd.Timestamp('2024-01-30')))


def test_overlap_flat_and_missing_denominator():
    r=np.tile([.1,0.,-.1,.2],(5,1))
    assert breadth_pair(r,[True]*4,min_members=2)==(.5,.5)
    assert breadth_pair(r,[False,True,True,True],min_members=2)==(1/3,1/3)
    r[-1,1]=np.nan
    assert np.isnan(breadth_pair(r,[True]*4,min_members=2)[0])
    assert breadth_pair(r,[True]*4,min_members=2,min_coverage=.75)==(2/3,(4*.5+2/3)/5)


def test_too_few_members_and_prior_day_missing():
    r=np.ones((5,20))*.01
    assert np.isnan(breadth_pair(r,[True]*19+[False])[0])
    r[0,:3]=np.nan
    assert np.isnan(breadth_pair(r,[True]*20)[0])


def test_whole_day_mask_ignores_unknown_labels():
    x=pd.DataFrame({'date':[1,1,2,2], 'a':[.1,np.nan,.2,.3], 'target':[1.,2.,np.nan,np.nan]})
    assert all_candidates_valid(x,['a']).tolist()==[False,False,True,True]
