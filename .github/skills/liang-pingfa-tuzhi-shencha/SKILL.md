---
name: liang-pingfa-tuzhi-shencha
description: 审查梁平法截图、PDF 页面图像或导出的 CAD 图像的表示可读性、空间归属和集中/原位注写关系；当需要阅读梁平法注写、解释集中标注与原位标注、核查疑似不一致或比较前后示例时使用。
license: MIT
compatibility: GitHub Copilot
---

# 梁平法图纸审查

## 使命与结论边界

本 Skill 只审查可见图纸表示是否可读、能否与梁及局部位置可靠绑定，以及集中标注与原位标注的作用域是否被正确处理。它不进行结构计算，不判断设计正确性、规范符合性、结构安全或施工可行性，也不提供施工指令或设计批准。

默认原则是：先证据，后语义；先位置，后数字；看不清就说看不清。除非输入中可见且可读，不得补全任何数字、直径、根数、间距、长度、钢筋等级或构造含义。

## 适用场景与触发条件

在用户提出下列任一任务时使用本 Skill：

- 阅读梁平法注写、梁编号、截面或配筋字段。
- 解释集中标注与原位标注，以及某个局部是否覆盖通用值。
- 核查截图中的疑似不一致、遮挡、错绑、作用域误用或无法分类的文字。
- 比较修改前后、左/右或两个版本的表示和可审查性。
- 审查用户提供的截图、PDF 页面图像或导出的 CAD 图像。

原始 DWG 的读取、转换、字体还原和图层可见性是环境相关能力。仅在当前环境能可靠打开并保留可视上下文时才使用它；否则请求 PDF 页面图像、截图或导出的 CAD 图像，不能把无法读取的 DWG 内容当作证据。

## 资源加载

开始审查前，按任务读取以下同目录资源：

1. [工作流与输出协议](references/workflow-output.md) - 执行证据门、状态判定和固定输出。
2. [注写字段与绑定规则](references/notation-fields.md) - 按字段族解析可见标注。
3. [来源范围与页题追踪](references/source-scope.md) - 只用页面主题追溯审查依据。
4. [本地表示回归协议](references/local-regression.md) - 仅在本地夹具回归时使用。
5. [两阶段 DWG 审计与受控修改](references/dwg-two-stage-workflow.md) - 任何 DWG 修改请求的强制审计优先流程。
6. [多标注簇重叠门](references/multi-annotation-overlap.md) - 当密集或疑似重叠注写可见时必须读取，并在语义解析前执行。
7. [可选梁图拓扑与原位注写审计](references/beam-topology-audit.md) - 使用本地 DWG topology profile 时的固定只读证据门。
8. [可选原生 CAD Bridge](references/native-cad-bridge.md) - 只有用户明确选择外部原生适配器、PID 和本地命名管道时读取；不替代 ODA 流程。

不要复述、重建或索取来源文件中的表格、图例、数值样例或版式。输出中的来源只能写页题主题，不能伪造条文、页内坐标或不可见原文。

## 输入与前置条件

接受下列输入：用户上传截图、PDF 的页面图像、导出的 CAD 图像，以及在环境可用时的原始 DWG。每项输入先记录可见范围、比例、旋转、裁切、遮挡和字体异常。

在任何语义分析前完成图像可读性检查：

- 梁线、支座或柱墙/轴网、梁编号和完整标注簇是否同时可见。
- 数字、直径符号、间距符号、括号、分隔符、G/N/g 前缀、上下排关系和引出线是否能可靠区分。
- 图面是平面注写、截面注写、局部放大图还是混合图。
- 是否存在辅助文字、临时标记、覆盖层、裁切、失真或字体替换。

任一关键条件不满足时，停止对应字段的语义判断并使用 `证据不足`。OCR、文字识别或相邻文字距离只能作为线索，不能替代人工可读性和空间归属确认。

