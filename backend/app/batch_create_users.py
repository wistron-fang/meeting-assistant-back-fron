# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
批量建用户 CLI。用户名 = 姓名拼音，邮箱 = 拼音@gigba.org.cn，强随机密码。

在 meeting 环境、/opt/meeting/backend/app 下运行：
    /root/miniconda3/envs/meeting/bin/python batch_create_users.py

行为：
- 逐个建号；用户名或邮箱已存在则【跳过】（可重复运行，不会重复建）。
- 密码现场随机生成（14 位，含大小写/数字/符号），明文只在本次输出。
- 同时把「姓名,用户名,邮箱,密码」写入 created_users.csv（请下载后妥善保存并删除该文件）。

调整：改下面 USERS 列表 / DOMAIN / STAR / gen_password 长度即可。
"""
import csv
import secrets
import string

from core.database import SessionLocal
from core.security import get_password_hash
from models.user import User

DOMAIN = "gigba.org.cn"
STAR = 4              # 普通用户默认星级
CSV_PATH = "created_users.csv"

# (拼音用户名, 中文姓名)
USERS = [
    ("fangyuxin", "方玉欣"),
    ("jianghan", "姜涵"),
    ("keyian", "柯怡安"),
    ("lijiahao", "李嘉豪"),
    ("lijiaqi", "李嘉琪"),
    ("limingbo", "李明波"),
    ("linkaizhao", "林铠钊"),
    ("liuaiwen", "刘艾雯"),
    ("liucaiyan", "刘彩燕"),
    ("liulilan", "刘利兰"),
    ("panxuanming", "潘炫明"),
    ("shiyongli", "史永丽"),
    ("suzhou", "苏舟"),
    ("wangyanli", "王艳丽"),
    ("xurun", "徐润"),
    ("yanxing", "严兴"),
    ("yida", "易达"),
    ("zhangxia", "张霞"),
    ("zhongxuan", "钟煊"),
    ("zhuyuanbing", "朱元冰"),
    ("caowu", "曹武"),
    ("chenxi", "陈希"),
]

LOWERS = string.ascii_lowercase
UPPERS = string.ascii_uppercase
DIGITS = string.digits
SYMBOLS = "!@#%^*-_=+"
ALPHABET = LOWERS + UPPERS + DIGITS + SYMBOLS


def gen_password(n: int = 14) -> str:
    """生成强密码：保证四类字符各至少一个。"""
    while True:
        pw = "".join(secrets.choice(ALPHABET) for _ in range(n))
        if (any(c in LOWERS for c in pw)
                and any(c in UPPERS for c in pw)
                and any(c in DIGITS for c in pw)
                and any(c in SYMBOLS for c in pw)):
            return pw


def main() -> None:
    db = SessionLocal()
    created = []   # (中文, 用户名, 邮箱, 密码)
    skipped = []   # (用户名, 中文, 原因)
    try:
        for pinyin, cn in USERS:
            username = pinyin
            email = f"{pinyin}@{DOMAIN}"
            dup = db.query(User).filter(
                (User.username == username) | (User.email == email)
            ).first()
            if dup:
                skipped.append((username, cn, f"已存在(username={dup.username}, email={dup.email})"))
                continue
            pw = gen_password()
            u = User(
                username=username,
                email=email,
                hashed_password=get_password_hash(pw),
                star_level=STAR,
                is_superuser=False,
                is_active=True,
            )
            db.add(u)
            db.commit()
            created.append((cn, username, email, pw))

        # 写 CSV
        if created:
            with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["姓名", "用户名", "邮箱", "初始密码"])
                w.writerows(created)

        # 终端输出
        print("\n==== 创建成功（密码仅此一次，请保存）====")
        print("姓名\t用户名\t邮箱\t初始密码")
        for cn, un, em, pw in created:
            print(f"{cn}\t{un}\t{em}\t{pw}")
        if skipped:
            print("\n==== 跳过（已存在）====")
            for un, cn, why in skipped:
                print(f"{cn} ({un}): {why}")
        print(f"\n共建 {len(created)} 个，跳过 {len(skipped)} 个。")
        if created:
            print(f"清单已写入：{CSV_PATH}（下载保存后请删除）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
