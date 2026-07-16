"""
DirectPublisher —— 零扩展依赖的三平台直连发布器

完整流程（每篇帖子串行处理3个平台）：
  1. 脉脉：打开 maimai.cn → 填正文+上传图片+加话题+勾开关+发动态
  2. 微信公众号：打开 mp.weixin.qq.com → 新建图文+填标题正文图片+保存草稿
  3. 今日头条：打开 mp.toutiao.com → 填标题正文图片+追加话题+发布

⚠️ 前置条件：
  - Chrome 带调试端口启动（python3 start_chrome.py）
  - 已登录 maimai.cn、mp.weixin.qq.com、mp.toutiao.com
  - 不需要 MultiPost 扩展！

⚠️ 与 MultiPostPublisher 的关系：
  - 独立模块，不修改原有代码
  - 继承 MaimaiPageOps 复用脉脉DOM操作
  - 复用 Chrome 连接管理逻辑（dedicated模式）
  - publish()/batch_post() 接口签名与 MultiPostPublisher 兼容
"""

import os
import platform
import random
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page
from loguru import logger

from config import settings, PROJECT_ROOT
from publisher.maimai import MaimaiPageOps, MAIMAI_HOME_URL, DEFAULT_TOPIC


# ========== 跨平台配置 ==========

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    CHROME_PATHS = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    CHROME_DEFAULT_PROFILE = os.path.expandvars(
        r"%LocalAppData%\Google\Chrome\User Data"
    )
    DEDICATED_USER_DATA = os.path.join(
        os.environ.get("TEMP", ""), "chrome-direct-profile"
    )
else:
    CHROME_PATHS = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    CHROME_DEFAULT_PROFILE = str(
        Path.home() / "Library/Application Support/Google/Chrome"
    )
    DEDICATED_USER_DATA = "/tmp/chrome-direct-profile"

DEFAULT_CDP_URL = "http://localhost:9222"
DEDICATED_DEBUG_PORT = 9334  # 用9334避免与MultiPost的9333冲突

# 平台编辑器 URL
MAIMAI_EDITOR_URL = "https://maimai.cn/community/home/recommended"
WECHAT_EDITOR_URL = "https://mp.weixin.qq.com/"
TOUTIAO_EDITOR_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"

# 今日头条正文末尾要追加的话题
TOUTIAO_HASHTAG = "#上头条 聊热点#"

# 默认要发布的平台
DEFAULT_PLATFORMS = ["脉脉", "微信公众号", "今日头条"]

# 跨平台快捷键
SELECT_ALL_KEY = "Meta+A" if platform.system() == "Darwin" else "Control+A"


# ========== Chrome 启动辅助 ==========

def _find_chrome() -> str:
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    raise FileNotFoundError("未找到 Chrome，请安装 Google Chrome")


def _ensure_profile(profile_dir: str):
    dest = Path(profile_dir)
    if dest.exists():
        return
    logger.info("  首次使用独立 profile，复制登录状态（约30秒）...")
    dest.mkdir(parents=True, exist_ok=True)
    dest_default = dest / "Default"
    dest_default.mkdir(parents=True, exist_ok=True)

    src = Path(CHROME_DEFAULT_PROFILE)
    if not src.exists():
        logger.warning("  未找到 Chrome 默认 profile，将使用空白 profile")
        return

    src_default = src / "Default"
    items = [
        "Extensions", "Extension State", "Extension Rules",
        "Local Storage", "Session Storage", "IndexedDB",
        "Cookies", "Cookies-journal",
        "Login Data", "Login Data-journal",
        "Web Data", "Web Data-journal",
        "Preferences", "Secure Preferences",
        "Local Extension Settings",
        "Favicons", "Favicons-journal",
        "History", "History-journal",
        "Bookmarks",
    ]
    import shutil
    for item in items:
        src_item = src_default / item
        if src_item.exists():
            try:
                if IS_WINDOWS:
                    dst_item = dest_default / item
                    if src_item.is_dir():
                        if dst_item.exists():
                            shutil.rmtree(dst_item)
                        shutil.copytree(str(src_item), str(dst_item))
                    else:
                        shutil.copy2(str(src_item), str(dst_item))
                else:
                    subprocess.run(
                        ["cp", "-r", str(src_item), str(dest_default / item)],
                        capture_output=True, timeout=30,
                    )
            except Exception:
                pass
    for item in ["Local State", "First Run", "Last Browser"]:
        src_item = src / item
        if src_item.exists():
            try:
                if IS_WINDOWS:
                    shutil.copy2(str(src_item), str(dest / item))
                else:
                    subprocess.run(
                        ["cp", str(src_item), str(dest / item)],
                        capture_output=True, timeout=5,
                    )
            except Exception:
                pass
    logger.success("  ✓ Profile 复制完成")


