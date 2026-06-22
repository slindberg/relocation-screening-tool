"""
Self-validation: the spec's named anchor towns plus structural checks.

Each anchor axis must land in the top quartile ("high") or bottom quartile ("low")
of that score column's empirical distribution. Checks flagged with a proxy caveat
(e.g. Olympia synoptic, which needs ERA5 to see fronts) are reported but never
counted as hard failures in Phase 1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C


def _band(series: pd.Series):
    return series.quantile(0.25), series.quantile(0.75)


SMALL_N = 200  # below this, percentile ranks and spread are not meaningful


def run_smoke_tests(matrix: pd.DataFrame) -> dict:
    score_cols = [c for c in matrix.columns if c.startswith("score_")]
    small = len(matrix) < SMALL_N
    report = {"anchors": [], "null_check": {}, "spread_check": {},
              "hard_failures": [], "proxy_notes": [], "accepted_notes": [],
              "small_notes": [], "small": small, "n": int(len(matrix))}

    bands = {c: _band(matrix[c]) for c in score_cols}

    for anc in C.ANCHORS:
        row = matrix[(matrix["name"].str.lower() == anc["name"].lower())
                     & (matrix["state"] == anc["state"])]
        if row.empty:
            report["anchors"].append(
                {"town": f"{anc['name']}, {anc['state']}", "found": False})
            report["hard_failures"].append(f"{anc['name']}, {anc['state']}: not in matrix")
            continue
        r = row.iloc[0]
        checks = []
        for want, col_list in (("high", anc["high"]), ("low", anc["low"])):
            for col, flag in col_list:
                q25, q75 = bands[col]
                val = r[col]
                ok = val >= q75 if want == "high" else val <= q25
                checks.append({"axis": col, "want": want, "value": round(float(val), 1),
                               "q25": round(float(q25), 1), "q75": round(float(q75), 1),
                               "pass": bool(ok), "flag": flag})
                if not ok:
                    msg = f"{anc['name']}, {anc['state']} {col} want {want} got {val:.1f}"
                    if flag == "proxy" or flag is True:
                        report["proxy_notes"].append(msg + " (proxy-limited; expected)")
                    elif flag == "accepted":
                        report["accepted_notes"].append(msg + " (accepted divergence; see README)")
                    elif small:
                        report["small_notes"].append(msg + f" (N={len(matrix)}; not meaningful)")
                    else:
                        report["hard_failures"].append(msg)
        report["anchors"].append(
            {"town": f"{anc['name']}, {anc['state']}", "found": True, "checks": checks})

    # No nulls in any score_ column
    for c in score_cols:
        n = int(matrix[c].isna().sum())
        report["null_check"][c] = n
        if n:
            report["hard_failures"].append(f"{c}: {n} null score(s)")

    # Distribution spread (not all clustered): std and IQR must be non-trivial
    for c in score_cols:
        std = float(matrix[c].std())
        iqr = float(matrix[c].quantile(0.75) - matrix[c].quantile(0.25))
        report["spread_check"][c] = {"std": round(std, 1), "iqr": round(iqr, 1)}
        if std < 5:
            if small:
                report["small_notes"].append(f"{c}: low spread (std={std:.1f}) — N={len(matrix)}")
            else:
                report["hard_failures"].append(f"{c}: degenerate spread (std={std:.1f})")

    report["passed"] = len(report["hard_failures"]) == 0
    return report


def print_report(report: dict) -> None:
    print("\n================ SMOKE TEST ================")
    if report.get("small"):
        print(f"  NOTE: N={report['n']} (anchors-only). Percentile ranks and spread "
              f"are\n  not meaningful at this size — these checks are informational "
              f"here.\n  Run the full pipeline to validate rankings.")
    for a in report["anchors"]:
        if not a.get("found"):
            print(f"  [MISSING] {a['town']}")
            continue
        print(f"\n  {a['town']}")
        for chk in a["checks"]:
            flag = chk["flag"]
            mark = "PASS" if chk["pass"] else ("note" if flag else "FAIL")
            tag = ""
            if not chk["pass"] and flag:
                tag = "  <accepted>" if flag == "accepted" else "  <proxy>"
            print(f"    [{mark}] {chk['axis']:<28} want {chk['want']:<4} "
                  f"val={chk['value']:<6} (q25={chk['q25']}, q75={chk['q75']})" + tag)
    print("\n  Null scores:",
          "none" if not any(report["null_check"].values()) else report["null_check"])
    print("  Spread (std):", {k: v["std"] for k, v in report["spread_check"].items()})
    if report["proxy_notes"]:
        print("\n  Proxy-limited (expected in Phase 1):")
        for m in report["proxy_notes"]:
            print("    -", m)
    if report.get("accepted_notes"):
        print("\n  Accepted divergences (documented; not failures):")
        for m in report["accepted_notes"]:
            print("    -", m)
    if report.get("small_notes"):
        print("\n  Small-N (informational; re-check on full run):")
        for m in report["small_notes"]:
            print("    -", m)
    print("\n  RESULT:", "PASS" if report["passed"] else "HARD FAILURES:")
    for f in report["hard_failures"]:
        print("    x", f)
    print("===========================================\n")
