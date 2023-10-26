import ast
import copy
import itertools
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from syne_tune.experiments import load_experiments_df
from tqdm import tqdm

from autogluon_zeroshot.portfolio.zeroshot_selection import zeroshot_configs
from autogluon_zeroshot.repository import EvaluationRepository
from autogluon_zeroshot.repository.time_utils import (
    filter_configs_by_runtime, get_runtime, sort_by_runtime)
from autogluon_zeroshot.utils.parallel_for import parallel_for

default_ensemble_size = 20
@dataclass
class ResultRow:
    taskid: int  # OpenML taskid, also refered to "tid"
    fold: int
    method: str
    test_error: float
    rank: float
    normalized_score: float
    time_train_s: float
    time_infer_s: float
    config_selected: list = None

def evaluate_configs(
        repo: EvaluationRepository,
        rank_scorer,
        normalized_scorer,
        tid: int,
        method: str,
        config_selected: List[str],
        ensemble_size: int = default_ensemble_size,
        config_sampled: List[str] = None,
        folds: List[int] = range(10),
) -> List[ResultRow]:
    """

    :param repo:
    :param rank_scorer:
    :param normalized_scorer:
    :param tid:
    :param method:
    :param ensemble_size:
    :param config_selected:
    :param config_sampled: the list of configurations that was seen, to count total runtime. Default to `config_selected`
    :param folds:
    :return: list of results for each fold in `folds` evaluated on task `tid` with `config_selected` configurations
    """
    if not config_sampled:
        config_sampled = config_selected
    if ensemble_size is None:
        ensemble_size = default_ensemble_size

    # Makes results invariant to config order (otherwise tie breaking logic in Caruana selection can make the
    # result depend on configuration order)
    config_selected = list(sorted(config_selected.copy()))

    metric_errors, ensemble_weights = repo.evaluate_ensemble(
        tids=[tid],
        config_names=config_selected,
        ensemble_size=ensemble_size,
        backend='native',
        folds=folds,
        rank=False,
    )
    # we expect a tensor of results with shape (n_tasks, n_folds)
    assert metric_errors.shape == (1, len(folds))
    rows = []
    for fold, metric_error in zip(folds, metric_errors[0]):
        dataset_fold_name = repo.task_name(tid=tid, fold=fold)
        config_weights = ensemble_weights[dataset_fold_name]

        # select configurations used in the ensemble as infer time only depends on the models with non-zero weight.
        config_selected_ensemble = [
            config
            for config, weight in zip(config_selected, config_weights)
            if weight != 0
        ]
        runtimes = get_runtime(
            repo=repo,
            tid=tid,
            fold=fold,
            config_names=config_sampled,
            runtime_col='time_train_s',
        )
        latencies = get_runtime(
            repo=repo,
            tid=tid,
            fold=fold,
            config_names=config_selected_ensemble,
            runtime_col='time_infer_s',
        )
        rows.append(ResultRow(
            taskid=tid,
            fold=fold,
            method=method,
            test_error=metric_error,
            rank=rank_scorer.rank(dataset_fold_name, metric_error),
            normalized_score=normalized_scorer.rank(dataset_fold_name, metric_error),
            time_train_s=sum(runtimes.values()) * 8,  # TODO hack to make results comparable, we should get rid of this
            time_infer_s=sum(latencies.values()),
            config_selected=config_sampled,
        ))
    return rows


