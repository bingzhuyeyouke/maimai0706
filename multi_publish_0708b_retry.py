"""
多平台发布脚本 - 2026/07/08 批次2 重发
跳过第1篇（已成功），重发第2-16篇共15篇
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from loguru import logger
from config import settings, PROJECT_ROOT
from publisher.multipost import MultiPostPublisher

# ========== 日志 ==========
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>",
)

# ========== 图片目录 ==========
IMG_DIR = str(PROJECT_ROOT / "posts" / "multi_0708b_images")

TOPIC_IMAGES = {
    1: f"{IMG_DIR}/topic1_founder_dispute.jpg",
    2: f"{IMG_DIR}/topic2_xiaohongshu_ipo.jpg",
    3: f"{IMG_DIR}/topic3_referee_var.jpg",
    4: f"{IMG_DIR}/topic4_semiconductor_stock.jpg",
    5: f"{IMG_DIR}/topic5_skhynix_ipo.jpg",
    6: f"{IMG_DIR}/topic6_xiaomi_suv.jpg",
    7: f"{IMG_DIR}/topic7_humanoid_robot.jpg",
    8: f"{IMG_DIR}/topic8_weekend_rest.jpg",
}

TOPIC_TAGS = {
    1: "前华为天才少年被投资人指没契约精神",
    2: "小红书前高管向港交所实名举报",
    3: "阿根廷逆转争议，比赛焦点在哪？",
    4: "半导体行情反复，持仓人怎么操作？",
    5: "SK海力士上市会带来哪些影响？",
    6: "小米发布增程车，家用车怎么选？",
    7: "机器人公司你最看好哪家？",
    8: "工作日被掏空，周末怎么回血？",
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
    posts_file = str(PROJECT_ROOT / "posts" / "multi_0708b_posts.txt")
    platforms = ["脉脉", "微信公众号", "今日头条"]

    # 解析全部帖子，跳过第1篇（已成功）
    all_posts = parse_posts_file(posts_file)
    posts = all_posts[1:]  # 跳过第1篇

    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    logger.info("=" * 60)
    logger.info("📋 多平台发布 - 2026/07/08 批次2 重发（第2-16篇）")
    logger.info(f"   共 {len(posts)} 篇帖子，3平台（脉脉+公众号+头条）")
    logger.info(f"   每篇带1张Pexels配图")
    logger.info(f"   间隔 {settings.multipost_post_interval} 秒（±30秒抖动）")
    logger.info("=" * 60)

    for i, p in enumerate(posts, 2):
        logger.info(f"  [{i:2d}] title({len(p['title'])}c) body({len(p['body'])}c) | 话题: {p['topic']}")
        logger.info(f"       图片: {Path(p['image_paths'][0]).name if p['image_paths'] else '无'}")

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
