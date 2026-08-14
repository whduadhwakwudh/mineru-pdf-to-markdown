# MinerU PDF 转 Markdown Skill

这是一个面向 Windows 的 Agent Skill 仓库。**本仓库根目录就是 skill**（`SKILL.md` 就在仓库根），下载或克隆后无需理解任何嵌套目录，直接把它放进你的技能目录即可使用。用户只需要提供一个 PDF 和保存位置，Agent 可以根据 skill 完成环境检查、MinerU 安装、模型下载、转换、完整性校验和故障说明。

这里的“只使用这个 skill”不等于完全离线：首次使用仍依赖网络、PowerShell、Python/uv、PyPI、MinerU 模型仓库、磁盘空间和本机执行权限。本项目不打包 Python、MinerU 或模型。

## 仓库结构

```text
mineru-pdf-to-markdown/
├── SKILL.md                    ← 技能标志（skill 本体就是本仓库根）
├── scripts/
│   ├── Install-MinerU.ps1      ← 安装 + 可选模型下载
│   ├── Convert-PdfToMarkdown.ps1 ← 转换入口
│   └── convert_pdf.py          ← 转换核心（快照、流式内嵌、原子发布）
├── requirements.in / requirements.lock   ← 带 hashes 的依赖锁
├── agents/openai.yaml
├── tests/                      ← 38 个单元测试
├── README.md / LICENSE / .gitignore
```

## 安装为 skill（二选一）

**方式一：复制到本机技能目录**（对任意项目生效）

把整个仓库目录复制/移动到你的技能目录，使 `SKILL.md` 位于：

- Codex / DSH：`%USERPROFILE%\.agents\skills\mineru-pdf-to-markdown\SKILL.md`（或 `%USERPROFILE%\.codex\skills\`、`%USERPROFILE%\.dsh\skills\`）
- Claude Code：`%USERPROFILE%\.claude\skills\mineru-pdf-to-markdown\SKILL.md`

```powershell
# 以 Codex 用户级技能为例（在仓库解压目录的上一级执行；目标目录不存在则先创建）
Copy-Item -Path ".\mineru-pdf-to-markdown" -Destination "$env:USERPROFILE\.agents\skills\mineru-pdf-to-markdown" -Recurse
```

**方式二：从 GitHub 安装**：发布本仓库后，用 `skill-installer` 或其他技能安装器按仓库地址安装。

安装后，对 Agent 的示例请求：

- “用 MinerU 把这个扫描 PDF 转成 Markdown，结果只保留 PDF 和 Markdown。”
- “首次安装需要下载时先告诉我，再转换这篇含公式和表格的论文。”
- “Hugging Face 连不上，改用 ModelScope 下载 pipeline 模型。”

Agent 应先阅读 skill，再调用其中脚本；不应临时拼接另一套 MinerU 命令。

## 输出

成功后输出目录严格只有两个文件：

```text
论文-markdown\
├── 论文.pdf
└── 论文.md
```

PDF 是未经修改并经 SHA-256 校验的副本。转换开始时源 PDF 会被快照到临时目录，MinerU 只读取这个快照，最终 PDF 也只从同一快照发布；如果转换期间源 PDF 的字节发生变化，任务停止且不发布任何包。“源文件未变化”只按 SHA-256 判断，云盘刷新时间戳但内容未变时仍可成功。

MinerU 生成的本地图片会以 Base64 写入 Markdown，因此不留下 images、JSON 或日志目录；代价是图片多时 Markdown 会很大。转换器采用流式写入：逐行处理 Markdown、按 3 字节对齐的块编码图片，峰值内存不再随整个文档和全部图片线性累积，但单个超长 Markdown 行仍可能占用该行大小的内存。

“转换成功”只表示工具运行和文件契约成功，不保证 OCR、公式、表格或阅读顺序百分之百正确。引用事实前必须回查 PDF。

## 适用范围

- 扫描件、图片版 PDF；
- 学术论文、书籍章节和报告；
- 公式、表格、多栏或复杂版面；
- 中文、空格和长文件名；
- 明确要求只保留 PDF 与 Markdown 的任务。

当前只支持 Windows PowerShell 5.1/7，固定使用 MinerU 3.4.4（`pipeline` 后端，精确版本）。官方当前 Windows 支持 Python 3.10–3.12；安装器优先 3.11。CPU 可以运行，但可能很慢。

## 完全手动使用

以下示例假设仓库位于：

```text
C:\Users\你的用户名\Downloads\mineru-pdf-to-markdown
```

按 `Win + R`，输入 `powershell` 并回车。路径只用一层英文双引号。

### 1. 首次安装并下载 pipeline 模型

安装会连接 GitHub（下载固定版本 uv 资产并校验其 SHA-256）、PyPI，以及 Hugging Face 或 ModelScope。请预留约 20 GB 安全余量；实际占用随 MinerU、模型和缓存版本变化。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\你的用户名\Downloads\mineru-pdf-to-markdown\scripts\Install-MinerU.ps1" `
  -DownloadModels -ModelSource auto
