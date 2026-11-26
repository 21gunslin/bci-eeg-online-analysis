# 本机脑电研究工作台

在本机浏览器检视 BDF 波形、标记坏道和坏段，提取 EEG 特征，比较镇痛前后宫缩相关脑电变化，也可从 BDF 中的 EKG 通道提取五分钟心率变异性（HRV）。Python 服务仅监听 `127.0.0.1:8765`。命令行另支持 EDF / GDF。

## 安装和启动

Windows 推荐使用 Python 3.12。在仓库根目录打开 PowerShell，首次安装执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

双击 [启动脑电特征网页.bat](scripts/启动脑电特征网页.bat)，服务就绪后打开 <http://127.0.0.1:8765/>。看到“拒绝连接”时，先运行启动脚本，再访问 <http://127.0.0.1:8765/health> 检查服务状态。需要每次登录后自动启动，可运行 [安装脑电网页登录自启.bat](scripts/安装脑电网页登录自启.bat)。手动退出使用 [停止脑电特征网页.bat](scripts/停止脑电特征网页.bat)。[启动、日志与故障恢复](docs/WEBSITE_STABILITY.md)

其他系统在已安装依赖的 Python 环境中运行 `python backend/eeg_web_server.py`，保持终端打开，再访问同一地址。网页运行只需要 Python 和浏览器。

## 三个操作示例

### 镇痛前后宫缩相关 EEG 对比

1. 打开 **镇痛前后 EEG 对比**，选择 BDF 或 FIF。把同一人的镇痛前记录放入 A，镇痛后记录放入 B，两侧使用相同格式。启用事件标记并核对时间偏移，勾选受试者及镇痛前后顺序确认项。至少一侧需要配套的 `evt.bdf`；要计算“后减前”，两侧都需要各自的事件文件和合格配对。
2. 点击 **计算并导出 α / δ 对比数据**。对于本研究确认的宫缩标记 `KeyIn:253`，程序按原始时间定位，在标记前后 10 秒范围内选取完整的 2 秒 EEG 窗。每个事件窗在同一文件内匹配一个背景窗，背景窗与任一标记至少相隔 20 秒。
3. 查看结果表中的 **镇痛前、镇痛后、后减前**。前两列分别是该时相“宫缩附近相对功率减去背景相对功率”，单位为比例。例如 α 差值 `0.06` 表示宫缩附近比背景高 6 个百分点。至少需要 3 个共同电极，每个时相至少需要 10 对合格窗才报告该时相数值。遇到空值，先查看导出的分段核对 CSV。

### 从镇痛前 BDF 提取心电 HRV

打开 **心电 HRV**，选择一次镇痛前记录的文件夹（数字子目录中有 `data.bdf`），或直接选择单个 BDF。当前支持名为 `EKG`、原始采样率为 1000 Hz 的通道。`evt.bdf` 可选，提供后可统计选中时段内的宫缩标记数。

程序寻找同一文件内连续合格的 30 个 10 秒窗，对最早符合条件的五分钟重新检测心搏。整段通过质控后报告 **RMSSD（毫秒）**。可下载心电提取结果和分段检查 CSV；找到候选五分钟时，还会生成逐搏复核表和心电图。若缺少连续五分钟或整段复核未通过，页面会显示原因，RMSSD 留空。

### 复核并保存坏道、坏段

在原始 BDF 质控页面，点击**黄色巡检按钮**定位候选波形，默认检查平线。点击**绿色按钮**把建议填入可编辑草稿。仅当一个通道全程每个扫描窗均为平线，才建议整道排除；局部平线以及手动勾选的饱和、持续阶跃只填入对应坏段。工频尖峰可在 PSD 图上人工查看。

核对或修改草稿后保存 FIF。后续选择该 FIF 分析时，程序按标记排除整道，或跳过与坏段重叠的 2 秒窗。当前坏段标记作用于同一时间的全部通道。[巡检规则](docs/PATROL_FLAT_FIRST.md) · [FIF 导入导出](docs/FIF_IMPORT_EXPORT.md)

