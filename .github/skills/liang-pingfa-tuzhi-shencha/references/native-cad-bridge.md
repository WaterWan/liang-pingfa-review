# 可选原生 CAD Bridge 工作流

本原生通道的受支持执行和 CI 平台是 Windows；其他平台未测试且不受支持。

## 范围与非声明

原生 Bridge 是与现有 ODA 两阶段流程完全分离的可选本地通道。仓库只提供
协议、Python 客户端、受控编排、SDK-free C# 合同和生成式 mock；不提供、
不加载、不反向工程任何厂商 SDK、宿主插件、对象启用器或二进制文件。

公开 mock CI 只证明项目侧协议和拒绝路径，不证明外部宿主、插件、事务实现
或任意图纸的兼容性。外部适配器必须由操作人员单独安装、许可和验证；任何
事务/回滚字段都是外部 conformance claim，项目只能用保存后的独立导出检查
可见的精确状态，不能证明其内部实现。

## 明确选择和只读会话

默认只读。操作人员必须明确提供已加载外部 Bridge 的 PID 和其公布的本地
随机命名管道，使用：

```text
native-session prepare --pid <PID> --pipe <advertised-local-pipe> --session-out <private-session>
```

工具绝不枚举“第一个”进程，不扫描注册表或 PATH，不选择当前窗口，也不在
ODA 与原生通道之间回退。客户端在连接前后绑定 PID、创建时间、Windows
会话、服务器 PID、nonce/challenge、协议、适配器/插件指纹、能力和已保存
文档身份。持久会话描述符尚未存在时，专用 pre-handshake client 只能严格按
`health`、`get_session` 的顺序发出这两次 RPC；它不接受占位 descriptor，也不能
读取 document/inventory/geometry。只有两项响应、challenge transcript、当前已保存
文档及本地 PID/process binding 全部通过后，才构造、完整语义验证并私有发布一次
使用的 descriptor。会话短时、一次使用；断开、未知 RPC、重复/错配 ID、额外帧、
文档切换或能力漂移都会失败关闭。
`get_session` 的 `challenge_response` 必须是小写 SHA-256：按顺序编码协议
版本、`liang-pingfa/native-bridge/challenge-response/v1`、session ID、client
nonce、challenge、bridge nonce；每个 ASCII 字段均以前置的无符号 32 位大端字节
长度编码。不得用普通字符串拼接，且客户端以常数时间比较结果。因此任何重新签名
后但响应与上述完整 transcript 不一致的持久会话描述符都会被拒绝。

`native-session prepare` 只接受正常本地 NTFS 私有父目录：每个已保留词法
祖先都不得是 reparse point，且其 DACL 不得给予 Everyone、Users 或其他不受信
主体删除子项、改 ACL/所有者、改属性/EA 或替换既有组件的权限。目录仅有
`FILE_ADD_FILE` 或 `FILE_ADD_SUBDIRECTORY` 时不等同于替换已保留子项；保留的
无 delete/rename 句柄仍覆盖该竞争窗口。描述符不用公共工件写入器；它以 no-replace
专用文件 API 创建，在仍保留排他的句柄时应用并读回仅当前用户和 SYSTEM 的受保护
DACL，然后才报告成功。会话描述符在任何 RPC 前必须在其已保留的本地父目录中以
不可替换的随机 claimed 名称原子改名，并由同一打开句柄读取、保留和删除。第二个
消费者只能看到原名称不存在，不能读取、连接或复用同一管道；rename 后即使后续
绑定、解析或操作失败也必须经保留句柄删除原秘密文件；如无法证明所有权，保留
替换项并报错。审计会话与 apply 的新会话可以使用不同 PID/会话 ID、管道、nonce、
数据库实例和 pre-write revision，但必须有
相同的隐私安全 `native_host_binding`：协议、适配器/插件 ID/版本/指纹、
完整能力集合、宿主产品/发布版/runtime/模式、宿主可执行文件指纹、Core
Console 指纹和配置 profile 都要一致。PID、管道、nonce 和 session ID 不在
该稳定绑定中。不可获得的宿主可执行文件指纹只能保留只读结论，不能生成计划
或 manifest。

