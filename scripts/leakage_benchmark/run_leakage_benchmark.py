import pathlib
import pickle

from autogluon_zeroshot.repository import (EvaluationRepository,
                                           EvaluationRepositoryZeroshot)
from autogluon_zeroshot.utils.cache import cache_function
from scripts.leakage_benchmark.src.config_and_data_utils import (
    LeakageBenchmarkConfig, LeakageBenchmarkFoldResults)
from scripts.leakage_benchmark.src.stacking_simulator import (
    autogluon_l2_runner, obtain_input_data_for_l2)


def _leakage_analysis(repo, lbc, dataset, fold) -> LeakageBenchmarkFoldResults:
    print(f'Leakage Analysis for {dataset}, fold {fold}...')
    # L1
    l2_X_train, y_train, l2_X_test, y_test, eval_metric, oof_col_names, l1_results, l1_feature_metadata = \
        obtain_input_data_for_l2(repo, lbc.l1_models, dataset, fold)
    # L2
    l2_results, custom_meta_data = autogluon_l2_runner(lbc.l2_models, l2_X_train, y_train, l2_X_test, y_test,
                                                       eval_metric, oof_col_names, l1_feature_metadata,
                                                       problem_type=eval_metric.problem_type,
                                                       get_meta_data=lbc.compute_meta_data,
                                                       debug=lbc.debug_mode,
                                                       plot_insights=lbc.plot_insights,
                                                       l1_model_worst_to_best=list(
                                                           l1_results.sort_values(by='score_val').iloc[:, 0]))
    # LeakageBenchmarkFoldResults.print_leaderboard(l2_results)
    LeakageBenchmarkFoldResults.print_leaderboard(l1_results.sort_values(by='score_val', ascending=True))

    results = LeakageBenchmarkFoldResults(
        fold=fold,
        dataset=dataset,
        l1_leaderboard_df=l1_results,
        l2_leaderboard_df=l2_results,
        custom_meta_data=custom_meta_data
    )
    results.print_leaderboard(results.get_leak_overview_df())
    print(results.custom_meta_data)
    print('... done.')

    return results


def _dataset_subset_filter(repo):
    # Maybe move this to EvaluationRepositoryZeroshot class
    dataset_subset = []
    for dataset in repo.dataset_names():
        md = repo.dataset_metadata(repo.dataset_to_tid(dataset))

        if md['NumberOfClasses'] == 2:
            dataset_subset.append(dataset)

    return dataset_subset


def analyze_starter(repo: EvaluationRepositoryZeroshot, lbc: LeakageBenchmarkConfig, store_results=False):
    # Init
    lbc.repo_init(repo)

    # Stats
    n_datasets = len(lbc.datasets)
    print(f'n_l1_models={len(lbc.l1_models)} | l1_models={lbc.l1_models}')
    print(f'n_l2_models={len(lbc.l2_models)} | l2_models={lbc.l2_models}')
    print(f'n_datasets={n_datasets}')

    # Loop over datasets for benchmark
    file_dir = pathlib.Path(__file__).parent.resolve() / 'output' / 'fold_results_per_dataset'
    file_dir.mkdir(parents=True, exist_ok=True)

    for dataset_num, dataset in enumerate(lbc.datasets, start=1):
        if (file_dir / f'fold_results_{dataset}.pkl').exists() and store_results:
            continue

        print(f"Start Dataset Number {dataset_num}/{n_datasets}")
        fold_results = []
        for fold in repo.folds:
            fold_results.append(_leakage_analysis(repo, lbc, dataset, fold))

        # Save results for fold
        if store_results:
            with open(file_dir / f'fold_results_{dataset}.pkl', 'wb') as f:
                pickle.dump(fold_results, f)


if __name__ == '__main__':
    known_leaking_binary = ['blood-transfusion-service-center', 'GAMETES_Epistasis_3-Way_20atts_0_2H_EDM-1_1',  'kc2',
                            'meta', 'Satellite', 'Click_prediction_small',
                            'Titanic', 'eeg-eye-state', 'GAMETES_Epistasis_2-Way_1000atts_0_4H_EDM-1_EDM-1_1',
                            'APSFailure', 'numerai28_6',
                            'kc1', 'pc3', 'pc4', 'airlines', ]
    quick_leak = ['Titanic', 'blood-transfusion-service-center']
    # Download repository from S3 and cache it locally for re-use in future calls
    repository: EvaluationRepositoryZeroshot = cache_function(
        fun=lambda: EvaluationRepository.load('s3://autogluon-zeroshot/repository/BAG_D244_F1_C16_micro.pkl'),
        cache_name="repo_micro",
    ).to_zeroshot()
    init_lbc = LeakageBenchmarkConfig(
        l1_models=None,
        datasets=quick_leak  # known_leaking_binary # _dataset_subset_filter(repository) #repository.dataset_names()
    )
    analyze_starter(repo=repository, lbc=init_lbc)