## 想看懂代码，先从这里开始

打开[代码阅读流程 JPG](docs/code_reading_flow.jpg)，再读[从零读懂代码](docs/CODE_READING_GUIDE.md)。主线是：HTML按钮 → 单份JavaScript入口 → HTTP服务 → 分析总流程 → 读取 → 预处理 → 特征 → 图表与网页。

`eeg_feature_engine.py`保留主流程和兼容导入；参数、读取、预处理、公式和报告分别位于`eeg_models.py`、`eeg_readers.py`、`eeg_preprocessing.py`、`eeg_features.py`、`eeg_reporting.py`。常用MNE参数集中在`eeg_mne_ops.py`。前端也按单份、配对、多份、事件、解释和原始检视拆分，使用原生JavaScript。

标准读取、IIR滤波、Welch PSD、逐窗功率和FIF优先使用MNE公开接口；NumPy/SciPy保留明确的论文公式。原始BDF保真兼容层单独保留，不隐式上采样或改写Status。Black和Prettier只用于开发排版，网站运行不需要安装npm依赖。

## 功能

- 辅助人工巡检：全记录候选概览、一键定位、持续性问题合并、每页4通道与自动翻页；明确登记复核后记录准确浏览覆盖，候选及进度独立于BAD/FIF。

- 只读自适应检视：新BDF可只载入查看，旧结果可直接恢复；纵轴逐通道/同单位统一/手动标尺切换，横轴缩放、拖动和整段概览，支持单通道放大及仅导出当前视图，不改写BDF/FIF。

- 原始BDF质控与FIF：独立、不滤波/重采样/重参考/删道删段；原始波形检视、全通道全频Welch谱、手工坏道/坏段、枕区睁闭眼α峰与质控CSV，双精度FIF携带模板坐标及标记并逐块回读核对。详见[使用及保留口径](docs/RAW_BDF_QC.md)。

- Montage模板映射：RecordInfo预览和分析结果展示原始/标准电极名、通道类型与head坐标，提供CSV和模板点位图；不确定通道不强配，详见[装配说明](docs/MONTAGE_MAPPING.md)。

- 文件夹导入：选择某次姓名编号记录文件夹，或选择上层总目录并按编号/姓名/时间查找一次记录；自动获取数字子文件夹里的data、匹配evt与两份JSON，仍可手动增删与替换。

- 可选“论文特征方案”：每时相287列，配对前/后/差值861列；时频先汇总频段功率再计算标准差/CV，提供字典、缺失原因和固定列宽表。详见[计算口径](docs/THESIS_FEATURE_PROFILE.md)。

- 单份 EEG：0.5–45 Hz 四阶 Butterworth 双向零相位滤波，片段质控，时域、频域、时频和 PLV 网络特征。
- 镇痛前后对比：共同保留通道与相同有效时长，统一图表尺度，特征差值与 CSV 下载。
- spike 独立入口：单通道 1 Hz 设备计数；可添加同一记录段原始 EEG，复核候选通道、对应头皮区域及后续慢波。
- 多份入口可选导入datainfo.json生成计划及份数，并用RecordInfo.json按名称核对通道类型；保留时间差提示与安全白名单。
- 多份 data 时间顺序队列：2–30份、同一evt、间隙保留、独立连续段计算和加权特征汇总。
- 原三个入口均可选上传 evt.bdf，查看原始事件标签、按文件头对齐的时间轴及片段内外位置。
- 图表可点击放大，并支持新窗口查看和下载。

完整使用方法见 [使用说明](docs/USER_GUIDE.md)。

## 数据与方法说明

此仓库只包含程序与使用说明，不包含产妇原始记录、特征表、随访资料、报告、日志或分析缓存。运行时输出保存在本机 `results/eeg_web_output/`，已列入 Git 忽略规则。

