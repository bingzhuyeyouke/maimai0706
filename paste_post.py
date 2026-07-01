"""
粘贴发帖模式 — 你粘贴 DeepSeek 输出，我帮你批量发到脉脉

用法：
  方式1: 命令行
    python3 paste_post.py --posts "1. 标题：xxx\n正文：yyy\n---\n2. 标题：zzz\n正文：zzz"

  方式2: 从文件读取
    python3 paste_post.py --file posts.txt

  方式3: 交互式（推荐）
    python3 paste_post.py
    然后粘贴内容，输入 END 结束

文件格式（posts.txt）：
  1. 标题：华为员工爆料：余承东说盘古大模型是先行者
  正文：华为员工爆料：余承东在大会上表示……
  ---
  2. 标题：美团员工问：……
  正文：……
  ---
  3. 标题：……
  正文：……

图片：放在 posts/images/ 目录下，文件名在正文中用 [图片:xxx.jpg] 标记
  或者直接用 --images img1.jpg img2.jpg ... 给所有帖子配同一组图

前置条件：
  1. Chrome 带调试端口启动: python3 start_chrome.py
  2. 已登录脉脉
"""

import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger

from config import settings, PROJECT_ROOT
from publisher.maimai import MaimaiPoster


# ========== 日志 ==========

def setup_logger():
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    )


# ========== 解析粘贴内容 ==========

def parse_posts(text: str) -> List[Dict]:
    """
    解析粘贴的内容，支持多种格式：

    格式1（编号+标题+正文）：
      1. 标题：华为员工爆料：xxx
      正文：华为员工爆料……
      2. 标题：美团员工问：xxx
      正文：……

    格式2（标题+正文，无编号）：
      标题：华为员工爆料：xxx
      正文：华为员工爆料……
      ---
      标题：美团员工问：xxx
      正文：……

    格式3（用 === 或 --- 分隔的自由文本）：
      华为员工爆料：xxx。余承东在大会上表示……
      ---
      美团员工问：xxx。……

    格式4（DeepSeek 爆料模式输出）：
      1. 标题：xxx
      正文：xxx
      2. 标题：xxx
      正文：xxx
    """
    posts = []

    # 尝试格式1/4：编号 + 标题 + 正文
    # 匹配: "1. 标题：xxx" 或 "1. 标题:xxx"
    pattern_numbered = re.compile(
        r'(?:^|\n)\s*(\d+)\.\s*标题[：:]\s*(.+?)(?:\n\s*正文[：:]\s*(.+?))?(?=\n\s*\d+\.\s*标题[：:]|\Z)',
        re.DOTALL,
    )
    matches = list(pattern_numbered.finditer(text))

    if matches:
        for m in matches:
            title = m.group(2).strip()[:20]
            content = (m.group(3) or '').strip()[:1000]
            if not content:
                # 可能正文和标题在同一行或在下一行
                content = title  # 退回：用标题当内容
            posts.append({
                'title': title,
                'content': content,
                'topic': '我来爆个料',
                'image_paths': [],
            })
        return posts

    # 尝试格式2：标题 + 正文（无编号）
    pattern_labeled = re.compile(
        r'标题[：:]\s*(.+?)\n\s*正文[：:]\s*(.+?)(?=\n\s*标题[：:]|\Z)',
        re.DOTALL,
    )
    matches = list(pattern_labeled.finditer(text))

    if matches:
        for m in matches:
            title = m.group(1).strip()[:20]
            content = m.group(2).strip()[:1000]
            posts.append({
                'title': title,
                'content': content,
                'topic': '我来爆个料',
                'image_paths': [],
            })
        return posts

    # 格式3：用分隔符拆分的自由文本
    # 先尝试用 --- 或 === 拆分
    chunks = re.split(r'\n[-=]{3,}\n', text)

    if len(chunks) > 1:
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            # 取第一行作为标题（截断到20字），其余作为正文
            lines = chunk.split('\n')
            first_line = lines[0].strip()
            # 去掉编号前缀
            first_line = re.sub(r'^\d+\.\s*', '', first_line)
            title = first_line[:20]
            content = chunk[:1000]
            posts.append({
                'title': title,
                'content': content,
                'topic': '我来爆个料',
                'image_paths': [],
            })
        return posts

    # 格式4：整段文本，按编号拆分
    # "1. xxx\n2. xxx\n3. xxx"
    numbered_items = re.split(r'\n(?=\d+\.\s)', text)
    if len(numbered_items) > 1:
        for item in numbered_items:
            item = item.strip()
            if not item:
                continue
            # 去编号
            item = re.sub(r'^\d+\.\s*', '', item)
            # 第一行做标题
            first_line = item.split('\n')[0].strip()
            title = first_line[:20]
            content = item[:1000]
            posts.append({
                'title': title,
                'content': content,
                'topic': '我来爆个料',
                'image_paths': [],
            })
        return posts

    # 兜底：整段文本作为一篇帖子
    if text.strip():
        first_line = text.strip().split('\n')[0][:20]
        posts.append({
            'title': first_line,
            'content': text.strip()[:1000],
            'topic': '我来爆个料',
            'image_paths': [],
        })

    return posts


