"""Author-pack configuration shared by the standalone text and voice entries."""
from pathlib import Path
from .author import load_pack
from .language import configured_language

from angeleno.paths import ROOT


def configured_pack(env, *, root=ROOT):
    value = env.get("ANGELENO_AUTHOR_PACK", "characters/jo/author-pack.json").strip()
    if not value:
        raise ValueError("ANGELENO_AUTHOR_PACK must select an author manifest")
    from .paths import resource_or_path
    return load_pack(resource_or_path(value, root=root))


def author_instructions(env):
    return configured_pack(env).instructions(env.get("ANGELENO_AUTHOR_VARIANT", "guided"), language=configured_language(env))


def author_messages(messages, env):
    return configured_pack(env).messages(messages, env.get("ANGELENO_AUTHOR_VARIANT", "guided"), language=configured_language(env))
