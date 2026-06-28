#!/usr/bin/env python3
"""
微信聊天记录解析器（适配版）

支持的导出格式：
1. [YYYY-MM-DD HH:MM:SS] 发送人: 内容  （带方括号的时间戳）
2. YYYY-MM-DD HH:MM:SS 发送人: 内容    （标准格式）
3. WechatExporter 导出的 HTML 文件
4. CSV 格式

用法：
    python tools/wechat_parser.py --file chat.txt --target "大白鹅" --output /tmp/out.txt
"""

import re
import sys
import csv
import argparse
from pathlib import Path
from html.parser import HTMLParser


class WechatHTMLParser(HTMLParser):
    """解析 WechatExporter 导出的 HTML 格式"""

    def __init__(self, target_name: str):
        super().__init__()
        self.target_name = target_name
        self.messages: list[dict] = []
        self._current_sender = ""
        self._current_time = ""
        self._current_content: list[str] = []
        self._in_sender = False
        self._in_content = False
        self._in_time = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class") or ""
        if "sender" in cls:
            self._in_sender = True
        elif "content" in cls or "message-text" in cls:
            self._in_content = True
        elif "time" in cls or "timestamp" in cls:
            self._in_time = True

    def handle_endtag(self, tag):
        if self._in_sender:
            self._in_sender = False
        elif self._in_content:
            self._in_content = False
            content = "".join(self._current_content).strip()
            if content and self.target_name in self._current_sender:
                self.messages.append({
                    "sender": self._current_sender,
                    "content": content,
                    "timestamp": self._current_time,
                })
            self._current_content = []
        elif self._in_time:
            self._in_time = False

    def handle_data(self, data):
        if self._in_sender:
            self._current_sender = data.strip()
        elif self._in_content:
            self._current_content.append(data)
        elif self._in_time:
            self._current_time = data.strip()


def is_noise(content: str) -> bool:
    """判断消息是否为噪音（URL、转发、系统通知等）"""
    content = content.strip()
    if not content:
        return True
    if len(content) > 2000:
        return True     # 超长消息通常是转发文章
    # 纯 URL 消息
    if re.match(r"^https?://\S+$", content):
        return True
    # 媒体/系统占位符
    skip_media = [
        "[图片]", "[文件]", "[撤回了一条消息]", "[语音]", "[视频]",
        "[表情]", "[位置]", "[名片]", "[链接]", "[红包]", "[转账]",
        "<img", "<video", "<audio",
    ]
    if any(p in content for p in skip_media):
        return True
    # 典型的转发/通知/作业复制开头
    noise_starts = [
        "恭喜您获得", "你好，我是", "链接:", "提取码:",
        "建议及时保存", "Nitrogen and sulfur",
        "至少从目前来看", "采样地写在图注",
        "https://yz.nwsuaf", "https://pan.baidu",
        "ABSTRACT", "Introduction\n",
    ]
    if any(content.startswith(s) for s in noise_starts):
        return True
    # 长篇大论的政治/学术/公告内容（特征关键词组合）
    bulk_noise_keywords = [
        # 政治学习
        ("习近平总书记", "生态文明"),
        ("习近平总书记", "绿水青山"),
        ("治国理政", "理念"),
        ("十九大", "报告"),
        ("社会主义核心价值观",),
        # 学术论文
        ("et al.", "doped"),
        ("由表", "图", "可以看出"),
        ("浓度范围为", "mg/L"),
        ("氟在环境中",),
        ("氟离子浓度", "mg/L"),
        ("二者的补水来源",),
        ("mol/L", "结论"),
        # 群公告/转发
        ("群公告",),
        ("亲爱的同学们", "腊八"),
        # 淘宝链接
        ("【淘宝】", "https://m.tb.cn"),
        ("点击链接直接打开", "淘宝搜"),
    ]
    for keywords in bulk_noise_keywords:
        if all(kw in content for kw in keywords):
            return True
    # 学术引用特征
    if re.match(r"^\[\d+\][A-Z]", content):
        return True  # [1]Zhang XiaoYang...
    # 包含大量英文+特殊字符（学术论文复制）
    if len(content) > 100 and sum(1 for c in content if c.isascii() and c.isalpha()) / len(content) > 0.7:
        return True  # >70% 英文字母，很可能是论文/文章
    return False


