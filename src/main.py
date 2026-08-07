"""主程序入口：拉取 RAWG 数据并生成 ICS 文件。

职责：
    1. 读取环境变量中的 RAWG_API_KEY；
    2. 根据当前年份从 RAWG 拉取所有游戏发售信息；
    3. 写入 ``/data/{year}/game.ics``；
    4. 把当年事件与历史年份归档合并，写入 ``/data/game.ics`` 供订阅使用。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# 兼容两种运行方式：
#   1. python -m src.main    —— 此时 src 是 package，优先使用相对导入；
#   2. python src/main.py    —— 或 IDE（如 PyCharm）直接运行，src 不在 package
#      上下文里，需走绝对导入并依赖脚本所在路径。
try:
    from .ics_generator import ICSBuilder, GameEvent
    from .rawg_client import RawgClient
except ImportError:  # pragma: no cover - 仅调试 / IDE 直接运行场景
    from ics_generator import ICSBuilder, GameEvent  # type: ignore[no-redef]
    from rawg_client import RawgClient  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
ENV_API_KEY = "RAWG_API_KEY"


def configure_logging() -> None:
    """统一日志格式，方便在 GitHub Actions 中查看。"""
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def resolve_api_key() -> str:
    api_key = os.environ.get(ENV_API_KEY) or os.environ.get(ENV_API_KEY.lower())
    if not api_key:
        logger.error("未找到环境变量 %s，请先在 GitHub Secrets 或本地 env 中配置。", ENV_API_KEY)
        sys.exit(1)
    return api_key


def fetch_year_events(api_key: str, year: int) -> List[GameEvent]:
    """从 RAWG 拉取指定年份的所有游戏，并转换为 GameEvent 列表。"""
    client = RawgClient(api_key=api_key)
    events: List[GameEvent] = []
    for game in client.iter_games_for_year(year):
        event = GameEvent.from_rawg(game)
        if event is not None:
            events.append(event)
    logger.info("年份 %d 解析出 %d 个有效游戏事件", year, len(events))
    return events


def write_year_file(data_dir: Path, year: int, events: List[GameEvent]) -> Path:
    """将某年的事件写入 ``/data/{year}/game.ics``，返回文件路径。"""
    year_dir = data_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    builder = ICSBuilder()
    builder.add_events(events)
    target = year_dir / "game.ics"
    target.write_text(builder.to_ics(), encoding="utf-8")
    logger.info("已写入年份归档：%s（%d 条事件）", target, builder.event_count())
    return target


def aggregate_to_main_ics(
    data_dir: Path,
    current_year: int,
    current_events: List[GameEvent],
) -> Path:
    """合并所有历史年份归档与当前年份事件，生成统一的 ``/data/game.ics``。

    为了保留完整的 description 元数据（即 RAWG 给出的描述、平台等），当前年份
    事件直接复用主流程已加载的对象；只有历史年份归档走 ICS 文本解析（按本项目
    固定格式，仅恢复核心字段）。
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "game.ics"

    builder = ICSBuilder()
    # 1. 先加入历史年份（不含 current_year）
    historical_total = 0
    for year_file in sorted(data_dir.glob("*/game.ics")):
        if not year_file.is_file() or year_file == target:
            continue
        if year_file.parent.name == str(current_year):
            continue  # 当年归档不再重复解析，主流程已持有完整事件
        events, count = read_events_from_ics(year_file)
        builder.add_events(events)
        historical_total += count
        logger.info("聚合年份归档 %s：%d 条事件", year_file, count)

    # 2. 加入当年事件（含最新 RAWG 元数据）
    builder.add_events(current_events)

    target.write_text(builder.to_ics(), encoding="utf-8")
    logger.info(
        "已写入聚合文件：%s（历史 %d 条 + 当年 %d 条）",
        target,
        historical_total,
        len(current_events),
    )
    return target


def read_events_from_ics(path: Path) -> tuple[List[GameEvent], int]:
    """从我们自己生成的 ICS 文件中解析出 GameEvent 列表。

    这里采用简化处理：仅识别本项目 ``ICSBuilder`` 输出的字段，保证 format 演化
    时仍能聚合。如果未来调整了生成器，请同步修改这里的解析规则。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("无法读取 %s：%s", path, exc)
        return [], 0

    events: List[GameEvent] = []
    vevent_count = 0
    blocks = content.split("BEGIN:VEVENT")
    for block in blocks[1:]:  # 第一段是 VCALENDAR 头，不含 VEVENT
        vevent_end = block.find("END:VEVENT")
        if vevent_end == -1:
            continue
        vevent_body = block[:vevent_end]
        vevent_count += 1

        uid_raw = _extract_field(vevent_body, "UID")
        summary_raw = _extract_field(vevent_body, "SUMMARY")
        dtstart_raw = _extract_field(vevent_body, "DTSTART")
        url_raw = _extract_field(vevent_body, "URL")
        categories_raw = _extract_field(vevent_body, "CATEGORIES")

        try:
            released = datetime.strptime(dtstart_raw, "%Y%m%d").date()
        except (ValueError, TypeError):
            continue
        if not uid_raw or not summary_raw:
            continue

        slug = None
        url_value = _unescape_field(url_raw)
        if url_value and "rawg.io/games/" in url_value:
            slug = url_value.split("rawg.io/games/", 1)[1].split("/")[0] or None

        genres = [g.strip() for g in _unescape_field(categories_raw).split(",") if g.strip()]
        events.append(
            GameEvent(
                uid=uid_raw.rsplit("@", 1)[0],
                name=_unescape_field(summary_raw),
                released=released,
                slug=slug,
                description=None,
                platforms=[],
                genres=genres,
                website=url_value if url_value and url_value != "https://rawg.io/" else None,
            )
        )
    return events, vevent_count


def _extract_field(body: str, field_name: str) -> str:
    """从 VEVENT body 提取 ``FIELD:value`` 或 ``FIELD;PARAM=...:value`` 形式的值。"""
    for line in body.splitlines():
        head, sep, value = line.partition(":")
        if not sep:
            continue
        head_name = head.split(";", 1)[0]
        if head_name == field_name:
            return value
    return ""


def _unescape_field(value: str) -> str:
    """反向执行 ICS 的转义，得到原始显示文本。"""
    return (
        value.replace("\\n", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def main() -> int:
    """主入口。返回 0 表示成功，非 0 表示异常（GitHub Actions 会判定为失败）。"""
    # 本地开发场景下从 .env 加载环境变量，方便测试。GitHub Actions 已通过
    # secrets 直接注入同名变量，此调用不会覆盖已设置的值。
    load_dotenv(override=False)

    configure_logging()
    api_key = resolve_api_key()
    year = datetime.now().year
    data_dir = Path(os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)))
    logger.info("开始生成 %d 年的游戏发售日历，数据目录：%s", year, data_dir.resolve())

    # 1. 拉取当前年份数据
    events = fetch_year_events(api_key, year)

    # 2. 写入年份归档
    write_year_file(data_dir, year, events)

    # 3. 聚合所有年份归档到 /data/game.ics（当年事件直接复用，避免丢失元数据）
    aggregate_to_main_ics(data_dir, year, events)

    logger.info("全部任务完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
