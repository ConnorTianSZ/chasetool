"""
配置加载：优先读 .env，再读环境变量
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM — 通用配置，支持任意 API provider
    api_key: str = ""                # 通用 API key（兼容 OpenAI / Anthropic / 其他）
    api_base: str = ""               # 自定义 API 端点（OpenAI 兼容格式）
    llm_model: str = ""              # 模型名，默认由各 provider 决定
    llm_provider: str = "anthropic"  # anthropic / openai / custom

    # 保留向后兼容
    anthropic_api_key: str = ""

    # Outlook
    outlook_profile: str = ""
    inbox_folder: str = "Inbox"
    inbound_pull_days: int = 14

    # 路径
    data_dir: str = "./data"
    excel_default_dir: str = ""

    # 行为
    chase_default_mode: str = "draft"   # draft / send
    send_interval_seconds: int = 2
    timezone: str = "Asia/Shanghai"

    # 代理
    https_proxy: str = ""
    http_proxy: str = ""

    # 服务
    host: str = "127.0.0.1"
    port: int = 8000


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        # 强制写入代理环境变量（不用 setdefault，确保 .env 中的值始终生效）
        # httpx / requests / openai-sdk 等库均从这些变量自动读取代理
        import os
        proxy = _settings.https_proxy or _settings.http_proxy
        if proxy:
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["HTTP_PROXY"]  = proxy
            os.environ["https_proxy"] = proxy
            os.environ["http_proxy"]  = proxy
            # Bosch 内网地址绕过代理
            os.environ.setdefault(
                "NO_PROXY",
                "localhost,127.0.0.1,*.bosch.com,10.*,192.168.*",
            )
    return _settings
