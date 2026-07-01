"""
闪电观察者模式 — 粘贴 DeepSeek 输出，按话题批量发到脉脉

与爆料活动的区别：
  - 话题不固定，按运营给的热点名称搜索
  - 每个话题2篇文章，共享1张网络搜索配图
  - 间隔更短（1-2分钟）

用法：
  方式1: 从文件读取
    python3 shandian_post.py --file shandian.txt

  方式2: 交互式（推荐）
    python3 shandian_post.py
    然后粘贴 DeepSeek 输出，输入 END 结束

  方式3: 干跑预览
    python3 shandian_post.py --file shandian.txt --dry-run

输入格式（DeepSeek 输出直接复制）：
  ## 话题名称1

  **第一篇｜标题1**

  正文段落1

  正文段落2

  **第二篇｜标题2**

  正文段落1

  ## 话题名称2
  ...

前置条件：
  1. Chrome 带调试端口启动: python3 start_chrome.py
  2. 已登录脉脉
  3. .env 配置 PEXELS_API_KEY（搜图用，无key则不发图）
"""

import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger

from config import settings, PROJECT_ROOT
from publisher.maimai import MaimaiPoster
from adapter.image_search import search_and_download


# ========== 日志 ==========

def setup_logger():
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
    )


# ========== 解析 DeepSeek 输出 ==========

def parse_shandian(text: str) -> List[Dict]:
    """
    解析闪电观察者的 DeepSeek 输出

    格式：
      ## 话题名称
      **第一篇｜标题**
      正文...
      **第二篇｜标题**
      正文...

    返回：
      [{"title": "标题", "content": "正文", "topic": "话题名称", "image_paths": []}, ...]
      每个话题产生2篇文章

    支持两种格式：
      格式1：**第一篇｜标题**  正文
      格式2：### 【第一篇｜标题】  正文
    """
    posts = []

    # 1. 按 ## 话题标题拆分
    # 匹配 ## 开头的行作为话题分隔
    topic_blocks = re.split(r'\n(?=##\s+)', text.strip())

    for block in topic_blocks:
        block = block.strip()
        if not block:
            continue

        # 2. 提取话题名称（## 后面的第一行）
        topic_match = re.match(r'##\s+(.+)', block)
        if not topic_match:
            continue
        topic_name = topic_match.group(1).strip()

        # 去掉"话题X："前缀（如"话题一：大众汽车"→"大众汽车"）
        # 也去掉单纯的"话题："前缀（如"话题：WPS回应"→"WPS回应"）
        topic_name = re.sub(r'^话题[一二三四五六七八九十]*[：:]\s*', '', topic_name).strip()

        if not topic_name:
            continue

        # 去掉话题标题行，剩余为文章内容
        content_block = block[topic_match.end():].strip()

        # 3. 按文章标记拆分
        # 兼容两种格式：
        #   格式1：**第一篇｜标题**   → \*\*第[一二]篇[｜|]
        #   格式2：### 【第一篇｜标题】 → ###\s*[【\[]第[一二]篇[｜|]
        article_parts = re.split(
            r'(?:\*\*|###\s*[【\[])第[一二]篇[｜|]',
            content_block,
        )

        # article_parts[0] 是话题标题后的多余内容（通常为空），跳过
        for part in article_parts[1:]:  # 跳过第一段（话题名后面的内容）
            part = part.strip()
            if not part:
                continue

            # 4. 提取标题
            # 格式1：标题**  → title后跟**结束
            # 格式2：标题】** 或 标题】  → title后跟】和可能的**
            title_match = re.match(r'(.+?)(?:\*\*|[】\]])', part)
            if title_match:
                title = title_match.group(1).strip()
                # 去掉标题部分，剩余为正文
                body = part[title_match.end():].strip()
            else:
                # 没有标题标记，取第一行做标题
                lines = part.split('\n', 1)
                title = lines[0].strip()
                body = lines[1].strip() if len(lines) > 1 else ''

            # 5. 清理正文：去除所有 ** ### 【】 符号，保留分段
            title = re.sub(r'[\*\#【】\[\]]', '', title).strip()
            body = re.sub(r'[\*\#]', '', body).strip()

            if not body:
                body = title  # 兜底：用标题当正文

            posts.append({
                'title': title[:20],
                'content': body[:1000],
                'topic': topic_name,
                'image_paths': [],
            })

    return posts


# ========== 图片搜索与配对 ==========

