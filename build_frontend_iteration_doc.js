/**
 * 会议纪要助手 - 前端迭代总结（2026-05-24）
 * 风格沿用 build_qa_doc.js / 会议纪要助手-本次迭代总结.docx
 */
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun,
  AlignmentType, LevelFormat, HeadingLevel, PageBreak,
} = require('docx');

// ===================== Helpers =====================
const H1 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { before: 480, after: 240 },
    children: [new TextRun({ text, bold: true })],
  });

const H2 = (text) =>
  new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 320, after: 160 },
    children: [new TextRun({ text, bold: true })],
  });

const P = (text, opts = {}) =>
  new Paragraph({
    spacing: { before: 80, after: 80, line: 320 },
    children: [new TextRun({ text, ...opts })],
  });

const LI = (text, level = 0) =>
  new Paragraph({
    numbering: { reference: 'bullets', level },
    spacing: { before: 40, after: 40, line: 300 },
    children: [new TextRun({ text })],
  });

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
      new TextRun({ text: '会议纪要助手', bold: true, size: 56, color: '1a5276' }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { after: 720 },
    children: [
      new TextRun({ text: '前端迭代总结', size: 36, color: '555555' }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 1440 },
    children: [
      new TextRun({
        text: '内容：Claude Design 协作 · 全局卡片化布局 · 首页重设计 · 会议纪要页重设计',
        size: 22, color: '888888',
      }),
    ],
  }),
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240 },
    children: [
      new TextRun({ text: '生成日期：2026-05-24', size: 22, color: '888888' }),
    ],
  }),
  new Paragraph({ children: [new PageBreak()] })
);

// ---------- 一、本次迭代摘要 ----------
allChildren.push(H1('一、本次迭代摘要'));
allChildren.push(P(
  '今天围绕"把前端从功能可用提升到产品级体验"做了一次全面的视觉与交互重构。' +
  '工作流以 Anthropic Claude Design（claude.ai/design）出原型 + 本会话适配落地到现有 ' +
  'React 19 + Ant Design 5 + SCSS Module 技术栈为主线。'
));
allChildren.push(P('改动覆盖三个层级：'));
allChildren.push(LI('全局：BaseLayout 卡片化布局、浅灰背景、首页 + 会议纪要页 flush 模式'));
allChildren.push(LI('首页：Hero 卡 + 人物形象 + 3 张功能特性卡，文案与排版调优'));
allChildren.push(LI('会议纪要页：页头蓝色卡 + Segmented 输入切换 + 状态 chips 筛选 + 网格化任务卡 + 等高布局 + 过期提示'));
allChildren.push(P('配套：后端 status 多值筛选、Celery beat 启用、cleanup 时区修正。'));

// ---------- 二、Claude Design 协作流程 ----------
allChildren.push(H1('二、Claude Design 协作流程'));

allChildren.push(H2('2.1 工作流'));
allChildren.push(LIKV('原型阶段', 'claude.ai/design 网页版生成可视化设计稿（Artifact）'));
allChildren.push(LIKV('迭代阶段', '用户对原型微调样式 / 文案 / 布局，前端可视化即时反馈'));
allChildren.push(LIKV('落地阶段', '把 Design 产出的 tsx + scss 复制给本会话，做"技术栈适配"——替换 Tailwind 为 SCSS Module、替换 shadcn 为 antd 组件、接通现有 API'));

allChildren.push(H2('2.2 项目技术栈基线（必须遵守）'));
allChildren.push(LI('React 19.0 + TypeScript 5.7 + Vite 6.1'));
allChildren.push(LI('Ant Design 5.24（带 @ant-design/v5-patch-for-react-19 适配补丁）'));
allChildren.push(LI('SCSS Module（*.module.scss，通过 styles.xxx 引用）'));
allChildren.push(LI('Valtio 状态管理 + ahooks Hook 工具'));
allChildren.push(LI('axios + dayjs + classnames + lodash-es'));
allChildren.push(LI('路径别名 @/ 对应 src/'));

allChildren.push(H2('2.3 协作避坑'));
allChildren.push(LIKV('反约束 prompt', '让 Claude Design 不要用 Tailwind / shadcn / styled-components，必须用 antd 5 + SCSS Module'));
allChildren.push(LIKV('设计 token 锁定', '主色 #1677ff、背景 #f4f6fa、圆角 8/12px、阴影范本写进 prompt'));
allChildren.push(LIKV('技术栈强调', 'Header/Sider/Content 这些 Layout 容器是 Claude Design 的，要全部丢弃，替换为本项目的 BaseLayout'));

