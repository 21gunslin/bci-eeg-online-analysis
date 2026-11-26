"""网页和队列分析共用的镇痛前五分钟心电计算。"""
import numpy as np
import pandas as pd
from scipy import signal
from scipy.ndimage import uniform_filter1d, median_filter
from scipy.signal import find_peaks
from eeg_raw_bdf import NativeBDF

DURATION = 300
FS=250
WIN=10
def band(x,lo,hi):
    sos=signal.butter(3,[lo,hi],btype='bandpass',fs=FS,output='sos')
    return signal.sosfiltfilt(sos,x)

def assess(w,b,en):
    # 两条候选峰序列：5–25 Hz单极性尖峰，以及8–30 Hz斜率能量包络。
    # 以60 ms容差核对二者。阈值是本项目可复核的探索标准，不是金标准诊断。
    center=np.median(b); lower,upper=np.quantile(b,[.015,.985])-center
    sign=1 if upper>=abs(lower) else -1
    sharp=sign*(b-center)
    height=max(np.quantile(sharp,.90),.27*np.quantile(sharp,.99),1e-16)
    peaks=signal.find_peaks(sharp,height=height,prominence=height*.8,distance=int(.30*FS))[0]
    emin=np.median(en); spread=np.quantile(en,.97)-emin
    energy=signal.find_peaks(en,height=emin+.30*spread,prominence=.20*spread,distance=int(.30*FS))[0]
    peaks=peaks[(peaks>=.12*FS)&(peaks<len(b)-.2*FS)]
    energy=energy[(energy>=.12*FS)&(energy<len(b)-.2*FS)]
    matched=sum(np.any(np.abs(energy-i)<=.060*FS) for i in peaks)
    agreement=2*matched/max(1,len(peaks)+len(energy))
    out=dict(峰数=len(peaks),双检测一致度=agreement,心率=np.nan,生理RR比例=0.,模板相关=np.nan,峰噪比=0.,合格=False)
    if len(peaks)<5: return out,peaks
    rr=np.diff(peaks)/FS
    # 心搏模板来自较宽带的0.5–40 Hz波形，去除每搏的局部基线。
    beats=np.stack([w[i-25:i+45] for i in peaks])
    beats-=np.median(beats[:,:12],axis=1,keepdims=True)
    template=np.median(beats,axis=0)
    z=beats-beats.mean(axis=1,keepdims=True); t=template-template.mean()
    corr=np.median((z@t)/(np.linalg.norm(z,axis=1)*np.linalg.norm(t)+1e-36))
    noise=np.ones(len(sharp),bool)
    for i in peaks: noise[max(0,i-21):i+22]=False
    mad=np.median(abs(sharp[noise]-np.median(sharp[noise]))) if noise.any() else np.inf
    snr=np.median(sharp[peaks])/(1.4826*mad+1e-15)
    hr=60/np.median(rr); valid=np.mean((rr>=.32)&(rr<=1.5))
    # 同时要求足够多的心搏、两种检测相符、重复形态清楚且心搏间期合理。
    ok=7<=len(peaks)<=30 and agreement>=.85 and valid>=.90 and 45<=hr<=170 and corr>=.80 and snr>=5
    out.update(心率=hr,生理RR比例=valid,模板相关=corr,峰噪比=snr,合格=bool(ok))
    return out,peaks

def load_signal(path):
    raw = NativeBDF(path)
    c = next((r for r in raw.signals if r['name'] == 'EKG'), None)
    if c is None:
        raise ValueError('没有名为 EKG 的采样通道。')
    x=raw.read(c)
    if c['fs']!=1000: raise ValueError('本批预期EKG采样率为1000 Hz')
    x=signal.resample_poly(x,1,4)
    return band(x,.5,40),band(x,5,25),uniform_filter1d(np.gradient(band(x,8,30))**2,size=17)

def first_start(piece):
    # 只允许同一文件中首尾相接的30个10秒窗。遇到空缺或质量失败便重新计数。
    run = 0
    last = None
    for start, passed in piece.sort_values('起点秒')[['起点秒', '合格']].itertuples(index=False, name=None):
        if last is None or start != last + 10:
            run = 0
        run = run + 1 if passed else 0
        if run == 30:
            return start - 290
        last = start
    return None


def measure(qrs, energy):
    # 两个检测器都一次性扫描全长。每10秒估一次背景噪声，再平滑连接阈值。
    # 固定一个主波极性，避免在同一条记录内由R波顶端切换到S波底端。
    width = 10 * FS
    blocks = qrs.reshape(-1, width)
    median = np.median(blocks, axis=1)
    low, high = np.quantile(blocks, [.015,.985], axis=1) - median
    polarity = 1 if np.median(high - abs(low)) >= 0 else -1
    centers = (np.arange(len(blocks)) + .5) * width
    samples = np.arange(len(qrs))
    def smooth(values):
        return np.interp(samples, centers, values)
    sharp = polarity * (qrs - smooth(median))
    q90, q99 = np.quantile(sharp.reshape(-1,width), [.90,.99], axis=1)
    threshold = smooth(np.maximum(np.maximum(q90, .27*q99), 1e-16))
    peaks = find_peaks(sharp, height=(threshold,None), prominence=(.8*threshold,None), distance=.30*FS)[0]
    base, upper = np.quantile(energy.reshape(-1,width), [.50,.97], axis=1)
    spread = smooth(upper - base)
    alternate = find_peaks(energy, height=(smooth(base)+.30*spread,None), prominence=(.20*spread,None), distance=.30*FS)[0]
    # 两端留出0.2秒。此操作只发生在整段的开头和末尾。
    def interior(p):
        return p[(p >= .20*FS) & (p < len(qrs) - .20*FS)]
    peaks, alternate = interior(peaks), interior(alternate)
    confirmed = np.array([np.any(np.abs(alternate - p) <= .060*FS) for p in peaks], dtype=bool)
    rr = np.diff(peaks)*1000./FS
    if len(rr) < 2:
        raise ValueError('合格五分钟内意外缺少候选搏')
    # 异常间期保留在台账中。仅连续两个间期同时通过时才计算差值，不跨越坏搏或用插值补搏。
    local = median_filter(rr, size=11, mode='nearest')
    good = confirmed[1:] & confirmed[:-1] & (rr >= 320) & (rr <= 1500) & (np.abs(rr-local) <= .20*local)
    pairs = good[:-1] & good[1:]
    agreement = 2*confirmed.sum() / max(1, len(peaks)+len(alternate))
    usable = good.mean() >= .95 and pairs.sum() >= 180 and agreement >= .95
    value = np.sqrt(np.mean(np.diff(rr)[pairs]**2)) if pairs.any() else np.nan
    stats = dict(候选搏数=len(peaks),双检测一致度=agreement,保留RR比例=good.mean(),有效相邻间期对数=pairs.sum(),
                 通过整段质控=bool(usable),RMSSD_ms=value if usable else np.nan)
    beats = pd.DataFrame(dict(峰位置秒=peaks/FS,双检测匹配=confirmed,前一间期ms=np.r_[np.nan,rr],
                              前一间期合格=np.r_[False,good],用于RMSSD=np.r_[False,False,pairs]))
    return stats, beats