def framework_default_results(repo: EvaluationRepository, dataset_names: List[str], n_eval_folds: int, rank_scorer, normalized_scorer, **kwargs) -> List[ResultRow]:
    """
    :return: evaluations of default models (e.g. 'CatBoost_c1_BAG_L1') and the best/ensemble of all default models
    """
    def evaluate_tid(dataset_name, default, repo, rank_scorer, normalized_scorer):
        name, configs, ensemble_size = default
        return evaluate_configs(
            repo=repo,
            rank_scorer=rank_scorer,
            normalized_scorer=normalized_scorer,
            config_selected=configs,
            ensemble_size=ensemble_size,
            tid=repo.dataset_to_tid(dataset_name),
            folds=range(n_eval_folds),
            method=name,
        )
    default_models = [x for x in repo.list_models() if "_c" in x]
    defaults = [
        ('CatBoost (default)', ['CatBoost_c1_BAG_L1'], 1),
        ('ExtraTrees (default)', ['ExtraTrees_c1_BAG_L1'], 1),
        ('LightGBM (default)', ['LightGBM_c1_BAG_L1'], 1),
        ('NeuralNetFastAI (default)', ['NeuralNetFastAI_c1_BAG_L1'], 1),
        ('RandomForest (default)', ['RandomForest_c1_BAG_L1'], 1),
        ('All (default)', default_models, 1),
        ('All (default + ensemble)', default_models, 20),
    ]

    list_rows = parallel_for(
        evaluate_tid,
        inputs=list(itertools.product(dataset_names, defaults)),
        context=dict(repo=repo, rank_scorer=rank_scorer, normalized_scorer=normalized_scorer),
        engine="ray",
    )
    return [x for l in list_rows for x in l]


def sample_and_pick_best(
        repo: EvaluationRepository, tid: int, fold: int, framework_type: Optional[str], n_samples: int, n_output: int,
) -> Tuple[List[str], List[str]]:
    """
    :return: Samples `n_samples` random configurations for the given task and returns the top `n_output` configurations
    based on validation scores. If `framework_type` is specified then only configuration of this framework are considered.
    Returns the configurations sampled and the configurations chosen.
    """
    if n_output is None:
        n_output = default_ensemble_size
    df_score_val = repo._zeroshot_context.df_results_by_dataset_vs_automl

    # gets rows with desired task and framework
    mask = (df_score_val['tid'] == tid) & (df_score_val.fold == fold)
    if framework_type:
        mask &= (df_score_val.framework.str.contains(framework_type))
    df_sub = df_score_val[mask].reset_index()

    if len(df_sub) == 0:
        # assert len(df_sub) > 0, f"missing data {tid} {framework_type}"
        print(f"missing data {tid} {fold} {framework_type}")

    # sample random configurations with desired length
    if n_samples and len(df_sub) > n_samples:
        df_sub = df_sub.sample(n=n_samples).reset_index()

    # pick top `n_output` configurations with the best validation loss
    top_config_indices = df_sub["score_val"].argsort().values[-n_output:][::-1]
    best_configs = df_sub.loc[top_config_indices, "framework"].tolist()
    return df_sub["framework"].tolist(), best_configs


def framework_name(framework_type, n_configs, ensemble_size) -> str:
    method = framework_type if framework_type else "All"
    if not n_configs and not ensemble_size:
        return method
    else:
        suffix = "" if n_configs == 1 else f"{n_configs} samples"
        suffix += " + ensemble" if ensemble_size and ensemble_size > 1 else ""
        method += f" ({suffix})"
    return method

def framework_best_results(
        repo: EvaluationRepository, dataset_names: List[str], n_eval_folds: int, rank_scorer, normalized_scorer,
        n_configs: int = [100],
        ensemble_size: int = default_ensemble_size,
        framework_types=["CatBoost", "NeuralNetFastAI", "LightGBM", "RandomForest", "ExtraTrees"],
        **kwargs) -> List[ResultRow]:
    """
    Evaluates best configurations among `n_configs` random draws and ensemble built with `ensemble_size`
    configurations with highest validation scores among the `n_configs` configurations.
    """
    def evaluate_tid(dataset_name, n_configs, framework_type, ensemble_size, repo, rank_scorer, normalized_scorer):
        tid = repo.dataset_to_tid(dataset_name)
        rows = []

        for fold in range(n_eval_folds):
            config_sampled, config_selected = sample_and_pick_best(
                repo=repo,
                n_samples=n_configs,
                n_output=ensemble_size,
                tid=tid,
                fold=fold,
                framework_type=framework_type,
            )

            # evaluate them
            rows += evaluate_configs(
                repo=repo,
                rank_scorer=rank_scorer,
                normalized_scorer=normalized_scorer,
                config_sampled=config_sampled,
                config_selected=config_selected,
                ensemble_size=ensemble_size,
                tid=tid,
                folds=[fold],
                method=framework_name(framework_type, n_configs, ensemble_size),
            )
            rows
        return rows

    ensemble_sizes = [1, ensemble_size]
    list_rows = parallel_for(
        evaluate_tid,
        inputs=list(itertools.product(dataset_names, n_configs, framework_types, ensemble_sizes)),
        context=dict(repo=repo, rank_scorer=rank_scorer, normalized_scorer=normalized_scorer),
        engine="ray",
    )
    return [x for l in list_rows for x in l]