def parse_wechat_txt(
    file_path: str,
    target_name: str = "",
    target_only: bool = False,
    filter_noise: bool = True,
) -> list[dict]:
    """
    解析微信 TXT 聊天记录，支持方括号时间戳格式。

    Args:
        file_path: 聊天记录文件路径
        target_name: 目标人物姓名（用于筛选，空字符串=全部保留）
        target_only: True=只返回目标的消息，False=返回双方对话
        filter_noise: 是否过滤噪音（URL、转发等）
    """
    messages: list[dict] = []

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 支持两种时间戳格式：
    # [2021-03-21 16:52:43] 大白鹅: 消息
    #  2021-03-21 16:52:43  大白鹅: 消息
    pattern_bracket = re.compile(
        r"^\[(?P<time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\]\s+(?P<sender>.+?)[:：]\s*(?P<content>.+)$"
    )
    pattern_plain = re.compile(
        r"^(?P<time>\d{4}[-/]\d{1,2}[-/]\d{1,2}[\s\d:]*)\s+(?P<sender>.+?)[:：]\s*(?P<content>.+)$"
    )

    current_msg: dict | None = None

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue

        m = pattern_bracket.match(line) or pattern_plain.match(line)

        if m:
            if current_msg is not None:
                content = current_msg["content"].strip()
                if content and not (filter_noise and is_noise(content)):
                    messages.append(current_msg)
                current_msg = None

            current_msg = {
                "sender": m.group("sender").strip(),
                "content": m.group("content").strip(),
                "timestamp": m.group("time").strip(),
            }
        elif current_msg is not None:
            current_msg["content"] += "\n" + line

    # 最后一条
    if current_msg is not None:
        content = current_msg["content"].strip()
        if content and not (filter_noise and is_noise(content)):
            messages.append(current_msg)

    # 按需筛选发送人
    if target_name:
        if target_only:
            messages = [m for m in messages if target_name in m["sender"]]
        # 不筛选时保留双方对话，但可以在后续分析时过滤

    return messages


def extract_key_content(messages: list[dict]) -> dict:
    """对消息分类：长消息 / 情感类 / 日常"""
    long_messages: list[dict] = []
    emotional_messages: list[dict] = []
    daily_messages: list[dict] = []

    emotional_keywords = [
        "想你", "爱你", "喜欢", "讨厌", "生气", "难过", "开心", "高兴",
        "不开心", "委屈", "对不起", "分手", "在一起", "想见你", "好想",
        "心疼", "舍不得", "感动", "幸福", "孤独", "害怕", "担心",
        "吵架", "冷战", "和好", "原谅", "道歉", "伤心", "哭",
    ]

    for msg in messages:
        content = msg["content"]
        if len(content) > 50:
            long_messages.append(msg)
        elif any(kw in content for kw in emotional_keywords):
            emotional_messages.append(msg)
        else:
            daily_messages.append(msg)

    return {
        "long_messages": long_messages,
        "emotional_messages": emotional_messages,
        "daily_messages": daily_messages,
        "total_count": len(messages),
    }


def format_output_as_chunks(messages: list[dict], target_name: str, chunk_size: int = 200) -> list[str]:
    """
    将消息格式化为可直接存入向量数据库的文本块。
    每个块包含一个时间段内的连续消息，便于语义检索。
    """
    chunks: list[str] = []
    for i in range(0, len(messages), chunk_size):
        batch = messages[i:i + chunk_size]
        lines = [f"# {target_name}的聊天记录片段 {i // chunk_size + 1}"]
        for msg in batch:
            ts = msg.get("timestamp", "")
            sender = msg.get("sender", "")
            lines.append(f"[{ts}] {sender}: {msg['content']}")
        chunks.append("\n".join(lines))
    return chunks


