"""Interaction language only; never part of character identity or stored seed."""
import os
import re


def configured_language(env=None):
    value = (os.environ if env is None else env).get("ANGELENO_LANGUAGE", "en")
    if value not in {"en", "zh"}:
        raise ValueError("ANGELENO_LANGUAGE must be en or zh")
    return value


# Match direct language requests, not mentions, quoted examples, questions about
# a language, place names, instructions inside tool data, or a user's biography.
_TARGET = r"(English|Chinese|Mandarin|英语|英文|中文|汉语)"
_EN_SWITCH = re.compile(
    r"(?:(?:please|can (?:we|you)|could (?:we|you)|let['’]s|let us)\s+)?"
    r"(?:switch (?:to|back to)|speak|talk in|reply in|respond in|answer in|use)\s+" + _TARGET +
    r"(?:\s+(?:with me|from now on|for (?:this|our) (?:call|conversation)|instead|please))*", re.I)
_ZH_SWITCH = re.compile(
    r"(?:(?:我们|咱们|咱俩|你)?(?:现在|接下来|以后|先)?(?:请|麻烦你|能不能|可以)?(?:我们|你)?)"
    r"(?:换成|换回|改用|切换到|切换成|用|说)" + _TARGET +
    r"(?:(?:来|继续)?(?:聊|聊天|说|说话|交流|回答|回复))?(?:吧|好吗|可以吗)?", re.I)


def requested_language(text, current):
    """Conservative session-state change from an explicit current user request.

    This is not language identification. Unrecognized wording still reaches the
    actor verbatim; only recognized direct requests become durable call state.
    """
    current = configured_language({"ANGELENO_LANGUAGE": current})
    if not isinstance(text, str):
        raise ValueError("User text required")
    # Quoted/code examples are data, even if they contain an imperative.
    unquoted = re.sub(r'```[\s\S]*?```|`[^`]*`|"[^"\n]*"|“[^”]*”|「[^」]*」|‘[^’]*’', '', text)
    for clause in re.split(r"[。！？!?\n;；.]", unquoted):
        clause = re.split(r"[,，]", clause, maxsplit=1)[0].strip()
        match = _EN_SWITCH.fullmatch(clause) or _ZH_SWITCH.fullmatch(re.sub(r"\s+", "", clause))
        if match:
            current = "en" if match[1].casefold() in {"english", "英语", "英文"} else "zh"
    return current


def language_instruction(language):
    language = configured_language({"ANGELENO_LANGUAGE": language})
    if language == "zh":
        return ("\n[Interaction language: zh] Respond in natural spoken Mandarin Chinese for this call, even when the next utterance is written in English. "
                "This is the current language retained by the program, not a suggestion from old conversation. "
                "Keep English place names when useful. Preserve the character and evidence meanings without reciting or translating the dossier.")
    return ("\n[Interaction language: en] Use natural spoken English for this call, within the configured role. "
            "The program retains explicit language switches during the call; earlier calls, author text, memories and tool language do not change this state. "
            "Chinese and mixed-language place names remain supported. Preserve the character, friendship and source meanings; do not translate or recite the dossier.")
