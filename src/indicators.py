import numpy as np
import pandas as pd


def enrich_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]

    for window in (5, 10, 20, 60):
        df[f"ma{window}"] = close.rolling(window).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi14"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["dif"] = ema12 - ema26
    df["dea"] = df["dif"].ewm(span=9, adjust=False).mean()
    df["macd"] = (df["dif"] - df["dea"]) * 2

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    df["boll_mid"] = mid
    df["boll_upper"] = mid + 2 * std
    df["boll_lower"] = mid - 2 * std

    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    df["k"] = rsv.ewm(com=2, adjust=False).mean()
    df["d"] = df["k"].ewm(com=2, adjust=False).mean()
    df["j"] = 3 * df["k"] - 2 * df["d"]

    df["volume_ma5"] = df["volume"].rolling(5).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma5"].replace(0, np.nan)
    return df


def summarize_technical(df: pd.DataFrame, volume_ratio_threshold: float, rsi_overbought: float, rsi_oversold: float) -> list[str]:
    if df.empty or len(df) < 60:
        return ["历史数据不足，暂不生成完整技术面判断。"]

    latest = df.iloc[-1]
    previous = df.iloc[-2]
    signals: list[str] = []

    close = latest["close"]
    pct = latest.get("pct_change", np.nan)
    signals.append(f"收盘 {close:.2f}，日涨跌幅 {pct:.2f}%。")

    if latest["ma5"] > latest["ma20"] and previous["ma5"] <= previous["ma20"]:
        signals.append("MA5 上穿 MA20，短线转强信号。")
    elif latest["ma5"] < latest["ma20"] and previous["ma5"] >= previous["ma20"]:
        signals.append("MA5 下穿 MA20，短线转弱信号。")
    else:
        signals.append(f"均线：MA5 {latest['ma5']:.2f}，MA20 {latest['ma20']:.2f}，MA60 {latest['ma60']:.2f}。")

    if latest["macd"] > 0 and previous["macd"] <= 0:
        signals.append("MACD 柱线翻红。")
    elif latest["macd"] < 0 and previous["macd"] >= 0:
        signals.append("MACD 柱线翻绿。")
    else:
        signals.append(f"MACD：DIF {latest['dif']:.3f}，DEA {latest['dea']:.3f}，柱 {latest['macd']:.3f}。")

    if latest["rsi14"] >= rsi_overbought:
        signals.append(f"RSI14 {latest['rsi14']:.1f}，处于偏热区。")
    elif latest["rsi14"] <= rsi_oversold:
        signals.append(f"RSI14 {latest['rsi14']:.1f}，处于偏冷区。")
    else:
        signals.append(f"RSI14 {latest['rsi14']:.1f}，处于中性区。")

    if close > latest["boll_upper"]:
        signals.append("收盘价突破布林上轨，注意趋势延续与回落风险。")
    elif close < latest["boll_lower"]:
        signals.append("收盘价跌破布林下轨，注意超跌修复与继续走弱风险。")

    if latest["volume_ratio"] >= volume_ratio_threshold:
        signals.append(f"成交量为 5 日均量的 {latest['volume_ratio']:.2f} 倍，明显放量。")

    signals.append(f"KDJ：K {latest['k']:.1f}，D {latest['d']:.1f}，J {latest['j']:.1f}。")
    return signals