def automl_results(repo: EvaluationRepository, dataset_names: List[str], n_eval_folds: int, rank_scorer, normalized_scorer, **kwargs) -> List[ResultRow]:
    """
    :return: evaluation of AutoGluon medium/high/best quality.
    """
    automl_df = copy.deepcopy(repo._zeroshot_context.df_results_by_dataset_automl)
    automl_df['fold'] = automl_df['dataset'].map(repo._zeroshot_context.dataset_name_to_fold_dict)
    automl_df['tid'] = automl_df['dataset'].map(repo._zeroshot_context.dataset_name_to_tid_dict)
    automl_df['task'] = automl_df['dataset']
    # FIXME: Instead of returning list of "ResultRow", return this dataframe
    automl_df['dataset'] = automl_df['tid'].map(repo._zeroshot_context.tid_to_dataset_dict)

    rows_automl = []
    for dataset in tqdm(dataset_names):
        tid = repo.dataset_to_tid(dataset)
        for fold in range(n_eval_folds):
            dataset_fold_name = repo.task_name(tid=tid, fold=fold)
            automl_df_fold = automl_df[automl_df['task'] == dataset_fold_name]
            task_automl_dict = automl_df_fold.T.to_dict()

            for k, v in task_automl_dict.items():
                assert tid == v['tid']
                metric_error = v['metric_error']
                rows_automl.append(ResultRow(
                    taskid=tid,
                    fold=v['fold'],
                    method=v['framework'],
                    test_error=metric_error,
                    rank=rank_scorer.rank(dataset_fold_name, metric_error),
                    normalized_score=normalized_scorer.rank(dataset_fold_name, metric_error),
                    time_train_s=v['time_train_s'],
                    time_infer_s=v['time_infer_s'],
                ))

    return rows_automl


def zeroshot_name(
        n_portfolio: int = 20, n_ensemble: int = None, n_training_dataset: int = None, n_training_fold: int = None, n_training_config: int = None,
        max_runtime: float = None
):
    """
    :return: name of the zeroshot method such as Zeroshot-N20-C40 if n_training_dataset or n_training_folds are not
    None, suffixes "-D{n_training_dataset}" and "-S{n_training_folds}" are added, for instance "Zeroshot-N20-C40-D30-S5"
    would be the name if n_training_dataset=30 and n_training_fold=5
    """
    suffix = [
        f"-{letter}{x}" if x is not None else ""
        for letter, x in [("N", n_portfolio), ("D", n_training_dataset), ("S", n_training_fold), ("T", max_runtime), ("M", n_training_config)]
    ]
    if n_ensemble:
        suffix += f"-C{n_ensemble}"
    suffix = "".join(suffix)
    return f"Zeroshot{suffix}"


