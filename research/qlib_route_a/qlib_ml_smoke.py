"""冒烟2：因子 DataFrame 直驱 qlib 训练链（免 bin、免 provider、不 qlib.init）。

官方入口链（microsoft/qlib 0.9.7 源码实证）：
  DataHandlerLP.from_df(df)（handler.py:766，docstring："quick data handler"）
    → DatasetH(handler=dh, segments=时间切分)（handler 收对象，不查数据层）
    → LGBModel.fit/predict（只吃 DatasetH）
因子=parquet 行情 pandas 手算（mom5/mom10/vol20/amount20），标签=T+1 收益。
"""
import numpy as np
import pandas as pd
from scipy import stats as sps

# --- 1) parquet → 因子矩阵（pandas 手算，模拟"已有因子数据"）---
bars = pd.read_parquet(
    "/home/zxh/projects/6.resonance/data/cache/daily_bars.parquet",
    columns=["symbol", "date", "close", "amount"],
)
codes = ["399001.SZ", "399303.SZ", "000688.SH", "883957.TI", "885907.TI",
         "885959.TI", "886100.TI", "885300.TI", "885400.TI", "885500.TI"]
bars = bars[bars["symbol"].isin(codes) & (bars["date"] >= "2025-01-01")
            & (bars["date"] <= "2026-09-18")].copy()
bars["date"] = pd.to_datetime(bars["date"])
close = bars.pivot(index="date", columns="symbol", values="close").sort_index()
amount = bars.pivot(index="date", columns="symbol", values="amount").sort_index()
rets = close.pct_change()

feats = {
    "MOM5": close.pct_change(5),
    "MOM10": close.pct_change(10),
    "VOL20": rets.rolling(20).std(),
    "AMT20": amount.pct_change(20),
}
X = pd.concat({k: v.stack() for k, v in feats.items()}, axis=1)
X.index.names = ["datetime", "instrument"]
y = rets.shift(-1).stack().rename("LABEL0").to_frame()
y.index.names = ["datetime", "instrument"]
data = X.join(y, how="inner").dropna()
# qlib 模型层契约：列需 feature/label 分组两级列头（LGBModel._prepare_data 取 df["feature"]/df["label"]）
data.columns = pd.MultiIndex.from_tuples(
    [("feature", c) for c in X.columns] + [("label", "LABEL0")])
print(f"[data] factor matrix {data.shape}，样本日 {data.index.get_level_values(0).nunique()}")

# --- 2) 官方轻量入口：from_df → DatasetH → LGBModel ---
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.model import gbdt as gbdt_mod
from qlib.contrib.model.gbdt import LGBModel

# 离线桩：LGBModel.fit 尾部经 workflow Recorder 记指标（R.log_metrics），
# 该路径要求 qlib.init——研究回放不需要实验跟踪，置空。
gbdt_mod.R.log_metrics = lambda **kw: None

dh = DataHandlerLP.from_df(data)
segments = {
    "train": ("2025-01-01", "2026-03-31"),
    "valid": ("2026-04-01", "2026-06-30"),
    "test": ("2026-07-01", "2026-09-18"),
}
ds = DatasetH(handler=dh, segments=segments)
tr = ds.prepare("train")
print(f"[handler] train slice {tr.shape}，列={list(tr.columns)}")

model = LGBModel(loss="mse", early_stopping_rounds=30, num_boost_round=60,
                 learning_rate=0.05, num_leaves=8, verbose=-1)
model.fit(ds, verbose_eval=0)
pred = model.predict(ds, segment="test")
print(f"[model] 预测 {len(pred)} 条（{pred.index.get_level_values(0).nunique()} 日），"
      f"lgb best_iter={model.model.best_iteration}")

# --- 3) 快速 Rank IC 佐证（test 段逐日 Spearman）---
y_test = ds.prepare("test")["LABEL0"]  # 默认 col_set 展平列头（模型内部用分组口径）
df_ic = pd.concat([pred.rename("p"), y_test.rename("y")], axis=1).dropna()
ic = df_ic.groupby(level="datetime").apply(
    lambda g: sps.spearmanr(g["p"], g["y"])[0]).dropna()
print(f"[IC] test 段日频 Rank IC 均值 {ic.mean():+.4f}（n={len(ic)} 日，"
      f"t={ic.mean()/(ic.std()/np.sqrt(len(ic))):+.2f}）")
print("\n[SMOKE PASS] parquet 因子 → DataHandlerLP.from_df → DatasetH → LGBModel → 预测，"
      "全程免 bin、免 provider、不 qlib.init；预测分数可直接进已验证的 parquet 直驱回测。")
