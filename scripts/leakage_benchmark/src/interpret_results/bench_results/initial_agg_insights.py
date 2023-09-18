import json
from pathlib import Path

import numpy as np
import openml
import pandas as pd

from scripts.leakage_benchmark.src.interpret_results.plotting.cd_plot import \
    cd_evaluation

# ---
frameworks = ["AutoGluon_bq_nostack_1h8c_2023_08_28",
              "AutoGluon_bq_stack_1h8c_2023_08_28",
               #"AutoGluon_bq_stack_fix1_1h8c_2023_08_28",
            "AutoGluon_bq_stack_fix_v2_1h8c_2023_08_28",
              ]
problem_types = ["binary"] # ["binary", "multiclass", "regression"]
#datasets = ['rmftsa_ladata', 'blood-transfusion-service-center', 'meta', 'Satellite', 'ilpd', 'jm1', 'Click_prediction_small', 'Titanic', 'ada', 'kdd_el_nino-small', 'numerai28_6', 'GAMETES_Epistasis_3-Way_20atts_0_2H_EDM-1_1', 'eeg-eye-state', 'GAMETES_Epistasis_2-Way_1000atts_0_4H_EDM-1_EDM-1_1', 'madelon', 'mammography', 'airlines', 'APSFailure', 'credit-g', 'kc2', 'pc3', 'pc4']
# ---
lb = pd.read_csv("./leaderboard_preprocessed.csv")
res = pd.read_csv("./results_preprocessed.csv")

lb = lb[lb["problem_type"].isin(problem_types)]
lb = lb[lb["framework_parent"].isin(["AutoGluon_bq_nostack_1h8c", "AutoGluon_bq_stack_1h8c", "AutoGluon_bq_stack_fix1_1h8c",
                                     "AutoGluon_bq_stack_fix_v2_1h8c"])]


def _get_metadata(tids):
    print("Getting MetaData")

    md_file_path = Path("./md_file.json")
    md_file = None
    if md_file_path.exists():
        with open(md_file_path, "r") as f:
            md_file = json.load(f)
        if set(md_file.keys()) == set(tids):
            return md_file
        else:
            md_file = None

    if md_file is None:
        md_file = {
            int(tid): openml.datasets.get_dataset(
                openml.tasks.get_task(
                    int(tid), download_splits=False, download_data=False, download_qualities=True, download_features_meta_data=False
                ).dataset_id,
                download_data=False,
                download_qualities=True,
                download_features_meta_data=False,
            ).qualities
            for tid in tids
        }
        with open(md_file_path, "w") as f:
            json.dump(md_file, f)

    return md_file

import glob
import pathlib
import pickle


def _process_custom_fold_results():
    BASE_PATH = pathlib.Path(__file__).parent.parent.parent.parent.resolve() / 'output'
    file_dir = BASE_PATH / 'fold_results_per_dataset_leak_repo_test_v2'
    data = {}

    for f_path in glob.glob(str(file_dir / 'fold_results_*.pkl')):
        with open(f_path, 'rb') as f:
            fold_data = pickle.load(f)
            if not fold_data:
                raise ValueError(f"Empty fold data in {f_path}")
            data[f_path.split("/")[-1][(len('fold_results_')):-len('.pkl')]] = fold_data

    return data

custom_md = _process_custom_fold_results()

md_map = _get_metadata(res["tid"].unique().astype(int))

res = res[res["problem_type"].isin(problem_types)]
res = res[res["framework"].isin(frameworks)]
#print(len(datasets))
#res = res[~res['dataset'].isin(datasets)]
# Avg over folds
res = res.groupby(by=["dataset", "fold", "framework"]).mean().reset_index().drop(columns=["fold"])
lb = lb.groupby(by=["dataset", "fold", "framework", "model", "framework_parent", 'stack_level']).mean().reset_index().drop(columns=["fold"])

# ------- Leak analysis
print()
# select for stack
lb_info_leak = lb[lb["framework_parent"].isin(["AutoGluon_bq_stack_1h8c"])]
# ignore weighted ensembles
lb_info_leak = lb_info_leak[~lb_info_leak['model'].isin(['WeightedEnsemble_L2', 'WeightedEnsemble_L3', 'autogluon_ensemble',
                                                         'autogluon_single', 'WeightedEnsemble_BAG_L2'])]
# select by best performing model according to val score per layer
lb_info_leak= lb_info_leak.loc[lb_info_leak.groupby(by=['dataset', 'stack_level'])['score_val'].idxmax()]

# # select only those cases where l2 is better than l1 according to val score
leak_candidates_val_score = lb_info_leak.loc[lb_info_leak.groupby('dataset')['score_val'].idxmax()]
leak_candidates_val_score = leak_candidates_val_score[leak_candidates_val_score['stack_level'] != 1]

# # select only those were the l1 is better than l2 according to test score
leak_candidates_test_score = lb_info_leak.loc[lb_info_leak.groupby('dataset')['metric_score'].idxmax()]
leak_candidates_test_score = leak_candidates_test_score[leak_candidates_test_score['stack_level'] == 1]