协议仅允许 `health`、`get_session`、`get_current_document`、
`export_inventory` 和 `export_exact_geometry`。它是长度前缀、严格 UTF-8
JSON 的单请求协议；没有批处理、通知、自由命令、LISP、脚本正文或写 RPC。
客户端使用不可阻塞 single-flight 生命周期锁覆盖检查、写帧、读帧、验证和
失效：第二个并发调用在写任何帧前失败；任何协议错误都会失效会话并安全释放锁。
连接、health、session、document、inventory、geometry 均使用显式配置的正
超时，并且不得超过协议硬上限。
客户端以不可继承的 `FILE_FLAG_OVERLAPPED` 管道句柄执行每次 ReadFile/WriteFile，
每次操作都有独立 event，并且整帧前缀、正文和短写循环共享同一个绝对 deadline。
deadline 到达时会调用 `CancelIoEx`、等待取消完成、释放 event 并永久失效该会话；
不会以同步 I/O 后的事后计时来掩盖阻塞写入。
同一个绝对 deadline 还覆盖 UTF-8/嵌套/重复键/NFC/有限数规范化、通用 Draft
2020-12 schema 迭代、`uniqueItems`、实体/线段和指纹语义检查；这些遍历按固定批次
checkpoint，超时会报告稳定的 RPC timeout 或 session expired、关闭客户端并释放
single-flight 锁。v1 固定接受至多 2,000 个实体和全部实体合计 10,000 条线段；
原始 geometry JSON 上限为 16 MiB **UTF-8 字节**，而非 Unicode 代码点/字符数；
该限制在 bridge、私有导出、audit、manifest、Core Console export 和 readback 的每个
原始或嵌入 geometry 边界、解析/规范化前执行。JSON Schema 的 `maxLength` 只是次要
代码点约束。外层 geometry frame 上限为 32 MiB。inventory
不是 geometry 的替代品，其原始 JSON 上限为 64 KiB、外层 frame 上限为 256 KiB。
只有 bridge `result.geometry_json` / `result.inventory_json`、manifest
`preconditions_geometry_json` 和 Core Console export `geometry_json` 这四个精确
schema 路径的序列化 JSON 值是 outer opaque carrier：先以收到的精确 codepoint/
UTF-8 bytes 进行字节上限、binding 和 hashing，绝不把整段 carrier 交给 NFC。
随后才独立解析内部 JSON；内部键和值仍适用深度、重复键、有限数、单标量预 NFC
上限、canonical NFC、schema、语义和同一绝对 RPC deadline。相同名称的任意嵌套
攻击者字段不享有该例外。此前有效的 canonical 内部 JSON 保持相同 v1 artifact
bytes/hash，因此不需要迁移或静默重解释持久工件。
外部 server conformance 必须拒绝远程客户端、使用单一首实例、仅向当前用户和
SYSTEM 授权的 DACL，并在服务端核验客户端 PID/SID/Windows 会话；项目客户端
核验本地管道与 `GetNamedPipeServerProcessId`。这些措施防止意外错连，不把
恶意同帐户/管理员进程变成受防御对象。
受信边界是受信的本地 Windows 帐户/会话、操作系统、NTFS 卷和显式安装的
外部组件；恶意同帐户或管理员进程不在该边界内。

## 审计、意图与计划

