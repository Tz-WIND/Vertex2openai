# Vertex2OpenAI

将 Google Cloud Vertex AI 上的 Claude 模型暴露为 **OpenAI 兼容 API**，并自动修正多模态请求中图片 `media_type` 不匹配的问题。

## ✨ 特性

- 🔄 **OpenAI API 兼容** — 任何支持 OpenAI API 的客户端（ChatGPT-Next-Web、LobeChat、Open WebUI 等）均可直接对接
- 🖼️ **图片格式自动修正** — 通过文件魔数（magic bytes）检测图片真实格式，自动修正 `media_type` 声明与实际内容不匹配的问题
- 🌊 **流式响应支持** — 完整支持 SSE 流式输出（`stream: true`）
- ⚡ **一键启动** — 自动生成 litellm 配置文件，单条命令即可运行
- 🛡️ **支持多种图片格式** — PNG、JPEG、GIF、WebP、SVG、AVIF

## 🎯 解决的问题

在使用 Vertex AI Claude 多模态能力时，部分客户端（如浏览器截图、剪贴板粘贴等）发送的图片 `media_type` 与实际编码格式不一致。例如：

- 客户端声明 `image/jpeg`，但实际数据是 PNG 格式
- 客户端声明 `image/png`，但实际数据是 WebP 格式

Claude Vertex AI API 对此校验较严，会直接返回错误。本项目通过在代理层自动检测并修正 MIME 类型，**彻底消除此类问题**。

## 📋 前置要求

- Python 3.10+
- Google Cloud 服务账号 JSON 凭证文件（具备 Vertex AI 访问权限）
- 已启用 Vertex AI API 的 GCP 项目

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install litellm fastapi uvicorn httpx pyyaml
```

### 2. 配置凭证

将你的 GCP 服务账号 JSON 密钥文件放置到 `config/` 目录下，并修改 `main.py` 中的硬编码配置：

```python
CREDENTIALS_PATH = Path("config") / "your-credentials.json"
PROJECT_ID = "your-gcp-project-id"
LOCATION = "global"                          # 或 "us-east5" 等具体区域
MODEL_NAME = "claude-sonnet-4-5@20250929"   # 可更换为其他支持的模型
```

### 3. 启动服务

```bash
python main.py
```

默认配置下：
- **外部代理端口**：`4000`（客户端连接此端口）
- **litellm 后端端口**：`4002`（内部通信，无需外部访问）

### 4. 连接使用

将你的 OpenAI 兼容客户端指向：

```
API Base URL: http://localhost:4000
```

## ⚙️ 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | `4000` | 外部暴露的代理端口 |
| `--backend-port` | `4002` | litellm 后端内部端口 |
| `--config` | `config.yaml` | litellm 配置文件路径 |
| `--host` | `0.0.0.0` | 绑定监听地址 |

**示例：**

```bash
# 自定义端口
python main.py --port 8080 --backend-port 8081

# 仅本地访问
python main.py --host 127.0.0.1
```

## 🏗️ 架构

```
┌─────────────────┐     ┌──────────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  OpenAI 客户端   │────▶│  FastAPI 代理中间件    │────▶│  litellm 后端     │────▶│  Vertex AI   │
│  (port 4000)    │◀────│  (图片 media_type 修正)│◀────│  (port 4002)     │◀────│  Claude API  │
└─────────────────┘     └──────────────────────┘     └──────────────────┘     └──────────────┘
```

1. **客户端** 发送 OpenAI 格式的请求到 FastAPI 代理
2. **FastAPI 代理** 拦截 `chat/completions` 请求，检测并修正图片 `media_type`
3. **litellm** 将 OpenAI 格式转换为 Vertex AI 格式并调用 Claude
4. 响应原路返回（支持流式和非流式）

## 🖼️ 支持的图片格式检测

| 格式 | MIME 类型 | 检测方式 |
|------|-----------|----------|
| PNG | `image/png` | `\x89PNG\r\n\x1a\n` 魔数 |
| JPEG | `image/jpeg` | `\xff\xd8\xff` 魔数 |
| GIF | `image/gif` | `GIF87a` / `GIF89a` 魔数 |
| WebP | `image/webp` | `RIFF....WEBP` 魔数 |
| SVG | `image/svg+xml` | `<svg` 标签检测 |
| AVIF | `image/avif` | `\x00\x00\x00` ftyp box 检测 |

## 📁 项目结构

```
vertex2openai/
├── main.py              # 主程序（代理 + litellm 启动器）
├── config/
│   └── *.json           # GCP 服务账号凭证文件
├── config.yaml          # 自动生成的 litellm 配置（首次运行后生成）
└── README.md
```

## 🔧 高级配置

### 使用自定义 litellm 配置

如果你需要更复杂的 litellm 配置（多模型、负载均衡、限速等），可以手动创建 `config.yaml`：

```yaml
model_list:
  - model_name: claude-sonnet-4-5
    litellm_params:
      model: vertex_ai/claude-sonnet-4-5@20250929
      vertex_project: your-project-id
      vertex_location: global
      vertex_credentials: config/your-credentials.json

  - model_name: claude-haiku-3-5
    litellm_params:
      model: vertex_ai/claude-3-5-haiku@20241022
      vertex_project: your-project-id
      vertex_location: us-east5
      vertex_credentials: config/your-credentials.json

litellm_settings:
  modify_params: true
```

程序检测到已有配置文件时会直接使用，不会覆盖。

### Docker 部署

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir litellm fastapi uvicorn httpx pyyaml

EXPOSE 4000
CMD ["python", "main.py"]
```

```bash
docker build -t vertex2openai .
docker run -p 4000:4000 -v ./config:/app/config vertex2openai
```

## 📝 日志示例

当图片格式被修正时，控制台会输出：

```
[image-fix] 修正 media_type: image/jpeg -> image/png
[image-fix] 共修正 1 处图片 media_type
```

## ⚠️ 注意事项

- 请确保 GCP 服务账号具有 `Vertex AI User` 角色权限
- `LOCATION` 设置为 `global` 可自动路由，也可指定具体区域如 `us-east5`、`europe-west1` 等
- 凭证文件请勿提交到版本控制，建议将 `config/` 目录添加到 `.gitignore`

## 📄 License

MIT License
