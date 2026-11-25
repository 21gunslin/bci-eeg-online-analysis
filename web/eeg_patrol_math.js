/* Manual review coverage is measured in exact channel-seconds, not viewed pages. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.EEGPatrolMath = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function merge(intervals) {
    const sorted = intervals.map((r) => [r[0], r[1]]).sort((a, b) => a[0] - b[0]),
      out = [];
    for (const [a, b] of sorted) {
      if (b <= a) continue;
      if (out.length && a <= out.at(-1)[1]) out.at(-1)[1] = Math.max(out.at(-1)[1], b);
      else out.push([a, b]);
    }
    return out;
  }
  function intervals(entries, name) {
    return merge(entries.filter((r) => r.channels.includes(name)).map((r) => [r.start, r.end]));
  }
  function coverage(entries, names, duration) {
    let covered = 0;
    for (const n of names)
      for (const [a, b] of intervals(entries, n))
        covered += Math.max(0, Math.min(duration, b) - Math.max(0, a));
    const total = names.length * duration;
    return { covered, total, percent: total ? (100 * covered) / total : 0 };
  }
  function cellFraction(entries, name, start, end) {
    let seen = 0;
    for (const [a, b] of intervals(entries, name))
      seen += Math.max(0, Math.min(end, b) - Math.max(start, a));
    return Math.min(1, seen / (end - start));
  }
  function pages(names, size, index) {
    const count = Math.max(1, Math.ceil(names.length / size)),
      page = Math.max(0, Math.min(count - 1, index));
    return { page, count, names: names.slice(page * size, (page + 1) * size) };
  }
  function buildDraft(scan, badsText = '', segmentsText = '') {
    if (
      !scan ||
      !Array.isArray(scan.candidates) ||
      !Number.isFinite(scan.duration) ||
      scan.duration <= 0
    )
      throw new Error('请先扫描当前记录。');
    const names = new Set(
        badsText
          .split(/[,，\n]/)
          .map((n) => n.trim())
          .filter(Boolean),
      ),
      addedNames = [],
      groups = new Map(),
      allowed = new Set(scan.channels.map((c) => c.name));
    let skippedCandidates = 0;
    for (const c of scan.candidates) {
      // 工频和旧幅度规则仍不参与；主动勾选的饱和/阶跃只能转为局部注释。
      if (!['平线', '数字边界/饱和候选', '持续电位阶跃候选'].includes(c.reason) || c.draft_eligible === false) {
        skippedCandidates++;
        continue;
      }
      if (!allowed.has(c.channel) || /[,，\r\n]/.test(c.channel))
        throw new Error('候选通道名不能快速填入，请核对通道名。');
      const spans = Array.isArray(c.segments) ? c.segments : [{ start: c.start, end: c.end }];
      const wholeChannel = c.reason === '平线' && c.whole_channel === true &&
        c.fraction === 1 && spans.length === 1 && spans[0].start === 0 && spans[0].end === scan.duration;
      if (wholeChannel) {
        if (!names.has(c.channel)) {
          names.add(c.channel);
          addedNames.push(c.channel);
        }
        continue;
      }
      // 旧版的persistent候选只有包络，缺少正常间隙信息，必须重扫。
      if (c.persistent && !Array.isArray(c.segments))
        throw new Error('旧扫描缺少局部区间，请重新点击黄色按钮，避免把正常间隙标为坏段。');
      if (!spans.length) throw new Error('候选区间为空，请重新扫描。');
      for (const span of spans) {
        if (!Number.isFinite(span.start) || !Number.isFinite(span.end) ||
            span.start < 0 || span.end > scan.duration || span.start >= span.end)
          throw new Error('候选时间无效，请重新扫描。');
        const key = span.start + '|' + span.end;
        if (!groups.has(key)) groups.set(key, { start: span.start, end: span.end, labels: new Set() });
        groups.get(key).labels.add(c.channel + ':' + String(c.reason).replace(/[,，\r\n]/g, '；').slice(0,120));
      }
    }
    const existing = segmentsText
      .split('\n')
      .map((line) => line.split(/[,，]/).map((s) => s.trim()))
      .filter(
        (p) =>
          p.length === 3 &&
          p[0] !== '' &&
          p[1] !== '' &&
          Number.isFinite(Number(p[0])) &&
          Number.isFinite(Number(p[1])) &&
          Number(p[1]) > Number(p[0]),
      )
      .map((p) => [Number(p[0]), Number(p[1])]);
    const rows = [];
    let covered = merge(existing);
    for (const g of [...groups.values()].sort((a, b) => a.start - b.start || a.end - b.end)) {
      if (covered.some(([a, b]) => a <= g.start && b >= g.end)) continue;
      // Use the exact candidate bounds, not its surrounding display context or a persistent envelope.
      rows.push(g.start + ',' + g.end + ',待核候选_' + [...g.labels].join('；'));
      covered = merge([...covered, [g.start, g.end]]);
    }
    const append = (original, values, separator) =>
      !values.length
        ? original
        : original + (original.trim() ? separator : '') + values.join(separator);
    const nextSegments = append(segmentsText, rows, '\n');
    if (nextSegments.split('\n').filter((s) => s.trim()).length > 1000)
      throw new Error('坏段草稿超过1000条，请调整扫描参数后重试；原草稿未更改。');
    return {
      badsText: append(badsText, addedNames, ', '),
      segmentsText: nextSegments,
      addedBads: addedNames.length,
      skippedCandidates,
      addedSegments: rows.length,
      totalBads: names.size,
      allEegBad:
        scan.channels.some((c) => c.eeg) &&
        scan.channels.filter((c) => c.eeg).every((c) => names.has(c.name)),
    };
  }
  return { merge, coverage, cellFraction, pages, buildDraft };
});