def search_topic_images(posts: List[Dict], skip_image: bool = False, pexels_only: bool = False) -> Dict[str, List[str]]:
    """
    为每个话题搜索1张配图，同话题的2篇文章共享

    返回:
        {话题名称: [图片路径]} 的映射
    """
    if skip_image:
        logger.info("⏭️ 跳过图片搜索（--no-image）")
        return {}

    # 收集去重后的话题列表（保持顺序）
    unique_topics = list(dict.fromkeys(p['topic'] for p in posts))

    if not unique_topics:
        return {}

    img_dir = str(PROJECT_ROOT / 'posts' / 'shandian_images')
    Path(img_dir).mkdir(parents=True, exist_ok=True)

    # 检查已有图片，跳过已下载的话题
    existing = {}
    if Path(img_dir).exists():
        for f in Path(img_dir).iterdir():
            if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
                # 文件名还原话题名（_替换回原字符不好还原，用映射表）
                existing[f.stem] = str(f)

    # 中文→英文关键词映射（Pexels搜图用英文效果更好）
    pexels_queries = {
        '梅西登顶世界杯历史射手王': 'Messi World Cup goal celebration',
        '你的世界杯入坑之战是哪一场': 'World Cup football fans stadium',
        '马斯克身家超过140个国家GDP': 'Elon Musk SpaceX rocket launch',
    }

    logger.info(f"🔍 搜索图片: 共 {len(unique_topics)} 个话题...")

    topic_images = {}
    for i, topic in enumerate(unique_topics, 1):
        # 检查是否已有图片
        safe_name = re.sub(r'[^\w一-鿿]', '_', topic)[:30]
        if safe_name in existing:
            logger.info(f"  [{i}/{len(unique_topics)}] 已有图片: {topic} → 复用")
            topic_images[topic] = [existing[safe_name]]
            continue

        logger.info(f"  [{i}/{len(unique_topics)}] 搜索: {topic}")
        pexels_q = pexels_queries.get(topic)  # 英文关键词
        img_path = search_and_download(
            topic, img_dir,
            skip_web=pexels_only,
            pexels_query=pexels_q,
        )
        topic_images[topic] = [img_path] if img_path else []
        time.sleep(0.5)  # 避免请求过快

    return topic_images


# ========== 主流程 ==========

def run(
    text: str = None,
    file_path: str = None,
    dry_run: bool = False,
    limit: int = 10,
    skip_image: bool = False,
    pexels_only: bool = False,
):
    """闪电观察者主流程"""
    logger.info("=" * 55)
    logger.info("⚡ 闪电观察者模式")
    logger.info("=" * 55)

    # 获取文本内容
    if file_path:
        text = Path(file_path).read_text(encoding='utf-8')
        logger.info(f"📄 从文件读取: {file_path}")
    elif text is None:
        # 交互式输入
        logger.info("📝 请粘贴 DeepSeek 输出（输入 END 结束）：")
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
    posts = parse_shandian(text)

    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    # 限制话题数量
    unique_topics = list(dict.fromkeys(p['topic'] for p in posts))
    if len(unique_topics) > limit:
        allowed_topics = unique_topics[:limit]
        posts = [p for p in posts if p['topic'] in allowed_topics]
        logger.warning(f"⚠️ 话题数超过限制，只保留前 {limit} 个")

    # 搜索图片
    topic_images = search_topic_images(posts, skip_image, pexels_only)

    # 配对图片
    for post in posts:
        post['image_paths'] = topic_images.get(post['topic'], [])

    # 预览
    logger.success(f"✓ 解析出 {len(unique_topics)} 个话题，{len(posts)} 篇文章")
    for i, post in enumerate(posts, 1):
        logger.info(f"\n  [{i}/{len(posts)}] 标题: {post['title'][:20]}")
        logger.info(f"    话题: {post['topic']}")
        logger.info(f"    正文({len(post['content'])}字): {post['content'][:60]}...")
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
                    "title": "",  # 闪电观察者不填标题，直接发正文
                    "image_paths": p.get('image_paths', []),
                    "topic": p.get('topic', ''),
                }
                for p in posts
            ],
            interval=settings.shandian_post_interval,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.error(f"❌ 批量发帖异常: {e}")
        poster.disconnect()
        return False

    poster.disconnect()

    logger.info("=" * 55)
    logger.info(f"🏁 完成: 成功 {result['success']}, 失败 {result['failed']}")
    logger.info("=" * 55)

    return result['failed'] == 0


# ========== 入口 ==========

if __name__ == "__main__":
    setup_logger()

    import argparse

    cli = argparse.ArgumentParser(description="闪电观察者 — 粘贴DeepSeek输出，按话题批量发到脉脉")
    cli.add_argument("--posts", type=str, help="直接传入帖子内容")
    cli.add_argument("--file", type=str, help="从文件读取帖子内容")
    cli.add_argument("--dry-run", action="store_true", help="干跑模式")
    cli.add_argument("--limit", type=int, default=10, help="最多处理几个话题（默认10）")
    cli.add_argument("--no-image", action="store_true", help="跳过图片搜索")
    cli.add_argument("--pexels-only", action="store_true", help="只用Pexels搜图（跳过网页搜图，避免Playwright冲突）")

    args = cli.parse_args()

    if not args.posts and not args.file:
        # 交互模式
        success = run(dry_run=args.dry_run, limit=args.limit, skip_image=args.no_image, pexels_only=args.pexels_only)
    elif args.file:
        success = run(
            file_path=args.file,
            dry_run=args.dry_run,
            limit=args.limit,
            skip_image=args.no_image,
            pexels_only=args.pexels_only,
        )
    else:
        success = run(
            text=args.posts,
            dry_run=args.dry_run,
            limit=args.limit,
            skip_image=args.no_image,
            pexels_only=args.pexels_only,
        )

    sys.exit(0 if success else 1)
