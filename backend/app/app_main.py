# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import logging

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from router.auth_router import router as auth_router
from router.meeting_router import router as meeting_router
from core.database import engine, Base
# 导入所有模型以确保它们被注册
from models import User, MeetingTask

# 创建所有数据表（如果不存在）
Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("应用启动中...")
    yield
    logger.info("应用关闭中...")


app = FastAPI(
    title="会议纪要助手 API",
    description="基于 AI 的会议纪要自动生成系统",
    version="1.0.0",
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有源，生产环境中应该设置具体的源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
    expose_headers=["Content-Disposition"],  # 让前端 JS 能读到下载文件名
)

# 注册路由
app.include_router(auth_router)
app.include_router(meeting_router)


@app.get("/hello")
async def hello_world():
    """
    Simple hello world endpoint for network verification
    """
    return {
        "status": "success",
        "message": "Hello World! The API is working correctly."
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8100)
