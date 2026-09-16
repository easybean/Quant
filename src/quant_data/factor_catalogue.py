"""Data-independent Chinese documentation for the factor laboratory.

This is a description of the current local calculator, not a claim that an
indicator has validated investment returns.  It distinguishes mathematical
inputs from the OHLCV frame passed to ta's aggregate wrapper.
"""
from __future__ import annotations

from typing import Any

CATALOGUE_CATEGORIES = (
    "收益与动量", "趋势与均线", "价格位置与通道", "震荡与强弱", "波动与风险",
    "成交量", "量价关系", "K线结构", "统计与回归", "机器学习输入",
)
_TA_URL = "https://technical-analysis-library-in-python.readthedocs.io/en/latest/ta.html"
_QLIB_URL = "https://raw.githubusercontent.com/microsoft/qlib/main/qlib/contrib/data/loader.py"

_UNAVAILABLE = {
    "ta_trend_psar": "暂不可运行：ta 0.11 聚合输出没有 trend_psar 列；待兼容映射修复。",
    "ta_trend_ichimoku_a_visual": "暂不可运行：实际列名为 trend_visual_ichimoku_a，不是该旧 ID；且视觉前移序列含 fillna 全序列均值风险，不可用于回测。",
    "ta_trend_ichimoku_b_visual": "暂不可运行：实际列名为 trend_visual_ichimoku_b，不是该旧 ID；且视觉前移序列含 fillna 全序列均值风险，不可用于回测。",
    **{f"qlib158_VWAP{i}": "暂不可运行：derived/structural-v1 没有 vwap，当前计算保持 NaN，未以典型价替代。" for i in range(5)},
    "qlib158_CLOSE0": "不宜单因子检验：close[t]/close[t] 恒为 1，没有横截面信号。",
    "qlib158_VOLUME0": "不宜单因子检验：volume[t]/volume[t] 恒为 1（非零时），没有横截面信号。",
}

def availability(factor_id: str) -> tuple[bool, str]:
    if factor_id in _UNAVAILABLE:
        return False, _UNAVAILABLE[factor_id]
    return True, "有当前计算定义，尚不表示已验证收益；可用行数取决于历史窗口、非零分母和输入缺失情况。"

