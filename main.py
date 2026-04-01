#!/usr/bin/env python3
"""
一键启动 litellm 代理，将 Vertex AI Claude 模型暴露为 OpenAI API。
在 litellm 前增加一层中间件，自动检测并修正图片 media_type 不匹配的问题。
"""

import os
import sys
import argparse
import subprocess
import signal
import yaml
import shutil
import base64
import threading
import time
import httpx
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

# ========== 硬编码配置 ==========
CREDENTIALS_PATH = Path("config") / ""
PROJECT_ID = ""
LOCATION = "global"
MODEL_NAME = ""
# ===============================

# 图片格式魔数检测
IMAGE_SIGNATURES = [
    (b'\x89PNG\r\n\x1a\n',       "image/png"),
    (b'\xff\xd8\xff',             "image/jpeg"),
    (b'GIF87a',                   "image/gif"),
    (b'GIF89a',                   "image/gif"),
    (b'RIFF',                     "image/webp"),   # RIFF....WEBP, 进一步校验在下面
    (b'<svg',                     "image/svg+xml"),
    (b'\x00\x00\x00',            "image/avif"),    # ftyp box, 需要进一步校验
]

def detect_image_type(b64_data: str) -> str | None:
    """通过 base64 解码后的前几个字节检测真实图片格式"""
    try:
        # 只解码前 32 字节就够判断了，避免解码整个大图
        # base64 每 4 字符 = 3 字节，取前 48 字符足够
        partial = b64_data[:48]
        # 补齐 padding
        padding = 4 - len(partial) % 4
        if padding != 4:
            partial += '=' * padding
        raw = base64.b64decode(partial)
    except Exception:
        return None

    for sig, mime in IMAGE_SIGNATURES:
        if raw.startswith(sig):
            # WEBP 需要额外校验第 8-12 字节
            if mime == "image/webp":
                if len(raw) >= 12 and raw[8:12] == b'WEBP':
                    return mime
                continue
            return mime
    return None


def fix_image_media_types(messages: list) -> int:
    """
    遍历 messages，找到所有 image_url 类型的 content block，
    检测 base64 图片的真实格式并修正 media_type。
    返回修正的数量。
    """
    fixed_count = 0
    if not messages:
        return 0

    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue

            # OpenAI 格式: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
            if block.get("type") == "image_url":
                image_url = block.get("image_url", {})
                url = image_url.get("url", "")
                if url.startswith("data:") and ";base64," in url:
                    header, b64_data = url.split(";base64,", 1)
                    declared_mime = header.split("data:", 1)[1]
                    actual_mime = detect_image_type(b64_data)
                    if actual_mime and actual_mime != declared_mime:
                        image_url["url"] = f"data:{actual_mime};base64,{b64_data}"
                        fixed_count += 1
                        print(f"[image-fix] 修正 media_type: {declared_mime} -> {actual_mime}")

            # Anthropic 原生格式: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
            elif block.get("type") == "image":
                source = block.get("source", {})
                if source.get("type") == "base64":
                    declared_mime = source.get("media_type", "")
                    b64_data = source.get("data", "")
                    actual_mime = detect_image_type(b64_data)
                    if actual_mime and actual_mime != declared_mime:
                        source["media_type"] = actual_mime
                        fixed_count += 1
                        print(f"[image-fix] 修正 media_type: {declared_mime} -> {actual_mime}")

    return fixed_count


# ========== FastAPI 中间件代理 ==========

LITELLM_BACKEND = None  # 运行时设置

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    yield
    await app.state.client.aclose()

