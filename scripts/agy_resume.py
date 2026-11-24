#!/usr/bin/env python3
"""Seletor interativo de conversas do Antigravity CLI (agy --resume).

Lê conversation_summaries.db em modo somente leitura, lista as conversas da CLI
(filtradas pelo diretório atual por padrão, com Tab para alternar para todas),
permite filtrar digitando e abre a conversa escolhida com `agy --conversation <id>`.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, TextIO
from urllib.parse import unquote

CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
ISO_FRAC_RE = re.compile(r"(\.\d{6})\d+")
PBTXT_TITLE_RE = re.compile(r'title\s*:\s*"((?:\\.|[^"\\])*)"')

SQLITE_TIMEOUT_SECONDS = 3.0
MAX_PREVIEW_CHARS = 240
MAX_TITLE_CHARS = 200

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
ENABLE_WINDOW_INPUT = 0x0008
KEY_EVENT = 0x0001
WINDOW_BUFFER_SIZE_EVENT = 0x0004
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
RESIZE_POLL_MS = 80
MAX_DRAIN_EVENTS = 256
ALT_SCREEN_ENTER = "\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H"
ALT_SCREEN_EXIT = "\x1b[0m\x1b[2J\x1b[H\x1b[?25h\x1b[?1049l"


@dataclass(frozen=True)
class ConversationItem:
    conversation_id: str
    title: str
    preview: str
    step_count: int
    last_modified: datetime
    workspaces: tuple[str, ...]


def sanitize_text(value: str, max_len: int = MAX_TITLE_CHARS) -> str:
    """Remove caracteres de controle (incluindo ESC/ANSI) e limita tamanho."""
    if not value:
        return ""
    cleaned = CONTROL_CHARS_RE.sub(" ", value)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > max_len:
        return cleaned[: max_len - 1] + "…"
    return cleaned


def fold_search(value: str) -> str:
    """Normaliza string para busca sem acento e sem diferenciar caixa."""
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_marks.casefold()


def normalize_win_path(raw_path: str) -> str:
    """Normaliza caminho local para comparação consistente no Windows."""
    if not raw_path:
        return ""
    # Padroniza separadores antes de normpath para funcionar de forma previsível
    path = raw_path.replace("/", "\\").strip()
    if len(path) >= 3 and path[0] == "\\" and path[2] == ":":
        path = path[1:]
    norm = os.path.normpath(path)
    # Remove barra final exceto raiz de drive (C:\)
    if len(norm) > 3 and norm.endswith("\\"):
        norm = norm.rstrip("\\")
    return os.path.normcase(norm)


def uri_to_norm_path(uri: str) -> str:
    """Converte file:///C:/... ou file:///c%3A/... em caminho normalizado."""
    if not uri:
        return ""
    text = uri.strip()
    if text.lower().startswith("file://"):
        text = text[7:]
        # file:///C:/... -> /C:/...
        text = unquote(text)
        if len(text) >= 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    else:
        text = unquote(text)
    return normalize_win_path(text)


