# 梁平法图纸审查 Skill

`liang-pingfa-tuzhi-shencha` 是一个中文优先的 GitHub Copilot Agent Skill，用于审查梁平法截图、PDF 页面图像和导出的 CAD 图像。它把图像可读性、梁图拓扑/位置绑定、集中标注与原位标注作用域作为结论前提，帮助输出可追溯的表示审查发现。

## 用途与范围

适合以下任务：

- 阅读梁平法注写、梁编号、尺寸和可见配筋字段。
- 解释集中标注、原位标注及局部覆盖关系。
- 核查疑似不一致、遮挡、辅助文字污染、错绑或字段不可分类问题。
- 比较修改前后或左/右图纸版本的表示和可读性。
- 对已授权的本地 DWG 执行“先审计、后受控删除”的辅助覆盖文字工作流。

它只做图纸表示、可读性和可见关系审查。它不进行结构计算，不给出设计批准、施工指令、规范符合性、结构安全或工程验收结论。

Verified source scope: 22G101-1 printed pages 1-22 through 1-33. 本仓库只提供原创的程序性审查流程和页题追踪，不包含来源的正文、表格、图例、数值样例、扫描页或派生图像。

## 演示输入与输出

示例输入：

> 请检查这张梁平法截图。先确认文字和引出线是否可读，再说明左、右版本中辅助文字覆盖是否影响字段归属。不要推测模糊数字。

示例输出：

```text
输入可读性摘要
- 可读区域: 已确认的梁线、编号和局部标注。
- 不可读区域: 被覆盖文字及未显示的邻接区域。
- 已确认模式: 平面注写。
- 审查范围: 表示和可读性，不含结构计算。

发现清单
- 发现编号: F-01
  状态: 疑似不一致
  对象/位置: 左侧对应梁的被覆盖局部
  字段: 字段分类与位置绑定
  可见证据: 辅助文字覆盖梁几何和常规注写。
  推理: 覆盖使字段无法可靠绑定到具体梁和位置。
  来源页题: 平面与截面表达、集中与原位作用域
  不可读部分: 覆盖下的字符和引出线。
  下一步: 提供无遮挡的同一区域图像后重新审查。
```

## 安装

本 Skill 没有通用的 `install-skill` 命令。请先克隆或下载本仓库，再按需要复制整个 Skill 目录。

### 项目级安装

复制 `liang-pingfa-tuzhi-shencha` 目录到项目中的：

```text
.github\skills\liang-pingfa-tuzhi-shencha
```

最终文件必须为：

```text
.github\skills\liang-pingfa-tuzhi-shencha\SKILL.md
```

### Windows 用户级安装

复制同一目录到：

```text
%USERPROFILE%\.copilot\skills\liang-pingfa-tuzhi-shencha
```

最终文件必须为：

```text
%USERPROFILE%\.copilot\skills\liang-pingfa-tuzhi-shencha\SKILL.md
```

安装后用自然语言提出任务即可，不需要专用安装或调用命令。

## 触发示例

- “阅读这张梁平法图中的集中标注和原位标注。”
- “核查这段梁注写是否有疑似不一致。”
- “集中标注和原位标注在密集区域重叠；请先逐簇确认边界，不要把可读片段拼接。”
- “比较修改前后两张导出的 CAD 图像，说明覆盖文字是否已清除。”
- “这张 PDF 页面图像中的 G/N 侧面筋字段能否可靠读取？”

## 使用限制

