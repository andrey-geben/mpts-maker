import re
from dataclasses import dataclass


@dataclass
class Channel:
    name: str
    url: str
    group: str = ""
    logo: str = ""
    tvg_id: str = ""


# #EXTINF:-1 tvg-id="x" group-title="Y",Channel Name
ATTR_RE = re.compile(r'([\w-]+)\s*=\s*["\']([^"\']*)["\']')


def _split_extinf(line: str) -> tuple[str, str]:
    """Делит EXTINF по первой запятой вне кавычек (запятые бывают в group-title)."""
    body = line[len("#EXTINF:"):]
    quote = None
    for i, ch in enumerate(body):
        if ch in "\"'":
            if quote is None:
                quote = ch
            elif quote == ch:
                quote = None
        elif ch == "," and quote is None:
            return body[:i], body[i + 1:]
    return body, ""


def _read_text(path: str) -> str:
    # Playlists sometimes come in cp1251 (Cyrillic), sometimes UTF-8 w/ BOM.
    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def parse_m3u(path: str) -> list[Channel]:
    channels: list[Channel] = []
    current_attrs: dict = {}
    current_name = ""

    for raw in _read_text(path).splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            attrs_str, current_name = _split_extinf(line)
            current_name = current_name.strip()
            current_attrs = dict(ATTR_RE.findall(attrs_str))
        elif line.startswith("#"):
            continue
        else:
            channels.append(Channel(
                name=current_name or "Unnamed",
                url=line,
                group=current_attrs.get("group-title", ""),
                logo=current_attrs.get("tvg-logo", ""),
                tvg_id=current_attrs.get("tvg-id", ""),
            ))
            current_attrs = {}
            current_name = ""

    return channels