// 镇痛前后对比：先分别分析A/B，再用共同通道比较。
// 阅读顺序见 docs/CODE_READING_GUIDE.md；使用原生JavaScript，无需前端框架。
async function compare(channel) {
  status('pairStatus', '正在重算共同通道特征并绘制对比图……');
  const r = await post('/api/compare', { ...activePair, channel });
  const card = render('pairResult', r, [
    ['共同通道', r.metrics.common_channels],
    ['每份有效时长 s', r.metrics.duration_seconds],
    ['统一采样率 Hz', r.metrics.sampling_rate],
    ['时域/时频通道', r.metrics.channel],
  ]);
  card.append(
    el(
      'p',
      `A：${r.label_a}；B：${r.label_b}。全部变化量为 B−A。相对变化分母为 |A|，接近零时留空。`,
      'muted',
    ),
  );
  card.append(
    el(
      'p',
      `原始质控保留：A ${r.quality_a.epoch_count}/${r.quality_a.epoch_total} 片段（${fmt(r.quality_a.keep_ratio * 100)}%）；B ${r.quality_b.epoch_count}/${r.quality_b.epoch_total} 片段（${fmt(r.quality_b.keep_ratio * 100)}%）。`,
      'muted',
    ),
  );
  const select = $('pairChannel');
  select.replaceChildren(
    ...r.labels.map((l) => {
      const o = el('option', l);
      o.value = l;
      return o;
    }),
  );
  select.value = r.metrics.channel;
  $('pairControls').classList.remove('hide');
  const filter = el('select');
  ['全部', '时域', '频域', '时频域', '网络', '汇总'].forEach((v) => {
    const o = el('option', v);
    o.value = v;
    filter.append(o);
  });
  const search = el('input');
  search.placeholder = '搜索特征名';
  search.setAttribute('aria-label', '搜索特征名');
  filter.setAttribute('aria-label', '筛选特征域');
  const row = el('div', undefined, 'row');
  row.append(filter, search);
  card.append(row);
  const holder = el('div');
  card.append(holder);
  function update() {
    const rows = r.rows.filter(
      (x) =>
        (filter.value === '全部' || x['特征域'] === filter.value) &&
        x['特征'].toLowerCase().includes(search.value.toLowerCase()),
    );
    holder.replaceChildren();
    table(holder, rows, '配对特征变化（' + rows.length + ' 项）');
  }
  filter.onchange = update;
  search.oninput = update;
  update();
}
function handlePairSubmit(e) {
  e.preventDefault();
  run('pairStatus', async () => {
    verifyPairFolders();
    const a = $('pairFileA').files[0],
      b = $('pairFileB').files[0];
    checkFormat(a, inputFormat('pair'));
    checkFormat(b, inputFormat('pair'));
    if ($('pairUseEvt').checked && !$('pairEvtA').files[0] && !$('pairEvtB').files[0])
      throw new Error('已勾选事件标记，请至少选择 A 或 B 的 evt.bdf。');
    activePair = null;
    $('pairControls').classList.add('hide');
    const opts = { ...settings('pair'), cache_for_comparison: true };
    status('pairStatus', '步骤 1/3：分析 A 记录……');
    const ra = await analyzeSingleFile(a, { ...opts, ...(await eventOptions('pair', 'A')) });
    status('pairStatus', '步骤 2/3：分析 B 记录……');
    const rb = await analyzeSingleFile(b, { ...opts, ...(await eventOptions('pair', 'B')) });
    activePair = {
      run_a: ra.run_id,
      run_b: rb.run_id,
      label_a: $('pairLabelA').value,
      label_b: $('pairLabelB').value,
      max_seconds: Number($('pairSeconds').value),
    };
    await compare(null);
  });
}
$('pairForm').onsubmit = handlePairSubmit;
$('pairRedraw').onclick = () => run('pairStatus', () => compare($('pairChannel').value));

const pairFolderRecords = {};
function verifyPairFolders() {
  const { A, B } = pairFolderRecords;
  if (!A || !B) return;
  if (A.key === B.key) throw new Error('两侧选中了同一记录文件夹。');
  // 本队列的目录名包含受试者编号和采集年月日时分秒。
  const pattern = /^(\d+)[^/]*?_(\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2})(?:_|$)/;
  const pre = A.key.split('/').pop().match(pattern), post = B.key.split('/').pop().match(pattern);
  if (!pre || !post) return;
  if (Number(pre[1]) !== Number(post[1])) throw new Error('两侧文件夹的受试者编号不同，请重新选择。');
  if (pre[2] >= post[2]) throw new Error('镇痛前记录应早于镇痛后，请核对两侧顺序。');
}

