import pandas as pd, os, importlib.util, inspect

ws = "C:/abc/workBoddyHome刀具"
spec = importlib.util.spec_from_file_location("xgb", os.path.join(ws, "t1111_xgb_train.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

man = pd.read_csv(os.path.join(ws, "out_T1111", "t1111_xgb_cycle_manifest.csv"), encoding="utf-8-sig")
used = sorted(int(c) for c in man['life_cycle'].tolist())
cmax = used[-1]
missing = [c for c in range(1, cmax + 1) if c not in set(used)]
print("used =", used)
print("missing =", missing)
print("cmp =", missing == list(range(missing[0], missing[-1] + 1)))
print("---- function source (gap block) ----")
src = inspect.getsource(M.write_plain_report)
for i, line in enumerate(src.splitlines()):
    if "missing" in line or "shown" in line or "gap_note" in line or "连续区间" in line:
        print(i, line)
