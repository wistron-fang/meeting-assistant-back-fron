/**
 * 会议纪要助手 - 本次迭代总结文档
 * 风格参考：项目分离总结.docx（分章节 + 列表要点）
 */
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun,
  AlignmentType, LevelFormat, HeadingLevel, PageBreak,
} = require('docx');

// ===================== 内容数据 =====================

// 工具：构造 H1 标题
const H1 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 480, after: 240 },
    children: [new TextRun({ text, bold: true })],
  });

// H2
const H2 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 320, after: 160 },
    children: [new TextRun({ text, bold: true })],
  });

// 普通段落
const P = (text, opts = {}) =>
  new Paragraph({
    spacing: { before: 80, after: 80, line: 320 },
    children: [new TextRun({ text, ...opts })],
  });

// 列表项
const LI = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: 'bullets', level },
    spacing: { before: 40, after: 40, line: 300 },
    children: [new TextRun({ text })],
  });

// 高亮的列表项（key: value 形式）
const LIKV = (key, value) =>
  new Paragraph({
    numbering: { reference: 'bullets', level: 0 },
    spacing: { before: 40, after: 40, line: 300 },
    children: [
      new TextRun({ text: `${key}：`, bold: true }),
      new TextRun({ text: value }),
    ],
  });

const allChildren = [];

