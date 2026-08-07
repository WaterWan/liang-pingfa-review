# 可选原生 CAD Bridge 工作流

本原生通道的受支持执行和 CI 平台是 Windows；其他平台未测试且不受支持。

## 原生 v1/v2 版本边界

`c374e6c` 发布的 native v1 schema 和 C# V1 DTO/interface 是不可变的历史读取
契约。v1 artifact 只能被校验、读取、报告或送入明确迁移检查；任何 native plan、
apply、Core Console write、readback 或 published-output verify 都必须拒绝它，并返回
稳定的 `NATIVE_LEGACY_ARTIFACT_READ_ONLY`。不得把 v1 的字段名、预测输出或缺失
binding 当作 v2 授权。

活动 adapter config、session、geometry、audit、intent、plan、manifest、console result/
export 和 verification 使用 `liang-pingfa/native-*/v2`。v2 绑定同启动 monotonic
session、稳定 host、私有 cardinality、2,000 entity/10,000 segment/1,024 operation
边界、prewrite binding、final-output constraints 与实际输出/readback binding，并在
plan/manifest/result/export/verification 之间交叉绑定 v2 schema 和 integrity。
`native-bridge/v1` request/response 仍保留，仅因为其 wire JSON shape 确实冻结；
这不构成持久 artifact 的 v1 写入兼容承诺。

只有 v1 adapter config 可显式、确定性地改写到同语义的 v2 config。v1 session/audit/
plan/manifest 等缺少安全字段时必须拒绝迁移，要求 fresh session prepare 和 fresh
native audit；绝不合成 monotonic、stable-host、prewrite 或 actual-output 值。

## 范围与非声明

原生 Bridge 是与现有 ODA 两阶段流程完全分离的可选本地通道。仓库只提供
协议、Python 客户端、受控编排、SDK-free C# 合同和生成式 mock；不提供、
不加载、不反向工程任何厂商 SDK、宿主插件、对象启用器或二进制文件。

公开 mock CI 只证明项目侧协议和拒绝路径，不证明外部宿主、插件、事务实现
或任意图纸的兼容性。外部适配器必须由操作人员单独安装、许可和验证；任何
事务/回滚字段都是外部 conformance claim，项目只能用保存后的独立导出检查
可见的精确状态，不能证明其内部实现。

### SDK-free 可执行核心检查点（非宿主证明）

`native-cad` 现提供运行时生成内存数据上的 C# v2 协议/事务核心。它可执行
固定 manifest 的 `translate_dbtext`、`delete_auxiliary_overlay_text` 和经
capability/policy 门控的 `create_review_marker`，在一个 vendor-neutral staged
transaction 中预检、回滚、精确 staged export/readback，再且仅再 commit 一次；
commit 后核心必须先 dispose transaction，才可调用
`ICadDatabase.SaveAndReopen()`；只对**新打开**的数据库 export/readback 通过后才由
executor 内部 verified-readback token 构造 result 或 console export。完整、有序的
owner state（包括未使用 owner）也是 protected state；增删、重排或替换任一 owner 都会
失败。save、reopen 或该读回任一
失败时不得发送成功 result，也不得把已提交的私有副本说成已经 rollback。未来经许可
adapter 可用 private `SaveAs` 后新的 `Database.ReadDwgFile`，或独立 readback
process，实现这一 vendor-neutral 边界。它还
包含项目原创的 `Autodesk.*` **syntax-only** declarations：每个 public nonabstract
reference-type stub constructor 和可执行成员均立即抛出 `NotSupportedException`。
struct 保留 CLR 的 default value construction 边界，但其显式 constructor/可执行成员也
抛出；它们均不可部署、不允许 `dotnet pack` 或 `dotnet publish`，且不提供 runtime
behavior。

这只证明 SDK-free 核心的生成式模型和严格允许差异，不证明 AutoCAD command、
document lock、数据库映射、真实 transaction、TSSD、ODA、RealDWG 或任何
runtime compatibility。实际 AutoCAD adapter 仍是下一检查点；本检查点没有
加载、复制、反编译或发布厂商 DLL/SDK，也不得把 stub 编译或内存测试写成
真实宿主集成。

