import pandas as pd
import numpy as np

from tabrepo.predictions import TabularPredictionsInMemory
from tabrepo.repository import EvaluationRepository
from tabrepo.simulation.ground_truth import GroundTruth
from tabrepo.simulation.simulation_context import ZeroshotSimulatorContext


np.random.seed(0)


def make_random_metric(model):
    output_cols = ['time_train_s', 'time_infer_s', 'metric_error', 'metric_error_val']
    metric_value_dict = {
        "NeuralNetFastAI_r1": 1.0,
        "NeuralNetFastAI_r2": 2.0,
        "b1": -1.0,
        "b2": -2.0
    }
    metric_value = metric_value_dict[model]
    return {output_col: (i + 1) * metric_value for i, output_col in enumerate(output_cols)}


def load_context_artificial(**kwargs):
    # TODO write specification of dataframes schema, this code produces a minimal example that enables
    #  to use all the features required in evaluation such as listing datasets, evaluating ensembles or
    #  comparing to baselines
    datasets = ["ada", "abalone"]
    tids = [359944, 359946]
    n_folds = 3
    models = ["NeuralNetFastAI_r1", "NeuralNetFastAI_r2"]
    baselines = ["b1", "b2"]

    configs_full = {model: {} for model in models}

    df_metadata = pd.DataFrame([{
        'dataset': dataset,
        'task_type': "TaskType.SUPERVISED_CLASSIFICATION",
    }
        for tid, dataset in zip(tids, datasets)
    ])

    df_results_by_dataset_automl = pd.DataFrame({
        "dataset": dataset,
        "fold": fold,
        "framework": baseline,
        "problem_type": "regression",
        **make_random_metric(baseline)
    } for fold in range(n_folds) for baseline in baselines for dataset in datasets
    )
    df_raw = pd.DataFrame({
        "dataset": dataset,
        "tid": tid,
        "fold": fold,
        "framework": model,
        "problem_type": "regression",
        "metric": "root_mean_squared_error",
        **make_random_metric(model)
    } for fold in range(n_folds) for model in models for (tid, dataset) in zip(tids, datasets)
    )
    zsc = ZeroshotSimulatorContext(
        df_raw=df_raw,
        df_results_by_dataset_automl=df_results_by_dataset_automl,
        folds=list(range(n_folds)),
        df_metadata=df_metadata,
    )
    pred_dict = {
        dataset_name: {
            fold: {
                "pred_proba_dict_val": {
                    m: np.random.rand(123, 25)
                    for m in models
                },
                "pred_proba_dict_test": {
                    m: np.random.rand(13, 25)
                    for m in models
                }
            }
            for fold in range(n_folds)
        }
        for dataset_name in datasets
    }
    zeroshot_pred_proba = TabularPredictionsInMemory.from_dict(pred_dict)

    make_dict = lambda size: {
        dataset: {
            fold: pd.Series(np.random.randint(low=0, high=25, size=size))
            for fold in range(n_folds)
        }
        for dataset in datasets
    }

    zeroshot_gt = GroundTruth(label_val_dict=make_dict(123), label_test_dict=make_dict(13))

    return zsc, configs_full, zeroshot_pred_proba, zeroshot_gt


def load_repo_artificial():
    zsc, configs_full, zeroshot_pred_proba, zeroshot_gt = load_context_artificial()
    return EvaluationRepository(
        zeroshot_context=zsc,
        tabular_predictions=zeroshot_pred_proba,
        ground_truth=zeroshot_gt,
    )


if __name__ == '__main__':
    load_context_artificial()