原始几何导出只存在于 Bridge 内存或受限 PrivateWorkspace 中。它可以含有
精确 DBTEXT、LINE、简单 polyline 和受保护 opaque record；持久
会话描述符、`native-audit`、私有 intent、`native-plan`、manifest、Core Console
result/export、`native-verification` 和任何恢复日志均是私有机器可读文件。它们只保留
绑定摘要、opaque ID 和指纹，不公开文字、坐标、句柄、图层/块名称、管道、路径或控制台
输出；最终文件必须保留仅当前用户和 `SYSTEM` 的受保护 DACL，作为原生输入时也会重新
验证。它们会枚举 opaque 记录/操作，因此本地数量明确可见，并以
`record_cardinality: explicit_private` 如实声明；这不是公开报告接口。`native-audit`
和 `native-plan` 的 JSON 与红删 Markdown 成对无替换发布，JSON 不恢复公共父 ACL，
只有 Markdown 可以继承该公共读取策略。Markdown、CLI 摘要和稳定错误事件仍不公开
数量，也不公开私有原始字段。

每一份 geometry export 都重复并绑定 issuing session 的协议版本、session ID、完整
PID/Windows session/process-instance/可执行文件身份、宿主、adapter/plugin 版本与
fingerprint、**完全相等**的 capability 列表、保存源的 header/散列/大小/路径和文件
身份、current document 的 database instance/revision，以及 session/document digest。
`native-audit` 仅在这一完整 tuple 通过后生成，并把同一已验证的 session/document
digest 带入 audit；plan 由 audit integrity 及该 document digest 继承，manifest 的
fresh export 也必须与其 fresh session digest 完全相等。相同源字节、相同 stable host
但不同 session、或 capability superset/subset 都不能混入 audit、plan 或 manifest。

`native-audit` 的有效期为 15 分钟。私有 intent 只能请求以下固定 profile：

- `translate_dbtext/v1`：直接 Modelspace DBTEXT 的有限、非零 XY 平移；每个非零轴必须使位置、序列化边界和序列化线段的每个受影响 binary64 标量产生有限且位模式不同的结果。若舍入为原值、溢出或非有限，manifest 会在启动 Core Console 前失败关闭；零轴保留原始位模式。
  文本、旋转、图层、样式、所有者和类型必须保持。
- `delete_auxiliary_overlay_text/v1`：只删除精确审计过的 TEMP/textarea
  覆盖文字，且必须通过唯一内容、左右面板、可见干扰和不支持数据门；绝不按
  图层批量删除。
- `create_review_marker/v1`：默认关闭；只有配置和外部插件能力都明确开启，
  且已审计既有 marker 图层/样式时才允许一个由 operation ID 派生的固定
  DBTEXT marker。

不存在任意命令、坐标编辑、自由 marker 文本、图层/样式创建、块编辑、尺寸
编辑或配筋数据编辑。计划必须绑定新鲜 audit、私有 intent 散列、源/适配器/
profile、目标前状态、受保护状态和精确后置条件。

marker policy 是稳定宿主绑定、audit、plan 和 manifest 的同一精确对象：它包括
policy version、profile 及启用状态、插件 capability、layer/style token 与 fingerprint、
height/rotation 位串、固定文本前缀和派生版本，以及 direct Modelspace/block-path/
overlay-evidence 几何默认值。任何一项在 audit 或 plan 后变化都会在 manifest/apply
之前失败；原先关闭的 marker profile 不能由后续配置悄然开启。

## Copy-only 写入和读回

`native-apply` 先保留源文件的无跟随只读租约，要求尚不存在且不同于源的
公共输出，然后从保留句柄复制到私有 NTFS/DACL 工作区。外部 Core Console
只接触该副本，绝不接触源路径或最终公共路径。这是严格的 copy-only 边界。

配置的 Core Console、写插件和读回插件在散列前都必须逐级保留其词法祖先和
文件句柄；每个祖先及文件 DACL 的显式/继承 allow/deny ACE 都会用 Windows
安全 API 解释。只有当前 SID、SYSTEM 和 Administrators 可拥有写、删除、
改 ACL 或替换权限；Everyone、Authenticated Users、Builtin Users、访客、
匿名或其他 SID 的此类 allow ACE，以及无法解释的 ACL，都会失败关闭。保留
租约在脚本写入、启动、NETLOAD、进程树退出、读回验证和工作区清理完成之前
不得释放，并在启动前和进程结束后重新绑定身份、散列和 ACL。
标准 Program Files 安装链可由 Windows Modules Installer 的精确
TrustedInstaller service SID 所有；该 SID 只作为所有者例外，不把任意 service
SID 或该链上 Everyone/Users 的显式或继承写入 ACE 视为受信。