// ---------- 三、全局布局重构 ----------
allChildren.push(H1('三、全局布局重构'));

allChildren.push(H2('3.1 顶部 logo 横条'));
allChildren.push(LIKV('范围', '首页 + 会议纪要页（pathname === "/" 或 pathname.startsWith("/meeting")）'));
allChildren.push(LIKV('logo', '广州粤港澳大湾区研究院院徽（96px 高，等比缩放）'));
allChildren.push(LIKV('顶栏高度', '112px，固定定位 fixed + z-index 1001'));
allChildren.push(LIKV('修饰类', 'base-layout--with-topbar 自动给 sidebar 和 content 加偏移'));

allChildren.push(H2('3.2 卡片化布局'));
allChildren.push(LIKV('全站背景', '#f4f6fa 浅冷灰，从间隙透出'));
allChildren.push(LIKV('内容布局', '原单一大白卡 → 多张独立白卡（顶栏 / 侧边栏 / 内容区分离）'));
allChildren.push(LIKV('卡片样式', '8px 圆角 + 浅阴影 0 1px 3px rgba(0,0,0,0.08) + 8px 间距'));

allChildren.push(H2('3.3 Flush 模式（去白卡片包裹）'));
allChildren.push(LIKV('问题背景', 'base-layout__content 默认是白色卡片包裹；内部业务页（首页/会议纪要页）有自己的卡片化排版，造成"白上加白"视觉混乱'));
allChildren.push(LIKV('解决方案', '新增 base-layout--flush 修饰类；pathname 命中首页或 /meeting 时，把 content 的 bg/border-radius/box-shadow 全部清零'));
allChildren.push(LIKV('效果', '业务页直接坐在 #f4f6fa 灰底上，自有卡片的阴影和圆角更突出'));

// ---------- 四、首页重设计 ----------
allChildren.push(H1('四、首页（pages/index）重设计'));

allChildren.push(H2('4.1 整体结构'));
allChildren.push(P('从原来的"背景 + Hi 文字 + 一个按钮 + 3 张大卡"重做成更现代的：'));
allChildren.push(LI('问候区（标题 + 副标题）'));
allChildren.push(LI('Hero 欢迎卡（深蓝渐变 + 人物 + 文案 + 主 CTA 按钮）'));
allChildren.push(LI('"主要功能"栏 + 3 张功能特性卡（图标 + 标题 + 描述 + 标签 chip）'));
allChildren.push(LIKV('容器', 'max-width 1280px、padding 32×40px、background #f4f6fa（继承全局）'));

allChildren.push(H2('4.2 Hero 卡'));
allChildren.push(LIKV('背景', '120deg 线性渐变 #0b1d4a → #1e40af → #3b82f6'));
allChildren.push(LIKV('装饰', '::before 加 4 层叠加：右上柔光 + 左下柔光 + 横向网格线 + 纵向网格线'));
allChildren.push(LIKV('标题', '"我是广州粤港澳大湾区研究院研究助理，我擅长整理会议报告。"（白色 32px 加粗）'));
allChildren.push(LIKV('副标题', '"粘贴或上传会议原文，自动生成结构化会议纪要（Markdown + PDF + Word）。"（白色 14px 半透明 + nowrap 不换行）'));
allChildren.push(LIKV('主 CTA', '"开始整理会议纪要" 按钮，白底蓝字，圆角 24px，导航到 /meeting'));

allChildren.push(H2('4.3 人物形象'));
allChildren.push(LIKV('迭代轨迹', '自绘 SVG 男青年 → 用户提供 bg-female.png → bg-female2.png → female.png（最终采用）'));
allChildren.push(LIKV('位置', '初次在右侧，后改为左侧；flex 容器从 space-between 改为 flex-start 让人物和文字紧凑左对齐'));
allChildren.push(LIKV('背景渲染', '尝试过径向渐变白雾过渡 → 最终取消所有特效，纯图片显示'));
allChildren.push(LIKV('容器', '220×220 flex 居中容器，img 自适应保持比例'));

