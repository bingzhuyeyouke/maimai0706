# Claude Code 项目指引

本文件是 Claude Code 的项目级指令，克隆项目后自动生效。

## 发帖模式快捷指令

| 指令 | 模式 | 脚本 | 平台 |
|------|------|------|------|
| `mutipost` + 文案 | MultiPost三平台发布 | `multi_publish_0707.py` | 脉脉+公众号+头条 |
| `爆料` / `爆个料` | 爆料活动 | `paste_post.py` | 脉脉 |
| `闪电观察` / `话题` | 闪电观察者 | `shandian_post.py` | 脉脉 |

## MultiPost 三平台发布规则（mutipost 指令必读）

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

### 发帖间隔
- `multipost_post_interval = 90`（90秒，±30秒抖动）

## 关键文件

| 文件 | 作用 |
|------|------|
| `publisher/multipost.py` | MultiPostPublisher 核心，统管所有平台 |
| `publisher/maimai.py` | MaimaiPageOps mixin（脉脉DOM操作） |
| `multi_publish_0707.py` | MultiPost 批量发布脚本 |
| `start_chrome.py` | Chrome 启动（Mac/Windows 双平台） |
| `adapter/image_search.py` | Pexels API 搜图 |
| `config.py` | 间隔配置、API Key |
