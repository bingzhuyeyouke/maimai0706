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


# ========== 常量 ==========

# 跨平台判断（需在 DEDICATED_USER_DATA 之前定义）
IS_WINDOWS = platform.system() == "Windows"

# Chrome 远程调试地址（默认，connect 时可能被覆盖）
DEFAULT_CDP_URL = "http://localhost:9222"

# 独立 Chrome 实例的调试端口和 profile 目录
# dedicated 模式：脚本自动启停 Chrome，用户无需手动运行 start_chrome.py
# 使用与 start_chrome.py 相同的 profile 和端口，确保登录状态可用
DEDICATED_DEBUG_PORT = 9333
if IS_WINDOWS:
    # Windows 直接用默认 profile，不复制（避免文件锁导致扩展缺失）
    DEDICATED_USER_DATA = os.path.expandvars(r"%LocalAppData%\Google\Chrome\User Data")
else:
    DEDICATED_USER_DATA = "/tmp/chrome-automation-profile"

# MultiPost 编辑器地址
MULTIPOST_URL = "https://multipost.app/"

# 默认要发布的平台
DEFAULT_PLATFORMS = ["微信公众号", "今日头条"]

# MultiPost 扩展 ID
MULTIPOST_EXT_ID = "dhohkaclnjgcikfoaacfgijgjgceofih"

# 今日头条正文末尾要追加的话题（用户要求）
TOUTIAO_HASHTAG = "#上头条 聊热点#"# 今日头条正文末尾要追加的话题（用户要求）
TOUTIAO_HASHTAG = "#上头条 聊热点#"

# 跨平台 Chrome 路径
if IS_WINDOWS:
    CHROME_PATHS = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    CHROME_DEFAULT_PROFILE = os.path.expandvars(
        r"%LocalAppData%\Google\Chrome\User Data"
    )
else:
    CHROME_PATHS = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    CHROME_DEFAULT_PROFILE = str(Path.home() / "Library/Application Support/Google/Chrome")


def _find_chrome() -> str:
    """查找 Chrome 可执行文件"""
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    raise FileNotFoundError("未找到 Chrome，请安装 Google Chrome")