def zeroshot_results(
        repo: EvaluationRepository,
        dataset_names: List[str],
        rank_scorer,
        normalized_scorer,
        n_eval_folds: int,
        n_ensembles: List[int] = [None],
        n_portfolios: List[int] = [20],
        n_training_datasets: List[int] = [None],
        n_training_folds: List[int] = [None],
        n_training_configs: List[int] = [None],
        max_runtimes: List[float] = [None],
        engine: str = "ray",
) -> List[ResultRow]:
    """
    :param dataset_names: list of dataset to use when fitting zeroshot
    :param n_eval_folds: number of folds to consider for evaluation
    :param n_ensembles: number of caruana sizes to consider
    :param n_portfolios: number of folds to use when fitting zeroshot
    :param n_training_datasets: number of dataset to use when fitting zeroshot
    :param n_training_folds: number of folds to use when fitting zeroshot
    :param n_training_configs: number of configurations available when fitting zeroshot TODO per framework
    :param max_runtimes: max runtime available when evaluating zeroshot configuration at test time
    :param engine: engine to use, must be "sequential", "joblib" or "ray"
    :return: evaluation obtained on all combinations
    """
    def evaluate_dataset(test_dataset, n_portfolio, n_ensemble, n_training_dataset, n_training_fold, n_training_config, max_runtime, repo: EvaluationRepository, df_rank, rank_scorer, normalized_scorer, model_frameworks):
        method_name = zeroshot_name(
            n_portfolio=n_portfolio,
            n_ensemble=n_ensemble,
            n_training_dataset=n_training_dataset,
            n_training_fold=n_training_fold,
            max_runtime=max_runtime,
            n_training_config=n_training_config
        )

        # restrict number of evaluation fold
        if n_training_fold is None:
            n_training_fold = n_eval_folds

        # gets all tids that are possible available
        test_tid = repo.dataset_to_tid(test_dataset)
        available_tids = [repo.dataset_to_tid(dataset) for dataset in dataset_names if dataset != test_dataset]
        np.random.shuffle(available_tids)
        if n_training_dataset is None:
            n_training_dataset = len(available_tids)

        # restrict number of training tid availables to fit
        selected_tids = set(available_tids[:n_training_dataset])

        # restrict number of configurations available to fit
        if n_training_config:
            configs = []
            for models_framework in model_frameworks.values():
                if len(models_framework) <= n_training_config:
                    configs += models_framework
                else:
                    configs += list(np.random.choice(models_framework, n_training_config, replace=False))
            df_rank = df_rank.copy().loc[configs]

        # collects all tasks that are available
        train_tasks = []
        for task in df_rank.columns:
            tid, fold = task.split("_")
            if int(tid) in selected_tids and int(fold) < n_training_fold:
                train_tasks.append(task)

        # fit zeroshot portfolio on all available tasks
        indices = zeroshot_configs(-df_rank[train_tasks].values.T, n_portfolio)
        portfolio_configs = [df_rank.index[i] for i in indices]
        # TODO: Technically we should exclude data from the fold when computing the average runtime and also pass the
        #  current fold when filtering by runtime.
        portfolio_configs = sort_by_runtime(repo=repo, config_names=portfolio_configs)
        portfolio_configs = filter_configs_by_runtime(
            repo=repo,
            tid=test_tid,
            fold=0,
            config_names=portfolio_configs,
            # TODO hack to make results comparable, we should get rid of this
            max_cumruntime=max_runtime / 8 if max_runtime else None,
        )
        if len(portfolio_configs) == 0:
            # in case all configurations selected were above the budget, we evaluate a quick backup
            portfolio_configs = ["LightGBM_c1_BAG_L1"]

        return evaluate_configs(
            repo=repo,
            rank_scorer=rank_scorer,
            normalized_scorer=normalized_scorer,
            config_selected=portfolio_configs,
            ensemble_size=n_ensemble,
            tid=test_tid,
            method=method_name,
            folds=range(n_eval_folds),
        )
    dd = repo._zeroshot_context.df_results_by_dataset_vs_automl
    # df_rank = dd.pivot_table(index="framework", columns="dataset", values="score_val").rank()
    # TODO use normalized scores
    df_rank = dd.pivot_table(index="framework", columns="dataset", values="bestdiff").rank(ascending=False)
    df_rank.fillna(value=np.nanmax(df_rank.values), inplace=True)
    assert not any(df_rank.isna().values.reshape(-1))

    model_frameworks = {
        framework: [x for x in repo.list_models() if framework in x]
        for framework in ["CatBoost", "NeuralNetFastAI", "LightGBM", "RandomForest", "ExtraTrees"]
    }

    list_rows = parallel_for(
        evaluate_dataset,
        inputs=list(itertools.product(dataset_names, n_portfolios, n_ensembles, n_training_datasets, n_training_folds, n_training_configs, max_runtimes)),
        context=dict(repo=repo, df_rank=df_rank, rank_scorer=rank_scorer, normalized_scorer=normalized_scorer, model_frameworks=model_frameworks),
        engine=engine,
    )
    return [x for l in list_rows for x in l]