当可见或怀疑存在密集、相互压盖的集中标注或原位标注时，必须先读取[多标注簇重叠门](references/multi-annotation-overlap.md)。该重叠簇门在 P1 之前：先以实际墨迹/矢量交叉、掩膜或边界/引出线重叠证据划分候选，再处理字符或字段；单纯文字接近不能合并簇或证明重叠。颜色、图层和线型只能提示不同候选，不能证明集中/原位语义或归属。

## 审查工作流

1. **重叠簇门（P1 前）**：密集或疑似重叠时，按多标注簇重叠门保留集中和原位候选的独立作用域；任一簇的字形、分隔符、括号、字段边界、排次、引出线端点或归属未解，逐簇输出 `证据不足`，不得让部分 OCR 通过 P1。
2. **P1 可读性门**：仅对已分离的候选确认输入可读，标出每个不可读字符、遮挡区和缺失上下文。
3. **P2 拓扑/位置绑定门**：先识别梁中心线、支座、跨、悬挑端、编号候选和引出线，再把标注绑定到 `梁编号 + 构件位置 + 字段`。重叠簇的 P1 未通过时禁止 P2；浮在相邻梁之间的文字不得仅按最近距离绑定。
4. **P3 表达模式门**：区分平面注写、截面注写、局部放大和混合图。截面注写必须经剖面号或清晰关联进入截面分支，不能当作集中标注。
5. **P4 作用域门**：集中标注是同编号梁的通用字段候选；原位标注必须绑定到具体跨、支座、悬挑或局部。对同一字段，位置明确的原位标注在该局部覆盖集中标注，不应机械报告为冲突。
6. **P5 字段门**：仅解析可分类且可读的梁编号/类型、跨数和悬挑、尺寸、箍筋、上部纵筋、下部纵筋、G/N 侧面筋、梁顶高差、加腋及局部项、附加箍筋/吊筋、特殊梁和截面注写。
7. **P6 交叉核查门**：用可见拓扑检查编号、跨数、悬挑和标注位置是否相容；用字段作用域检查覆盖关系。无法确认位置时不得宣称矛盾。
8. **P7 比较门**：比较前后或左右版本时，逐项说明同一对象、同一位置、同一字段的可见变化。对辅助叠加文字导致字段无法分类或遮挡归属的情形，可判为 `疑似不一致`，但不得推断其工程数值含义。

加腋、特殊梁、并筋、复杂节点、井字关系、密集局部和截面混合表示必须进入专用上下文审查。缺少完整图形关系时，输出 `证据不足` 并要求补充完整视图、剖面号或由结构专业人员复核。

## 集中标注、原位标注与局部覆盖

- **集中标注**：记录为同编号梁的通用字段候选，保留原始文字、位置和适用前提。它不自动覆盖图中所有同类文字。
- **原位标注**：记录为特定梁的特定跨、支座、悬挑端或局部字段。先确认其引出线、端部关系或局部位置，再解析数值。
- **局部覆盖**：当同一字段的原位标注与集中标注不同，且位置绑定明确时，报告“原位覆盖集中”的局部关系。位置不明确、字符不清或模式不明时报告 `证据不足`，不报告冲突。

重叠候选绝不拼接字符串或合并字段。只有所有候选各自可读、边界与归属及位置绑定清楚，且仍可见表达式冲突时，才可报告 `疑似不一致`。

字段细节、符号保留方式和特殊项升级条件见[注写字段与绑定规则](references/notation-fields.md)。

## 固定输出格式

先给出输入可读性摘要和审查范围，再为每一项发现使用以下固定字段，不省略字段：

```text
发现编号:
状态: 一致 | 疑似不一致 | 证据不足
对象/位置:
字段:
可见证据:
推理:
来源页题:
不可读部分:
下一步:
```

状态含义固定如下：