- 先检查图像可读性。数字、符号、括号、分隔符、G/N/g 或引出线不清时，结论必须是 `证据不足`。
- 先完成梁、跨、支座、悬挑或局部的拓扑/位置绑定，再解析数字。
- 位置明确的原位标注只覆盖对应局部的同一字段，不自动构成与集中标注的冲突。
- 两个或以上候选出现实际墨迹/矢量、字段边界或引出线重叠时，先逐簇完成重叠门；文字接近不能合并候选，颜色、图层和线型只能作候选提示，不能证明语义或归属。
- 重叠簇的任何字形、分隔符、字段边界、排次、引出线端点或所有权未解时，每个受影响候选均为 `证据不足`；部分 OCR 不得通过可读性门，且禁止按最近距离绑定梁、支座或跨。未解决重叠区域不得报告 `一致`。
- 仅当每个候选独立可读、边界/归属/绑定明确，且仍可见表达式冲突时，才可报告 `疑似不一致`。补图应保留全部候选行列、字段边界、完整引出线端点、梁线、附近支座和跨上下文。
- 原始 DWG 的读取、转换、字体和图层可见性受环境影响。无法可靠读取时，应请求截图、PDF 页面图像或导出的 CAD 图像。
- 复杂节点、特殊梁、加腋、并筋、井字关系和截面混合表示需要完整上下文；高风险歧义应由结构专业人员复核。

详细操作见[Skill 工作流](.github/skills/liang-pingfa-tuzhi-shencha/SKILL.md)及其同目录参考资料。

## DWG 两阶段命令行工作流

本仓库还提供一个本地 DWG 审计工具包。它不是自然语言到 CAD 的编辑器，而是严格的两阶段流水线：

1. `audit` 只读审计源 DWG，在私有暂存目录中转换为 DXF，并创建绑定源文件、工具版本和完整实体指纹的本地审计工件。
2. `plan` 只能从审计工件确定性生成计划；没有可操作的 `疑似不一致` 时失败关闭。
3. `apply` 重新审计同一源 DWG，确认计划，再只删除精确审计过的直接 Modelspace `TEXT` 辅助覆盖文字。
4. 修改后的临时 DXF 必须转为 DWG、再转回 DXF、重新审计，并证明除计划目标外的实体、图层、布局、边界与对应右侧区域保持不变。

唯一允许的操作是 `delete_auxiliary_overlay_text`。它只适用于大小写不敏感的 `TEMP` 或 `textarea` 图层中、通过完整面板和干扰证据门的精确 `TEXT` 实体。它不会删除图层、替换文字、移动坐标或修改几何、尺寸、配筋、数量、块、属性、标注、填充或多段线。

有关强制顺序和安全边界，请阅读[两阶段 DWG 工作流](.github/skills/liang-pingfa-tuzhi-shencha/references/dwg-two-stage-workflow.md)。

### 已验证的公开支持边界

第二阶段确实存在并可用，但只面向已由第一阶段审计准入的初始
`R2018/AC1032 DXF-exposable` profile；它不是“可修改每一份 DWG”的承诺。`audit` 必须总在
`plan` 或 `apply` 之前运行。遇到不受支持的图纸，审计会在计划或执行前失败关闭，绝不通过
转换、剥离或忽略数据来继续。

初始 profile 会拒绝（包括但不限于）代理或自定义实体/对象、代理图形、非空或不受支持的
`ACDSDATA`、未建模的原始标签/节、`SORTENTSTABLE` 或其他不受支持的绘制顺序/对象元数据、
以及所需 object enablers 缺失的图纸。命令仍只输出如 `UNSAFE_ENTITY_TYPE` 的稳定脱敏错误
代码，不暴露图纸内容、路径或元数据。下一步是保留只读审计结论，而不是绕过门；需要保留这些
代理/自定义状态的使用者应转向具备相应能力的原生环境。

ODA File Converter 只是文件转换，不是原生数据库编辑，因而不能让每一种 DWG 都符合本
profile。若图纸必须保留代理/自定义状态，需要适当许可的 ODA Drawings SDK、Autodesk
RealDWG/AutoCAD 环境以及相关 object enablers；本项目不捆绑这些 SDK，也不推断使用者拥有
它们的访问权或许可。私有资格夹具不会发布；运行时生成的真实 ODA 测试仅证明这个受支持
profile 的能力，绝不证明通用 DWG 兼容性。

### 文档保全与受控易变项

