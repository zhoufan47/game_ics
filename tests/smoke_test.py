"""烟雾测试：使用 sample 数据验证 ICS 生成器与解析器的往返一致性。"""

import sys
from datetime import date
from pathlib import Path

# 通过 src 包路径导入，避免 main.py 中的相对导入报错
sys.path.insert(0, ".")

from src.ics_generator import ICSBuilder, GameEvent  # noqa: E402
from src.main import read_events_from_ics  # noqa: E402


def make_sample_rawg() -> list[dict]:
    """构造一份模拟的 RAWG 返回结构，覆盖中文/特殊字符等情形。"""
    return [
        {
            "id": 12345,
            "slug": "sample-game",
            "name": 'Hollow "Knight": Silksong',  # 含双引号、冒号
            "released": "2026-08-01",
            "description": "一款动作冒险游戏，介绍内容：续作。\\n支持多平台。",
            "platforms": [
                {"platform": {"name": "PC"}},
                {"platform": {"name": "Switch"}},
            ],
            "genres": [{"name": "Action"}, {"name": "Adventure"}],
            "metacritic": 90,
            "website": "https://example.com/silksong",
        },
        {
            "id": 67890,
            "slug": "chinese-game-中文",
            "name": "黑神话：悟空",  # 含中文与中文标点
            "released": "2026-09-15",
            "description": "国产动作 RPG。",
            "platforms": [{"platform": {"name": "PS5"}}],
            "genres": [{"name": "RPG"}],
            "metacritic": None,
            "website": None,
        },
    ]


def main() -> None:
    rawg_data = make_sample_rawg()
    builder = ICSBuilder()
    for game in rawg_data:
        event = GameEvent.from_rawg(game)
        assert event is not None, "事件不应为 None"
        builder.add_event(event)

    ics_text = builder.to_ics()
    assert "BEGIN:VCALENDAR" in ics_text
    assert "END:VCALENDAR" in ics_text
    assert "黑神话" in ics_text
    assert "DTSTART;VALUE=DATE:20260801" in ics_text
    assert "DTEND;VALUE=DATE:20260802" in ics_text
    print(f"ICS 文件大小: {len(ics_text)} 字符")

    target = Path("/tmp/test_sample.ics")
    target.write_text(ics_text, encoding="utf-8")

    events, count = read_events_from_ics(target)
    assert count == len(rawg_data), f"事件数匹配失败：{count} vs {len(rawg_data)}"
    by_uid = {e.uid: e for e in events}
    print(f"读回事件数：{count}")
    for e in events:
        print(f"  - {e.released} | {e.uid} | {e.name} | genres={e.genres}")

    assert "rawg-12345" in by_uid
    assert "rawg-67890" in by_uid
    assert by_uid["rawg-12345"].released == date(2026, 8, 1)
    assert by_uid["rawg-67890"].released == date(2026, 9, 15)
    assert by_uid["rawg-12345"].website == "https://example.com/silksong"
    assert by_uid["rawg-67890"].slug == "chinese-game-中文"

    print("\n所有断言通过 ✔")


if __name__ == "__main__":
    main()
