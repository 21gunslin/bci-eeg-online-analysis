"""导出原始波形、滤波对照和人工决定的离线质控报告。"""

import base64
from html import escape
import json
import math

import numpy as np

from eeg_artifact_patrol import VERSION, review_state
from eeg_mne_ops import bandpass_zero_phase, notch_zero_phase, welch_psd
from eeg_raw_qc import assemble_mapping, validate_intervals


def table(headers, rows):
    def row(tag, cells):
        return "<tr>" + "".join(f"<{tag}>{escape(str(v))}</{tag}>" for v in cells) + "</tr>"

    return (
        "<table><thead>" + row("th", headers) + "</thead><tbody>"
        + "".join(row("td", cells) for cells in rows) + "</tbody></table>"
    )


def number(value):
    return "—" if value is None else f"{value:.6g}"


def excluded_seconds(intervals):
    end = total = 0.0
    for item in sorted(intervals, key=lambda item: item["start"]):
        total += max(0.0, item["end"] - max(end, item["start"]))
        end = max(end, item["end"])
    return total


def plot_case(source, channel, start, end, path):
    from eeg_pair_spike import plotting

    fs = channel["fs"]
    a, b = round(start * fs), round(end * fs)
    left = max(0, a - round(5 * fs))
    right = min(source.records * channel["spr"], b + round(5 * fs))
    if b - a < 2 or right - left > 2_000_000:
        raise ValueError("案例窗采样点太少或过多，请调整显示时长。")
    voltage = channel["scale"] is not None
    unit = "µV" if voltage else channel["unit"] or "原始单位"
    signal = source.read(channel, left, right) * (1e6 if voltage else 1)
    values = {"原始": signal}
    if voltage and fs > 100 and len(signal) > 27:
        values["50 Hz 陷波"] = notch_zero_phase(signal, fs, 50)
    if voltage and fs > 90 and len(signal) > 27:
        band = bandpass_zero_phase(signal, fs, 0.5, 45)
        values["0.5–45 Hz 带通"] = band
        if fs > 100:
            values["带通＋50 Hz 陷波"] = notch_zero_phase(band, fs, 50)
    view = {name: y[a-left:b-left] for name, y in values.items()}
    window = min(round(2 * fs), b - a)
    if window < 2:
        raise ValueError("采样点不足，无法计算功率谱。")
    time = np.arange(a, b) / fs
    waveform_names = list(view)
    if "带通＋50 Hz 陷波" in view:
        waveform_names.remove("0.5–45 Hz 带通")
    plt = plotting()
    fig, axes = plt.subplots(len(waveform_names) + 1, 1, figsize=(10, 2.35 * (len(waveform_names) + 1)),
                             constrained_layout=True)
    try:
        fig.suptitle(f"{channel['name']} · {a/fs:g}–{b/fs:g} s · {fs:g} Hz")
        for ax, name in zip(axes, waveform_names):
            ax.plot(time, view[name], lw=0.65)
            ax.set(xlim=(a/fs, b/fs), title=name, ylabel=unit, xlabel="记录时间（s）")
            ax.grid(alpha=0.22)
        if "50 Hz 陷波" in waveform_names:
            # 原始与仅陷波共用纵轴，直接比较振荡和基线。
            axes[1].set_ylim(axes[0].get_ylim())
        spectra = []
        for name, y in view.items():
            f, p = welch_psd(y, fs, nperseg=window, noverlap=window // 2)
            visible = (f > 0) & (f <= 160)
            axes[-1].plot(f[visible], 10 * np.log10(np.maximum(p[visible], 1e-30)), label=name)
            index = int(np.argmin(abs(f - 50)))
            spectra.append((name, number(np.min(y)), number(np.max(y)), number(np.ptp(y)),
                            number(np.max(np.abs(y))), number(f[index]) if fs > 100 else "—",
                            number(p[index]) if fs > 100 else "—"))
        if fs > 100:
            axes[-1].axvline(50, color="#b93030", linestyle="--", linewidth=1)
        axes[-1].set(xlabel="频率（Hz）", ylabel=f"PSD（dB re 1 {unit}²/Hz）",
                     xlim=(0, min(160, fs / 2)))
        axes[-1].legend(ncol=2, fontsize=9)
        fig.savefig(path, dpi=160)
    finally:
        plt.close(fig)
    return unit, spectra, (a/fs, b/fs, left/fs, right/fs, window/fs, fs/window)


def build_report(source, run, output, opts):
    decision = opts.get("decision", "pending")
    decisions = {"pending": "待人工决定", "keep": "保留", "exclude": "排除", "recommend_exclude": "建议暂缓纳入主分析"}
    if decision not in decisions:
        raise ValueError("复核决定无效。")
    note = str(opts.get("note", "")).strip()
    if len(note) > 2000 or (decision != "pending" and not note):
        raise ValueError("请填写保留或排除的依据，最多2000字。")
    draft = opts.get("draft", {})
    private = json.loads((run / "raw_qc_private.json").read_text(encoding="utf8"))
    saved = json.loads((run / "raw_qc_result.json").read_text(encoding="utf8"))
    mapping = assemble_mapping(source, private.get("recordinfo"), draft.get("overrides"))
    lookup = {r["name"]: r for r in source.signals}
    channel = lookup.get(opts.get("channel"))
    start, end = float(opts["start"]), float(opts["end"])
    if channel is None or not all(map(math.isfinite, (start, end))) or not 0 <= start < end <= source.duration + 1e-9:
        raise ValueError("请选择记录中的通道和有效案例时间窗。")
    if end - start > 120:
        raise ValueError("案例窗最多120秒，请缩短当前视图后导出。")
    bads = draft.get("bads", [])
    if not isinstance(bads, list) or any(not isinstance(n, str) or n not in lookup for n in bads):
        raise ValueError("坏道草稿含无效通道。")
    manual = validate_intervals(draft.get("bad_segments", []), source.duration)
    source_bad = []
    for event in source.annotations():
        a = max(0, event["onset"])
        b = min(source.duration, event["onset"] + event["duration"])
        if event["description"].lower().startswith("bad") and a < b:
            source_bad.append({"start": a, "end": b, "label": event["description"]})
    retained = [r["原始通道"] for r in mapping["rows"]
                if r["MNE类型"] == "eeg" and r["原始通道"] not in bads]
    removed = excluded_seconds(source_bad + manual)
    usable = max(0.0, source.duration - removed) if retained else 0.0
    saved_opts = saved.get("options", {})
    coverage = table(["项目", "记录"], [
        ("记录总时长（s）", number(source.duration)),
        ("当前草稿坏道", "、".join(dict.fromkeys(bads)) or "空"),
        ("当前草稿保留EEG电极", f"{len(retained)}个 · " + ("、".join(retained) or "空")),
        ("源BAD和草稿BAD合并时长（s）", number(removed)),
        ("按标记估算的可用时长（s）", number(usable)),
        ("上次载入或导出结果中的坏道", "、".join(saved_opts.get("bads", [])) or "空"),
    ])
    annotations = table(["来源", "起点（s）", "终点（s）", "标记"],
                        [(name, number(r["start"]), number(r["end"]), r["label"])
                         for name, spans in [("源BDF", source_bad), ("当前草稿", manual)]
                         for r in spans] or [("—", "—", "—", "无已登记坏段")])
    cache = run / "artifact_patrol_cache.json"
    cached = json.loads(cache.read_text(encoding="utf8")) if cache.is_file() else {}
    signature = cached.get("signature", {})
    stat = source.path.stat()
    current_cache = (
        signature.get("version") == VERSION
        and signature.get("source_size") == stat.st_size
        and signature.get("source_mtime_ns") == stat.st_mtime_ns
    )
    scan = cached.get("scan", {}) if current_cache else {}
    candidates = []
    case_reasons = set()
    for cells in scan.get("cells", []):
        for c in cells:
            if c.get("reasons"):
                if c["channel"] == channel["name"] and c["start"] < end and c["end"] > start:
                    case_reasons.update(c["reasons"])
                row = lookup[c["channel"]]
                cell_unit = "µV" if row["scale"] is not None else row["unit"] or "原始单位"
                candidates.append((c["channel"], cell_unit, number(c["start"]), number(c["end"]),
                                   "、".join(c["reasons"]), number(c.get("median")),
                                   number(c.get("ptp")), number(c.get("std")),
                                   number(c.get("max_step")), number(c.get("jump_threshold")),
                                   number(c.get("sustained_step_uv")), number(c.get("rail_fraction")),
                                   number(c.get("line_db")), number(c.get("line_fraction"))))
    entries = review_state(run, source, {})["entries"]
    review = table(["通道", "起点（s）", "终点（s）", "人工浏览状态"],
                   [("、".join(r["channels"]), number(r["start"]), number(r["end"]),
                     "已浏览" if r["status"] == "reviewed" else "需再看") for r in entries]
                   or [("—", "—", "—", "无登记")])
    figure = output / "质控案例_波形与PSD.png"
    unit, spectra, (a, b, left, right, window, df) = plot_case(source, channel, start, end, figure)
    values = table(["处理", f"最小值（{unit}）", f"最大值（{unit}）", f"峰峰值（{unit}）",
                    f"最大绝对值（{unit}）", "近50 Hz频点（Hz）", f"该频点PSD（{unit}²/Hz）"], spectra)
    candidate_table = table(
        ["通道", "单位", "起点（s）", "终点（s）", "候选原因", "中位数", "峰峰值", "标准差", "最大相邻差",
         "相邻差阈值", "持续阶跃（µV）", "边界占比", "工频突出度（dB）", "工频功率占比"],
        candidates or [("—",) * 14],
    )
    rules = json.dumps(signature.get("settings", {}), ensure_ascii=False) if scan else "无匹配扫描"
    case_status = "、".join(sorted(case_reasons)) or ("未触发已启用规则" if scan else "请运行当前版本的巡检")
    if scan and channel["name"] not in {r["name"] for r in scan["channels"]}:
        case_status = "该通道不在扫描范围内"
    encoded = base64.b64encode(figure.read_bytes()).decode("ascii")
    report = output / "脑电质控复核报告.html"
    report.write_text(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>脑电质控复核报告</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1120px;margin:auto;padding:24px;color:#183047}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #cbd7e2;padding:7px;text-align:left}}
th{{background:#eaf2f8}}img{{width:100%;height:auto}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}
.table{{overflow:auto}}@media print{{body{{padding:0}}img{{max-height:220mm;object-fit:contain}}}}</style>
<h1>脑电质控复核报告</h1>
<p>记录编号 {escape(run.name)} · 案例电极 {escape(channel["name"])} · {a:g}–{b:g} 秒 · 原采样率 {channel["fs"]:g} Hz</p>
<h2>案例处理决定</h2><p><strong>{decisions[decision]}</strong></p><pre>{escape(note) or "尚未填写依据"}</pre>
<h2>原始波形、滤波对照与频谱</h2>
<p>纵轴按BDF文件头标定。原始与仅陷波的波形共用纵轴。带通后单列实际纵轴。
在 {left:g}–{right:g} 秒连续样本上做零相位滤波，再截取案例窗。带通为四阶Butterworth原型，陷波Q=30。
Welch统一用 {window:g} 秒Hann窗、50%重叠、去均值，频率间隔 {df:g} Hz。图表用于原采样率下的局部诊断比较。</p>
<img alt="波形与PSD对照" src="data:image/png;base64,{encoded}">{values}
<h2>保留电极、坏段与有效时长</h2>{coverage}{annotations}
<p>有效时长按源BAD及当前草稿BAD的区间并集扣减，表示标记覆盖。建模实际时长还受滤波、切窗和后续质控约束。
比较镇痛前后时，需核对两次记录的共同电极和可匹配时长。</p>
<h2>自动候选及实际数值</h2><p>案例窗 {escape(case_status)}。最近一次匹配扫描 {escape(str(scan.get("version", "无")))} · 参数 {escape(rules)}。
候选表按扫描窗逐行列出。电压通道幅值为µV，其他通道使用各自原始单位。</p>
<div class="table">{candidate_table}</div>
<h2>人工浏览记录</h2>{review}<p>浏览状态用于记录复核范围。案例决定对应上方指定电极和时间窗。
保留电极和可用时长按源BAD标记及导出时的坏道、坏段草稿计算。</p></html>""", encoding="utf8")
    return [report, figure]
