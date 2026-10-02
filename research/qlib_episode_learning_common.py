"""条件模拟交易标签的成熟边界。"""
from pathlib import Path
import numpy as np
OUT=Path('outputs/qlib_episode_learning')


def mature_before(frame,deadline):
    return frame.loc[frame.maturity.notna() & (frame.maturity<deadline) & np.isfinite(frame.target)].copy()
