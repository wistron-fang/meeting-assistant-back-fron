-- 工作一 + 工作二：星级 / 配额列 + token 审计表
-- 用途：
--   1) 新数据卷首次启动时由 docker postgres 自动执行（init-db）。
--   2) 生产已有库（如托管 RDS）必须【手动】跑一次——create_all 只建新表、绝不给已有表加列。
--      psql "host=... dbname=meeting_assistant user=postgres" -f docker/init-db/02_quota.sql
-- 全部幂等，可重复执行。

-- users 加列（任务数 / token 配额均为「按月」窗口，与定稿数字一致）
ALTER TABLE users ADD COLUMN IF NOT EXISTS star_level             SMALLINT NOT NULL DEFAULT 4;
ALTER TABLE users ADD COLUMN IF NOT EXISTS max_concurrent         SMALLINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_task_quota     INT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_token_quota    BIGINT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_used_this_month BIGINT NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start     DATE;

-- task_usage 审计表（每个会议任务一条）
CREATE TABLE IF NOT EXISTS task_usage (
    id                BIGSERIAL PRIMARY KEY,
    task_id           INTEGER REFERENCES meeting_tasks(id) ON DELETE CASCADE,
    user_id           UUID    REFERENCES users(id),
    model             VARCHAR(64),
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens      INTEGER DEFAULT 0,
    calls             INTEGER DEFAULT 0,
    created_at        TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_task_usage_user ON task_usage(user_id);
CREATE INDEX IF NOT EXISTS ix_task_usage_task ON task_usage(task_id);
