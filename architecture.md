# Architecture

## 整体结构

本项目由三个独立运行的组件构成：

```
浏览器
  │  HTTP  :3000
  ▼
┌──────────────────────────────┐
│  Open WebUI                  │  Docker 容器，监听内部 8080，映射到宿主机 3000
│  前端（SvelteKit）            │  负责：用户界面、账号系统、对话历史持久化（SQLite）
│  后端（FastAPI）              │
└──────────────┬───────────────┘
               │  HTTP  host.docker.internal:8001
               │  POST /chat/completions
               ▼
┌──────────────────────────────┐
│  adapter.py                  │  运行在宿主机，监听 0.0.0.0:8001
│  协议适配层                   │  负责：OpenAI 协议 ↔ Claude Agent SDK 协议转换
└──────────────┬───────────────┘
               │  Claude Agent SDK（Python 方法调用）
               ▼
┌──────────────────────────────┐
│  ClaudeSDKClient             │  Agent 运行时
│  phases.py                   │  负责：阶段状态管理、系统提示词、工具权限控制
└──────────────┬───────────────┘
               │  HTTPS（Anthropic 兼容 API）
               ▼
┌──────────────────────────────┐
│  MiniMax API                 │  远程模型服务（MiniMax-M3）
└──────────────────────────────┘
```

---

## 组件职责边界

### Open WebUI
Open WebUI 是完整的全栈应用，本项目不修改其源码，只将其作为现成的用户界面使用。它负责的内容与 Agent 逻辑完全无关：

- 用户注册、登录、权限
- 对话历史的存储与展示（SQLite，挂载到宿主机持久化）
- 每次用户发消息时，将完整对话历史打包成 OpenAI 格式请求发给 adapter
- 将 adapter 返回的 SSE 流渲染为打字机效果

Open WebUI 不感知 Agent 的存在，它只认为自己在调用一个"OpenAI 兼容模型"。

### adapter.py
adapter 是本项目唯一需要开发的后端文件。它是一个**无状态的协议转换层**，不持有任何业务逻辑。

**入方向（Open WebUI → SDK）：**
Open WebUI 发来的请求体符合 OpenAI 规范：
```json
{
  "model": "BioAgent",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "当前消息"}
  ],
  "stream": true
}
```
adapter 将 `messages` 数组拼接成完整的对话上下文字符串，作为 `prompt` 传给 SDK：
```
USER: ...
ASSISTANT: ...
USER: 当前消息
```
这样 Agent 每次都能看到完整历史，实现多轮对话。

**出方向（SDK → Open WebUI）：**
SDK 以 Python 对象流的形式吐出响应：
```
StreamEvent(event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你"}})
```
adapter 将每个文字 delta 包装成 OpenAI SSE 格式：
```
data: {"choices": [{"delta": {"content": "你"}}]}\n\n
```
通过 `StreamingResponse` 实时推送给 Open WebUI，Open WebUI 将其渲染为打字机效果。

**命令拦截：**
adapter 在转发之前检查最后一条用户消息，拦截特殊命令：
- `/execute` → 调用 `phases.switch_to_execute()`，返回确认消息，不走 Agent
- `/plan` → 调用 `phases.switch_to_plan()`，返回确认消息，不走 Agent

### phases.py
维护 Agent 的运行时状态。核心是全局变量 `phase`（`"plan"` 或 `"execute"`），以及对应的系统提示词。

- `system_prompt()` — 根据当前 phase 返回不同的系统提示词，adapter 每次构建 `ClaudeAgentOptions` 时调用
- `switch_to_execute()` / `switch_to_plan()` — 切换 phase
- `make_permission_handler()` — 为 WebSocket 版本保留，adapter 版本使用 `auto_allow`（自动放行所有工具）

---

## 关键设计决策

### 为什么用 OpenAI 兼容接口而不是自建前端

Open WebUI 提供了现成的账号系统、对话历史持久化、文件上传、UI 渲染。自建前端需要重新实现所有这些功能。adapter 只需 ~80 行代码即可接入。

### 为什么不直接让 Open WebUI 调用 MiniMax API

Open WebUI 可以直接配置任意 OpenAI 兼容的模型 API，但这样会绕过 Agent 运行时（`ClaudeSDKClient`），失去两阶段工作流、工具调用、系统提示词控制等所有 Agent 能力。adapter 是让 Agent 逻辑对 Open WebUI 透明的必要中间层。

### 为什么 adapter 是无状态的

每次请求 Open WebUI 都会带来完整的对话历史（`messages`），因此 adapter 不需要自己维护会话状态。对话持久化由 Open WebUI 的数据库负责，adapter 只做一次性的协议转换。

### 端口规则

| 端口 | 绑定方 | 访问方 |
|------|--------|--------|
| 3000 | Open WebUI（宿主机映射） | 浏览器 |
| 8080 | Open WebUI（容器内部） | Docker 内部 |
| 8001 | adapter.py（`0.0.0.0`） | Open WebUI 容器、浏览器 |

adapter 必须绑定 `0.0.0.0` 而非 `localhost`，否则 Docker 容器无法通过 `host.docker.internal` 访问到它。

---

## 数据流完整时序

```
用户在浏览器输入消息并发送
    │
    ▼
Open WebUI 前端
    从 SQLite 取出历史对话
    拼成 messages 数组
    POST /chat/completions → adapter:8001
    │
    ▼
adapter.py
    拦截 /execute /plan 命令（如有）
    将 messages 拼成 prompt 字符串
    构建 ClaudeAgentOptions（phase、model、auto_allow）
    启动 ClaudeSDKClient
    client.query(prompt)
    │
    ▼
ClaudeSDKClient
    携带 system_prompt + prompt 请求 MiniMax API
    │
    ▼
MiniMax API
    流式返回 token
    │
    ▼
ClaudeSDKClient
    封装为 StreamEvent 对象流
    │
    ▼
adapter.py
    解包 StreamEvent → 提取 text delta
    包装为 OpenAI SSE 格式
    yield 给 StreamingResponse
    │
    ▼
Open WebUI
    接收 SSE 流
    实时追加到聊天气泡（打字机效果）
    对话结束后写入 SQLite
    │
    ▼
用户看到回复
```