每次快照都会绑定实体清单、图层可见状态和标志、表/样式、完整支持的 `OBJECTS` 节，以及除明确易变项外的 `HEADER` 变量和自定义属性。因而 `$LTSCALE`、坐标/UCS、单位、版本和其他表示设置都不能在往返后静默变化；`GROUP` 的句柄、所有者、内容、XDATA、appdata 和扩展字典引用也被绑定。未知或未建模的 `OBJECTS` 类型/元数据会失败关闭，绝不会被跳过。

唯一排除的 `HEADER` 字段是 `$ACADMAINTVER`、`$HANDSEED`、`$TDCREATE`、`$TDUPDATE`、`$TDUUPDATE`、`$TDINDWG`、`$TDUSRTIMER`、`$VERSIONGUID`、`$FINGERPRINTGUID` 和 `$LASTSAVEDBY`。它们分别是 ODA/ezdxf 可重建的维护版本标识、工具管理的句柄分配、创建/保存/编辑计时或保存者/版本标识；不改变图纸表示或内容。`$ACADMAINTVER` 例外仅允许其值变化，仍绑定变量身份、位置、组码序列和重数，并由测试覆盖。`OBJECTS` 中仅排除 ezdxf 每次写入都会重写的 `EZDXF_META/WRITTEN_BY_EZDXF` 单个值标签；该对象的句柄、所有者、字典关系和其他标签仍被绑定。此列表是固定且经过测试的窄允许列表，不包括任何单位、坐标、显示、图层或内容设置。

在任何 ezdxf 读取或规范化之前，私有暂存的 ASCII DXF 都会先经原始标签预检。首个版本只接受 R2018 所需的 `HEADER`、`CLASSES`、`TABLES`、`BLOCKS`、`ENTITIES`、`OBJECTS` 节；`ACDSDATA` 如存在只能是无记录、无元数据的规范空节。`THUMBNAILIMAGE`、任何未知节、非空或非规范 `ACDSDATA`、以及无法与规范化模型一一对应的原始 `CLASS` 记录都会失败关闭，不会被静默删除或部分保留。

### 前提条件

- Python 3.11 或更高版本；公开 CI 使用 Python 3.13。
- 安装本项目会固定安装 `ezdxf==1.4.4` 和 `jsonschema==4.23.0`。
- 使用者自行安装并许可 ODA File Converter `27.1.0`。本项目不捆绑、下载或接受该工具的许可。
- 私有暂存要求本地 NTFS、正常的无重解析祖先、当前受信 Windows 会话可验证的限制性
  DACL，以及 `SYSTEM` 所需访问权。每次方向转换创建两个独立 CSPRNG 命名的私有根；
  根、输入和输出目录均由无跟随祖先/目录租约保护。DACL 应用或回读、NTFS 语义、目录
  空性、租约或 ODA 前提任一不可用时，公开第二阶段都会失败关闭。
- ODA 输出目录启动时必须为空；每次只向 ODA 暴露一个随机源文件名、精确文件过滤器、
  `recursion=0` 和 `audit=1`。运行前的直接输入清单会在运行后重验。成功运行后输出目录
  必须恰有一个新的、预期名称的普通非重解析文件，不能有同名旧文件、多个结果或 sidecar。
  系统随即以禁止写入/删除的无跟随句柄打开该候选，绑定文件身份、字节数、SHA-256 与格式
  头后才会解析或采用它；不会预创建 ODA 输出、要求 ODA 保留文件身份，或使用 ADS 标记。
   这种后打开采用只适用于转换器私有候选；面向用户的 DWG 和 JSON/Markdown 工件仍通过保留
   句柄、同卷临时副本和禁止替换的最终发布语义创建，绝不覆盖已有路径。
- 每个方向必须独立执行两次；DXF 结果要求原始节结构/建模记录摘要和完整规范快照一致，
  DWG 结果还会逐个反向走同一双重 DWG→DXF 路径。支持的头、图层、布局、块、实体、表、
  对象、类、顺序、边界与建模元数据都必须一致。生成的 ODA 27.1.0 探针表明字节序列含
  易变序列化数据，故不以字节相等代替比较；仅允许经过测试的固定宽度 CLASS 书写、
  ezdxf 已知默认化和 ODA 管理的 TABLESTYLE/重组辅助句柄等窄易变项。