- **一致**：字段可读、对象和位置绑定明确、作用域已处理，且未见与对应页题主题不相容的表示问题。此状态仅限已审查的表示和可读性范围。
- **疑似不一致**：存在可见的表示冲突、无支持的辅助叠加、错误作用域使用，或编号与可见拓扑不相容。它不是安全、强度或合规结论。
- **证据不足**：存在裁切、模糊、遮挡、字体问题、图层不可见、模式不明、位置不明或特殊项上下文不足，无法形成可靠判断。

固定输出不得把未读出的字符写成推测值；不得把“未发现”写成“全部正确”；不得省略不可读部分和下一步。

重叠区域还必须给出区域摘要：可见区域、候选簇数量/标识、重叠证据、不可读字形/字段边界/排次关系、已阻断的绑定、逐簇发现和所需的下一张图像或上下文。存在未解决重叠时，该区域摘要不得为 `一致`。

## 安全边界与升级

- 不得臆造不可读数字、符号、位置、图层语义或标准要求。
- 不做结构计算，不替代设计复核，不给出设计批准、施工指令或安全认证。
- 不把表示审查结论宣称为规范符合性或工程验收结论。
- 对高风险歧义、承载相关疑问、复杂节点、特殊梁或完整上下文缺失，建议由具备资质的结构专业人员复核。
- 原始 DWG 无法可靠处理时，明确说明环境限制并请求可读图像，不使用猜测替代证据。

本地夹具的左/右表示回归只检验表示与可读性，不检验配筋强度、锚固、构造安全或施工可行性。执行方式见[本地表示回归协议](references/local-regression.md)。

## 两阶段 DWG 工作流

当用户要求删除、清理或修正 DWG 中的辅助覆盖文字时，绝不从请求直接进入修改。必须先读取[两阶段 DWG 审计与受控修改](references/dwg-two-stage-workflow.md)，并严格执行：

1. **第一阶段只读审计**：创建本地审计工件，建立源文件、工具链和实体清单绑定；保持 `一致`、`疑似不一致`、`证据不足` 三种固定状态。
2. **计划阶段**：只能从未过期且完整的审计生成确定性计划。只有具备面板、右侧缺失、唯一指纹和可见干扰证据的 `疑似不一致` 直接 Modelspace `TEXT` 辅助覆盖文字可进入计划。
3. **第二阶段受控执行**：必须提供同一源 DWG、有效审计、有效计划、精确计划确认和尚不存在的新输出路径。修改只在临时 DXF 中删除精确目标，并经过 DWG 往返、重新审计和非目标保持验证。

不得对 `一致` 或 `证据不足` 修改；不得删除图层、替换文字、移动坐标、改变几何、尺寸、配筋、数量、块、属性或其他实体。DWG 表示修改不是结构设计编辑、规范符合结论、施工指令或安全结论。

第二阶段已经实现，但只对审计准入的初始 `R2018/AC1032 DXF-exposable` profile 工作。审计必须始终
先于计划和执行；代理/自定义实体或对象、代理图形、非空或不受支持的 `ACDSDATA`、未建模原始
标签/节、`SORTENTSTABLE` 或其他不受支持的绘制顺序/对象元数据、以及对象启用器缺失时，必须在
计划或执行前失败关闭。不得绕过这些兼容性门，也不得通过转换或剥离代理/自定义状态来强迫成功。
稳定的脱敏错误（例如 `UNSAFE_ENTITY_TYPE`）不含图纸细节；下一步是保留只读结论。

`audit --topology-profile <profile.json>` 可额外运行固定 `beam-plan-in-situ/v1`，只审查显式梁边、支座、跨与支座上部/跨中下部原位注写的位置关系。profile 仅在本地使用，角色图层互斥且不含容差、正则、实体类型、回退或修改控制；实际重叠门必须先于 token 和拓扑绑定。不得按最近梁、支座或跨绑定，也不得把梁线交点当作支座。拓扑发现永不授权编辑：它们固定为非可操作、不会进入计划目标，`plan`、`apply`、`verify` 仍只使用既有辅助覆盖文字删除流程。