# suffix after volume_/volatility_/trend_/momentum_/others_.  Each entry is
# (中文名, 公式, 数学输入, 用途, 特有局限).  ta wrapper receives OHLCV, but
# this table is deliberately narrower whenever the mathematics is narrower.
_TA_DOCS: dict[str, tuple[str, str, str, str, str]] = {
 "adi":("累积派发/吸筹线","累计 CLV×volume，CLV=((close-low)-(high-close))/(high-low)；ta 将无定义的 CLV 填为0。","high、low、close、volume","资金流方向与价格区间位置。","high=low 时该期按0贡献；累计量依赖起点。"),
 "obv":("能量潮","累计 np.where(close<close[1], -volume, volume)：上涨、平盘及首行都加volume。","close、volume","价量确认或背离。","平盘处理与常见“平盘不变”版本不同；累计值不可按绝对水平比较。"),
 "cmf":("蔡金资金流","20期 Σ(CLV×volume)/Σvolume；ta 将无定义的 CLV 填为0。","high、low、close、volume","区间内买卖压力。","high=low 时该期CLV按0；零成交量窗口仍可能无定义。"),
 "fi":("力量指标","(close-close[1])×volume 的13期 EMA。","close、volume","带成交量的价格推动力。","量纲受成交量尺度影响。"),
 "em":("简易波动","(high.diff()+low.diff())×(high-low)/(2×volume)×10^8；原EM不平滑。","high、low、volume","低量下价格移动效率。","零区间或零量不稳定。"),
 "sma_em":("简易波动均线","Easy of Movement 的14期 SMA。","high、low、volume","平滑的移动效率。","继承 EM 零分母问题并滞后。"),
 "vpt":("量价趋势","累计 volume×(close-close[1])/close[1]。","close、volume","量价同向确认。","累计值依赖样本起点。"),
 "vwap":("成交量加权平均价","14期 Σ(典型价×volume)/Σvolume，典型价=(high+low+close)/3。","high、low、close、volume","价格相对近期成交重心。","这是滚动指标，不是逐笔VWAP；零量窗口无定义。"),
 "mfi":("资金流量指数","14期典型价资金流按涨跌分正负后转换为0–100。","high、low、close、volume","量能加权超买超卖。","阈值不是交易规则。"),
 "nvi":("负量指标","仅 volume[t]<volume[t-1] 时按价格收益更新的累计指数。","close、volume","低量日价格趋势。","累计基数依赖起点。"),
 "bbm":("布林中轨","20期 close SMA。","close","价格均值基准。","均线滞后。"), "bbh":("布林上轨","20期均值+2×标准差。","close","相对高位。","参数并非普适阈值。"), "bbl":("布林下轨","20期均值−2×标准差。","close","相对低位。","参数并非普适阈值。"),
 "bbw":("布林带宽","(上轨-下轨)/中轨×100，20期、2倍标准差。","close","波动收缩/扩张。","中轨接近零不稳定。"), "bbp":("布林位置","(close-下轨)/(上轨-下轨)。","close","价格在通道的位置。","带宽为零无定义。"), "bbhi":("突破布林上轨","close>上轨的0/1标记。","close","事件型突破筛选。","离散且可能频繁反复。"), "bbli":("跌破布林下轨","close<下轨的0/1标记。","close","事件型下破筛选。","离散且可能频繁反复。"),
 "kcc":("肯特纳中轨","原始版本：SMA((high+low+close)/3,10)。","high、low、close","通道中心。","这是 ta 默认 original_version=True，不是EMA±ATR版本。"), "kch":("肯特纳上轨","rolling mean((4high-2low+close)/3,10,min_periods=0)。","high、low、close","上行通道边界。","这是原始 Keltner 口径，不是 ATR 上轨。"), "kcl":("肯特纳下轨","rolling mean((-2high+4low+close)/3,10,min_periods=0)。","high、low、close","下行通道边界。","这是原始 Keltner 口径，不是 ATR 下轨。"),
 "kcw":("肯特纳宽度","(上轨-下轨)/中轨×100。","high、low、close","波动尺度。","中轨接近零不稳定。"), "kcp":("肯特纳位置","(close-下轨)/(上轨-下轨)。","high、low、close","通道位置。","带宽为零无定义。"), "kchi":("突破肯特纳上轨","close>上轨的0/1标记。","high、low、close","事件型突破。","不是收益已验证信号。"), "kcli":("跌破肯特纳下轨","close<下轨的0/1标记。","high、low、close","事件型下破。","不是收益已验证信号。"),
 "dcl":("唐奇安下轨","20期 low 最小值。","low","近期区间下界。","极值对异常价敏感。"), "dch":("唐奇安上轨","20期 high 最大值。","high","近期区间上界。","极值对异常价敏感。"), "dcm":("唐奇安中轨","20期上下轨平均。","high、low","区间中心。","由极值决定，非成交重心。"), "dcw":("唐奇安宽度","(max(high,20)-min(low,20))/SMA(close,20)×100。","high、low、close","区间波动尺度。","SMA(close,20)接近零不稳定。"), "dcp":("唐奇安位置","(close-下轨)/(上轨-下轨)。","high、low、close","收盘区间位置。","区间为零无定义。"),
 "atr":("平均真实波幅","Wilder 10期 TR 平滑均值；TR=max(high-low,|high-prev_close|,|low-prev_close|)。","high、low、close","绝对波动/风控尺度。","这是 ta 聚合列的10期ATR；自定义 atr_14_pct 才是14期。"), "ui":("终极回撤指数","14期相对滚动高点回撤的均方根。","close","下行波动。","只反映窗口内历史回撤。"),
 "macd":("MACD线","EMA(close,12)-EMA(close,26)。","close","趋势动量。","EMA滞后。"), "macd_signal":("MACD信号线","MACD 的9期 EMA。","close","平滑MACD。","较MACD更滞后。"), "macd_diff":("MACD柱","MACD-信号线。","close","动量加速/减速。","交叉不是交易结论。"),
 "sma_fast":("快速简单均线","12期 close SMA。","close","短期趋势基准。","滞后。"), "sma_slow":("慢速简单均线","26期 close SMA。","close","中期趋势基准。","滞后。"), "ema_fast":("快速指数均线","12期 close EMA。","close","短期趋势平滑。","初值与滞后影响。"), "ema_slow":("慢速指数均线","26期 close EMA。","close","中期趋势平滑。","初值与滞后影响。"),
 "vortex_ind_pos":("正向涡旋线","14期正向移动和/真实波幅和。","high、low、close","方向性趋势确认。","震荡市易反复。"), "vortex_ind_neg":("负向涡旋线","14期负向移动和/真实波幅和。","high、low、close","方向性趋势确认。","震荡市易反复。"), "vortex_ind_diff":("涡旋差","正向涡旋线-负向涡旋线。","high、low、close","浓缩方向差异。","交叉不等于可交易信号。"),
 "trix":("TRIX","三重15期 EMA(close) 的一期百分比变化。","close","滤噪后的动量。","多重平滑明显滞后。"), "mass_index":("质量指数","9期 EMA(high-low)/二次EMA 的25期和。","high、low","价格区间扩张。","不提供方向。"), "dpo":("去趋势价格振荡","close.shift(11,fill_value=close.mean()) - SMA(close,20)。","close","周期性偏离。","fillna=False 时前期仍被20期均线热身掩盖；成熟输出不使用未来数据。"),
 "kst":("KST","四组平滑 ROC 加权和：ROC窗10/15/20/30，平滑10/10/10/15，权重1/2/3/4。","close","多周期动量。","多重平滑滞后。"), "kst_sig":("KST信号线","KST 的9期 SMA。","close","平滑KST。","更滞后。"), "kst_diff":("KST柱","KST-KST信号线。","close","KST动量差。","非收益证明。"),
 "ichimoku_conv":("一目转换线","9期 high/low 中点。","high、low","短期均衡价。","极值敏感。"), "ichimoku_base":("一目基准线","26期 high/low 中点。","high、low","中期均衡价。","极值敏感、滞后。"), "ichimoku_a":("一目先行带A","(转换线+基准线)/2（本非视觉列不前移）。","high、low","云带基准。","勿与视觉前移列混用。"), "ichimoku_b":("一目先行带B","52期 high/low 中点（本非视觉列不前移）。","high、low","长期云带基准。","勿与视觉前移列混用。"),
 "aroon_up":("阿隆上行","26行窗口内 argmax(high)/25×100（参数window=25）。","high","近期新高时效。","窗口实现含26行；使用high。"), "aroon_down":("阿隆下行","26行窗口内 argmin(low)/25×100（参数window=25）。","low","近期新低时效。","窗口实现含26行；使用low。"), "aroon_ind":("阿隆差","阿隆上行-阿隆下行。","high、low","新高/新低时效差。","震荡市易反转。"),
 "adx":("平均趋向指数","14期方向运动与真实波幅平滑得到的 ADX。","high、low、close","趋势强度，不含方向。","热身期较长；不表示涨跌方向。"), "adx_pos":("正向趋向指标","14期平滑 +DI。","high、low、close","上行方向压力。","与-DI配合而非单独交易结论。"), "adx_neg":("负向趋向指标","14期平滑 -DI。","high、low、close","下行方向压力。","与+DI配合而非单独交易结论。"), "cci":("顺势指标","20期 (典型价-SMA(典型价))/(0.015×平均绝对偏差)。","high、low、close","价格偏离近期典型价的程度。","阈值随品种和窗口而变。"),
 "psar_up":("抛物线转向上行","PSAR 上行段，步长0.02、上限0.20。","high、low、close","趋势跟踪止损参考。","震荡市频繁翻转。"), "psar_down":("抛物线转向下行","PSAR 下行段，步长0.02、上限0.20。","high、low、close","趋势跟踪止损参考。","震荡市频繁翻转。"), "psar_up_indicator":("PSAR上行切换","PSAR 转为上行的0/1标记。","high、low、close","方向翻转事件。","离散且可能频繁反复。"), "psar_down_indicator":("PSAR下行切换","PSAR 转为下行的0/1标记。","high、low、close","方向翻转事件。","离散且可能频繁反复。"), "stc":("Schaff趋势循环","MACD的随机化循环；慢50、快23、周期10、平滑3。","close","趋势内周期强弱。","参数敏感。"),
 "rsi":("相对强弱指数","Wilder 14期平均上涨/下跌幅转换为0–100。","close","超买超卖或趋势过滤。","强趋势可长期极端。"), "stoch_rsi":("随机RSI","14期 RSI 在自身14期区间的位置。","close","RSI区间位置。","双层窗口有噪声与滞后。"), "stoch_rsi_k":("随机RSI %K","随机RSI的3期平滑。","close","平滑随机RSI。","较原值滞后。"), "stoch_rsi_d":("随机RSI %D","%K的3期平滑。","close","进一步平滑。","较%K更滞后。"),
 "tsi":("真实强弱指数","100×（价格变化及绝对变化的25/13期双EMA比）。","close","平滑动量强弱。","双平滑滞后。"), "uo":("终极振荡","100×(4×BP/TR(7)+2×BP/TR(14)+BP/TR(28))/7。","high、low、close","多窗口超买超卖。","阈值不是交易规则。"), "stoch":("随机指标%K","14期 (close-lowest low)/(highest high-lowest low)×100。","high、low、close","收盘区间位置。","区间为零无定义。"), "stoch_signal":("随机指标%D","%K的3期 SMA。","high、low、close","平滑%K。","滞后。"), "wr":("威廉指标","14期 (最高high-close)/(最高high-最低low)×-100。","high、low、close","区间位置/强弱。","区间为零无定义。"),
 "ao":("动量震荡","中价=(high+low)/2 的5期SMA-34期SMA。","high、low","短长周期动量差。","均线差滞后。"), "roc":("变动率","close 相对12期前 close 的百分比变化。","close","固定期价格动量。","极端基期放大值。"), "ppo":("百分比价格振荡","(12期EMA-26期EMA)/26期EMA×100。","close","归一化价格动量。","EMA滞后。"), "ppo_signal":("PPO信号线","PPO的9期EMA。","close","平滑PPO。","更滞后。"), "ppo_hist":("PPO柱","PPO-PPO信号线。","close","PPO动量差。","非收益证明。"),
 "pvo":("百分比成交量振荡","(12期volume EMA-26期EMA)/26期EMA×100。","volume","成交量动量。","零量或口径变更会失真。"), "pvo_signal":("PVO信号线","PVO的9期EMA。","volume","平滑PVO。","更滞后。"), "pvo_hist":("PVO柱","PVO-PVO信号线。","volume","成交量动量差。","非收益证明。"), "kama":("考夫曼自适应均线","10期效率比控制平滑，快2、慢30。","close","自适应趋势平滑。","仍有参数与初始化敏感性。"),
 "dr":("日收益率","100×close.pct_change(1)。","close","最短期收益输入。","噪声高。"), "dlr":("日对数收益","100×ln(close/close[1])。","close","可加性收益变换。","要求正价格。"), "cr":("累计收益率","100×(close/close.iloc[0]-1)。","close","样本内累计变化。","以输入序列第一行close为基准，严重依赖样本起点。"),
}

