import pandas as pd, os, importlib.util

ws = "C:/abc/workBoddyHome刀具"
spec = importlib.util.spec_from_file_location("xgb", os.path.join(ws, "t1111_xgb_train.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

man = pd.read_csv(os.path.join(ws, "out_T1111", "t1111_xgb_cycle_manifest.csv"), encoding="utf-8-sig")
metrics = {
    "target": "life_used_pct_cut", "split": "by life_cycle (grouped)", "test_frac": 0.2,
    "n_features": 378, "n_train_rows": 112502, "n_test_rows": 20071,
    "n_train_cycles": 6, "n_test_cycles": 2,
    "train": {"R2": 0.7430723111209037, "RMSE": 16.933668135002037, "MAE": 10.40803369893682},
    "test":  {"R2": -0.22665344998891324, "RMSE": 38.514122104163896, "MAE": 34.28201059059934},
}
p = M.write_plain_report(metrics, [17,18,19,20,22,23], [24,25], man, "life_used_pct_cut", os.path.join(ws, "out_T1111"))
print("SAVED:", p)
