# CloverBoard

一个轻量、本地优先（local-first）的实验追踪工具。每个 Run 都有自己的目录
和 `run.sqlite`；文本保存在 SQLite 中，图片、音频和代码 ZIP 是 Run 目录下的
普通文件。工作区观看状态和命名 View 保存在 `.workspace/workspace.sqlite`。

> 本文是根目录 [README.md](README.md) 的中文版。命令、路径和 API 名称保持原样；
> 如有歧义，以英文版为准。

## 快速开始

需要 Python 3.11+。构建好的 wheel 已包含 WebUI，不需要 Node.js：

```sh
python -m pip install ./dist/cloverboard-0.1.0-py3-none-any.whl
cloverboard serve --logdir ./runs --port 6006
```

从当前仓库进行开发时，还需要 Node.js 20.19+ 或 22.12+：

```sh
python -m pip install -e ".[dev]"
npm ci --prefix web
npm run build --prefix web
cloverboard serve --logdir ./runs --port 6006
```

UI 和只读 MCP Streamable HTTP 端点（`/mcp`）会一起启动。
可复现数据集和三来源预览见 [scripts/README.md](scripts/README.md)。

安装 `dev` extra 后运行 `python -m build` 可以构建 wheel 和源码包。构建过程使用
`npm ci`，并基于锁定依赖构建 WebUI。两种产物都输出到 `dist/`；从源码构建需要
Node.js，安装 wheel 则不需要。本地日志、预览数据和导入的 TensorBoard 数据不会
包含在构建产物中。

## TensorBoard 标量导入

在当前仓库安装可选集成，然后在两个终端分别运行导入器和服务：

```sh
python -m pip install -e ".[tensorboard]"
cloverboard import-tensorboard ./tb_logs --output ./clover_runs --follow --interval 5
cloverboard serve --logdir ./clover_runs
```

省略 `--follow` 表示只导入一次。源 event 文件只读；每个直接 Run 子目录都会保留
自己的名字作为输出中的 Run 名。源根目录下的 event 文件使用根目录名。嵌套 Run
和名称冲突会被拒绝。输出是标准 CloverBoard logdir，不会额外多出一层 `runs/`；
源目录和输出目录不能重叠。只导入标量。