allChildren.push(H2('4.4 3 张功能特性卡'));
allChildren.push(LIKV('卡 1 "支持多种输入"', '描述："粘贴会议原文、上传 .txt / .Word 文件、上传录音（自动转写文稿）"；标签：粘贴文本 / 上传文件 / 上传录音'));
allChildren.push(LIKV('卡 2 "支持多种输出"', '描述："同时生成 Markdown / PDF / Word 等格式，支持下载"；标签：Markdown / PDF / Word'));
allChildren.push(LIKV('卡 3 "自动保留 7 天"', '描述："历史会议纪要保留 7 天后自动清理，期间随时下载"；标签：隐私安全 / 7 天保留'));
allChildren.push(LIKV('视觉', 'antd Card + 自定义 icon 容器（color/${color}1a 透明色背景）+ Tag chip'));

// ---------- 五、会议纪要页重设计 ----------
allChildren.push(H1('五、会议纪要页（pages/meeting）重设计'));

allChildren.push(H2('5.1 整体布局'));
allChildren.push(LI('顶部独立深蓝页头卡（与首页 Hero 同款渐变 + 装饰，padding 24×32 更紧凑）'));
allChildren.push(LI('主体 Row 两列：xs={24} lg={8/16}'));
allChildren.push(LI('容器 max-width 1400px，左对齐（margin: 0 auto 0 0），padding 12×48×56×4 经多轮微调'));
allChildren.push(LI('右侧任务卡 margin-left -8px 收紧两列间距'));

allChildren.push(H2('5.2 新建任务卡（左列）'));
allChildren.push(LIKV('输入方式切换', 'antd Tabs → antd Segmented（block 模式，带 EditOutlined / UploadOutlined / AudioOutlined 图标）'));
allChildren.push(LIKV('Sticky 行为', '初次加 position: sticky; top: 12px；最终决定取消 sticky 改为普通卡片'));
allChildren.push(LIKV('粘贴模式', 'TextArea autoSize 10~16 行 + 示例文本按钮 + showCount 字数统计'));
allChildren.push(LIKV('上传文档模式', 'Dragger 接受 .txt / .docx，最多 2 个，单文件 ≤ 50MB（不支持 .doc）'));
allChildren.push(LIKV('上传录音模式', 'Dragger 接受 m4a / mp4 / wav / aac / mp3，单文件 ≤ 500MB，自动转写'));
allChildren.push(LIKV('会议标题', '可选输入，maxLength 120'));
allChildren.push(LIKV('提交按钮', 'antd Button type="primary" block size="large"，loading 状态绑定 submitting'));

allChildren.push(H2('5.3 任务列表卡（右列）'));
allChildren.push(LIKV('标题栏', '"我的纪要任务" + info 图标 Popover（点击展开 7 天清理说明）'));
allChildren.push(LIKV('工具栏', '按标题搜索（Input.Search 回车 / 失焦触发）+ 批量打包 + 刷新 按钮'));
allChildren.push(LIKV('状态筛选 chips', '4 个 chip：全部任务 / 进行中 / 已完成 / 失败；圆点 + 文案 + 数字 + 激活态高亮'));
allChildren.push(LIKV('chip 联动', '"进行中" chip 映射后端 status=pending,running（后端配套支持逗号多值）；切换 chip 重置 page=1'));
allChildren.push(LIKV('任务网格', 'Row gutter [16,16] + Col xs=24 md=12 xxl=8（2列/3列响应式）'));

allChildren.push(H2('5.4 单条任务卡'));
allChildren.push(LIKV('左侧色条', '3px 宽彩色条：pending 灰 / running 蓝 / done 绿 / failed 红'));
allChildren.push(LIKV('标题行', '任务标题 + 状态 Tag（带 icon + 色彩）'));
allChildren.push(LIKV('Meta 行', '来源（粘贴/上传/录音）+ 创建时间 + 过期提示（橙色 #faad14，仅 done/failed 显示）'));
allChildren.push(LIKV('进行中', 'LoadingOutlined spin + 阶段文案（蓝色）'));
allChildren.push(LIKV('失败', '错误信息一行（红字 + 浅红背景）'));
allChildren.push(LIKV('用时', '"用时 HH:MM:SS"，颜色随状态：进行中蓝 / 完成绿 / 失败红'));
allChildren.push(LIKV('底部', '虚线分隔；左侧 md/pdf/word 三个下载按钮（音频任务多 "转写" 按钮）；右侧删除按钮（Popconfirm 二次确认）'));
allChildren.push(LIKV('选中态', '边框变蓝 + 外阴影 rgba(22,119,255,0.18)'));

