"""在浏览器选中的一次镇痛前记录中查找最早的合格五分钟。"""
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from eeg_events import recording_start, read_event_file
from eeg_hrv import FS, WIN, DURATION, assess, first_start, load_signal, measure
from eeg_raw_bdf import NativeBDF
from eeg_web_extra import new_run, public_files


def receive(handler, folder):
    # 请求头仅列出分段号和字节数。正文依次存入临时文件，无需传输姓名或本机路径。
    header = handler.headers.get("X-HRV-Files", "")
    if len(header) > 8192:
        raise ValueError("分段清单过长。")
    parts = json.loads(header)
    if not isinstance(parts, list) or not 1 <= len(parts) <= 129:
        raise ValueError("请选择一次记录的 1–128 个数据分段。")
    manifest = {}
    for tag, size in parts:
        if tag != "evt" and (type(tag) is not int or not 0 < tag <= 1000000):
            raise ValueError("无效分段编号。")
        limit = 20*1024**2 if tag == "evt" else 2*1024**3
        if tag in manifest or type(size) is not int or not 0 < size <= limit:
            raise ValueError("文件重复、为空或超过大小限制。")
        manifest[tag] = size
    if not 1 <= len(manifest.keys()-{"evt"}) <= 128 or sum(manifest.values()) != int(handler.headers.get("Content-Length", -1)):
        raise ValueError("上传清单与文件内容长度不符。")
    files = {}
    for tag, remaining in manifest.items():
        path = folder / f"{tag}.bdf"
        with path.open("wb") as stream:
            while remaining:
                chunk = handler.rfile.read(min(remaining, 1024**2))
                if not chunk:
                    raise ValueError("上传中断，请重试。")
                stream.write(chunk)
                remaining -= len(chunk)
        files[tag] = path
    return files


def inspect_part(path):
    raw = NativeBDF(path)
    raw.annotations()
    clock = recording_start(dict(start_date=raw.local_date, start_time=raw.local_time,
                                 start_offset_seconds=getattr(raw,"annotation_origin",0)))
    if not any(c["name"] == "EKG" for c in raw.signals):
        raise ValueError("缺少 EKG 通道")
    if raw.duration < DURATION:
        return clock, None, "单段时长不足 300 秒"
    broad, qrs, energy = load_signal(path)
    rows = []
    for start in range(0,len(qrs)-WIN*FS+1,WIN*FS):
        sl = slice(start,start+WIN*FS)
        quality, _ = assess(broad[sl],qrs[sl],energy[sl])
        rows.append({"起点秒":start//FS, "合格":quality["合格"]})
    start = first_start(pd.DataFrame(rows))
    return clock, start, "有连续合格五分钟" if start is not None else "没有连续 30 个合格的 10 秒窗"


def review_plot(wave, beats, path):
    figure = Figure(figsize=(9,5), layout="constrained")
    top,bottom = figure.subplots(2,1)
    t = np.arange(10*FS)/FS
    top.plot(t,wave[:len(t)],color="#264c67",lw=.85)
    peak = beats.loc[beats.峰位置秒 < 10]
    top.scatter(peak.峰位置秒,wave[np.rint(peak.峰位置秒*FS).astype(int)],s=15,color="#bc5838")
    top.set(title="Detected beats | first 10 seconds",xlabel="Time in window (s)",ylabel="EKG (native scaled units)")
    for ok,color,label in [(True,"#187568","Retained"),(False,"#bf593e","Excluded")]:
        g=beats[beats.前一间期合格.eq(ok)]
        bottom.scatter(g.峰位置秒,g.前一间期ms,s=8,c=color,label=label)
    bottom.set(title="Beat intervals | full 5 minutes",xlabel="Time in window (s)",ylabel="RR interval (ms)",xlim=(0,300))
    bottom.legend()
    figure.savefig(path,dpi=130)


def analyze(files, root):
    files = files.copy()
    evt = files.pop("evt",None)
    candidates, audit, warnings = [], [], []
    for part, path in files.items():
        try:
            clock, start, reason = inspect_part(path)
        except (ValueError, OSError) as exc:
            clock, start, reason = None, None, str(exc)
            warnings.append(f"分段 {part} 无法核验：{reason}")
        if start is not None:
            candidates.append((clock+timedelta(seconds=int(start)), str(part), part, int(start)))
        audit.append(dict(分段=part,首个合格窗起点秒=start,分段检查=reason))
    run_id, folder = new_run(root)
    audit_file = folder/"分段检查.csv"
    pd.DataFrame(audit).to_csv(audit_file,index=False,encoding="utf-8-sig")
    output = [audit_file]
    info = dict(状态="没有可用的连续五分钟")
    if candidates:
        clock, _, part, start = min(candidates)
        broad, qrs, energy = load_signal(files[part])
        sl = slice(start*FS,(start+DURATION)*FS)
        stats, beats = measure(qrs[sl],energy[sl])
        info = dict(状态="通过" if stats["通过整段质控"] else "整段质控未通过",
                    分段=part,文件内起点秒=start,文件内终点秒=start+DURATION,**stats)
        if not stats["通过整段质控"]:
            warnings.append("首个五分钟未同时满足双检测一致度 ≥95%、合格 RR ≥95% 和有效相邻间期对数 ≥180；本次不输出 RMSSD。")
        if evt is not None:
            parsed = read_event_file(evt)
            offset = (clock-recording_start(parsed)).total_seconds()
            info["事件时钟起点秒"] = offset
            info["宫缩标记数"] = len({e["onset"] for e in parsed["events"] if e["label"]=="KeyIn:253" and offset<=e["onset"]<offset+DURATION})
        beats.to_csv(folder/"逐搏复核.csv",index=False,encoding="utf-8-sig")
        review_plot(broad[sl],beats,folder/"五分钟心电复核.png")
        output.extend([folder/"逐搏复核.csv",folder/"五分钟心电复核.png"])
    summary = pd.DataFrame([info])
    summary.to_csv(folder/"心电提取结果.csv",index=False,encoding="utf-8-sig")
    output.append(folder/"心电提取结果.csv")
    return dict(metrics=json.loads(summary.to_json(orient="records",double_precision=15))[0],
                parts=audit, warnings=warnings, files=public_files(output,run_id))


def handle_hrv(handler, root):
    with TemporaryDirectory(prefix="hrv_") as temp:
        files = receive(handler,Path(temp))
        result = analyze(files, root)
    handler.send_json(200,result)
