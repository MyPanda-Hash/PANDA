"""把已经拆散的同主题待办并回去。

用法：
    .venv/bin/python scripts/merge_duplicate_todos.py           # 只看会并什么
    .venv/bin/python scripts/merge_duplicate_todos.py --apply   # 真的并

不改删数据：被并掉的那条只是置为 dismissed，正文全部搬进保留那条的明细。
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402

from app.core.database import SessionLocal, initialize_database  # noqa: E402
from app.models.auth import User  # noqa: E402
from app.models.group_chat import GroupTodo  # noqa: E402
from app.services import todo_topics  # noqa: E402


def main() -> int:
    apply = "--apply" in sys.argv
    initialize_database()
    todo_topics.reset_known_entities_cache()

    with SessionLocal() as db:
        filled = 0
        for row in db.scalars(select(GroupTodo).where(GroupTodo.topic == "")):
            key = todo_topics.topic_of(row.title, row.detail or "").key
            if key:
                row.topic = key[:160]
                filled += 1
        db.commit()
        print(f"补齐主题键 {filled} 条")

        report = todo_topics.merge_existing(db, dry_run=not apply)
        if not apply:
            db.rollback()
        else:
            db.commit()

        if not report:
            print("没有需要合并的待办")
            return 0

        print(f"{'合并' if apply else '可合并'} {len(report)} 条：")
        for item in report:
            keep = db.get(GroupTodo, item["keep"])
            owner = db.get(User, keep.assignee_user_id) if keep else None
            print(f"  [{owner.display_name if owner else '?'}] 保留「{item['keepTitle']}」")
            print(f"        ← 「{item['dropTitle']}」")
        if not apply:
            print("\n这是预览。加 --apply 才会真的写库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
