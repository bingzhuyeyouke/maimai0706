"""
脉脉发帖模块 —— 自动在脉脉社区发布帖子

完整流程：
  1. 连接到用户已打开的 Chrome（带远程调试端口 9222）
  2. 打开脉脉社区发帖页（整个批量只打开一次）
  3. 切换身份为"职场领域创作者"
  4. 循环每篇帖子：填入标题/正文 → 添加话题 → 上传图片 → 点击"发动态" → 等待间隔

⚠️  前置条件：
  - Chrome 带调试端口(9222)启动
  - 已登录脉脉

⚠️  风险提示：
  - 发布是真实操作，会创建真实内容
  - 批量发帖需控制频率，建议每篇间隔 3 分钟
  - 不要短时间内大量发布，可能触发平台风控
"""

import platform
import random
import time
from typing import Optional, List

# 跨平台快捷键：Mac 用 Meta(Command)，Windows/Linux 用 Control
SELECT_ALL_KEY = "Meta+A" if platform.system() == "Darwin" else "Control+A"

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT


# ========== 常量 ==========

CDP_URL = "http://localhost:9222"
MAIMAI_HOME_URL = "https://maimai.cn/community/home/recommended"
DEFAULT_TOPIC = "我来爆个料"


class MaimaiPoster:
    """
    脉脉发帖器

    用法：
        poster = MaimaiPoster()
        poster.connect()
        poster.batch_post(posts=[...], interval=180)
        poster.disconnect()
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    def connect(self) -> bool:
        """连接到用户已启动的 Chrome"""
        logger.info(f"连接到 Chrome（{CDP_URL}）...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
            self._context = self._browser.contexts[0] if self._browser.contexts else None
            if not self._context:
                logger.error("❌ 未找到浏览器上下文")
                return False
            logger.success("✓ 已连接到 Chrome")
            return True
        except Exception as e:
            logger.error(f"❌ 连接 Chrome 失败: {e}")
            return False

    def disconnect(self):
        """断开连接"""
        if self._playwright:
            self._playwright.stop()
        logger.info("已断开 Chrome 连接")

    def post(
        self,
        content: str,
        title: str = "",
        image_paths: List[str] = None,
        topic: str = DEFAULT_TOPIC,
        dry_run: bool = False,
    ) -> bool:
        """单篇发帖（会先打开页面）"""
        page = self._open_post_page()
        self._switch_identity(page)
        return self._fill_and_publish(page, content, title, image_paths, topic, dry_run)

    def batch_post(
        self,
        posts: List[dict],
        interval: int = 180,
        dry_run: bool = False,
    ) -> dict:
        """
        批量发帖 —— 只打开一次页面，循环填内容+发布

        参数:
            posts:    帖子列表，每项 {"content": str, "title": str, "image_paths": list, "topic": str}
            interval: 发帖间隔秒数，默认180秒(3分钟)
            dry_run:  干跑模式
        """
        total = len(posts)
        success = 0
        failed = 0
        results = []

        logger.info(f"📋 批量发帖开始: 共 {total} 篇，间隔 {interval} 秒")

        # 只打开一次页面
        page = self._open_post_page()
        self._switch_identity(page)

        for i, post_data in enumerate(posts, 1):
            logger.info(f"\n{'='*40}")
            logger.info(f"📝 第 {i}/{total} 篇")
            logger.info(f"{'='*40}")

            try:
                result = self._fill_and_publish(
                    page,
                    content=post_data.get("content", ""),
                    title=post_data.get("title", ""),
                    image_paths=post_data.get("image_paths"),
                    topic=post_data.get("topic", DEFAULT_TOPIC),
                    dry_run=dry_run,
                )

                if result:
                    success += 1
                    results.append({"index": i, "status": "success"})
                else:
                    failed += 1
                    results.append({"index": i, "status": "failed"})

            except Exception as e:
                logger.error(f"❌ 第 {i} 篇发帖失败: {e}")
                failed += 1
                results.append({"index": i, "status": "failed", "error": str(e)})
                # 出错后尝试重新打开页面并切换身份
                try:
                    page = self._open_post_page()
                    self._switch_identity(page)
                except Exception:
                    pass

            # 不是最后一篇时等待（随机抖动防检测）
            if i < total and not dry_run:
                jitter = random.randint(-30, 30)  # ±30秒抖动
                actual_wait = max(60, interval + jitter)  # 最少等1分钟
                logger.info(f"⏳ 等待 {actual_wait} 秒后发布下一篇...")
                time.sleep(actual_wait)

        logger.info(f"\n{'='*40}")
        logger.info(f"🏁 批量发帖完成: 成功 {success}, 失败 {failed}")
        logger.info(f"{'='*40}")

        return {"success": success, "failed": failed, "results": results}

    # ========== 核心发帖流程 ==========

    def _fill_and_publish(
        self,
        page: Page,
        content: str,
        title: str = "",
        image_paths: List[str] = None,
        topic: str = DEFAULT_TOPIC,
        dry_run: bool = False,
    ) -> bool:
        """在同一页面上填入内容并发布"""

        # 干跑模式下先清空上一篇残留内容
        if dry_run:
            self._clear_form(page)

        # 填入标题
        if title:
            self._fill_title(page, title)

        # 填入正文
        self._fill_content(page, content)

        # 添加话题（搜不到自动跳过，不阻塞发帖）
        if topic:
            topic_ok = self._add_topic(page, topic)
            if not topic_ok:
                # 刷新页面重试1次（解决弹窗不渲染的问题）
                logger.info(f"  刷新页面重试添加话题 (第1次)...")
                page.keyboard.press("Escape")
                time.sleep(1)
                page.reload(wait_until="domcontentloaded", timeout=15000)
                time.sleep(5)
                if title:
                    self._fill_title(page, title)
                self._fill_content(page, content)
                topic_ok = self._add_topic(page, topic)
                if not topic_ok:
                    logger.warning(f"  ⚠️ 添加话题失败: {topic}，跳过话题继续发布")

        # 上传图片
        if image_paths:
            self._upload_images(page, image_paths)

        # 确保发布设置开关已开启（同步主页 + 昵称水印）
        self._enable_publish_settings(page)

        # 截图预览
        self._save_screenshot(page, f"maimai_before_post_{int(time.time())}")

        if dry_run:
            logger.info("🔍 干跑模式：内容已填入，但不点击发布")
            return True

        # 点击"发动态"
        result = self._click_publish(page)

        # 发布后导航回首页（发布后页面会跳转到帖子详情，需要回到发帖页）
        time.sleep(2)
        logger.info("  导航回社区首页，准备下一篇...")
        try:
            page.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
            # 刷新页面确保DOM状态干净（否则弹窗可能不渲染）
            page.reload(wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
        except Exception:
            logger.warning("  ⚠️ 导航回首页失败，下一篇会自动重试")

        return result

    # ========== 内部方法 ==========

    def _open_post_page(self) -> Page:
        """打开发帖页 —— 确保编辑器存在，不存在则导航到社区首页"""
        logger.info("打开脉脉社区发帖页...")

        # 找已有的脉脉标签页
        for pg in self._context.pages:
            try:
                if "maimai.cn" in pg.url and not pg.is_closed():
                    self._page = pg
                    # 验证编辑器是否存在（标题输入框 + 正文 contenteditable）
                    editor_ok = pg.evaluate('''() => {
                        const titleInput = document.querySelector('input[placeholder*="标题"]');
                        const contentEditor = document.querySelector('[contenteditable="true"]');
                        const rect1 = titleInput ? titleInput.getBoundingClientRect() : null;
                        const rect2 = contentEditor ? contentEditor.getBoundingClientRect() : null;
                        return titleInput && contentEditor
                            && rect1 && rect1.width > 50
                            && rect2 && rect2.width > 50;
                    }''')
                    if editor_ok:
                        pg.reload(wait_until="domcontentloaded", timeout=15000)
                        time.sleep(3)
                        logger.success("✓ 发帖页已打开（复用现有标签页）")
                        return pg
                    else:
                        logger.info("  现有标签页无编辑器，导航到社区首页...")
                        pg.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
                        time.sleep(3)
                        # 再次检查
                        editor_ok2 = pg.evaluate('''() => {
                            const titleInput = document.querySelector('input[placeholder*="标题"]');
                            const contentEditor = document.querySelector('[contenteditable="true"]');
                            return titleInput && contentEditor
                                && titleInput.getBoundingClientRect().width > 50
                                && contentEditor.getBoundingClientRect().width > 50;
                        }''')
                        if editor_ok2:
                            logger.success("✓ 发帖页已打开（导航后编辑器就绪）")
                            return pg
            except Exception:
                continue

        # 新建页面
        page = self._context.new_page()
        page.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(5)
        self._page = page

        if "signin" in page.url:
            raise RuntimeError("未登录脉脉，请先登录")

        logger.success("✓ 发帖页已打开")
        return page

    def _switch_identity(self, page: Page):
        """确保身份为'职场领域创作者'"""
        logger.info("检查发帖身份...")

        # 检查当前身份文本是否包含"职场领域创作者"
        current = page.evaluate('''() => {
            const all = document.querySelectorAll('span, div');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                // 找包含"职场领域创作者"且不太大的文本元素（精确匹配身份标签）
                if (t.includes('职场领域创作者') && t.length < 30
                    && rect.width > 50 && rect.width < 300
                    && rect.y > 80 && rect.y < 200) {
                    return t.substring(0, 30);
                }
            }
            return '';
        }''')

        if '职场领域创作者' in current:
            logger.info("  ✓ 身份已是职场领域创作者")
            return

        logger.info("  切换身份为职场领域创作者...")

        # 点击"切换"文字（精确匹配span text==="切换"，class含text-primary）
        clicked_switch = page.evaluate('''() => {
            const all = document.querySelectorAll('span, a, div');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();
                if (t === '切换' && rect.y > 80 && rect.y < 200
                    && rect.width > 10 && rect.width < 80) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if not clicked_switch:
            logger.warning("  ⚠️ 未找到切换按钮")
            return

        time.sleep(2)

        # 选择"职场领域创作者"
        selected = page.evaluate('''() => {
            const all = document.querySelectorAll('span, div, li, p');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if (t === '职场领域创作者' && el.children.length === 0
                    && rect.width > 50 && rect.width < 300) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if selected:
            logger.success("  ✓ 已切换为职场领域创作者")
        else:
            logger.warning("  ⚠️ 未找到职场领域创作者选项")
        time.sleep(1)

    def _fill_title(self, page: Page, title: str):
        """填入标题，标题为空则跳过"""
        if not title or not title.strip():
            logger.info("  标题为空，跳过填入")
            return
        logger.info(f"填入标题: {title[:20]}...")
        title = title[:20]

        # 先清空已有内容
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill("")  # Playwright fill 会自动清空
            title_input.first.fill(title)
            logger.success(f"  ✓ 标题已填入: {title}")
        else:
            # 用 JS 方式
            filled = page.evaluate('''(title) => {
                const inputs = document.querySelectorAll('input');
                for (const input of inputs) {
                    const ph = (input.placeholder || '') + (input.getAttribute('aria-label') || '');
                    if (ph.includes('标题')) {
                        // 用 nativeInputValueSetter 确保 React 感知
                        const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        nativeInputValueSetter.call(input, title);
                        input.dispatchEvent(new Event('input', { bubbles: true }));
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }''', title)
            if filled:
                logger.success(f"  ✓ 标题已填入: {title}")
            else:
                logger.warning("  ⚠️ 未找到标题输入框（标题为选填，继续）")

        time.sleep(0.5)

    def _clear_form(self, page: Page):
        """清空发帖表单（干跑模式下防止残留内容影响下一篇）"""
        logger.debug("清空表单残留内容...")

        # 清空标题
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.first.fill("")

        # 清空正文 contenteditable
        editor = page.locator('[contenteditable="true"]')
        if editor.count() > 0:
            editor.first.click()
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            time.sleep(0.3)

        # 清空已添加的话题标签（找到话题标签旁边的 × 按钮并点击）
        page.evaluate('''() => {
            // 话题标签通常有 "×" 关闭按钮，或者直接删除话题容器
            const closeButtons = document.querySelectorAll('svg, button, div');
            for (const btn of closeButtons) {
                const rect = btn.getBoundingClientRect();
                const parent = btn.closest('[class*="cursor-pointer"]');
                // 找话题标签区的小×按钮
                if (rect.width > 0 && rect.width < 25 && rect.height > 0 && rect.height < 25
                    && rect.y > 250 && rect.y < 320) {
                    const svg = btn.querySelector('svg');
                    if (svg && (btn.getAttribute('aria-label')?.includes('关闭')
                        || btn.getAttribute('aria-label')?.includes('close')
                        || (btn.textContent || '').trim() === '×')) {
                        btn.click();
                    }
                }
            }
        }''')

        time.sleep(0.3)

    def _fill_content(self, page: Page, content: str):
        """填入正文"""
        logger.info(f"填入正文: {len(content)} 字")
        content = content[:1000]

        # 先清空已有内容
        # 策略1：textarea
        textarea = page.locator('textarea[placeholder*="想法"], textarea[placeholder*="分享"]')
        if textarea.count() > 0:
            textarea.first.click()
            # 全选并删除已有内容
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            time.sleep(0.2)
            textarea.first.fill(content)
            logger.success(f"  ✓ 正文已填入 (textarea)")
            time.sleep(0.5)
            return

        # 策略2：contenteditable
        editor = page.locator('[contenteditable="true"]')
        if editor.count() > 0:
            editor.first.click()
            # 全选删除已有内容
            page.keyboard.press(SELECT_ALL_KEY)
            page.keyboard.press("Backspace")
            time.sleep(0.2)
            page.keyboard.type(content, delay=10)
            logger.success(f"  ✓ 正文已填入 (contenteditable)")
            time.sleep(0.5)
            return

        raise RuntimeError("未找到正文输入框")

    def _add_topic(self, page: Page, topic: str):
        """
        添加话题 —— 带重试机制：
          1. 点击「添加话题」按钮
          2. 等待弹出面板（等待最多10秒）
          3. 在搜索框输入话题名称
          4. 点击搜索结果
          如果搜索框不出，返回 False 让调用方刷新页面重试
        """
        logger.info(f"添加话题: {topic}")

        # 1. 点击「添加话题」按钮
        clicked = page.evaluate('''() => {
            const all = document.querySelectorAll('div, span, label');
            let best = null;
            let bestArea = Infinity;

            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();
                const area = rect.width * rect.height;

                if (t.includes('添加话题') && rect.y > 250 && rect.width > 0
                    && cls.includes('cursor-pointer')) {
                    if (area < bestArea) {
                        bestArea = area;
                        best = el;
                    }
                }
            }

            if (best) {
                best.click();
                return best.textContent.trim();
            }

            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if ((t === '添加话题' || t === '# 添加话题') && rect.y > 250 && rect.width < 150) {
                    el.click();
                    return t;
                }
            }

            return false;
        }''')

        if not clicked:
            logger.warning("  ⚠️ 未找到'添加话题'按钮")
            return False

        logger.info(f"  已点击添加话题按钮: {clicked}")
        time.sleep(3)

        # 2. 在弹出面板的搜索框中输入话题名称（等待最多10秒）
        # 策略：优先匹配 input[type="search"]（弹出面板），y>100 排除顶部导航栏(y≈24)
        #       备用 input[type="text"]，y>250 排除标题输入框(y≈161)
        popup_search = None
        for _ in range(10):
            # 优先：type=search 且 y>100（排除顶部导航搜索栏）
            for inp in page.locator('input[type="search"]').all():
                try:
                    box = inp.bounding_box()
                    if box and box['y'] > 100 and box['width'] > 50:
                        popup_search = inp
                        break
                except Exception:
                    continue
            # 备用：type=text 且 y>250（排除标题输入框）
            if not popup_search:
                for inp in page.locator('input[type="text"]').all():
                    try:
                        box = inp.bounding_box()
                        if box and box['y'] > 250 and box['width'] > 50:
                            popup_search = inp
                            break
                    except Exception:
                        continue
            if popup_search:
                break
            time.sleep(1)

        if not popup_search:
            logger.warning("  ⚠️ 搜索框未出现")
            return False

        popup_search.click()
        time.sleep(0.5)
        popup_search.fill(topic)
        logger.info(f"  已在弹出搜索框输入: {topic}")
        time.sleep(2)

        # 3. 点击搜索结果（精确匹配：话题名后必须紧跟数字"XX条帖子"，而非"，其他文字"）
        selected = page.evaluate('''(topic) => {
            const all = document.querySelectorAll('div');
            let exactRow = null;
            let exactLen = Infinity;
            let prefixRow = null;
            let prefixLen = Infinity;

            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const cls = (el.className || '').toString();

                if (!cls.includes('cursor-pointer') || rect.y < 300 || rect.height < 30 || rect.height > 60) continue;
                if (!t.includes(topic)) continue;

                // 精确匹配：话题名后紧跟数字（如"我来爆个料130721条帖子"）
                // 排除话题名后跟逗号等文字的（如"我来爆个料，进来瞅瞅22条帖子"）
                const afterTopic = t.substring(topic.length);
                const isExactTopic = t.startsWith(topic) && (afterTopic.length === 0 || /^\d/.test(afterTopic));

                if (isExactTopic) {
                    if (t.length < exactLen) { exactLen = t.length; exactRow = el; }
                } else {
                    if (t.length < prefixLen) { prefixLen = t.length; prefixRow = el; }
                }
            }

            const target = exactRow || prefixRow;
            if (target) {
                target.click();
                return { match: exactRow ? 'exact' : 'prefix', text: target.textContent.trim().substring(0, 30) };
            }
            return null;
        }''', topic)

        if selected:
            logger.success(f"  ✓ 话题已点击: {selected.get('text', topic)}")
            time.sleep(2)
            page.keyboard.press("Escape")
            time.sleep(1)
            return True
        else:
            logger.warning("  ⚠️ 搜索结果中未找到话题")
            return False

    def _upload_images(self, page: Page, image_paths: List[str]):
        """
        上传图片 —— 直接通过 #picture file input 上传（无需先点图标）
        """
        logger.info(f"上传图片: {len(image_paths)} 张")

        try:
            # 直接用 #picture file input 上传
            picture_input = page.locator('#picture')
            if picture_input.count() > 0:
                picture_input.set_input_files(image_paths)
                logger.info(f"  ✓ 上传 {len(image_paths)} 张图片成功 (#picture)")
                time.sleep(3)
            else:
                # 备用：找其他 file input
                image_input = page.locator('input[type="file"][accept*="image"]')
                if image_input.count() > 0:
                    image_input.first.set_input_files(image_paths)
                    logger.info(f"  ✓ 上传 {len(image_paths)} 张图片成功 (file input)")
                    time.sleep(3)
                else:
                    logger.warning("  ⚠️ 未找到图片上传 file input")
        except Exception as e:
            logger.warning(f"  ⚠️ 图片上传异常: {e}")

        logger.success("✓ 图片上传完成")

    def _enable_publish_settings(self, page: Page):
        """确保发布设置面板中的两个开关已开启：
        1. 发布后同步到我的主页展示（匿名/社区身份）
        2. 使用昵称作为水印

        这两个开关在页面刷新后状态会丢失，每次发帖前需检查。
        设置面板通过工具栏中的蓝色圆形按钮展开，面板内有 h3"发布设置" 标题。
        """
        logger.info("检查发布设置开关...")

        try:
            # ===== 第1步：确保设置面板已展开 =====
            # 工具栏结构（y随内容高度变化，需动态定位）：
            #   x≈939  DIV (图片图标)
            #   x≈983  BUTTON (表情图标)
            #   x≈1027 BUTTON (设置按钮 - 蓝色圆形，触发"发布设置"面板)
            #   x≈1071 DIV (另一个图标)
            #   x≈1115 DIV "添加话题"
            #   x≈1480 BUTTON "发动态"
            # 设置按钮是工具栏中从左数第3个BUTTON，24x24px，无文字
            panel_open = page.evaluate('''() => {
                // 检查设置面板是否已经打开
                const h3s = document.querySelectorAll('h3');
                for (const h3 of h3s) {
                    if ((h3.textContent || '').trim() === '发布设置'
                        && h3.getBoundingClientRect().width > 0) {
                        return true;
                    }
                }
                return false;
            }''')

            if not panel_open:
                # 点击设置按钮：工具栏第3个button（x≈1027, 24x24px, 无文字）
                # 也可以通过"在'添加话题'左边最近的button"来定位
                clicked = page.evaluate('''() => {
                    // 策略1：找到"添加话题"文字，然后往左找最近的button
                    const allDivs = document.querySelectorAll('div');
                    let topicEl = null;
                    for (const div of allDivs) {
                        const t = (div.textContent || '').trim();
                        const rect = div.getBoundingClientRect();
                        const cls = (div.className || '').toString();
                        if (t === '添加话题' && rect.width > 50 && rect.width < 150
                            && rect.height > 15 && rect.height < 30) {
                            topicEl = div;
                            break;
                        }
                    }

                    if (topicEl) {
                        const topicRect = topicEl.getBoundingClientRect();
                        // 找"添加话题"左边的所有button，选最近的一个
                        const buttons = document.querySelectorAll('button');
                        let bestBtn = null;
                        let bestDist = Infinity;
                        for (const btn of buttons) {
                            const rect = btn.getBoundingClientRect();
                            const t = (btn.textContent || '').trim();
                            // 在同一行（y差<15），在添加话题左边，不是"发动态"
                            if (Math.abs(rect.y - topicRect.y) < 15
                                && rect.x < topicRect.x
                                && t !== '发动态' && t !== '发布'
                                && rect.width > 15 && rect.width < 40) {
                                const dist = topicRect.x - rect.x;
                                if (dist < bestDist) {
                                    bestDist = dist;
                                    bestBtn = btn;
                                }
                            }
                        }
                        if (bestBtn) {
                            bestBtn.click();
                            return { strategy: 'left_of_topic', x: Math.round(bestBtn.getBoundingClientRect().x) };
                        }
                    }

                    // 策略2：如果上面没找到，找工具栏中所有24x24的无文字button
                    // 逐个点击直到"发布设置"面板出现
                    const buttons2 = document.querySelectorAll('button');
                    const candidates = [];
                    for (const btn of buttons2) {
                        const rect = btn.getBoundingClientRect();
                        const t = (btn.textContent || '').trim();
                        if (rect.width >= 20 && rect.width <= 30
                            && rect.height >= 20 && rect.height <= 30
                            && t === '' && rect.y > 300) {
                            candidates.push(btn);
                        }
                    }
                    // 按x排序
                    candidates.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
                    for (const btn of candidates) {
                        btn.click();
                        // 检查面板是否出现
                        const h3s = document.querySelectorAll('h3');
                        for (const h3 of h3s) {
                            if ((h3.textContent || '').trim() === '发布设置'
                                && h3.getBoundingClientRect().width > 0) {
                                return { strategy: 'try_each_button', x: Math.round(btn.getBoundingClientRect().x) };
                            }
                        }
                    }

                    return null;
                }''')

                if clicked:
                    logger.info(f"  ⚙️ 点击设置按钮展开面板 (x≈{clicked.get('x')})")
                    time.sleep(1.5)
                else:
                    logger.info("  ⚙️ 未找到设置按钮，尝试直接检查开关...")

            # ===== 第2步：检查并启用两个开关 =====
            #    开关是 button[role="switch"]，状态由 aria-checked 控制
            #    每个 switch 附近有 label 包含说明文字
            toggles_result = page.evaluate('''() => {
                const result = { sync_home: null, nickname_watermark: null, enabled: 0 };

                // 查找所有 role="switch" 的按钮
                const switches = document.querySelectorAll('button[role="switch"]');
                for (const sw of switches) {
                    const ariaChecked = sw.getAttribute('aria-checked');
                    const swRect = sw.getBoundingClientRect();

                    // 找到对应的 label —— 在 switch 同一行（y接近）且在 switch 左侧
                    const labels = document.querySelectorAll('label');
                    for (const label of labels) {
                        const labelText = (label.textContent || '').trim();
                        const labelRect = label.getBoundingClientRect();

                        // label 和 switch 在同一行（y差值<20），label 在 switch 左边
                        if (Math.abs(labelRect.y - swRect.y) < 20 && labelRect.x < swRect.x) {
                            if (labelText.includes('发布后同步到我的主页展示')) {
                                result.sync_home = { ariaChecked };
                                if (ariaChecked !== 'true') {
                                    sw.click();
                                    result.enabled++;
                                }
                            } else if (labelText.includes('使用昵称作为水印')) {
                                result.nickname_watermark = { ariaChecked };
                                if (ariaChecked !== 'true') {
                                    sw.click();
                                    result.enabled++;
                                }
                            }
                        }
                    }
                }

                return result;
            }''')

            # 日志输出
            if toggles_result:
                sync = toggles_result.get('sync_home')
                watermark = toggles_result.get('nickname_watermark')

                if sync:
                    status = '✓ 已开启' if sync.get('ariaChecked') == 'true' else '✅ 未开启，已点击开启'
                    logger.info(f"  {status} \"发布后同步到我的主页展示\"")
                else:
                    logger.info("  ⚠️ 未找到\"发布后同步到我的主页展示\"开关")

                if watermark:
                    status = '✓ 已开启' if watermark.get('ariaChecked') == 'true' else '✅ 未开启，已点击开启'
                    logger.info(f"  {status} \"使用昵称作为水印\"")
                else:
                    logger.info("  ⚠️ 未找到\"使用昵称作为水印\"开关")

                if toggles_result.get('enabled', 0) > 0:
                    logger.info(f"  ✓ 发布设置检查完成，已启用 {toggles_result['enabled']} 个开关")
                    time.sleep(0.5)
                elif sync and watermark:
                    logger.info("  ✓ 发布设置检查完成，开关均已开启")
                else:
                    logger.info("  ⚠️ 发布设置检查完成，但部分开关未找到")
            else:
                logger.info("  ⚠️ 未找到发布设置开关（面板可能未展开）")

            # ===== 第3步：关闭设置面板（按 Escape）=====
            page.keyboard.press("Escape")
            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"  ⚠️ 发布设置检查异常: {e}，跳过（不影响发帖）")

    def _click_publish(self, page: Page) -> bool:
        """点击'发动态'按钮"""
        logger.info("⚠️  点击'发动态'按钮...")

        # 先确保没有弹窗挡着（按 Escape 关闭任何残留面板）
        page.keyboard.press("Escape")
        time.sleep(1)

        # 点击"发动态"按钮
        # 优先点击 <button> 元素（最可靠），确保按钮可见且可点击
        clicked = page.evaluate('''() => {
            // 优先 button
            const buttons = document.querySelectorAll('button');
            for (const btn of buttons) {
                const t = (btn.textContent || '').trim();
                const rect = btn.getBoundingClientRect();
                if ((t === '发动态' || t === '发布') && rect.width > 0 && !btn.disabled) {
                    btn.click();
                    return { tag: 'button', text: t };
                }
            }
            // 备用
            const all = document.querySelectorAll('div, span');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                if ((t === '发动态' || t === '发布') && rect.width > 50 && rect.y > 200) {
                    el.click();
                    return { tag: el.tagName, text: t };
                }
            }
            return null;
        }''')

        if not clicked:
            raise RuntimeError("未找到'发动态'按钮")

        logger.info(f"  ✓ 已点击: {clicked.get('tag')}.{clicked.get('text')}")

        # 等待发布完成 — 页面可能跳转到帖子详情页
        time.sleep(5)

        # 截图验证发布结果
        self._save_screenshot(page, f"maimai_after_post_{int(time.time())}")
        logger.success("✓ 发帖完成")
        return True

    # ========== 截图工具 ==========

    def _save_screenshot(self, page: Page, name: str):
        """保存调试截图"""
        try:
            debug_dir = PROJECT_ROOT / "debug_screenshots"
            debug_dir.mkdir(exist_ok=True)
            page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
            logger.debug(f"  截图已保存: {name}.png")
        except Exception:
            pass
