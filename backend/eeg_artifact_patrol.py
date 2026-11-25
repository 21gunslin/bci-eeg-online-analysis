"""辅助巡检：在整份原始记录里找可疑通道和时间窗，供人工优先复核。扫描不是诊断，也不自动删除数据。

Read-only triage for manual artifact review, not automatic rejection.

Flat lines are checked first. Saturation and sustained steps are optional
review prompts. Line noise and ordinary voltage changes are not bad channels.
No filtering, interpolation, calibration correction,
Raw/FIF writes or diagnostic labels occur here.
"""

from collections import defaultdict
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from eeg_raw_bdf import NativeBDF

VERSION = "patrol_v3_span_marks"
NOTICE = "默认仅检查平线，工频不参与。绿色按钮仅把全程平线建议为整道坏道；局部平线及勾选的饱和/阶跃按实际区间标注坏段。全部样本保留，保存FIF后分析忽略BAD时段。候选仍需人工核对。"
CHECKS = ("flat", "saturation", "step")


def normalize_checks(value=None):
    """只允许明确的检查项目；旧请求未传此项时，也默认只检查平线。"""
    value = ["flat"] if value is None else value
    if (not isinstance(value, (list, tuple)) or not value
            or any(not isinstance(item, str) or item not in CHECKS for item in value)):
        raise ValueError("至少选择一项检查：flat、saturation、step。")
    return [name for name in CHECKS if name in value]


def sustained_step(x, delta, fs, begin, end, threshold, minimum_change, sensitivity, quantum):
    """单点尖峰、平滑起伏都不算阶跃；必须有突变，并保持前后电位差。

    取边界前后各100毫秒，避开紧邻边界的10毫秒。用中位数排除偶发尖峰，
    再要求前后电位差超过最小幅度及局部波动的稳健阈值。这只是复核线索。
    """
    context, guard = max(2, round(.1 * fs)), max(1, round(.01 * fs))
    left, right = max(0, begin - 1), max(0, end - 1)
    candidates = np.flatnonzero(delta[left:right] > threshold) + left + 1
    for point in candidates:
        if point < context or point + context > len(x):
            continue
        before, after = x[point-context:point-guard], x[point+guard:point+context]
        if not len(before) or not len(after):
            continue
        a, b = float(np.median(before)), float(np.median(after))
        background = 1.4826 * max(float(np.median(np.abs(before-a))),
                                  float(np.median(np.abs(after-b))))
        height = abs(b-a)
        if height >= max(minimum_change, sensitivity * max(background, quantum)):
            return float(point/fs), float(height)
    return None


