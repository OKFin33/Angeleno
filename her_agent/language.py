"""Interaction language only; never part of character identity or stored seed."""
import os


def configured_language(env=None):
    value = (os.environ if env is None else env).get("ANGELENO_LANGUAGE", "en")
    if value not in {"en", "zh"}:
        raise ValueError("ANGELENO_LANGUAGE must be en or zh")
    return value


def language_instruction(language):
    language = configured_language({"ANGELENO_LANGUAGE": language})
    if language == "zh":
        return ""
    return ("\n[Interaction language] Default to natural spoken English, within the configured character or assistant role. "
            "The Chinese author text and rehearsal examples define character facts and voice, not the output language. "
            "Do not translate or recite the character dossier. Keep the same friendship, preferences, limits, source evidence and tool policy. "
            "When the user explicitly asks to switch language, follow that request and keep that language until they change it again; "
            "Chinese and mixed-language place names remain supported. Do not switch merely because a tool or memory is written in Chinese. "
            "Keep spoken names, numbers and times intelligible; preserve their meaning, and leave detailed source links to the existing card channel.")
