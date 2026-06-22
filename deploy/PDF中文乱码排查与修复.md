# Linux 上 reportlab 生成 PDF 中文乱码 —— 排查与修复

> 现象：会议纪要 PDF 在 Linux 服务器上中文显示成**方块/乱码**（数字、英文正常）。
> 结论：reportlab 的 `TTFont` **只支持 TrueType(glyf) 轮廓，不支持 CFF/OpenType-PS 轮廓**；
> 而服务器常装的 Noto CJK / 思源黑体恰恰是 CFF，注册即失败回退 Helvetica → 中文方块。
> 修复见 commit `3da91a7`（`backend/app/service/meeting/pdf_renderer.py`）。

---

## 1. 现象
- PDF 里**数字、英文、标点正常**，**中文全是黑方块（豆腐字）**。
- 说明：不是“没字体”那么简单——若纯 Helvetica，中文应是空白/缺字；出现方块说明走到了字体但画不出字形。

## 2. 排查过程（两层根因）

### 第一层：Linux 没装中文字体
- 旧代码 `_find_font_source()` 只检查 3 个 **Debian 风格**字体路径
  （`/usr/share/fonts/truetype/wqy/...`、`/usr/share/fonts/opentype/noto/...`）。
- 服务器是 **Alibaba Cloud Linux 8（RHEL 系）**，没有这些路径、也没装任何中文字体
  → 找不到 → 回退 `Helvetica` → 中文乱码。

### 第二层（真正的根因）：reportlab 不支持 CFF 轮廓
- 装了 `google-noto-sans-cjk-ttc-fonts` 并让代码能找到它后，**仍然乱码**。
- worker 日志给出铁证：
  ```
  ⚠ 字体注册失败: TTF file ".../NotoSansCJK-Regular.ttf": postscript outlines are not supported
  ⚠ 未找到中文字体，PDF中文可能显示异常
  ```
- **Noto CJK / 思源黑体是 CFF（PostScript 轮廓）的 OpenType 字体**，而 reportlab 的 `TTFont`
  只能渲染 **TrueType(glyf) 轮廓**。所以注册失败、又回退 Helvetica → 方块。
- ⚠️ 教训：**给 reportlab 用的中文字体必须是 TrueType(glyf)**，不能用 Noto CJK / Source Han 这类 CFF 字体。

## 3. 修复（代码，commit `3da91a7`）
改 `backend/app/service/meeting/pdf_renderer.py`，三处：

1. **新增 `_is_glyf_font(path)`**：用 fonttools 判断字体是否 TrueType(glyf) 轮廓
   （`.ttc` 取第 0 子字体看是否含 `glyf` 表）。CFF 字体一律判为不可用。

2. **`_find_font_source()` 改为「汇总候选 → 只返回 glyf 字体」**：
   - ① 各平台显式路径（补全 alinux/RHEL/CentOS）；② 递归扫描 `/usr/share/fonts` 等目录；
     ③ `fc-list :lang=zh file` 动态查询。
   - 三路候选合并后逐个 `_is_glyf_font` 校验，**Noto 等 CFF 自动被跳过**，
     只返回真正能用的 TrueType 字体（如 wqy-zenhei）。

3. **`register_font()` 加 CID 兜底**：找不到可用 TrueType 时，
   回退到 reportlab **内置中文 CID 字体 `STSong-Light`**（`UnicodeCIDFont`，零依赖、
   不嵌入字形、由阅读器用 Adobe-GB1 替代字体显示）——**保证中文不再是方块**；
   仍找不到才退 Helvetica。

> 效果：**即使系统一个中文字体都不装**，部署新代码后中文也能正常显示（走 CID）。
> 装了 TrueType 字体（wqy）则自动优先用它并**嵌入**到 PDF（更稳、可打印/任意阅读器）。

## 4. 部署
```bash
# 替换文件
cp /opt/meeting/backend/app/service/meeting/pdf_renderer.py{,.bak}
mv -f /root/pdf_renderer.py /opt/meeting/backend/app/service/meeting/pdf_renderer.py
# 重启 worker（PDF 由 celery worker 生成）
systemctl restart meeting-worker
```
⚠️ 字体是**首次生成 PDF 时才注册**（懒加载）：光重启不会打日志，
**必须提交一个新会议任务跑出 PDF**，才会出现新字体日志。验证：
```bash
journalctl -u meeting-worker --since "2 min ago" --no-pager | grep 字体
# CID 路径： 字体(CID回退): STSong-Light ...
# 嵌入路径： 字体(嵌入): wqy-zenhei.ttc
```

## 5.（可选）要“嵌入字体”更稳——装 TrueType 中文字体
CID 字体不嵌入字形，靠阅读器替代；要 PDF 自带字体（下载/打印/任意阅读器都稳），
装一款 **TrueType** 中文字体，代码会自动优先用它并嵌入：
```bash
dnf install -y epel-release          # alinux 需先开 EPEL
dnf install -y wqy-zenhei-fonts      # TrueType(glyf)，reportlab 可嵌入
systemctl restart meeting-worker
```
> 不要装 Noto CJK / 思源来解决这个问题——它们是 CFF，reportlab 用不了。

## 6. 速查
| 关键点 | 结论 |
|---|---|
| reportlab 支持的字体轮廓 | 只支持 **TrueType(glyf)**，不支持 CFF/OpenType-PS |
| 能用的中文字体 | wqy-zenhei、wqy-microhei、文鼎 uming/ukai、Windows simsun/msyh |
| **不能**用的中文字体 | Noto CJK、Source Han（思源）——都是 CFF |
| 报错特征 | `postscript outlines are not supported` |
| 零依赖兜底 | `UnicodeCIDFont("STSong-Light")`，不嵌入但能显示中文 |
| 字体何时注册 | 首次生成 PDF 时（懒加载），需提交新任务才会触发/打日志 |
| 哪个服务生成 PDF | `meeting-worker`（celery），改完重启它 |
