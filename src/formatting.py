import pandas as pd


def dataframe_snapshot(df: pd.DataFrame, max_rows: int = 3, max_cols: int = 6) -> list[str]:
    if df.empty:
        return []

    view = df.head(max_rows).copy()
    if len(view.columns) > max_cols:
        view = view.iloc[:, :max_cols]

    lines: list[str] = []
    for _, row in view.iterrows():
        parts = []
        for column, value in row.items():
            text = _clean_cell(value)
            if text:
                parts.append(f"{column}: {text}")
        if parts:
            lines.append("；".join(parts))
    return lines


def _clean_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return " ".join(text.split())
