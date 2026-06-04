# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

# 确保 task 被 Celery 注册
from . import tasks  # noqa: F401
from . import cleanup  # noqa: F401