def parse_timestamp(raw: str) -> datetime:
    """Interpreta timestamp ISO8601 com fração arbitrária (ex.: 7 dígitos do Go)."""
    if not raw:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    text = raw.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    text = ISO_FRAC_RE.sub(r"\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_workspace_uris(raw_json: str) -> tuple[str, ...]:
    """Lê JSON array de workspace_uris e devolve caminhos normalizados únicos."""
    if not raw_json:
        return ()
    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for entry in parsed:
        if not isinstance(entry, str):
            continue
        norm = uri_to_norm_path(entry)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return tuple(result)


def read_annotation_title(annotations_dir: Path, conversation_id: str) -> str:
    """Lê título customizado em annotations/<id>.pbtxt se existir."""
    if not CONVERSATION_ID_RE.match(conversation_id):
        return ""
    pbtxt = annotations_dir / f"{conversation_id}.pbtxt"
    try:
        content = pbtxt.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        return ""
    match = PBTXT_TITLE_RE.search(content)
    if not match:
        return ""
    raw_title = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    return sanitize_text(raw_title, MAX_TITLE_CHARS)


def load_conversations(base_dir: Path) -> list[ConversationItem]:
    """Carrega conversas válidas da CLI a partir de conversation_summaries.db."""
    db_path = base_dir / "conversation_summaries.db"
    if not db_path.is_file():
        return []

    conversations_dir = base_dir / "conversations"
    annotations_dir = base_dir / "annotations"

    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_SECONDS)
    try:
        cur = conn.cursor()
        rows = cur.execute(
            """
            SELECT conversation_id, title, preview, step_count, last_modified_time, workspace_uris
            FROM conversation_summaries
            WHERE app_data_dir = 'antigravity-cli'
              AND (parent_conversation_id IS NULL OR parent_conversation_id = '')
              AND killed = 0
            ORDER BY last_modified_time DESC
            """
        ).fetchall()
    finally:
        conn.close()

    items: list[ConversationItem] = []
    for cid, title, preview, step_count, last_mod, ws_json in rows:
        if not isinstance(cid, str) or not CONVERSATION_ID_RE.match(cid):
            continue
        if not (conversations_dir / f"{cid}.db").is_file():
            continue
        ann_title = read_annotation_title(annotations_dir, cid)
        clean_title = ann_title or sanitize_text(str(title or ""), MAX_TITLE_CHARS)
        clean_preview = sanitize_text(str(preview or ""), MAX_PREVIEW_CHARS)
        if not clean_title:
            clean_title = clean_preview or cid
        try:
            steps = max(0, int(step_count))
        except (TypeError, ValueError):
            steps = 0
        items.append(
            ConversationItem(
                conversation_id=cid,
                title=clean_title,
                preview=clean_preview,
                step_count=steps,
                last_modified=parse_timestamp(str(last_mod or "")),
                workspaces=parse_workspace_uris(str(ws_json or "")),
            )
        )

    items.sort(key=lambda item: item.last_modified, reverse=True)
    return items


def filter_conversations(
    items: Iterable[ConversationItem],
    cwd: str,
    show_all: bool,
    query: str,
) -> list[ConversationItem]:
    """Filtra conversas por diretório (se show_all=False) e por busca textual."""
    norm_cwd = normalize_win_path(cwd)
    folded_q = fold_search(query.strip())

    result: list[ConversationItem] = []
    for item in items:
        if not show_all and norm_cwd and norm_cwd not in item.workspaces:
            continue
        if folded_q:
            haystack = fold_search(
                f"{item.title} {item.preview} {item.conversation_id} {' '.join(item.workspaces)}"
            )
            if folded_q not in haystack:
                continue
        result.append(item)
    return result


def format_relative_time(dt: datetime, now: datetime) -> str:
    """Formata data relativa curta em pt-BR a partir de um relógio injetado."""
    local_tz = now.tzinfo or timezone.utc
    dt_local = dt.astimezone(local_tz)
    delta_s = int((now - dt_local).total_seconds())
    if delta_s < 60:
        return "agora"
    if delta_s < 3600:
        return f"há {delta_s // 60} min"
    if delta_s < 86400 and dt_local.date() == now.date():
        return f"hoje {dt_local.strftime('%H:%M')}"
    if (now.date() - dt_local.date()).days == 1:
        return f"ontem {dt_local.strftime('%H:%M')}"
    return dt_local.strftime("%d/%m %H:%M")


def shorten_path(path: str, home: str) -> str:
    """Encurta caminho substituindo o diretório do usuário por ~."""
    if not path:
        return ""
    norm_home = normalize_win_path(home)
    norm_path = normalize_win_path(path)
    if norm_home and norm_path == norm_home:
        return "~"
    if norm_home and norm_path.startswith(norm_home + "\\"):
        return "~" + path[len(home) :]
    return path


def char_width(ch: str) -> int:
    """Largura aproximada em células do terminal (2 para CJK/emojis largos, 0 para marcas)."""
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Largura visual aproximada do texto em colunas do terminal."""
    return sum(char_width(ch) for ch in text)


def truncate_line(text: str, width: int) -> str:
    """Trunca texto para caber na largura visual do terminal sem quebrar linha."""
    if width <= 1:
        return ""
    if display_width(text) <= width:
        return text
    out: list[str] = []
    used = 0
    limit = width - 1
    for ch in text:
        w = char_width(ch)
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def resolve_agy_executable() -> Path | None:
    """Localiza o binário agy.exe no caminho padrão ou no PATH."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        default_exe = Path(local_app_data) / "agy" / "bin" / "agy.exe"
        if default_exe.is_file():
            return default_exe
    found = shutil.which("agy")
    return Path(found) if found else None