def resolve_images(posts: List[Dict], image_dir: str = None, image_files: List[str] = None):
    """
    解析图片路径，支持3种配对方式（优先级从高到低）：

    1. --images 指定图片列表（所有帖子共用）
    2. 正文中 [图片:xxx.jpg] 标记（每篇独立指定）
    3. posts/images/ 目录按序号自动配对（1.jpg→第1篇，2.jpg→第2篇...）
    """
    # 优先使用 --image-dir 参数，否则默认 posts/images/
    if image_dir:
        img_dir = Path(image_dir)
        if not img_dir.is_absolute():
            img_dir = PROJECT_ROOT / img_dir
    else:
        img_dir = PROJECT_ROOT / 'posts' / 'images'

    # 方式1：从 --images 参数（所有帖子共用）
    if image_files:
        for post in posts:
            resolved = []
            for f in image_files:
                p = Path(f)
                if p.exists():
                    resolved.append(str(p))
                elif (img_dir / f).exists():
                    resolved.append(str(img_dir / f))
                else:
                    logger.warning(f"图片不存在: {f}")
            post['image_paths'] = resolved
        return

    # 方式2：从正文中的 [图片:xxx.jpg] 标记
    has_inline_images = False
    for post in posts:
        img_refs = re.findall(r'\[图片[：:](.+?)\]', post['content'])
        if img_refs:
            has_inline_images = True
            resolved = []
            for ref in img_refs:
                ref = ref.strip()
                p = Path(ref)
                if p.exists():
                    resolved.append(str(p))
                elif (img_dir / ref).exists():
                    resolved.append(str(img_dir / ref))
                else:
                    logger.warning(f"图片不存在: {ref}")
            post['image_paths'] = resolved
            # 从正文中移除图片标记
            post['content'] = re.sub(r'\[图片[：:].+?\]\s*', '', post['content'])
    if has_inline_images:
        return

    # 方式3：从 posts/images/ 目录按序号自动配对
    # 1.jpg/1.png → 第1篇, 2.jpg → 第2篇, ...
    if img_dir.exists():
        all_images = sorted([
            f for f in img_dir.iterdir()
            if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')
        ], key=lambda f: _natural_sort_key(f.name))

        if all_images:
            for i, post in enumerate(posts):
                if i < len(all_images):
                    post['image_paths'] = [str(all_images[i])]
                    logger.info(f"    📎 图片配对: {all_images[i].name} → 第{i+1}篇")


