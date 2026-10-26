"""论坛发言与模型上下文的本机隐私检查，不记录匹配到的内容。"""

import re
import unicodedata


PII = re.compile(
    r"[\w.+-]+@[\w.-]+\.[a-z]{2,}"
    r"|(?<!\d)1[3-9]\d{9}(?!\d)"
    r"|(?<!\d)(?:20|30)\d{8,10}(?!\d)"
    r"|(?<!\d)\d{17}[\dx](?!\d)"
    r"|/(?:etc|root|home|opt|var)/[^\s]+",
    re.IGNORECASE,
)


def normalized(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", value).casefold()
        if not c.isspace() and unicodedata.category(c) != "Cf"
    )


class PrivacyGuard:
    def __init__(self, protected=()):
        self._protected = tuple(
            normalized(value) for value in protected if isinstance(value, str) and value
        )

    def contains(self, value):
        if not isinstance(value, str):
            return False
        compact = normalized(value)
        return bool(PII.search(compact) or any(secret in compact for secret in self._protected))

    def redact(self, value):
        if isinstance(value, str):
            # 整段隐去，避免局部替换遗漏变体或意外保留关联身份信息。
            return "[已隐去敏感内容]" if self.contains(value) else value
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact(item) for key, item in value.items()}
        return value
