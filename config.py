"""
配置管理模块 —— 从 .env 文件读取所有配置
所有配置项都在这里集中管理，其他模块不要直接读环境变量
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path

# 项目根目录（config.py 所在目录）
PROJECT_ROOT = Path(__file__).parent


class Settings(BaseSettings):
    """
    全局配置，自动从 .env 文件加载
    用法：from config import settings
    """

    # ---------- 日志 ----------
    log_level: str = Field(default="INFO", description="日志级别")

    # ---------- 脉脉发帖 ----------
    maimai_post_interval: int = Field(
        default=150,
        description="脉脉发帖间隔基准（秒），实际会随机±30秒抖动防检测，默认150秒≈2.5分钟",
    )

    # ---------- 闪电观察者 ----------
    shandian_post_interval: int = Field(
        default=210,
        description="闪电观察者发帖间隔基准（秒），±30秒抖动，默认210秒≈3-4分钟",
    )

    # ---------- MultiPost 多平台发布 ----------
    multipost_post_interval: int = Field(
        default=90,
        description="MultiPost 发帖间隔基准（秒），±30秒抖动，默认90秒≈1.5分钟",
    )

    # ---------- DirectPublisher 直连发布 ----------
    direct_post_interval: int = Field(
        default=90,
        description="DirectPublisher 发帖间隔基准（秒），±30秒抖动，默认90秒≈1.5分钟",
    )

    # ---------- Pexels 搜图 ----------
    pexels_api_key: str = Field(
        default="",
        description="Pexels API Key（免费申请：https://www.pexels.com/api/）",
    )

    # ---------- AI 内容生成 ----------
    ai_api_key: str = Field(default="", description="AI 接口密钥")
    ai_model: str = Field(default="gpt-4o-mini", description="AI 模型名称")
    ai_base_url: str = Field(default="https://api.openai.com/v1", description="AI 接口地址")

    # ---------- 数据库 ----------
    db_path: str = Field(
        default="media_assistant.db",
        description="SQLite 数据库文件名",
    )

    @property
    def db_full_path(self) -> Path:
        """数据库完整路径（基于项目根目录）"""
        return PROJECT_ROOT / self.db_path

    class Config:
        # 从项目根目录的 .env 文件加载
        env_file = str(PROJECT_ROOT / ".env")
        env_file_encoding = "utf-8"


# 全局单例，其他模块直接 import 使用
settings = Settings()
