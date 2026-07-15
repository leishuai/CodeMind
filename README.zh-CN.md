# CodeMind

[English](README.md) | 中文

**CodeMind 是围绕 coding agent 的证据驱动执行闭环。它帮助 agent 规划、实现、
验证、修复并交付真实工程任务，而不是停在一个“看起来没问题”的答案上。**

CodeMind 可以配合 Codex、Claude Code、Trae 等 coding agent 使用。它不替代
这些 agent，而是让它们持续工作，直到结果有构建、测试、设备、UI 或其他具体
证据支撑。

> 给 coding agent 一个 harness，而不只是一个 prompt。

## 为什么用 CodeMind

Coding agent 很快，但真实任务经常在边缘失败：需求不清、测试缺失、环境异常、
UI 流程从未真正运行，以及没有证据的“已经完成”。

CodeMind 为 agent 增加工程闭环：

- **动代码前先规划** —— 明确目标、范围、风险和必须通过的验证。
- **验证真实结果** —— 按任务需要运行项目构建、测试、App、设备和 UI 流程。
- **失败后继续修复** —— 根据失败证据修复产品或验证路径，然后重新验证。
- **只在真正需要时停下来问人** —— 用户意图、权限、签名、设备或敏感操作需要
  决策时再暂停。
- **生成可审查的交付** —— 输出代码改动、证据、人类可读报告，以及以后可复用
  的成功经验。

## 一个任务如何完成

```text
你的请求
  -> 澄清和规划
  -> 实现
  -> 构建和验证
  -> 诊断失败
  -> 修复并重新验证
  -> 只有结果被证明后才完成
  -> 生成报告并沉淀可复用经验
```

CodeMind 会把任务状态保存在项目中。如果 agent 进程或验证步骤中断，下次可以
从已记录的任务状态继续，而不是依赖聊天记忆。

完整工作流和证据规则见 [docs/workflow.md](docs/workflow.md)。

## 快速开始

安装 CodeMind：

```bash
curl -fsSL https://raw.githubusercontent.com/leishuai/CodeMind/main/install-curl.sh | bash
```

运行不需要设备的 smoke test：

```bash
codemind smoke offline-demo
```

后续更新：

```bash
codemind update
```

安装路径和环境要求见
[installation-runtime.md](docs/references/installation-runtime.md)。

## 使用 CodeMind

### 在 Codex / Claude Code / Trae 中

安装后重启或 reload coding agent，然后输入：

```text
/codemind 修复登录崩溃并完成验证
```

CodeMind 使用当前 coding-agent session 做规划和实现，并持续推进验证和修复，
直到证据通过、确实需要用户决策，或遇到已经证明的 blocker。

### 在终端中

从希望 CodeMind 处理的项目目录运行：

```bash
cd /path/to/your-project
codemind ask "修复登录崩溃并完成验证"
```

常用命令：

```bash
codemind                         # 打开交互式 shell
codemind ask "..."               # 创建任务
codemind status <task-code>      # 查看进度和下一步
codemind resume <task-code>      # 继续已保存任务
codemind report <task-code>      # 生成人类可读报告
codemind update                  # 更新 CodeMind
```

运行 `codemind help` 查看完整命令列表。原有的 `automind` 和 `/automind`
继续作为兼容别名使用。

已有安装和任务历史无需迁移。CodeMind 继续使用 `.automind/` 数据目录和
`AUTOMIND_*` 环境变量，原有任务可直接继续。

### 在飞书中使用

CodeMind 可以连接飞书机器人。你可以直接在飞书中自然语言交流、确认开发任务、
查看进度、回答待确认问题，并接收最终结果。

```bash
codemind channel start [botId]
codemind channel dashboard
```

- 不传 `botId`：连接所有已注册机器人。
- 传入 `botId`：配置或启动指定机器人。

配置和使用方法见 [飞书 Bridge](lark-bridge/README.md)。

## 全自动模式

默认情况下，非琐碎的实现任务可能会在动代码前暂停一次，让你确认方向、范围、
风险和验证方式。

如果希望 CodeMind 不经过这次规划确认、直接持续执行到完成，可以在请求中加入
“全自动”“一站到底”“不用确认”：

```text
/codemind 修复登录崩溃并完成验证，全自动
```

```bash
codemind ask "修复登录崩溃并完成验证，一站到底"
```

全自动模式仍不会静默批准账号访问、支付、破坏性操作、影响生产的操作，以及
真实的设备、签名和权限门禁。

## 你会得到什么

每个任务都会在项目中获得一个持久目录：

```text
.automind/tasks/<task-code>/
```

主要交付包括：

- 实现或修复结果；
- 任务使用的需求和验证计划；
- 适用时的构建、测试、设备、UI 和日志证据；
- 失败、恢复过程和剩余 blocker 的记录；
- `Report.html`，建议优先打开它审查结果；
- 可供以后任务复用的成功与失败经验。

查看任务：

```bash
codemind status <task-code>
codemind report <task-code>
```

## 安全边界

CodeMind 追求高度自动化，但不会把每个失败或每条输入都当作修改机器的授权。

- 不会静默安装系统 SDK、签名材料、设备信任、特权服务或私有凭据。
- 敏感或不可逆操作需要明确授权。
- 环境、设备、签名和权限失败会被标记为 blocker，不会伪装成产品代码问题。
- 存在可运行路径时，App 和 UI 结论需要真实运行证据。
- 是否完成由证据检查决定，而不只相信 agent 的最后一句话。

## 常见问题

### `codemind: command not found`

把 wrapper 目录加入 shell profile，通常是：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

重启 shell 后运行 `codemind help`。

### 看不到 `/codemind`

安装后重启或 reload coding agent。

### 任务卡住或持续失败

先运行：

```bash
codemind status <task-code>
codemind resume <task-code>
```

状态输出会说明当前 blocker 和Home的下一步。更完整的诊断与验证命令见
[命令目录](docs/references/command-script-catalog.md)。

### 缺少移动端或 UI 工具

明确安装项目需要的平台工具后继续任务。CodeMind 可以准备自身的低风险 helper
包，但不会替你安装 Xcode、Android Studio、签名材料或设备信任设置。

## 深入了解

- [产品设计](automind_design.md) —— 为什么 CodeMind 强调 loop 和证据。
- [完整工作流](docs/workflow.md) —— 阶段、恢复和证据规则。
- [安装与运行环境](docs/references/installation-runtime.md) —— 安装路径、项目
  workspace 和前置条件。
- [飞书使用](lark-bridge/README.md) —— 连接和使用飞书机器人。
- [文档地图](docs/README.md) —— 所有高级和平台专项文档。

## CodeMind 不是什么

CodeMind 不是另一个 coding agent，不是项目原生测试或平台 SDK 的替代品，也不
保证每个任务都能自动解决。

它是围绕 coding agent 的工程闭环：规划、实现、验证、恢复、报告和复用。