def _ta_detail(factor_id: str) -> dict[str, str]:
    column = factor_id.removeprefix("ta_")
    suffix = column.split("_", 1)[1]
    if column == "trend_psar":
        return {"definition": "抛物线转向（PSAR，旧 ID）。", "formula": "理论上为 high/low 驱动的 PSAR，默认步长0.02、上限0.20；但 ta 0.11 聚合并不输出该总列。", "purpose": "趋势跟踪的止损/翻转参考。", "limitations": "此 ID 暂不可运行；震荡市会频繁翻转，且仅有计算定义、尚未验证收益。", "_requirements": "high、low、close", "_name": "抛物线转向（PSAR，暂不可运行）"}
    if column in {"trend_ichimoku_a_visual", "trend_ichimoku_b_visual"}:
        band = "A" if column.endswith("a_visual") else "B"
        return {"definition": f"一目视觉先行带{band}（旧 ID）。", "formula": f"理论视觉列为一目先行带{band}前移26期；实际 ta 0.11 输出名为 trend_visual_ichimoku_{band.lower()}，不是该 ID。", "purpose": "仅用于图形展示。", "limitations": "此 ID 暂不可运行；视觉前移序列的 fillna 可使用全序列均值，存在未来信息风险，禁止回测。", "_requirements": "high、low", "_name": f"一目视觉先行带{band}（暂不可运行）"}
    doc = _TA_DOCS.get(suffix)
    if not doc:
        return {"definition": f"ta 0.11 聚合输出 {column}。", "formula": "当前 ta 文档默认实现；参数待逐项复核。", "purpose": "技术指标研究。", "limitations": "仅有计算定义，尚未验证收益。", "_requirements": "未核对"}
    name, formula, req, purpose, limitation = doc
    return {"definition": f"{name}（ta 0.11 输出 {column}）。", "formula": formula, "purpose": purpose, "limitations": f"{limitation} 仅有计算定义，尚未验证收益。", "_requirements": req, "_name": name}

