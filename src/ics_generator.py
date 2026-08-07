"""ICS 日历文件生成器。

依据 RFC 5545 手工生成 VCALENDAR 内容，避免第三方 ICS 库对中文/特殊字符
的转义不一致问题。所有日期采用全天事件（VALUE=DATE）形式，适合游戏发售
提醒场景。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

ICAL_LINE_SEPARATOR = "\r\n"
UID_DOMAIN = "game-ics.local"
MAX_LINE_BYTES = 75  # RFC 5545 推荐的行长度上限


@dataclass
class GameEvent:
    """单个游戏发售事件的轻量数据结构。"""

    uid: str
    name: str
    released: date
    slug: Optional[str] = None
    description: Optional[str] = None
    platforms: List[str] = field(default_factory=list)
    genres: List[str] = field(default_factory=list)
    metacritic: Optional[int] = None
    website: Optional[str] = None

    @classmethod
    def from_rawg(cls, game: Dict[str, Any]) -> Optional["GameEvent"]:
        """从 RAWG 返回的字典构造事件，无法解析发售日期时返回 None。"""
        released_at = game.get("released")
        if not released_at:
            return None
        try:
            released_date = datetime.strptime(released_at, "%Y-%m-%d").date()
        except ValueError:
            logger.debug("跳过无法解析发售日期的游戏：%s", game.get("name"))
            return None

        platforms = [
            p["platform"]["name"] for p in game.get("platforms", []) or [] if p.get("platform")
        ]
        genres = [g["name"] for g in game.get("genres", []) or [] if g.get("name")]

        return cls(
            uid=f"rawg-{game.get('id')}",
            name=game.get("name") or "Unknown",
            released=released_date,
            slug=game.get("slug"),
            description=(game.get("description") or "").strip() or None,
            platforms=platforms,
            genres=genres,
            metacritic=game.get("metacritic"),
            website=game.get("website") or None,
        )


class ICSBuilder:
    """ICS 日历生成器，构造可被 Apple 日历订阅的 VCALENDAR 文档。"""

    def __init__(
        self,
        calendar_name: str = "Game Releases Calendar",
        calendar_desc: str = "游戏发售日历，数据来源 RAWG.io",
        prod_id: str = "-//game_ics//Game Releases Calendar//ZH",
    ) -> None:
        self.calendar_name = calendar_name
        self.calendar_desc = calendar_desc
        self.prod_id = prod_id
        self._events: List[GameEvent] = []

    def add_event(self, event: GameEvent) -> None:
        """添加一个游戏事件；按游戏 UID 去重以保证同一游戏不会重复出现。"""
        if any(existing.uid == event.uid for existing in self._events):
            return
        self._events.append(event)

    def add_events(self, events: Iterable[GameEvent]) -> None:
        for event in events:
            self.add_event(event)

    def clear(self) -> None:
        self._events.clear()

    def event_count(self) -> int:
        return len(self._events)

    def to_ics(self) -> str:
        """生成完整的 VCALENDAR 字符串。"""
        lines: List[str] = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            f"PRODID:{self.prod_id}",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            f"X-WR-CALNAME:{self._escape(self.calendar_name)}",
            f"X-WR-CALDESC:{self._escape(self.calendar_desc)}",
            "X-WR-TIMEZONE:UTC",
        ]

        # 按发售日期排序，让日历显示更直观
        self._events.sort(key=lambda e: (e.released, e.name))

        for event in self._events:
            lines.extend(self._render_event(event))

        lines.append("END:VCALENDAR")
        return ICAL_LINE_SEPARATOR.join(lines) + ICAL_LINE_SEPARATOR

    @staticmethod
    def _escape(text: Optional[str]) -> str:
        """对 ICS 文本字段进行必要的转义。"""
        if text is None:
            return ""
        return (
            text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace("\r", "\\n")
        )

    def _render_event(self, event: GameEvent) -> List[str]:
        """渲染单个 VEVENT，返回未做 line folding 的字段行。"""
        dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        # 使用全天事件（VALUE=DATE）形式。根据 RFC 5545，DTEND 是排他的，
        # 因此写成次日以表示“这一天”这一整天（跨午夜），这是 Apple/Google
        # 日历都支持的明确写法。
        dtstart = event.released.strftime("%Y%m%d")
        dtend = (event.released + timedelta(days=1)).strftime("%Y%m%d")

        description_parts: List[str] = []
        if event.platforms:
            description_parts.append("平台：" + "、".join(event.platforms))
        if event.genres:
            description_parts.append("类型：" + "、".join(event.genres))
        if event.metacritic is not None:
            description_parts.append(f"Metacritic 评分：{event.metacritic}")
        if event.description:
            description_parts.append("")  # 空行分隔
            description_parts.append(event.description)
        description = "\\n".join(description_parts) if description_parts else ""

        url = event.website or (
            f"https://rawg.io/games/{event.slug}" if event.slug else "https://rawg.io/"
        )

        lines: List[str] = [
            "BEGIN:VEVENT",
            f"UID:{event.uid}@{UID_DOMAIN}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{dtstart}",
            f"DTEND;VALUE=DATE:{dtend}",
            f"SUMMARY:{self._escape(event.name)}",
            f"DESCRIPTION:{self._escape(description)}",
            f"URL:{self._escape(url)}",
            "TRANSP:TRANSPARENT",
        ]
        if event.genres:
            lines.append(f"CATEGORIES:{self._escape(','.join(event.genres))}")
        lines.append("END:VEVENT")
        return lines


def stable_event_uid(game: Dict[str, Any]) -> str:
    """为没有稳定 ID 的 RAWG 数据生成稳定的 UID。"""
    raw = f"{game.get('id')}-{game.get('slug')}-{game.get('released')}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"rawg-{digest}"
