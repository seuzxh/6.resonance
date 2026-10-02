"""决策质量学习的冻结边界与桥文件定义。"""
from pathlib import Path
import numpy as np

OUT=Path('outputs/qlib_decision_learning')
FEATURES=['ret5','ret20','relative10','vol20','dd10','anchor3','anchor10','anchor_dd10',
          'score','sync','capture','score_gap']


def split_positions(start,total):
    assert start>=327
    return np.arange(start-327,start-75),np.arange(start-69,start-6),np.arange(start,min(start+21,total))


def open_target(open_):
    return open_.shift(-6)/open_.shift(-1)*.999**2-1


class EntryChecks(set):
    """记录原引擎实际查询的空仓入场日；集合行为保持不变。"""
    def __init__(self,values):
        super().__init__(values)
        self.checked=[]
    def __contains__(self,value):
        self.checked.append(value)
        return super().__contains__(value)
