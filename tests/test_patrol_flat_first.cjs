const test = require('node:test'), assert = require('node:assert/strict');
const M = require('../web/eeg_patrol_math.js');
const scan = () => ({duration:20, channels:[{name:'Fp1',eeg:true},{name:'F3',eeg:true}],
  candidates:[
    {channel:'Fp1',reason:'平线',persistent:true,start:0,end:20,draft_eligible:true,
      fraction:1,whole_channel:true,segments:[{start:0,end:20}]},
    {channel:'F3',reason:'平线',persistent:false,start:4,end:6,draft_eligible:true},
    {channel:'F3',reason:'50Hz强工频候选',persistent:true,start:0,end:20},
    {channel:'F3',reason:'持续电位阶跃候选（仅复核）',persistent:false,start:8,end:10,draft_eligible:false},
    {channel:'F3',reason:'数字边界/饱和候选',persistent:true,start:0,end:20,draft_eligible:false},
  ]});
test('whole flat goes to bad channel, local flat to span, legacy line noise ignored',()=>{
  const d=M.buildDraft(scan());
  assert.equal(d.badsText,'Fp1');
  assert.equal(d.addedSegments,1);
  assert.match(d.segmentsText,/^4,6,/);
  assert.doesNotMatch(d.segmentsText,/工频|阶跃|饱和/);
  assert.equal(d.skippedCandidates,3);
});
test('manual entries preserved and repeated click does not duplicate flats',()=>{
  const a=M.buildDraft(scan(),'manual','1,2,user');
  const b=M.buildDraft(scan(),a.badsText,a.segmentsText);
  assert.equal(a.badsText,b.badsText); assert.equal(a.segmentsText,b.segmentsText);
  assert.equal(b.addedBads,0);assert.equal(b.addedSegments,0);
});
test('review-only candidates do not create BADs or intervals',()=>{
  const s=scan();s.candidates=s.candidates.slice(2);
  const d=M.buildDraft(s,'manual','1,2,user');
  assert.equal(d.badsText,'manual');assert.equal(d.segmentsText,'1,2,user');
});
test('eighty percent flat retains the normal gap and does not become whole-channel BAD',()=>{
  const s=scan();s.candidates=[{channel:'F3',reason:'平线',persistent:true,start:0,end:20,
    fraction:.8,whole_channel:false,segments:[{start:0,end:4},{start:8,end:20}]}];
  const d=M.buildDraft(s);
  assert.equal(d.badsText,'');assert.equal(d.addedSegments,2);
  assert.match(d.segmentsText,/^0,4,/);assert.match(d.segmentsText,/\n8,20,/);
});
test('selected saturation and step become spans even if persistent, never bad channels',()=>{
  const s=scan();s.candidates=[
    {channel:'F3',reason:'数字边界/饱和候选',persistent:true,start:0,end:20,whole_channel:false,
      draft_eligible:true,segments:[{start:0,end:4},{start:8,end:20}]},
    {channel:'F3',reason:'持续电位阶跃候选',persistent:false,start:5,end:6,draft_eligible:true}];
  const d=M.buildDraft(s);
  assert.equal(d.badsText,'');assert.equal(d.addedSegments,3);
});
test('old persistent envelopes must be rescanned instead of marking normal gaps',()=>{
  const s=scan();s.candidates=[{channel:'F3',reason:'平线',persistent:true,start:0,end:20}];
  assert.throws(()=>M.buildDraft(s),/重新/);
});
