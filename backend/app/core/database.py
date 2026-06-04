# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""数据库连接和会话管理"""
import os
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 防御性加载 .env：当本模块被 celery worker 等非 uvicorn 入口加载时，
# 确保 POSTGRES_* 环境变量已就绪（否则会落到默认端口 5432 连不上新项目的 5532）
try:
    from dotenv import load_dotenv
    _here = os.path.dirname(os.path.abspath(__file__))
    for _env in (
        os.path.normpath(os.path.join(_here, "..", "..", ".env")),  # backend/.env
        os.path.normpath(os.path.join(_here, "..", ".env")),        # backend/app/.env
    ):
        if os.path.isfile(_env):
            load_dotenv(_env)
            break
except ImportError:
    pass

# 从环境变量获取数据库配置
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5532")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres123")
POSTGRES_DB = os.getenv("POSTGRES_DB", "meeting_assistant")

DATABASE_URL = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"

# 连接池走环境变量。PG max_connections 实测仅 70（RDS 2G 默认），预算很紧：
#   - API（uvicorn）：QueuePool 小池抗轮询。轮询是短查询，瞬时并发连接很低，池不必大。
#   - worker（celery -c 32）：用 NullPool（DB_POOL_MODE=null），用完即关、不留 idle。
#     否则 QueuePool 每个 prefork 子进程留 1 条 idle ×32 ≈ 32 条白占掉大半 max_connections。
# 总连接 ≈ API进程数 ×(池+溢出) + worker 瞬时并发 + RDS 自身(~12)，必须 < 70。
DB_POOL_MODE = os.getenv("DB_POOL_MODE", "queue")  # "null" 给 worker
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

if DB_POOL_MODE == "null":
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, poolclass=NullPool)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_recycle=DB_POOL_RECYCLE,
    )
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """获取数据库会话的依赖函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
