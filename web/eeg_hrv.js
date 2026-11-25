// 一个请求携带本次记录的全部BDF，后端用采集时钟决定哪一段最早。
(() => {
  const folder = $('hrvFolder'), single = $('hrvSingle'), evt = $('hrvEvt');
  function selection() {
    let data = [], marker = evt.files[0];
    if (folder.files.length) {
      const { records } = EEGFolderImport.indexFolderFiles(folder.files, 'bdf');
      if (records.length !== 1) throw new Error('请选择恰好一次镇痛前记录的文件夹，其中应有数字子目录及 data.bdf。');
      const record = records[0];
      data = record.data.map((part) => [part.number, part.file]);
      marker = marker || record.aux.evt[0];
      if (new Set(data.map(([n]) => n)).size !== data.length) throw new Error('存在重复分段编号，请核对记录目录。');
    } else if (single.files.length) {
      data = [[1, single.files[0]]];
    }
    if (!data.length) throw new Error('请选择记录文件夹或单个 data.bdf。');
    data.forEach(([, f]) => checkFormat(f, 'bdf'));
    if (marker) {
      checkFormat(marker, 'bdf');
      if (marker.size > 20 * 1024 ** 2) throw new Error('evt.bdf 超过 20 MB。');
    }
    return { data, marker };
  }
  function changed(source) {
    if (source === folder || source === single) {
      (source === folder ? single : folder).value = '';
      evt.value = '';
    }
    $('hrvResult').classList.add('hide');
    status('hrvStatus','');
    try {
      const { data, marker } = selection();
      $('hrvSelection').textContent = '已选 ' + data.length + ' 份数据 · ' + (marker ? '已匹配事件文件' : '未提供事件文件，宫缩计数留空');
    } catch (e) { $('hrvSelection').textContent = e.message; }
  }
  for (const field of [folder, single, evt]) field.onchange = () => changed(field);
  $('hrvForm').onsubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    lock(true);
    for (const field of [folder, single, evt]) field.disabled = true;
    $('hrvResult').classList.add('hide');
    try {
      const { data, marker } = selection();
      const parts = marker ? [...data, ['evt',marker]] : data;
      status('hrvStatus','正在本机读取 EKG、检查连续时段并重新检测心搏…');
      const result = await request('/api/hrv', {
        method: 'POST',
        headers: { 'X-HRV-Files': JSON.stringify(parts.map(([id,f]) => [id,f.size])) },
        body: new Blob(parts.map(([,f]) => f)),
      });
      const m = result.metrics;
      const metrics = [['整段质控', m.状态]];
      if ('RMSSD_ms' in m) {
        metrics.push(['RMSSD（ms）', m.RMSSD_ms ?? '未通过'], ['选中分段', single.files.length ? '当前文件' : m.分段],
          ['文件内时段（秒）', m.文件内起点秒 + '–' + m.文件内终点秒], ['候选心搏',m.候选搏数],
          ['双检测一致度',(100*m.双检测一致度).toFixed(2)+'%'], ['合格 RR 比例',(100*m.保留RR比例).toFixed(2)+'%'],
          ['有效相邻间期对数',m.有效相邻间期对数], ['宫缩标记数',m.宫缩标记数 ?? '未提供 evt']);
      }
      const card = render('hrvResult',result,metrics);
      table(card,result.parts,'各分段初筛');
      status('hrvStatus','已完成本次心电核验。');
    } catch (err) { status('hrvStatus',err.message,true); }
    finally {
      for (const field of [folder,single,evt]) field.disabled = false;
      lock(false);
    }
  };
})();
