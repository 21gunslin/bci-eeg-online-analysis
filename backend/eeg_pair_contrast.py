"""镇痛前后事件对照。每个分段独立质控和估谱，以原始时钟选择事件与背景。"""
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from eeg_events import align_events, read_event_file
from eeg_feature_engine import AnalysisSettings, EEGQualityError, preprocess_eeg, read_eeg_file
from eeg_mne_ops import welch_psd
from eeg_raw_bdf import NativeBDF
from eeg_web_extra import new_run, public_files

CHANNELS = "Fp1 Fp2 F3 Fz F4 F7 F8 C3 Cz C4 T3 T4".split()


def prepare(path, events, offset, settings):
    data, labels, units, header, _ = read_eeg_file(path)
    header.channel_labels, header.channel_units = labels, units
    if path.suffix == ".bdf":
        raw = NativeBDF(path)
        raw.annotations()
        header.start_offset_seconds = getattr(raw, "annotation_origin", 0.0)
    rows, _ = align_events(events, header.to_dict(), offset_seconds=offset)
    times = sorted({r["相对本段起点_s"] for r in rows if r["原始标签"] == "KeyIn:253"})
    if not times:
        raise ValueError("缺少 KeyIn:253")
    pre = preprocess_eeg(data, header, settings)
    n = pre["original_epoch_samples"]
    if n != 500 or pre["fs"] != 250:
        raise ValueError("数据无法按 250 Hz、2 秒分析")
    centers = 2 * np.asarray(pre["kept_epoch_indices"]) + 1
    times = np.asarray(times)
    right = np.searchsorted(times, centers).clip(0, len(times)-1)
    left = (right-1).clip(0)
    nearest = np.where(abs(centers-times[left]) <= abs(centers-times[right]), left, right)
    distance = abs(centers-times[nearest])
    targets = np.flatnonzero(distance <= 9 + 1e-8)
    if not len(targets):
        raise ValueError("标记附近没有通过质控的完整片段")
    names = [c for c in CHANNELS if c in pre["channel_names"]]
    # 只存每个2秒窗的频段功率和总功率。最后先平均功率，再算比值。
    if len(names) < 3:
        raise ValueError("候选电极不足3个")
    ix = [pre["channel_names"].index(c) for c in names]
    epochs = pre["data"][ix].reshape(len(ix), -1, n)
    hz, psd = welch_psd(epochs, 250, nperseg=n, noverlap=0)
    power = np.stack([psd[..., mask].sum(axis=-1) for mask in
                      [(hz >= 8) & (hz < 13), (hz >= .5) & (hz < 4), (hz >= .5) & (hz <= 45)]], axis=-1)
    return dict(names=names, centers=centers, targets=targets, distance=distance,
                background=np.flatnonzero(distance >= 21), power=power)


def select_targets(pieces):
    return sorted((p["distance"][i], key, int(i)) for key,p in pieces.items() for i in p["targets"])


def measure(pieces, selected, common):
    event_power, bg_power = [], []
    for key in sorted({key for _,key,_ in selected}):
        p = pieces[key]
        targets = [i for _,k,i in selected if k == key]
        bg = p["background"]
        dist = abs(p["centers"][targets,None] - p["centers"][bg])
        # 虚拟列允许配对失败，每个真实背景最多用一次。
        costs = np.column_stack([np.where(dist <= 120, dist, 1e8), np.full((len(targets),len(targets)),1e5)])
        ii,jj = linear_sum_assignment(costs)
        channels = [p["names"].index(c) for c in common]
        for i,j in zip(ii,jj):
            if j < len(bg) and dist[i,j] <= 120:
                event_power.append(p["power"][channels,targets[i]])
                bg_power.append(p["power"][channels,bg[j]])
    pairs = len(event_power)
    if pairs < 10 or len(common) < 3:
        return pairs, [None,None]
    relative = []
    for spectra in (event_power,bg_power):
        mean = np.mean(spectra,axis=0)
        if np.any(mean[:,2] <= 0) or not np.isfinite(mean).all():
            return pairs, [None,None]
        relative.append((mean[:,:2] / mean[:,2,None]).mean(axis=0))
    return pairs, (relative[0]-relative[1]).tolist()