# get the intersection
leak_datasets = set(leak_candidates_test_score['dataset']).intersection(set(leak_candidates_val_score['dataset']))

EPS = np.finfo(np.float32).eps
leak_l1_error = leak_candidates_test_score[leak_candidates_test_score['dataset'].isin(leak_datasets)][['dataset','metric_error']].set_index('dataset')
leak_l2_error = leak_candidates_val_score[leak_candidates_val_score['dataset'].isin(leak_datasets)][['dataset','metric_error']].set_index('dataset')
leak_l1_error['metric_error'] += EPS
leak_l2_error['metric_error'] += EPS
equal_mask = (leak_l2_error - leak_l1_error) == 0

test_score_loss_by_leak = (leak_l2_error - leak_l1_error)/leak_l2_error
print(leak_datasets)
print(list(np.unique(res[res['dataset'].isin(leak_datasets)]['tid']).astype(int)))
print(list(np.unique(res[~res['dataset'].isin(leak_datasets)]['tid']).astype(int)))

exit()
print(f'Leak here: {len(test_score_loss_by_leak)}/{len(set(res.dataset))} ({len(test_score_loss_by_leak)/len(set(res.dataset))})')
print('The leak increases the error by:')
print(test_score_loss_by_leak.describe())

# test_score_loss_by_leak.index = [int(i) for i in test_score_loss_by_leak.index]
# missing_keys = set(np.unique(res['tid'])).difference(test_score_loss_by_leak.index)
# test_score_loss_by_leak = pd.concat([test_score_loss_by_leak, pd.DataFrame([-1 for _ in range(len(missing_keys))], index=list(missing_keys), columns=['metric_error'])])
# md = pd.DataFrame(md_map).T.dropna(axis=1)
# md = md.loc[test_score_loss_by_leak.index, :]
#
# from scipy.stats import pearsonr
#
# for m_f in md.columns:
#     vals = md[m_f].values
#     c_vals = test_score_loss_by_leak['metric_error'].values
#     print(m_f, pearsonr(vals, c_vals))

leak_datasets

lb_compare = lb_info_leak[lb_info_leak.dataset.isin(list(custom_md.keys()))]
lb_compare = lb_compare[['dataset', 'stack_level', 'metric_score', 'score_val']]
l1_b = lb_compare[lb_compare['stack_level'] == 1].drop(columns=['stack_level']).reset_index(drop=True).set_index('dataset')
l2_b = lb_compare[lb_compare['stack_level'] == 2].drop(columns=['stack_level']).reset_index(drop=True).set_index('dataset')

l2_overfitting = l2_b['score_val'] - l2_b['metric_score'] # positive value mean we did overfit (val > test), negative value underfit (val < test)
diff_to_l1_val = l1_b['score_val'] - l2_b['score_val']
diff_to_l1_test = l1_b['metric_score'] - l2_b['metric_score']
leak_mask = pd.Series(l1_b.index.isin(leak_datasets))
leak_mask.index = l1_b.index

leak_score = l1_b['metric_score'].copy()
leak_score.iloc[:] = -1
rel_index = [x for x in test_score_loss_by_leak.index if x in leak_score.index]
leak_score.loc[rel_index] = test_score_loss_by_leak.loc[rel_index, 'metric_error']

exp_md = pd.DataFrame({x: val[0]['tdc'] for x, val in custom_md.items()}).T
exp_md.columns = ['Val', 'LeakRepo', 'RandomLeakRepo', 'Test', 'AccVal', 'AccTest']
exp_md = exp_md.loc[l1_b.index, ['LeakRepo', 'RandomLeakRepo']]
res = exp_md['LeakRepo'] - exp_md['RandomLeakRepo']

res_df = pd.concat([res, l2_overfitting, diff_to_l1_val, diff_to_l1_test, leak_mask, leak_score], axis=1)
res_df.columns=['LeakRepoScore', 'l2_overfitting', 'l1-l2-val', 'l1-l2-test', 'Leak?', 'LeakScore']
# res_df['LeakRepoScore'] = abs(res_df['LeakRepoScore'])
print()
exit()
# ------- Leak analysis  End



