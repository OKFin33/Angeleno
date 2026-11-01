"""Offline preview and memory management; explicitly enabled DeepSeek text chat."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from .author import load_pack
from .config import flag, load_env, resolve_path
from .runtime import ConversationRuntime, open_store


ROOT = Path(__file__).resolve().parent.parent


def open_memory(args, pack, *, create=False):
    user = getattr(args, "memory_user", None)
    if not user:
        return None
    return open_store(ROOT, pack, user, create=create, data_dir=getattr(args, "data_dir", None), purpose="real_user")


def save_private(path, data):
    path = Path(path).resolve()
    private = (ROOT / "private").resolve()
    if not path.is_relative_to(private):
        raise ValueError("Input snapshots must be saved inside Angeleno/private/")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def chat(args, pack, env=None):
    if getattr(args, "memory_tools", False) and not args.memory_user:
        raise ValueError("--memory-tools requires --memory-user")
    # Imported only on explicit chat; preview and memory management stay offline.
    from .budget import BudgetExceeded
    from .provider import configured_providers
    env = load_env(root=ROOT) if env is None else env
    from .config import flag
    attribution_check = flag(env, 'ANGELENO_MEMORY_ATTRIBUTION_CHECK')
    if attribution_check and not args.memory_user:
        raise ValueError('Attribution check requires persistent memory')
    provider, writer = configured_providers(env, root=ROOT)
    store = None
    try:
        store = open_memory(args, pack, create=getattr(args, "memory_create", False))
    except BaseException:
        await asyncio.gather(provider.aclose(), writer.aclose())
        raise
    runtime = ConversationRuntime(pack, provider, store=store, writer=writer,
                                  variant=args.variant, memory_tools=getattr(args, "memory_tools", False),
                                  attribution_check=attribution_check)
    maintainer = runtime.maintainer
    allow_flush = True
    print("文字试聊；会调用 DeepSeek 计费。/exit 结束。")
    if store:
        search_status = "可按需查询已有记忆，每轮最多一次查询与两次角色请求。" if getattr(args, "memory_tools", False) else "无搜索。"
        print("记忆已开启：文本保存在私有目录，后台每3轮及结束时会另行调用模型整理；" + search_status)
        print("/memory 查看；/sync 付费整理一次；/forget ID 遗忘；/forget all 清空；/private 临时聊；/public 恢复保存。")
        print("遗忘会沿输入依赖连带清除后续交流，可能跨多次会话；另存备份不随之删除。")
    else:
        print("本次不保存对话、不提供跨次记忆或搜索。")
    try:
        while True:
            try:
                # Let maintenance run while the user is typing; input() alone blocks asyncio.
                user = (await asyncio.to_thread(input, "你：")).strip()
            except EOFError:
                break
            if user == "/exit":
                break
            if not user:
                continue
            if store and user == "/memory":
                print(json.dumps(store.records(include_hidden=True), ensure_ascii=False, indent=2))
                print(f"尚未整理的消息：{len(store.pending())}")
                continue
            if store and user == "/sync":
                await maintainer.cancel()
                await maintainer.once()
                print("整理未完成，原文仍保留。" if maintainer.error else f"整理完成；剩余 {len(store.pending())} 条未处理消息。")
                continue
            if store and user.startswith("/forget "):
                await maintainer.cancel()
                target = user.removeprefix("/forget ").strip()
                try:
                    count = store.forget(None if target == "all" else target)
                    runtime.temporary_history = []
                    maintainer.error = None
                    print(f"已移除 {count} 条记忆及相关证据。另存的备份需分别处理。")
                except ValueError as exc:
                    print(str(exc))
                continue
            if store and user in {"/private", "/public"}:
                runtime.set_temporary(user == "/private")
                print("临时对话开启，不保存本段内容。" if runtime.temporary else "恢复保存，临时对话已从当前上下文移除。")
                continue
            was_temporary = runtime.temporary
            try:
                turn = runtime.begin(user)
                if runtime.temporary and not was_temporary:
                    print("已进入临时对话，本段不保存；/public 恢复。删除既有内容请用 /forget。")
            except ValueError as exc:
                print(f"本轮未发送：{exc}。可用 /sync 整理积压、/memory 检查或 /exit 结束。")
                continue
            pieces = []
            print(f"{pack.id}：", end="", flush=True)
            try:
                iterator = runtime.stream(turn)
                try:
                    while True:
                        try:
                            piece = await anext(iterator)
                        except StopAsyncIteration:
                            break
                        pieces.append(piece)
                        print(piece, end="", flush=True)
                finally:
                    await iterator.aclose()
                if not pieces:
                    raise ValueError("Provider returned no spoken text")
            except asyncio.CancelledError:
                runtime.delivered(turn, "".join(pieces), complete=False)
                raise
            except Exception as exc:
                # End the audition: displayed partial text cannot silently disappear
                # from the next turn's context or count as a complete reply.
                allow_flush = False
                runtime.delivered(turn, "".join(pieces), complete=False)
                status = "已显示的片段保留为中断证据，未确立为完整记忆。" if store and not turn.temporary else "本轮未保存。"
                if isinstance(exc, BudgetExceeded):
                    print("\n账户余额不足或无法确认，费用保护已停止新的模型请求。", end="")
                print("\n本轮没有完整完成，文字试聊已结束。" + status + "不会自动重试产生额外费用。")
                return
            print()
            runtime.delivered(turn, "".join(pieces), complete=True)
            if maintainer and maintainer.error:
                print("记忆整理上次未完成，近期原文仍保留；本轮之后将尝试整理新的批次。")
    except asyncio.CancelledError:
        # Cancelling a slow reply or input wait is not consent to start a
        # fresh maintenance call; already stored evidence stays pending.
        allow_flush = False
        raise
    finally:
        await runtime.close(flush=allow_flush)
        if maintainer and maintainer.error:
            print("后台整理未完成，原文仍在；本次没有宣称已整理记忆。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "chat", "memory"])
    parser.add_argument("--env-file", type=Path, help="Explicit optional KEY=value configuration file")
    parser.add_argument("--pack", type=Path, help="Author pack; defaults to ANGELENO_AUTHOR_PACK or the synthetic demo")
    parser.add_argument("--variant", choices=["raw", "guided"])
    parser.add_argument("--output", type=Path, help="Private request snapshot (preview only; never overwrite)")
    parser.add_argument("--memory-user", help="Opt in to persistent text memory for this user id")
    parser.add_argument("--memory-tools", action=argparse.BooleanOptionalAction, default=None, help="One memory search per actor turn (requires --memory-user)")
    parser.add_argument("--memory-create", action=argparse.BooleanOptionalAction, default=None, help="Explicitly allow a new memory identity")
    parser.add_argument("--data-dir", type=Path, help="Private state directory; defaults to private/state")
    parser.add_argument("--operation", choices=["inspect", "forget", "clear", "backup"], default="inspect")
    parser.add_argument("--record-id", help="Memory id to forget; also deletes dependent later exchanges across sessions")
    args = parser.parse_args(argv)
    env = load_env(env_file=args.env_file, root=ROOT)
    if env.get("ANGELENO_TIMEZONE"):
        # Author dates need this one setting; never export credentials or the
        # complete configuration into the process environment.
        os.environ["ANGELENO_TIMEZONE"] = env["ANGELENO_TIMEZONE"]
    args.pack = resolve_path(args.pack or env.get("ANGELENO_AUTHOR_PACK", "examples/demo/author-pack.json"), root=ROOT)
    args.variant = args.variant or env.get("ANGELENO_AUTHOR_VARIANT", "guided")
    if args.variant not in {"raw", "guided"}:
        parser.error("ANGELENO_AUTHOR_VARIANT must be raw or guided")
    # Preview is an author-input inspection unless memory is explicitly selected.
    # A configured chat identity must not make preview read or create its state.
    if args.action != "preview":
        args.memory_user = args.memory_user or env.get("ANGELENO_MEMORY_USER")
    args.data_dir = resolve_path(args.data_dir or env.get("ANGELENO_DATA_DIR", "private/state"), root=ROOT)
    env["ANGELENO_DATA_DIR"] = str(args.data_dir)
    if args.memory_tools is None:
        args.memory_tools = flag(env, "ANGELENO_MEMORY_TOOLS") if args.action == "chat" else False
    if args.memory_create is None:
        args.memory_create = flag(env, "ANGELENO_MEMORY_CREATE")
    if args.memory_tools and (args.action != "chat" or not args.memory_user):
        parser.error("--memory-tools requires chat and --memory-user")
    pack = load_pack(args.pack)
    if args.action == "preview":
        store = open_memory(args, pack)
        try:
            query = "今天没什么特别的事，就是想找你随便聊两句。"
            context = store.context("preview", query)[0] if store else None
            messages = pack.messages([{"role": "user", "content": query}], args.variant, memory_context=context)
        finally:
            if store:
                store.close()
        result = {"variant": args.variant, "provenance": pack.provenance(), "messages": messages}
        if args.output:
            save_private(args.output, result)
            print(f"Input snapshot saved: {args.output}")
        print(json.dumps({"id": pack.id, "variant": args.variant, "source_count": len(pack.documents),
                          "messages": len(messages), "input_chars": sum(len(m["content"]) for m in messages),
                          "network_calls": 0}, ensure_ascii=False))
    elif args.action == "memory":
        if not args.memory_user:
            parser.error("memory requires --memory-user")
        store = open_memory(args, pack)
        try:
            if args.operation == "inspect":
                print(json.dumps({"records": store.records(include_hidden=True), "pending": len(store.pending())},
                                 ensure_ascii=False, indent=2))
            elif args.operation == "forget":
                if not args.record_id:
                    parser.error("forget requires --record-id; use clear explicitly for all records")
                print(f"Removed {store.forget(args.record_id)} records and related evidence; separate backups are unchanged.")
            elif args.operation == "clear":
                print(f"Removed {store.forget()} records and all exchange text; separate backups are unchanged.")
            else:
                if not args.output or not args.output.resolve().is_relative_to((ROOT / "private").resolve()):
                    parser.error("backup requires --output inside Angeleno/private/")
                store.backup(args.output)
                print("Private database backup saved; future deletions do not modify this separate copy.")
        finally:
            store.close()
    else:
        if args.output:
            parser.error("--output is unavailable for chat; use memory backup explicitly")
        asyncio.run(chat(args, pack, env))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
