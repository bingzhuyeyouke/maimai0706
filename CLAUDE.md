# Claude Code 项目指引

本文件是 Claude Code 的项目级指令，克隆项目后自动生效。

## 前置条件

1. **Chrome 必须以调试端口启动**：`python3 start_chrome.py`（Mac/Windows 双平台）
2. **环境配置**：复制 `.env.example` 为 `.env`，填入 `PEXELS_API_KEY`
3. **依赖安装**：`pip install -r requirements.txt`
4. **首次运行务必 `--dry-run`** 测试流程

## 发帖模式快捷指令

| 指令 | 模式 | 脚本 | 平台 | 依赖扩展 |
|------|------|------|------|----------|
| `mutipost` + 文案 | MultiPost三平台发布 | `multi_publish_0715.py` | 脉脉+公众号+头条 | ⚠️需要MultiPost扩展 |
| `directpost` + 文案 | 直连三平台发布 | `direct_publish_0715.py` | 脉脉+公众号+头条 | ✅零扩展依赖 |
| `爆料` / `爆个料` | 爆料活动 | `paste_post.py` | 脉脉 | ⚠️需要MultiPost扩展 |
| `闪电观察` / `话题` | 闪电观察者 | `shandian_post.py` | 脉脉 | ⚠️需要MultiPost扩展 |

## ⚠️ 发帖决策树（收到发帖任务必读）

| 特征 | 爆料活动 | 闪电观察活动 | MultiPost三平台 |
|------|---------|-------------|---------------|
| 关键词 | "爆料"、"爆个料" | "闪电观察"、"话题" | "mutipost"、"MultiPost" |
| 内容格式 | 编号+标题+正文 | ##话题名+**第X篇｜标题** | 话题N+粗体导语+正文 |
| 有图 | ✅ 用户说"图片已存好" | ✅ Pexels自动搜图 | ✅ Pexels自动搜图 |
| 话题 | 固定"我来爆个料" | 每篇不同，需搜索匹配 | 每话题不同，需搜索匹配 |
| 平台 | 脉脉 | 脉脉 | 脉脉+公众号+头条 |

### 绝对不要做的事
1. ❌ 不要用 paste_post.py 发闪电观察内容
2. ❌ 不要用 shandian_post.py 发爆料内容
3. ❌ 不要忘记 cd 到项目目录
4. ❌ 不要自己改代码绕弯路，先看已有脚本是否已支持

## 爆料活动规则

- **话题固定**："我来爆个料"
- **有标题**：用户给的编号标题就是标题
- **有图**：图片放到 `posts/images/`（1.png→第1篇，2.png→第2篇...）
- **命令**：`python3 paste_post.py --file posts/xxx.txt`
- **输入格式**：
```
1. 标题：xxx
正文：第一段

第二段

2. 标题：xxx
正文：...
```
- 帖子之间只留空行，不要加 `---` 分隔符

## 闪电观察者规则

- **无标题**：标题就是话题名
- **动态话题**：每篇不同，需要搜索匹配
- **可选图**：默认不带图，加 `--no-image`；带图时去掉
- **命令**：`python3 shandian_post.py --file posts/shandian.txt --no-image`
- **每话题2篇**，用 `## 话题名称` 分隔

## MultiPost 三平台发布规则（mutipost 指令必读）

> ⚠️ MultiPost 模式需要 Chrome 安装 MultiPost 扩展。如果扩展不可用，使用 `directpost` 指令。

## DirectPublisher 直连三平台发布规则（directpost 指令必读）

> ✅ 零扩展依赖！Playwright 直接操作各平台发布页面。Windows/Mac 开箱即用。

### 与 MultiPost 的区别
- **不需要 MultiPost 扩展**——Playwright 直接打开各平台编辑器
- **每平台独立标签页**——打开→操作→关闭，不残留
- **接口签名兼容**——`publish()` 和 `batch_post()` 与 MultiPostPublisher 一致
- **Chrome 端口 9334**——避免与 MultiPost 的 9333 冲突

### 直连发布流程
1. 脉脉：打开 maimai.cn → 清表单 → 填正文 → 上传图片 → 切身份 → 加话题 → 勾开关 → 发动态
2. 公众号：打开 mp.weixin.qq.com → 新建图文 → 填标题 → 填正文 → 上传图片 → 保存草稿 ⚠️**不点发表**
3. 头条：打开 mp.toutiao.com → 填标题 → 上传图片 → 填正文 → 追加 #上头条 聊热点# → 发布

### 内容格式理解
- 与 MultiPost 模式完全一致：title=话题名，body=粗体导语+后续段落
- 话题名引号规则相同：必须用中文引号

