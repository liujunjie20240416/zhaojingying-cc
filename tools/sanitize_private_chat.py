"""Create a redacted copy of a private chat export without logging secrets."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path


PATTERNS = [
    ("id_card", re.compile(r"(?<![0-9A-Za-z])(?:\d{6}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]|\d{15})(?![0-9A-Za-z])"), "[身份证号已脱敏]"),
    ("phone", re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), "[手机号已脱敏]"),
    ("bank_card", re.compile(r"(?<!\d)(?:\d[ -]?){16,19}(?!\d)"), "[银行卡号已脱敏]"),
    ("email", re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])"), "[邮箱已脱敏]"),
    ("ipv4", re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?!\d)"), "[IP地址已脱敏]"),
    ("number_340", re.compile(r"(?<!\d)340\d+(?!\d)"), "[340开头数字已脱敏]"),
    ("ljj_secret", re.compile(r"(?<![A-Za-z0-9_])ljj[A-Za-z0-9_@.!#$%^&*+-]+", re.IGNORECASE), "[ljj开头信息已脱敏]"),
]

# Only redact short/generic secrets when a privacy label is present. This avoids
# replacing dates, prices, anniversaries and ordinary quantities everywhere.
SECRET_CONTEXT = re.compile(
    r"(?P<label>(?:登录|支付|银行卡|微信|QQ|邮箱|账号|账户|锁屏|开机|WiFi|wifi|WIFI|PIN|pin)?\s*"
    r"(?:密码|验证码|校验码|动态码|口令|密钥|秘钥|token|Token|TOKEN))"
    r"(?P<sep>\s*(?:是|为|[:：=]|叫)?\s*)"
    r"(?P<secret>(?!已脱敏)[A-Za-z0-9０-９_@.!#$%^&*+-]{4,64})"
)

SECRET_NUMBER_AFTER_LABEL = re.compile(
    r"(?P<label>密码|验证码|校验码|动态码|口令|PIN|pin|密钥|秘钥)"
    r"(?P<between>[^\r\n0-9０-９]{0,30})"
    r"(?P<secret>(?:[0-9０-９][ ]?){4,64})"
)

LABELED_ACCOUNT = re.compile(
    r"(?P<label>(?:身份证号|身份证号码|银行卡号|卡号|手机号|手机号码|电话号码|QQ号|微信号))"
    r"(?P<sep>\s*(?:是|为|[:：=]|叫)?\s*)"
    r"(?P<value>[A-Za-z0-9_-]{5,32})"
)


def redact(text: str) -> tuple[str, Counter]:
    counts: Counter = Counter()
    result = text
    for name, pattern, replacement in PATTERNS:
        result, count = pattern.subn(replacement, result)
        counts[name] += count

    def replace_secret(match: re.Match) -> str:
        counts["password_or_code"] += 1
        separator = match.groupdict().get("sep", match.groupdict().get("between", ""))
        return f"{match.group('label')}{separator}[密码或验证码已脱敏]"

    def replace_account(match: re.Match) -> str:
        counts["labeled_account"] += 1
        return f"{match.group('label')}{match.group('sep')}[账号信息已脱敏]"

    result = SECRET_CONTEXT.sub(replace_secret, result)
    result = SECRET_NUMBER_AFTER_LABEL.sub(replace_secret, result)
    result = LABELED_ACCOUNT.sub(replace_account, result)
    return result, counts


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: sanitize_private_chat.py INPUT OUTPUT")
    source = Path(sys.argv[1]).expanduser().resolve()
    destination = Path(sys.argv[2]).expanduser().resolve()
    if source == destination:
        raise SystemExit("refusing to overwrite the source file")
    text = source.read_text(encoding="utf-8-sig")
    sanitized, counts = redact(text)
    destination.write_text(sanitized, encoding="utf-8")
    print(f"sanitized_copy={destination}")
    print(f"characters={len(text)} replacements={sum(counts.values())}")
    for name in sorted(counts):
        print(f"{name}={counts[name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