def handle_contrast(handler, root):
    form = json.loads(handler.headers["X-Pair-Manifest"])
    fmt = form["format"]
    notch = form["notch"]
    if fmt not in ("bdf","fif") or notch not in ("50","60","none"):
        raise ValueError("格式或陷波设置无效")
    settings = AnalysisSettings(notches=() if notch=="none" else (float(notch),2*float(notch)))
    entries = form["files"]
    if not isinstance(entries,list) or not 2 <= len(entries) <= 258:
        raise ValueError("两次记录最多各支持128段")
    seen = set()
    total = 0
    for side,part,size in entries:
        if side not in ("A","B") or (part != "evt" and (type(part) is not int or not 0 <= part <= 1000000)):
            raise ValueError("分段标识无效")
        if (side,part) in seen or type(size) is not int or not 0 < size <= (20*1024**2 if part=="evt" else 2*1024**3):
            raise ValueError("重复、空或超大文件")
        seen.add((side,part))
        total += size
    if total != int(handler.headers["Content-Length"]):
        raise ValueError("文件清单长度不符")
    pieces = {s:{} for s in "AB"}
    audit = []
    with TemporaryDirectory(prefix="pair_contrast_") as tmp:
        paths = {}
        for side,part,size in entries:
            path = Path(tmp) / f"{side}_{part}.{'bdf' if part=='evt' else fmt}"
            paths[side,part] = path
            with path.open("wb") as out:
                while size:
                    chunk = handler.rfile.read(min(size,1024**2))
                    if not chunk:
                        raise ValueError("上传中断")
                    out.write(chunk)
                    size -= len(chunk)
        for side in "AB":
            offset = float(form["offset"][side])
            if not math.isfinite(offset) or abs(offset)>86400:
                raise ValueError("事件时间偏移无效")
            files = [(k,p) for (s,k),p in paths.items() if s==side and k!="evt"]
            if not 1 <= len(files) <= 128:
                raise ValueError("每次记录需要1至128个数据分段")
            marker = paths.get((side,"evt"))
            events = read_event_file(marker) if marker else None
            for k,path in files:
                try:
                    if events is None:
                        raise ValueError("未提供 evt.bdf")
                    pieces[side][k] = prepare(path,events,offset,settings)
                    note = "有候选事件窗"
                except (ValueError, EEGQualityError, OSError) as exc:
                    note = str(exc)
                audit.append(dict(时相=side,分段=k,检查=note))
    targets = {s:select_targets(pieces[s])[:20] for s in "AB"}
    if all(len(v)>=10 for v in targets.values()):
        count = min(map(len,targets.values()))
        targets = {s:v[:count] for s,v in targets.items()}
    used = [pieces[s][key]["names"] for s in "AB" for _,key,_ in targets[s]]
    common = [c for c in CHANNELS if used and all(c in names for names in used)]
    a, b = [measure(pieces[s],targets[s],common) for s in "AB"]
    rows = []
    for name,x,y in zip(["alpha_contrast","delta_contrast"],a[1],b[1]):
        rows.append(dict(指标=name,镇痛前=x,镇痛后=y,后减前=y-x if x is not None and y is not None else None,
                         前配对数=a[0],后配对数=b[0],电极=" ".join(common),单位="相对功率差（比例）",
                         标记="KeyIn:253",采样率Hz=250,窗长秒=2,陷波Hz=notch,
                         前事件校正秒=form["offset"]["A"],后事件校正秒=form["offset"]["B"]))
    rid,folder = new_run(root)
    paths = [folder/"镇痛前后_事件背景对比.csv", folder/"镇痛前后_分段核对.csv"]
    for path,records in zip(paths,[rows,audit]):
        pd.DataFrame(records).to_csv(path,index=False,encoding="utf-8-sig")
    handler.send_json(200,dict(rows=rows,audit=audit,files=public_files(paths,rid)))