class _WinInputRecord(ctypes.Structure):
    """Espelho de INPUT_RECORD (20 bytes) apenas para inspecionar EventType."""

    _fields_ = [
        ("EventType", ctypes.c_uint16),
        ("_pad", ctypes.c_uint16),
        ("Event", ctypes.c_byte * 16),
    ]


def _win_kernel32():
    """Instância dedicada de kernel32 com assinaturas corretas para Windows 64-bit."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetConsoleMode.restype = wintypes.BOOL
    k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.SetConsoleMode.restype = wintypes.BOOL
    k32.PeekConsoleInputW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WinInputRecord),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k32.PeekConsoleInputW.restype = wintypes.BOOL
    k32.ReadConsoleInputW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WinInputRecord),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k32.ReadConsoleInputW.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    return k32


def _is_valid_win_handle(handle: object) -> bool:
    if not handle:
        return False
    invalid = ctypes.c_void_p(-1).value
    return handle != invalid


def configure_windows_console() -> tuple[int | None, int | None]:
    """Habilita VT na saída e eventos de resize na entrada, devolvendo modos originais."""
    if os.name != "nt":
        return (None, None)
    orig_out: int | None = None
    orig_in: int | None = None
    try:
        k32 = _win_kernel32()
        out_handle = k32.GetStdHandle(STD_OUTPUT_HANDLE)
        if _is_valid_win_handle(out_handle):
            mode = wintypes.DWORD()
            if k32.GetConsoleMode(out_handle, ctypes.byref(mode)):
                orig_out = int(mode.value)
                k32.SetConsoleMode(out_handle, orig_out | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        in_handle = k32.GetStdHandle(STD_INPUT_HANDLE)
        if _is_valid_win_handle(in_handle):
            mode = wintypes.DWORD()
            if k32.GetConsoleMode(in_handle, ctypes.byref(mode)):
                orig_in = int(mode.value)
                k32.SetConsoleMode(in_handle, orig_in | ENABLE_WINDOW_INPUT)
    except OSError:
        # Console redirecionado ou sem suporte VT; continua degradando limpo
        return (orig_out, orig_in)
    return (orig_out, orig_in)


def restore_windows_console(orig_out: int | None, orig_in: int | None) -> None:
    """Restaura os modos originais do console antes de passar o controle ao agy.exe."""
    if os.name != "nt" or (orig_out is None and orig_in is None):
        return
    try:
        k32 = _win_kernel32()
        if orig_out is not None:
            out_handle = k32.GetStdHandle(STD_OUTPUT_HANDLE)
            if _is_valid_win_handle(out_handle):
                k32.SetConsoleMode(out_handle, orig_out)
        if orig_in is not None:
            in_handle = k32.GetStdHandle(STD_INPUT_HANDLE)
            if _is_valid_win_handle(in_handle):
                k32.SetConsoleMode(in_handle, orig_in)
    except OSError:
        return


def get_live_terminal_size(fallback: tuple[int, int] = (100, 30)) -> tuple[int, int]:
    """Lê o tamanho atual diretamente do console (ignora COLUMNS/LINES congelados do env)."""
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):
        size = shutil.get_terminal_size(fallback)
    else:
        if size.columns <= 0 or size.lines <= 0:
            size = shutil.get_terminal_size(fallback)
    return (size.columns, size.lines)


def _drain_non_key_console_events(k32: object, in_handle: object) -> bool:
    """Consome eventos não-tecla do topo da fila do console sem tocar em KEY_EVENT."""
    saw_resize = False
    rec = _WinInputRecord()
    read_count = wintypes.DWORD(0)
    for _ in range(MAX_DRAIN_EVENTS):
        if not k32.PeekConsoleInputW(in_handle, ctypes.byref(rec), 1, ctypes.byref(read_count)):
            break
        if read_count.value == 0 or rec.EventType == KEY_EVENT:
            break
        if not k32.ReadConsoleInputW(in_handle, ctypes.byref(rec), 1, ctypes.byref(read_count)):
            break
        if rec.EventType == WINDOW_BUFFER_SIZE_EVENT:
            saw_resize = True
    return saw_resize


def read_key() -> tuple[str, str]:
    """Lê uma tecla do console Windows e devolve (tipo, caractere)."""
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        special_map = {
            "H": "UP",
            "P": "DOWN",
            "I": "PGUP",
            "Q": "PGDN",
            "G": "HOME",
            "O": "END",
        }
        return (special_map.get(code, "IGNORED"), "")
    if ch in ("\r", "\n"):
        return ("ENTER", "")
    if ch == "\x1b":
        return ("ESC", "")
    if ch == "\t":
        return ("TAB", "")
    if ch == "\x08":
        return ("BACKSPACE", "")
    if ch == "\x03":
        return ("CTRL_C", "")
    if ch == "\x0e":
        return ("CTRL_N", "")
    if ch.isprintable():
        return ("CHAR", ch)
    return ("IGNORED", "")


def read_event(
    last_size: tuple[int, int],
    get_size_fn: Callable[[], tuple[int, int]] = get_live_terminal_size,
    poll_ms: int = RESIZE_POLL_MS,
) -> tuple[str, str]:
    """Aguarda uma tecla ou redimensionamento do terminal sem busy-wait."""
    if os.name != "nt":
        return read_key()

    import msvcrt

    try:
        k32 = _win_kernel32()
        in_handle = k32.GetStdHandle(STD_INPUT_HANDLE)
    except OSError:
        return read_key()

    if not _is_valid_win_handle(in_handle):
        return read_key()

    while True:
        if get_size_fn() != last_size:
            return ("RESIZE", "")
        if msvcrt.kbhit():
            return read_key()
        if _drain_non_key_console_events(k32, in_handle):
            return ("RESIZE", "")
        if msvcrt.kbhit():
            return read_key()
        wait = k32.WaitForSingleObject(in_handle, poll_ms)
        if wait not in (WAIT_OBJECT_0, WAIT_TIMEOUT):
            return read_key()


def render_picker(
    items: list[ConversationItem],
    selected: int,
    scroll: int,
    query: str,
    show_all: bool,
    cwd: str,
    home: str,
    total_in_scope: int,
    total_all: int,
    now: datetime,
    term_size: tuple[int, int],
    full_clear: bool = False,
) -> tuple[str, int]:
    """Monta a tela ANSI do seletor com exatamente `rows` linhas e devolve (texto, novo_scroll)."""
    cols, rows = term_size
    cols = max(20, cols)
    rows = max(8, rows)
    line_width = max(10, cols - 1)

    header_lines = 5
    footer_lines = 2
    list_height = max(1, rows - header_lines - footer_lines)

    if selected < scroll:
        scroll = selected
    elif selected >= scroll + list_height:
        scroll = selected - list_height + 1
    max_scroll = max(0, len(items) - list_height)
    scroll = max(0, min(scroll, max_scroll))

    scope_label = (
        f"Todas as pastas ({total_all})"
        if show_all
        else f"{shorten_path(cwd, home)} ({total_in_scope} nesta pasta / {total_all} no total)"
    )

    rule = "─" * line_width
    lines: list[str] = [
        "\x1b[1;36m" + truncate_line("Agy CLI — Retomar conversa", line_width) + "\x1b[0m\x1b[K",
        "\x1b[2m" + truncate_line(scope_label, line_width) + "\x1b[0m\x1b[K",
        truncate_line(
            f"Buscar: {query}▌" if query else "Buscar: (digite para filtrar)",
            line_width,
        )
        + "\x1b[K",
        f"\x1b[2m{rule}\x1b[0m\x1b[K",
        "\x1b[K",
    ]

    if not items:
        if query:
            msg = f"Nenhuma conversa encontrada para \"{query}\". Esc limpa a busca."
        elif not show_all and total_all > 0:
            msg = "Nenhuma conversa nesta pasta. Aperte Tab para ver todas ou Ctrl+N para nova conversa."
        else:
            msg = "Ainda não há conversas salvas do Agy CLI. Aperte Enter ou Ctrl+N para iniciar uma nova."
        lines.append("\x1b[33m" + truncate_line("  " + msg, line_width) + "\x1b[0m\x1b[K")
        for _ in range(list_height - 1):
            lines.append("\x1b[K")
    else:
        visible = items[scroll : scroll + list_height]
        for offset, item in enumerate(visible):
            idx = scroll + offset
            is_sel = idx == selected
            when = format_relative_time(item.last_modified, now)
            steps = f"{item.step_count}p"
            ws_suffix = ""
            if show_all and item.workspaces:
                ws_suffix = f"  [{shorten_path(item.workspaces[0], home)}]"
            meta = f"{when} · {steps}{ws_suffix}"
            prefix = "❯ " if is_sel else "  "
            prefix_w = display_width(prefix)
            meta_w = display_width(meta)
            avail_title = max(8, line_width - prefix_w - meta_w - 2)
            title_part = truncate_line(item.title, avail_title)
            gap = max(1, line_width - prefix_w - display_width(title_part) - meta_w)
            line = truncate_line(f"{prefix}{title_part}{' ' * gap}{meta}", line_width)
            if is_sel:
                lines.append(f"\x1b[7m{line}\x1b[0m\x1b[K")
            else:
                lines.append(f"{line}\x1b[K")
        for _ in range(list_height - len(visible)):
            lines.append("\x1b[K")

    lines.append(f"\x1b[2m{rule}\x1b[0m\x1b[K")
    footer = "↑/↓ mover  Enter abrir  Tab pasta/todas  Ctrl+N nova  Esc limpar/sair"
    lines.append("\x1b[2m" + truncate_line(footer, line_width) + "\x1b[0m\x1b[K")

    prefix_seq = "\x1b[2J\x1b[H" if full_clear else "\x1b[H"
    return (prefix_seq + "\r\n".join(lines) + "\x1b[J", scroll)


def run_interactive_picker(
    all_items: list[ConversationItem],
    cwd: str,
    home: str,
    clock: Callable[[], datetime],
    read_event_fn: Callable[[tuple[int, int]], tuple[str, str]] | None = None,
    get_size_fn: Callable[[], tuple[int, int]] | None = None,
    stream: TextIO | None = None,
) -> tuple[str, str | None]:
    """Executa o seletor interativo no alternate screen buffer.

    Devolve:
      ("open", conversation_id) — abrir conversa existente
      ("new", None)             — iniciar nova conversa
      ("cancel", None)          — usuário cancelou
    """
    out = sys.stdout if stream is None else stream
    size_fn = get_live_terminal_size if get_size_fn is None else get_size_fn
    event_fn = (lambda last: read_event(last, size_fn)) if read_event_fn is None else read_event_fn

    use_real_console = stream is None and read_event_fn is None
    orig_out, orig_in = configure_windows_console() if use_real_console else (None, None)

    show_all = False
    query = ""
    selected = 0
    scroll = 0
    last_size: tuple[int, int] | None = None

    total_all = len(all_items)
    total_in_scope = len(filter_conversations(all_items, cwd, show_all=False, query=""))

    out.write(ALT_SCREEN_ENTER)
    out.flush()
    try:
        while True:
            filtered = filter_conversations(all_items, cwd, show_all=show_all, query=query)
            if filtered:
                selected = max(0, min(selected, len(filtered) - 1))
            else:
                selected = 0

            current_size = size_fn()
            needs_full_clear = last_size is None or current_size != last_size
            last_size = current_size

            frame, scroll = render_picker(
                items=filtered,
                selected=selected,
                scroll=scroll,
                query=query,
                show_all=show_all,
                cwd=cwd,
                home=home,
                total_in_scope=total_in_scope,
                total_all=total_all,
                now=clock(),
                term_size=current_size,
                full_clear=needs_full_clear,
            )
            out.write(frame)
            out.flush()

            key, ch = event_fn(last_size)
            if key == "RESIZE":
                continue
            if key == "CTRL_C":
                return ("cancel", None)
            if key == "ESC":
                if query:
                    query = ""
                    selected = 0
                    scroll = 0
                else:
                    return ("cancel", None)
            elif key == "TAB":
                show_all = not show_all
                selected = 0
                scroll = 0
            elif key == "CTRL_N":
                return ("new", None)
            elif key == "ENTER":
                if filtered:
                    return ("open", filtered[selected].conversation_id)
                if not query and total_all == 0:
                    return ("new", None)
            elif key == "UP" and filtered:
                selected = (selected - 1) % len(filtered)
            elif key == "DOWN" and filtered:
                selected = (selected + 1) % len(filtered)
            elif key == "PGUP" and filtered:
                selected = max(0, selected - 10)
            elif key == "PGDN" and filtered:
                selected = min(len(filtered) - 1, selected + 10)
            elif key == "HOME":
                selected = 0
            elif key == "END" and filtered:
                selected = len(filtered) - 1
            elif key == "BACKSPACE":
                if query:
                    query = query[:-1]
                    selected = 0
                    scroll = 0
            elif key == "CHAR" and len(query) < 120:
                query += ch
                selected = 0
                scroll = 0
    finally:
        out.write(ALT_SCREEN_EXIT)
        out.flush()
        if use_real_console:
            restore_windows_console(orig_out, orig_in)


def build_agy_command(
    agy_exe: Path,
    extra_args: list[str],
    conversation_id: str | None,
) -> list[str]:
    """Monta argv seguro (lista, sem shell) para chamar o agy.exe."""
    cmd = [str(agy_exe), *extra_args]
    if conversation_id is not None:
        if not CONVERSATION_ID_RE.match(conversation_id):
            raise ValueError(f"ID de conversa inválido: {conversation_id!r}")
        cmd.extend(["--conversation", conversation_id])
    return cmd


def parse_cli_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Separa opções do seletor dos argumentos extras repassados ao agy."""
    if "--" in argv:
        idx = argv.index("--")
        own_argv = argv[:idx]
        agy_args = argv[idx + 1 :]
    else:
        own_argv = argv
        agy_args = []

    parser = argparse.ArgumentParser(
        description="Lista conversas do Antigravity CLI e abre a escolhida."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Apenas imprime a lista de conversas (não interativo) e sai.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Com --list, mostra conversas de todas as pastas.",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Diretório de referência para filtrar conversas (padrão: diretório atual).",
    )
    ns = parser.parse_args(own_argv)
    return ns, agy_args


