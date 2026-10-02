import pandas as pd
from research.qlib_episode_learning_common import mature_before


def test_long_holding_label_excluded_until_actual_exit():
    f=pd.DataFrame({'target':[.1,-.1,.2,.4],
        'maturity':pd.to_datetime(['2026-03-02','2026-03-10','2026-03-11',None])},
        index=pd.to_datetime(['2026-02-02','2026-02-03','2026-02-04','2026-02-05']))
    got=mature_before(f,pd.Timestamp('2026-03-10'))
    assert got.index.tolist()==[pd.Timestamp('2026-02-02')]
    assert len(mature_before(f,pd.Timestamp('2026-03-12')))==3