`spike.bdf` 支持的是标签为 spike 的单通道 1 Hz 计数结构。该文件本身不能定位出现通道、头皮区域或慢波；需要配套原始 EEG。自动波形检测是未经专家标签验证的启发式候选筛查。对应头皮区域不是脑源定位，结果不能用于诊断癫痫或产后抑郁。

常规镇痛前后配对图的横轴拼接了通过质控的片段。核对宫缩时点请使用事件时间轴；上面的 α / δ 专项计算按原始记录时钟匹配事件和背景。镇痛前后顺序须结合采集记录确认，差值用于探索性比较。

## 当前验证

本次网站更新通过 96 项 Python 测试和 36 项 JavaScript 测试，涵盖合成 BDF/FIF、特征计算与巡检交互。另用合成 BDF 验证了 `/api/hrv` 和 `/api/pair-contrast` 能返回可下载的结果文件。上述检查验证程序行为，临床预测性能需另行评估。

## 目录与维护

| 目录 | 用途 |
|---|---|
| `backend/` | 文件读取、预处理、特征计算、配对/spike 分析与 HTTP API |
| `web/` | HTML、CSS 和 JavaScript 页面 |
| `scripts/` | Windows 后台服务启动、健康检查及停止脚本 |
| `docs/` | 使用方法、架构与方法边界 |
| `tests/` | 不依赖真实患者资料的合成数据验证 |

开发验证可在项目根目录运行以下命令。完整 Python 测试需要批处理建模依赖，版本见 [requirements-batch.txt](requirements-batch.txt)。JavaScript 测试使用 Node.js。

```sh
python -m unittest discover -s tests -v
python -m compileall -q backend
node --check web/eeg_app.js
npm run test:web
```

JavaScript 测试与语法检查使用 Node.js。

贡献与分支流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)。


## MNE 对照审查（0.2.0）

可选安装 `python -m pip install -r requirements-mne.txt` 后执行：

```sh
python tools/mne_parity_review.py
```

可用 `--real-data-root` 指定本机 EEG 数据目录；输出默认保存在已忽略的 `results/mne_review/`。工具不输出患者姓名、文件路径或文件头身份信息。完整审查范围、已修正问题及剩余限制见 [MNE_REVIEW.md](docs/MNE_REVIEW.md)。`tools/` 保存独立对照验证工具。

对照通过不等于临床有效性验证。未知参考、设备单位误标、BAD 注释与拼接边界仍需结合采集资料处理。


## BDF/FIF互斥导入与同目录保存

单份、多份及镇痛前后入口可单选BDF或FIF，禁止混用。原始质控可从本机选择BDF，将主EEG组保存为同目录`fdata.fif`，已有导出先备份。FIF单份分析另有原始坏道、坏段、眼状态/EOG复核图。详见[FIF导入导出说明](docs/FIF_IMPORT_EXPORT.md)。

## 批量转换、特征与随机森林

新增独立批处理入口`scripts/run_batch_fif_rf.ps1`：逐份保真转换BDF到同目录FIF，提取论文特征，再汇总成每位产妇一行，采用固定参数随机森林和按产妇独立的重复5折交叉验证计算OOF AUC。转换、质控和建模排除分别留痕，不覆盖未知的已有FIF。

入门请看[从BDF到AUC的代码阅读指南](docs/BATCH_RANDOM_FOREST_GUIDE.md)，先读`tools/train_random_forest.py`的`main()`。可复现依赖单列于`requirements-batch.txt`；患者特征、预测、模型及报告只保存在本机忽略目录。内部评价不等于临床有效性。

另附[批处理代码流程JPG](docs/batch_random_forest_flow.jpg)。新增7项合成数据测试覆盖保真转换、通道名称对齐、病例排除和训练/测试泄漏；`tools/validate_batch_results.py`复核实际结果中的标签对应、分折与指标。
