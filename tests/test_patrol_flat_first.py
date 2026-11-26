"""有正常起伏、单点尖峰、强工频时不误填BAD；真正平线仍能定位。"""
from pathlib import Path
import hashlib
import sys
import tempfile
import unittest

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from eeg_artifact_patrol import scan_record, normalize_checks
from eeg_montage import montage_mapping


def synthetic_bdf(path, intermittent=False):
    """写一份已知答案的24位BDF：物理标定为每个计数1µV。"""
    fs, seconds = 500, 20
    t=np.arange(fs*seconds)/fs
    sine=20*np.sin(2*np.pi*10*t)
    bump=200*np.exp(-((t-8)/.4)**2)
    spike=sine.copy(); spike[7*fs]=3000
    step=sine.copy(); step[9*fs:]+=500
    rail=sine.copy();rail[6*fs:7*fs]=8388607
    signals={'Fp1':np.zeros_like(t),'F3':sine+bump,'F4':spike,
             'C3':sine+200*np.sin(2*np.pi*50*t),'C4':step,'Cz':rail}
    if intermittent:
        signals['Fp1'][4*fs:8*fs] = sine[4*fs:8*fs]
    count=len(signals)
    fixed=bytearray(b' '*256);fixed[:8]=b'\xffBIOSEMI'
    for offset,width,value in [(168,8,'01.01.24'),(176,8,'08.30.00'),
        (184,8,str(256*(count+1))),(236,8,'1'),(244,8,str(seconds)),(252,4,str(count))]:
        fixed[offset:offset+width]=value.encode().ljust(width)
    fields=[(list(signals),16),(['']*count,80),(['uV']*count,8),
        ([-8388608]*count,8),([8388607]*count,8),([-8388608]*count,8),
        ([8388607]*count,8),(['']*count,80),([fs*seconds]*count,8),(['']*count,32)]
    header=b''.join(str(v).encode().ljust(width) for values,width in fields for v in values)
    payload=[]
    for values in signals.values():
        u=np.rint(values).astype('i4')&0xFFFFFF
        payload.append(np.stack([u&255,(u>>8)&255,(u>>16)&255],axis=1).astype('u1').tobytes())
    path.write_bytes(fixed+header+b''.join(payload))
    return montage_mapping(list(signals))


class FlatFirstTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'synthetic.bdf'
        self.mapping=synthetic_bdf(self.path)
        self.original=hashlib.sha256(self.path.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),self.original)
        self.tmp.cleanup()

    def test_default_only_flat_even_with_large_spike_rail_and_powerline(self):
        scan=scan_record(self.path,self.mapping)
        self.assertEqual(scan['enabled_checks'],['flat'])
        self.assertEqual([(c['channel'],c['reason']) for c in scan['candidates']],[('Fp1','平线')])
        flat=scan['candidates'][0]
        self.assertTrue(flat['persistent'] and flat['draft_eligible'])
        self.assertTrue(flat['whole_channel'])
        self.assertEqual(flat['mark_kind'],'channel')
        self.assertEqual((flat['start'],flat['end']),(0,20))

    def test_optional_step_rejects_normal_bump_spike_and_powerline(self):
        scan=scan_record(self.path,self.mapping,enabled_checks=['step'])
        candidates=scan['candidates']
        self.assertTrue(any(c['channel']=='C4' for c in candidates))
        self.assertFalse(any(c['channel'] in ['F3','F4','C3'] for c in candidates))
        self.assertTrue(all(c['draft_eligible'] and not c['whole_channel'] for c in candidates))
        self.assertTrue(all('阶跃' in c['reason'] for c in candidates))

    def test_optional_saturation_is_review_only(self):
        scan=scan_record(self.path,self.mapping,enabled_checks=['saturation'])
        self.assertEqual({c['channel'] for c in scan['candidates']},{'Cz'})
        self.assertTrue(all(c['draft_eligible'] and not c['whole_channel'] for c in scan['candidates']))

    def test_eighty_percent_flat_keeps_normal_gap_and_never_discards_channel(self):
        path=Path(self.tmp.name)/'intermittent.bdf'
        mapping=synthetic_bdf(path,intermittent=True)
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        scan=scan_record(path,mapping)
        flat=next(c for c in scan['candidates'] if c['channel']=='Fp1')
        self.assertEqual(flat['fraction'],.8)
        self.assertTrue(flat['persistent'])
        self.assertFalse(flat['whole_channel'])
        self.assertEqual(flat['mark_kind'],'segment')
        self.assertEqual(flat['segments'],[{'start':0.,'end':4.},{'start':8.,'end':20.}])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_threshold_and_settings_are_validated(self):
        for invalid in [[],['line'],['step',True],'flat']:
            with self.assertRaises(ValueError):normalize_checks(invalid)
        with self.assertRaises(ValueError):scan_record(self.path,self.mapping,step_min_uv=float('nan'))
        scan=scan_record(self.path,self.mapping,enabled_checks=['step'],step_min_uv=1e6)
        # 500µV阶跃被下限挡住；数字饱和达数百万µV，仍可能触发复核。
        self.assertFalse(any(c['channel']=='C4' for c in scan['candidates']))


if __name__=='__main__':unittest.main()