def _alpha158_detail(factor_id: str) -> dict[str, str]:
    token = factor_id.removeprefix("qlib158_")
    kind = token.rstrip("0123456789")
    rest = token[len(kind):]
    common = "窗口含当期且须满窗口；仅有计算定义，尚未验证收益。本地为9个K-bar+25个价格滞后+5个成交量滞后+119个滚动项，非官方常见的9+4+145配置。"
    definition = f"Alpha158 词汇中的本地特征 {token}；稳定 ID 不代表严格官方 Alpha158。"
    kbar = {"KMID":"(close-open)/open", "KLEN":"(high-low)/open", "KMID2":"(close-open)/(high-low)", "KUP":"(high-max(open,close))/open", "KUP2":"(high-max(open,close))/(high-low)", "KLOW":"(min(open,close)-low)/open", "KLOW2":"(min(open,close)-low)/(high-low)", "KSFT":"(2close-high-low)/open", "KSFT2":"(2close-high-low)/(high-low)"}
    if token in kbar:
        kbar_names={"KMID":"K线实体/开盘价","KLEN":"K线全长/开盘价","KMID2":"K线实体/全长","KUP":"上影线/开盘价","KUP2":"上影线/全长","KLOW":"下影线/开盘价","KLOW2":"下影线/全长","KSFT":"收盘偏移/开盘价","KSFT2":"收盘偏移/全长"}
        return {"definition":definition,"formula":f"当期 {kbar[token]}。","purpose":"刻画实体、影线或收盘在日内区间的位置。","limitations":"分母为零时缺失；仅有计算定义，尚未验证收益。","_requirements":"open、high、low、close","_name":f"Alpha158 · {kbar_names[token]}"}
    if kind in {"OPEN","HIGH","LOW","CLOSE","VWAP","VOLUME"} and rest:
        n=int(rest); field=kind.lower(); denom="volume[t]" if kind=="VOLUME" else "close[t]"
        req="volume" if kind=="VOLUME" else ("vwap、close" if kind=="VWAP" else f"{field}、close")
        return {"definition":definition,"formula":f"{field}[t-{n}] / {denom}。","purpose":"标准化滞后价格/成交量输入。","limitations":"lag=0 的 CLOSE0/VOLUME0 恒为1；VWAP 当前无数据。仅有计算定义，尚未验证收益。","_requirements":req,"_name":f"Alpha158 · {kind}滞后{n}期"}
    n=int(rest)
    formulas={
      "ROC":(f"close.pct_change({n})=close[t]/close[t-{n}]-1。","close","固定期收益动量；不同于 Qlib 常见 Ref(close,n)/close。"),
      "MA":(f"mean(close,{n})/close[t]。","close","价格相对滚动均值。"), "STD":(f"rolling std(close,{n},ddof=0)/close[t]。","close","价格离散度，不是收益波动率。"),
      "BETA":(f"{n}期 close 对时间序号0…{n-1} 的最小二乘斜率/close[t]。","close","归一化时间趋势斜率；不是对大盘或基准收益回归的 CAPM Beta。"), "RSQR":(f"{n}期 close 对时间序号的线性回归 R²。","close","时间趋势的线性拟合优度；不是预测准确率，也不是未来收益把握度。"), "RESI":(f"(close[t]-(rolling mean+slope×({n}-1)/2))/close[t]。","close","末端相对本地线性拟合残差。"),
      "MAX":(f"max(high,{n})/close[t]。","high、close","相对近期最高价。"), "MIN":(f"min(low,{n})/close[t]。","low、close","相对近期最低价。"), "QTLU":(f"rolling quantile(close,0.8,{n})/close[t]。","close","相对上分位价格。"), "QTLD":(f"rolling quantile(close,0.2,{n})/close[t]。","close","相对下分位价格。"),
      "RANK":(f"close[t] 在{n}期 close 中的 pct rank（average ties）。","close","收盘历史相对位置。"), "RSV":(f"(close[t]-min(low,{n}))/(max(high,{n})-min(low,{n}))。","high、low、close","随机值式区间位置。"),
      "IMAX":(f"argmax(high,{n})/{n}。","high","按 Qlib IdxMax 的最高 high 时点。"), "IMIN":(f"argmin(low,{n})/{n}。","low","按 Qlib IdxMin 的最低 low 时点。"), "IMXD":(f"(argmax(high,{n})-argmin(low,{n}))/{n}。","high、low","按 Qlib 的高点与低点时距。"),
      "CORR":(f"corr(close,log1p(volume)) 的{n}期滚动 Pearson 相关。","close、volume","价格水平与对数量的同步性。"), "CORD":(f"corr(close.pct_change(),volume.pct_change()) 的{n}期滚动 Pearson 相关。","close、volume","日收益与成交量变化相关性。"),
      "CNTP":(f"{n}期 mean(close.pct_change()>0)。","close","上涨日占比。"), "CNTN":(f"{n}期 mean(close.pct_change()<0)。","close","下跌日占比。"), "CNTD":(f"CNTP({n})-CNTN({n})。","close","涨跌日比例差。"),
      "SUMP":(f"Σmax(ret,0)/Σ|ret|，ret=close.pct_change()，{n}期。","close","正收益贡献占比。"), "SUMN":(f"Σ|min(ret,0)|/Σ|ret|，{n}期。","close","负收益贡献占比。"), "SUMD":(f"Σret/Σ|ret|，{n}期。","close","净收益方向；本地是收益变化，不是价格差。"), "VMA":(f"mean(volume,{n})/volume[t]。","volume","当期量相对滚动均量。"),
    }
    formula, req, purpose=formulas[kind]
    kind_names={"ROC":"收益变动率","MA":"价格均值比","STD":"价格标准差比","BETA":"线性趋势斜率","RSQR":"线性趋势R²","RESI":"线性残差比","MAX":"最高价比","MIN":"最低价比","QTLU":"80%分位价比","QTLD":"20%分位价比","RANK":"历史分位排名","RSV":"随机值区间位置","IMAX":"最高点时点","IMIN":"最低点时点","IMXD":"高点低点时距","CORR":"价量对数相关","CORD":"收益量变相关","CNTP":"上涨日占比","CNTN":"下跌日占比","CNTD":"涨跌日比例差","SUMP":"正收益占比","SUMN":"负收益占比","SUMD":"净收益占比","VMA":"成交量均值比"}
    return {"definition":definition,"formula":formula,"purpose":purpose,"limitations":common,"_requirements":req,"_name":f"Alpha158 · {kind_names[kind]}({n})"}