allChildren.push(H2('5.5 等高布局'));
allChildren.push(LIKV('需求', '右侧任务卡的白色背景在任务少时也要"撑满"，与左侧"新建任务"卡视觉等高'));
allChildren.push(LIKV('实现', 'antd Row 默认 align-items: stretch；.tasksCard 加 height: 100% + display: flex + flex-direction: column；.ant-card-body 加 flex: 1'));

allChildren.push(H2('5.6 分页器'));
allChildren.push(LIKV('迭代轨迹', '位置反复调整：卡片内 → 卡片外 → 回到卡片内；与最后一行间距 16 → 28 → 36 → 40 → 45px'));
allChildren.push(LIKV('最终', '卡片底部内置；margin-top 45px 固定间距；右对齐；showTotal "共 X 条"'));

// ---------- 六、过期提示与清理机制 ----------
allChildren.push(H1('六、过期提示与清理机制'));

allChildren.push(H2('6.1 前端过期提示'));
allChildren.push(LIKV('工具函数', 'formatExpireHint(createdAt)：基于 RETENTION_DAYS = 7 计算剩余天数'));
allChildren.push(LIKV('文案', '> 0 天："X 天后过期"；= 0："今天过期"；< 0："已过期"（兜底）'));
allChildren.push(LIKV('显示状态', '仅 done / failed 显示，pending / running 不显示（任务都还没跑完，谈过期无意义）'));
allChildren.push(LIKV('样式', '橙色 #faad14 + font-weight 500'));

allChildren.push(H2('6.2 后端 7 天自动清理'));
allChildren.push(LIKV('任务定义', 'service/meeting/cleanup.py 的 cleanup_expired_meetings'));
allChildren.push(LIKV('调度方式', 'Celery beat 每 24 小时执行；run_worker.py 加 -B 内嵌 beat'));
allChildren.push(LIKV('时区修正', '原 cutoff 用 datetime.utcnow()，与 created_at 的北京时间不一致；改为 _beijing_now() 保持口径统一'));
allChildren.push(LIKV('硬删除策略', '删除产物目录 + 删除 DB 行；磁盘空间释放、隐私彻底清除、列表整洁'));

allChildren.push(H2('6.3 "已过期还显示"的真因'));
allChildren.push(P('用户观察到部分已过 7 天的任务仍出现在前端列表里。排查后定位：'));
allChildren.push(LI('Celery beat 的 schedule: 24 * 3600 只代表"每 24h 执行一次"，不保证启动时立即触发'));
allChildren.push(LI('如果 beat 上次运行记录还在（celerybeat-schedule 文件），重启 worker 后 beat 等满 24h 才再触发'));
allChildren.push(LI('调试期可手动触发：python -c "from service.meeting.cleanup import cleanup_expired_meetings; cleanup_expired_meetings()"'));

// ---------- 七、后端配套改动 ----------
allChildren.push(H1('七、后端配套改动'));

allChildren.push(H2('7.1 status 参数多值支持'));
allChildren.push(LIKV('文件', 'backend/app/router/meeting_router.py'));
allChildren.push(LIKV('改动', 'GET /meeting/tasks 的 status 参数支持单值或逗号分隔多值'));
allChildren.push(LIKV('实现', '解析为 list，长度 1 用 filter(MeetingTask.status == s)，长度 >1 用 filter(MeetingTask.status.in_(...))'));
allChildren.push(LIKV('前端使用', '"进行中" chip 发送 status=pending,running，一次拉到两种状态'));

allChildren.push(H2('7.2 Celery beat 启用'));
allChildren.push(LIKV('文件', 'backend/app/run_worker.py'));
allChildren.push(LIKV('改动', '启动参数列表追加 -B（embedded beat），与 worker 同进程跑'));
allChildren.push(LIKV('副作用', '日常调度 cleanup_expired_meetings 周期任务，无需额外起 beat 进程'));

