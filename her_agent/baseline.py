"""Author-pack configuration shared by the standalone text and voice entries."""
from pathlib import Path
from .author import load_pack

ROOT = Path(__file__).resolve().parents[1]


def configured_pack(env, *, root=ROOT):
    value = env.get("ANGELENO_AUTHOR_PACK", "examples/demo/author-pack.json").strip()
    if not value:
        raise ValueError("ANGELENO_AUTHOR_PACK must select an author manifest")
    path = Path(value)
    if not path.is_absolute():
        path = Path(root) / path
    return load_pack(path)


def author_instructions(env):
    return configured_pack(env).instructions(env.get("ANGELENO_AUTHOR_VARIANT", "guided"))


def author_messages(messages, env):
    return configured_pack(env).messages(messages, env.get("ANGELENO_AUTHOR_VARIANT", "guided"))