ODA File Converter 不是原生数据库编辑，不能使每份 DWG 获得资格。若用户要求保留代理/自定义
状态，应说明需要适当许可的 ODA Drawings SDK、Autodesk RealDWG/AutoCAD 环境和相关 object
enablers；本项目不捆绑或推断这些 SDK 的访问权。生成式真实 ODA 测试只证明该 profile 的能力，
私有资格夹具不会发布，不能据此宣称通用 DWG 兼容性。

本 Skill 的受支持执行和 CI 平台是 Windows。真实 DWG 的第二阶段 `apply` 和 `verify` 依赖保留的文件句柄来禁止发布临时文件或已验证输出被写入、删除或替换。其他平台未测试且不受支持。

ODA 转换必须在受验证 NTFS 与限制性 DACL 的两个独立随机私有根中执行。输出目录先为空，
只允许一个随机源文件和精确过滤器；运行后只接受恰好一个新的普通非重解析候选，并须先用
禁止写入/删除的无跟随句柄绑定身份、大小、SHA-256 和格式头。不得预创建 ODA 输出、
要求 ODA 保留文件身份或使用 ADS；同名、陈旧、多余或 sidecar 文件均失败关闭。两个 ODA
运行不是独立实现，必须有完整双重比较，并以完整重新审计的 `before - planned targets`
状态证明作为第二阶段依据。具体受信本地会话威胁边界与窄易变项见
[两阶段 DWG 审计与受控修改](references/dwg-two-stage-workflow.md)。

验证 JSON 仅是有时间界限的证据，不是敌对替换防护或任何编辑授权。其 `output_binding` 会绑定当前输出 DWG 的 SHA-256、字节数、路径散列、文件身份、DWG 头/版本和验证时间，并被 JSON 完整性覆盖；使用者必须对当前 DWG 重新计算匹配，文件被替换或移动后旧 JSON 不再证明它。

## 可选原生 CAD Bridge

原生 Bridge 是与上述 ODA 两阶段工作流分离的可选通道。操作人员可明确选择
外部适配器、PID 和已公布的本地随机命名管道，或由
`native-session prepare --bootstrap <private-file> --native-config <private-config>`
原子 claim 一次性 private bootstrap advertisement；后者在连接前检查
DACL/reparse、expiry、nonce/config、plugin/host/capability 和 PID/process identity，
且 Python 始终拥有 session ID。详细边界见
[可选原生 CAD Bridge](references/native-cad-bridge.md)。默认只读；不扫描进程、
注册表或 PATH，不选择窗口，不使用 GUI、鼠标、键盘、焦点或自动化，也绝不在
原生失败时回退到 ODA（反之亦然）。手动 NETLOAD 与 full-host bootstrap 绝不由
Python 或 qualification script 自动执行。

已发布 native v1 artifact 是冻结、legacy-read-only 的读取表面，不能执行原生写入；
active session/audit/plan/manifest/result/export/verification 均为 v2。v1 缺少
monotonic、stable-host 或 actual-output security binding 时不得补造，必须 fresh
session prepare 和 fresh native audit；执行门返回
`NATIVE_LEGACY_ARTIFACT_READ_ONLY`。未变化的 `native-bridge/v1` 仅是冻结的
read-only wire request/response，不是 v1 写入兼容声明。

任何原生写入必须从保留的源句柄复制到私有副本，以固定 `NETLOAD` 与固定命令在
外部 Core Console 上运行；源和最终公共路径绝不交给该进程。保存后必须启动新的
读回进程并验证精确允许差异，成功后才无替换发布。外部插件的单事务/回滚声明是
conformance claim，不是本项目可证明的内部事实；公开 CI 的生成 mock 也不构成
外部宿主集成声明。外部许可、对象启用器和专有组件由操作人员负责，仓库不分发。
`native-cad` 的 SDK-free 内存事务核心和 syntax-only API stubs 只证明固定协议/
允许差异模型；它们不是 runtime qualification。受许可操作人员可用实际 adapter
source/package 执行窄 `DBText` translation、copy-only apply 和 fresh readback；
但只有显式 `LIANG_PINGFA_RUN_REAL_HOST=1` 的 private evidence 才可资格认证，
public CI、dry-run、fake SDK 和 stub 都不能作此声明。当前 AutoCAD adapter
没有 TSSD profile；TSSD 需要独立 adapter ID、TSSD-specific host identity、
plugin/vendor evidence 和 object enabler 后才可能资格认证。

