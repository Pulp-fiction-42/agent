# BioAgent

Virus Classify Agent 

---

## 架构

```
浏览器
  │  http://localhost:3000
  ▼
Open WebUI（Docker）
  │  POST /chat/completions
  │  http://host.docker.internal:8001
  ▼
adapter.py（宿主机 :8001）
  │  Claude Agent SDK
  ▼
MiniMax API（远程）
```

---

## 文件结构

```
agent_sdk/
├── adapter.py     # OpenAI 兼容接口，协议转换层
├── phases.py      # Agent 阶段状态、系统提示词
└── README.md
```

**`adapter.py`**
接收 Open WebUI 发来的 OpenAI 格式请求，转交给 `ClaudeSDKClient`，将流式响应转换回 OpenAI SSE 格式返回。

**`phases.py`**
维护当前阶段（`plan` / `execute`），提供对应的系统提示词和阶段切换函数。

---

## 启动

**1. 启动 adapter**
```bash
cd /path/to/agent_sdk
export ANTHROPIC_AUTH_TOKEN="你的 MiniMax API Key"
uvicorn adapter:app --port 8001 --host 0.0.0.0
```
验证：浏览器打开 `http://localhost:8001/v1/models`，看到 BioAgent 即成功。

**2. 启动 Open WebUI**
```bash
docker run -d -p 3000:8080 \
  -v ~/.open-webui:/app/backend/data \
  ghcr.io/open-webui/open-webui:main
```
等约 1-2 分钟后打开 `http://localhost:3000`。

**3. 连接 adapter**

Settings → Connections → OpenAI API → 齿轮图标：
- URL: `http://host.docker.internal:8001`
- Key: 任意字符

保存后模型下拉框出现 BioAgent 即连通。

---

## 使用

**阶段切换**

| 命令 | 效果 |
|------|------|
| `/execute` | 切换到执行阶段，工具启用 |
| `/plan` | 切换回规划阶段，工具禁用 |

**文件上传**

直接在 Open WebUI 输入框旁点附件图标上传文件，Open WebUI 会自动将文件内容提取并附加到消息中发给 Agent。

**对话历史**

挂载 `-v ~/.open-webui:/app/backend/data` 后，对话历史持久化到本机，重启容器不丢失，侧边栏可查看历史会话。

---

## 依赖

```bash
pip install claude-agent-sdk fastapi uvicorn
```

- Python 3.10+
- Docker
- `ANTHROPIC_AUTH_TOKEN` 或 `MINIMAX_API_KEY` 环境变量