allChildren.push(H2('7.3 cleanup 时区修正'));
allChildren.push(LIKV('文件', 'backend/app/service/meeting/cleanup.py'));
allChildren.push(LIKV('改动', 'import _beijing_now，cutoff = _beijing_now() - timedelta(days=RETENTION_DAYS)'));
allChildren.push(LIKV('意义', '与 created_at 的北京时间口径完全一致，避免 +8h 偏移导致任务被延迟清理 8 小时'));

// ---------- 八、改动文件清单 ----------
allChildren.push(H1('八、改动文件清单'));

allChildren.push(H2('8.1 前端（6 个文件 + 1 个资源）'));
allChildren.push(LIKV('layout/base/index.tsx', '新增 useLocation + flushContent 路由判断 + topbar 控制'));
allChildren.push(LIKV('layout/base/index.scss', '卡片化布局 tokens、全局浅灰背景、topbar 固定定位、base-layout--flush 修饰类'));
allChildren.push(LIKV('pages/index/index.tsx', '整页重写：问候 + Hero 卡 + 3 张功能卡；引入 female.png'));
allChildren.push(LIKV('pages/index/index.module.scss', '整页重写：深蓝渐变 hero + 装饰 + 紧凑功能卡样式'));
allChildren.push(LIKV('pages/meeting/index.tsx', '整页重写：保留全部业务逻辑（API/轮询/校验/分页/搜索），重做排版'));
allChildren.push(LIKV('pages/meeting/index.module.scss', '整页重写：设计 tokens + 蓝色页头 + chips + 网格任务卡 + 等高 + 分页吸底'));
allChildren.push(LIKV('assets/index/female.png', '用户提供的人物插画'));

allChildren.push(H2('8.2 后端（3 个文件）'));
allChildren.push(LIKV('router/meeting_router.py', 'status 参数支持逗号分隔多值'));
allChildren.push(LIKV('service/meeting/cleanup.py', '使用 _beijing_now() 修正时区'));
allChildren.push(LIKV('run_worker.py', '启动参数加 -B 启用 beat'));

// ---------- 九、未实施 / 后续可继续优化 ----------
allChildren.push(H1('九、未实施 / 后续可继续优化'));
allChildren.push(LIKV('任务详情面板', '点任务弹右侧 Drawer 直接渲染 md 内容，替代"下载文件再看"，体验跃迁最大'));
allChildren.push(LIKV('卡片视图 ↔ 表格视图切换', '任务多时表格视图更适合查找（参考 Notion / Airtable 多视图）'));
allChildren.push(LIKV('批量操作浮动工具栏', '选中后底部出现 contextual bar（参考 Gmail）'));
allChildren.push(LIKV('任务标题前端可编辑', '已设计未实施：新增 report_title 列冻结报告标题，title 用于前端展示编辑'));
allChildren.push(LIKV('任务步骤显示', '已尝试圆点 + tooltip 形式后回退，可换交互方式重试'));
allChildren.push(LIKV('任务实际耗时预估', '基于历史数据（started_at / finished_at）+ 文本长度做线性估算，启动期方案 B 兜底'));

// ---------- 十、回顾 ----------
allChildren.push(H1('十、回顾'));
allChildren.push(P('本轮迭代把前端从"功能完整但视觉粗糙"提升到"接近成熟 SaaS 产品的成色"。沉淀几条经验：'));
allChildren.push(LI('Claude Design 出原型 + 本地适配落地，是当前最高 ROI 的设计协作工作流'));
allChildren.push(LI('任何视觉改动都要在 prompt 里锁死技术栈和设计 token，避免被默认 Tailwind / shadcn 污染'));
allChildren.push(LI('迭代时"px 级微调"成本极低（margin 4px 也能立刻看到），要善用快速反馈循环'));
allChildren.push(LI('容易踩坑的点：sticky + 等高布局冲突、Pagination 在 flex 容器里的定位、Card body padding 与子元素 margin 的关系、antd Row 默认 stretch 行为'));
allChildren.push(LI('"已过期"这类用户感知问题往往不是数据 bug 而是调度 bug，要分清"功能能用"和"功能在用"'));

// ===================== Build =====================
const doc = new Document({
  creator: 'Claude',
  title: '会议纪要助手 - 前端迭代总结 20260524',
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
  const outPath = 'E:/meeting_minutes_assistant/会议纪要助手-前端迭代总结20260524.docx';
  fs.writeFileSync(outPath, buf);
  console.log('生成成功:', outPath);
  console.log('总字节数:', buf.length);
});