原生会话描述符、机器可读 audit/intent/plan/manifest/Core Console result/export/
verification（以及任何恢复日志）均为私有最终文件：它们只允许当前用户和 `SYSTEM`，
并在作为原生输入时重新验证该受保护 DACL。它们会枚举 opaque 记录或操作，因此本地
数量可见并明确标记为 `record_cardinality: explicit_private`；其中部分工件可包含原始私有字段；其分类与公开报告不同，见下文。原生 audit/plan 的 JSON 与红删 Markdown
成对无替换发布，但只有 Markdown 继承明确的公共父目录读取策略。Markdown 报告、CLI
摘要和稳定错误事件不报告数量。marker policy 的 version、profile/
enable/capability、layer/style token 和 fingerprint、height/rotation、文本派生版本及
几何默认值必须从 audit 到 plan/manifest 完全相等；任何后续漂移都失败关闭。


### 原生私有工件的准确隐私分类

PRIVATE-ARTIFACT-PRIVACY: sensitive local raw artifacts; retained no-follow
handle owner/DACL validation is required; never commit or upload.
Administrators is accepted only when it is the current process token's default
file owner, which is the supported elevated-token private-file creation case.
Session
descriptors contain pipe, nonce/challenge, and process/document bindings. Exact
geometry and console exports contain raw text, coordinates, layers, paths,
handles, and geometry. Manifests contain raw preconditions/geometry and plugin,
Core Console, and source-copy bindings. Intent can contain requested deltas and
marker geometry. Console results/logs are sensitive and bounded. Native
audit/plan/verification/recovery JSON is private redacted-or-opaque machine data
with explicit local cardinality where applicable, not a public redaction claim.
Only public Markdown reports, CLI error events, and CI logs are redacted and
cardinality-independent; they contain no raw private fields.

所有原生机器工件均为**敏感私有工件**，必须存放在受保护的本地 NTFS 存储中，使用仅当前用户和 `SYSTEM` 的有效 DACL；读取前必须通过同一保留的无跟随文件句柄验证所有者和 DACL，并在有界读取、JSON 重复键/模式/完整性验证期间保持该句柄。默认受信所有者仅为当前用户或 `SYSTEM`；只有 Windows 报告当前进程 token 的默认文件所有者正是 `Administrators` 时，才允许该精确 SID，以支持提升 token 的正常私有文件创建。Builtin Users、Everyone、Authenticated Users、任意服务 SID 或其他帐户永不受信。私有工件绝不提交、上传或写入公共 CI 日志。

- 会话描述符是敏感私有数据，含管道、nonce/challenge、进程和文档绑定。
- 精确 geometry export 与 Core Console export 是敏感私有原始文本，可能含坐标、图层、路径、句柄和几何；Core Console result/log 同样敏感且有固定字节上限。
- native manifest 是敏感私有原始前置条件/几何工件，含插件、Core Console 与源副本绑定；intent 也可能含请求的增量或 marker 几何。
- native audit、plan、verification 与恢复日志是私有的 redacted/opaque 机器 JSON；可明确列出本地记录/操作数量（`record_cardinality: explicit_private`），但绝非公开脱敏接口。
- 只有公开 Markdown 报告、CLI 错误事件和 CI 日志是脱敏且与数量无关的输出；它们不得包含原始字段、私有路径、坐标、图层、句柄、管道或几何。
