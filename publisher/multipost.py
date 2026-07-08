"""
MultiPost 发布模块 —— 自动操作 MultiPost 网页端发布内容到多平台

完整流程：
  1. 连接到用户已打开的 Chrome（带远程调试端口 9222）
  2. 打开 MultiPost 编辑器（multipost.app）
  3. 上传图片
  4. 填入标题和正文
  5. 点击「下一步」（蓝色箭头按钮）
  6. 取消全选，勾选目标平台（头条/公众号）
  7. 点击发布按钮
  8. 检测新打开的平台标签页
  9. 在各平台标签页中填入标题、正文、分类，点击各平台发布按钮

⚠️  前置条件：
  - 用户需要先启动 Chrome 并打开远程调试端口：python3 start_chrome.py
    （start_chrome.py 已支持 Mac/Windows 双平台）
  - 用户需要已登录 MultiPost（multipost.app）
  - 用户需要已登录各目标平台（头条/公众号）

⚠️  风险提示：
  - 发布是真实操作，会在平台上创建真实内容
  - 建议先用测试内容验证流程，确认无误后再用正式内容
  - 不要短时间内大量发布，可能触发平台风控
"""

import random
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT
from publisher.maimai import MaimaiPageOps, MAIMAI_HOME_URL, DEFAULT_TOPIC


# ========== 常量 ==========

# Chrome 远程调试地址
CDP_URL = "http://localhost:9222"

# MultiPost 编辑器地址
MULTIPOST_URL = "https://multipost.app/"

# 默认要发布的平台
DEFAULT_PLATFORMS = ["微信公众号", "今日头条"]

# MultiPost 扩展 ID
MULTIPOST_EXT_ID = "dhohkaclnjgcikfoaacfgijgjgceofih"

# 今日头条正文末尾要追加的话题（用户要求）
TOUTIAO_HASHTAG = "#上头条 聊热点#"