def evaluate_tuning(
        repo: EvaluationRepository, dataset_names: List[str], n_eval_folds: int, rank_scorer, normalized_scorer,
        expname="02-05-v2",
        **kwargs,
) -> List[ResultRow]:
    """
    :param expname: name of the experiment tag passed when launching `scripts/method_comparison/run_method_comparison.py`
    :return: results of top configurations found by local search and zeroshot when using LocalSearch, Zeroshot, Zeroshot ensemble.
    """
    def load_df_tuning(expname):
        name_filter = lambda path: expname in str(path)
        df_results = load_experiments_df(path_filter=name_filter)
        df_results["fold"] = df_results.apply(lambda row: int(row['tuner_name'].split("fold-")[1].split("-")[0]),
                                              axis=1)
        for col in ['configs', 'train_datasets', 'test_datasets']:
            df_results[col] = df_results[f'config_{col}'].apply(lambda x: ast.literal_eval(x))
        return df_results

    def extract_configs_from_tuning_results(df_results):
        rows = []
        for fold in sorted(df_results.fold.unique()):
            df_sub = df_results[(df_results.fold == fold) & (df_results.searcher == "localsearch")]
            # zeroshot config is always the first trial
            row_zeroshot = df_sub.loc[
                df_sub.trial_id == 0, ['configs', 'train_datasets', 'test_datasets', 'train_error', 'test_error']].head(
                1)
            # localsearch config is the one with lowest train error
            row_localsearch = df_sub.sort_values("train_error").loc[:,
                              ['configs', 'train_datasets', 'test_datasets', 'train_error', 'test_error']].head(1)
            assert row_localsearch["train_datasets"].values[0] == row_zeroshot["train_datasets"].values[0]
            rows.append({
                "metafold": fold,
                "zeroshot": row_zeroshot["configs"].values[0],
                "localsearch": row_localsearch["configs"].values[0],
                "train_datasets": row_localsearch["train_datasets"].values[0],
                "test_datasets": row_localsearch["test_datasets"].values[0],
            })
        return rows

    def taskid_to_config(tuning_rows, taskid):
        contains_task = lambda tasks: any(task.split("_")[0] == str(taskid) for task in tasks)
        matches = [row for row in tuning_rows if not contains_task(row['train_datasets'])]
        assert len(matches) >= 1
        return matches[0]

    df_results = load_df_tuning(expname=expname)
    if len(df_results) == 0:
        print(f"No tuning result could be found from the experiment tag passed {expname}, please run run_method_comparison.py with this tag first.")
        return

    tuning_rows = extract_configs_from_tuning_results(df_results)

    rows = []
    for dataset in tqdm(dataset_names):
        tid = repo.dataset_to_tid(dataset)
        for suffix, ensemble_size in [("", 1), (f" (ensemble)", 20)]:
            for method in ["zeroshot", "localsearch"]:
                test_errors, ensemble_weights = repo.evaluate_ensemble(
                    tids=[tid],
                    config_names=taskid_to_config(tuning_rows, tid)[method],
                    ensemble_size=ensemble_size,
                    rank=False,
                )
                assert test_errors.shape[0] == 1  # we send one model, we should get one row back
                for fold in range(n_eval_folds):
                    test_error = test_errors[0][fold]
                    task_name = repo.task_name(tid=tid, fold=fold)
                    rows.append(ResultRow(
                        taskid=tid,
                        fold=fold,
                        method=f"{method}{suffix}".capitalize(),
                        test_error=test_error,
                        rank=rank_scorer.rank(task_name, test_error),
                        normalized_score=normalized_scorer.rank(task_name, test_error),
                    ))
    return rows
