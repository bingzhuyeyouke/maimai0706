"""
多平台发布脚本 - 2026/07/08 批次
8个话题16篇帖子，脉脉+公众号+头条三平台
带图发布（每话题1张Pexels配图）

从 posts/multi_0708_posts.txt 读取帖子内容，避免Python引号冲突
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
IMG_DIR = str(PROJECT_ROOT / "posts" / "multi_0708_images")

# 话题编号 → 图片文件名映射
TOPIC_IMAGES = {
    1: f"{IMG_DIR}/topic1_deepseek_chip.jpg",
    2: f"{IMG_DIR}/topic2_momenta_ipo.jpg",
    3: f"{IMG_DIR}/topic3_autohome_layoff.jpg",
    4: f"{IMG_DIR}/topic4_xiaohongshu_tech.jpg",
    5: f"{IMG_DIR}/topic5_xiaohongshu_culture.jpg",
    6: f"{IMG_DIR}/topic6_messi_worldcup.jpg",
    7: f"{IMG_DIR}/topic7_worldcup_quarter.jpg",
    8: f"{IMG_DIR}/topic8_referee_controversy.jpg",
}

# 话题编号 → 脉脉话题词
# ⚠️ 话题名中的引号必须用中文引号 “ ”，不能用英文引号
# Write 工具会将中文引号转成英文引号，所以话题名必须从脚本硬编码读取
TOPIC_TAGS = {
    1: "传DeepSeek启动造芯，专攻推理任务",
    2: "Momenta上市，总市值超700亿港元",
    3: "传汽车之家开始裁员，总裁王秋凤离职",
    4: "原快手研发线负责人陈彬加入小红书",
    5: "《置身薯内》来了，如何评价？",
    6: "如何评价阿根廷3:2绝境翻盘埃及",
    7: "世界杯8强名单来了，你最期待哪组对决？",
    8: "裁判黑哨？阿根廷埃及判罚引发争议",
}


def parse_posts_file(filepath: str) -> list:
    """
    解析帖子文件，格式：
      ========== 话题N：xxx ==========
      ## 话题标签
      BODY:
      正文段落1

      正文段落2
      ---
      BODY:
      正文...

    返回: [{"title", "body", "topic", "image_paths"}, ...]
    ⚠️ 标题和正文必须完整保留用户原文，不能截断、改写、缩写
    """
    text = Path(filepath).read_text(encoding="utf-8")

    # 按 ========== 分隔符拆分话题段落
    topic_sections = re.split(r'={5,}', text)

    posts = []
    topic_num = 0

    for section in topic_sections:
        section = section.strip()
        if not section:
            continue

        # 检测话题标题行，提取话题编号
        header_match = re.match(r'话题([一二三四五六七八九十\d]+)[：:]', section)
        if header_match:
            cn_nums = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                       '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
            num_str = header_match.group(1)
            topic_num = cn_nums.get(num_str, int(num_str) if num_str.isdigit() else 0)

        if topic_num == 0:
            continue

        # ⚠️ 话题名统一用 TOPIC_TAGS（中文引号），不从文件读取
        topic_tag = TOPIC_TAGS.get(topic_num, "")

        # 去掉 ## 行和 ========== 行，避免干扰 TITLE: 匹配
        section = re.sub(r'^={3,}.*$', '', section, flags=re.MULTILINE).strip()
        section = re.sub(r'^##\s+.+$', '', section, count=1, flags=re.MULTILINE).strip()

        # 按 --- 分隔符拆分帖子
        sub_posts = re.split(r'\n---\n', section)

        for sub in sub_posts:
            sub = sub.strip()
            if not sub:
                continue

            # 匹配 BODY: 标记
            body_match = re.search(r'^BODY:\s*\n(.+)', sub, re.DOTALL)

            if not body_match:
                continue

            body = body_match.group(1).strip()
            title = topic_tag  # ⚠️ title就是话题名（topic）

            if not title or not body:
                continue

            # ⚠️ 标题和正文完整保留，不截断不改写
            posts.append({
                "title": title,
                "body": body,
                "topic": topic_tag,
                "image_paths": [TOPIC_IMAGES.get(topic_num, "")],
            })

    return posts


# ========== 发布 ==========
def main():
    posts_file = str(PROJECT_ROOT / "posts" / "multi_0708_posts.txt")
    platforms = ["脉脉", "微信公众号", "今日头条"]

    logger.info("=" * 60)
    logger.info("📋 多平台发布 - 2026/07/08 批次")

    # 解析帖子
    posts = parse_posts_file(posts_file)
    if not posts:
        logger.error("❌ 没有解析出任何帖子")
        return False

    logger.info(f"   共 {len(posts)} 篇帖子，3平台（脉脉+公众号+头条）")
    logger.info(f"   每篇带1张Pexels配图")
    logger.info(f"   间隔 {settings.multipost_post_interval} 秒（±30秒抖动）")
    logger.info("=" * 60)

    # 预览
    for i, p in enumerate(posts, 1):
        logger.info(f"  [{i:2d}] title({len(p['title'])}c) body({len(p['body'])}c) | 话题: {p['topic']}")
        logger.info(f"       图片: {Path(p['image_paths'][0]).name if p['image_paths'] else '无'}")

    # 连接Chrome
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
