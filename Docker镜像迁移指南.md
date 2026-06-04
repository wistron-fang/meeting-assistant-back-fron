# Docker 镜像迁移指南

> 适用场景:把 `postgres:15-alpine` + `redis:7-alpine` 两个镜像离线带到另一台 Windows 电脑,
> 让目标机器 **不联网** 也能跑起整套会议纪要项目。

---

## 一、原理

Docker 镜像本质就是个分层的 tar 包。`docker save` 把镜像导成 tar,
`docker load` 在目标机器上把 tar 重新注册成镜像。导完后:

- 目标机器执行 `docker compose up` 时,因为本地已经有同名同 tag 的镜像,
  **不会触发网络拉取**。
- 不需要 Docker Hub、不需要镜像加速器、不需要内网 registry。

---

## 二、在源机器导出 (你现在这台电脑)

### 前置:Docker Desktop 必须在运行

任务栏托盘里能看到鲸鱼图标 (Docker Desktop) 并且是 "Engine running" 状态。
如果没启动,双击开始菜单的 Docker Desktop,等到鲸鱼图标稳定显示。

```powershell
docker version
```

底部 `Server:` 那一段能正常显示版本号,就是 OK。如果报
`error during connect: ... open //./pipe/dockerDesktopLinuxEngine`,
说明 Docker 引擎还没就绪,等 30 秒再试。

### 步骤 1:确认两个镜像在本地

```powershell
docker images | findstr /R "postgres redis"
```

应该看到:

```
postgres   15-alpine   xxxxxxxxx   ...   ~200MB
redis      7-alpine    xxxxxxxxx   ...   ~40MB
```

如果某个不在,先 `docker pull postgres:15-alpine` / `docker pull redis:7-alpine` 拉下来。

### 步骤 2:导出成单个 tar

```powershell
cd E:\meeting_minutes_assistant
docker save -o meeting-images.tar postgres:15-alpine redis:7-alpine
```

- `-o` 指定输出文件名
- 后面跟多个镜像名,会合并打到同一个 tar
- 跑完没回显,但文件已经写好,需要 30-60 秒

### 步骤 3:验证 tar 文件

```powershell
dir meeting-images.tar
```

文件大小应该是 **220-260 MB** 量级。

可以列一下 tar 里有哪些镜像 (可选):

```powershell
docker load -i meeting-images.tar --quiet
# 这步会输出 Loaded image: 的两行,因为本机已有所以是幂等的
```

---

## 三、拷贝到目标机器

`meeting-images.tar` 一个文件就够,**不要解压**。任意方式都行:

- U 盘 (最常用)
- 共享网盘
- 局域网共享文件夹
- SCP / Robocopy / 微信文件传输

放到目标机器任意目录,比如 `D:\` 根目录或桌面。

---

## 四、在目标机器导入

### 前置:目标机器装好 Docker Desktop 并启动

如果还没装:https://www.docker.com/products/docker-desktop
装完启动一次,等鲸鱼图标稳定。

### 步骤 4:加载 tar

```powershell
cd D:\          # 或者你放 tar 的目录
docker load -i meeting-images.tar
```

成功输出:

```
Loaded image: postgres:15-alpine
Loaded image: redis:7-alpine
```

### 步骤 5:验证

```powershell
docker images | findstr /R "postgres redis"
```

应该看到这两行,**不需要任何网络**就能用了。

### 步骤 6:启动项目容器

把项目代码也拷过去 (`E:\meeting_minutes_assistant\` 整个目录,排除
`node_modules` / `data` / `__pycache__`)。然后:

```powershell
cd <项目目录>
docker compose up -d
```

Docker Compose 会看到 `docker-compose.yml` 里要求 `postgres:15-alpine`
和 `redis:7-alpine`,本地有,**直接起容器,不去网络拉**。

`docker ps` 看到 `meeting_postgres` + `meeting_redis` 都 `(healthy)` 就成功了。

---

## 五、常见问题

### Q1: 目标机器架构不一样(x86 → ARM,或反过来)能用吗?

**不能**。`docker save` 出来的是平台专属的二进制。
源机器和目标机器 CPU 架构必须一致 (一般都是 amd64,问题不大)。
要跨架构,得用 `docker buildx` 重新构建,或者用 multi-arch 的镜像并用
`--platform` 显式指定。

### Q2: 导出的 tar 能不能压缩一下?

可以:

```powershell
# 导出后 gzip 压缩 (在 Windows 上需要 7-Zip 或 WSL 里的 gzip)
# 不过 docker save 出来的 tar 本身已经是分层 tar.gz 套层,压缩比有限
# 简单的话直接拷 tar 原文件就行
```

### Q3: 想顺便把当前数据库的内容也带过去?

镜像本身**不含数据**,数据存在 docker volume 里。导数据要单独操作:

```powershell
# 源机器: 把 postgres 数据卷打包
docker compose stop postgres
docker run --rm `
    -v meeting_minutes_assistant_postgres_data:/data `
    -v ${PWD}:/backup `
    alpine tar czf /backup/pg-data.tar.gz -C /data .
docker compose start postgres

# 目标机器: 恢复
docker compose stop postgres
docker run --rm `
    -v meeting_minutes_assistant_postgres_data:/data `
    -v ${PWD}:/backup `
    alpine sh -c "cd /data && tar xzf /backup/pg-data.tar.gz"
docker compose start postgres
```

Redis 同理,把卷名换成 `meeting_minutes_assistant_redis_data` 即可。

### Q4: 加载后镜像 tag 显示 `<none>`?

说明源机器导出时镜像没打 tag,或导出命令用的是 image ID 不是 name:tag。
解决:在源机器用完整的 `镜像名:tag` 形式 (如 `postgres:15-alpine`) 导出。

### Q5: 镜像更新了,要重新导一次怎么办?

`docker pull` 拉新版后,重复步骤 1-2 就行。`docker load` 在目标机器上
会覆盖同名同 tag 的旧镜像。

---

## 六、命令速查 (一页纸)

| 操作 | 命令 |
|---|---|
| 源机器导出 | `docker save -o meeting-images.tar postgres:15-alpine redis:7-alpine` |
| 目标机器导入 | `docker load -i meeting-images.tar` |
| 查看镜像 | `docker images` |
| 删除镜像 (清理用) | `docker rmi postgres:15-alpine redis:7-alpine` |
| 起容器 | `docker compose up -d` |
| 停容器 (保留数据) | `docker compose stop` |
| 删容器 (保留数据) | `docker compose down` |
| 删容器 **+** 数据 | `docker compose down -v` ⚠️ |