- **这个公开 profile 不需要 AutoCAD；**但需要保留代理/自定义状态的图纸不在该承诺内，
  应使用具备相关 object enablers 的适当许可原生 SDK/环境。

所有图纸处理均在本地完成；工具不会上传图纸或连接网络服务。ODA 只处理私有暂存副本，绝不直接处理源目录或最终输出路径。

### 受信本地会话威胁边界

本实现采用 Oracle 接受的有界前提：**trusted Windows account/session, ODA executable,
OS, and local NTFS volume; no hostile same-account/admin process**（受信 Windows 帐户/会话、
ODA 可执行文件、操作系统和本地 NTFS 卷；不存在恶意的同帐户或管理员进程）。

这些控制防止意外并发、陈旧文件和普通路径竞态，**不**防御恶意软件、被攻陷主机或恶意的
同帐户/管理员进程。两次 ODA 执行不是两个独立实现；双重比较只发现不一致，完整重新审计
和 `before - planned targets` 状态证明才是第二阶段的状态证明。运行时生成的 ODA 夹具仅是
一个狭窄支持配置的资格验证，不是一般 DWG 认证。LibreDWG 不是运行时回退，也不被要求或
捆绑；项目不添加其 GPL 工件。

审计工件的自完整性 SHA-256 只用于检测意外损坏；它不能认证恶意同帐户编辑者重新签名的工件。
任何外部提供、手工编辑或不受信任的 audit/v2 在依赖结论前，必须针对其绑定的源文件和 profile 重新运行全新的 `audit --topology-profile`。
`validate_artifact` 只验证工件模式、规范自完整性和内部关联；没有源文件时，它不证明几何事实。
第二阶段仍会重新审计基础源文件并忽略 topology 作为授权依据。

### 安装与环境检查

```powershell
python -m pip install .
python -m liang_pingfa_review doctor
```

`doctor` 会在不输出本地路径或身份细节的情况下报告 ODA 版本、Windows 就绪状态、本地
NTFS 私有暂存能力、DACL 回读状态，以及该公开 profile 的环境就绪状态。它始终把
`per_file_compatibility` 报为 `audit_required`：`doctor` 不会也不能判断某一份 DWG 已获支持，
只有 `audit` 能作出该文件的兼容性结论。
若 ODA 不在常规位置，可通过 `--oda-file-converter` 显式选择；也可设置本地 `ODA_FILE_CONVERTER` 环境变量。发现多个未显式选择的转换器时会失败关闭。

### 标准流程

所有输入和最终输出均为 DWG；DXF 仅是内部暂存格式，公共命令不接受 DXF 修改输入。
示例中的 `.\output` 必须由使用者预先创建为普通本地目录；工具不会创建
用户指定的输出父目录，也会拒绝符号链接、junction、UNC、设备路径和其他
重解析路径。相对输出和工件路径只按启动时的当前工作目录做词法锚定，随后
逐级以不跟随重解析点的句柄验证。

```powershell
python -m liang_pingfa_review audit `
  --input .\input.dwg `
  --audit-out .\output\audit.json `
  --report-out .\output\audit.md

python -m liang_pingfa_review plan `
  --audit .\output\audit.json `
  --plan-out .\output\edit-plan.json `
  --review-out .\output\plan-review.md

python -m liang_pingfa_review review-plan `
  --audit .\output\audit.json `
  --plan .\output\edit-plan.json
```

### 可选梁图拓扑审计（只读）

若本机拥有明确的图层角色约定，可只为 `audit` 额外提供严格本地 JSON profile：

```powershell
python -m liang_pingfa_review audit `
  --input .\input.dwg `
  --audit-out .\output\audit-v2.json `
  --report-out .\output\audit-v2.md `
  --topology-profile .\beam-topology-profile.json
```