```

中国大陆网络无法访问 Hugging Face 时，将最后一项改为：

```powershell
-DownloadModels -ModelSource modelscope
```

安装器会：

1. 检查 Windows、目标盘空间和 GPU；
2. 需要 uv 时，按 Windows 架构下载固定版本 uv 资产，先用仓库中记录的 SHA-256 校验，校验失败则不解压、不执行；
3. 复用 Python 3.10–3.12，优先 3.11；没有时用 uv 隔离安装 Python 3.11；
4. 在 `%USERPROFILE%\mineru-env` 创建环境，不替换系统 Python、不修改 PATH；
5. 只从带 hashes 的 `requirements.lock` 安装精确版本 `mineru[pipeline]==3.4.4`，启用 hash 强制校验；
6. 仅在指定 `-DownloadModels` 时下载 pipeline 模型。

需要重建环境时添加 `-ForceReinstall`。旧环境会移动成带时间戳的备份，不会直接删除。

### 2. 转换一个 PDF

输出目录必须不存在或完全为空：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\你的用户名\Downloads\mineru-pdf-to-markdown\scripts\Convert-PdfToMarkdown.ps1" `
  -PdfPath "C:\Users\你的用户名\Documents\论文.pdf" `
  -OutputDirectory "C:\Users\你的用户名\Documents\论文-markdown"
```

终端默认每 15 秒显示一次仍在运行的提示。不要重复打开转换。脚本默认不设置总超时，因为长扫描件可能耗时很久；确需限制时添加例如 `-TimeoutMinutes 120`。

自定义安装位置时，安装和转换都传同一个路径：

```powershell
-EnvironmentPath "D:\Tools\mineru-env"
```

### 3. 需要完整 MinerU 日志时

默认错误信息已脱敏，只含退出码和一条行动建议。需要完整日志排障时，显式传入本地文件路径（父目录必须已存在；已有文件不会被覆盖；日志可能含敏感信息）：

```powershell
... Convert-PdfToMarkdown.ps1 `
  -PdfPath "C:\Users\你的用户名\Documents\论文.pdf" `
  -OutputDirectory "C:\Users\你的用户名\Documents\论文-markdown" `
  -DiagnosticLogPath "C:\Users\你的用户名\Documents\mineru-debug.log"
```

## 常见失败

- **目标目录非空**：换一个新目录，不要为运行脚本而删除旧文件。
- **不是有效 PDF**：扩展名为 `.pdf` 仍不够，文件内部必须有 PDF 标记。
- **转换期间源 PDF 变化或不可读**：任务停止且不发布；确认输入文件后重试。
- **uv 资产哈希校验失败**：下载未通过校验，未解压也未执行；重试安装。
- **加密或需要密码**：使用有权解密的副本。
- **下载、SSL、代理失败**：检查网络；经确认后切换 ModelScope。
- **空间不足**：环境盘、模型缓存盘、输出盘都需要空间。
- **CUDA/显存不足**：关闭游戏、视频、绘图等 GPU 程序；pipeline 可退到 CPU。
- **本地防火墙提示**：MinerU 3.4 会启动临时 localhost API，不应开放公网访问。
- **Markdown 很大**：Base64 图片是维持“两文件输出”的代价；转换器已流式写入以降低峰值内存。
- **内容错漏**：这是机器提取结果，必须与 PDF 抽查，不能把不确定内容写成事实。

## 开发验证

运行测试：

```powershell
& "$env:USERPROFILE\mineru-env\Scripts\python.exe" -m unittest discover -s tests -v
```

中文 Windows 验证 Skill 结构时显式使用 UTF-8（在仓库根目录执行）：

```powershell
$env:PYTHONUTF8 = "1"
python "C:\Users\你的用户名\.codex\skills\.system\skill-creator\scripts\quick_validate.py" "."
Remove-Item Env:PYTHONUTF8
```

另需解析两个 PowerShell 脚本、执行真实一页 PDF 冒烟转换，并确认结果仅有 PDF 与 Markdown、PDF 哈希与转换快照一致、Markdown 非空。

升级 MinerU 属于显式维护动作：重新生成 `requirements.lock`（`uv pip compile --universal --generate-hashes`）、更新 `requirements.in` 的精确版本、运行全部测试和真实一页 PDF 冒烟转换，再同步更新本文档和 `SKILL.md` 中声明的版本。

## License

MIT