def _custom_detail(fid: str) -> dict[str, str] | None:
    docs={
      "return_5":("5日收益","close.pct_change(5)=close[t]/close[t-5]-1。","close","固定期价格动量。"), "return_20":("20日收益","close.pct_change(20)=close[t]/close[t-20]-1。","close","中期价格动量。"), "return_60":("60日收益","close.pct_change(60)=close[t]/close[t-60]-1。","close","较长期价格动量。"),
      "sma_gap_20":("20日均线乖离","close[t]/SMA(close,20)-1。","close","价格相对短期均值。"), "sma_crossover_20_60":("20/60日均线差","SMA(close,20)/SMA(close,60)-1。","close","短长均线相对位置，不是交易信号。"), "rsi_14":("RSI(14)","ta Wilder RSI：14期平均上涨/下跌幅转0–100。","close","超买超卖或趋势过滤。"),
      "atr_14_pct":("ATR(14)/价格","ta Wilder ATR(14)/close[t]，TR=max(high-low,|high-prev_close|,|low-prev_close|)。","high、low、close","归一化真实波幅。"), "volatility_20":("20日波动率","20期 close.pct_change() 的rolling std(ddof=1)，未年化。","close","收益波动风险尺度。"), "volume_ratio_20":("20日相对成交量","volume[t]/mean(volume,20)。","volume","当期量相对近期均量。"),
    }
    x=docs.get(fid)
    if not x:return None
    name,formula,req,purpose=x
    return {"definition":name,"formula":formula,"purpose":purpose,"limitations":"窗口含当期且须满窗口；缺失值不填补。仅有计算定义，尚未验证收益。","_requirements":req,"_name":name}

