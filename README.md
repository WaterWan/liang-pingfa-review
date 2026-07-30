# 梁平法图纸审查 Skill

`liang-pingfa-tuzhi-shencha` 是一个中文优先的 GitHub Copilot Agent Skill，用于审查梁平法截图、PDF 页面图像和导出的 CAD 图像。它把图像可读性、梁图拓扑/位置绑定、集中标注与原位标注作用域作为结论前提，帮助输出可追溯的表示审查发现。

## 用途与范围

适合以下任务：

- 阅读梁平法注写、梁编号、尺寸和可见配筋字段。
- 解释集中标注、原位标注及局部覆盖关系。
- 核查疑似不一致、遮挡、辅助文字污染、错绑或字段不可分类问题。
- 比较修改前后或左/右图纸版本的表示和可读性。

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
- “比较修改前后两张导出的 CAD 图像，说明覆盖文字是否已清除。”
- “这张 PDF 页面图像中的 G/N 侧面筋字段能否可靠读取？”

## 使用限制

- 先检查图像可读性。数字、符号、括号、分隔符、G/N/g 或引出线不清时，结论必须是 `证据不足`。
- 先完成梁、跨、支座、悬挑或局部的拓扑/位置绑定，再解析数字。
- 位置明确的原位标注只覆盖对应局部的同一字段，不自动构成与集中标注的冲突。
- 原始 DWG 的读取、转换、字体和图层可见性受环境影响。无法可靠读取时，应请求截图、PDF 页面图像或导出的 CAD 图像。
- 复杂节点、特殊梁、加腋、并筋、井字关系和截面混合表示需要完整上下文；高风险歧义应由结构专业人员复核。

详细操作见[Skill 工作流](.github/skills/liang-pingfa-tuzhi-shencha/SKILL.md)及其同目录参考资料。

## 来源与版权

本仓库不附带任何外部标准、图纸、PDF、DWG、DXF、渲染图或 OCR 输出。外部技术来源及用户图纸的权利仍归各自权利人所有；使用者应确保自己拥有或获得使用输入材料的合法权限。

本仓库的原创文本和脚本使用 [MIT License](LICENSE)。请勿提交来源材料、派生图像、私有路径、散列、OCR 转储或本地夹具。

## 本地验证

在仓库根目录运行：

```powershell
python scripts\validate_skill.py
python -m unittest discover -s tests -p "test_*.py"
python -m py_compile scripts\validate_skill.py tests\test_validate_skill.py
```

所有脚本和测试仅使用 Python 3.13 标准库。回归夹具只可本地保存，说明见[本地夹具说明](tests/local-fixtures/README.md)。

## 贡献规则

- 保持 `.github\skills\liang-pingfa-tuzhi-shencha\SKILL.md` 为唯一规范入口，并让目录名与 front matter 的 `name` 一致。
- 保持 Skill 简洁，将扩展程序说明放入同目录 `references`。
- 不添加运行时依赖、网络调用、CAD 转换器、OCR 引擎或二进制文件。
- 不提交 PDF、DWG、DXF、图像、OCR 转储、来源内容、派生材料或本地夹具。
- 对非平凡改动补充标准库单元测试，并运行全部本地验证命令。

## 许可证

见 [LICENSE](LICENSE)。