profile 固定包含互斥的 `beam_edges`、`beam_ids`、`column_supports`、`wall_supports`、`generic_supports`、`support_upper_annotations`、`span_lower_annotations` 和 `leaders` 图层数组；名称按 NFC/casefold 比较，不能使用 `TEMP`/`textarea`，也不能配置容差、正则、实体类型、回退、修改或代码。该选项生成 `liang-pingfa/audit/v2`，以固定 `beam-plan-in-situ/v1` 对直接可见、不透明、共面的 Modelspace 几何建立方向无关的梁轴、显式矩形支座、跨和原位注写位置证据。

它不按最近对象绑定，不把梁或次梁交点合成为支座；未互配的受控梁边保留为私有阻断几何，触及标注、引出线或目标走廊时只能输出 `证据不足`。所有精确关系共享固定预算并先经有界空间/区间索引筛选；耗尽时以 `TOPOLOGY_LIMIT_EXCEEDED` 失败关闭。v2 不公开原文、坐标、图层、颜色、路径、原始散列或 token 单独散列；token 只在内存中比较，trace 最多表达布尔的相等性门结果。所有 topology finding 都是 `actionability: false` 和 `target_id: null`，trace 永不进入 `audited_targets`。`plan`、`apply`、`verify` 没有 topology option，第二阶段仍只删除既有、精确审计过的辅助覆盖 `TEXT`；从 audit/v2 重新审计时只比较 audit/v1 基础/覆盖状态。详见[可选梁图拓扑审计参考](.github/skills/liang-pingfa-tuzhi-shencha/references/beam-topology-audit.md)。

计划文件中的精确 `plan_id` 必须显式确认。`apply` 需要一个不同于源文件且尚不存在的新输出路径；没有 `--force`，也不会原地写入或替换任何已有文件。

```powershell
python -m liang_pingfa_review apply `
  --input .\input.dwg `
  --audit .\output\audit.json `
  --plan .\output\edit-plan.json `
  --confirm-plan "<plan-id>" `
  --output .\output\corrected.dwg

python -m liang_pingfa_review verify `
  --input .\output\corrected.dwg `
  --audit .\output\audit.json `
  --plan .\output\edit-plan.json `
  --verification-out .\output\verification.json
```

`apply --dry-run` 仍执行完整的重新验证、临时删除、DWG 往返和重新审计，但不会发布 DWG、审计或验证工件。

本项目的受支持执行和 CI 平台是 Windows。真实 DWG 的 `apply` 和 `verify` 依赖保留的 Windows 文件句柄：发布临时文件从复制/`fsync` 一直持有到不替换最终重命名，验证输出在读取租约期间禁止写入和删除。其他平台未测试且不受支持。

`verification.json` 是有时间界限的证据，不是敌对替换防护或编辑授权。其 `output_binding`
被工件完整性散列覆盖，绑定经验证输出的 SHA-256、字节数、已解析路径散列、文件身份指纹、
DWG 头签名/版本和验证时间。任何使用者都必须针对**当前** DWG 重新计算这些字段；DWG 被
替换、重写或移动到不同已解析路径后，旧验证 JSON 不能证明新文件，更不能授权后续编辑。

### 本地授权回归

真实 DWG 回归是显式选择的本地操作，来源只通过环境变量提供，运行结束后会清理暂存目录：

```powershell
$env:LIANG_PINGFA_LOCAL_DWG = "<authorized-dwg>"
python -m liang_pingfa_review local-regression `
  --source-env LIANG_PINGFA_LOCAL_DWG `
  --work-root $env:TEMP
