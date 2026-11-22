"""Turn plain text or Markdown into a clean, self-contained HTML page ready for Edge's print-to-PDF.

Security: all input text is HTML-escaped before any Markdown formatting is applied, and the output page sets
a strict Content-Security-Policy ("default-src 'none'; style-src 'unsafe-inline'") so printing a PDF can never
run scripts, fetch web addresses, or read local files.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

CSS = """
@page { size: auto; margin: 18mm; }
body {
  font-family: "Segoe UI", -apple-system, Arial, sans-serif;
  font-size: 11pt;
  line-height: 1.55;
  color: #1c1f24;
  margin: 0;
}
h1, h2, h3, h4 {
  color: #163a5f;
  margin-top: 1.25em;
  margin-bottom: 0.45em;
  line-height: 1.25;
  break-after: avoid;
}
h1 { font-size: 20pt; border-bottom: 1.5pt solid #d0d7de; padding-bottom: 0.25em; margin-top: 0; }
h2 { font-size: 14.5pt; }
h3 { font-size: 12pt; }
h4 { font-size: 11pt; }
p { margin: 0 0 0.75em 0; }
ul, ol { margin: 0 0 0.85em 0; padding-left: 1.5em; }
li { margin-bottom: 0.25em; }
blockquote {
  margin: 0.8em 0;
  padding: 0.4em 0.9em;
  border-left: 3pt solid #7a9ec2;
  background: #f5f8fb;
  color: #364152;
  break-inside: avoid;
}
code {
  font-family: Consolas, "Courier New", monospace;
  font-size: 9.5pt;
  background: #f1f3f5;
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
pre {
  background: #f4f6f8;
  border: 1px solid #d8dee4;
  border-radius: 4px;
  padding: 0.75em 0.9em;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  break-inside: avoid;
  margin: 0 0 0.85em 0;
}
pre code { background: none; padding: 0; }
hr { border: none; border-top: 1px solid #d0d7de; margin: 1.2em 0; }
table { width: 100%; border-collapse: collapse; margin: 0.85em 0; font-size: 10pt; }
th, td { border: 1px solid #cfd6de; padding: 6px 8px; text-align: left; vertical-align: top; }
th { background: #eef3f8; color: #163a5f; font-weight: 600; }
tr { break-inside: avoid; }
"""

_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_HEADING_RE = re.compile(r"^\s*(#{1,4})\s+(.+?)\s*#*\s*$")
_UL_RE = re.compile(r"^\s*[-*•]\s+(.+)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _inline(text: str) -> str:
    """Escape HTML first, then apply safe inline Markdown (code, bold, italic, link text)."""
    escaped = html.escape(text, quote=True)
    code_spans: list[str] = []

    def stash_code(match: re.Match) -> str:
        code_spans.append(f"<code>{match.group(1)}</code>")
        return f"\x00CODE{len(code_spans) - 1}\x00"

    escaped = re.sub(r"`([^`]+)`", stash_code, escaped)
    escaped = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", escaped)
    escaped = re.sub(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", r"<strong><em>\1</em></strong>", escaped)
    escaped = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\s|\*)(.+?)(?<!\s|\*)\*(?!\*)", r"<em>\1</em>", escaped)
    for idx, snippet in enumerate(code_spans):
        escaped = escaped.replace(f"\x00CODE{idx}\x00", snippet)
    return escaped


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _starts_block(lines: list[str], idx: int) -> bool:
    line = lines[idx]
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("```") or stripped.startswith(">"):
        return True
    if _HEADING_RE.match(line) or _HR_RE.match(line) or _UL_RE.match(line) or _OL_RE.match(line):
        return True
    if "|" in line and idx + 1 < len(lines) and _TABLE_SEP_RE.match(lines[idx + 1]):
        return True
    return False


def _body_html(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            headers = _table_cells(line)
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_table_cells(lines[i]))
                i += 1
            head_html = "".join(f"<th>{_inline(c)}</th>" for c in headers)
            body_rows = []
            for row in rows:
                padded = row + [""] * max(0, len(headers) - len(row))
                body_rows.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in padded[:len(headers)]) + "</tr>")
            out.append(f"<table><thead><tr>{head_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>")
            continue
        if stripped.startswith(">"):
            quote_lines = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote><p>" + "<br>".join(_inline(q) for q in quote_lines) + "</p></blockquote>")
            continue
        if _UL_RE.match(line):
            items = []
            while i < n:
                m = _UL_RE.match(lines[i])
                if not m:
                    break
                items.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        if _OL_RE.match(line):
            items = []
            while i < n:
                m = _OL_RE.match(lines[i])
                if not m:
                    break
                items.append(f"<li>{_inline(m.group(1))}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue
        para = [stripped]
        i += 1
        while i < n and not _starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
    return "\n".join(out)


def markdown_to_html(text: str, title: str = "") -> str:
    """Build a complete, self-contained HTML5 document ready for headless Edge print-to-PDF."""
    clean_title = (title or "").strip()
    body = text or ""
    first_nonempty = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    first_h1 = _HEADING_RE.match(first_nonempty)
    already_titled = bool(clean_title and first_h1 and len(first_h1.group(1)) == 1
                          and first_h1.group(2).strip().lower() == clean_title.lower())
    title_block = f"<h1>{html.escape(clean_title)}</h1>\n" if clean_title and not already_titled else ""
    doc_title = html.escape(clean_title or (first_h1.group(2).strip() if first_h1 else "Document"))
    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline';\">\n"
        f"<title>{doc_title}</title>\n"
        f"<style>{CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{title_block}{_body_html(body)}\n"
        "</body>\n"
        "</html>\n"
    )


# --- reading PDF and Office files with the standard library only ------------------------------------

def _parse_cmap(stream_bytes: bytes) -> dict[int, str]:
    cmap: dict[int, str] = {}
    txt = stream_bytes.decode("latin-1", "ignore")
    for block in re.findall(r"beginbfchar(.*?)endbfchar", txt, re.S):
        for src_h, dst_h in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            try:
                cmap[int(src_h, 16)] = bytes.fromhex(dst_h).decode("utf-16-be", "ignore")
            except ValueError:
                pass
    for block in re.findall(r"beginbfrange(.*?)endbfrange", txt, re.S):
        for m_r in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(?:<([0-9A-Fa-f]+)>|\[(.*?)\])", block, re.S):
            lo, hi = int(m_r.group(1), 16), int(m_r.group(2), 16)
            if m_r.group(3):
                try:
                    base = int(m_r.group(3), 16)
                    for off in range(min(512, hi - lo + 1)):
                        cmap[lo + off] = chr(base + off)
                except ValueError:
                    pass
            elif m_r.group(4):
                targets = re.findall(r"<([0-9A-Fa-f]+)>", m_r.group(4))
                for off, t_h in enumerate(targets[: max(0, hi - lo + 1)]):
                    try:
                        cmap[lo + off] = bytes.fromhex(t_h).decode("utf-16-be", "ignore")
                    except ValueError:
                        pass
    return cmap


def _unescape_pdf_bytes(b: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(b)
    while i < n:
        if b[i:i + 1] == b"\\" and i + 1 < n:
            nxt = b[i + 1:i + 2]
            if nxt == b"n":
                out.append(10)
                i += 2
                continue
            if nxt == b"r":
                out.append(13)
                i += 2
                continue
            if nxt == b"t":
                out.append(9)
                i += 2
                continue
            if nxt in (b"(", b")", b"\\"):
                out.extend(nxt)
                i += 2
                continue
            m_oct = re.match(rb"^([0-7]{1,3})", b[i + 1:i + 4])
            if m_oct:
                out.append(int(m_oct.group(1), 8) & 0xFF)
                i += 1 + len(m_oct.group(1))
                continue
            i += 1
        else:
            out.append(b[i])
            i += 1
    return bytes(out)


def _decode_pdf_token(raw_b: bytes, cmap: dict[int, str]) -> str:
    if raw_b.startswith(b"\xfe\xff"):
        return raw_b[2:].decode("utf-16-be", "ignore")
    if cmap:
        max_k = max(cmap)
        if max_k > 255 and len(raw_b) % 2 == 0:
            return "".join(cmap.get((raw_b[j] << 8) | raw_b[j + 1], "") for j in range(0, len(raw_b), 2))
        return "".join(cmap.get(b, chr(b)) for b in raw_b)
    return raw_b.decode("cp1252", "ignore")


def extract_pdf_text(path: Path) -> str:
    """Extract readable text from a PDF file using only Python's standard library (zlib + CMap + BT/ET parser)."""
    raw = path.read_bytes()
    obj_streams: dict[int, bytes] = {}
    obj_bodies: dict[int, bytes] = {}
    for m in re.finditer(rb"(\d+)\s+0\s+obj\b(.*?)endobj", raw, re.S):
        oid = int(m.group(1))
        body = m.group(2)
        obj_bodies[oid] = body
        sm = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S)
        if sm:
            chunk = sm.group(1)
            for cand in (chunk, chunk.strip(b"\r\n")):
                try:
                    obj_streams[oid] = zlib.decompress(cand)
                    break
                except zlib.error:
                    obj_streams[oid] = chunk

    # Unpack compressed PDF 1.5 object streams (/Type /ObjStm) so font/ToUnicode links inside them are visible.
    for oid, body in list(obj_bodies.items()):
        if b"/ObjStm" in body and oid in obj_streams:
            m_first = re.search(rb"/First\s+(\d+)", body)
            if not m_first:
                continue
            first = int(m_first.group(1))
            stm = obj_streams[oid]
            header_pairs = re.findall(rb"(\d+)\s+(\d+)", stm[:first])
            for idx, (sub_id_b, off_b) in enumerate(header_pairs):
                sub_id = int(sub_id_b)
                start = first + int(off_b)
                end = first + int(header_pairs[idx + 1][1]) if idx + 1 < len(header_pairs) else len(stm)
                if 0 <= start < end <= len(stm):
                    obj_bodies.setdefault(sub_id, stm[start:end])

    cmaps_by_obj = {oid: _parse_cmap(s) for oid, s in obj_streams.items() if b"beginbf" in s}
    font_obj_to_cmap: dict[int, dict[int, str]] = {}
    for oid, body in obj_bodies.items():
        tu = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", body)
        if tu and int(tu.group(1)) in cmaps_by_obj:
            font_obj_to_cmap[oid] = cmaps_by_obj[int(tu.group(1))]
    font_name_to_cmap: dict[str, dict[int, str]] = {}
    for body in obj_bodies.values():
        for m in re.finditer(rb"/([A-Za-z0-9_.-]+)\s+(\d+)\s+0\s+R", body):
            fname, foid = m.group(1).decode("ascii", "ignore"), int(m.group(2))
            if foid in font_obj_to_cmap:
                font_name_to_cmap[fname] = font_obj_to_cmap[foid]

    lines: list[str] = []
    token_re = re.compile(
        rb"/([A-Za-z0-9_.-]+)\s+[\d.]+\s+Tf|\[(.*?)\]\s*TJ|\(((?:\\.|[^\\()])*)\)\s*(?:Tj|'|\"\s*)"
        rb"|<([0-9A-Fa-f\s]+)>\s*Tj|\b(T\*|TD|Td|Tm)\b",
        re.S,
    )
    sub_re = re.compile(rb"\(((?:\\.|[^\\()])*)\)|<([0-9A-Fa-f\s]+)>|(-?\d+(?:\.\d+)?)")
    for oid, s in sorted(obj_streams.items()):
        if b"BT" not in s or b"beginbf" in s:
            continue
        cur_cmap: dict[int, str] = {}
        for bt in re.findall(rb"BT\b(.*?)\bET", s, re.S):
            cur: list[str] = []
            for tok in token_re.finditer(bt):
                if tok.group(1) is not None:
                    fname = tok.group(1).decode("ascii", "ignore")
                    cur_cmap = font_name_to_cmap.get(fname, {})
                elif tok.group(5) is not None:
                    if cur:
                        lines.append("".join(cur))
                        cur = []
                elif tok.group(2) is not None:
                    for sub in sub_re.finditer(tok.group(2)):
                        if sub.group(1) is not None:
                            cur.append(_decode_pdf_token(_unescape_pdf_bytes(sub.group(1)), cur_cmap))
                        elif sub.group(2) is not None:
                            hs = re.sub(rb"\s+", b"", sub.group(2)).decode("ascii", "ignore")
                            if len(hs) % 2:
                                hs += "0"
                            cur.append(_decode_pdf_token(bytes.fromhex(hs), cur_cmap))
                        elif sub.group(3) is not None:
                            try:
                                if float(sub.group(3)) < -120:
                                    cur.append(" ")
                            except ValueError:
                                pass
                elif tok.group(3) is not None:
                    cur.append(_decode_pdf_token(_unescape_pdf_bytes(tok.group(3)), cur_cmap))
                elif tok.group(4) is not None:
                    hs = re.sub(rb"\s+", b"", tok.group(4)).decode("ascii", "ignore")
                    if len(hs) % 2:
                        hs += "0"
                    cur.append(_decode_pdf_token(bytes.fromhex(hs), cur_cmap))
            if cur:
                lines.append("".join(cur))
    cleaned = []
    for ln in lines:
        norm = " ".join(ln.split())
        if norm:
            cleaned.append(norm)
    return "\n".join(cleaned)


def extract_office_text(path: Path) -> str:
    """Extract readable text from .docx, .pptx, .xlsx, or .odt files using stdlib zipfile + ElementTree."""
    ext = path.suffix.lower()
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            parts: list[str] = []
            if ext == ".docx" and "word/document.xml" in names:
                root = ET.fromstring(zf.read("word/document.xml"))
                for para in root.iter():
                    if para.tag.endswith("}p"):
                        text = "".join(t.text or "" for t in para.iter() if t.tag.endswith("}t")).strip()
                        if text:
                            parts.append(text)
            elif ext == ".pptx":
                slides = sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
                for idx, sname in enumerate(slides, 1):
                    root = ET.fromstring(zf.read(sname))
                    texts = [t.text.strip() for t in root.iter() if t.tag.endswith("}t") and (t.text or "").strip()]
                    if texts:
                        parts.append(f"[Slide {idx}]\n" + "\n".join(texts))
            elif ext == ".xlsx":
                shared: list[str] = []
                if "xl/sharedStrings.xml" in names:
                    sroot = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                    for si in sroot.iter():
                        if si.tag.endswith("}si"):
                            shared.append("".join(t.text or "" for t in si.iter() if t.tag.endswith("}t")))
                sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
                for sname in sheets:
                    root = ET.fromstring(zf.read(sname))
                    for row in root.iter():
                        if row.tag.endswith("}row"):
                            cells = []
                            for c in row:
                                if not c.tag.endswith("}c"):
                                    continue
                                kind = c.attrib.get("t", "")
                                val = next((v.text or "" for v in c if v.tag.endswith("}v")), "")
                                if kind == "s" and val.isdigit() and int(val) < len(shared):
                                    val = shared[int(val)]
                                if val:
                                    cells.append(val)
                            if cells:
                                parts.append("\t".join(cells))
            elif ext == ".odt" and "content.xml" in names:
                root = ET.fromstring(zf.read("content.xml"))
                for elem in root.iter():
                    if elem.tag.endswith(("}p", "}h")):
                        text = "".join(elem.itertext()).strip()
                        if text:
                            parts.append(text)
            return "\n".join(parts)
    except (zipfile.BadZipFile, ET.ParseError, OSError, KeyError):
        return ""
