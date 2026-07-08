"""
多平台发布脚本 - 2026/07/08 批次4
8个话题16篇帖子，脉脉+公众号+头条三平台
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from config import settings, PROJECT_ROOT
from publisher.multipost import MultiPostPublisher

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
)

IMG_DIR = str(PROJECT_ROOT / "posts" / "multi_0708d_images")

TOPIC_IMAGES = {
    1: f"{IMG_DIR}/topic1_xiaomi_skynomad.jpg",
    2: f"{IMG_DIR}/topic2_huawei_token.jpg",
    3: f"{IMG_DIR}/topic3_ant_health.jpg",
    4: f"{IMG_DIR}/topic4_xiaohongshu_report.jpg",
    5: f"{IMG_DIR}/topic5_fifa_referee.jpg",
    6: f"{IMG_DIR}/topic6_worldcup_upset.jpg",
    7: f"{IMG_DIR}/topic7_tencent_vlm.jpg",
    8: f"{IMG_DIR}/topic8_security_privacy.jpg",
}

TOPIC_TAGS = {
    1: "雷军回应SkyNomad上市时间",
    2: "华为高管：国内Token流通效率仅60%",
    3: "蚂蚁集团入股薄荷健康，成最大外部股东",
    4: "前员工回应举报小红书：为求公道非物质赔偿",
    5: "埃及足协正式申诉 要求调查主裁判",
    6: "世界杯哪场冷门颠覆你的认知？",
    7: "原OpenAI研究员田永龙入职腾讯",
    8: "工信部：ClaudeCode存在安全隐患",
}


def parse_posts_file(filepath: str) -> list:
    text = Path(filepath).read_text(encoding="utf-8")
    topic_sections = re.split(r'={5,}', text)
    posts = []
    topic_num = 0

    for section in topic_sections:
        section = section.strip()
        if not section:
            continue

        header_match = re.match(r'话题([一二三四五六七八九十\d]+)[：:]', section)
        if header_match:
            cn_nums = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                       '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            num_str = header_match.group(1)
            topic_num = cn_nums.get(num_str, int(num_str) if num_str.isdigit() else 0)

        if topic_num == 0:
            continue

        topic_tag = TOPIC_TAGS.get(topic_num, "")
        section = re.sub(r'^={3,}.*$', '', section, flags=re.MULTILINE).strip()
        section = re.sub(r'^##\s+.+$', '', section, count=1, flags=re.MULTILINE).strip()
        sub_posts = re.split(r'\n---\n', section)

        for sub in sub_posts:
            sub = sub.strip()
            if not sub:
                continue
            body_match = re.search(r'^BODY:\s*\n(.+)', sub, re.DOTALL)
            if not body_match:
                continue
            body = body_match.group(1).strip()
            title = topic_tag
            if not title or not body:
                continue
            posts.append({
                "title": title,
                "body": body,
                "topic": topic_tag,
                "image_paths": [TOPIC_IMAGES.get(topic_num, "")],
            })

    return posts


def main():
    posts_file = str(PROJECT_ROOT / "posts" / "multi_0708d_posts.txt")
    platforms = ["脉脉", "微信公众号", "今日头条"]

    logger.info("=" * 60)
    logger.info("📋 多平台发布 - 2026/07/08 批次4")

    posts = parse_posts_file(posts_file)
    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    logger.info(f"   共 {len(posts)} 篇帖子，3平台（脉脉+公众号+头条）")
    logger.info(f"   间隔 {settings.multipost_post_interval} 秒（±30秒抖动）")
    logger.info("=" * 60)

    for i, p in enumerate(posts, 1):
        logger.info(f"  [{i:2d}] title({len(p['title'])}c) body({len(p['body'])}c) | {p['topic']}")
        logger.info(f"       img: {Path(p['image_paths'][0]).name if p['image_paths'] else '无'}")

    publisher = MultiPostPublisher()
    if not publisher.connect():
        logger.error("❌ 连接 Chrome 失败")
        return False

    try:
        result = publisher.batch_post(
            posts=posts,
            platforms=platforms,
            interval=settings.multipost_post_interval,
            dry_run=False,
            cleanup_images=True,
        )

        logger.info("\n" + "=" * 60)
        logger.info(f"🏁 发布完成: 成功 {result['success']}, 失败 {result['failed']}")
        logger.info("=" * 60)
        return result['failed'] == 0

    except Exception as e:
        logger.error(f"❌ 发布异常: {e}")
        return False
    finally:
        publisher.disconnect()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