// ---------- 封面 ----------
allChildren.push(
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 3600, after: 240 },
    children: [
      new TextRun({
        text: '会议纪要助手',
        bold: true, size: 56, color: '1a5276',
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 720 },
    children: [
      new TextRun({ text: '本次迭代总结', size: 36, color: '555555' }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1440 },
    children: [
      new TextRun({
        text: '内容：Docker 迁移落地 · 会议纪要引擎并入 · 功能优化',
        size: 22, color: '888888',
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240 },
    children: [
      new TextRun({ text: '生成日期：2026-05-16', size: 22, color: '888888' }),
    ],
  }),
  new Paragraph({ children: [new PageBreak()] })
);

// ---------- 一、本次迭代摘要 ----------
allChildren.push(H1('一、本次迭代摘要'));
allChildren.push(P(
  '本轮工作以"迁移过来的 Docker 镜像（postgres / redis）+ 业务代码（FastAPI 后端、Vite 前端、Celery worker）"为起点，完成本地环境跑通、沙箱会议纪要引擎并入主项目，并在此基础上迭代了多项产品体验与工程化优化。'
));
allChildren.push(P('围绕的目标是三个层面：'));
allChildren.push(LI('环境可用：让迁移后的项目在本机能稳定跑起来'));
allChildren.push(LI('引擎落地：把沙箱里调好的 minutes_engine / pdf_renderer / docx_renderer 整合到后端 Celery 流水线'));
allChildren.push(LI('体验打磨：补齐导出格式、时区、分页、搜索、耗时显示等用户感知点'));

// ---------- 二、本地环境搭建（关键经验）----------
allChildren.push(H1('二、本地环境搭建（关键经验）'));

allChildren.push(H2('2.1 Python 版本选择'));
allChildren.push(LIKV('对齐原镜像', 'backend Dockerfile 第一行 FROM python:3.11.7-slim，本地至少 3.11.x'));
allChildren.push(LIKV('最终采用', 'Python 3.11.9（3.11 系列最后的常规稳定版）'));
allChildren.push(LIKV('避坑', '不要混用系统 Python 3.8/3.9，必须用 .venv 虚拟环境隔离'));

allChildren.push(H2('2.2 依赖安装'));
allChildren.push(LI('全程国内镜像：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple'));
allChildren.push(LI('原 requirements.txt 不完整，缺包按报错逐项补：bcrypt、python-jose[cryptography]、redis、pydantic[email]、celery 等'));
allChildren.push(LI('datrie 在 Windows 上无预编译 wheel，注释跳过即可（孤立的历史遗留依赖）'));
allChildren.push(LI('前端 npm install 遇 ahooks 与 React 19 的 peer 冲突，加 --legacy-peer-deps 解决'));

allChildren.push(H2('2.3 三进程协同的启动模型'));
allChildren.push(LIKV('窗口 A：FastAPI', 'cd backend/app → 激活 venv → python app_main.py（端口 8100）'));
allChildren.push(LIKV('窗口 B：Celery worker', 'cd backend/app → 激活 venv → python run_worker.py（自封装启动入口）'));
allChildren.push(LIKV('窗口 C：Vite', 'cd frontend → npm run dev（端口 5173）'));
allChildren.push(LIKV('数据库 / 缓存', 'Docker 自动跑（restart: unless-stopped），无需手动启停'));

// ---------- 三、会议纪要引擎并入 ----------
allChildren.push(H1('三、会议纪要引擎并入主项目'));

allChildren.push(H2('3.1 迁移策略'));
allChildren.push(P('沙箱版 minutes_engine.py（4854 行）相对项目版（4452 行）多 400 行新逻辑；沙箱 pdf_renderer.py 多 60 行；docx_renderer.py 为沙箱独有。沙箱代码里作者用 "回填还原" / "回填后端删除" 标记了所有需要在并入主项目时调整的位置。'));
allChildren.push(P('采取 "以沙箱版为新基准 + 修复集成点" 的策略，而不是逐条挑改动合并。'));

allChildren.push(H2('3.2 三个集成点的处理'));
allChildren.push(LIKV('Config', '硬编码 API Key 替换为 from config.llm_config import get_config'));
allChildren.push(LIKV('CLI 入口删除', 'minutes_engine.py / pdf_renderer.py / docx_renderer.py 末尾的 main() + __main__ 全部移除'));
allChildren.push(LIKV('保留沙箱算法逻辑', 'PPL 切分、大会发言模式检测、主题集中展开、质量校验等全部沿用'));

allChildren.push(H2('3.3 新增依赖（11 个，全部钉版本）'));
allChildren.push(LI('langchain-core==0.2.43、langchain==0.2.17、langchain-openai==0.1.25'));
allChildren.push(LI('langgraph==0.2.62、langgraph-checkpoint==2.0.10、langsmith==0.1.147'));
allChildren.push(LI('numpy==1.26.4、pyyaml==6.0.2、tenacity==8.5.0'));
allChildren.push(LI('reportlab==4.2.5、fonttools==4.55.3（PDF 渲染）'));

// ---------- 四、新增 / 优化的功能模块 ----------
allChildren.push(H1('四、新增 / 优化的功能模块'));

allChildren.push(H2('4.1 北京时间存储'));
allChildren.push(LIKV('问题', 'datetime.utcnow 写入 UTC，前端无转换，时间差 8 小时'));
allChildren.push(LIKV('方案', '在 models/meeting.py 加 _beijing_now() 工具函数'));
allChildren.push(LIKV('影响范围', 'created_at / updated_at / started_at / finished_at 全部用它做 default'));
allChildren.push(LIKV('未动 DB schema', '列类型仍为 naive DateTime，最小破坏；老任务不回溯'));

allChildren.push(H2('4.2 Word 导出'));
allChildren.push(LIKV('后端', '新增 docx_path 列；worker 在 PDF 渲染后追加 md_to_docx 调用，失败容错；download 接口扩展 docx 分支'));
allChildren.push(LIKV('前端', 'MeetingTask 类型加 has_docx；任务卡片增加 FileWordOutlined 下载按钮'));
allChildren.push(LIKV('DB 迁移', 'ALTER TABLE meeting_tasks ADD COLUMN docx_path VARCHAR(500)；以 SQL 形式手工执行'));

allChildren.push(H2('4.3 标题智能命名与文件名安全'));
allChildren.push(LIKV('交互逻辑', '用户填了标题用用户的；没填先存占位 "标题待生成…"，任务跑完用引擎从原文提取的标题覆盖'));
allChildren.push(LIKV('文件名安全', '统一 _safe_filename()：非法字符 \\ / : * ? " < > | 替换为 _，去首尾空格/点，截至 80 字符，空了兜底 "会议纪要"'));
allChildren.push(LIKV('文件命名格式', '磁盘上 {safe_title}_{ts}.{ext}，老任务保留原时间戳命名'));
allChildren.push(LIKV('下载时统一', 'Content-Disposition 中 filename 永远用 safe(task.title) + 扩展名，老任务也受益'));
allChildren.push(LIKV('引擎兜底', '提取不到标题时落到 "会议座谈纪要"（按业务方决策保留通用兜底）'));
allChildren.push(LIKV('音频模式', '保留原行为：用音频文件名作为默认标题（用户对自己起的文件名有概念）'));

allChildren.push(H2('4.4 任务列表分页 + 标题搜索'));
allChildren.push(LIKV('问题', '前端固定取 page_size=50，超 50 条的旧任务不可见'));
allChildren.push(LIKV('后端', 'GET /meeting/tasks 增加 title 模糊查询参数，转义 LIKE 元字符（% _）后做 ilike'));
allChildren.push(LIKV('前端', 'PAGE_SIZE=10；Input.Search 加在 "我的纪要任务" 标题旁，回车 / 点搜索 / 清空触发；列表底部 Pagination 组件'));
allChildren.push(LIKV('细节', '提交新任务后自动跳回第 1 页；轮询保持当前 page / search 不错乱；useEffect 依赖 [page, searchTitle] 自动重拉'));

allChildren.push(H2('4.5 任务耗时实时显示'));
allChildren.push(LIKV('数据模型', '新增 started_at（转 running 时打点）+ finished_at（转 done / failed 时打点）两列'));
allChildren.push(LIKV('耗时口径', '从 running 起算，排除排队等待时间；结束以 finished_at 为准，不依赖会变动的 updated_at'));
allChildren.push(LIKV('展示格式', '"用时：HH:MM:SS"，统一替代原本的"✓ 完成"图标，运行中 / 已完成 / 失败三种状态都显示'));
allChildren.push(LIKV('实时刷新', '前端加 1 秒本地 tick，仅在有运行中任务时启动，避免空转'));

allChildren.push(H2('4.6 Worker 启动加速'));
allChildren.push(LIKV('两个瓶颈', 'mingle 阶段 60 秒等待 + 首次任务 lazy import 46 秒（langchain/langgraph 全家桶）'));
allChildren.push(LIKV('一键脚本', '封装 run_worker.py，把 --pool=solo / --without-mingle / --without-gossip / --without-heartbeat 等参数烤进去，并自动注入 sys.path（省 PYTHONPATH）'));
allChildren.push(LIKV('顶层 import', '把 minutes_engine 等三个重型 import 从 process_meeting 函数体移到 tasks.py 模块顶部'));
allChildren.push(LIKV('权衡', 'worker 启动 ~30 秒 → ~80 秒，但第一个任务 "准备中" 从 46 秒压缩到秒级；后端启动也跟着慢约 47 秒'));

allChildren.push(H2('4.7 空纪要问题：占位符泄漏 + 垃圾输入兜底'));
allChildren.push(P('一次线上排查：一份 ASR 转写质量极差的输入（满屏"嗯啊对"填充词，1468 字里有效信息不足 200 字），跑出来的纪要只有几十个字、标题还显示成 "标题待生成…"。同时定位到两个独立问题：'));
allChildren.push(LIKV('Bug 定位', 'minutes_engine.py 的标题兜底逻辑 if not metadata["title"] or metadata["title"] == "会议" 没识别出 "标题待生成…" 这个占位符。调用方传入的 title 参数就是该占位符，导致兜底链路"用占位符兜底占位符"，最终泄漏到 metadata["title"] / 文件名 / 报告 H1'));
allChildren.push(LIKV('Bug 修复', '引入 _PLACEHOLDER_TITLES 集合统一过滤；提取出的 title 或调用方传入的 title 命中占位符都视为"没标题"，最终落到通用兜底 "会议座谈纪要"'));
allChildren.push(LIKV('输入质量预检', 'tasks.py 在调用 generate_minutes 前加 _effective_text_len() 检查：用正则剔除填充词（嗯啊对哦呃噢…）+ 标点 + 空白，剩余 < 200 字直接 raise RuntimeError，任务转 failed 而不是继续跑流水线'));
allChildren.push(LIKV('产品价值', '从"产出空文档还显示已完成"（具有误导性）变成"明确告知用户输入不达标"（可执行的反馈）'));
allChildren.push(LIKV('阈值可调', '常量 _MIN_EFFECTIVE_CHARS = 200，后续根据真实失败案例分布微调'));

// ---------- 五、技术要点 / 易踩坑 ----------
allChildren.push(H1('五、技术要点与易踩坑'));

allChildren.push(H2('5.1 文件名 / 下载相关'));
allChildren.push(LIKV('Starlette FileResponse', '含非 ASCII 时只设 filename*=UTF-8\'\'... (RFC 5987)，不设普通 filename=，前端正则要兼容两种形式并 URL 解码'));
allChildren.push(LIKV('CORS 暴露 header', 'Content-Disposition 不在浏览器默认可读的 safelist 内，必须在 CORSMiddleware 显式 expose_headers=["Content-Disposition"]，否则 JS 拿不到，文件名回落到 task_{id}.{ext}'));
allChildren.push(LIKV('文件名安全函数', '同一份 _safe_filename 在引擎和路由中各放一份（未来可抽公共模块）'));

allChildren.push(H2('5.2 SQLAlchemy / 数据库'));
allChildren.push(LIKV('Base.metadata.create_all 不自动加列', '加新列必须手工跑 ALTER TABLE，否则 INSERT 报错'));
allChildren.push(LIKV('本项目新增的三列', 'docx_path / started_at / finished_at，对应三次 ALTER'));
allChildren.push(LIKV('外键 / UUID', 'user_id 是 PostgreSQL UUID 类型，迁移时 init-db SQL 已自动建好'));

allChildren.push(H2('5.3 Celery on Windows'));
allChildren.push(LIKV('--pool=solo 必填', '默认 prefork 在 Windows 不可用，单任务顺序处理，调试期最简单稳定'));
allChildren.push(LIKV('mingle / gossip / heartbeat', '单 worker 部署下毫无意义，关闭可省启动 60 秒'));
allChildren.push(LIKV('sys.path 注入', 'celery 命令通过 -A 加载时 cwd 不在 sys.path，需要在 celery_app.py 或启动脚本中显式插入 backend/app 路径'));
allChildren.push(LIKV('Python 进程不热加载', '改了 tasks.py / minutes_engine.py 之后 worker 必须重启；改了 router / model 之后后端必须重启'));

allChildren.push(H2('5.4 前端工程化'));
allChildren.push(LIKV('Vite 热更新', 'src 下的改动几乎所有场景都能 HMR，特殊情况浏览器 Ctrl+Shift+R 强刷'));
allChildren.push(LIKV('TypeScript 类型同步', '后端 schema 加字段后，前端 MeetingTask 接口必须同步加，避免运行时 undefined'));
allChildren.push(LIKV('antd Input.Search 用法', 'onSearch 回调统一处理回车 / 点按钮 / 清空（allowClear）三种触发'));
allChildren.push(LIKV('Pagination 配合 useEffect', '把 page / searchTitle 放到依赖数组，状态变化自动重拉，无需手动调 load()'));

// ---------- 六、未实施的备选方案 ----------
allChildren.push(H1('六、未实施的备选方案（备查）'));
allChildren.push(P('以下方案在本次迭代中已完成设计，但根据业务方判断暂不实施，留作后续参考：'));
allChildren.push(LIKV('任务标题前端可编辑', '设计上新增 report_title 列作为"任务完成时冻结的标题"，title 可用于前端展示编辑；与下载文件名解耦。改动量约 3 后端文件 + 2 前端文件 + DB 加 1 列'));
allChildren.push(LIKV('引擎兜底改进', '提取不到标题时改用文本前 20 字而非通用"会议座谈纪要"，业务方决定保留兜底文案'));
allChildren.push(LIKV('Celery 并发提升', '当前 --pool=solo 单线程串行处理；以后任务量大可换 --pool=threads -c 4 增加并发，注意 minutes_engine 内部 LLM 调用是否线程安全'));

// ---------- 七、改动文件清单 ----------
allChildren.push(H1('七、改动文件清单'));

allChildren.push(H2('7.1 后端'));
allChildren.push(LIKV('backend/app/requirements.txt', '追加 langchain 全家桶等 11 个新依赖'));
allChildren.push(LIKV('backend/app/app_main.py', 'CORSMiddleware 加 expose_headers'));
allChildren.push(LIKV('backend/app/models/meeting.py', '加 _beijing_now()、docx_path、started_at、finished_at'));
allChildren.push(LIKV('backend/app/service/meeting/minutes_engine.py', '沙箱版基线 + 修复集成 Config + 新增 _safe_filename + save_result 文件名带标题 + generate_minutes 返回 title + 标题占位符兜底过滤（_PLACEHOLDER_TITLES）'));
allChildren.push(LIKV('backend/app/service/meeting/pdf_renderer.py', '沙箱版基线 + 删 CLI'));
allChildren.push(LIKV('backend/app/service/meeting/docx_renderer.py', '新增（沙箱版基线 + 删 CLI）'));
allChildren.push(LIKV('backend/app/service/meeting/tasks.py', '顶层预加载重型 import、渲染 docx 块、写 started_at/finished_at、用引擎标题回写 task.title、输入质量预检 _effective_text_len()'));
allChildren.push(LIKV('backend/app/router/meeting_router.py', 'docx 下载分支 + 标题搜索 + 文件名安全 + 时间戳字段输出 + 占位标题'));
allChildren.push(LIKV('backend/app/run_worker.py', '新增：一键启动 worker（自动 sys.path 注入 + Celery 启动参数烤入）'));

allChildren.push(H2('7.2 前端'));
allChildren.push(LIKV('frontend/src/api/meeting.ts', 'MeetingTask 接口加 has_docx / started_at / finished_at；downloadMeetingTask 加 docx；listMeetingTasks 加 title 参数；suggestFilename 兼容 RFC 5987 filename*='));
allChildren.push(LIKV('frontend/src/pages/meeting/index.tsx', 'Word 下载按钮 + 分页 + 搜索框 + 耗时实时显示 + 替换"完成"图标'));

allChildren.push(H2('7.3 数据库变更'));
allChildren.push(LI('ALTER TABLE meeting_tasks ADD COLUMN docx_path VARCHAR(500);'));
allChildren.push(LI('ALTER TABLE meeting_tasks ADD COLUMN started_at TIMESTAMP, ADD COLUMN finished_at TIMESTAMP;'));

// ---------- 八、回顾与下一步建议 ----------
allChildren.push(H1('八、回顾与下一步建议'));
allChildren.push(P('本轮迭代覆盖了从基础环境到产品体验的多个层级，几条经验值得沉淀：'));
allChildren.push(LI('"建议先讲方案，确认后再动手"——避免反复返工，已纳入协作惯例'));
allChildren.push(LI('Python 进程的代码改动后必须重启对应进程，开发期可考虑用 watchdog / uvicorn --reload 加速循环'));
allChildren.push(LI('小步快跑的功能（北京时间、Word 导出、分页、耗时）效果显著，单次迭代 < 1 天即可上线'));
allChildren.push(P('下一步可考虑：'));
allChildren.push(LI('任务标题前端可编辑（方案见第六章）'));
allChildren.push(LI('录音转写阶段也加入可见的进度条（当前阶段笼统的"语音识别中"略粗）'));
allChildren.push(LI('生产部署时把后端 + worker 都打回 Docker 镜像，避免本地 venv 维护成本'));
allChildren.push(LI('单元测试 / 集成测试覆盖：当前以人工触发为主，长期需要回归测试保护核心流水线'));

// ===================== 文档构建 =====================
const doc = new Document({
  creator: 'Claude',
  title: '会议纪要助手 - 本次迭代总结',
  numbering: {
    config: [
      {
        reference: 'bullets',
        levels: [
          {
            level: 0,
            format: LevelFormat.BULLET,
            text: '•',
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
          {
            level: 1,
            format: LevelFormat.BULLET,
            text: '◦',
            alignment: AlignmentType.LEFT,
            style: { paragraph: { indent: { left: 1440, hanging: 360 } } },
          },
        ],
      },
    ],
  },
  styles: {
    default: { document: { run: { font: '微软雅黑', size: 22 } } },
    paragraphStyles: [
      {
        id: 'Heading1',
        name: 'Heading 1',
        basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 32, bold: true, font: '微软雅黑', color: '1a5276' },
        paragraph: { spacing: { before: 480, after: 240 }, outlineLevel: 0 },
      },
      {
        id: 'Heading2',
        name: 'Heading 2',
        basedOn: 'Normal', next: 'Normal', quickFormat: true,
        run: { size: 26, bold: true, font: '微软雅黑', color: '2c3e50' },
        paragraph: { spacing: { before: 320, after: 160 }, outlineLevel: 1 },
      },
    ],
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 11906, height: 16838 }, // A4
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
        },
      },
      children: allChildren,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  const outPath = 'E:/meeting_minutes_assistant/会议纪要助手-本次迭代总结.docx';
  fs.writeFileSync(outPath, buf);
  console.log('生成成功:', outPath);
  console.log('总字节数:', buf.length);
});