def _start_chrome(port: int, profile_dir: str) -> Optional[subprocess.Popen]:
    chrome_path = _find_chrome()
    _ensure_profile(profile_dir)
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-networking",
        "--disable-popup-blocking",
        "--disable-features=CalculateNativeWinOcclusion",
    ]
    if not IS_WINDOWS:
        cmd = ["caffeinate", "-i"] + cmd
    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.info("  等待独立 Chrome 启动...")
    import urllib.request
    for _ in range(10):
        time.sleep(2)
        try:
            resp = urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=3)
            if resp.status == 200:
                logger.success(f"  ✓ 独立 Chrome 已启动，端口: {port}")
                return process
        except Exception:
            continue
    logger.error("  ❌ 独立 Chrome 启动失败")
    return None


def _stop_chrome(process: subprocess.Popen):
    try:
        if IS_WINDOWS:
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        logger.info("  ✓ Chrome 已关闭")
    except Exception as e:
        logger.warning(f"  ⚠️ 关闭 Chrome 异常: {e}")


def _kill_chrome_on_port(port: int):
    try:
        if IS_WINDOWS:
            subprocess.run(
                f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{port}\') do taskkill /F /PID %a',
                shell=True, capture_output=True, timeout=10,
            )
        else:
            subprocess.run(
                f'lsof -ti :{port} | xargs kill -9 2>/dev/null',
                shell=True, capture_output=True, timeout=10,
            )
    except Exception:
        pass


# ========== DirectPublisher ==========

