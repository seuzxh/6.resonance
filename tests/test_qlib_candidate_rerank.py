import numpy as np
import pandas as pd
from research.qlib_candidate_rerank_common import fuse_ranking


def test_constant_model_preserves_original_order_and_ties():
    original=pd.DataFrame({'concept':['C','B','A'],'score':[.9,.8,.7]})
    got=fuse_ranking(original,{'A':1.,'B':1.,'C':1.},.75)
    assert got.concept.tolist()==['C','B','A']
    got=fuse_ranking(original,{'A':3.,'B':2.,'C':1.},.5)
    assert got.concept.tolist()==['C','B','A']


def test_missing_candidate_score_falls_back_whole_original_list():
    original=pd.DataFrame({'concept':['A','B'],'score':[1.,.5]})
    assert fuse_ranking(original,{'A':1.},.75) is original
    assert fuse_ranking(original,{'A':np.nan,'B':2.},.75) is original
    assert fuse_ranking(original,{'A':1.,'B':2.},.75).concept.tolist()==['B','A']


def test_exact_fusion_tie_with_four_candidates_preserves_order():
    original=pd.DataFrame({'concept':['0','1','2','3'],'score':[4.,3.,2.,1.]})
    got=fuse_ranking(original,{'0':0.,'1':0.,'2':1.,'3':1.},.5)
    assert got.concept.tolist()==['0','2','1','3']
