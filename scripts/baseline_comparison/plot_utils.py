from dataclasses import dataclass
from pathlib import Path
from typing import List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.baseline_comparison.baselines import zeroshot_name


@dataclass
class MethodStyle:
    name: str
    color: str
    linestyle: str = None  # linestyle of the method, default to plain
    label: bool = True  # whether to show the method name as label
    label_str: str = None


def iqm(x):
    x = list(sorted(x))
    start = len(x) * 1 // 4
    end = len(x) * 3 // 4
    return np.mean(x[start:end])

def show_latex_table(df: pd.DataFrame):
    df_metrics = compute_avg_metrics(df)
    print(df_metrics.to_latex(float_format="%.2f"))

def compute_avg_metrics(df: pd.DataFrame):
    avg_metrics = {}
    for metric in ["normalized_score", "rank", "time_train_s", "time_infer_s"]:
        avg_metric = df.groupby("method").agg(iqm)[metric]
        avg_metric.sort_values().head(60)
        xx = avg_metric.sort_values()
        avg_metrics[metric] = xx
    df_metrics = pd.DataFrame(avg_metrics).sort_values(by="normalized_score")
    df_metrics.columns = [x.replace("_", "-") for x in df_metrics.columns]
    return df_metrics


def show_cdf(df: pd.DataFrame, method_styles: List[MethodStyle] = None):
    if method_styles is None:
        method_styles = [
            MethodStyle(method, color=None, linestyle=None, label=method)
            for method in df.method.unique()
        ]
    fig, axes = plt.subplots(1, 2, figsize=(8, 3), sharey=True)
    metrics = ["normalized_score", "rank"]
    for i, metric in enumerate(metrics):
        for j, method_style in enumerate(method_styles):
            xx = df.loc[df.method == method_style.name, metric].sort_values()
            if len(xx) > 0:
                if method_style.label:
                    label = method_style.label_str if method_style.label_str else method_style.name
                else:
                    label = None
                axes[i].plot(
                    xx.values, np.arange(len(xx)) / len(xx),
                    # label=method_style.name if method_style.label else None,
                    label=label,
                    color=method_style.color,
                    linestyle=method_style.linestyle,
                    lw=1.5,
                )
                # axes[i].set_title(metric.replace("_", "-"))
                axes[i].set_xlabel(metric.replace("_", "-"))
                if i == 0:
                    axes[i].set_ylabel(f"CDF")
            else:
                print(f"Could not find method {method_style.name}")
    axes[-1].legend(fontsize="small")
    return fig, axes


def show_scatter_performance_vs_time(df: pd.DataFrame, max_runtimes):
    import seaborn as sns
    n_frameworks = 5
    df_metrics = compute_avg_metrics(df)
    autogluon_methods = [
        f"AutoGluon {preset} quality (ensemble)"
        for preset in ["medium", "high", "best"]
    ]
    zeroshot_methods = [zeroshot_name(max_runtime=max_runtime) for max_runtime in max_runtimes]
    cash_methods = df_metrics.index.str.match("All \(.* samples.*ensemble\)")
    fig, axes = plt.subplots(1, 2, sharey=True, figsize=(8, 3))
    for i, metric in enumerate([
        "time-train-s",
        "time-infer-s",
    ]):
        df_metrics[df_metrics.index.isin(zeroshot_methods)].plot(
            kind="scatter", x=metric, y="normalized-score", label="Zeroshot + ensemble", ax=axes[i],
            color=sns.color_palette('bright')[n_frameworks + 1],
            marker="*",
            s=50.0,
        )
        df_metrics[df_metrics.index.isin(autogluon_methods)].plot(
            kind="scatter", x=metric, y="normalized-score", label="AutoGluon + ensemble", ax=axes[i],
            color="black",
            marker="^",
        )
        df_metrics[cash_methods].plot(
            kind="scatter", x=metric, y="normalized-score", label="All (N samples + ensemble)", ax=axes[i],
            marker="D",
            color=sns.color_palette('bright')[n_frameworks]
        )
    return fig, axes