def _natural_sort_key(s: str) -> list:
    """自然排序 key，让 1.jpg < 2.jpg < 10.jpg（而不是 1.jpg < 10.jpg < 2.jpg）"""
    return [
        int(c) if c.isdigit() else c.lower()
        for c in re.split(r'(\d+)', s)
    ]


# ========== 主流程 ==========

def merge_and_renumber(file_paths: List[str]) -> str:
    """
    合并多组文案文件，重新编号1-N。
    每组文件内部是 1-9 编号，合并后按顺序重编为 1,2,3...N
    """
    all_text = []
    global_idx = 0
    for fp in file_paths:
        path = Path(fp)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            logger.warning(f"⚠️ 文件不存在: {fp}")
            continue
        text = path.read_text(encoding='utf-8')
        # 替换每组内的编号为全局编号
        # 匹配 "1. 标题："、"2. 标题：" 等
        def renumber(match):
            nonlocal global_idx
            global_idx += 1
            return f"{global_idx}. 标题{match.group(1)}"
        text = re.sub(r'\d+\.\s*标题([：:])', renumber, text)
        all_text.append(text)

    merged = '\n'.join(all_text)
    logger.info(f"📎 合并 {len(file_paths)} 个文件，共 {global_idx} 篇帖子")
    return merged


