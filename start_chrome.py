"""
Chrome 启动辅助脚本（Mac/Windows 双平台）

用途：一键启动带远程调试端口的 Chrome，供 Playwright 连接

使用方法：
  python3 start_chrome.py

启动后 Chrome 会打开，保持该终端窗口不要关闭。
然后在另一个终端运行发帖程序：
  python3 paste_post.py

⚠️  如果 Chrome 已经在运行，需要先完全退出再运行本脚本
⚠️  Windows 用户：必须先关闭日常 Chrome！脚本直接使用默认 profile，
     同一个 profile 不能被两个 Chrome 实例同时占用。
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
    # ⚠️ Windows 直接使用默认 profile，不复制！
    # 原因：Windows 强制文件锁，复制 profile 时如果 Chrome 在运行会导致扩展文件缺失
    # 直接用默认 profile = 扩展/登录状态 100% 可用，零复制风险
    CHROME_USER_DATA = os.path.expandvars(
        r"%LocalAppData%\Google\Chrome\User Data"
    )
else:
    # macOS Chrome 路径
    CHROME_PATHS = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    # macOS 用临时 profile（cp -r 无视建议锁，复制可靠）
    CHROME_USER_DATA = "/tmp/chrome-automation-profile"
    CHROME_DEFAULT_PROFILE = str(Path.home() / "Library/Application Support/Google/Chrome")

# 远程调试端口
DEBUG_PORT = 9222

# MultiPost 扩展 ID（用于复制后验证）
MULTIPOST_EXT_ID = "dhohkaclnjgcikfoaacfgijgjgceofih"


def find_chrome() -> str:
    """查找 Chrome 可执行文件"""
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    raise FileNotFoundError(
        "未找到 Chrome，请安装 Google Chrome\n"
        "下载地址：https://www.google.com/chrome/"
    )


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


def _kill_chrome():
    """强制关闭 Chrome 进程，等待文件锁释放"""
    try:
        if IS_WINDOWS:
            subprocess.run('taskkill /F /IM chrome.exe', shell=True, capture_output=True, timeout=15)
            # Windows 强制锁：Chrome 进程杀掉后文件锁不会立即释放
            # 需要等待更久确保 SQLite/Extensions 锁全部释放
            logger.info("  等待 Windows 文件锁释放...")
            time.sleep(8)
            # 二次确认：可能还有残留子进程
            if _is_chrome_running():
                logger.warning("  检测到残留 Chrome 进程，再次关闭...")
                subprocess.run('taskkill /F /IM chrome.exe', shell=True, capture_output=True, timeout=15)
                time.sleep(5)
        else:
            subprocess.run(['pkill', '-x', 'Google Chrome'], capture_output=True, timeout=10)
            time.sleep(3)
        logger.success("✓ Chrome 已关闭")
    except Exception as e:
        logger.warning(f"关闭 Chrome 失败: {e}")


def _validate_multipost_extension(profile_dir: str) -> bool:
    """验证 MultiPost 扩展文件是否完整存在于 profile 中

    检查关键文件：
      - manifest.json（扩展注册入口）
      - inject-api.js（网页检测扩展的核心文件）
      - service-worker-loader.js（Manifest V3 后台脚本）
    """
    default_dir = Path(profile_dir) / "Default"
    ext_base = default_dir / "Extensions" / MULTIPOST_EXT_ID

    if not ext_base.exists():
        logger.warning(f"  ⚠️ MultiPost 扩展目录不存在: {ext_base}")
        return False

    # 找到版本子目录（如 2.0.9_0）
    version_dirs = [d for d in ext_base.iterdir() if d.is_dir()]
    if not version_dirs:
        logger.warning(f"  ⚠️ MultiPost 扩展无版本目录: {ext_base}")
        return False

    version_dir = version_dirs[0]
    critical_files = [
        "manifest.json",
        "inject-api.js",
        "service-worker-loader.js",
    ]

    missing = []
    for fname in critical_files:
        if not (version_dir / fname).exists():
            missing.append(fname)

    if missing:
        logger.warning(f"  ⚠️ MultiPost 扩展文件缺失: {missing}")
        logger.warning(f"  扩展路径: {version_dir}")
        return False

    logger.info(f"  ✓ MultiPost 扩展文件完整 (版本目录: {version_dir.name})")
    return True


def copy_profile_if_needed(force: bool = False):
    """如果临时 profile 不存在，从默认 profile 复制

    ⚠️ Windows 不复制！直接使用默认 profile（CHROME_USER_DATA 就是默认路径）。
    只有 macOS 走复制逻辑（macOS 建议锁，cp -r 不受影响）。

    参数:
        force: 强制重新复制（删除旧 profile 再复制）
    """
    # Windows 直接用默认 profile，不需要复制
    if IS_WINDOWS:
        if not Path(CHROME_USER_DATA).exists():
            logger.error(f"❌ Chrome 默认 profile 不存在: {CHROME_USER_DATA}")
            logger.error("   请确认已安装 Google Chrome 并至少启动过一次")
            sys.exit(1)
        logger.info(f"Windows: 直接使用默认 profile（无需复制）")
        return

    # === 以下为 macOS 逻辑 ===
    dest = Path(CHROME_USER_DATA)

    # 强制重新复制
    if force and dest.exists():
        logger.info("强制重新复制 profile（删除旧 profile）...")
        try:
            shutil.rmtree(dest)
        except Exception as e:
            logger.error(f"❌ 无法删除旧 profile: {e}")
            sys.exit(1)

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

    # Windows: 直接用默认 profile，必须先关闭日常 Chrome
    if IS_WINDOWS and _is_chrome_running():
        logger.warning("⚠️ 检测到 Chrome 正在运行")
        logger.warning("   Windows 直接使用默认 profile，必须先关闭日常 Chrome")
        logger.warning("   正在自动关闭 Chrome...")
        _kill_chrome()
        if _is_chrome_running():
            logger.error("❌ Chrome 关闭失败，请手动关闭后重试")
            logger.error("   方法：右键任务栏 Chrome 图标 → 退出")
            logger.error("   或运行：taskkill /F /IM chrome.exe")
            sys.exit(1)

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


if __name__ == "__main__":
    start_chrome()
