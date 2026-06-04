# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
管理员建用户 CLI（自助注册关闭后用这个建号）。

在 meeting 环境、/opt/meeting/backend/app 下运行：
    python admin_create_user.py <用户名> <邮箱> <密码> [--star 4] [--superuser]

例：
    python admin_create_user.py alice alice@corp.com 'Init@1234'
    python admin_create_user.py admin admin@corp.com 'Strong@Pwd' --star 5 --superuser

自动处理：bcrypt 密码哈希、UUID、星级默认、用户名/邮箱查重。
"""
import sys
import argparse

from core.database import SessionLocal
from core.security import get_password_hash
from models.user import User


def main() -> None:
    ap = argparse.ArgumentParser(description="管理员建用户")
    ap.add_argument("username", help="用户名（≥3 字符，唯一）")
    ap.add_argument("email", help="邮箱（唯一）")
    ap.add_argument("password", help="初始密码（≥6 字符）")
    ap.add_argument("--star", type=int, default=4, help="星级 1~5，默认 4")
    ap.add_argument("--superuser", action="store_true", help="设为管理员")
    a = ap.parse_args()

    if len(a.username) < 3:
        print("用户名至少 3 个字符"); sys.exit(1)
    if len(a.password) < 6:
        print("密码至少 6 个字符"); sys.exit(1)
    if a.star not in (1, 2, 3, 4, 5):
        print("星级必须是 1~5"); sys.exit(1)

    db = SessionLocal()
    try:
        dup = db.query(User).filter(
            (User.username == a.username) | (User.email == a.email)
        ).first()
        if dup:
            print(f"已存在同名用户名或邮箱（username={dup.username}, email={dup.email}），放弃")
            sys.exit(1)

        u = User(
            username=a.username,
            email=a.email,
            hashed_password=get_password_hash(a.password),
            star_level=a.star,
            is_superuser=a.superuser,
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        print(f"已创建: id={u.id} username={u.username} star={u.star_level} superuser={u.is_superuser}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