```

不要把真实输入、输出、DXF、截图、审计、计划、验证报告、路径、散列或转换日志提交到仓库。

## 来源与版权

本仓库不附带任何外部标准、图纸、PDF、DWG、DXF、渲染图或 OCR 输出。外部技术来源及用户图纸的权利仍归各自权利人所有；使用者应确保自己拥有或获得使用输入材料的合法权限。

本仓库的原创文本和脚本使用 [MIT License](LICENSE)。请勿提交来源材料、派生图像、私有路径、散列、OCR 转储或本地夹具。

## 本地验证

安装后运行以下公开验证。测试在运行时生成原创合成 DXF，并以模拟 ODA 执行覆盖转换命令；CI 从不安装或运行 ODA，也不使用真实 DWG。

```powershell
python scripts\validate_skill.py
python scripts\validate_skill.py --tracked
python -m unittest discover -s tests -p "test_*.py" -v
python -m compileall -q src tests scripts
dotnet build native-bridge-contracts\LiangPingfa.NativeBridge.Contracts.csproj -c Release --nologo
dotnet run --project native-bridge-contracts\tests\LiangPingfa.NativeBridge.Contracts.ApiSurface.Tests.csproj -c Release --nologo
dotnet build native-cad\LiangPingfa.NativeCad.sln -c Release --nologo
dotnet run --project native-cad\tests\LiangPingfa.NativeCad.Core.Tests -c Release --no-build
python -m liang_pingfa_review --help
```

回归夹具只可本地保存，说明见[本地夹具说明](tests/local-fixtures/README.md)。

已安装 ODA 的 Windows 用户可显式运行仅含运行时原创 R2018 数据的资格测试；它不会读取本仓库
外的 DWG，也不会在 CI 自动运行：

```powershell
$env:LIANG_PINGFA_RUN_GENERATED_ODA = "1"
python -m unittest tests.test_oda_cli.GeneratedOdaIntegrationTests -v
Remove-Item Env:\LIANG_PINGFA_RUN_GENERATED_ODA
```

该生成式真实 ODA 工作流只证明 `R2018/AC1032 DXF-exposable` 初始 profile 的能力，并不证明
每一份 DWG 都可转换、审计或修改。

## 可选原生 Bridge 通道

除现有 ODA 流程外，仓库提供一个**完全独立、默认只读**的可选原生 CAD Bridge
通道。它是项目侧协议/客户端/编排交付物，不是厂商插件实现，也不改变
`doctor`、`audit`、`plan`、`review-plan`、`apply`、`verify` 或
`local-regression` 的 ODA 行为。

### 原生契约版本政策

已发布的 `native-*/v1` schema、`ProtocolV1.cs` 和 `Interfaces.cs` 是冻结的历史
读取表面，按 `c374e6c` 基线逐字节/签名校验。v1 只能校验、读取、报告或执行明确的
迁移检查；它**不能**执行 native plan、apply、Core Console write 或 published-output
verify。任何此类执行门都会以
`NATIVE_LEGACY_ARTIFACT_READ_ONLY` 失败，而不会把旧字段重新解释成新授权。

当前活动配置、私有 session、geometry、audit、intent、plan、manifest、Core Console
result/export 和 verification 全部使用明确的 `liang-pingfa/native-*/v2`。v2 绑定
同启动 monotonic session、稳定宿主、私有枚举基数、1,024 operation 上限、精确
prewrite binding、最终输出 constraints 与实际 readback binding；plan/manifest/result/
export/verification 还携带精确的 v2 schema/integrity 交叉绑定。`native-bridge/v1`
request/response 保持不变，因为其 wire shape 没有不兼容变化；这不是对持久 v1
artifact 的“全面兼容”声明。

逐工件的首次发布 commit 与 SHA-256 冻结图见
`tests/contracts/native-v1-baseline.json`；`tests/fixtures/native-v1/` 中的
source-free historical vectors 必须始终通过 v1 reader。
`native-cad` 在该发布基线后才进入工作树，因此其 executable core 只公开/使用 v2
types，不伪造另一套已发布 v1 API。

仅无状态的 v1 adapter config 可由显式工具确定性迁移到 v2。v1 session、audit、
intent、plan、manifest、result、export 和 verification 缺少必要安全绑定，绝不补造；
必须重新 prepare session 并对源文件进行 fresh native audit。

### SDK-free 可执行核心检查点

仓库现有 `native-cad` 的 SDK-free C# 协议/事务核心：它只对运行时生成的内存
CAD 快照执行固定 manifest 的 `translate_dbtext`、`delete_auxiliary_overlay_text`
和受 policy 门控的 `create_review_marker`，并在单次 commit 前做精确
before/allowed-delta/after 读回验证。它还包含原创的、**仅语法**
`Autodesk.*` API stub，以便下一检查点编写窄适配器边界。

这只是内存模型、协议与回滚语义的可执行证明；不加载厂商 SDK、没有 AutoCAD
命令、不能部署，且**不证明**真实 AutoCAD、RealDWG、TSSD、ODA 或任何宿主
事务运行时兼容性。实际 AutoCAD adapter 和命令映射仍在下一检查点，当前不得将
stub 或核心测试称为真实宿主能力。

操作人员必须显式选择外部适配器、PID 和其公布的本地随机命名管道；工具绝不
枚举第一个进程、扫描 PATH/注册表、选择窗口或在 ODA/原生之间自动回退。协议
仅允许固定的只读 health/session/document/inventory/精确几何导出请求，绑定
进程创建时间、Windows 会话、服务器 PID、nonce/challenge、文档、插件、协议
和能力。未知方法、额外帧、文档漂移或会话过期都会失败关闭。
在尚无持久 descriptor 的 prepare 阶段，独立 pre-handshake client 只能按顺序执行
`health` 后 `get_session`；它不接受占位会话，也不能提前执行 document、inventory
或 geometry RPC。两项响应及本地进程绑定通过后才构造并完整语义验证一次使用的
私有 session descriptor。
`native-session prepare` 一开始即捕获 UTC 创建时间以及同一 Windows 启动周期内的
`GetTickCount64` 签发 tick、精确五分钟 deadline 和私有 boot/domain ID。descriptor
完整性覆盖 `monotonic_clock`、`monotonic_boot_id`、`monotonic_issued` 和
`monotonic_expires`；它们只存在于私有 descriptor，绝不进入 wire response、报告或
CLI 事件。health、session 和后续 RPC 都取配置方法超时、严格 UTC 过期和这个原始
uptime deadline 的最小值。重启/domain 不同、当前 uptime 小于签发值或达到 deadline
都会失败关闭；延迟握手、落盘或另一进程消费都不会重新开始五分钟窗口。
每份活动 v2 精确几何还必须与**同一**会话的 ID、完整进程/宿主、适配器/插件版本和指纹、
完全相等的能力集合、保存源（含头、散列、大小、路径/文件身份）、数据库实例及
revision 相符，并携带会话/文档 binding digest；只比较源字节绝不充分。v1 的原始读取上限保持冻结；
活动 v2 的保守上限为 2,000 个实体和总计 10,000 条线段，原始 geometry JSON 最多 16 MiB
**UTF-8 字节**（不是字符/代码点数；schema `maxLength` 仅为次要约束），外层
几何响应最多 32 MiB；inventory 是独立的双摘要响应，原始 JSON 最多 64 KiB。
仅在已定义的精确 schema 路径（bridge `result.geometry_json` /
`result.inventory_json`、manifest `preconditions_geometry_json`、Core Console
export `geometry_json`）中，序列化 JSON 字符串才是**外层 opaque carrier**：
先按收到的精确 codepoint/UTF-8 bytes 绑定和散列并检查字节上限，绝不对整个
carrier 做 NFC。随后独立解析内部 JSON；内部键和值仍执行深度、重复键、有限数、
单标量 NFC 上限、schema、语义和同一绝对 deadline 校验。历史 v1 工件只按其冻结 schema 验证；不会被静默迁移、版本重命名或重新解释为 v2。

原生计划只允许经审计的 DBTEXT XY 平移、精确辅助覆盖文字删除，以及默认关闭且
需配置/能力/既有图层样式共同准入的 review marker。没有自由命令、LISP、脚本
正文、层批量删除、图层/样式创建、块/尺寸/配筋编辑或 GUI 自动化。

原生会话描述符、机器可读 `native-audit`/intent/`native-plan`、manifest、Core
Console result/export、`native-verification` 和任何恢复日志均为私有文件：最终文件
必须保留仅当前用户和 `SYSTEM` 的受保护 DACL，且读取这些输入时会重新验证该 DACL。
这些工件会枚举 opaque 记录或操作，因此本地数量可见，并以
`record_cardinality: explicit_private` 如实声明；其中部分工件可包含原始私有字段；其分类与公开报告不同，见下文。`native-audit` 和 `native-plan` 以成对事务发布：JSON 保持私有，
相应的红删 Markdown 才继承明确的公共父目录读取策略。Markdown 报告、CLI 摘要和
稳定错误事件不披露数量。marker policy 的 version、
profile/enable/capability、layer/style token 与 fingerprint、height/rotation、文本派生
版本和几何默认值从 audit 到 plan/manifest 必须完全相等，任何后续漂移均失败关闭。

写入始终从保留的源文件句柄复制到私有 NTFS/DACL 工作区，这是严格的 copy-only
边界。外部 Core Console
只能处理该副本，并只接收固定 `NETLOAD` 与固定项目命令；manifest 经私有环境
变量传递，绝不出现在脚本中。保存后必须启动新的读回进程，逐项比较
`before → manifest 允许差异 → after`；仅在实体、顺序、表、布局、块、文档和
受保护状态均无未计划变化时，才无替换发布公共 DWG 和私有证据型 verification。
配置组件及其全部词法祖先在使用期间均须通过限制性 DACL、无跟随租约和重复
身份/散列检查；会话描述符先原子 claim 再使用。新 apply 会话可以更换 PID，
但必须证明相同的稳定宿主/插件/能力/profile 绑定和新鲜文档 revision。Windows
Core Console 必须在启动前进入 kill-on-close Job Object，超时或异常只终止并等待
整个进程树；不会采用直接进程 kill 的成功回退。

外部插件的事务/回滚字段是未经项目内部证明的 conformance claim；项目只能验证
其结果的精确读回状态。原生 verification 是有时限的证据，绝不授权后续编辑。
外部宿主许可、对象启用器和专有组件由操作人员负责；仓库不捆绑、下载或分发。
公共 CI 只运行生成 mock，不声称已在本机验证任何真实外部集成。详见
[可选原生 CAD Bridge](.github/skills/liang-pingfa-tuzhi-shencha/references/native-cad-bridge.md)。

## 贡献规则

- 保持 `.github\skills\liang-pingfa-tuzhi-shencha\SKILL.md` 为唯一规范入口，并让目录名与 front matter 的 `name` 一致。
- 保持 Skill 简洁，将扩展程序说明放入同目录 `references`。
- 保持固定运行时依赖、离线处理和 ODA 外部安装边界；不添加网络调用、转换器二进制或来源材料。
- 不提交 PDF、DWG、DXF、图像、OCR 转储、来源内容、派生材料或本地夹具。
- 对非平凡改动补充标准库单元测试，并运行全部本地验证命令。

## Private artifact privacy

PRIVATE-ARTIFACT-PRIVACY: sensitive local raw artifacts; retained no-follow
handle owner/DACL validation is required; never commit or upload.
Administrators is accepted only when it is the current process token's default
file owner, which is the supported elevated-token private-file creation case.
Session descriptors contain pipe, nonce/challenge, and process/document bindings.
Exact geometry and console exports contain raw text, coordinates, layers, paths,
handles, and geometry. Manifests contain raw preconditions/geometry and plugin,
Core Console, and source-copy bindings. Intent can contain requested deltas and
marker geometry. Console results/logs are sensitive and bounded. Native
audit/plan/verification/recovery JSON is private redacted-or-opaque machine data
with explicit local cardinality where applicable, not a public redaction claim.
Only public Markdown reports, CLI error events, and CI logs are redacted and
cardinality-independent; they contain no raw private fields.

## 许可证

见 [LICENSE](LICENSE)。
