# Vertex2openai
Vertex2OpenAI 是一个轻量级代理服务，将 Google Cloud Vertex AI 上的 Claude 模型转换为 OpenAI 兼容的 API 接口。它基于 litellm 构建，并在其之上增加了一层图片 media_type 自动修正中间件，解决了在多模态对话中因客户端声明的图片 MIME 类型与实际图片格式不匹配而导致 API 调用失败的痛点问题。
