# MinerU PDF 转 Markdown Skill

这是一个给 Windows 用户使用的 PDF 转换工具。你只要准备好 PDF，并告诉 Agent 结果要放在哪里，它就能帮你检查安装、下载需要的文件，再把 PDF 转成 Markdown。

最省事的用法，是直接对 Agent 说：

> 用 MinerU 把 `D:\资料\示例.pdf` 转成 Markdown，结果放到 `D:\资料\示例结果`。

第一次使用时需要联网安装。后面再转换其他 PDF，一般不需要重复安装。

## 转换后会得到什么

每次成功转换后，结果文件夹里只有两个文件：

```text
示例结果\
├── 示例.pdf
└── 示例.md
```

- `示例.pdf`：原 PDF 的一份备份，方便你随时对照原文。
- `示例.md`：转换后的文字、标题、表格、公式和图片。

图片会直接放进 Markdown 文件里，所以不会再多出一个图片文件夹。图片多的 PDF，生成的 `.md` 文件可能比较大，这是正常现象。

程序不会覆盖结果文件夹里原有的内容。如果指定的文件夹已经有文件，它会停下来，请你换一个新的空文件夹。

如果转换过程中原 PDF 被替换或改动，程序也会停下来，不会交付一份可能对不上原文的结果。

> MinerU 可以省下大量复制和排版时间，但它也可能认错文字、表格或公式。论文引用、实验数字和重要结论，请回到结果文件夹里的 PDF 再核对一次。

## 自动修正常见的字体乱码

很多论文 PDF 的字体把 `fi`、`fl`、`ff`、`ffi`、`ft` 这些连字做成单个字形。转换器如果无法把它还原成普通字母，就会输出看不见的乱码字符，例如 `scienti[乱码]c` 应该是 `scientific`、`A[乱码]er` 应该是 `After`。

这个 skill 会在写 Markdown 时自动修好这一类问题，包括：

- 私用区乱码字符（U+E000–U+F8FF）还原成对应字母组合；
- Unicode 连字（U+FB00–U+FB06）展开成普通字母；
- 连字丢字母的常见词（`suficient` → `sufficient`、`diferent` → `different`）；
- 不间断空格、被误用成度数符号的白圆点。

转换结束后会打印修好的处数，例如 `Text repairs (ligature/symbol artifacts): 335`。代码块里的内容不会被改动，图片数据也不会被触碰。无法确定的乱码字符会保留并统计，不会瞎猜。

> 修复依据上下文和一份常见词表判断，能覆盖绝大多数情况，但不能保证 100% 正确。关键数字和术语仍要回原文核对。

## 适合处理哪些 PDF

这个 skill 适合：

- 可以直接选中文字的普通 PDF；
- 扫描版 PDF 或拍照生成的 PDF；
- 带有标题、分栏、图片、表格或公式的论文和报告；
- 希望最后只保留“一份 PDF + 一份 Markdown”的整理流程。

如果 PDF 有打开密码，请先用你有权使用的密码解除保护，再进行转换。

## 安装这个 skill

这个仓库本身就是一个完整的 skill，不需要再去找里面的某个子文件夹。

1. 在 GitHub 页面点击 `Code`，再点击 `Download ZIP`。
2. 解压下载的文件。
3. 把整个文件夹放进你的 Agent 技能目录。

常见位置如下：

```text
%USERPROFILE%\.agents\skills\mineru-pdf-to-markdown\
%USERPROFILE%\.codex\skills\mineru-pdf-to-markdown\
%USERPROFILE%\.claude\skills\mineru-pdf-to-markdown\
```

`%USERPROFILE%` 代表你自己的 Windows 用户文件夹，例如 `C:\Users\小明`。

安装完成后，你可以重新打开 Agent，然后直接用前面的自然语言示例让它处理 PDF。

## 第一次准备 MinerU

如果你是让 Agent 操作，把下面这件事直接交给它即可：

> 请按这个 skill 的说明安装 MinerU，并下载转换 PDF 需要的文件。

如果你想自己操作，请打开 PowerShell，把下面整段命令复制进去：

```powershell
$env:PYTHONUTF8 = "1"
& ".\scripts\install_mineru.ps1" `
  -EnvPath "$env:USERPROFILE\mineru-env" `
  -DownloadModels
```

第一次安装前，请注意：

- 需要联网；
- 最好准备约 20 GB 可用空间；
- 下载时间取决于网速，模型文件可能比较大；
- MinerU 会装在单独的文件夹里，不会替换你平时使用的 Python。

如果默认下载线路不稳定，可以换一个来源：

```powershell
& ".\scripts\install_mineru.ps1" `
  -EnvPath "$env:USERPROFILE\mineru-env" `
  -ModelSource modelscope `
  -DownloadModels
```

如果电脑里已经装过旧版 MinerU，安装程序会提醒你。需要重装时，可以运行：

```powershell
& ".\scripts\install_mineru.ps1" `
  -EnvPath "$env:USERPROFILE\mineru-env" `
  -DownloadModels `
  -ForceReinstall
```