def run(
    text: str = None,
    file_path: str = None,
    file_paths: List[str] = None,
    image_dir: str = None,
    image_files: List[str] = None,
    topic: str = '我来爆个料',
    dry_run: bool = False,
    limit: int = 0,
):
    """粘贴发帖主流程"""
    logger.info("=" * 55)
    logger.info("📋 粘贴发帖模式")
    logger.info("=" * 55)

    # 获取文本内容
    if file_paths:
        # 多文件合并模式
        text = merge_and_renumber(file_paths)
        file_path = file_paths[0]  # 用于清理时定位
    elif file_path:
        text = Path(file_path).read_text(encoding='utf-8')
        logger.info(f"📄 从文件读取: {file_path}")
    elif text is None:
        # 交互式输入
        logger.info("📝 请粘贴内容（输入 END 结束）：")
        lines = []
        for line in sys.stdin:
            line = line.rstrip('\n')
            if line.strip() == 'END':
                break
            lines.append(line)
        text = '\n'.join(lines)

    if not text or not text.strip():
        logger.error("❌ 没有内容")
        return False

    # 解析帖子
    logger.info("📝 解析帖子内容...")
    posts = parse_posts(text)

    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    # 解析图片
    resolve_images(posts, image_dir, image_files)

    if limit > 0:
        posts = posts[:limit]

    # 预览
    logger.success(f"✓ 解析出 {len(posts)} 篇帖子")
    for i, post in enumerate(posts, 1):
        logger.info(f"\n  [{i}/{len(posts)}] 标题: {post['title'][:20]}")
        logger.info(f"    正文({len(post['content'])}字): {post['content'][:60]}...")
        logger.info(f"    话题: {post.get('topic', topic)}")
        logger.info(f"    图片: {len(post.get('image_paths', []))} 张")

    # 发布
    logger.info("🚀 开始发布到脉脉...")
    poster = MaimaiPoster()
    if not poster.connect():
        logger.error("❌ 连接 Chrome 失败")
        return False

    try:
        result = poster.batch_post(
            posts=[
                {
                    "content": p['content'][:1000],
                    "title": p.get('title', '')[:20],
                    "image_paths": p.get('image_paths', []),
                    "topic": p.get('topic', topic),
                }
                for p in posts
            ],
            interval=settings.maimai_post_interval,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.error(f"❌ 批量发帖异常: {e}")
        poster.disconnect()
        return False

    poster.disconnect()

    # 发帖全部成功后自动清理图片和文案（失败则保留，方便补发）
    if result['failed'] == 0:
        _cleanup_images()
        _cleanup_batch_images(file_path)
        # 清理主文案文件
        if file_path:
            content_path = Path(file_path)
            if not content_path.is_absolute():
                content_path = PROJECT_ROOT / content_path
            if content_path.exists():
                content_path.unlink()
                logger.info(f"🧹 已清理文案文件: {content_path.name}")
        # 清理多文件模式下的所有文案文件
        if file_paths:
            for fp in file_paths:
                p = Path(fp)
                if not p.is_absolute():
                    p = PROJECT_ROOT / p
                if p.exists():
                    p.unlink()
                    logger.info(f"🧹 已清理文案文件: {p.name}")
    else:
        logger.warning(f"⚠️ 有 {result['failed']} 篇失败，保留图片和文案以便补发")

    logger.info("=" * 55)
    logger.info(f"🏁 完成: 成功 {result['success']}, 失败 {result['failed']}")
    logger.info("=" * 55)

    return result['failed'] == 0


def _cleanup_images():
    """发帖成功后自动清理 posts/images/ 目录中的图片"""
    img_dir = PROJECT_ROOT / 'posts' / 'images'
    if not img_dir.exists():
        return
    images = [f for f in img_dir.iterdir()
              if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
    if images:
        for f in images:
            f.unlink()
        logger.info(f"🧹 已清理 images/ 下 {len(images)} 张图片")


def _cleanup_batch_images(file_path: str):
    """发帖成功后自动清理对应 batch 文件夹中的图片和文案"""
    if not file_path:
        return
    # 从文件路径推断 batch 目录（如 posts/batch3/content.txt → posts/batch3/）
    content_path = Path(file_path)
    if not content_path.is_absolute():
        content_path = PROJECT_ROOT / content_path
    if content_path.parent.name.startswith('batch') and content_path.parent.exists():
        batch_dir = content_path.parent
        # 清理图片
        images = [f for f in batch_dir.iterdir()
                  if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp')]
        if images:
            for f in images:
                f.unlink()
            logger.info(f"🧹 已清理 {batch_dir.name}/ 下 {len(images)} 张图片")
        # 清理文案文件
        if content_path.exists():
            content_path.unlink()
            logger.info(f"🧹 已清理 {batch_dir.name}/ 下文案文件")


# ========== 入口 ==========

if __name__ == "__main__":
    setup_logger()

    import argparse

    cli = argparse.ArgumentParser(description="粘贴发帖 — 复制粘贴内容，批量发到脉脉")
    cli.add_argument("--posts", type=str, help="直接传入帖子内容")
    cli.add_argument("--file", type=str, help="从文件读取帖子内容")
    cli.add_argument("--files", nargs='+', help="多文件合并发帖（自动重新编号1-N）")
    cli.add_argument("--images", nargs='*', help="图片文件路径（所有帖子共用）")
    cli.add_argument("--image-dir", type=str, help="图片目录（自动分配给各帖子）")
    cli.add_argument("--topic", type=str, default="我来爆个料", help="话题（默认：我来爆个料）")
    cli.add_argument("--dry-run", action="store_true", help="干跑模式")
    cli.add_argument("--limit", type=int, default=0, help="最多发几篇")

    args = cli.parse_args()

    if not args.posts and not args.file and not args.files:
        # 交互模式
        success = run(dry_run=args.dry_run, limit=args.limit, topic=args.topic)
    elif args.files:
        success = run(
            file_paths=args.files,
            image_dir=args.image_dir,
            image_files=args.images,
            dry_run=args.dry_run,
            limit=args.limit,
            topic=args.topic,
        )
    elif args.file:
        success = run(
            file_path=args.file,
            image_dir=args.image_dir,
            image_files=args.images,
            dry_run=args.dry_run,
            limit=args.limit,
            topic=args.topic,
        )
    else:
        success = run(
            text=args.posts,
            image_dir=args.image_dir,
            image_files=args.images,
            dry_run=args.dry_run,
            limit=args.limit,
            topic=args.topic,
        )

    sys.exit(0 if success else 1)