class DirectPublisher(MaimaiPageOps):
    """
    直连三平台发布器 — 零扩展依赖

    用法：
        publisher = DirectPublisher(dedicated=True)
        publisher.connect()
        publisher.publish(
            title="话题名",
            body="正文内容",
            platforms=["脉脉", "微信公众号", "今日头条"],
            image_paths=["/path/to/image.jpg"],
            maimai_topic="话题名",
        )
        publisher.disconnect()

    继承 MaimaiPageOps 以复用脉脉DOM操作（加话题/勾开关/发动态等）。
    """

    def __init__(self, dedicated: bool = True, cdp_url: str = None, port: int = None):
        self._dedicated = dedicated
        self._cdp_url = cdp_url or DEFAULT_CDP_URL
        self._port = port or DEDICATED_DEBUG_PORT
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._chrome_process: Optional[subprocess.Popen] = None

    def connect(self) -> bool:
        """连接到 Chrome（dedicated模式自动启动独立Chrome）"""
        if self._dedicated:
            _kill_chrome_on_port(self._port)
            time.sleep(2)
            self._chrome_process = _start_chrome(self._port, DEDICATED_USER_DATA)
            if not self._chrome_process:
                return False
            cdp_url = f"http://localhost:{self._port}"
        else:
            cdp_url = self._cdp_url

        logger.info(f"连接到 Chrome（{cdp_url}）...")
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._context = (
                self._browser.contexts[0] if self._browser.contexts else None
            )
            if not self._context:
                logger.error("❌ 未找到浏览器上下文")
                return False
            logger.success("✓ 已连接到 Chrome")
            return True
        except Exception as e:
            logger.error(f"❌ 连接 Chrome 失败: {e}")
            return False

    def disconnect(self):
        """断开连接（dedicated模式同时关闭Chrome）"""
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        if self._dedicated and self._chrome_process:
            _stop_chrome(self._chrome_process)
            self._chrome_process = None
        logger.info("已断开 Chrome 连接")

    # ========== 核心发布接口 ==========

    def publish(
        self,
        title: str,
        body: str,
        platforms: list = None,
        image_paths: list = None,
        maimai_topic: str = None,
        dry_run: bool = False,
    ) -> bool:
        """
        发布内容到多个平台（串行，每平台独立标签页）

        参数:
            title:       标题（=话题名，用于脉脉话题搜索）
            body:        正文内容
            platforms:   目标平台列表，默认 ["脉脉", "微信公众号", "今日头条"]
            image_paths: 本地图片路径列表
            maimai_topic:脉脉话题名（默认=title）
            dry_run:     干跑模式

        返回:
            True 全部成功，False 有失败
        """
        if platforms is None:
            platforms = list(DEFAULT_PLATFORMS)

        topic = maimai_topic or title or DEFAULT_TOPIC
        results = {}

        # 平台处理顺序：脉脉 → 公众号 → 头条
        PLATFORM_ORDER = {"脉脉": 1, "微信公众号": 2, "今日头条": 3}
        sorted_platforms = sorted(platforms, key=lambda p: PLATFORM_ORDER.get(p, 99))

        for platform_name in sorted_platforms:
            logger.info(f"\n{'=' * 40}")
            logger.info(f"📝 平台: {platform_name}")
            logger.info(f"{'=' * 40}")

            page = None
            try:
                page = self._context.new_page()

                if platform_name == "脉脉":
                    results["脉脉"] = self._publish_maimai_direct(
                        page, title, body, image_paths, topic, dry_run
                    )
                elif platform_name == "微信公众号":
                    results["微信公众号"] = self._publish_wechat_direct(
                        page, title, body, image_paths, dry_run
                    )
                elif platform_name == "今日头条":
                    results["今日头条"] = self._publish_toutiao_direct(
                        page, title, body, image_paths, dry_run
                    )
                else:
                    logger.warning(f"  ⚠️ 未知平台: {platform_name}")
                    results[platform_name] = False

            except Exception as e:
                logger.error(f"❌ {platform_name} 发布异常: {e}")
                results[platform_name] = False
            finally:
                # 关闭平台标签页
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass

        # 汇总结果
        for platform_name, success in results.items():
            status = "✅ 成功" if success else "❌ 失败"
            logger.info(f"  {platform_name}: {status}")

        all_success = all(results.values())
        if all_success:
            logger.success("🎉 所有平台发布成功！")
        return all_success

    def batch_post(
        self,
        posts: list,
        platforms: list = None,
        interval: int = None,
        dry_run: bool = False,
        cleanup_images: bool = True,
    ) -> dict:
        """
        批量发布 —— 逐篇调用 publish，篇与篇之间等待 interval 秒

        参数:
            posts:    帖子列表，每项 {"title", "body", "topic", "image_paths"}
            platforms: 目标平台列表
            interval: 发帖间隔秒数
            dry_run:  干跑模式
            cleanup_images: 发布完成后删除本地图片

        返回:
            {"success": int, "failed": int}
        """
        if platforms is None:
            platforms = list(DEFAULT_PLATFORMS)
        if interval is None:
            interval = settings.direct_post_interval

        total = len(posts)
        success = 0
        failed = 0

        logger.info(f"📋 DirectPublisher 批量发布开始: 共 {total} 篇，间隔 {interval} 秒（±30秒抖动）")

        for i, post_data in enumerate(posts, 1):
            logger.info(f"\n{'=' * 60}")
            logger.info(f"📝 第 {i}/{total} 篇")
            logger.info(f"{'=' * 60}")

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
            else:
                failed += 1

            # 间隔等待
            if i < total and not dry_run:
                jitter = random.randint(-30, 30)
                actual_wait = max(60, interval + jitter)
                logger.info(f"⏳ 等待 {actual_wait} 秒后发布下一篇...")
                time.sleep(actual_wait)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"🏁 发布完成: 成功 {success}, 失败 {failed}")
        logger.info(f"{'=' * 60}")

        # 清理本地图片
        if cleanup_images and not dry_run:
            all_image_paths = []
            for p in posts:
                for ip in (p.get("image_paths") or []):
                    if ip and ip not in all_image_paths:
                        all_image_paths.append(ip)
            self._cleanup_images(all_image_paths)

        return {"success": success, "failed": failed}

    # ========== 脉脉直发 ==========

    def _publish_maimai_direct(
        self, page: Page, title: str, body: str,
        image_paths: list, topic: str, dry_run: bool,
    ) -> bool:
        """脉脉直发：复用 MaimaiPageOps 的所有方法"""
        logger.info("📝 脉脉：直连发布")

        # 1. 打开脉脉首页
        page.goto(MAIMAI_EDITOR_URL, wait_until="domcontentloaded", timeout=15000)
        time.sleep(3)

        # 检查登录状态
        if "login" in page.url.lower() or "signin" in page.url.lower():
            logger.error("❌ 脉脉未登录，请先在 Chrome 中登录 maimai.cn")
            return False

        # 2. 清空表单
        self._maimai_clear_form(page)
        time.sleep(0.5)

        # 3. 填正文
        self._maimai_fill_content(page, body)

        # 4. 上传图片
        if image_paths:
            self._maimai_upload_images(page, image_paths)

        # 5. 切换身份
        self._maimai_switch_identity(page)

        # 6. 添加话题
        if topic:
            topic_ok = self._maimai_add_topic(page, topic)
            if not topic_ok:
                logger.info("  关闭弹窗后重试添加话题...")
                page.keyboard.press("Escape")
                time.sleep(2)
                if not self._maimai_add_topic(page, topic):
                    logger.warning(f"  ⚠️ 添加话题失败: {topic}，跳过话题继续发布")

        # 7. 勾选发布设置开关
        self._maimai_enable_publish_settings(page)

        self._save_screenshot(page, f"direct_maimai_before_post_{int(time.time())}")

        if dry_run:
            logger.info("🔍 干跑模式：脉脉内容已填入，但不点击发布")
            return True

        # 8. 点击「发动态」
        result = self._maimai_click_publish(page)
        return result

    # ========== 微信公众号直发 ==========

    def _publish_wechat_direct(
        self, page: Page, title: str, body: str,
        image_paths: list, dry_run: bool,
    ) -> bool:
        """微信公众号直发：新建图文+填内容+保存草稿"""
        logger.info("📝 微信公众号：直连发布（草稿模式）")

        # 1. 打开公众号后台
        page.goto(WECHAT_EDITOR_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(5)

        # 检查登录状态
        if "login" in page.url.lower() or "signin" in page.url.lower():
            logger.error("❌ 公众号未登录，请先在 Chrome 中登录 mp.weixin.qq.com")
            return False

        # 2. 点击「新建图文」或导航到图文编辑页
        # 尝试找「新的创作」或「图文消息」按钮
        clicked_new = page.evaluate('''() => {
            const all = document.querySelectorAll('a, button, span, div');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                if (t.includes('图文消息') || t.includes('新建图文') || t.includes('写新图文')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 20 && rect.height > 10) {
                        el.click();
                        return t;
                    }
                }
            }
            return false;
        }''')

        if clicked_new:
            logger.info(f"  点击了「{clicked_new}」按钮")
            time.sleep(5)

            # 等待编辑器页面加载
            # 公众号可能会打开新标签页或跳转
            # 检查当前页面或新标签页
            editor_page = self._find_wechat_editor_page(page)
            if editor_page:
                page = editor_page
        else:
            logger.info("  未找到新建图文按钮，尝试直接导航到编辑页...")
            # 尝试直接导航到图文编辑页
            page.goto(
                "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit&type=77",
                wait_until="domcontentloaded", timeout=15000,
            )
            time.sleep(5)

        self._save_screenshot(page, "direct_wechat_after_nav")

        # 3. 填标题
        self._fill_wechat_title(page, title)

        # 4. 填正文
        content_ok = self._fill_wechat_content(page, body)
        if not content_ok:
            logger.error("  ❌ 微信公众号：正文填写失败")
            self._save_screenshot(page, "direct_wechat_content_fail")
            return False

        # 5. 上传图片（正文内嵌图片或封面图）
        if image_paths:
            self._upload_wechat_images(page, image_paths)

        self._save_screenshot(page, "direct_wechat_before_draft")

        if dry_run:
            logger.info("🔍 干跑模式：公众号内容已填入，但不保存草稿")
            return True

        # 6. 点击「保存为草稿」⚠️不点「发表」
        saved = page.evaluate('''() => {
            const candidates = ['保存为草稿', '保存草稿', '存草稿'];
            const btns = document.querySelectorAll('button, a, span, div');
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (candidates.includes(t)) {
                    const r = b.getBoundingClientRect();
                    if (r.width > 30 && r.height > 15) {
                        b.click();
                        return t;
                    }
                }
            }
            return false;
        }''')

        if not saved:
            logger.warning("  ⚠️ 微信公众号：未找到「保存为草稿」按钮")
            self._save_screenshot(page, "direct_wechat_draft_btn_fail")
            return False

        logger.info(f"  ✓ 已点击「{saved}」")
        time.sleep(3)

        self._save_screenshot(page, "direct_wechat_after_draft")
        logger.success("✓ 微信公众号草稿已保存")
        return True

    def _find_wechat_editor_page(self, original_page: Page) -> Optional[Page]:
        """查找公众号图文编辑器页面（可能在新标签页中打开）"""
        # 检查所有标签页
        for pg in self._context.pages:
            if "appmsg_edit" in pg.url:
                return pg
        # 如果没有新标签页，返回原页面
        return original_page

    def _fill_wechat_title(self, page: Page, title: str) -> bool:
        """在公众号编辑器中填写标题"""
        logger.info(f"  填写标题: {title[:30]}...")

        # 策略1：直接找标题输入框
        title_input = page.locator('input[placeholder*="标题"], input[placeholder*="请输入"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.success(f"  ✓ 标题已填写")
            return True

        # 策略2：JS 兜底
        filled = page.evaluate('''(title) => {
            const inputs = document.querySelectorAll('input[type="text"]');
            for (const input of inputs) {
                const ph = (input.placeholder || '') + (input.getAttribute('aria-label') || '');
                if (ph.includes('标题') || ph.includes('请输入')) {
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
            logger.success(f"  ✓ 标题已填写 (JS)")
        else:
            logger.warning("  ⚠️ 未找到标题输入框")
        return filled

    def _upload_wechat_images(self, page: Page, image_paths: list):
        """在公众号编辑器中上传图片（封面图+正文图片）"""
        logger.info(f"  上传图片: {len(image_paths)} 张")

        # 尝试找到图片上传入口
        # 策略1：找 file input
        file_input = page.locator('input[type="file"][accept*="image"]')
        if file_input.count() > 0:
            try:
                file_input.first.set_input_files(image_paths[0])
                logger.info(f"  ✓ 封面图已上传")
                time.sleep(3)
            except Exception as e:
                logger.warning(f"  ⚠️ 封面图上传失败: {e}")
        else:
            # 策略2：点击上传图片按钮
            clicked = page.evaluate('''() => {
                const all = document.querySelectorAll('div, button, span, a');
                for (const el of all) {
                    const t = (el.textContent || '').trim();
                    if (t.includes('上传图片') || t.includes('添加图片')) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 20 && rect.height > 10) {
                            el.click();
                            return t;
                        }
                    }
                }
                return false;
            }''')

            if clicked:
                time.sleep(1)
                file_input = page.locator('input[type="file"][accept*="image"]')
                if file_input.count() > 0:
                    try:
                        file_input.first.set_input_files(image_paths[0])
                        logger.info(f"  ✓ 图片已上传（点击后上传）")
                        time.sleep(3)
                    except Exception as e:
                        logger.warning(f"  ⚠️ 图片上传失败: {e}")
                else:
                    logger.warning("  ⚠️ 点击上传后未找到 file input")
            else:
                logger.warning("  ⚠️ 未找到图片上传入口，跳过图片上传（降级为无图）")

    # ========== 今日头条直发 ==========

    def _publish_toutiao_direct(
        self, page: Page, title: str, body: str,
        image_paths: list, dry_run: bool,
    ) -> bool:
        """今日头条直发：填标题+正文+图片+话题+发布"""
        logger.info("📝 今日头条：直连发布")

        # 1. 打开头条发布页
        page.goto(TOUTIAO_EDITOR_URL, wait_until="domcontentloaded", timeout=20000)
        time.sleep(5)

        # 检查登录状态
        if "login" in page.url.lower() or "passport" in page.url.lower():
            logger.error("❌ 头条未登录，请先在 Chrome 中登录 mp.toutiao.com")
            return False

        self._save_screenshot(page, "direct_toutiao_after_nav")

        # 2. 填标题
        self._fill_toutiao_title(page, title)

        # 3. 上传图片
        if image_paths:
            self._upload_toutiao_images(page, image_paths)

        # 4. 填正文
        content_ok = self._fill_toutiao_content(page, body)
        if not content_ok:
            logger.error("  ❌ 今日头条：正文填写失败")
            self._save_screenshot(page, "direct_toutiao_content_fail")
            return False

        # 5. 追加话题
        logger.info(f"  追加话题: {TOUTIAO_HASHTAG}")
        appended = self._append_toutiao_hashtag(page)
        if not appended:
            logger.warning("  ⚠️ 话题未写入正文，继续发布")

        self._save_screenshot(page, "direct_toutiao_before_publish")

        if dry_run:
            logger.info("🔍 干跑模式：头条内容已填入，但不点击发布")
            return True

        # 6. 点击红色「发布」按钮
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
                if ((b.textContent || '').trim() === '发布') {
                    b.click();
                    return '发布(fallback)';
                }
            }
            return false;
        }''')

        if not publish_clicked:
            logger.warning("  ⚠️ 今日头条：未找到发布按钮")
            self._save_screenshot(page, "direct_toutiao_publish_btn_fail")
            return False

        logger.info(f"  ✓ 今日头条发布按钮已点击: {publish_clicked}")
        time.sleep(4)

        # 7. 处理确认弹窗
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

        self._save_screenshot(page, "direct_toutiao_after_publish")
        logger.success("✓ 今日头条发布完成")
        return True

    def _fill_toutiao_title(self, page: Page, title: str) -> bool:
        """在头条编辑器中填写标题"""
        logger.info(f"  填写标题: {title[:30]}...")

        # 策略1：placeholder 匹配
        title_input = page.locator('input[placeholder*="标题"], input[placeholder*="请输入"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.success(f"  ✓ 标题已填写")
            return True

        # 策略2：常见 id/name
        title_input = page.locator('#title, input[name="title"], input[type="text"]')
        if title_input.count() > 0:
            title_input.first.click()
            title_input.first.fill(title)
            logger.success(f"  ✓ 标题已填写 (id/name)")
            return True

        # 策略3：JS 兜底
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
            logger.success(f"  ✓ 标题已填写 (JS)")
        else:
            logger.warning("  ⚠️ 未找到标题输入框")
        return filled

    def _fill_toutiao_content(self, page: Page, content: str) -> bool:
        """在头条编辑器中填写正文"""
        logger.info(f"  填写正文: {len(content)} 字")

        # 策略1：contenteditable div
        content_editor = page.locator('div[contenteditable="true"]')
        if content_editor.count() > 0:
            content_editor.first.click()
            page.keyboard.type(content, delay=5)
            logger.success(f"  ✓ 正文已填写 (contenteditable)")
            return True

        # 策略2：role="textbox"
        textbox = page.locator('[role="textbox"]')
        if textbox.count() > 0:
            textbox.first.click()
            page.keyboard.type(content, delay=5)
            logger.success(f"  ✓ 正文已填写 (textbox)")
            return True

        # 策略3：textarea
        textarea = page.locator('textarea[placeholder*="正文"], textarea[placeholder*="内容"], textarea')
        if textarea.count() > 0:
            textarea.first.click()
            textarea.first.fill(content)
            logger.success(f"  ✓ 正文已填写 (textarea)")
            return True

        # 策略4：JS 兜底
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
            logger.success(f"  ✓ 正文已填写 (JS)")
        else:
            logger.warning("  ⚠️ 未找到正文编辑器")
        return filled

    def _upload_toutiao_images(self, page: Page, image_paths: list):
        """在头条编辑器中上传图片"""
        logger.info(f"  上传图片: {len(image_paths)} 张")

        # 策略1：找到工具栏的图片上传按钮
        clicked = page.evaluate('''() => {
            const all = document.querySelectorAll('div, button, span, label');
            for (const el of all) {
                const t = (el.textContent || '').trim();
                if (t === '图片' || t.includes('上传图片') || t.includes('添加图片')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 10 && rect.height > 10 && rect.width < 100) {
                        el.click();
                        return t;
                    }
                }
            }
            return false;
        }''')

        if clicked:
            time.sleep(1)

        # 策略2：直接找 file input
        file_input = page.locator('input[type="file"][accept*="image"]')
        if file_input.count() > 0:
            try:
                file_input.first.set_input_files(image_paths)
                logger.info(f"  ✓ 图片已上传")
                time.sleep(3)
                return
            except Exception as e:
                logger.warning(f"  ⚠️ 图片上传失败: {e}")
        else:
            logger.warning("  ⚠️ 未找到图片上传入口，跳过图片上传（降级为无图）")

    def _append_toutiao_hashtag(self, page: Page) -> bool:
        """在头条正文末尾追加 #上头条 聊热点#"""
        # 找到正文编辑器，聚焦到末尾
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
            range.collapse(false);
            sel.removeAllRanges();
            sel.addRange(range);
            return true;
        }''')

        if not focused:
            return False

        page.keyboard.press('Enter')
        time.sleep(0.2)
        page.keyboard.type(TOUTIAO_HASHTAG, delay=80)
        time.sleep(2)

        # 验证是否写入
        state = page.evaluate('''() => {
            const editors = document.querySelectorAll(
                'div[contenteditable="true"], [role="textbox"]'
            );
            let best = null;
            for (const e of editors) {
                const len = (e.innerText || '').length;
                if (!best || len > (best.innerText || '').length) best = e;
            }
            return best ? best.innerText.slice(-60) : '';
        }''')

        return TOUTIAO_HASHTAG in state

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
    def _cleanup_images(image_paths: list):
        """删除本地下载的图片"""
        if not image_paths:
            return
        deleted = 0
        for img_path in image_paths:
            try:
                p = Path(img_path) if Path(img_path).is_absolute() else PROJECT_ROOT / img_path
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
