import json
from pathlib import Path

src = Path(
    r"C:\Users\lizko\.cursor\projects\c-3-1-wf-grid\agent-transcripts"
    r"\116c37ae-8af8-4cb9-a07e-f17a3cfbfa37\116c37ae-8af8-4cb9-a07e-f17a3cfbfa37.jsonl"
)
out = Path(r"C:\3.1_wf_grid\docs\session_dialog_20260618_wf_grid_optimization.md")

lines = []
lines.append("# Диалог: инженерный анализ wf_grid / volume strategy")
lines.append("")
lines.append("> Полный экспорт сессии. Сохранено символ в символ из agent transcript.")
lines.append("")

turn = 0
for raw in src.read_text(encoding="utf-8").splitlines():
    if not raw.strip():
        continue
    obj = json.loads(raw)
    role = obj.get("role")
    msg = obj.get("message", {})
    parts = msg.get("content", [])
    texts = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            texts.append(p.get("text", ""))
    if not texts:
        continue
    body = "\n".join(texts)
    turn += 1
    if role == "user":
        lines.append(f"## Пользователь — сообщение {turn}")
        lines.append("")
        lines.append(body)
        lines.append("")
    elif role == "assistant":
        lines.append(f"## Ассистент — сообщение {turn}")
        lines.append("")
        lines.append(body)
        lines.append("")
        lines.append("---")
        lines.append("")

out.write_text("\n".join(lines), encoding="utf-8")
print(f"written: {out}")
print(f"bytes: {out.stat().st_size}")
print(f"lines: {len(lines)}")