def scan_record(path, mapping, *, seconds=2.0, sensitivity=6.0, scope="eeg",
                enabled_checks=None, step_min_uv=100.0):
    """全程平线才建议整道排除；局部问题仅标时间段，保留其他正常时段。"""
    enabled_checks = normalize_checks(enabled_checks)
    source = NativeBDF(path)
    if (
        not math.isfinite(seconds)
        or not 1 <= seconds <= 120
        or not math.isfinite(sensitivity)
        or not 3 <= sensitivity <= 12
        or scope not in ("eeg", "all")
        or not math.isfinite(step_min_uv) or not 1 <= step_min_uv <= 1_000_000
    ):
        raise ValueError("巡检分窗须1–120秒，阈值3–12，阶跃下限1–1000000µV，范围eeg/all。")
    from eeg_raw_display import ordered_channels

    order = ordered_channels([r["name"] for r in source.signals], mapping)
    selected = [r for r in order if scope == "all" or r["eeg"]]
    bins = math.ceil(source.duration / seconds)
    if not selected:
        raise ValueError("没有可识别的EEG候选通道，可切换“全部信号”扫描。")
    if bins > 2000 or bins * len(selected) > 100000:
        raise ValueError("巡检单元过多，请增大分窗；单份最多2000个时间窗、10万个通道×时间单元。")
    lookup = {r["name"]: r for r in source.signals}
    cells = []
    candidates = []
    channel_summary = []
    for display in selected:
        row = lookup[display["name"]]
        fs = row["fs"]
        digital = source.read(row, digital=True)
        x = source.read(row) * (1e6 if row["scale"] is not None else 1.0)
        quantum = (
            1.0
            if row["digital_only"]
            else abs((row["pmax"] - row["pmin"]) / (row["dmax"] - row["dmin"]))
            * (row["scale"] * 1e6 if row["scale"] is not None else 1.0)
        )
        quantum = max(quantum, 1e-12)
        delta = np.abs(np.diff(x))
        dmed = float(np.median(delta)) if len(delta) else 0.0
        dmad = float(np.median(np.abs(delta - dmed))) * 1.4826 if len(delta) else 0.0
        jump_threshold = dmed + 2 * sensitivity * max(dmad, quantum, dmed * 0.2)
        current = []
        for i in range(bins):
            start, end = i * seconds, min(source.duration, (i + 1) * seconds)
            a, b = round(start * fs), min(len(x), round(end * fs))
            values = x[a:b]
            if not len(values):
                current.append(
                    {
                        "channel": row["name"],
                        "bin": i,
                        "start": start,
                        "end": end,
                        "reasons": [],
                        "score": 0.0,
                        "evaluable": False,
                    }
                )
                continue
            center = float(np.median(values))
            ptp = float(np.ptp(values))
            sd = float(np.std(values))
            rail = float(np.mean((digital[a:b] <= row["dmin"]) | (digital[a:b] >= row["dmax"])))
            dstart = max(0, a - 1)
            differences = delta[dstart : max(dstart, b - 1)]
            max_step = float(differences.max()) if len(differences) else 0.0
            peak_time = (a + int(np.argmax(np.abs(values - center)))) / fs
            reasons = []
            score = 0.0
            line_db = None
            line_fraction = None
            if "flat" in enabled_checks and ptp <= quantum * 0.1:
                reasons.append("平线")
                score = max(score, 8.0)
            if "saturation" in enabled_checks and rail >= 0.01:
                reasons.append("数字边界/饱和候选")
                score = max(score, 9.0)
            # 只对电压信号应用µV阈值。50/60Hz在PSD中查看，不参与坏道扫描。
            step = None
            if "step" in enabled_checks and row["scale"] is not None and max_step > max(jump_threshold, step_min_uv):
                step = sustained_step(x, delta, fs, a, b,
                    max(jump_threshold, step_min_uv), step_min_uv, sensitivity, quantum)
                if step is not None:
                    reasons.append("持续电位阶跃候选")
                    score = max(score, 6.0)
                    peak_time = step[0]
            current.append(
                {
                    "channel": row["name"],
                    "bin": i,
                    "start": float(start),
                    "end": float(end),
                    "reasons": reasons,
                    "score": score,
                    "evaluable": True,
                    "ptp": ptp,
                    "std": sd,
                    "median": center,
                    "rail_fraction": rail,
                    "max_step": max_step,
                    "jump_threshold": jump_threshold,
                    "line_db": line_db,
                    "line_fraction": line_fraction,
                    "sustained_step_uv": step[1] if step is not None else None,
                    "focus_time": float(peak_time),
                }
            )
        valid = [c for c in current if c["evaluable"]]
        flags = defaultdict(list)
        for c in valid:
            for reason in c["reasons"]:
                flags[reason].append(c)
        # Persistent problems become one channel-level row instead of hundreds of alarms.
        persistent = []
        for reason, matches in flags.items():
            coverage = len(matches) / len(valid)
            # 80%只是列表合并阈值，不能据此牺牲其余20%的有效脑电。
            whole_channel = (reason == "平线" and len(matches) == len(current)
                             and all(c["evaluable"] for c in current))
            groups = [matches] if coverage >= 0.8 else []
            if coverage >= 0.8:
                persistent.append(reason)
            else:
                for cell in matches:
                    if groups and cell["bin"] == groups[-1][-1]["bin"] + 1:
                        groups[-1].append(cell)
                    else:
                        groups.append([cell])
            for group in groups:
                worst = max(group, key=lambda c: c["score"])
                segments = []
                for cell in group:
                    if segments and abs(segments[-1]["end"] - cell["start"]) < 1e-9:
                        segments[-1]["end"] = cell["end"]
                    else:
                        segments.append({"start": cell["start"], "end": cell["end"]})
                candidates.append(
                    {
                        "channel": row["name"],
                        "reason": reason,
                        "start": group[0]["start"],
                        "end": group[-1]["end"],
                        "focus_time": worst["focus_time"],
                        "score": max(c["score"] for c in group),
                        "persistent": coverage >= 0.8,
                        "fraction": coverage,
                        "windows": len(group),
                        "eeg": display["eeg"],
                        "region": display["region"],
                        "draft_eligible": True,
                        "whole_channel": whole_channel,
                        "mark_kind": "channel" if whole_channel else "segment",
                        "segments": segments,
                    }
                )
        channel_summary.append(
            {
                **display,
                "candidate_windows": sum(bool(c["reasons"]) for c in valid),
                "windows": len(valid),
                "persistent_reasons": persistent,
            }
        )
        cells.append(current)
    candidates.sort(key=lambda r: (r["persistent"], -r["score"], r["start"], r["channel"]))
    for index, candidate in enumerate(candidates):
        candidate["id"] = f"{VERSION}_{index}"
    return {
        "version": VERSION,
        "notice": NOTICE,
        "duration": source.duration,
        "seconds": seconds,
        "scope": scope,
        "sensitivity": sensitivity,
        "enabled_checks": enabled_checks,
        "step_min_uv": step_min_uv,
        "channels": channel_summary,
        "bins": bins,
        "cells": cells,
        "candidates": candidates,
        "rules": [
            "平线：峰峰值≤0.1个文件头标定量化步长。",
            "饱和候选：数字上下边界占比≥1%。",
            f"本次启用：{', '.join(enabled_checks)}；默认只有flat（平线）。",
            f"可选持续电位阶跃：单次采样变化须超过{step_min_uv:g}µV及通道稳健阈值，同时前后各100ms（避开边界10ms）的中位电位差也须超过幅度下限及局部稳健阈值；只提示复核。",
            "工频及其谐波不进入候选或BAD草稿，保留原始PSD供查看；普通幅度起伏、单点尖峰和基线变化不按旧跳变规则标红。",
            "绿色按钮：仅全程每一窗均平线才加入整道坏道；局部平线及勾选的饱和/阶跃只写BAD时段，波形和通道保留。",
            "同一原因触发≥80%窗口只合并列表展示，不升级为整道坏道；按实际连续区间标坏段，间隙中的正常片段保留。",
        ],
    }