轮询会检查文件大小、修改时间和文件身份，然后通过保留的游标读取追加记录。重复的
metric/Step 记录按文件名顺序、再按文件内事件顺序决定最终值，重启后也是如此。
Follow 模式会等待被截断的尾部；完整但非法的事件记录会让导入器报错停止。
重启会重新读取当前所有源文件，并把标量 upsert 到已有 Run 数据库；不会重建或清空
该数据库。相同的 metric/Step 值会更新，新点会插入，源中已不存在的点会保留。
Run 名称、开始时间、config、环境、Tags、Notes、媒体/文本和 Web 工作区状态不会被
修改。完整细节和边界见
[导入指南](scripts/README.md#import-local-tensorboard-logs)。

## 记录

```python
import cloverboard as cb

cb.init(exp_name="unet-lr-test", config={"lr": 1e-5}, tags=["ablation", "dataset-v2"])
cb.log({"loss": 0.12, "score": 0.91}, step=100, group="validation")
cb.add_tags(["reviewed"])
cb.remove_tags(["reviewed"])
cb.log_image("validation/prediction", image, step=100)
cb.log_audio("validation/generated", waveform, step=100, sample_rate=24000)
cb.log_text("debug/output", "A plain text record.\nLine breaks are preserved.", step=100)
```

要续写一个稳定的实验目录，传入它在 logdir 下的直接子目录名：

```python
cb.init(logdir="./clover_runs", run_dir="experiment_a", config={"lr": 1e-5})
```

不存在的 Run 数据库会被初始化；已存在的会被重新打开用于追加。原始显示名称、开始
时间和环境保持不变。显式 config 键会合并，提供的 Tags 会添加，重复的
metric/Step 值会覆盖。不传 `run_dir` 时，每次 `init()` 都会分配一个新 Run。
训练器负责恢复 checkpoint 并提供 Step。要接管一个已导入的实验，请先从 SDK
写入同一 Run 前停止导入器。导入期间仍可编辑 Tags、Notes 和 Views。

Tags 是区分大小写的单层字符串。每个 Run 最多 64 个 Tag，每个 Tag 长度为
1–80 个字符。SDK 和 UI 都使用增量增删操作，因此并发更新不会影响无关标签。
Tags 随 Run 数据库一起保存。

`log_text(name, text, step)` 只接受纯字符串，最多 256 KiB UTF-8。相同的 name/Step
会覆盖之前的正文和时间戳。空字符串、Unicode、空白和换行都会原样保留；
HTML/Markdown 会按字面显示。Text 复用现有后台写入器，不创建媒体文件。

`init(workspace_root=...)` 可选地把**该目录内部**的绝对路径，在 config、argv
（包括 `--option=/path`）和快照元信息中记录为相对路径。根目录本身记为 `.`；
目录外路径和已经是相对路径的字符串保持不变。基准在 init 时固定，即使之后 cwd
发生变化。默认值 `None` 保持原有记录方式。这不会改变实际文件访问、用户传入的
config 对象、文本正文、caption、Notes 或归档代码，并且不会把工作区绝对根路径
写入元信息。

```python
cb.init(logdir="runs", workspace_root="/home/alice/project",
        config={"data": "/home/alice/project/data"})  # 将 data 记录为 "data"
```

Audio 接受 `[-1, 1]` 范围内的 float NumPy 数组、int16 数组或 PCM16 WAV 路径。
数组形状为 `[samples]` 或 `[samples, channels]`，并且必须提供整数采样率；支持
单声道/立体声，每条记录解码后的 PCM 最大 32 MiB。WAV 路径使用文件头中的采样率；
显式传入的采样率必须一致。不自动归一化、不重采样，也不依赖 FFmpeg。WAV 编码在
有界后台队列中执行；队列过载时会跳过记录并发出警告。正常关闭会排空队列；强制
杀死进程可能丢失待写记录。

## 浏览

- 通过 Run 的 `⋯` → **Edit tags**，或在 Details → Overview 中编辑。批量增删使用
  **Edit tags for selection**。Peer 和分享模式下的 Tags 只读；附加的本地来源目录
  仍可编辑。
- 搜索旁的 tag 过滤器只隐藏列表行，不改变选择。Dynamic View Rules 支持精确的 tag
  成员条件（any/all/none）以及 tagged/untagged。
- Table → **Table settings** 控制指标小数位（0–8，默认 4）和可选的 Tags 列。
  已记录的指标和差值使用该格式；已登记参数保持原有表示。较小的非零值和大值使用
  科学计数法。排序和计算使用原始值。
- **Media** 包含 Images、Audio 和 Text。三者都使用显式 Step、Follow latest 和
  采样时间线；Images/Audio 还支持分页。固定 Step 使用请求 Step 当天或之前最近的
  一条记录。Audio 按需加载，并支持通过本地和已认证 Peer 文件路由进行 seek。
  自动刷新会保留当前播放。
- Text 每个 run/name/Step 显示一个字符串，带简短初始预览、全文展开和复制。
  名称/Step 选择按每种媒体类型独立保存。`/api/text-names`、`/api/text-timeline`
  和 `/api/text` 提供目录、采样 Step 和所选字符串；缺失文本返回 null。
- 观看偏好、过滤和格式化保存在工作区状态、命名 View 和共享状态链接中。

## 通过 HTTP 使用 MCP

将支持 Streamable HTTP 的 MCP 客户端指向：

```text
http://127.0.0.1:6006/mcp
```

不需要第二个进程，也不需要 MCP 专用启动命令。使用 `--share` 时，在
`Authorization: Bearer …` 头中填入与 Web 服务相同的访问 token。不要把凭据放进
MCP URL。分享和只读模式允许 MCP 查询 POST，但仍继续拒绝 REST 写入。该端点只
暴露只读工具；不能执行 Python/SQL、启动训练或修改实验。

| 工具 | 用途 |
| --- | --- |
| `list_runs` | 按名称/ID/来源/config/tag 过滤；默认返回最近 10 个 Run，每页最多 50 个；支持按时间/名称排序。 |
| `get_runs` | 最多 20 个 Run；默认返回元信息和 config 键。config 字段、Notes、环境、快照摘要和完整 Tags 需要显式选择。 |
| `list_metrics` | 合并后的指标路径和记录该指标的 Run ID；支持子串过滤和分页；不返回点或统计。 |
| `compare_configs` | 默认返回 config 差异；支持转义点号字段过滤、分页和逐 Run 错误。 |
| `compare_metrics` | 原始最新值，或共同 Step 当天/之前的值；可选 baseline/targets 和历史统计；行分页。 |
| `get_series` | 默认 200 个原始采样；支持 Step 范围、数量和采样元信息；时间戳可选。 |
| `plot_metric` | 一个指标最多 8 个 Run，返回内联 PNG 和精简元信息；可选尾部/Step 范围和 EMA。 |
| `list_image_names` | 单个 Run 的纯文本图像类别；计数和首末 Step；支持过滤和分页，默认 20 个。 |
| `list_images` | 最新或指定 Step 当天/之前的纯文本 ID/尺寸；默认 6 个，可选有界 caption。 |
| `get_images` | 以 1–4 个指定 image ID 读取内联 PNG；分辨率有界，可选按原图像素裁剪。 |

`list_runs.query` 接受带类型的参数和 tag 过滤。例如：

```json
{"rules": [{"field": "tags", "operator": "has_all", "value": ["ablation"]}]}
```

`get_runs.config_fields` 选择转义点号的叶子路径或前缀，例如
`["optimizer.lr", "model"]`；这些字段会以扁平 map 返回。`include=["config"]`
请求嵌套 config，Notes 使用 `notes_offset`/`notes_limit`。被省略的大字段会列出
名称，便于针对性重试。Snapshot 返回数量/状态/大小。

`compare_metrics` 返回实际采样 Step；只有提供 baseline 或 targets 时才出现差值。
历史统计通过 `include_stats=["extrema", "first_hit"]` 显式启用：extrema 包含原始
Step，两项统计都覆盖完整原始历史，与所选 Step 无关。失败按行返回。
`compare_configs.values` 与 `run_ids` 对齐；`missing_indices` 区分字段缺失和 null，
`omitted_indices` 标识超出输出预算的值。

JSON 输出使用类型化模型和 24,000 字符的紧凑 JSON 上限。大型参数/环境值共享
12,000 字符预算。按需使用过滤、更小的页或更少的样本。`get_series` 为满足上限不会
删除内部 gap marker：过多 gap 会产生带 `truncated` 和 `next_step` 的前缀以便继续。
配置的来源仍保持一跳。工具不继承 Web 的选择/设置；Notes 和 Tags 是数据，不是指令。

`plot_metric` 返回原生 MCP 图片内容（`image/png`，1000 × 480），
`structuredContent` 中带精简元信息和一个文本块；不返回点/刻度数组。例如：

```json
{"run_ids": ["run_20261025_001", "run_20261025_002"], "metric": "metrics/BSDS100/psnr", "range_mode": "last_20"}
```

使用 `list_runs` 返回的真实 ID。`range_mode` 为 `full`（默认）、`last_50` 或
`last_20`，按请求 Run 记录 Step 范围的并集计算。也可以传入包含端点的
`step_min`/`step_max`；预设和显式边界不能同时使用。默认使用原始值；`smoothing`
（0–0.999）在裁剪和采样前复用 Web 的完整历史 EMA。`max_points` 默认每条曲线
800，最高 2000，允许少量额外极值。过多 gap 会产生逐 Run 错误，而不是静默省略
曲线的一部分。

绘图使用线性坐标、白底和绘图区外右侧带序号的图例。完整显示标签（最多 200
字符）、实际图例文本、颜色、数量和逐 Run 错误随图片返回。空曲线标记为
`[no data]`。相同比较集合的颜色稳定，并复用 Web 基础色板，不依赖工作区颜色
槽位。图片使用已有 Pillow 在内存中渲染，不写文件。字体在本地探测；服务器没有
合适字体时，可设置 `CLOVERBOARD_PLOT_FONT` 指向可读的 TTF/TTC，中文标签尤其
需要。不下载字体。JSON 上限只作用于元信息，不作用于图片字节。

训练图片使用 `list_image_names` → `list_images` → `get_images`。
发现过程从不打开图片文件。`list_image_names` 接收单个 `run_id`、可选的不区分
大小写的 `name` 子串和 `limit`/`offset`（默认 20，最多 50）。`list_images`
需要 `run_id` 和 `name`；返回实际 Step、该 Step 的总数、`next_offset` 和图片
ID/尺寸。省略 `step` 表示最新，或请求不晚于它的最大已记录 Step。实时 Run 翻页时
应固定返回的 Step。每页默认 6 张，最多 24 张。`include=["caption"]` 最多增加
200 个 caption 字符和截断标记。Offset 是页位置，不是跨 Run 的样本身份。

`get_images` 需要显式 ID；只有该工具读取训练图片像素：

```json
{"items": [{"run_id": "run_20261025_001", "image_id": "<image_id from list_images>", "crop": [120, 80, 256, 256]}], "max_side": 512}
```

可选 crop 是 EXIF 方向归一化后原图像素坐标的 `[x, y, width, height]`。必须完全
位于图片内，并在缩小前应用。`max_side` 默认 512（128–1024）；保持宽高比，小图
不会放大。最多四个 item 可引用不同 Run 或同一图片的不同裁剪。输出是原生 PNG
ImageContent 加精简元信息：item 保持请求顺序，成功项的 `image_index` 从 1 开始
编号返回图片。包含原始/返回尺寸和请求的裁剪。逐项错误会保留其他图片；全部失败时
不返回像素且 `isError=true`。结果中不重复路径、URL 和 caption。

Web 和 MCP 共享 64 MiB 本地预览缓存，按文件版本、大小和裁剪区域区分。Peer 读取
会获取所选原图以保持裁剪精度，沿用正常来源鉴权、单文件 64 MiB 传输上限和现有
1600 万像素图片限制。解码/编码有两个共享工作槽位。所有内容都留在内存中；不引入
新的图片依赖、HTTP 端点、拼图或自动样本匹配。

HTTP 协议测试覆盖初始化、工具发现/调用、校验、鉴权和只读行为；无需注册真实
Agent。

## 检查

```sh
python -m pytest -q
npm test --prefix web
npm run build --prefix web
```
