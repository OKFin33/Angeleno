"""A deterministic synthetic demo of persistence and request assembly, never a model audition."""

import json
from pathlib import Path
import tempfile

from .author import load_pack
from .continuity import Conversation
from .memory import MemoryStore, seed_digest


def main():
    print("离线合成演示：整理结果预先编写，只验证保存、召回和请求接线；不调用模型。")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "character.md").write_text("林是一名成年修复师。她与用户的已有关系由作者定义。")
        manifest = root / "pack.json"
        manifest.write_text(json.dumps({"format": "her-author-pack/1", "id": "demo", "as_of": "2026-10-01",
                                        "documents": [{"path": "character.md", "kind": "character"}]}))
        pack = load_pack(manifest)
        path = root / "memory.sqlite3"

        def open_store():
            return MemoryStore(path, character=pack.id, user="synthetic", seed=seed_digest(pack))

        store = open_store()
        try:
            chat = Conversation(pack, store)
            chat.begin("我打算周六去 MOCA。")
            chat.delivered("合成回复：那次展览可以先看一件作品。")
            batch = store.batch()
            rec = {"id": "episode.moca", "kind": "episode", "text": "用户打算周六去 MOCA。",
                   "keywords": ["MOCA", "当代艺术馆"], "known": True, "evidence": [batch.events[0]["seq"]]}
            store.apply(batch, {"updates": [rec], "retire": []})
            for n in range(25):
                chat.begin(f"无关的合成话题 {n}。")
                chat.delivered("合成回应。")
                store.apply(store.batch(), {"updates": [], "retire": []})
        finally:
            store.close()
        store = open_store()
        try:
            new = Conversation(pack, store)
            request = new.begin("上次聊的当代艺术馆，我改成周日去了。")
            assert "用户打算周六去 MOCA" in request[0]["content"]
            assert '无关的合成话题 0。' not in str(request)
            assert '无关的合成话题 24。' in str(request)
            assert len(request) <= 21
            print("PASS：跨25轮并重新打开后，相关旧经历进入请求；近期交流仍保留，更早无关历史留在库里。")
            new.delivered("合成回复：收到，周日。")
            batch = store.batch()
            corrected = {**rec, "id": "episode.moca_sunday", "text": "用户纠正为周日去了 MOCA。",
                         "evidence": [batch.events[0]["seq"]]}
            store.apply(batch, {"updates": [corrected], "retire": [rec["id"]]})
            request = Conversation(pack, store).begin("MOCA 是哪天去的？")
            # The actor prompt includes the real weekday. Running this demo on
            # Saturday must not look like stale memory; check the retired fact.
            assert "用户纠正为周日去了 MOCA" in request[0]["content"]
            assert "用户打算周六去 MOCA" not in str(request)
            print("PASS：后台替代后，新会话使用周日，旧说法不再被召回。")
            store.forget(corrected["id"])
            context, _ = store.context("another", "MOCA")
            assert "用户纠正为周日" not in str(context)
            print("PASS：遗忘清除了记录、关联交流及索引；临时数据库将在退出时删除。")
        finally:
            store.close()


if __name__ == "__main__":
    main()