checkpoint 1 的 MSBuild policy 也为 fail-closed：每个 current project 只能有
精确 root `Project Sdk="Microsoft.NET.Sdk"` 和一个无条件的 allowlisted
`TargetFramework`；child SDK、`FrameworkReference`、package、`Reference`、
`HintPath`、Import/UsingTask/Exec/network hooks 及 props/targets/environment
override 均拒绝。每个 project 文件末尾无条件精确 import
`NativeCad.RepositoryPolicy.targets`，故 `ImportDirectoryBuildProps=false` 或
`ImportDirectoryBuildTargets=false` 也不关闭该 policy。未来 adapter 仅可在其自身
已评审、明确 policy mode/condition 的 ItemGroup
中讨论三项 proprietary `<Reference>`；这不是当前 exemption，也绝不放宽
`HintPath`、SDK、package 或 shared props/targets。

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
`native-session prepare` 的最开始即捕获 UTC `created_at` 和同一 Windows 启动周期
可跨 CLI 进程比较的 `GetTickCount64` tick，并只计算一次精确五分钟的
`monotonic_expires`。私有 descriptor 的完整性还覆盖
`monotonic_clock`、`monotonic_boot_id`、`monotonic_issued` 和
`monotonic_expires`；这些字段不是 wire response、Markdown 报告或 CLI 事件。
health、`get_session`、descriptor publication/consumption 和后续每个 RPC 都以原始
uptime deadline、严格 UTC expiry 与配置方法超时的最早值为界。descriptor 仅在同一
boot/domain 有效：重启/domain 不匹配、当前 uptime 小于签发值、或达到 expiry 都失败
关闭，墙钟回拨、延迟握手和另一个进程读取不会重新获得五分钟窗口。
`get_session` 的 `challenge_response` 必须是小写 SHA-256：按顺序编码协议
版本、`liang-pingfa/native-bridge/challenge-response/v1`、session ID、client
nonce、challenge、bridge nonce；每个 ASCII 字段均以前置的无符号 32 位大端字节
长度编码。不得用普通字符串拼接，且客户端以常数时间比较结果。因此任何重新签名
后但响应与上述完整 transcript 不一致的持久会话描述符都会被拒绝。

`native-session prepare` 只接受正常固定本地 NTFS 私有父目录：每个已保留词法
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
single-flight 锁。冻结 v1 读取上限保持原样；活动 v2 固定接受至多 2,000 个实体和全部实体合计 10,000 条线段；
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
攻击者字段不享有该例外。历史 v1 artifact 只按冻结 schema 读取，绝不静默迁移或
重解释为 v2 write authorization。
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

`native-audit` 的有效期为 15 分钟。vendor-neutral core/ODA-compatible profile 可请求
以下固定 profile；selected adapter/config 必须同时广告对应 capability。实际 AutoCAD
adapter 是更窄的 profile：只广告 `translate_dbtext/v1` 及 default-disabled、
capability-gated `create_review_marker/v1`，绝不广告或接受 delete。

- `translate_dbtext/v1`：直接 Modelspace DBTEXT 的有限、非零 XY 平移；每个非零轴必须使位置、序列化边界和序列化线段的每个受影响 binary64 标量产生有限且位模式不同的结果。若舍入为原值、溢出或非有限，manifest 会在启动 Core Console 前失败关闭；零轴保留原始位模式。
  文本、旋转、图层、样式、所有者和类型必须保持。
- `delete_auxiliary_overlay_text/v1`：只删除精确审计过的 TEMP/textarea
  覆盖文字，且必须通过唯一内容、左右面板、可见干扰和不支持数据门；绝不按
  图层批量删除。
- `create_review_marker/v1`：默认关闭；只有配置和外部插件能力都明确开启，
  且已审计既有 marker 图层/样式时才允许一个由 operation ID 派生的固定
  DBTEXT marker。

AutoCAD 的 `SaveAs` 后重新打开会压缩已擦除 slot，当前 v2 的 gap-preserving delete
合同无法表达该序列变化。因此任何 AutoCAD delete manifest 都必须在
`BeginTransaction()` 前以 `LPF_UNSUPPORTED_OPERATION` 拒绝，adapter 源码不得调用
`DBText.Erase()`。只有新的版本化 sequence-compaction policy 和真实宿主证据才可改变
这一点；ODA 窄 profile 的精确 overlay TEXT delete 行为不受影响。

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

## checkpoint 2：许可 AutoCAD adapter 源码边界