(() => {
  const sides = ['A','B'];
  const records = pairFolderRecords;
  function fill(input, file) {
    const transfer = new DataTransfer();
    if (file) transfer.items.add(file);
    input.files = transfer.files;
  }
  function clearResults() {
    $('pairControls').classList.add('hide');
    $('pairResult').classList.add('hide');
    $('contrastResult').classList.add('hide');
    activePair = null;
    status('contrastStatus','');
  }
  function clearSide(s, keepFolder = false) {
    records[s] = null;
    if (!keepFolder) $('pairFolder'+s).value = '';
    $('pairPart'+s).replaceChildren();
    $('pairPart'+s).hidden = true;
    $('pairSource'+s).textContent = '';
    $('pairEvt'+s).value = '';
    $('pairEvtOffset'+s).value = '0';
    $('pairConfirm').checked = false;
    clearResults();
  }
  for (const s of sides) {
    const folder = $('pairFolder'+s), selector = $('pairPart'+s), input = $('pairFile'+s);
    input.addEventListener('change', () => clearSide(s));
    selector.onchange = () => {
      fill(input,records[s].data[Number(selector.value)].file);
      clearResults();
    };
    folder.onchange = () => {
      const files = Array.from(folder.files);
      clearSide(s,true);
      input.value = '';
      try {
        const found = EEGFolderImport.indexFolderFiles(files,inputFormat('pair')).records;
        if (found.length !== 1) throw new Error('请选一次采集的记录文件夹，内含数字子目录及所选格式的数据。');
        const record = found[0];
        if (record.aux.evt.length > 1) throw new Error('找到多份事件文件，请核对后导入。');
        if (new Set(record.data.map(p=>p.number)).size !== record.data.length) throw new Error('分段编号重复，请核对记录文件夹。');
        records[s] = record;
        record.data.forEach((part,i) => selector.add(new Option('第 '+part.number+' 段 · '+part.file.name,i)));
        selector.hidden = record.data.length < 2;
        selector.onchange();
        const evt = record.aux.evt[0];
        if (evt) {
          $('pairUseEvt').checked = true;
          $('pairUseEvt').dispatchEvent(new Event('change'));
          fill($('pairEvt'+s),evt);
        }
        $('pairSource'+s).textContent = record.title+' · '+record.data.length+' 段 · '+(evt?'已匹配 evt.bdf':'缺少 evt.bdf');
      } catch (e) { $('pairSource'+s).textContent = e.message; }
    };
  }
  for (const radio of document.querySelectorAll('[name=pairFormat]'))
    radio.addEventListener('change',()=>sides.forEach(s=>clearSide(s)));
  $('pairForm').addEventListener('change',()=>{ $('contrastResult').classList.add('hide'); status('contrastStatus',''); });
  $('pairContrast').onclick = () => run('contrastStatus',async () => {
    clearResults();
    if (!$('pairConfirm').checked) throw new Error('请先确认同一受试者及镇痛前后顺序。');
    verifyPairFolders();
    const fmt = inputFormat('pair'), files = [], blobs = [], offset = {};
    function append(s,part,file) {
      check(file);
      files.push([s,part,file.size]);
      blobs.push(file);
    }
    for (const s of sides) {
      const parts = records[s]?.data || [{number:1,file:$('pairFile'+s).files[0]}];
      for (const part of parts) {
        checkFormat(part.file,fmt);
        append(s,part.number,part.file);
      }
      const evt = $('pairUseEvt').checked && $('pairEvt'+s).files[0];
      if (evt) append(s,'evt',evt);
      offset[s] = evt ? Number($('pairEvtOffset'+s).value) : 0;
    }
    if (!files.some(([,part])=>part==='evt')) throw new Error('请启用事件标记，并至少导入一侧配套的 evt.bdf。');
    const form = $('pairForm'), controls = Array.from(form.elements).filter(e=>!e.disabled);
    controls.forEach(e=>e.disabled=true);
    try {
      status('contrastStatus','正在逐段质控、匹配事件与背景并计算 α / δ……');
      const result = await request('/api/pair-contrast',{method:'POST',
        headers:{'X-Pair-Manifest':JSON.stringify({format:fmt,notch:$('pairNotch').value,offset,files})},
        body:new Blob(blobs)});
      const card = render('contrastResult',result,[]);
      table(card,result.rows,'事件窗减背景窗（相对功率差）');
      table(card,result.audit,'分段核对');
      return '计算完成，可在「下载结果」保存 CSV。';
    } finally { controls.forEach(e=>e.disabled=false); }
  });
})();
