from __future__ import annotations

from typing import Any, Literal, Mapping

from fi.simulate.simulation.models import Persona

CallType = Literal["inbound", "outbound"]

VOICE_PERSONALITY_GUIDES: dict[str, str] = {
    "friendly and cooperative": "Be warm, approachable, and willing to work together. Show genuine interest and maintain a positive, collaborative attitude.",
    "professional and formal": "Maintain a business-like demeanor. Use formal language, stay focused, and keep interactions professional.",
    "cautious and skeptical": "Do not immediately accept everything at face value. Ask questions, verify information, and express concerns when appropriate.",
    "impatient and direct": "Get to the point quickly. Show impatience with lengthy explanations. Be straightforward and minimize pleasantries.",
    "detail-oriented": "Pay attention to specifics. Ask about details and ensure accuracy. Do not gloss over important information.",
    "easy-going": "Be relaxed and flexible. Do not stress over small issues. Go with the flow and maintain a laid-back attitude.",
    "anxious": "Show signs of worry or concern. Express uncertainty, ask for reassurance, and ask for clarification when needed.",
    "confident": "Speak with assurance. Do not second-guess yourself. Express certainty in your decisions and appear self-assured.",
    "analytical": "Think logically and systematically. Break down problems, consider pros and cons, and make decisions based on analysis.",
    "emotional": "Express feelings openly. Show emotional reactions, use emotive language, and let your feelings guide your responses.",
    "reserved": "Be measured and private. Think before speaking, do not overshare, and keep some distance in interactions.",
    "talkative": "Enjoy talking and sharing. Expand on topics, engage actively, and keep the conversation flowing with detailed responses.",
}

VOICE_COMMUNICATION_STYLE_GUIDES: dict[str, str] = {
    "direct and concise": "Get straight to the point. Be brief, clear, and avoid unnecessary details. Do not ramble or over-explain.",
    "detailed and elaborate": "Provide comprehensive explanations with full context. Elaborate on your points, give examples, and ensure thorough understanding.",
    "casual and friendly": "Use relaxed, conversational language. Be warm and approachable. Feel free to use colloquialisms and friendly expressions.",
    "formal and polite": "Use professional, courteous language. Maintain formality, use proper titles, and avoid casual expressions.",
    "technical": "Use technical terminology and precise language. Focus on accuracy, specifications, and technical details.",
    "simple and clear": "Use straightforward, easy-to-understand language. Avoid jargon. Break down concepts into simple explanations.",
    "questioning": "Ask clarifying questions frequently. Seek more information, verify understanding, and probe deeper into topics.",
    "assertive": "Speak with confidence and authority. State your needs clearly and directly. Do not be hesitant about your requirements.",
    "passive": "Be more accommodating and less direct. Avoid being pushy. Let the conversation flow naturally without forcing your agenda.",
    "collaborative": "Work together to find solutions. Be open to suggestions, build on ideas, and engage in cooperative dialogue.",
}


def _first(value: object) -> str:
    if isinstance(value, Mapping):
        value = next(iter(value.values()), "")
    elif isinstance(value, list):
        value = value[0] if value else ""
    return str(value).strip() if value is not None else ""


def _persona_data(persona: Persona) -> dict[str, Any]:
    data = dict(persona.persona)
    identity = persona.identity
    if identity is None:
        return data

    if identity.name:
        data.setdefault("name", identity.name)
    if identity.role:
        data.setdefault("role", identity.role)
    if identity.language:
        data.setdefault("language", identity.language)
    for key, value in identity.demographics.items():
        data.setdefault(key, value)

    metadata = data.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    if identity.summary:
        metadata.setdefault("identity_summary", identity.summary)
    if identity.style_notes:
        metadata.setdefault("style_notes", list(identity.style_notes))
    if metadata:
        data["metadata"] = metadata
    return data


