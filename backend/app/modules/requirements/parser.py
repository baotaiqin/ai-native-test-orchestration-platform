import re
from pathlib import Path

from app.modules.requirements.schemas import MarkdownPreviewNode

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def parse_markdown_tree(content: str, filename: str | None = None) -> list[MarkdownPreviewNode]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING_PATTERN.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    if not headings:
        title = Path(filename).stem if filename else "未命名需求"
        return [
            MarkdownPreviewNode(
                temp_id="node-1",
                parent_temp_id=None,
                title=title,
                level=1,
                markdown_content=content.strip(),
                order_index=0,
            )
        ]

    nodes: list[MarkdownPreviewNode] = []
    level_stack: list[tuple[int, str]] = []
    for order_index, (line_index, level, title) in enumerate(headings):
        next_line_index = (
            headings[order_index + 1][0]
            if order_index + 1 < len(headings)
            else len(lines)
        )
        direct_content = "\n".join(lines[line_index:next_line_index]).strip()
        while level_stack and level_stack[-1][0] >= level:
            level_stack.pop()
        parent_temp_id = level_stack[-1][1] if level_stack else None
        temp_id = f"node-{order_index + 1}"
        nodes.append(
            MarkdownPreviewNode(
                temp_id=temp_id,
                parent_temp_id=parent_temp_id,
                title=title,
                level=level,
                markdown_content=direct_content,
                order_index=order_index,
            )
        )
        level_stack.append((level, temp_id))
    return nodes
