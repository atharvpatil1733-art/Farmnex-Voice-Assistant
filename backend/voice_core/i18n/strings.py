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


PENDING_EXPIRED = {
    "hi-IN": "वह अनुरोध समय पर पक्का नहीं हुआ, इसलिए रद्द हो गया। कृपया फिर से बताइए।",
    "mr-IN": "ती विनंती वेळेत पक्की झाली नाही, म्हणून रद्द झाली. कृपया पुन्हा सांगा.",
    "en-IN": "That request wasn't confirmed in time, so it was cancelled. Please ask again.",
}

ACTION_ALREADY_DONE = {
    "hi-IN": "यह काम पहले ही हो चुका है।",
    "mr-IN": "हे काम आधीच झाले आहे.",
    "en-IN": "That has already been done.",
}

ACTION_IN_PROGRESS = {
    "hi-IN": "यह काम अभी चल रहा है, कृपया थोड़ा रुकिए।",
    "mr-IN": "हे काम सध्या सुरू आहे, कृपया थोडे थांबा.",
    "en-IN": "That is already in progress, please wait a moment.",
}

WRITE_FAILED = {
    "hi-IN": "माफ़ कीजिए, यह काम नहीं हो पाया। कृपया थोड़ी देर बाद फिर कोशिश करें।",
    "mr-IN": "माफ करा, हे काम होऊ शकले नाही. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.",
    "en-IN": "Sorry, that couldn't be done. Please try again in a little while.",
}

NOTHING_TO_CONFIRM = {
    "hi-IN": "अभी पक्का करने के लिए कुछ बाकी नहीं है।",
    "mr-IN": "आत्ता पक्के करण्यासाठी काहीही बाकी नाही.",
    "en-IN": "There is nothing waiting for confirmation right now.",
}


def get(strings: dict[str, str], language: str, default_language: str = "en-IN") -> str:
    return strings.get(language, strings[default_language])