app = FastAPI(lifespan=lifespan)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy(request: Request, path: str):
    """拦截所有请求，对 chat/completions 做图片修正，其余直接转发"""
    client: httpx.AsyncClient = request.app.state.client
    target_url = f"{LITELLM_BACKEND}/{path}"

    body = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)

    # 只对 chat completions 请求做图片修正
    if request.method == "POST" and ("chat/completions" in path):
        try:
            import json
            payload = json.loads(body)
            messages = payload.get("messages", [])
            fixed = fix_image_media_types(messages)
            if fixed > 0:
                print(f"[image-fix] 共修正 {fixed} 处图片 media_type")
                body = json.dumps(payload).encode("utf-8")
        except (json.JSONDecodeError, Exception) as e:
            print(f"[image-fix] 解析请求体失败，跳过修正: {e}")

    # 检查是否需要流式响应
    is_stream = False
    if request.method == "POST" and body:
        try:
            import json
            req_json = json.loads(body)
            is_stream = req_json.get("stream", False)
        except Exception:
            pass

    if is_stream:
        # 流式转发
        req = client.build_request(
            method=request.method,
            url=target_url,
            content=body,
            headers=headers,
            params=dict(request.query_params),
        )
        upstream_resp = await client.send(req, stream=True)

        async def stream_generator():
            try:
                async for chunk in upstream_resp.aiter_bytes():
                    yield chunk
            finally:
                await upstream_resp.aclose()

        return StreamingResponse(
            stream_generator(),
            status_code=upstream_resp.status_code,
            headers=dict(upstream_resp.headers),
        )
    else:
        # 普通转发
        resp = await client.request(
            method=request.method,
            url=target_url,
            content=body,
            headers=headers,
            params=dict(request.query_params),
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )


# ========== litellm 配置与启动 ==========

def generate_config(credentials_path, project_id, location, model_name, output_path):
    config = {
        "model_list": [
            {
                "model_name": model_name.split('@')[0],
                "litellm_params": {
                    "model": f"vertex_ai/{model_name}",
                    "vertex_project": project_id,
                    "vertex_location": location,
                    "vertex_credentials": str(credentials_path),
                }
            }
        ],
        "litellm_settings": {
            "modify_params": True,
        }
    }
    with open(output_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    return output_path


def start_litellm_backend(config_path, port, host="127.0.0.1"):
    """启动 litellm 作为后端进程"""
    cmd = [
        "litellm",
        "--config", config_path,
        "--port", str(port),
        "--host", host,
    ]
    process = subprocess.Popen(cmd)
    return process


def main():
    parser = argparse.ArgumentParser(description="Start litellm proxy with image media_type auto-fix")
    parser.add_argument("--port", type=int, default=4000, help="外部暴露端口 (default: 4000)")
    parser.add_argument("--backend-port", type=int, default=4002, help="litellm 后端端口 (default: 4002)")
    parser.add_argument("--config", type=str, default="config.yaml", help="litellm 配置文件路径")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址 (default: 0.0.0.0)")
    args = parser.parse_args()

    global LITELLM_BACKEND
    LITELLM_BACKEND = f"http://127.0.0.1:{args.backend_port}"

    # 检查凭证
    if not CREDENTIALS_PATH.exists():
        print(f"Error: Credentials file not found: {CREDENTIALS_PATH.absolute()}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("litellm"):
        print("Error: litellm not found. pip install litellm", file=sys.stderr)
        sys.exit(1)

    # 生成配置
    config_path = args.config
    if not os.path.exists(config_path):
        print(f"生成配置文件: {config_path}")
        generate_config(CREDENTIALS_PATH, PROJECT_ID, LOCATION, MODEL_NAME, config_path)
    else:
        print(f"使用已有配置: {config_path}")

    # 启动 litellm 后端
    print(f"启动 litellm 后端于 127.0.0.1:{args.backend_port}...")
    litellm_process = start_litellm_backend(config_path, args.backend_port)

    def cleanup(sig=None, frame=None):
        print("\n正在关闭...")
        litellm_process.terminate()
        try:
            litellm_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            litellm_process.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # 等一下让 litellm 启动
    print(f"等待 litellm 后端就绪...")
    time.sleep(3)

    # 启动 FastAPI 代理
    print(f"启动图片修正代理于 {args.host}:{args.port}...")
    print(f"客户端请连接: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止")

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        cleanup()


if __name__ == "__main__":
    main()