def algo_vs_algo_view(metric_col_name="metric_score"):
    lb_stack = lb[lb["framework_parent"].isin(["AutoGluon_bq_stack_1h8c"])]
    lb_stack_fix = lb[lb["framework_parent"].isin(["AutoGluon_bq_stack_fix_v2_1h8c"])]
    models_performance_per_dataset = lb_stack.pivot(index=["dataset"], columns="model", values=metric_col_name)
    models_performance_per_dataset_fix = lb_stack_fix.pivot(index=["dataset"], columns="model", values=metric_col_name)
    models_performance_per_dataset_fix = models_performance_per_dataset_fix.drop(columns=["WeightedEnsemble_BAG_L2"])
    assert list(models_performance_per_dataset) == list(models_performance_per_dataset_fix)
    valid = set(models_performance_per_dataset.index).intersection(set(models_performance_per_dataset_fix.index))
    models_performance_per_dataset_fix = models_performance_per_dataset_fix.loc[valid, :]
    models_performance_per_dataset = models_performance_per_dataset.loc[valid, :]

    score_diff = models_performance_per_dataset - models_performance_per_dataset_fix
    score_diff = score_diff[[col for col in score_diff.columns if (col.endswith("L2") or col.endswith("L3"))]]
    avg_perf_non_normalized = score_diff.mean(axis=0, skipna=True)

    t = score_diff.copy()
    win_mask = score_diff < 0
    lose_mask = score_diff > 0
    tie_mask = score_diff == 0
    t[win_mask] = 1
    t[lose_mask] = 0
    t[tie_mask] = np.nan
    t[score_diff.isnull()] = np.nan
    win_rate = t.mean(axis=0, skipna=True)

    return win_rate, avg_perf_non_normalized, score_diff


algo_vs_algo_view()
algo_vs_algo_view(metric_col_name="score_val")

# Avg number of models per framework
print("Model Count:", res.groupby("framework")["models_count"].mean())
print("Ensemble Size:", res.groupby("framework")["models_ensemble_count"].mean())

md_df = pd.DataFrame(md_map).T[["NumberOfFeatures", "NumberOfInstances"]]
md_df["complexity"] = md_df["NumberOfInstances"]
md_df.index.name = "tid"
res = res.merge(md_df, how="inner", left_on="tid", right_on="tid", validate="m:1")

# new_m = pd.concat([res[(res['framework'] == "AutoGluon_bq_nostack_1h8c_2023_08_28") & (res["NumberOfInstances"]<=1000)].copy(), res[(res['framework'] == "AutoGluon_bq_stack_1h8c_2023_08_28") & (res["NumberOfInstances"]>1000)].copy()])
# new_m['framework'] = 'frankenstein_no_stack_stack'
# res = pd.concat([res, new_m])
#
# new_m = pd.concat([res[(res['framework'] == "AutoGluon_bq_stack_fix_v2_1h8c_2023_08_28") & (res["NumberOfInstances"]<=1000)].copy(), res[(res['framework'] == "AutoGluon_bq_stack_1h8c_2023_08_28") & (res["NumberOfInstances"]>1000)].copy()])
# new_m['framework'] = 'frankenstein_fix_stack'
# res = pd.concat([res, new_m])

def win_rate(in_df):
    rank_df = in_df.rank(axis=1, ascending=False)
    rank_df = rank_df[~(rank_df.min(axis=1) != 1)]
    return {m: np.mean(rank_df[m] == 1) for m in in_df.columns}

# Stitch together new models
for metric in ["metric_score"]: # auc , "balacc"
    for (complexity_name, complexity_l, complexity_u) in [
        ("all", 0, np.inf),
        # ("tier1", 0, 1000),
        # ("tier2", 1000 , 10000),
        # ("tier3", 10000 , 100000 ),
        # ("tier4", 100000 , np.inf),
    ]:

        tmp_res = res[(res["complexity"] > complexity_l) & (res["complexity"] <= complexity_u)]

        performance_per_dataset = tmp_res.pivot(index="dataset", columns="framework", values=metric)
        for f_work in performance_per_dataset.columns:
            print(f"Failures for {f_work}: {list(performance_per_dataset.index[performance_per_dataset[f_work].isnull()])}")

        # -- CD plots
        performance_per_dataset = performance_per_dataset.dropna()
        # performance_per_dataset = performance_per_dataset.round(3)
        # cd_evaluation(performance_per_dataset, True, None, ignore_non_significance=True,
        #               plt_title=f"{complexity_name} | {metric} | {len(performance_per_dataset)}")



        # only leak
        print('Leak:', win_rate(performance_per_dataset[performance_per_dataset.index.isin(leak_datasets)][["AutoGluon_bq_stack_1h8c_2023_08_28", "AutoGluon_bq_stack_fix_v2_1h8c_2023_08_28"]]))
        print(len(leak_datasets))

        # only no leak
        print('No Leak:', win_rate(performance_per_dataset[~performance_per_dataset.index.isin(leak_datasets)][["AutoGluon_bq_stack_1h8c_2023_08_28", "AutoGluon_bq_stack_fix_v2_1h8c_2023_08_28"]]))
        print(len(performance_per_dataset) - len(leak_datasets))


        # cd_evaluation(performance_per_dataset[performance_per_dataset.index.isin(leak_datasets)], True, None, ignore_non_significance=True,
        #               plt_title=f"{complexity_name} | {metric} | {len(leak_datasets)} | Only Leak")
        # # only no leak
        # cd_evaluation(performance_per_dataset[~performance_per_dataset.index.isin(leak_datasets)], True, None, ignore_non_significance=True,
        #               plt_title=f"{complexity_name} | {metric} | {len(performance_per_dataset) - len(leak_datasets)} | No Leak")