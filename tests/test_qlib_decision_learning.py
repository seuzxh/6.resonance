import numpy as np
import pandas as pd
from research.qlib_decision_learning_common import split_positions, open_target, EntryChecks


def test_rolling_labels_mature_before_next_segment():
    train,valid,test=split_positions(400,500)
    assert len(train)==252 and len(valid)==63 and len(test)==21
    assert train[-1]+6<valid[0] and valid[-1]+6<test[0]
    assert valid[0]-train[-1]-1==6 and test[0]-valid[-1]-1==6
    assert len(split_positions(495,500)[2])==5


def test_open_target_uses_next_open_and_sixth_open():
    x=pd.Series([999.,100.,120.,90.,80.,70.,110.,np.nan])
    y=open_target(x)
    assert np.isclose(y.iloc[0],1.1*.999**2-1)
    assert y.iloc[1:].isna().all()


def test_entry_checks_audit_without_changing_membership():
    s=EntryChecks({1,3})
    assert 1 in s and 2 not in s and 3 in s
    assert s.checked==[1,2,3]
