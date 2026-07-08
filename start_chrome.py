"""
Chrome 启动辅助脚本（Mac/Windows 双平台）

用途：一键启动带远程调试端口的 Chrome，供 Playwright 连接

使用方法：
  python3 start_chrome.py

启动后 Chrome 会打开，保持该终端窗口不要关闭。
然后在另一个终端运行发帖程序：
  python3 paste_post.py

⚠️  如果 Chrome 已经在运行，需要先完全退出再运行本脚本
"""

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from loguru import logger

# ========== 跨平台配置 ==========

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    # Windows Chrome 路径（按优先级尝试）
    CHROME_PATHS = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    CHROME_USER_DATA = os.path.join(os.environ.get("TEMP", ""), "chrome-automation-profile")
    CHROME_DEFAULT_PROFILE = os.path.expandvars(
        r"%LocalAppData%\Google\Chrome\User Data"
    )
else:
    # macOS Chrome 路径
    CHROME_PATHS = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    CHROME_USER_DATA = "/tmp/chrome-automation-profile"
    CHROME_DEFAULT_PROFILE = str(Path.home() / "Library/Application Support/Google/Chrome")

# 远程调试端口
DEBUG_PORT = 9222


def find_chrome() -> str:
    """查找 Chrome 可执行文件"""
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        "未找到 Chrome，请安装 Google Chrome\n"
        "下载地址：https://www.google.com/chrome/"
    )


def copy_profile_if_needed():
    """如果临时 profile 不存在，从默认 profile 复制"""
    dest = Path(CHROME_USER_DATA)
    if dest.exists():
        logger.info(f"临时 profile 已存在: {dest}")
        return

    src = Path(CHROME_DEFAULT_PROFILE)
    if not src.exists():
        logger.warning("未找到 Chrome 默认 profile，将使用空白 profile")
        dest.mkdir(parents=True, exist_ok=True)
        return

    logger.info("首次运行，复制 Chrome profile（约需1分钟）...")
    dest.mkdir(parents=True, exist_ok=True)
    dest_default = dest / "Default"
    dest_default.mkdir(parents=True, exist_ok=True)

    src_default = src / "Default"
    # 复制关键文件
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

    for item in items:
        src_item = src_default / item
        if src_item.exists():
            try:
                if IS_WINDOWS:
                    # Windows 用 shutil.copytree/copy2
                    dst_item = dest_default / item
                    if src_item.is_dir():
                        if dst_item.exists():
                            shutil.rmtree(dst_item)
                        shutil.copytree(str(src_item), str(dst_item))
                    else:
                        shutil.copy2(str(src_item), str(dst_item))
                else:
                    # macOS 用 cp -r（更快）
                    subprocess.run(["cp", "-r", str(src_item), str(dest_default / item)],
                                   capture_output=True, timeout=30)
                logger.debug(f"  ✓ {item}")
            except Exception as e:
                logger.warning(f"  ✗ {item}: {e}")

    # 复制父级配置
    for item in ["Local State", "First Run", "Last Browser"]:
        src_item = src / item
        if src_item.exists():
            try:
                if IS_WINDOWS:
                    shutil.copy2(str(src_item), str(dest / item))
                else:
                    subprocess.run(["cp", str(src_item), str(dest / item)],
                                   capture_output=True, timeout=5)
            except Exception:
                pass

    logger.success("Profile 复制完成 ✓")


def check_port_available() -> bool:
    """检查调试端口是否已被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        result = s.connect_ex(('localhost', DEBUG_PORT))
        if result == 0:
            logger.warning(f"端口 {DEBUG_PORT} 已被占用，Chrome 可能已在运行")
            return False
    return True


def start_chrome():
    """启动带调试端口的 Chrome"""
    chrome_path = find_chrome()
    copy_profile_if_needed()

    if not check_port_available():
        logger.info("Chrome 可能已经在运行，尝试连接...")
        return True

    logger.info("启动 Chrome...")
    cmd = [
        chrome_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={CHROME_USER_DATA}",
        # ⚠️ 反休眠参数（锁屏后 Chrome 不挂起，Playwright 可继续操作）
        "--disable-backgrounding-occluded-windows",   # 窗口被遮挡时不降低优先级
        "--disable-renderer-backgrounding",           # 渲染进程不被后台挂起
        "--disable-background-networking",             # 后台网络不被节流
        "--disable-popup-blocking",                   # 不拦截弹窗（MultiPost需要）
        "--disable-features=CalculateNativeWinOcclusion",  # 禁用窗口遮挡检测
    ]

    # 在后台启动
    # macOS: 用 caffeinate 包裹，防止系统休眠导致 Chrome 挂起
    if not IS_WINDOWS:
        cmd = ["caffeinate", "-i"] + cmd

    process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待启动
    logger.info("等待 Chrome 启动...")
    time.sleep(5)

    # 检查是否成功
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/version", timeout=3)
        if resp.status == 200:
            logger.success(f"✓ Chrome 已启动，调试端口: {DEBUG_PORT}")

            # 检查 Wechatsync 扩展是否已加载
            _check_wechatsync_extension()

            logger.info(f"保持此窗口不要关闭，在另一个终端运行：python3 paste_post.py")
            return True
    except Exception:
        pass

    logger.error("Chrome 启动失败，请检查是否有其他 Chrome 实例在运行")
    if IS_WINDOWS:
        logger.info("请先完全退出 Chrome（关闭所有窗口），然后重新运行本脚本")
    else:
        logger.info("请先完全退出 Chrome（Cmd+Q），然后重新运行本脚本")
    return False


def _check_wechatsync_extension():
    """检查 Wechatsync Chrome 扩展是否已加载"""
    import json
    import urllib.request

    try:
        # 通过 Chrome DevTools Protocol 获取已安装的扩展
        resp = urllib.request.urlopen(f"http://localhost:{DEBUG_PORT}/json/list", timeout=3)
        targets = json.loads(resp.read().decode())

        # 检查是否有 Wechatsync 相关的扩展页面
        wechatsync_found = False
        for target in targets:
            url = target.get("url", "")
            title = target.get("title", "")
            if "wechatsync" in url.lower() or "sync-assistant" in url.lower() or "wechatsync" in title.lower():
                wechatsync_found = True
                break

        if wechatsync_found:
            logger.success("  ✓ Wechatsync 扩展已加载")
        else:
            logger.warning("  ⚠️ Wechatsync 扩展未检测到")
            logger.info("  如需多平台发布，请安装 Wechatsync 扩展：")
            logger.info("    1. Chrome 打开 chrome://extensions/")
            logger.info("    2. 启用开发者模式")
            # 跨平台扩展路径提示
            if IS_WINDOWS:
                ext_path = str(Path(os.environ.get("USERPROFILE", "~")) / "claude" / "Wechatsync" / "packages" / "extension" / "dist")
            else:
                ext_path = str(Path.home() / "claude" / "Wechatsync" / "packages" / "extension" / "dist")
            logger.info(f"    3. 加载已解压的扩展: {ext_path}")
            logger.info("    4. 扩展设置中启用 MCP 连接，Token: maimai-sync-2024")

    except Exception as e:
        logger.debug(f"  Wechatsync 扩展检查跳过: {e}")


if __name__ == "__main__":
    start_chrome()