仓库现含 `native-cad/src/LiangPingfa.NativeCad.AutoCAD.Adapter` 的受控
AutoCAD managed adapter **源码**。它不是二进制发行包，也不是任何真实宿主的
兼容性声明。公开 CI 只能使用项目原创、带 `syntax-only` assembly marker 的
`ApiStubs` 编译相同源码；stub DLL 不会复制到 adapter 输出或部署内容。此编译只证明
语法、命令 metadata、禁止 API 扫描和 SDK-free core 合约，绝不证明 AutoCAD、
Core Console、TSSD、object enabler 或图纸运行时。

adapter 必须显式传入 `BuildAutoCadAdapter=true`、`CadHostProfile` 与
`UseAutodeskApiStubs`。真实模式还必须由持证操作人员显式传入绝对、存在、非
reparse 的 `CadSdkDir`，其中仅可有 `AcMgd.dll`、`AcDbMgd.dll` 与
`AcCoreMgd.dll` 三项 managed reference，且全部 `Private=false`/`CopyLocal=false`。
不得搜索 PATH、registry 或安装目录；不得下载、复制、pack 或 publish 厂商 DLL。
缺少 SDK、错误 profile、reparse、copied DLL 或 stub deployment 都必须失败关闭。

profile 是明确的：`autocad2024` 为 `net48`；`autocad2025` 为
`net8.0-windows`；`autocad2026` 为 `net10.0-windows`。Autodesk 当前 2026
managed compatibility table 规定
Update 1.2 及以后使用 .NET 10；不得将 2026 profile 错留在 .NET 8。
当前 AutoCAD adapter 不实现、构建、advertise 或资格认证 TSSD。未来 TSSD
adapter 必须有不同 adapter ID/version、TSSD-specific product identity、
plugin/vendor evidence 和必要 object enabler；不得把 AutoCAD host 标为 TSSD。

真实 adapter 只注册三个固定命令：