def system_clock() -> datetime:
    """Relógio padrão com fuso local explícito a partir de UTC."""
    return datetime.now(timezone.utc).astimezone()


def main(
    argv: list[str] | None = None,
    clock: Callable[[], datetime] = system_clock,
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ns, agy_args = parse_cli_args(sys.argv[1:] if argv is None else argv)
    home = os.environ.get("USERPROFILE") or str(Path.home())
    base_dir = Path(home) / ".gemini" / "antigravity-cli"
    cwd = ns.dir or os.getcwd()
    now_fn = clock

    try:
        all_items = load_conversations(base_dir)
    except sqlite3.Error as exc:
        print(f"Erro ao ler histórico do Agy CLI: {exc}", file=sys.stderr)
        all_items = []

    if ns.list:
        filtered = filter_conversations(all_items, cwd, show_all=ns.all, query="")
        if not filtered:
            print("Nenhuma conversa encontrada.")
            return 0
        now = now_fn()
        for item in filtered:
            when = format_relative_time(item.last_modified, now)
            ws = shorten_path(item.workspaces[0], home) if item.workspaces else ""
            print(f"{item.conversation_id}  {when:<12}  {item.step_count:>3}p  {item.title}  [{ws}]")
        return 0

    agy_exe = resolve_agy_executable()
    if agy_exe is None:
        print("Erro: agy.exe não encontrado em %LOCALAPPDATA%\\agy\\bin nem no PATH.", file=sys.stderr)
        return 1

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("Terminal interativo necessário. Use --list para saída em texto.", file=sys.stderr)
        return 2

    action, conv_id = run_interactive_picker(all_items, cwd, home, now_fn)
    if action == "cancel":
        return 0

    cmd = build_agy_command(agy_exe, agy_args, conv_id if action == "open" else None)
    completed = subprocess.run(cmd, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