def export_scan(scan, directory):
    path = Path(directory) / "人工巡检_候选定位.csv"
    pd.DataFrame(
        [
            {
                "通道": c["channel"],
                "原因": c["reason"],
                "起点_s": c["start"],
                "终点_s": c["end"],
                "建议复核点_s": c["focus_time"],
                "持续性提示": c["persistent"],
                "触发窗口比例": c["fraction"],
                "标记方式": "整道排除候选" if c["whole_channel"] else "仅标注坏段，保留通道",
                "实际问题区间_s": json.dumps(c["segments"], ensure_ascii=False),
                "说明": "候选，未自动设置坏道或删段",
            }
            for c in scan["candidates"]
        ]
        or [{"说明": "未触发候选规则，不等于无伪迹"}]
    ).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def review_state(directory, source, options):
    """单独登记人工实际浏览的通道和时间；程序扫描或自动翻页不能冒充人工复核。"""
    path = Path(directory) / "manual_review_state.json"
    state = json.loads(path.read_text(encoding="utf8")) if path.is_file() else {"entries": []}
    action = options.get("action", "get")
    if action == "append":
        names = options.get("channels")
        allowed = {r["name"] for r in source.signals}
        a, b = float(options["start"]), float(options["end"])
        status = options.get("status", "reviewed")
        if (
            not isinstance(names, list)
            or not names
            or len(set(names)) != len(names)
            or any(n not in allowed for n in names)
            or status not in ("reviewed", "uncertain")
            or not all(math.isfinite(t) for t in (a, b))
            or not 0 <= a < b <= source.duration
        ):
            raise ValueError("人工复核范围、通道或状态无效。")
        if len(state["entries"]) >= 10000:
            raise ValueError("复核记录超过10000条，请导出后分开管理。")
        state["entries"].append({"channels": names, "start": a, "end": b, "status": status})
    elif action == "undo":
        if state["entries"]:
            state["entries"].pop()
    elif action != "get":
        raise ValueError("未知复核操作。")
    if action != "get":
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf8")
        temporary.replace(path)
    return state