- `LPF_NATIVE_BRIDGE_BOOTSTRAP`：full-host/session 命令；只从私有 env
  nonce/output/root/expiry/**canonical config SHA-256** 创建随机、one-instance、
  本地 named pipe advertisement。`native-bridge-bootstrap/v1` 是 bounded、
  canonical、no-replace、current-user/SYSTEM DACL 的一次性私有文件，含
  schema/protocol、PID/pipe、read-only mode、adapter/plugin/host/runtime、
  canonical capabilities、进程 identity、nonce/config binding 和 expiry，
  **绝不含 session ID**。Python 先原子 rename claim、验 DACL/reparse/expiry/
  nonce/config/plugin/host/capability/process，再连接 pipe；第二消费者、replay、
  stale 或错 config 均在 pipe 使用前失败。pipe 拒绝远程 client，DACL 只给当前 user 与 SYSTEM，并核验
  client PID/SID/Windows session。pipe worker 不直接访问 Autodesk database；
  它只做 bounded canonical UTF-8 framing/auth，随后经
  `ExecuteInCommandContextAsync` 和 document lock 调度只读导出。
  advertisement 和每一次 `get_session`/document/inventory/geometry 导出都必须先
  拒绝未命名、路径不存在、`DWGTITLED != 1`、  `DBMOD != 0` 的 drawing；绝不保存或提示用户。adapter 不使用不存在的
  `Document.Saved` 或 profile 不一致的 active-transaction 计数。导出 transaction
  前后均以读句柄捕获 disk binding，并严格比较 document/database path、
  `FingerprintGuid`、`VersionGuid` 和 binding；dirty、SaveAs、切换、关闭、数据库
  替换或字节漂移均返回 `DOCUMENT_CHANGED` 并失效 session。
- `LPF_NATIVE_EXECUTE_MANIFEST`：Core Console modal 命令；只读取固定 private
  manifest/result/run/root env，校验 canonical v2、integrity、expiry、one-use、
  plugin/Core Console fingerprint、source/private-copy binding 和 DACL/reparse
  边界后，才调用 core executor。
- `LPF_NATIVE_EXPORT_MANIFEST`：独立 Core Console modal 命令；只读取同一固定 env
  和 private write receipt，重新打开当前私有 copy 并 fresh export/readback。

不允许 command 参数、Editor.Command、SendStringToExecute、AutoLISP、COM、
dialog、selection、mouse、keyboard、focus 或任意 CAD command。adapter 用
Handle→ObjectId 显式解析、完整 owner/container/物理顺序保护；可编辑范围只包括
direct Modelspace DBTEXT。DBTEXT 的 logical bounds 是 insertion point，绝不使用
glyph-dependent extents。LINE、零 bulge LWPOLYLINE 可以只读导出；proxy/custom/
unknown 或缺少 enabler 的对象没有 stable opaque provider 时必须失败关闭。

写操作先在一个 Autodesk Transaction 中完整 preflight，再逐项重新解析 target、
apply、staged verify，所有 postcondition 通过后仅 Commit 一次；失败则 Abort。
Dispose transaction 后才对当前私有路径同版本 `SaveAs`，再用新的 `Database`
`ReadDwgFile` readback。final result 只能在 fresh readback binding/geometry
verification 之后写入 private result 文件；Python retained-handle output binding
仍是最终公共发布的权威。真实 host qualification 只能通过
`LiangPingfa.NativeCad.AutoCAD.RealHost.Tests` 的显式许可 SDK/Core Console/
generated-private-fixture gate 获得私有证据，不能由 stub 或 CI 推断。

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

没有脚本自动执行 NETLOAD、UI、Editor prompt、鼠标、键盘、焦点、窗口发现、
SendKeys、动态命令或脚本。NETLOAD 与 bootstrap 命令由持证操作人员在明确的
clean/saved document 中手动执行。写入超时为 120 秒，读回
超时为 60 秒，标准输出/错误均有固定上限且不会出现在报告中。
`native-console-result/v2` 的硬读取上限固定为 256 KiB，不得因大量操作而提高；v2
最多 1,024 个 native operations（覆盖已验证的 623-operation 场景），并在
`BeginTransaction` 前以所有 operation ID/status/digest 和完整 canonical envelope
精确计算结果字节数，保留 16 KiB headroom。超出预算的 plan/result 必须在 mutation
之前失败，失败结果也不得超过 reader cap。
`native-console-export/v2` 的外层则固定使用 ConsoleExport canonical profile：
只有根 `geometry_json` 是 16 MiB UTF-8 opaque carrier，integrity 计算和写文件都必须
使用同一 profile；完整外层 export 在写入前还受独立 32 MiB cap。没有 carrier 的 result
始终使用严格 64 KiB string profile。

manifest 只保存新鲜导出的 `expected_prewrite_revision`，其中包含源文件身份/
散列和 `PortablePrewriteProjectionV2`：有序实体/容器、geometry、owner/opaque
protected state 与 policy-independent table/layout/block digest。它明确排除 source
path/file identity、session/process 和 database/revision GUID-derived identity，因为
private copy 打开后可获得不同宿主值。bridge-only database/revision identity 只显式保留
为 embedded bridge geometry 的一致性证据，不与 Core Console private copy 比较。
marker policy 也不混入 portable geometry，而是通过 stable-host digest 单独、精确地绑定；
portable table digest 仍会拒绝 marker resource drift。它绝不预测 final revision。
写入后必须启动**新的** Core Console 进程，以固定读回命令导出私有输出。
`native-edit-manifest/v2` 在写入前只绑定精确的
`expected_prewrite_output_copy_binding`：它完整包含将要打开的私有副本 SHA-256、
字节数、路径指纹、文件身份和 DWG 头。**最终** SHA-256、大小、身份和 revision 在
SaveAs 前不可知，绝不得预测或从 prewrite 复制。manifest 的
`final_output_constraints` 改为完整性覆盖的授权：精确私有路径/根指纹、同卷及私有
根策略、必需 DWG header/version、最大大小和 identity transition policy
（same-identity 或允许 replacement）。transaction 内的 staged export 必须逐字段等于
prewrite binding；`SaveAndReopen` 后保留实际私有 DWG 句柄，计算实际 binding 并先验证
这些 constraints，随后 `native-console-result/v2`、新的 readback export 和嵌入 geometry
必须逐字段等于这个**实际** binding。原输入、陈旧 prewrite、错误路径/header/大小、
禁止的 replacement、伪造 result 或任意新保存目标都不能成功。稳定宿主 digest 同时绑定 protocol、adapter
ID/profile/version、plugin ID/version/fingerprint、完整 capability set、宿主
product/release/runtime/mode/可执行文件指纹，以及 marker policy；新的 session/PID/
pipe/database/revision 可以变化，其余任一漂移均不得产生 success token。
已发布的 v1 schema 仅保留用于结构化读取旧 artifact；所有 active execution gate
（session、audit/plan、manifest、Core Console、readback、verification）都明确拒绝
v1 并返回 `NATIVE_LEGACY_ARTIFACT_READ_ONLY`，绝不把旧字段静默解释为 v2 constraint。
项目比较 `before → manifest 允许差异 → after`：平移必须精确移动位置/边界/
线段；vendor-neutral core/ODA delete 仅能去除目标且不重编号其余实体；实际 AutoCAD
adapter 不接受 delete；一个或多个 marker 必须在唯一的
直接 Modelspace 容器中按 operation ID 派生的追加顺序逐一双射匹配。每个
既有实体的 sequence index、容器、相对顺序和指纹序列均受绑定；paperspace、
block 和所有非目标容器也必须保持。写结果产生新的 `final_revision_fingerprint` 和 final database/document/output-copy
binding；读回 envelope、嵌入 geometry 和保存的私有输出副本必须与该结果完全相等，
任何可自洽但陈旧的导出都拒绝。默认 save/reopen 语义要求 Core Console 在其**本地**
prewrite/readback 边界确认 revision transition；bridge revision 不可被当作 private-copy
revision。只有经配置的插件 capability 和匹配 transition enum 才能明确允许保留。
验证通过后才以无替换语义发布公共 DWG，并生成仅作证据、绝不授权未来编辑、且保持
私有 DACL 的 `native-verification`。

活动 `native-geometry-export/v2` 还必须为每个实体容器携带有界
`physical_slot_count`。这是包含已擦除 slot 的物理 extent，不可由活跃实体最大
sequence 推断，也不泄露已擦除对象的 handle 或内容。vendor-neutral core/ODA delete
保留 count 和 gap，平移保留 count，marker 从原始 direct Modelspace count 加确定 ordinal 预约并每次追加
恰好加一；audit/plan/manifest precondition、geometry/protected/order/document
digest 与最终 readback 都必须精确绑定这些 container-specific counts。AutoCAD adapter
必须枚举 Autodesk 文档所述的只读 `BlockTableRecord.IncludingErased`
`BlockTableRecord` 返回值，而不能把它伪造成泛型 `IEnumerable` 属性。

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

### 持证操作人员的实际运行顺序

1. 安装匹配的受许可 host/SDK，使用
   `native-cad/scripts/build-autocad-adapter.ps1` 显式给出 profile、SDK、
   新 package 路径、private root 和 private receipt。该 package 只能包含本仓库
   adapter/core/protocol DLL、PDB、deps JSON、文档/template；不得有 vendor DLL、
   stub、SDK、receipt 或 evidence。receipt 必须列出所有 allowlisted file 和
   Adapter/Core/Protocol（2025/2026 还包括 `.deps.json`）的 size/SHA-256；其
   runtime-package fingerprint 对 format/profile/TFM/按 NFC 文件名排序的组件
   记录计算。
2. 在 private NTFS root 创建 config 和 bootstrap nonce/expiry；手动 NETLOAD
   package，并在明确的 clean/saved full-host drawing 内手动运行
   `LPF_NATIVE_BRIDGE_BOOTSTRAP`。config 必须携带 receipt 的完整
   `runtime_package` 和 plugin `runtime_package_fingerprint`；bootstrap 环境还
   必须提供同一 fingerprint。不得由 Python 或 qualification script 自动 NETLOAD。
3. 用
   `native-session prepare --bootstrap <private-file> --native-config <private-config>`
   让 Python claim advertisement 并生成 client-owned one-use audit session；随后
   `native-doctor`、`native-audit`、reviewed private intent、`native-plan`。
4. apply 前操作人员必须在未改变的 full host 中再次手动 bootstrap。新
   bootstrap/session 用于 `native-apply`，它只保存新 output copy，并执行
   Core Console write、fresh readback 和 `native-verify`。
5. 只对生成且获授权的 private fixture，使用
   `qualify-real-host.ps1 -Phase audit`，再在 fresh bootstrap 后使用
   `-Phase apply`，同时显式设置 `LIANG_PINGFA_RUN_REAL_HOST=1` 和传入同一
   private `-ReceiptPath`。脚本在 bootstrap 前、audit 后、apply 前和
   readback/verify 后重新验证 receipt、package allowlist、每个 critical hash/
   size 和 config fingerprint；receipt 缺失/篡改、extra/vendor/stub 文件、deps/
   Core/Protocol 替换或 package-root switch 均不得产生 evidence success。脚本仅产生
   private redacted summary/evidence；public CI、stub 编译和 dry-run 都不是
   runtime qualification。当前路径没有 TSSD profile；TSSD 必须先实现上述不同
   adapter/evidence 路径。