脚本严格只有 `_.NETLOAD`、一个经指纹验证的 DLL 路径和一个固定配置命令；
manifest 路径仅经一个私有环境变量传递。没有 UI、Editor prompt、鼠标、
键盘、焦点、窗口发现、SendKeys、动态命令或脚本。写入超时为 120 秒，读回
超时为 60 秒，标准输出/错误均有固定上限且不会出现在报告中。

manifest 只保存新鲜导出的 `expected_prewrite_revision`，其中包含源文件身份/
散列、保存文档路径和文件身份、内容/geometry/protected 摘要、适配器/插件、
稳定宿主绑定及审计语义状态；它绝不预测 final revision。写入后必须启动**新的**
Core Console 进程，以固定读回命令导出私有输出。
项目比较 `before → manifest 允许差异 → after`：平移必须精确移动位置/边界/
线段；删除仅能去除目标且不重编号其余实体；一个或多个 marker 必须在唯一的
直接 Modelspace 容器中按 operation ID 派生的追加顺序逐一双射匹配。每个
既有实体的 sequence index、容器、相对顺序和指纹序列均受绑定；paperspace、
block 和所有非目标容器也必须保持。写结果产生新的 `final_revision_fingerprint` 和 final database/document/output-copy
binding；读回 envelope、嵌入 geometry 和保存的私有输出副本必须与该结果完全相等，
任何可自洽但陈旧的导出都拒绝。默认 save/reopen 语义要求 final revision 不同于
pre-write revision；只有经配置的插件 capability 和匹配 transition enum 才能明确
允许保留。验证通过后才以无替换语义发布公共 DWG，并生成仅作证据、绝不授权未来编辑、且保持
私有 DACL 的 `native-verification`。

发布前的 DWG 和 verification 都是公共父目录中的隐藏临时文件，但不会继承并暴露
该父目录的可读 ACL：它们以零 share 的保留句柄创建，先通过同一句柄应用并读回
仅当前用户和 SYSTEM 的受保护 DACL，复制/写入和完整性绑定全程不按路径重新打开。
因此即使公共输出父目录较宽，清理 gate 完成前第二个进程也不能读取、写入、替换或
删除这些字节。私有工作区清理成功后、最终 no-replace rename 紧前，公共 DWG 才由
同一保留句柄恢复创建时捕获的父目录派生 ACL 并读回验证；verification 则保留其
私有 DACL，绝不恢复公共父 ACL。DWG 与 private verification 的两个 rename 后仍保留句柄，
先完成源、输出父目录、组件和其他失败型清理/最终绑定检查，才最终释放句柄；任何该
阶段失败都必须通过保留句柄删除两个 final。无法证明回滚时只报告 fatal recovery，
绝不把 passed verification 当作成功结果。

Windows 上 Core Console 只能在 kill-on-close Job Object 已创建后，以
CreateProcessW 的 suspended 方式启动；进程必须在 resume 前被赋给该 Job。
启动、赋 Job 或 resume 任一失败时会终止尚未运行的子进程并失败关闭。超时、
输出超限、异常或后代存活时只终止整个 Job 并等待树退出，绝不把直接进程 kill
当作成功回退；子进程仅继承明确列出的标准输入/输出/错误句柄。

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

## 许可和操作提示

原生通道可能需要外部宿主的相应许可、对象启用器和专有组件；这些均不随本仓库
下载或分发。配置必须显式给出 Core Console、读/写 DLL、精确 SHA-256、
固定命令、协议、能力、超时和 marker policy。无法满足任何本地文件信任、
NTFS、DACL、指纹、会话或读回条件时，保持只读结论；不要尝试 ODA 回退或 GUI
自动化。
