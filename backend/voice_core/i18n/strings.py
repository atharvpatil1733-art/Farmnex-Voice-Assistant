from __future__ import annotations

LLM_FAILURE = {
    "hi-IN": "माफ़ कीजिए, अभी जवाब नहीं दे पा रही। कृपया फिर से कोशिश करें।",
    "mr-IN": "माफ करा, आत्ता उत्तर देऊ शकत नाही. कृपया पुन्हा प्रयत्न करा.",
    "en-IN": "Sorry, I couldn't answer that right now. Please try again.",
}

TOOL_ROUND_CUTOFF = {
    "hi-IN": "माफ़ कीजिए, मुझे अभी जवाब नहीं मिल पाया।",
    "mr-IN": "माफ करा, मला आत्ता उत्तर सापडले नाही.",
    "en-IN": "Sorry, I wasn't able to work that out right now.",
}

CANCELLATION_ACK = {
    "hi-IN": "ठीक है, रहने दिया।",
    "mr-IN": "ठीक आहे, रद्द केले.",
    "en-IN": "Okay, cancelled.",
}


CONFIRM_UNRESOLVED = {
    "hi-IN": "माफ़ कीजिए, मुझे वह नहीं मिला। कृपया बताइए आप किसकी बात कर रहे हैं।",
    "mr-IN": "माफ करा, मला ते सापडले नाही. कृपया कोणते ते सांगा.",
    "en-IN": "Sorry, I couldn't find that. Please tell me which one you mean.",
}


def get(strings: dict[str, str], language: str, default_language: str = "en-IN") -> str:
    return strings.get(language, strings[default_language])