旧的 MinerU 文件夹会被改名保留，方便出问题时找回，不会直接删除。

## 转换一个 PDF

下面的例子把桌面上的 `sample.pdf` 转到一个新的结果文件夹。复制命令后，只需要修改双引号里的路径。

```powershell
$env:PYTHONUTF8 = "1"
& "$env:USERPROFILE\mineru-env\Scripts\python.exe" `
  ".\scripts\convert_pdf.py" `
  "$env:USERPROFILE\Desktop\sample.pdf" `
  "$env:USERPROFILE\Desktop\sample-result"
```

转换时通常每 15 秒会显示一次进度。页面很多、图片很多，或者电脑只使用处理器运行时，等待时间可能较长。

## 常用设置

大多数时候不需要加任何设置，让程序自动判断即可。只有结果不理想时，再尝试下面这些选项。

```powershell
& "$env:USERPROFILE\mineru-env\Scripts\python.exe" `
  ".\scripts\convert_pdf.py" `
  "D:\资料\示例.pdf" `
  "D:\资料\示例结果" `
  -Method auto `
  -Language ch `
  -StartPage 0 `
  -EndPage 9
```

- `-Method auto`：让 MinerU 自己判断怎样识别，推荐保持这个设置。
- `-Method txt`：适合能够直接选中文字的 PDF。
- `-Method ocr`：适合扫描件或照片 PDF。
- `-Language ch`：告诉 MinerU 文档主要是中文。也可以填写 `en` 等语言代号。
- `-StartPage` 和 `-EndPage`：只转换一部分页面。这里第一页记作 `0`，所以 `0` 到 `9` 表示前 10 页。
- `-DisableFormula`：不识别公式。
- `-DisableTable`：不识别表格。

最后两个选项一般不要使用，除非公式或表格识别明显拖慢了转换，或者造成了很多错误。

## 遇到问题怎么办

### 提示结果文件夹不是空的

换一个新的文件夹名称。程序这样做是为了避免覆盖你已有的文件。

### 提示 PDF 无效或打不开

先用常用的 PDF 阅读器打开它。如果阅读器也打不开，请重新下载或重新导出这份 PDF。仅仅把其他文件的后缀改成 `.pdf` 是没有用的。

### 提示 PDF 有密码

先用你有权使用的密码解除保护，再转换解除保护后的文件。

### 安装或下载中断

先检查网络和剩余磁盘空间，再重新运行安装命令。如果默认线路反复失败，可以使用前面带 `-ModelSource modelscope` 的命令。

### 提示 MinerU 版本不对

安装的 MinerU 版本由 `requirements.lock` 决定（当前 3.4.5），但转换器本身接受任何已安装版本，并在输出里报告实际使用的版本。只有环境损坏时才需要运行带 `-ForceReinstall` 的命令重装，旧文件夹仍会保留。

### Windows 弹出网络提示

MinerU 转换时会在这台电脑内部临时运行一个小服务，脚本只连接本机，不需要把它公开到互联网。

### 转换成功，但内容有错

先尝试切换 `-Method txt` 或 `-Method ocr`。如果问题只出现在表格、公式或少数页面，请对照 PDF 手动修正重要内容。

## 保存更详细的错误记录

平时出错时，程序只显示一段简短说明。需要请别人协助排查时，可以把更详细的记录保存到文件：

```powershell
& "$env:USERPROFILE\mineru-env\Scripts\python.exe" `
  ".\scripts\convert_pdf.py" `
  "D:\资料\示例.pdf" `
  "D:\资料\示例结果" `
  -DiagnosticLogPath "D:\资料\mineru-error.log"
```

这个记录里可能出现文件名、文件夹位置，以及 MinerU 返回的部分内容。发送给别人之前，请先看看里面有没有不方便公开的信息。

## 给维护者

普通用户不需要阅读这一节。

```text
mineru-pdf-to-markdown\
├── SKILL.md
├── README.md
├── LICENSE
├── references\
│   └── troubleshooting.md
├── scripts\
│   ├── install_mineru.ps1
│   ├── convert_pdf.py
│   ├── normalize_text.py
│   └── requirements.lock
└── tests\
    ├── test_convert_pdf.py
    └── test_normalize_text.py
```

- 当前安装版本：MinerU 3.4.5（转换器接受其他已安装版本并报告实际版本）。
- 支持 Python 3.10、3.11 和 3.12。
- `requirements.lock` 记录安装时使用的具体软件版本。
- `scripts/normalize_text.py` 负责连字/符号乱码修复，可单独 import 使用。
- 转换脚本只接受本机文件，不会替用户下载网络上的 PDF。

运行测试：

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s ".\tests" -v
```

测试会跳过依赖本机 MinerU 环境的用例，其余全部离线运行。

## License

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