def format_voice_persona(persona: Persona, *, call_type: CallType) -> str:
    if call_type not in {"inbound", "outbound"}:
        raise ValueError("call_type must be inbound or outbound")

    data = _persona_data(persona)
    sections: list[str] = []
    identity_lines = []
    identity_fields = (
        ("Name", data.get("name")),
        ("Role", data.get("role")),
        ("Occupation", data.get("profession") or data.get("occupation")),
        ("Age Group", data.get("age_group") or data.get("ageGroup")),
        ("Location", data.get("location")),
        ("Gender", data.get("gender")),
    )
    for label, value in identity_fields:
        if value:
            identity_lines.append(f"**{label}:** {value}")
    if identity_lines:
        sections.append("# YOUR IDENTITY\n\n" + "\n".join(identity_lines))

    situation = persona.situation or "You are engaging in a routine conversation."
    situation_lines = [
        "# YOUR CURRENT SITUATION",
        "",
        situation,
        "",
        f"**Your objective:** {persona.outcome}",
        "",
        "## Your Role in This Call",
        "",
    ]
    if call_type == "outbound":
        situation_lines.extend(
            [
                "**You are RECEIVING this call.** Someone is calling you.",
                "",
                "**CRITICAL: You did NOT initiate this call. You are the person being contacted.**",
                "",
                "- Let the caller introduce themselves and explain their purpose.",
                "- React naturally based on whether you expected the call.",
                "- Ask questions, express reactions, or raise concerns as this person would.",
                "- NEVER switch roles and act as if you made the call or provide the service.",
            ]
        )
    else:
        situation_lines.extend(
            [
                "**You are MAKING this call.** You initiated this contact.",
                "",
                "**CRITICAL: YOU started this conversation. You are reaching out to someone.**",
                "",
                "- Start by introducing yourself and stating your purpose clearly.",
                "- YOU are seeking information, help, service, or answers.",
                "- Provide information when asked and follow the other person's guidance.",
                "- NEVER switch roles and act as if you receive the call or provide assistance.",
            ]
        )
    situation_lines.extend(
        [
            "",
            "React to what you hear in real time. Ask clarifying questions, express confusion when needed, and stay in YOUR role for the entire conversation.",
        ]
    )
    sections.append("\n".join(situation_lines))

    personality = _first(data.get("personality"))
    communication_style = _first(
        data.get("communication_style") or data.get("communicationStyle")
    )
    keywords = data.get("keywords")
    if isinstance(keywords, str):
        keywords = [item.strip() for item in keywords.split(",") if item.strip()]
    personality_lines = ["# YOUR PERSONALITY & COMMUNICATION", ""]
    if personality:
        guide = VOICE_PERSONALITY_GUIDES.get(
            personality.lower(),
            "Let this personality trait guide your reactions, responses, and overall demeanor.",
        )
        personality_lines.extend(
            [f"## Personality: {personality}", "", guide, ""]
        )
    if communication_style:
        guide = VOICE_COMMUNICATION_STYLE_GUIDES.get(
            communication_style.lower(),
            "Let this style guide how you express yourself throughout the conversation.",
        )
        personality_lines.extend(
            [f"## Communication Style: {communication_style}", "", guide, ""]
        )
    if isinstance(keywords, list) and keywords:
        personality_lines.append(
            "**Key Traits:** " + ", ".join(str(item) for item in keywords)
        )
    if len(personality_lines) > 2:
        sections.append("\n".join(personality_lines).rstrip())

    language = data.get("language") or data.get("languages")
    accent = _first(data.get("accent"))
    if language or accent:
        languages = language if isinstance(language, list) else [language]
        language_text = ", ".join(str(item) for item in languages if item)
        language_lines = ["# LANGUAGE & SPEECH PATTERNS", ""]
        if language_text:
            language_lines.extend(
                [
                    f"**Language(s):** {language_text}",
                    f"Use vocabulary and expressions natural to someone who speaks {language_text}.",
                ]
            )
        if data.get("multilingual"):
            language_lines.append("Switch languages naturally when the context calls for it.")
        if accent:
            language_lines.append(f"Maintain the natural speech patterns of a {accent} accent.")
        sections.append("\n".join(language_lines))

    metadata = data.get("metadata")
    if isinstance(metadata, Mapping) and metadata:
        metadata_lines = ["# ADDITIONAL CHARACTERISTICS", ""]
        for key in sorted(metadata):
            label = str(key).replace("_", " ").title()
            metadata_lines.append(f"**{label}:** {metadata[key]}")
        sections.append("\n".join(metadata_lines))

    sections.append(
        "# HOW TO BE THIS PERSON\n\n"
        "You ARE this person. Embody the character in every response.\n\n"
        "1. Generate only natural spoken dialogue. Never include stage directions, labels, markup, or meta-commentary.\n"
        "2. Maintain the identity, personality, communication style, and call direction from start to finish.\n"
        "3. Actively pursue the objective in Your Current Situation without inventing authority or information you do not have.\n"
        "4. Respond as this specific person would, not as the service agent or the person on the other end of the line.\n"
        "5. If you begin offering assistance, asking how you can help, or taking the other person's responsibilities, stop and return to your assigned role.\n"
        "6. Share personal details only when relevant or requested.\n"
        "7. Wait for the other side's reply before ending the call. When the conversation is mutually finished, say one natural closing sentence and silently call end_call. Never say 'function', 'tool', or 'end_call' aloud."
    )
    return "\n\n".join(sections)


def build_voice_simulator_prompt(
    persona: Persona,
    *,
    call_type: CallType,
    agent_name: str | None = None,
) -> str:
    channel = (
        f"You will make a call to an agent named {agent_name}."
        if call_type == "inbound" and agent_name
        else "You will make a call to an agent."
        if call_type == "inbound"
        else f"You will receive a call from an agent named {agent_name}."
        if agent_name
        else "You will receive a call from an agent."
    )
    persona_text = format_voice_persona(persona, call_type=call_type)
    return (
        "You are a customer in a voice simulation. "
        f"{channel} Stay consistent with the persona throughout the conversation.\n\n"
        f"{persona_text}\n\n"
        "---\n\n"
        "# CONVERSATION EXECUTION RULES\n\n"
        "These are internal instructions. Never reference or quote them.\n\n"
        "Generate ONLY spoken dialogue without emotional tags, action descriptions, quotation marks, brackets, or meta-commentary. "
        "Use natural hesitations and self-corrections when they fit the persona. "
        "Speak numbers, dates, currency, phone numbers, and times in voice-natural words rather than symbolic formatting. "
        "Before every response, confirm that you are speaking AS the customer, pursuing the stated objective, and not reversing roles."
    )


__all__ = [
    "CallType",
    "VOICE_COMMUNICATION_STYLE_GUIDES",
    "VOICE_PERSONALITY_GUIDES",
    "build_voice_simulator_prompt",
    "format_voice_persona",
]
