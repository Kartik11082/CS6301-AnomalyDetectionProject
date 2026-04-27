"""Side-by-side analysis of the real and synthetic IAM workbooks.

Usage:
    python -m src.compare_datasets
    python -m src.compare_datasets --real data/dataset.xlsx --syn data/syntheticdataset/syntheticDataset.xlsx

Outputs:
    - Console summary table
    - outputs/logs/dataset_sidebyside.md  (markdown report)
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import textwrap
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _load(path: str, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")


def _safe_literal(text: str) -> Any:
    try:
        return ast.literal_eval(str(text))
    except Exception:
        return None


def _count_tokens(value: Any) -> int:
    """Return number of items in a JSON-list-like cell."""
    obj = _safe_literal(value) if isinstance(value, str) else value
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, str) and obj.strip():
        return len([p for p in obj.split(",") if p.strip()])
    return 0


def _flatten_statements(policy_text: str) -> list[dict]:
    """Extract statement dicts from a PolicyObject cell, tolerating truncation."""
    try:
        obj = ast.literal_eval(str(policy_text))
    except Exception:
        return []
    if isinstance(obj, dict):
        stmts = obj.get("Statement", obj)
        obj = stmts if isinstance(stmts, list) else [stmts]
    if isinstance(obj, list):
        return [s for s in obj if isinstance(s, dict)]
    return []


def _is_wildcard_action(stmt: dict) -> bool:
    action = stmt.get("Action", stmt.get("NotAction", ""))
    if isinstance(action, str):
        return action.strip() == "*" or action.strip().endswith(":*")
    if isinstance(action, list):
        return any(a.strip() == "*" or a.strip().endswith(":*") for a in action)
    return False


def _is_wildcard_resource(stmt: dict) -> bool:
    res = stmt.get("Resource", stmt.get("NotResource", ""))
    if isinstance(res, str):
        return res.strip() == "*"
    if isinstance(res, list):
        return any(r.strip() == "*" for r in res)
    return False


def _fmt(v: float | int | None, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if pct:
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _pct(n: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{n / total * 100:.1f}%"


# ---------------------------------------------------------------------------
# Per-sheet analysis
# ---------------------------------------------------------------------------


def analyse_policies(real_path: str, syn_path: str, label_names: list[str]) -> dict:
    r = _load(real_path, "policies")
    s = _load(syn_path, "policies")
    label_set = set(label_names)

    def policy_stats(df: pd.DataFrame, tag: str) -> dict:
        n = len(df)
        n_anomaly = df["PolicyName"].isin(label_set).sum()
        ac = df["AttachmentCount"]
        path_dist = df["Path"].value_counts(normalize=True).head(4).to_dict()
        vid_dist = df["DefaultVersionId"].value_counts(normalize=True).head(4).to_dict()

        all_stmts: list[dict] = []
        n_stmts_per_policy: list[int] = []
        wc_action_policies = 0
        wc_resource_policies = 0

        for val in df["PolicyObject"]:
            stmts = _flatten_statements(val)
            n_stmts_per_policy.append(len(stmts))
            all_stmts.extend(stmts)
            if any(_is_wildcard_action(st) for st in stmts):
                wc_action_policies += 1
            if any(_is_wildcard_resource(st) for st in stmts):
                wc_resource_policies += 1

        effects = [st.get("Effect", "") for st in all_stmts]
        allow_ratio = effects.count("Allow") / len(effects) if effects else 0.0
        deny_ratio = effects.count("Deny") / len(effects) if effects else 0.0

        s_ser = pd.Series(n_stmts_per_policy)
        return {
            "tag": tag,
            "n": n,
            "n_anomaly": int(n_anomaly),
            "anomaly_pct": _pct(int(n_anomaly), n),
            "attachment_mean": round(float(ac.mean()), 3),
            "attachment_median": float(ac.median()),
            "attachment_max": int(ac.max()),
            "path_dist": {k: f"{v*100:.1f}%" for k, v in path_dist.items()},
            "version_dist": {k: f"{v*100:.1f}%" for k, v in vid_dist.items()},
            "stmts_mean": round(float(s_ser.mean()), 3),
            "stmts_median": float(s_ser.median()),
            "stmts_max": int(s_ser.max()),
            "wc_action_policies": int(wc_action_policies),
            "wc_action_pct": _pct(int(wc_action_policies), n),
            "wc_resource_policies": int(wc_resource_policies),
            "wc_resource_pct": _pct(int(wc_resource_policies), n),
            "allow_ratio": round(allow_ratio, 3),
            "deny_ratio": round(deny_ratio, 3),
            "total_statements": len(all_stmts),
        }

    return {
        "real": policy_stats(r, "real"),
        "syn": policy_stats(s, "syn"),
    }


def analyse_attached(real_path: str, syn_path: str, sheet: str, col: str) -> dict:
    r = _load(real_path, sheet)
    s = _load(syn_path, sheet)

    def stats(df: pd.DataFrame) -> dict:
        counts = df[col].apply(_count_tokens)
        return {
            "n": len(df),
            "attached_mean": round(float(counts.mean()), 3),
            "attached_median": float(counts.median()),
            "attached_max": int(counts.max()),
            "attached_min": int(counts.min()),
            "zero_pct": _pct(int((counts == 0).sum()), len(df)),
        }

    return {"real": stats(r), "syn": stats(s)}


def analyse_groups(real_path: str, syn_path: str) -> dict:
    r = _load(real_path, "groups")
    s = _load(syn_path, "groups")

    def stats(df: pd.DataFrame) -> dict:
        pol_counts = df["AttachedPolicies"].apply(_count_tokens)
        usr_counts = df["Users"].apply(_count_tokens)
        return {
            "n": len(df),
            "policies_mean": round(float(pol_counts.mean()), 3),
            "policies_max": int(pol_counts.max()),
            "users_mean": round(float(usr_counts.mean()), 3),
            "users_max": int(usr_counts.max()),
            "empty_policy_pct": _pct(int((pol_counts == 0).sum()), len(df)),
            "empty_users_pct": _pct(int((usr_counts == 0).sum()), len(df)),
        }

    return {"real": stats(r), "syn": stats(s)}


# ---------------------------------------------------------------------------
# Fidelity score (0–100, higher = more realistic synthetic)
# ---------------------------------------------------------------------------


def _pct_to_f(s: str) -> float:
    return float(s.rstrip("%")) / 100


def fidelity_score(p: dict, u: dict, g: dict, r_roles: dict) -> tuple[float, list[str]]:
    notes: list[str] = []
    score = 100.0

    # 1. Anomaly ratio — real has ~2.5% labeled, synthetic has ~4.2%
    real_ar = _pct_to_f(p["real"]["anomaly_pct"])
    syn_ar = _pct_to_f(p["syn"]["anomaly_pct"])
    ar_diff = abs(syn_ar - real_ar)
    if ar_diff > 0.05:
        deduct = min(20, ar_diff * 200)
        score -= deduct
        notes.append(
            f"Anomaly ratio gap {ar_diff*100:.1f}pp (real={p['real']['anomaly_pct']}, syn={p['syn']['anomaly_pct']}) -{deduct:.0f}pts"
        )

    # 2. AttachmentCount — real median 0, syn median ~13
    ac_real = p["real"]["attachment_median"]
    ac_syn = p["syn"]["attachment_median"]
    if abs(ac_real - ac_syn) > 3:
        deduct = min(20, abs(ac_real - ac_syn) * 1.5)
        score -= deduct
        notes.append(
            f"AttachmentCount median gap {abs(ac_real - ac_syn):.0f} (real={ac_real}, syn={ac_syn}) -{deduct:.0f}pts"
        )

    # 3. Wildcard action policies — real ~0%, synthetic has labeled ones
    wc_real = _pct_to_f(p["real"]["wc_action_pct"])
    wc_syn = _pct_to_f(p["syn"]["wc_action_pct"])
    wc_diff = abs(wc_syn - wc_real)
    if wc_diff > 0.10:
        deduct = min(15, wc_diff * 100)
        score -= deduct
        notes.append(f"Wildcard-action ratio gap {wc_diff*100:.1f}pp -{deduct:.0f}pts")

    # 4. Statements per policy distribution
    sm_real = p["real"]["stmts_mean"]
    sm_syn = p["syn"]["stmts_mean"]
    if abs(sm_real - sm_syn) > 1.5:
        deduct = min(15, abs(sm_real - sm_syn) * 3)
        score -= deduct
        notes.append(
            f"Statements/policy mean gap {abs(sm_real - sm_syn):.2f} (real={sm_real}, syn={sm_syn}) -{deduct:.0f}pts"
        )

    # 5. User attached-policy distribution
    u_real = u["real"]["attached_mean"]
    u_syn = u["syn"]["attached_mean"]
    if abs(u_real - u_syn) > 2:
        deduct = min(10, abs(u_real - u_syn) * 2)
        score -= deduct
        notes.append(
            f"User AttachedPolicies mean gap {abs(u_real - u_syn):.2f} -{deduct:.0f}pts"
        )

    # 6. Allow/Deny ratio
    allow_real = p["real"]["allow_ratio"]
    allow_syn = p["syn"]["allow_ratio"]
    if abs(allow_real - allow_syn) > 0.05:
        deduct = min(10, abs(allow_real - allow_syn) * 100)
        score -= deduct
        notes.append(
            f"Allow-ratio gap {abs(allow_real - allow_syn):.3f} (real={allow_real}, syn={allow_syn}) -{deduct:.0f}pts"
        )

    return round(max(0.0, score), 1), notes


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


def _row(label: str, real_val: str, syn_val: str, flag: str = "") -> str:
    return f"| {label:<40} | {real_val:>20} | {syn_val:>20} | {flag} |"


def _header(title: str) -> str:
    return (
        f"\n## {title}\n\n"
        + _row("Metric", "Real", "Synthetic", "Note")
        + "\n"
        + _row("-" * 40, "-" * 20, "-" * 20, "")
    )


def _flag(real_v: float, syn_v: float, threshold: float, fmt: str = ".2f") -> str:
    if abs(real_v - syn_v) > threshold:
        return f"(!) gap={abs(real_v - syn_v):{fmt}}"
    return "ok"


def build_markdown(
    p: dict, u: dict, g: dict, r_roles: dict, score: float, notes: list[str]
) -> str:
    lines = [
        "# Dataset Side-by-Side Analysis",
        "",
        f"**Fidelity score: {score}/100**  _(higher = synthetic closer to real)_",
        "",
    ]

    # Policies
    rp, sp = p["real"], p["syn"]
    lines.append(_header("Policies"))
    lines.append(_row("Row count", str(rp["n"]), str(sp["n"])))
    lines.append(
        _row(
            "Labeled anomalies (n / %)",
            f"{rp['n_anomaly']} / {rp['anomaly_pct']}",
            f"{sp['n_anomaly']} / {sp['anomaly_pct']}",
            _flag(rp["n_anomaly"] / rp["n"], sp["n_anomaly"] / sp["n"], 0.05),
        )
    )
    lines.append(
        _row(
            "AttachmentCount mean",
            _fmt(rp["attachment_mean"]),
            _fmt(sp["attachment_mean"]),
            _flag(rp["attachment_mean"], sp["attachment_mean"], 2),
        )
    )
    lines.append(
        _row(
            "AttachmentCount median",
            _fmt(rp["attachment_median"]),
            _fmt(sp["attachment_median"]),
            _flag(rp["attachment_median"], sp["attachment_median"], 2),
        )
    )
    lines.append(
        _row(
            "AttachmentCount max", str(rp["attachment_max"]), str(sp["attachment_max"])
        )
    )
    lines.append(
        _row(
            "Statements / policy (mean)",
            _fmt(rp["stmts_mean"]),
            _fmt(sp["stmts_mean"]),
            _flag(rp["stmts_mean"], sp["stmts_mean"], 1.5),
        )
    )
    lines.append(
        _row(
            "Statements / policy (median)",
            _fmt(rp["stmts_median"]),
            _fmt(sp["stmts_median"]),
        )
    )
    lines.append(
        _row("Statements / policy (max)", str(rp["stmts_max"]), str(sp["stmts_max"]))
    )
    lines.append(
        _row(
            "Wildcard-action policies",
            f"{rp['wc_action_policies']} ({rp['wc_action_pct']})",
            f"{sp['wc_action_policies']} ({sp['wc_action_pct']})",
            _flag(_pct_to_f(rp["wc_action_pct"]), _pct_to_f(sp["wc_action_pct"]), 0.05),
        )
    )
    lines.append(
        _row(
            "Wildcard-resource policies",
            f"{rp['wc_resource_policies']} ({rp['wc_resource_pct']})",
            f"{sp['wc_resource_policies']} ({sp['wc_resource_pct']})",
        )
    )
    lines.append(
        _row(
            "Allow ratio (of all stmts)",
            _fmt(rp["allow_ratio"]),
            _fmt(sp["allow_ratio"]),
            _flag(rp["allow_ratio"], sp["allow_ratio"], 0.05),
        )
    )
    lines.append(
        _row(
            "Deny ratio (of all stmts)", _fmt(rp["deny_ratio"]), _fmt(sp["deny_ratio"])
        )
    )
    lines.append("")

    # Path distribution
    lines.append("### Path Distribution (policies)\n")
    all_paths = sorted(set(rp["path_dist"]) | set(sp["path_dist"]))
    lines.append("| Path | Real | Synthetic |")
    lines.append("| --- | --- | --- |")
    for path in all_paths:
        lines.append(
            f"| `{path}` | {rp['path_dist'].get(path, '—')} | {sp['path_dist'].get(path, '—')} |"
        )
    lines.append("")

    # Version distribution
    lines.append("### DefaultVersionId Distribution (policies)\n")
    all_vids = sorted(set(rp["version_dist"]) | set(sp["version_dist"]))
    lines.append("| VersionId | Real | Synthetic |")
    lines.append("| --- | --- | --- |")
    for vid in all_vids:
        lines.append(
            f"| `{vid}` | {rp['version_dist'].get(vid, '—')} | {sp['version_dist'].get(vid, '—')} |"
        )
    lines.append("")

    # Users
    ru, su = u["real"], u["syn"]
    lines.append(_header("Users"))
    lines.append(_row("Row count", str(ru["n"]), str(su["n"])))
    lines.append(
        _row(
            "AttachedPolicies mean",
            _fmt(ru["attached_mean"]),
            _fmt(su["attached_mean"]),
            _flag(ru["attached_mean"], su["attached_mean"], 1),
        )
    )
    lines.append(
        _row(
            "AttachedPolicies median",
            _fmt(ru["attached_median"]),
            _fmt(su["attached_median"]),
        )
    )
    lines.append(
        _row("AttachedPolicies max", str(ru["attached_max"]), str(su["attached_max"]))
    )
    lines.append(_row("Users with 0 policies (%)", ru["zero_pct"], su["zero_pct"]))
    lines.append("")

    # Groups
    rg, sg = g["real"], g["syn"]
    lines.append(_header("Groups"))
    lines.append(_row("Row count", str(rg["n"]), str(sg["n"])))
    lines.append(
        _row(
            "AttachedPolicies mean",
            _fmt(rg["policies_mean"]),
            _fmt(sg["policies_mean"]),
        )
    )
    lines.append(
        _row("AttachedPolicies max", str(rg["policies_max"]), str(sg["policies_max"]))
    )
    lines.append(
        _row("Users per group mean", _fmt(rg["users_mean"]), _fmt(sg["users_mean"]))
    )
    lines.append(
        _row("Users per group max", str(rg["users_max"]), str(sg["users_max"]))
    )
    lines.append(
        _row(
            "Groups with 0 policies (%)", rg["empty_policy_pct"], sg["empty_policy_pct"]
        )
    )
    lines.append(
        _row("Groups with 0 users (%)", rg["empty_users_pct"], sg["empty_users_pct"])
    )
    lines.append("")

    # Roles
    rr, sr = r_roles["real"], r_roles["syn"]
    lines.append(_header("Roles"))
    lines.append(_row("Row count", str(rr["n"]), str(sr["n"])))
    lines.append(
        _row(
            "AttachedPolicies mean",
            _fmt(rr["attached_mean"]),
            _fmt(sr["attached_mean"]),
        )
    )
    lines.append(
        _row(
            "AttachedPolicies median",
            _fmt(rr["attached_median"]),
            _fmt(sr["attached_median"]),
        )
    )
    lines.append(
        _row("AttachedPolicies max", str(rr["attached_max"]), str(sr["attached_max"]))
    )
    lines.append(_row("Roles with 0 policies (%)", rr["zero_pct"], sr["zero_pct"]))
    lines.append("")

    # Fidelity breakdown
    lines.append("## Fidelity Breakdown\n")
    if notes:
        for note in notes:
            lines.append(f"- {note}")
    else:
        lines.append("- No major gaps detected.")
    lines.append("")

    lines.append("## Recommendations\n")
    lines.append(_recommendations(p, u, g, r_roles, notes))

    return "\n".join(lines) + "\n"


def _recommendations(p, u, g, r_roles, notes) -> str:
    recs = []
    rp, sp = p["real"], p["syn"]

    ac_gap = abs(rp["attachment_median"] - sp["attachment_median"])
    if ac_gap > 3:
        recs.append(
            f"**AttachmentCount is too high** in synthetic (median {sp['attachment_median']} vs real {rp['attachment_median']}). "
            "Most real AWS policies have 0 attachments. Set `AttachmentCount` to 0 for ~95% of synthetic policies, "
            "using small counts only for deliberately attached ones."
        )

    sm_gap = abs(rp["stmts_mean"] - sp["stmts_mean"])
    if sm_gap > 1:
        recs.append(
            f"**Statement count per policy** differs (real mean={rp['stmts_mean']}, syn={sp['stmts_mean']}). "
            "Real policies are mostly single-statement; weight your generator toward 1 statement with a long tail."
        )

    if _pct_to_f(sp["wc_action_pct"]) > _pct_to_f(rp["wc_action_pct"]) + 0.02:
        recs.append(
            "**Wildcard actions are overrepresented** in synthetic data. "
            "In the real dataset no normal policy uses `*` actions — keep wildcards exclusively in labeled anomaly rows."
        )

    if abs(rp["allow_ratio"] - sp["allow_ratio"]) > 0.05:
        recs.append(
            f"**Allow/Deny ratio differs** (real={rp['allow_ratio']}, syn={sp['allow_ratio']}). "
            "Consider adding `Deny` statements in some synthetic policies for realism."
        )

    if not recs:
        recs.append(
            "Synthetic dataset is a reasonable structural match. Focus next on embedding-space overlap verification."
        )
    return "\n".join(f"{i+1}. {r}" for i, r in enumerate(recs))


# ---------------------------------------------------------------------------
# Console print
# ---------------------------------------------------------------------------


def print_summary(p, u, g, r_roles, score, notes):
    print("\n" + "=" * 65)
    print(f"  DATASET SIDE-BY-SIDE SUMMARY   fidelity={score}/100")
    print("=" * 65)

    def row(label, r_val, s_val, warn=""):
        w = " (!)" if warn else ""
        print(f"  {label:<38} {str(r_val):>12}  {str(s_val):>12}{w}")

    print(f"\n{'  POLICIES':}")
    print(f"  {'Metric':<38} {'Real':>12}  {'Synthetic':>12}")
    print("  " + "-" * 64)
    rp, sp = p["real"], p["syn"]
    row("Row count", rp["n"], sp["n"])
    row(
        "Anomalies (n)",
        f"{rp['n_anomaly']} ({rp['anomaly_pct']})",
        f"{sp['n_anomaly']} ({sp['anomaly_pct']})",
        abs(rp["n_anomaly"] / rp["n"] - sp["n_anomaly"] / sp["n"]) > 0.05,
    )
    row(
        "AttachmentCount median",
        rp["attachment_median"],
        sp["attachment_median"],
        abs(rp["attachment_median"] - sp["attachment_median"]) > 3,
    )
    row(
        "Statements/policy mean",
        rp["stmts_mean"],
        sp["stmts_mean"],
        abs(rp["stmts_mean"] - sp["stmts_mean"]) > 1.5,
    )
    row(
        "Wildcard-action (%)",
        rp["wc_action_pct"],
        sp["wc_action_pct"],
        _pct_to_f(sp["wc_action_pct"]) > _pct_to_f(rp["wc_action_pct"]) + 0.05,
    )
    row(
        "Allow ratio",
        rp["allow_ratio"],
        sp["allow_ratio"],
        abs(rp["allow_ratio"] - sp["allow_ratio"]) > 0.05,
    )

    print(f"\n{'  USERS':}")
    ru, su = u["real"], u["syn"]
    row("Row count", ru["n"], su["n"])
    row(
        "AttachedPolicies mean",
        ru["attached_mean"],
        su["attached_mean"],
        abs(ru["attached_mean"] - su["attached_mean"]) > 2,
    )

    print(f"\n{'  GROUPS':}")
    rg, sg = g["real"], g["syn"]
    row("Row count", rg["n"], sg["n"])
    row("AttachedPolicies mean", rg["policies_mean"], sg["policies_mean"])
    row("Users/group mean", rg["users_mean"], sg["users_mean"])

    print(f"\n{'  ROLES':}")
    rr, sr = r_roles["real"], r_roles["syn"]
    row("Row count", rr["n"], sr["n"])
    row("AttachedPolicies mean", rr["attached_mean"], sr["attached_mean"])

    if notes:
        print("\n  FIDELITY GAPS:")
        for n in notes:
            print(f"    * {textwrap.fill(n, width=60, subsequent_indent='      ')}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Side-by-side IAM dataset analysis")
    p.add_argument(
        "--real", default="../data/merge_dataset.xlsx", help="Path to real workbook"
    )
    p.add_argument(
        "--syn",
        default="../data/syntheticdataset/syntheticDataset.xlsx",
        help="Path to synthetic workbook",
    )
    p.add_argument(
        "--data-config", default="../config/data.yaml", help="Pipeline data config"
    )
    p.add_argument(
        "--out",
        default="../outputs/logs/dataset_sidebyside.md",
        help="Markdown output path",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    with open(args.data_config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    label_names = list(cfg.get("misconfigured_policies_by_name", []))

    print("Analysing policies...")
    p = analyse_policies(args.real, args.syn, label_names)
    print("Analysing users...")
    u = analyse_attached(args.real, args.syn, "users", "AttachedPolicies")
    print("Analysing groups...")
    g = analyse_groups(args.real, args.syn)
    print("Analysing roles...")
    r_roles = analyse_attached(args.real, args.syn, "roles", "AttachedPolicies")

    score, notes = fidelity_score(p, u, g, r_roles)
    print_summary(p, u, g, r_roles, score, notes)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    md = build_markdown(p, u, g, r_roles, score, notes)
    out_path.write_text(md, encoding="utf-8")
    print(f"Markdown report -> {out_path}")


if __name__ == "__main__":
    main()