def _is_chrome_running() -> bool:
    """检测 Chrome 是否正在运行"""
    try:
        if IS_WINDOWS:
            result = subprocess.run(
                'tasklist /FI "IMAGENAME eq chrome.exe"',
                capture_output=True, text=True, timeout=10,
            )
            return 'chrome.exe' in result.stdout
        else:
            result = subprocess.run(
                ['pgrep', '-x', 'Google Chrome'],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
    except Exception:
        return False


def _validate_multipost_extension(profile_dir: str) -> bool:
    """验证 MultiPost 扩展文件是否完整存在于 profile 中

    检查关键文件：manifest.json、inject-api.js、service-worker-loader.js
    """
    default_dir = Path(profile_dir) / "Default"
    ext_base = default_dir / "Extensions" / MULTIPOST_EXT_ID

    if not ext_base.exists():
        logger.warning(f"  ⚠️ MultiPost 扩展目录不存在: {ext_base}")
        return False

    version_dirs = [d for d in ext_base.iterdir() if d.is_dir()]
    if not version_dirs:
        logger.warning(f"  ⚠️ MultiPost 扩展无版本目录: {ext_base}")
        return False

    version_dir = version_dirs[0]
    critical_files = ["manifest.json", "inject-api.js", "service-worker-loader.js"]
    missing = [f for f in critical_files if not (version_dir / f).exists()]

    if missing:
        logger.warning(f"  ⚠️ MultiPost 扩展文件缺失: {missing}")
        logger.warning(f"  扩展路径: {version_dir}")
        return False

    logger.info(f"  ✓ MultiPost 扩展文件完整 (版本: {version_dir.name})")
    return True


def _ensure_profile(profile_dir: str):
    """确保独立 profile 目录存在

    Windows: 直接使用默认 profile（DEDICATED_USER_DATA 就是默认路径），不复制。
    macOS: 从默认 profile 复制关键文件到临时目录。
    """
    # Windows 直接用默认 profile，无需复制
    if IS_WINDOWS:
        if not Path(profile_dir).exists():
            logger.error(f"  ❌ Chrome 默认 profile 不存在: {profile_dir}")
            raise RuntimeError("Chrome 默认 profile 不存在，请确认已安装 Chrome")
        return

    # === macOS 逻辑 ===
    dest = Path(profile_dir)
    if dest.exists():
        return

    logger.info(f"  首次使用独立 profile，复制登录状态（约30秒）...")
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
                subprocess.run(["cp", "-r", str(src_item), str(dest_default / item)],
                               capture_output=True, timeout=30)
            except Exception:
                pass

    for item in ["Local State", "First Run", "Last Browser"]:
        src_item = src / item
        if src_item.exists():
            try:
                subprocess.run(["cp", str(src_item), str(dest / item)],
                               capture_output=True, timeout=5)
            except Exception:
                pass

    logger.success("  ✓ Profile 复制完成")


def _start_dedicated_chrome(port: int, profile_dir: str) -> Optional[subprocess.Popen]:
    """启动独立的 Chrome 实例，返回进程对象"""
    chrome_path = _find_chrome()

    # Windows: 直接用默认 profile，必须先关闭日常 Chrome
    if IS_WINDOWS and _is_chrome_running():
        logger.warning("  ⚠️ 检测到 Chrome 正在运行，自动关闭...")
        try:
            subprocess.run('taskkill /F /IM chrome.exe', shell=True, capture_output=True, timeout=15)
        except Exception:
            pass
        time.sleep(3)
        if _is_chrome_running():
            raise RuntimeError("Chrome 关闭失败，请手动关闭后重试（taskkill /F /IM chrome.exe）")

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

    # macOS: caffeinate 防休眠
    if not IS_WINDOWS:
        cmd = ["caffeinate", "-i"] + cmd

    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待启动
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


def _stop_dedicated_chrome(process: subprocess.Popen, port: int):
    """关闭独立 Chrome 实例"""
    try:
        # 先尝试优雅关闭
        if IS_WINDOWS:
            process.terminate()
        else:
            process.send_signal(signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        logger.info("  ✓ 独立 Chrome 已关闭")
    except Exception as e:
        logger.warning(f"  ⚠️ 关闭独立 Chrome 异常: {e}")


class MultiPostPublisher(MaimaiPageOps):
    """
    MultiPost 发布器

    用法：
        # 方式1：自动启动独立Chrome（推荐，不影响日常浏览器）
        publisher = MultiPostPublisher(dedicated=True)
        publisher.connect()
        publisher.publish(...)
        publisher.disconnect()  # 自动关闭独立Chrome

        # 方式2：连接已有Chrome（传统方式）
        publisher = MultiPostPublisher()
        publisher.connect()
        publisher.publish(...)
        publisher.disconnect()  # 只断开连接，不关Chrome

    继承 MaimaiPageOps 以获得脉脉页面的 DOM 操作（添加话题/勾选开关/发动态），
    在 _publish_maimai 中用于 MultiPost 打开并填好的脉脉标签页。
    """

    def __init__(self, dedicated: bool = False, cdp_url: str = None):
        """
        参数:
            dedicated: True=自动启动独立Chrome实例（发布完成后自动关闭）
                       False=连接用户已有的Chrome（默认，兼容旧逻辑）
            cdp_url:   自定义CDP地址（仅非dedicated模式生效）
        """
        self._dedicated = dedicated
        self._cdp_url = cdp_url or DEFAULT_CDP_URL
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        # 独立 Chrome 进程
        self._chrome_process: Optional[subprocess.Popen] = None
        self._chrome_port: int = DEDICATED_DEBUG_PORT
        # 脉脉话题，publish() 时设置，_publish_maimai 读取
        self._maimai_topic: Optional[str] = None
        # 本次 publish 选中的平台列表，_click_publish 据此决定是否扫描脉脉标签页
        self._selected_platforms: Optional[list] = None

    def connect(self) -> bool:
        """
        连接到 Chrome 浏览器

        dedicated=True 时自动启动独立Chrome实例，
        dedicated=False 时连接用户已有的Chrome。
        """
        if self._dedicated:
            return self._connect_dedicated()
        else:
            return self._connect_existing()

    def _connect_dedicated(self) -> bool:
        """连接独立 Chrome 实例（9333端口，用户需提前启动并登录）"""
        cdp_url = f"http://localhost:{self._chrome_port}"
        logger.info(f"连接到独立 Chrome（{cdp_url}）...")

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else None

            if not self._context:
                logger.error("❌ 未找到浏览器上下文")
                return False

            logger.success("✓ 已连接到独立 Chrome")
            return True

        except Exception as e:
            logger.error(f"❌ 连接独立 Chrome 失败: {e}")
            logger.info(f"请先启动独立 Chrome（端口{self._chrome_port}）并登录各平台")
            return False

    @staticmethod
    def _kill_chrome_on_port(port: int):
        """关闭占用指定端口的 Chrome 进程"""
        try:
            if IS_WINDOWS:
                subprocess.run(
                    f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{port}\') do taskkill /F /PID %a',
                    shell=True, capture_output=True, timeout=10
                )
            else:
                # macOS/Linux: 找到监听该端口的进程并关闭
                result = subprocess.run(
                    f'lsof -ti :{port} | xargs kill -9 2>/dev/null',
                    shell=True, capture_output=True, timeout=10
                )
        except Exception:
            pass

    def _connect_existing(self) -> bool:
        """连接用户已启动的 Chrome 浏览器（传统方式）"""
        logger.info(f"连接到 Chrome（{self._cdp_url}）...")

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self._cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else None

            if not self._context:
                logger.error("❌ 未找到浏览器上下文")
                return False

            logger.success("✓ 已连接到 Chrome")
            return True

        except Exception as e:
            logger.error(f"❌ 连接 Chrome 失败: {e}")
            logger.info("请先启动 Chrome（带调试端口）：")
            if IS_WINDOWS:
                logger.info("  python start_chrome.py")
            else:
                logger.info("  python3 start_chrome.py")
            return False

    def _get_cdp_url(self) -> str:
        """获取当前CDP地址"""
        if self._dedicated:
            return f"http://localhost:{self._chrome_port}"
        return self._cdp_url

    def disconnect(self):
        """断开连接（不关闭用户的 Chrome）"""
        if self._playwright:
            self._playwright.stop()
        if self._dedicated:
            logger.info("已断开独立 Chrome 连接")
        else:
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

        ⚠️ 0711 改进：内置重试机制
        Chrome 扩展偶尔不打开所有平台标签页（如只打开头条，漏掉脉脉/公众号），
        mouse.click 改善了成功率但不能完全解决。加重试：如果标签页不全，
        关掉已打开的标签页，回到编辑器重新走一遍发布流程，最多重试2次。
        """
        if platforms is None:
            platforms = DEFAULT_PLATFORMS

        # 脉脉话题供 _publish_maimai 读取（单线程同步，无并发问题）
        self._maimai_topic = maimai_topic or DEFAULT_TOPIC
        self._selected_platforms = platforms

        max_attempts = 3  # 最多尝试3次

        for attempt in range(1, max_attempts + 1):
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

                # 第6步：点击发布 + 处理平台标签页（返回缺失平台列表）
                missing_platforms = self._click_publish(page, title, body, attempt=attempt, max_attempts=max_attempts)

                if not missing_platforms:
                    # 所有平台都成功了
                    return True

                # 有缺失平台，需要重试
                if attempt < max_attempts:
                    logger.warning(f"  🔄 第{attempt}次尝试缺失平台: {missing_platforms}，将重试...")
                    # 更新 platforms 为只缺失的平台，避免重复发布已成功的平台
                    platforms = missing_platforms
                    self._selected_platforms = platforms
                    # 等待一下再重试
                    time.sleep(5)
                else:
                    # 最后一次尝试仍失败
                    logger.error(f"  ❌ {max_attempts}次尝试后仍有缺失平台: {missing_platforms}")
                    return False

            except Exception as e:
                logger.error(f"❌ 发布失败: {e}")
                if attempt >= max_attempts:
                    return False
                logger.warning(f"  🔄 第{attempt}次尝试异常，重试...")
                time.sleep(5)

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
                            self._browser = self._playwright.chromium.connect_over_cdp(self._get_cdp_url())
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
                self._browser = self._playwright.chromium.connect_over_cdp(self._get_cdp_url())
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

        # ⚠️ 检测 MultiPost 扩展是否可用（window.$poster 是扩展注入的 API）
        # 扩展 content script 注入 inject-api.js → 创建 window.$poster / window.$syncer
        # 如果检测不到，说明扩展未加载（通常是 profile 复制不完整导致）
        ext_ok = found_page.evaluate('() => typeof window.$poster !== "undefined" || typeof window.$syncer !== "undefined"')
        if not ext_ok:
            # 再等2秒，content script 可能还没注入完
            time.sleep(2)
            ext_ok = found_page.evaluate('() => typeof window.$poster !== "undefined" || typeof window.$syncer !== "undefined"')

        if ext_ok:
            logger.success("✓ MultiPost 扩展已检测到（window.$poster 可用）")
        else:
            logger.error("=" * 50)
            logger.error("❌ MultiPost 扩展未检测到！页面无法发布到各平台")
            logger.error("   原因：Chrome 未加载 MultiPost 扩展（window.$poster 不存在）")
            logger.error("")
            logger.error("   修复步骤：")
            logger.error("   1. 确认已在 Chrome 中安装 MultiPost 扩展（文章同步助手）")
            logger.error("   2. 完全关闭日常 Chrome（Windows: 右键任务栏→退出）")
            if not IS_WINDOWS:
                logger.error("   3. 删除临时 profile：rm -rf /tmp/chrome-automation-profile")
                logger.error("   4. 重新运行 python3 start_chrome.py")
            else:
                logger.error("   3. 重新运行 python start_chrome.py（Windows 直接用默认 profile，无需删除）")
            logger.error("=" * 50)
            raise RuntimeError("MultiPost 扩展未检测到，请按上述步骤修复")

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
        """第3步：点击蓝色「下一步」按钮

        ⚠️ 使用 Playwright mouse.click() 代替 JS btn.click()，
        与 _click_publish 保持一致，确保用户手势标志正确传递。
        """
        logger.info("点击「下一步」...")

        # 找蓝色按钮坐标
        btn_pos = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {
                        return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
                    }
                }
            }
            return null;
        }''')

        if not btn_pos:
            raise RuntimeError("未找到「下一步」按钮")

        page.mouse.click(btn_pos['x'], btn_pos['y'])

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

    def _click_publish(self, page: Page, title: str, body: str, attempt: int = 1, max_attempts: int = 1) -> list:
        """第6步：点击 MultiPost 发布按钮，然后处理各平台标签页

        MultiPost 点发布后，各平台标签页可能是：
          - 新开的（头条/公众号通常新开）
          - 复用已有的（脉脉通常复用）
        统一用按内容匹配的方式找到所有需要处理的标签页。

        ⚠️ 0711 修复：使用 Playwright mouse.click() 代替 JS evaluate btn.click()
        原因：JS btn.click() 是程序化点击，不携带浏览器「用户手势标志」，
        Chrome 扩展调用 chrome.tabs.create() 时可能因缺少手势标志而静默失败，
        导致只打开头条标签页而丢失脉脉/公众号。
        Playwright mouse.click() 模拟完整的 mousedown→mouseup→click 事件序列，
        与真实用户点击一致，手势标志能正确传递给扩展。

        返回:
            缺失平台中文名列表（如 ["脉脉", "微信公众号"]），空列表=全部成功
        """
        logger.info("⚠️  即将点击发布按钮，这是真实发布操作！")

        # 用 JS 找到按钮的坐标，再用 Playwright mouse.click() 点击
        # ⚠️ 不能用 evaluate(() => btn.click())——那是程序化点击，不传手势标志
        btn_pos = page.evaluate('''() => {
            const btns = document.querySelectorAll('button');
            for (const btn of btns) {
                const style = getComputedStyle(btn);
                if (style.backgroundColor.includes('0, 111, 238')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100) {
                        return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, w: rect.width };
                    }
                }
            }
            return null;
        }''')

        if not btn_pos:
            raise RuntimeError("未找到发布按钮")

        # Playwright mouse.click 模拟真实鼠标点击（mousedown → mouseup → click）
        page.mouse.click(btn_pos['x'], btn_pos['y'])
        logger.info("  ✓ MultiPost 发布按钮已点击（Playwright mouse.click）")

        # 等待 Chrome 扩展打开平台标签页（需要足够时间）
        logger.info("  等待 Chrome 扩展打开平台标签页（20秒）...")
        time.sleep(20)

        # ⚠️ 重连 Playwright：Chrome 扩展新开的标签页不会被已连接的 context 追踪，
        # 必须断开重连才能拿到最新标签页列表（0707版验证）
        logger.info("  重连 Playwright 以扫描新标签页...")
        try:
            self._playwright.stop()
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self._get_cdp_url())
            self._context = self._browser.contexts[0] if self._browser.contexts else None
            if self._context:
                logger.success("  ✓ Playwright 已重连")
            else:
                logger.error("  ❌ 重连后未找到上下文")
                return list(self._selected_platforms or [])
        except Exception as e:
            logger.error(f"  ❌ Playwright 重连失败: {e}")
            return list(self._selected_platforms or [])

        # 按内容匹配找到所有刚填入内容的平台标签页
        # ⚠️ 传入期望平台列表 + 增大超时，避免只找到快打开的头条就返回
        platform_tabs = self._find_all_platform_tabs(
            body,
            timeout=25,
            expected_platforms=self._selected_platforms,
        )

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
                return []
            elif result == 'error':
                logger.error("❌ MultiPost 发布失败")
                return list(self._selected_platforms or [])
            else:
                logger.warning("⚠️  发布结果未知，未检测到平台标签页")
                self._save_screenshot(page, "multipost_after_publish")
                return []

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
            return list(self._selected_platforms or [])

        all_success = all(known_results.values())
        if all_success:
            logger.success("🎉 所有平台发布成功！")
            return []

        # 计算缺失平台：期望的平台中，未成功发布的
        # 平台 key → 中文名 反向映射
        KEY_TO_CN = {v: k for k, v in self.PLATFORM_CN_TO_KEY.items()}
        missing = []
        for cn_name in (self._selected_platforms or []):
            key = self.PLATFORM_CN_TO_KEY.get(cn_name)
            if key and key not in known_results:
                missing.append(cn_name)
            elif key and not known_results.get(key, False):
                missing.append(cn_name)

        if missing:
            logger.warning(f"  ⚠️ 缺失平台: {missing}")
        return missing

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

    # 平台中文名 → 内部 key 的映射（供 _find_all_platform_tabs 使用）
    PLATFORM_CN_TO_KEY = {
        '脉脉': 'maimai',
        '微信公众号': 'wechat',
        '今日头条': 'toutiao',
    }

    def _find_all_platform_tabs(self, expected_body: str, timeout: int = 15, expected_platforms: list = None) -> list:
        """
        找到所有刚被 MultiPost 打开的平台标签页。

        策略（0711版改进）：
          1. 先按 URL 匹配平台（maimai.cn / mp.weixin.qq.com / mp.toutiao.com）
          2. 内容匹配仅作辅助确认（不强制，因为MultiPost可能格式化了内容）
          3. ⚠️ 找到部分标签页后继续等待，直到所有 expected_platforms 都找到或超时
             （旧版：找到任何一个就立即返回，导致慢打开的脉脉/公众号被漏掉）
        返回去重后的 Page 列表（每个平台只取最新的一个）。
        """
        fragment = (expected_body or "")[:30]

        # 计算期望找到的平台 key 集合
        expected_keys = set()
        if expected_platforms:
            for cn_name in expected_platforms:
                key = self.PLATFORM_CN_TO_KEY.get(cn_name)
                if key:
                    expected_keys.add(key)
        if not expected_keys:
            # 没传期望列表，退化为旧逻辑（找到任何一个就返回）
            expected_keys = None

        logger.info("扫描所有平台标签页（URL匹配+内容辅助）...")
        if expected_keys:
            logger.info(f"  期望平台: {sorted(expected_keys)}")

        # 平台 URL 匹配规则
        PLATFORM_URL_RULES = {
            'maimai': 'maimai.cn',
            'wechat': 'mp.weixin.qq.com',
            'toutiao': 'mp.toutiao.com',
        }

        # 已确认找到的平台标签页（platform_key → Page），跨轮次累积
        confirmed_tabs = {}
        deadline = time.time() + timeout

        while time.time() < deadline:
            # 扫描当前所有标签页
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

                # 已经确认过的平台，跳过重复扫描
                if platform in confirmed_tabs:
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

                # 确认该平台标签页
                confirmed_tabs[platform] = pg

            # 检查是否已找齐期望平台
            if expected_keys is not None:
                found_keys = set(confirmed_tabs.keys())
                missing = expected_keys - found_keys
                if not missing:
                    logger.info(f"  ✓ 所有期望平台标签页已找齐: {sorted(found_keys)}")
                    break
                else:
                    remaining = int(deadline - time.time())
                    if remaining > 0:
                        logger.info(f"  ⏳ 已找到 {sorted(found_keys)}，等待 {sorted(missing)}（剩余 {remaining}s）...")
            else:
                # 旧逻辑：找到任何一个就返回
                if confirmed_tabs:
                    break

            time.sleep(2)

        if not confirmed_tabs:
            logger.warning("  ⚠️ 未找到任何平台标签页")
            return []

        # 汇总结果
        result_pages = list(confirmed_tabs.values())
        platforms_found = list(confirmed_tabs.keys())
        logger.info(f"  ✓ 共找到 {len(result_pages)} 个平台标签页: {platforms_found}")

        # 如果有期望但未找到的平台，记录警告
        if expected_keys:
            missing = expected_keys - set(platforms_found)
            if missing:
                logger.warning(f"  ⚠️ 以下平台标签页未在超时内打开: {sorted(missing)}")

        return result_pages

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
                p = Path(img_path) if Path(img_path).is_absolute() else PROJECT_ROOT / img_path
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