### 发帖间隔
- `direct_post_interval = 90`（90秒，±30秒抖动）

### 命令
```bash
python3 direct_publish_0715.py        # 正式发布
python3 direct_publish_0715.py --dry-run  # 干跑测试（修改脚本 dry_run=True）
```

### 内容格式理解
- **title = 话题名**（即"话题N："后面的部分，如 `曝Claude脑子里长出"意识"`）
- **topic = 话题名**（同title，用于脉脉搜索匹配）
- **body = 粗体导语 + 后续段落**（所有文字内容合并为正文，原样保留）
- 粗体导语不是标题，是正文的第一段

### 内容完整性铁律
- ❌ 标题不能截断、改写、缩写
- ❌ 正文不能改动，一字不改
- ❌ 话题名不能改，标点不能丢（冒号、逗号、问号、引号）
- ❌ 不能把正文当话题名

### 话题名引号规则
- **话题名中的引号必须用中文引号 `""`（U+201C/U+201D）**
- **不能用英文引号 `""`（U+0022）**，否则脉脉搜索匹配不到
- Write 工具会将中文引号转成英文引号，所以话题名必须从脚本硬编码的 `TOPIC_TAGS` 读取，不从文件读取
- **⚠️ 创建脚本后必须运行 `python3 fix_topic_quotes.py <script.py>` 修复引号！** Write 创建脚本时也会把 TOPIC_TAGS 中的中文引号转成英文引号，必须用此工具修复

### 平台处理优先级（用户明确要求）
1. **脉脉** → 最先处理：切身份 → 加话题(带重试) → 勾两个开关(同步主页+昵称水印) → 发动态
2. **微信公众号** → 第二处理：点「保存为草稿」⚠️**不点「发表」**
3. **今日头条** → 最后处理：追加 `#上头条 聊热点#` → 点红色「发布」

### 标签页扫描机制
- MultiPost 点「发布」后，Chrome 扩展会打开3个平台编辑器标签页
- Playwright CDP 不会自动追踪扩展打开的标签页
- 解决：点发布后等20秒 → 重连一次 Playwright → 扫描 context.pages
- URL匹配为主，内容匹配为辅

### 发布后清理
- 每组发布完成后，自动关闭所有平台标签页（`_cleanup_platform_tabs`）
- 自动删除从 API 下载的本地图片（Pexels）

### Pexels 搜图
- 中文话题需翻译为英文关键词再搜图
- 例：字节豆包股 → ByteDance Doubao stock，微信支付AI专属卡 → WeChat Pay AI card
- 翻译原则：保留品牌英文原名，通用词用英文直译，尽量简短精准

### 发帖间隔
- `multipost_post_interval = 90`（90秒，±30秒抖动）
- `maimai_post_interval = 150`（2.5分钟，±30秒抖动）

### MultiPost 发布流程
1. 用户给 Claude 热点话题和文案
2. Claude 解析文章（标题=话题名，正文=粗体导语+后续段落）
3. Pexels API 搜配图（中文话题翻译为英文关键词，每话题1张）
4. 脉脉 + 公众号 + 头条全走 MultiPost（一次会话可同时勾选多平台）
5. 发布完成后自动清理平台标签页 + 删除本地下载的图片

## 跨平台兼容规则
- ⚠️ **所有代码必须兼容 Windows + Mac 双平台**
- 使用 `platform.system()` 检测操作系统
- 路径使用 `pathlib.Path`，不硬编码斜杠
- Chrome 启动参数适配不同平台（`start_chrome.py` 已处理）
- 考虑不同屏幕尺寸的兼容性

## 关键文件

| 文件 | 作用 |
|------|------|
| `publisher/multipost.py` | MultiPostPublisher 核心，统管所有平台 |
| `publisher/direct.py` | DirectPublisher 直连三平台（零扩展依赖） |
| `publisher/maimai.py` | MaimaiPageOps mixin（脉脉DOM操作） |
| `paste_post.py` | 爆料活动入口（固定话题，有标题，手动配图） |
| `shandian_post.py` | 闪电观察者入口（动态话题，无标题，自动搜图） |
| `multi_publish_0707.py` | MultiPost 批量发布脚本（模板） |
| `fix_topic_quotes.py` | 修复脚本中 TOPIC_TAGS 的中文引号 |
| `start_chrome.py` | Chrome 启动（Mac/Windows 双平台） |
| `adapter/image_search.py` | Pexels API 搜图 + 百度图片备用 |
| `adapter/compliance.py` | 图片合规打码 |
| `config.py` | 间隔配置、API Key（.env → pydantic Settings） |
| `db/database.py` | SQLite 存储 |

## 字段限制
- 脉脉标题：20字
- 脉脉正文：1000字
