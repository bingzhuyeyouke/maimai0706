"""
多平台发布脚本 - 2026/07/08 批次3
9个话题17篇帖子，脉脉+公众号+头条三平台
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

IMG_DIR = str(PROJECT_ROOT / "posts" / "multi_0708c_images")

TOPIC_IMAGES = {
    1: f"{IMG_DIR}/topic1_skillweaver.jpg",
    2: f"{IMG_DIR}/topic2_icml_algorithm.jpg",
    3: f"{IMG_DIR}/topic3_openai_chip.jpg",
    4: f"{IMG_DIR}/topic4_microsoft_mai.jpg",
    5: f"{IMG_DIR}/topic5_shanghai_housing.jpg",
    6: f"{IMG_DIR}/topic6_wedding_cost.jpg",
    7: f"{IMG_DIR}/topic7_ai_frontend.jpg",
    8: f"{IMG_DIR}/topic8_aigc_backend.jpg",
    9: f"{IMG_DIR}/topic9_ai_testing.jpg",
}

TOPIC_TAGS = {
    1: "阿里发布SkillWeaver能降多少token？",
    2: "ICML 新算法落地业务上手难度高吗？",
    3: "OpenAI 自研芯片能彻底脱离 GPU 吗？",
    4: "微软自研MAI模型能省下多少开销？",
    5: "裁员潮下，上海房价靠什么支撑？",
    6: "工资不高，结婚到底花多少钱？",
    7: "懂AI赋能的前端，跳槽薪资能溢价多少",
    8: "后端深耕AIGC业务，能否打破薪资内卷",
    9: "AI替代基础岗，测试的薪资会全面降级吗",
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
    posts_file = str(PROJECT_ROOT / "posts" / "multi_0708c_posts.txt")
    platforms = ["脉脉", "微信公众号", "今日头条"]

    logger.info("=" * 60)
    logger.info("📋 多平台发布 - 2026/07/08 批次3")

    posts = parse_posts_file(posts_file)
    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    logger.info(f"   共 {len(posts)} 篇帖子，3平台（脉脉+公众号+头条）")
    logger.info(f"   每篇带1张Pexels配图")
    logger.info(f"   间隔 {settings.multipost_post_interval} 秒（±30秒抖动）")
    logger.info("=" * 60)

    for i, p in enumerate(posts, 1):
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
