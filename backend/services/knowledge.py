"""Search backend/knowledge/*.txt|md|pdf by word overlap. Returns passages or nothing."""
import re
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge"
STOP = {"the", "a", "an", "is", "are", "do", "does", "you", "your", "i", "my", "me", "can", "to", "of",
        "in", "on", "at", "for", "and", "or", "what", "how", "when", "where", "which", "it", "we", "there",
        "have", "has", "any", "with", "be", "this", "that", "please", "tell", "about"}


def _tok(s: str) -> set[str]:
    return {w for w in re.findall(r"\w+", s.lower()) if w not in STOP and len(w) > 1}


def _read(path: Path) -> str:
    if path.suffix.lower() in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except Exception:
            return ""
    return ""


def _chunks() -> list[tuple[str, str]]:
    out = []
    if not KB_DIR.exists():
        return out
    for f in sorted(KB_DIR.iterdir()):
        text = _read(f)
        heading = ""
        for block in re.split(r"\n\s*\n", text):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#") and "\n" not in block:
                heading = block.lstrip("# ").strip()
                continue
            out.append((f.name, (heading + ": " if heading else "") + block))
    return out


def search(query: str, limit: int = 3) -> list[dict]:
    q = _tok(query)
    if not q:
        return []
    need = 1 if len(q) == 1 else 2
    scored = []
    for src, chunk in _chunks():
        score = len(q & _tok(chunk))
        if score >= need:
            scored.append((score, src, chunk[:600]))
    scored.sort(key=lambda x: -x[0])
    return [{"source": s, "text": c} for _, s, c in scored[:limit]]
