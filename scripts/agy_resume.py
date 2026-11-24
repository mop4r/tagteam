#!/usr/bin/env python3
"""Seletor interativo de conversas do Antigravity CLI (agy --resume).

Lê conversation_summaries.db em modo somente leitura, lista as conversas da CLI
(filtradas pelo diretório atual por padrão, com Tab para alternar para todas),
permite filtrar digitando e abre a conversa escolhida com `agy --conversation <id>`.
"""

from __future__ import annotations

import argparse
import ctypes
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
from typing import Callable, Iterable
from urllib.parse import unquote

CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
ISO_FRAC_RE = re.compile(r"(\.\d{6})\d+")
PBTXT_TITLE_RE = re.compile(r'title\s*:\s*"((?:\\.|[^"\\])*)"')

SQLITE_TIMEOUT_SECONDS = 3.0
MAX_PREVIEW_CHARS = 240
MAX_TITLE_CHARS = 200


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


def truncate_line(text: str, width: int) -> str:
    """Trunca texto para caber na largura do terminal."""
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def resolve_agy_executable() -> Path | None:
    """Localiza o binário agy.exe no caminho padrão ou no PATH."""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        default_exe = Path(local_app_data) / "agy" / "bin" / "agy.exe"
        if default_exe.is_file():
            return default_exe
    found = shutil.which("agy")
    return Path(found) if found else None


def enable_windows_vt_mode() -> None:
    """Habilita sequências ANSI no console do Windows (no-op fora do Windows)."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        # Console redirecionado ou sem suporte VT; continua degradando limpo
        return


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
) -> tuple[str, int]:
    """Monta a tela ANSI do seletor e devolve (texto, novo_scroll)."""
    cols, rows = term_size
    cols = max(40, cols)
    rows = max(12, rows)

    header_lines = 5
    footer_lines = 2
    list_height = max(3, rows - header_lines - footer_lines)

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

    out: list[str] = ["\x1b[H\x1b[2J"]
    out.append(
        "\x1b[1;36m"
        + truncate_line("Agy CLI — Retomar conversa", cols)
        + "\x1b[0m"
    )
    out.append("\x1b[2m" + truncate_line(scope_label, cols) + "\x1b[0m")
    search_text = f"Buscar: {query}▌" if query else "Buscar: (digite para filtrar)"
    out.append(truncate_line(search_text, cols))
    out.append("\x1b[2m" + ("─" * min(cols, 100)) + "\x1b[0m")
    out.append("")

    if not items:
        if query:
            msg = f"Nenhuma conversa encontrada para \"{query}\". Esc limpa a busca."
        elif not show_all and total_all > 0:
            msg = "Nenhuma conversa nesta pasta. Aperte Tab para ver todas ou Ctrl+N para nova conversa."
        else:
            msg = "Ainda não há conversas salvas do Agy CLI. Aperte Enter ou Ctrl+N para iniciar uma nova."
        out.append("\x1b[33m" + truncate_line("  " + msg, cols) + "\x1b[0m")
        for _ in range(list_height - 1):
            out.append("")
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
            avail_title = max(12, cols - len(prefix) - len(meta) - 3)
            title_part = truncate_line(item.title, avail_title)
            gap = max(1, cols - len(prefix) - len(title_part) - len(meta) - 1)
            line = f"{prefix}{title_part}{' ' * gap}{meta}"
            line = truncate_line(line, cols)
            if is_sel:
                out.append(f"\x1b[7m{line}\x1b[0m")
            else:
                out.append(line)
        for _ in range(list_height - len(visible)):
            out.append("")

    out.append("\x1b[2m" + ("─" * min(cols, 100)) + "\x1b[0m")
    footer = "↑/↓ mover  Enter abrir  Tab pasta/todas  Ctrl+N nova  Esc limpar/sair"
    out.append("\x1b[2m" + truncate_line(footer, cols) + "\x1b[0m")
    return ("\r\n".join(out), scroll)


def run_interactive_picker(
    all_items: list[ConversationItem],
    cwd: str,
    home: str,
    clock: Callable[[], datetime],
) -> tuple[str, str | None]:
    """Executa o seletor interativo.

    Devolve:
      ("open", conversation_id) — abrir conversa existente
      ("new", None)             — iniciar nova conversa
      ("cancel", None)          — usuário cancelou
    """
    enable_windows_vt_mode()
    show_all = False
    query = ""
    selected = 0
    scroll = 0

    total_all = len(all_items)
    total_in_scope = len(filter_conversations(all_items, cwd, show_all=False, query=""))

    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    try:
        while True:
            filtered = filter_conversations(all_items, cwd, show_all=show_all, query=query)
            if filtered:
                selected = max(0, min(selected, len(filtered) - 1))
            else:
                selected = 0

            size = shutil.get_terminal_size((100, 30))
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
                term_size=(size.columns, size.lines),
            )
            sys.stdout.write(frame)
            sys.stdout.flush()

            key, ch = read_key()
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
        sys.stdout.write("\x1b[?25h\x1b[H\x1b[2J")
        sys.stdout.flush()


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