def parse_wechat_html(file_path: str, target_name: str) -> list[dict]:
    """解析 HTML 格式的微信聊天记录（WechatExporter 导出）"""
    with open(file_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    parser = WechatHTMLParser(target_name)
    parser.feed(html_content)
    return parser.messages


def parse_wechat_csv(file_path: str, target_name: str) -> list[dict]:
    """解析 CSV 格式的微信聊天记录"""
    messages: list[dict] = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sender = (
                row.get("sender") or row.get("发送人")
                or row.get("from") or row.get("NickName") or ""
            )
            content = (
                row.get("content") or row.get("内容")
                or row.get("message") or row.get("Message") or ""
            )
            timestamp = (
                row.get("timestamp") or row.get("时间")
                or row.get("time") or row.get("StrTime") or ""
            )
            if target_name and target_name not in str(sender):
                continue
            if not content.strip():
                continue
            messages.append({
                "sender": str(sender),
                "content": str(content).strip(),
                "timestamp": str(timestamp),
            })
    return messages


def main():
    parser = argparse.ArgumentParser(description="解析微信聊天记录导出文件")
    parser.add_argument("--file", required=True, help="输入文件路径")
    parser.add_argument("--target", default="", help="目标人物姓名（空=全部）")
    parser.add_argument("--output", default=None, help="输出文件路径")
    parser.add_argument("--target-only", action="store_true", help="只提取目标的消息（默认保留双方）")
    parser.add_argument("--no-filter", action="store_true", help="不过滤噪音")
    parser.add_argument("--chunks", action="store_true", help="输出为分块格式（用于向量数据库）")

    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"错误：文件不存在 {file_path}", file=sys.stderr)
        sys.exit(1)

    suffix = file_path.suffix.lower()

    if suffix in (".html", ".htm"):
        messages = parse_wechat_html(str(file_path), args.target)
    elif suffix == ".csv":
        messages = parse_wechat_csv(str(file_path), args.target)
    else:
        messages = parse_wechat_txt(
            str(file_path),
            target_name=args.target,
            target_only=args.target_only,
            filter_noise=not args.no_filter,
        )

    if not messages:
        print(f"警告：未找到 '{args.target}' 发出的消息", file=sys.stderr)
        sys.exit(0)

    if args.chunks:
        chunks = format_output_as_chunks(messages, args.target)
        output_text = "\n\n---\n\n".join(chunks)
    else:
        extracted = extract_key_content(messages)
        output_text = format_output(args.target, extracted)

    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"已输出到 {args.output}，共 {len(messages)} 条消息")
    else:
        print(output_text)


def format_output(target_name: str, extracted: dict) -> str:
    """格式化输出，供 AI 分析使用"""
    lines = [
        f"# 微信聊天记录提取结果",
        f"目标人物：{target_name}",
        f"总消息数：{extracted['total_count']}",
        "",
        "---",
        "",
        "## 长消息（心情/想法类，权重最高）",
        "",
    ]
    for msg in extracted["long_messages"]:
        ts = f"[{msg['timestamp']}] " if msg["timestamp"] else ""
        lines.append(f"{ts}{msg['content']}")
        lines.append("")

    lines += ["---", "", "## 情感类消息", ""]
    for msg in extracted["emotional_messages"]:
        ts = f"[{msg['timestamp']}] " if msg["timestamp"] else ""
        lines.append(f"{ts}{msg['content']}")
        lines.append("")

    lines += ["---", "", "## 日常沟通（风格参考）", ""]
    for msg in extracted["daily_messages"][:200]:
        ts = f"[{msg['timestamp']}] " if msg["timestamp"] else ""
        lines.append(f"{ts}{msg['content']}")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