class MultiPostPublisher(MaimaiPageOps):
    """
    MultiPost 发布器

    用法：
        publisher = MultiPostPublisher()
        publisher.connect()
        publisher.publish(title="标题", body="正文", platforms=["今日头条", "微信公众号"])
        publisher.disconnect()

    继承 MaimaiPageOps 以获得脉脉页面的 DOM 操作（添加话题/勾选开关/发动态），
    在 _publish_maimai 中用于 MultiPost 打开并填好的脉脉标签页。
    """

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        # 脉脉话题，publish() 时设置，_publish_maimai 读取
        self._maimai_topic: Optional[str] = None
        # 本次 publish 选中的平台列表，_click_publish 据此决定是否扫描脉脉标签页
        self._selected_platforms: Optional[list] = None

    def connect(self) -> bool:
        """
        连接到用户已启动的 Chrome 浏览器

        返回:
            True 连接成功，False 连接失败
        """
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
            logger.info("请先启动 Chrome（带调试端口）：")
            import platform
            if platform.system() == "Windows":
                logger.info("  python start_chrome.py")
                logger.info("  或手动: chrome.exe --remote-debugging-port=9222 --user-data-dir=%TEMP%\\chrome-automation-profile")
            else:
                logger.info("  python3 start_chrome.py")
                logger.info("  或手动: /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-automation-profile")
            return False

    def disconnect(self):
        """断开连接（不关闭用户的 Chrome）"""
        # 不关闭 browser，因为是用户的 Chrome
        if self._playwright:
            self._playwright.stop()
        logger.info("已断开 Chrome 连接")

    def publish(
        self,
        title: str,
        body: str,
        platforms: list[str] = None,
        image_paths: list[str] = None,
        maimai_topic: str = None,
        dry_run: bool = False,
    ) -> bool:
        """
        发布内容到 MultiPost

        参数:
            title:       文章标题
            body:        文章正文
            platforms:   目标平台列表，默认 ["微信公众号", "今日头条"]
            image_paths: 要上传的本地图片路径列表
            maimai_topic: 脉脉话题（仅 platforms 含「脉脉」时使用），默认 "我来爆个料"
            dry_run:     干跑模式——只填内容选平台，不点最终发布按钮

        返回:
            True 发布成功，False 失败
        """
        if platforms is None:
            platforms = DEFAULT_PLATFORMS

        # 脉脉话题供 _publish_maimai 读取（单线程同步，无并发问题）
        self._maimai_topic = maimai_topic or DEFAULT_TOPIC
        self._selected_platforms = platforms

        try:
            # 第1步：打开 MultiPost 编辑器
            page = self._open_editor()

            # 第2步：上传图片（在填文字之前，因为上传后光标位置更可控）
            if image_paths:
                self._upload_images(page, image_paths)

            # 第3步：填入标题和正文
            self._fill_content(page, title, body)

            # 第4步：点击「下一步」
            self._click_next(page)

            # 第5步：先取消所有已勾选平台，再勾选目标平台
            self._deselect_all_platforms(page)
            self._select_platforms(page, platforms)

            if dry_run:
                logger.info("🔍 干跑模式：内容已填入，平台已选择，但不点击发布")
                page.screenshot(path="debug_screenshots/dry_run_preview.png", full_page=True)
                return True

            # 第6步：点击发布 + 处理平台标签页
            result = self._click_publish(page, title, body)

            return result

        except Exception as e:
            logger.error(f"❌ 发布失败: {e}")
            return False

    def batch_post(
        self,
        posts: list,
        platforms: list[str] = None,
        interval: int = None,
        dry_run: bool = False,
        cleanup_images: bool = True,
    ) -> dict:
        """
        批量发布 —— 逐篇调用 publish，篇与篇之间等待 interval 秒

        参数:
            posts:    帖子列表，每项 {"title": str, "body": str, "image_paths": list}
            platforms: 目标平台列表，默认 ["微信公众号", "今日头条"]
            interval: 发帖间隔秒数，默认读 settings.multipost_post_interval（180秒≈3分钟）
            dry_run:  干跑模式
            cleanup_images: 发布完成后是否自动删除本地图片（默认 True，节省磁盘空间）

        返回:
            {"success": int, "failed": int, "results": list}

        间隔机制与脉脉 batch_post 一致：±30 秒随机抖动，最少等 60 秒。
        """
        if platforms is None:
            platforms = DEFAULT_PLATFORMS
        if interval is None:
            interval = settings.multipost_post_interval

        total = len(posts)
        success = 0
        failed = 0
        results = []

        logger.info(
            f"📋 MultiPost 批量发布开始: 共 {total} 篇，间隔 {interval} 秒（±30秒抖动）"
        )

        for i, post_data in enumerate(posts, 1):
            logger.info(f"\n{'=' * 40}")
            logger.info(f"📝 第 {i}/{total} 篇")
            logger.info(f"{'=' * 40}")

            # 锁屏恢复重试：如果 Chrome 不响应，等待恢复后重试当前篇
            max_retries = 3
            for retry in range(max_retries):
                try:
                    result = self.publish(
                        title=post_data.get("title", ""),
                        body=post_data.get("body", ""),
                        platforms=platforms,
                        image_paths=post_data.get("image_paths"),
                        maimai_topic=post_data.get("topic"),
                        dry_run=dry_run,
                    )
                    if result:
                        success += 1
                        results.append({"index": i, "status": "success"})
                    else:
                        failed += 1
                        results.append({"index": i, "status": "failed"})
                    break  # 成功或普通失败，跳出重试循环

                except RuntimeError as e:
                    if "Chrome 长时间未响应" in str(e) and retry < max_retries - 1:
                        # 锁屏导致的失败，等更久后重试
                        wait_time = 60 * (retry + 1)  # 60s, 120s
                        logger.warning(f"  ⏳ Chrome 未响应，等待 {wait_time} 秒后重试（第{retry+1}次）...")
                        time.sleep(wait_time)
                        # 重连 Chrome
                        try:
                            if self._playwright:
                                try:
                                    self._playwright.stop()
                                except Exception:
                                    pass
                            self._playwright = sync_playwright().start()
                            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
                            self._context = self._browser.contexts[0] if self._browser.contexts else None
                            if self._context:
                                logger.success("  ✓ Chrome 已重连")
                                continue
                        except Exception:
                            pass
                        continue
                    else:
                        # 非锁屏错误或重试耗尽
                        logger.error(f"❌ 第 {i} 篇发布失败: {e}")
                        failed += 1
                        results.append({"index": i, "status": "failed", "error": str(e)})
                        break

                except Exception as e:
                    logger.error(f"❌ 第 {i} 篇发布失败: {e}")
                    failed += 1
                    results.append({"index": i, "status": "failed", "error": str(e)})
                    break

            # 不是最后一篇时等待（随机抖动防检测，与脉脉一致）
            if i < total and not dry_run:
                jitter = random.randint(-30, 30)  # ±30秒抖动
                actual_wait = max(60, interval + jitter)  # 最少等1分钟
                logger.info(f"⏳ 等待 {actual_wait} 秒后发布下一篇...")
                time.sleep(actual_wait)

        logger.info(f"\n{'=' * 40}")
        logger.info(f"🏁 MultiPost 批量发布完成: 成功 {success}, 失败 {failed}")
        logger.info(f"{'=' * 40}")

        # 发布完成后自动删除本地图片（从 API 下载的，发完就没用了，省磁盘）
        if cleanup_images and not dry_run:
            all_image_paths = []
            for p in posts:
                for ip in (p.get("image_paths") or []):
                    if ip and ip not in all_image_paths:
                        all_image_paths.append(ip)
            self._cleanup_downloaded_images(all_image_paths)

        return {"success": success, "failed": failed, "results": results}

    def _ensure_chrome_responsive(self, timeout: int = 120) -> bool:
        """确保 Chrome 可响应（处理锁屏/休眠场景）

        锁屏后 Chrome 页面会被挂起，Playwright 操作超时。
        此方法检测 Chrome 是否响应，不响应则等待恢复（用户解锁屏幕），
        然后重连 Playwright。

        参数:
            timeout: 最长等待秒数（默认120秒=2分钟）

        返回:
            True Chrome 已恢复，False 超时未恢复
        """
        # 先快速检查当前连接是否可用
        try:
            pages = self._context.pages if self._context else []
            if pages:
                pages[0].evaluate('1+1', timeout=5000)
                return True
        except Exception:
            pass

        # Chrome 不响应，等待恢复
        logger.info("⏳ Chrome 未响应（可能屏幕已锁定），等待恢复...")
        start = time.time()
        while time.time() - start < timeout:
            try:
                # 断开旧连接，重连
                if self._playwright:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass
                time.sleep(2)

                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
                self._context = self._browser.contexts[0] if self._browser.contexts else None

                if self._context:
                    pages = self._context.pages
                    if pages:
                        pages[0].evaluate('1+1', timeout=5000)
                        elapsed = int(time.time() - start)
                        logger.success(f"✓ Chrome 已恢复响应（等待 {elapsed} 秒）")
                        return True
            except Exception:
                pass
            time.sleep(5)

        logger.error(f"❌ Chrome {timeout} 秒内未恢复响应")
        return False

    def _open_editor(self) -> Page:
        """第1步：打开或切换到 MultiPost 编辑器（确保在编辑状态，不是平台选择页）

        支持锁屏恢复：如果页面加载超时，会自动等待 Chrome 恢复响应后重试。
        """
        logger.info("打开 MultiPost 编辑器...")

        # 先看看是否已经打开了 multipost 页面
        found_page = None
        for pg in self._context.pages:
            if "multipost.app" in pg.url and "signin" not in pg.url:
                found_page = pg
                break

        if found_page:
            # 已有页面，刷新回到编辑器初始状态
            logger.info("  刷新页面回到编辑器...")
            try:
                found_page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                logger.warning("  ⚠️ 页面刷新超时，可能屏幕已锁定，等待恢复...")
                if not self._ensure_chrome_responsive():
                    raise RuntimeError("Chrome 长时间未响应，请解锁屏幕后重试")
                # 恢复后重新查找 multipost 页面（重连后 pages 引用可能失效）
                for pg in self._context.pages:
                    if "multipost.app" in pg.url and "signin" not in pg.url:
                        found_page = pg
                        break
                found_page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._page = found_page
        else:
            # 没有就新建
            page = self._context.new_page()
            try:
                page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            except Exception:
                logger.warning("  ⚠️ 新页面加载超时，等待恢复...")
                if not self._ensure_chrome_responsive():
                    raise RuntimeError("Chrome 长时间未响应，请解锁屏幕后重试")
                page.goto(MULTIPOST_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            self._page = page
            found_page = page

        # 检查是否被重定向到登录页
        if "signin" in found_page.url:
            raise RuntimeError("未登录 MultiPost，请先在 Chrome 中登录")

        # 验证编辑器是否就绪（检查是否有正文输入框）
        textarea = found_page.locator('textarea[placeholder*="内容"]')
        if textarea.count() == 0:
            logger.warning("  编辑器未就绪，再次刷新...")
            try:
                found_page.reload(wait_until="domcontentloaded", timeout=10000)
            except Exception:
                logger.warning("  ⚠️ 再次刷新超时，等待恢复...")
                if not self._ensure_chrome_responsive():
                    raise RuntimeError("Chrome 长时间未响应")
                found_page.reload(wait_until="domcontentloaded", timeout=10000)
            time.sleep(3)

        logger.success("✓ 编辑器已打开")
        return found_page

    def _fill_content(self, page: Page, title: str, body: str):
        """第2步：填入标题和正文"""
        logger.info("填入内容...")

        # 填标题
        title_input = page.locator('input[placeholder*="标题"]')
        if title_input.count() > 0:
            title_input.click()
            title_input.fill(title)
            logger.info(f"  标题: {title}")
        else:
            logger.warning("  未找到标题输入框")

        time.sleep(0.5)

        # 填正文
        textarea = page.locator('textarea[placeholder*="内容"]')
        if textarea.count() > 0:
            textarea.click()
            textarea.fill(body)
            logger.info(f"  正文: {body[:50]}...")
        else:
            raise RuntimeError("未找到正文输入框")

        time.sleep(1)
        logger.success("✓ 内容已填入")

    def _click_next(self, page: Page):
        """第3步：点击蓝色「下一步」按钮"""
        logger.info("点击「下一步」...")

        # 找蓝色按钮（background-color: rgb(0, 111, 238)）
        clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {  // 确保是主按钮，不是小图标
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }''')

        if not clicked:
            raise RuntimeError("未找到「下一步」按钮")

        time.sleep(3)  # 等待平台选择页加载
        logger.success("✓ 已进入平台选择页")

    def _select_platforms(self, page: Page, platforms: list[str]):
        """第5步：选择目标平台（先取消全选再勾选目标）"""
        logger.info(f"选择平台: {platforms}")

        # 先取消所有已勾选的平台
        self._deselect_all_platforms(page)

        for platform_name in platforms:
            result = self._select_single_platform(page, platform_name)
            if result:
                logger.info(f"  ✓ 已选择: {platform_name}")
            else:
                logger.warning(f"  ⚠️ 未找到或已选择: {platform_name}")
            time.sleep(0.5)

        logger.success(f"✓ 平台选择完成")

    def _deselect_all_platforms(self, page: Page):
        """取消所有已勾选的平台"""
        logger.info("取消所有已勾选的平台...")

        # 取消热门列表中已勾选的
        page.evaluate('''() => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            let unchecked = 0;
            for (const cb of checkboxes) {
                if (cb.checked) {
                    cb.click();
                    unchecked++;
                }
            }
            return unchecked;
        }''')

        # 也检查「其他」分类下是否有已勾选的
        page.evaluate('''() => {
            const all = document.querySelectorAll('button, span, a, div');
            for (const el of all) {
                if (el.textContent.trim() === '其他') {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')
        time.sleep(1)

        page.evaluate('''() => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            for (const cb of checkboxes) {
                if (cb.checked) {
                    cb.click();
                }
            }
        }''')

        logger.info("  ✓ 已取消所有平台勾选")

    def _upload_images(self, page: Page, image_paths: list[str]):
        """上传图片到 MultiPost 编辑器

        交互流程：
          1. 点击编辑器下方的「上传图片」卡片按钮
          2. 按钮点击后会激活隐藏的 <input type="file" accept="image/*">
          3. 通过该 input 上传本地图片文件
        """
        logger.info(f"上传图片: {len(image_paths)} 张")

        # 先点击「上传图片」按钮，激活 file input
        logger.info("  点击「上传图片」按钮...")
        clicked = page.evaluate('''() => {
            // 找包含「上传图片」文字的卡片/按钮
            const all = document.querySelectorAll('div, button, span');
            for (const el of all) {
                const text = (el.textContent || '').trim();
                // 精确匹配「上传图片」文字的卡片
                if (text === '上传图片' || text.startsWith('上传图片')) {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')

        if not clicked:
            logger.warning("  ⚠️ 未找到「上传图片」按钮")
        else:
            logger.info("  ✓ 已点击「上传图片」按钮")

        time.sleep(1)

        # 通过激活的 file input 上传图片
        image_input = page.locator('input[type="file"][accept*="image"]')

        if image_input.count() > 0:
            # 一次性上传所有图片（input 支持 multiple）
            image_input.set_input_files(image_paths)
            logger.info(f"  ✓ 已上传 {len(image_paths)} 张图片")
            time.sleep(3)  # 等待上传完成
        else:
            logger.warning("  ⚠️ 未找到图片 file input，尝试逐张上传...")
            for i, img_path in enumerate(image_paths, 1):
                # 再次点击上传按钮
                page.evaluate('''() => {
                    const all = document.querySelectorAll('div, button, span');
                    for (const el of all) {
                        if ((el.textContent || '').trim().startsWith('上传图片')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }''')
                time.sleep(1)

                image_input = page.locator('input[type="file"][accept*="image"]')
                if image_input.count() > 0:
                    image_input.set_input_files(img_path)
                    logger.info(f"    ✓ 图片 {i}/{len(image_paths)} 上传成功")
                    time.sleep(2)
                else:
                    logger.warning(f"    ⚠️ 图片 {i} 上传失败：未找到 file input")

        logger.success(f"✓ 图片上传完成")

    def _select_single_platform(self, page: Page, platform_name: str) -> bool:
        """
        选择单个平台

        策略：
          1. 先在热门列表里找
          2. 找不到就点「其他」展开，再找
          3. 找到后勾选 checkbox
        """
        result = page.evaluate('''(platformName) => {
            // 找到包含目标平台名的行
            const rows = document.querySelectorAll('div.flex.items-center.rounded-lg.p-2');
            for (const row of rows) {
                const text = (row.textContent || '').trim();
                if (text.includes(platformName) && text.length < 30) {
                    const checkbox = row.querySelector('input[type="checkbox"]');
                    if (checkbox && !checkbox.checked) {
                        checkbox.click();
                        return { found: true, clicked: true };
                    } else if (checkbox && checkbox.checked) {
                        return { found: true, clicked: false, reason: 'already checked' };
                    }
                }
            }
            return { found: false };
        }''', platform_name)

        if result.get('found'):
            return True

        # 热门列表里没找到，尝试展开「其他」
        logger.info(f"  {platform_name} 不在热门列表，尝试展开「其他」...")
        page.evaluate('''() => {
            const all = document.querySelectorAll('button, span, a, div');
            for (const el of all) {
                if (el.textContent.trim() === '其他') {
                    el.click();
                    return true;
                }
            }
            return false;
        }''')
        time.sleep(2)

        # 再试一次
        result = page.evaluate('''(platformName) => {
            const rows = document.querySelectorAll('div.flex.items-center.rounded-lg.p-2');
            for (const row of rows) {
                const text = (row.textContent || '').trim();
                if (text.includes(platformName) && text.length < 30) {
                    const checkbox = row.querySelector('input[type="checkbox"]');
                    if (checkbox && !checkbox.checked) {
                        checkbox.click();
                        return { found: true, clicked: true };
                    } else if (checkbox && checkbox.checked) {
                        return { found: true, clicked: false, reason: 'already checked' };
                    }
                }
            }
            return { found: false };
        }''', platform_name)

        return result.get('found', False)

    def _click_publish(self, page: Page, title: str, body: str) -> bool:
        """第6步：点击 MultiPost 发布按钮，然后处理各平台标签页

        MultiPost 点发布后，各平台标签页可能是：
          - 新开的（头条/公众号通常新开）
          - 复用已有的（脉脉通常复用）
        统一用按内容匹配的方式找到所有需要处理的标签页。
        """
        logger.info("⚠️  即将点击发布按钮，这是真实发布操作！")

        # 点击蓝色发布按钮
        clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }''')

        if not clicked:
            raise RuntimeError("未找到发布按钮")

        logger.info("  ✓ MultiPost 发布按钮已点击")

        # 等待 Chrome 扩展打开平台标签页（需要足够时间）
        logger.info("  等待 Chrome 扩展打开平台标签页（20秒）...")
        time.sleep(20)

        # ⚠️ 重连 Playwright：Chrome 扩展新开的标签页不会被已连接的 context 追踪，
        # 必须断开重连才能拿到最新标签页列表（0707版验证）
        logger.info("  重连 Playwright 以扫描新标签页...")
        try:
            self._playwright.stop()
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(CDP_URL)
            self._context = self._browser.contexts[0] if self._browser.contexts else None
            if self._context:
                logger.success("  ✓ Playwright 已重连")
            else:
                logger.error("  ❌ 重连后未找到上下文")
                return False
        except Exception as e:
            logger.error(f"  ❌ Playwright 重连失败: {e}")
            return False

        # 按内容匹配找到所有刚填入内容的平台标签页
        platform_tabs = self._find_all_platform_tabs(body, timeout=15)

        if not platform_tabs:
            # 没有找到任何平台标签页，检查 MultiPost 页面状态
            result = page.evaluate('''() => {
                const text = document.body.textContent || '';
                if (text.includes('publish.success') || text.includes('success')) {
                    return 'success';
                } else if (text.includes('error') || text.includes('失败')) {
                    return 'error';
                }
                return 'unknown';
            }''')

            if result == 'success':
                logger.success("🎉 MultiPost 发布成功（未打开平台标签页）")
                return True
            elif result == 'error':
                logger.error("❌ MultiPost 发布失败")
                return False
            else:
                logger.warning("⚠️  发布结果未知，未检测到平台标签页")
                self._save_screenshot(page, "multipost_after_publish")
                return True

        # 逐个处理平台标签页（按优先级排序：脉脉→公众号→头条）
        PLATFORM_PRIORITY = {'maimai': 1, 'wechat': 2, 'toutiao': 3}
        sorted_tabs = sorted(platform_tabs, key=lambda t: PLATFORM_PRIORITY.get(self._identify_platform(t), 99))

        results = {}
        for tab in sorted_tabs:
            platform = self._identify_platform(tab)
            logger.info(f"  处理平台: {platform}")

            if platform == 'maimai':
                try:
                    results['maimai'] = self._publish_maimai(tab, title, body)
                except Exception as e:
                    logger.error(f"  maimai 发布异常: {e}")
                    results['maimai'] = False
            elif platform == 'wechat':
                results['wechat'] = self._publish_wechat(tab, title, body)
            elif platform == 'toutiao':
                results['toutiao'] = self._publish_toutiao(tab, title, body)
            else:
                logger.warning(f"  未知平台，跳过: {tab.url}")
                results['unknown'] = False

        # 清理平台标签页（MultiPost不一定自动关闭，需主动清理）
        self._cleanup_platform_tabs(platform_tabs)

        # 汇总结果
        for platform, success in results.items():
            status = "✅ 成功" if success else "❌ 失败"
            logger.info(f"  {platform}: {status}")

        known_results = {k: v for k, v in results.items() if k != 'unknown'}
        if not known_results:
            return False

        all_success = all(known_results.values())
        if all_success:
            logger.success("🎉 所有平台发布成功！")
        return all_success

    # ========== 平台标签页检测与交互 ==========

    def _cleanup_platform_tabs(self, platform_tabs: list):
        """关闭本次发布打开的平台标签页，避免干扰下一篇发布"""
        if not platform_tabs:
            return

        closed = 0
        for tab in platform_tabs:
            try:
                url = tab.url
                # 只关闭平台标签页（脉脉/头条/公众号），不关MultiPost和其他页面
                if any(domain in url for domain in ['maimai.cn', 'toutiao.com', 'weixin.qq.com']):
                    tab.close()
                    closed += 1
            except Exception:
                pass

        if closed:
            logger.info(f"🧹 已关闭 {closed} 个平台标签页")
        else:
            logger.info("  无需关闭平台标签页")

    def _close_multipost_done_dialog(self):
        """点击 MultiPost 弹窗的「完成并关闭所有标签页」按钮

        MultiPost 发布完所有平台后会弹出一个确认窗口，
        上面有「完成并关闭所有标签页」按钮，点击后关闭本次打开的所有平台标签页。
        """
        logger.info("查找 MultiPost 完成弹窗...")
        # 弹窗可能在 MultiPost 主页面上
        for pg in self._context.pages:
            if 'multipost.app' not in pg.url and 'chrome-extension' not in pg.url:
                continue
            try:
                clicked = pg.evaluate('''() => {
                    const btns = document.querySelectorAll('button, span, div, a');
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if (t.includes('完成并关闭') || t.includes('关闭所有标签') || t.includes('关闭标签页')) {
                            btn.click();
                            return t;
                        }
                    }
                    // 兜底：找「完成」按钮
                    for (const btn of btns) {
                        const t = (btn.textContent || '').trim();
                        if (t === '完成' || t === 'Done') {
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 40 && rect.height > 20) {
                                btn.click();
                                return t;
                            }
                        }
                    }
                    return false;
                }''')
                if clicked:
                    logger.info(f"  ✓ 已点击: {clicked}")
                    time.sleep(2)
                    return
            except Exception:
                continue
        logger.info("  未找到完成弹窗（可能已自动关闭）")

    def _wait_for_platform_tabs(self, existing_count: int, timeout: int = 15) -> list:
        """
        等待 MultiPost 打开平台标签页

        参数:
            existing_count: 点击发布前的页面数
            timeout: 最长等待秒数

        返回:
            新打开的 Page 列表
        """
        logger.info("等待平台标签页打开...")
        start = time.time()
        while time.time() - start < timeout:
            current_pages = self._context.pages
            if len(current_pages) > existing_count:
                new_pages = current_pages[existing_count:]
                logger.info(f"  ✓ 检测到 {len(new_pages)} 个新标签页")
                return new_pages
            time.sleep(1)

        logger.warning("  ⚠️ 未检测到新标签页")
        return []

    def _find_maimai_tab_by_content(self, expected_body: str, timeout: int = 10) -> Optional[Page]:
        """按正文内容找到脉脉标签页（旧方法，保留兼容）"""
        return self._find_platform_tab_by_content('maimai.cn', expected_body, timeout)

    def _find_platform_tab_by_content(
        self, url_pattern: str, expected_body: str, timeout: int = 10
    ) -> Optional[Page]:
        """按 URL 模式 + 正文内容匹配找到某个平台的标签页"""
        fragment = (expected_body or "")[:30]
        if not fragment:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            for pg in self._context.pages:
                if url_pattern not in pg.url:
                    continue
                try:
                    found = pg.evaluate('''(frag) => {
                        const editors = document.querySelectorAll(
                            'div[contenteditable="true"], textarea'
                        );
                        for (const e of editors) {
                            if ((e.innerText || e.value || '').includes(frag)) return true;
                        }
                        return false;
                    }''', fragment)
                    if found:
                        return pg
                except Exception:
                    continue
            time.sleep(1)
        return None

    def _find_all_platform_tabs(self, expected_body: str, timeout: int = 15) -> list:
        """
        找到所有刚被 MultiPost 打开的平台标签页。

        策略（0707版改进）：
          1. 先按 URL 匹配平台（maimai.cn / mp.weixin.qq.com / mp.toutiao.com）
          2. 内容匹配仅作辅助确认（不强制，因为MultiPost可能格式化了内容）
          3. 找到任何一个平台标签页即返回（不等全部）
        返回去重后的 Page 列表（每个平台只取最新的一个）。
        """
        fragment = (expected_body or "")[:30]
        logger.info("扫描所有平台标签页（URL匹配+内容辅助）...")

        # 平台 URL 匹配规则
        PLATFORM_URL_RULES = {
            'maimai': 'maimai.cn',
            'wechat': 'mp.weixin.qq.com',
            'toutiao': 'mp.toutiao.com',
        }

        deadline = time.time() + timeout

        while time.time() < deadline:
            tabs = []
            seen_platforms = set()
            for pg in self._context.pages:
                url = pg.url
                # 跳过 MultiPost 自己和扩展页
                if 'multipost.app' in url or 'chrome-extension' in url:
                    continue

                # 按 URL 识别平台
                platform = None
                for pkey, purl in PLATFORM_URL_RULES.items():
                    if purl in url:
                        platform = pkey
                        break

                if not platform:
                    continue

                # 内容辅助确认（非强制：匹配到内容加分，匹配不到也接受）
                content_ok = False
                if fragment:
                    try:
                        content_ok = pg.evaluate('''(frag) => {
                            const editors = document.querySelectorAll(
                                'div[contenteditable="true"], textarea, input'
                            );
                            for (const e of editors) {
                                const text = e.innerText || e.value || '';
                                if (text.includes(frag)) return true;
                            }
                            // 也检查页面纯文本（有些平台编辑器不在标准元素里）
                            return document.body.innerText.includes(frag);
                        }''', fragment)
                    except Exception:
                        pass

                if content_ok:
                    logger.info(f"  ✓ 找到平台标签页（内容匹配）: {platform}")
                else:
                    logger.info(f"  ✓ 找到平台标签页（URL匹配，内容未确认）: {platform} - {url[:60]}")

                # 每个平台只取最后一个（最新的标签页）
                tabs = [t for t in tabs if t[0] != platform]
                tabs.append((platform, pg))

            if tabs:
                result = [t[1] for t in tabs]
                platforms_found = [t[0] for t in tabs]
                logger.info(f"  ✓ 共找到 {len(result)} 个平台标签页: {platforms_found}")
                return result

            time.sleep(2)

        logger.warning("  ⚠️ 未找到任何平台标签页")
        return []

    def _identify_platform(self, page: Page) -> str:
        """
        根据 URL 识别平台

        返回:
            'toutiao' / 'wechat' / 'maimai' / 'unknown'
        """
        url = page.url
        logger.debug(f"  标签页 URL: {url}")

        if 'mp.toutiao.com' in url or 'toutiao.com' in url:
            return 'toutiao'
        elif 'mp.weixin.qq.com' in url or 'weixin.qq.com' in url:
            return 'wechat'
        elif 'maimai.cn' in url:
            return 'maimai'

        # 等待页面跳转完成后再次检查
        time.sleep(3)
        url = page.url
        logger.debug(f"  标签页 URL (等待后): {url}")

        if 'mp.toutiao.com' in url or 'toutiao.com' in url:
            return 'toutiao'
        elif 'mp.weixin.qq.com' in url or 'weixin.qq.com' in url:
            return 'wechat'
        elif 'maimai.cn' in url:
            return 'maimai'

        # 未知平台，保存截图用于调试
        self._save_screenshot(page, "unknown_platform_tab")
        logger.warning(f"  未知平台: {url}")
        return 'unknown'

    def _publish_toutiao(self, page: Page, title: str, content: str) -> bool:
        """
        在今日头条发布页面：确保正文已填入 → 追加 #上头条 聊热点# 话题 → 点击发布

        ⚠️ 用户要求：头条正文末尾必须带 #上头条 聊热点# 话题。
        MultiPost 扩展通常会先填好正文，这里仅在为空时补填，避免重复。

        返回:
            True 发布成功
        """
        logger.info("📝 今日头条：追加话题并发布")

        # 等待页面加载
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("  今日头条页面加载超时")
        time.sleep(3)

        self._save_screenshot(page, "toutiao_before_fill")

        # MultiPost 通常已填入正文；仅在为空时补填，避免重复
        state = self._get_main_editor_state(page)
        if state.get("len", 0) < 10:
            logger.info("  正文为空，填入内容...")
            self._fill_platform_title(page, title, "toutiao")
            self._fill_platform_content(page, content, "toutiao")
        else:
            logger.info(f"  MultiPost 已填入正文（{state.get('len')} 字），跳过补填")

        # 追加话题 #上头条 聊热点#
        logger.info(f"  追加话题: {TOUTIAO_HASHTAG}")
        appended = self._append_text_to_editor(page, TOUTIAO_HASHTAG)
        if not appended:
            logger.error("  ❌ 话题未写入正文，中止发布（不点发布按钮）")
            self._save_screenshot(page, "toutiao_hashtag_fail")
            return False
        logger.success("  ✓ 话题已写入正文末尾")

        # TODO: 分类标签选择（需要根据实际页面元素确定选择器）
        logger.info("  分类标签：暂未实现，跳过")

        self._save_screenshot(page, "toutiao_before_publish")

        # 点击红色「发布」按钮（bg rgb(255,94,94)），找不到则兜底任意「发布」
        publish_clicked = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                const text = (b.textContent || '').trim();
                const style = getComputedStyle(b);
                if (text === '发布' && style.backgroundColor.includes('255, 94, 94')) {
                    b.click();
                    return text;
                }
            }
            for (const b of btns) {
                if ((b.textContent || '').trim() === '发布') { b.click(); return '发布(fallback)'; }
            }
            return false;
        }''')

        if not publish_clicked:
            logger.warning("  ⚠️ 今日头条：未找到发布按钮")
            self._save_screenshot(page, "toutiao_publish_btn_fail")
            return False

        logger.info(f"  ✓ 今日头条发布按钮已点击: {publish_clicked}")
        time.sleep(4)

        # 处理可能的确认弹窗
        page.evaluate('''() => {
            const btns = document.querySelectorAll('button, span, a, div');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (['确认发布', '确定', '确认', '继续发布', '确认发布微头条'].includes(t)) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 30 && r.height > 15) { b.click(); return t; }
                }
            }
            return false;
        }''')
        time.sleep(2)

        self._save_screenshot(page, "toutiao_after_publish")
        logger.success("✓ 今日头条发布完成")
        return True

    def _publish_wechat(self, page: Page, title: str, content: str) -> bool:
        """
        在微信公众号发布页面：确保正文已填入 → 点击「保存为草稿」

        ⚠️ 用户要求：公众号只保存为草稿，不要点「发表」。

        返回:
            True 草稿保存成功
        """
        logger.info("📝 微信公众号：保存为草稿（不发表）")

        # 等待页面加载
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("  微信公众号页面加载超时")
        time.sleep(3)

        self._save_screenshot(page, "wechat_before_fill")

        # MultiPost 通常已填入标题+正文；仅在为空时补填，避免重复
        state = self._get_main_editor_state(page)
        if state.get("len", 0) < 10:
            logger.info("  正文为空，填入内容...")
            self._fill_platform_title(page, title, "wechat")
            content_filled = self._fill_wechat_content(page, content)
            if not content_filled:
                logger.error("  ❌ 微信公众号：正文填写失败")
                self._save_screenshot(page, "wechat_content_fail")
                return False
        else:
            logger.info(f"  MultiPost 已填入正文（{state.get('len')} 字），跳过补填")

        # TODO: 分类标签选择
        logger.info("  分类标签：暂未实现，跳过")

        self._save_screenshot(page, "wechat_before_draft")

        # ⚠️ 点击「保存为草稿」，不要点「发表」「群发」
        saved = page.evaluate('''() => {
            const candidates = ['保存为草稿', '保存草稿', '存草稿'];
            const btns = document.querySelectorAll('button, a, span, div');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (candidates.includes(t)) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 30 && r.height > 15) { b.click(); return t; }
                }
            }
            return false;
        }''')

        if not saved:
            logger.warning("  ⚠️ 微信公众号：未找到「保存为草稿」按钮")
            self._save_screenshot(page, "wechat_draft_btn_fail")
            return False

        logger.info(f"  ✓ 已点击「{saved}」")
        time.sleep(3)

        self._save_screenshot(page, "wechat_after_draft")
        logger.success("✓ 微信公众号草稿已保存")
        return True

    def _publish_maimai(self, page: Page, title: str, body: str) -> bool:
        """
        在脉脉标签页执行脉脉特有操作：加话题 → 核实两个开关 → 点「发动态」

        MultiPost 已完成：填标题/正文/图片。本方法只做 MultiPost 做不了的脉脉特有步骤。
        ⚠️ 不要刷新页面或重填内容——会清掉 MultiPost 填好的图片。

        返回:
            True 发布成功
        """
        logger.info("📝 脉脉：添加话题 → 核实发布设置 → 发动态")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            logger.warning("  脉脉页面加载超时")
        time.sleep(3)

        self._save_screenshot(page, "maimai_before_finalize")

        # 1. 编辑器存在性守卫
        editor_ok = page.evaluate('''() => {
            const c = document.querySelector('[contenteditable="true"], textarea');
            const r = c ? c.getBoundingClientRect() : null;
            return c && r && r.width > 50;
        }''')
        if not editor_ok:
            logger.warning("  脉脉标签页无编辑器，导航到社区首页...")
            page.goto(MAIMAI_HOME_URL, wait_until="domcontentloaded", timeout=15000)
            time.sleep(3)
            # MultiPost 没填成功，这里补填（极端情况）
            if title:
                self._maimai_fill_title(page, title)
            try:
                self._maimai_fill_content(page, body)
            except Exception:
                pass

        # 2. 切换身份（幂等，只点元素不刷页）
        self._maimai_switch_identity(page)

        # 3. 添加话题（不刷新页面！刷新会丢图片；失败只 warn 不阻塞）
        topic = self._maimai_topic or DEFAULT_TOPIC
        if topic:
            topic_ok = self._maimai_add_topic(page, topic)
            if not topic_ok:
                # 不刷新，只按 Escape 关掉残留弹窗再试一次
                logger.info("  关闭弹窗后重试添加话题 (第1次)...")
                page.keyboard.press("Escape")
                time.sleep(2)
                if not self._maimai_add_topic(page, topic):
                    logger.warning(f"  ⚠️ 添加话题失败: {topic}，跳过话题继续发布")

        # 4. 核实两个发布设置开关已开启（同步主页 + 昵称水印）
        self._maimai_enable_publish_settings(page)

        self._save_screenshot(page, f"maimai_before_post_{int(time.time())}")

        # 5. 点击「发动态」
        result = self._maimai_click_publish(page)
        return result

    # ========== 平台通用填写方法 ==========

    def _get_main_editor_state(self, page: Page) -> dict:
        """获取主正文编辑器状态（contenteditable/textbox 中字数最多的那个）"""
        return page.evaluate('''() => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], [role="textbox"]'
            );
            let best = null;
            for (const e of editors) {
                const rect = e.getBoundingClientRect();
                const len = (e.innerText || '').length;
                if (rect.width > 100 && rect.height > 30 && len > (best ? best.len : 0)) {
                    best = {len: len, tail: (e.innerText || '').slice(-60)};
                }
            }
            return best || {len: 0, tail: ''};
        }''')

    def _append_text_to_editor(self, page: Page, text: str) -> bool:
        """
        在主正文编辑器末尾追加文本（用于追加头条话题）

        先用 keyboard.type 触发编辑器的 input 事件（让 #话题# 被识别为蓝链）；
        若未写入，再用 execCommand insertText 兜底。
        """
        focused = page.evaluate('''() => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], [role="textbox"]'
            );
            let target = null;
            for (const e of editors) {
                const rect = e.getBoundingClientRect();
                const len = (e.innerText || '').length;
                if (rect.width > 100 && rect.height > 30 && len > 50) {
                    if (!target || len > (target.innerText || '').length) target = e;
                }
            }
            if (!target) return false;
            target.focus();
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(target);
            range.collapse(false);  // 光标移到末尾
            sel.removeAllRanges();
            sel.addRange(range);
            return true;
        }''')
        if not focused:
            return False

        # 方式1：keyboard.type（让编辑器触发 input 事件以识别 #话题#）
        page.keyboard.press('Enter')
        time.sleep(0.2)
        page.keyboard.type(text, delay=80)
        time.sleep(2)

        state = self._get_main_editor_state(page)
        if text in state.get("tail", ""):
            return True

        # 方式2：execCommand insertText 兜底
        logger.info("  keyboard.type 未生效，用 execCommand 兜底...")
        page.evaluate('''(txt) => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], [role="textbox"]'
            );
            let target = null;
            for (const e of editors) {
                const len = (e.innerText || '').length;
                if (len > 50 && (!target || len > (target.innerText || '').length)) target = e;
            }
            if (!target) return false;
            target.focus();
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(target);
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);
            document.execCommand('insertText', false, '\\n' + txt);
            return true;
        }''', text)
        time.sleep(2)

        state = self._get_main_editor_state(page)
        return text in state.get("tail", "")

    def _fill_platform_title(self, page: Page, title: str, platform: str) -> bool:
        """在平台发布页面填写标题"""
        # 策略1：通过 placeholder 定位标题输入框
        title_input = page.locator('input[placeholder*="标题"], input[placeholder*="请输入"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.info(f"  标题已填写: {title[:30]}...")
            return True

        # 策略2：通过 id 或常见选择器
        title_input = page.locator('#title, input[name="title"], input[type="text"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.info(f"  标题已填写: {title[:30]}...")
            return True

        # 策略3：JS evaluate 兜底
        filled = page.evaluate('''(title) => {
            const inputs = document.querySelectorAll('input[type="text"]');
            for (const input of inputs) {
                const ph = (input.placeholder || '') + (input.getAttribute('aria-label') || '');
                if (ph.includes('标题') || ph.includes('请输入') || ph === '') {
                    input.focus();
                    input.value = title;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }''', title)

        if filled:
            logger.info(f"  标题已填写 (JS): {title[:30]}...")
        return filled

    def _fill_platform_content(self, page: Page, content: str, platform: str) -> bool:
        """在平台发布页面填写正文（通用方法，非微信）"""
        # 策略1：contenteditable div（今日头条常用）
        content_editor = page.locator('div[contenteditable="true"]')
        if content_editor.count() > 0:
            content_editor.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (contenteditable): {len(content)} 字")
            return True

        # 策略2：textarea
        textarea = page.locator('textarea[placeholder*="正文"], textarea[placeholder*="内容"], textarea')
        if textarea.count() > 0:
            textarea.first.click()
            textarea.first.fill(content)
            logger.info(f"  正文已填写 (textarea): {len(content)} 字")
            return True

        # 策略3：role="textbox"
        textbox = page.locator('[role="textbox"]')
        if textbox.count() > 0:
            textbox.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (textbox): {len(content)} 字")
            return True

        # 策略4：JS evaluate 兜底
        filled = page.evaluate('''(content) => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], textarea, [role="textbox"]'
            );
            for (const editor of editors) {
                if (editor.tagName === 'TEXTAREA') {
                    editor.value = content;
                } else {
                    editor.innerHTML = content;
                }
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            }
            return false;
        }''', content)

        if filled:
            logger.info(f"  正文已填写 (JS): {len(content)} 字")
        return filled

    def _fill_wechat_content(self, page: Page, content: str) -> bool:
        """在微信公众号发布页面填写正文（处理 iframe 编辑器）"""
        # 策略1：iframe 编辑器（微信公众号常用）
        editor_frame = page.locator('iframe[id*="edui"], iframe[class*="editor"], iframe[src*="editor"]')
        if editor_frame.count() > 0:
            try:
                frame = editor_frame.first.content_frame()
                body = frame.locator('body[contenteditable="true"]')
                if body.count() > 0:
                    body.click()
                    page.keyboard.type(content, delay=5)
                    logger.info(f"  正文已填写 (iframe编辑器): {len(content)} 字")
                    return True
            except Exception as e:
                logger.warning(f"  iframe 编辑器填写失败: {e}")

        # 策略2：直接 contenteditable
        content_editor = page.locator('div[contenteditable="true"], [role="textbox"]')
        if content_editor.count() > 0:
            content_editor.first.click()
            page.keyboard.type(content, delay=5)
            logger.info(f"  正文已填写 (contenteditable): {len(content)} 字")
            return True

        # 策略3：JS evaluate 尝试访问 iframe
        filled = page.evaluate('''(content) => {
            // 尝试 iframe
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                try {
                    const body = iframe.contentDocument && iframe.contentDocument.body;
                    if (body && body.contentEditable === 'true') {
                        body.innerHTML = content;
                        return true;
                    }
                } catch (e) { /* 跨域 iframe */ }
            }
            // 尝试 contenteditable
            const editors = document.querySelectorAll('div[contenteditable="true"], [role="textbox"]');
            for (const editor of editors) {
                editor.focus();
                editor.innerHTML = content;
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return true;
            }
            return false;
        }''', content)

        if filled:
            logger.info(f"  正文已填写 (JS): {len(content)} 字")
        return filled

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

    # ========== 图片清理 ==========

    @staticmethod
    def _cleanup_downloaded_images(image_paths: list):
        """
        删除本地已下载的图片文件（从 Pexels / 百度图片 API 下载的配图）。

        发布完成后这些图片不再需要，删除可节省磁盘空间。
        只删除项目目录下的文件（安全守卫：不删系统文件）。
        """
        if not image_paths:
            return

        deleted = 0
        for img_path in image_paths:
            try:
                p = PROJECT_ROOT / img_path if not str(img_path).startswith('/') else Path(img_path)
                # 安全守卫：只删项目目录下的文件
                if not str(p).startswith(str(PROJECT_ROOT)):
                    continue
                if p.exists() and p.is_file() and p.suffix.lower() in (
                    '.jpg', '.jpeg', '.png', '.gif', '.webp',
                ):
                    p.unlink()
                    deleted += 1
            except Exception:
                pass

        if deleted:
            logger.info(f"🧹 已清理 {deleted} 张本地图片")
