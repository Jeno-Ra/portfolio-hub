from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent  # ...\backend\app
PROJECT_ROOT = APP_DIR.parent.parent  # ...\portfolio-hub


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout: int = 5
    llm_total_budget: int = 30

    # 向量化服务（硅基流动，OpenAI 兼容接口）
    embedding_api_key: str
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024  # bge-m3 输出维度，换模型必须同步改并重建向量库
    embedding_batch: int = 32  # 每次请求提交多少条，太大容易被限流

    # 视觉服务（图片 → 文字描述），走 OpenAI 兼容接口
    gpt_api_key: str
    gpt_base_url: str = "https://api.openai.com/v1"
    gpt_model: str = "gpt-6.1-terra"
    vision_timeout: int = 120  # 单张图 4–5 秒，留足余量给大图

    # 数据落盘位置。留空 = 用 backend/ 下的默认路径（本地开发）。
    # 容器里由 compose 注入 /app/data/...，这样数据在命名卷里，重建容器不丢。
    db_path: str = ""
    chroma_dir: str = ""

    # 关系库连接串。留空 = 用本地 SQLite（开发默认）；
    # 容器里由 compose 注入 postgresql+psycopg://... 指向 postgres 服务。
    database_url: str = ""


settings = Settings()