def detail(record: dict[str, Any]) -> dict[str, str]:
    """Return complete, UI-safe Chinese documentation for one registry record."""
    fid=str(record["id"])
    if fid.startswith("ta_"):
        result=_ta_detail(fid); source_default=f"ta 0.11 文档：{_TA_URL}"
    elif fid.startswith("qlib158_"):
        result=_alpha158_detail(fid); source_default=f"本地实现；官方词汇参照：{_QLIB_URL}"
    elif fid.startswith("qlib360_"):
        result={"definition":"Alpha360 联合机器学习输入，不作为单因子候选。","formula":"Ref(field,lag)/当期 close（价格）或volume（成交量）；VWAP缺失则NaN。","purpose":"仅用于联合时序模型。","limitations":"lag=0常量输入不宜单检；仅有计算定义，尚未验证收益。","_requirements":"由字段决定"}; source_default=f"本地实现；官方词汇参照：{_QLIB_URL}"
    else:
        result=_custom_detail(fid) or {"definition":str(record.get("说明") or "未提供定义。"),"formula":"未核对。","purpose":str(record.get("预期用途") or "因子研究。"),"limitations":"仅有计算定义，尚未验证收益。","_requirements":"未核对"}; source_default="本地自定义计算"
    requirements=result.pop("_requirements")
    display_name=result.pop("_name", None)
    result["data_requirements"]=f"数学输入：{requirements}。计算调用虽可能传入OHLCV聚合帧，但不表示该指标数学上使用全部OHLCV；只读取 derived/structural-v1，不修改原始行情。"
    supplied=str(record.get("来源") or "").strip()
    result["source"]=(f"{supplied}；" if supplied else "")+source_default
    result["related"]=str(record.get("近似重复组") or "未分组")
    result["availability"]=availability(fid)[1]
    result["name"]=str(display_name or record.get("名称") or result["definition"].split("（",1)[0])
    return result
