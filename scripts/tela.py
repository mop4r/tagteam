"""Terminal sem dependencias para o tagteam.py: cores ANSI, teclas pelo msvcrt, tela alternativa e idioma.

So a biblioteca padrao (o curses nao vem no Python do Windows). Onde nao ha console interativo do Windows (pipe,
outro sistema), o tagteam.py usa o modo simples, de perguntas numeradas, e nada daqui que le teclas e chamado.
"""
import os
import re
import shutil
import sys
import time

ESC = "\x1b["
CORES = {"verde": "32", "amarelo": "33", "vermelho": "31", "ciano": "36", "fraco": "2", "negrito": "1", "inverso": "7"}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SETAS = {"H": "cima", "P": "baixo", "K": "esq", "M": "dir", "S": "del"}
TECLAS = {"\r": "enter", "\n": "enter", " ": "espaco", "\x1b": "esc", "\x03": "sair", "\x08": "apagar", "\t": "tab"}
PAUSA_TECLA_S = 0.05


def ligar_vt():
    """Liga as sequencias ANSI no console do Windows 10+ (ENABLE_VIRTUAL_TERMINAL_PROCESSING). True se deu."""
    if os.name != "nt":
        return sys.stdout.isatty()
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        modo = ctypes.c_uint32()
        if not k.GetConsoleMode(h, ctypes.byref(modo)):
            return False
        return bool(k.SetConsoleMode(h, modo.value | 0x0004))
    except (AttributeError, OSError):
        return False


def interativo():
    """Console do Windows de verdade, com teclas e cores: usa a tela cheia. Senao, o modo simples."""
    return os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty() and ligar_vt()


def idioma_do_sistema():
    """"pt" se o Windows estiver em portugues, senao "en"."""
    try:
        import ctypes
        return "pt" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF == 0x16 else "en"
    except (AttributeError, OSError):
        lang = os.environ.get("LANG", "")
        return "pt" if lang.lower().startswith("pt") else "en"


def cor(texto, *nomes):
    return f"{ESC}{';'.join(CORES[n] for n in nomes)}m{texto}{ESC}0m" if nomes else str(texto)


def visivel(texto):
    return len(ANSI_RE.sub("", texto))


def cortar(texto, largura):
    """Corta no tamanho visivel (as sequencias de cor nao contam) e fecha a cor se cortou no meio."""
    if visivel(texto) <= largura:
        return texto
    saida, n, i = [], 0, 0
    while i < len(texto) and n < max(0, largura - 1):
        m = ANSI_RE.match(texto, i)
        if m:
            saida.append(m.group())
            i = m.end()
            continue
        saida.append(texto[i])
        n += 1
        i += 1
    return "".join(saida) + "…" + f"{ESC}0m"


def tamanho():
    t = shutil.get_terminal_size((100, 30))
    return max(40, t.columns), max(15, t.lines)


class Tela:
    """Tela alternativa (o historico do terminal volta igual ao sair) e cursor escondido."""

    def __enter__(self):
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        return self

    def __exit__(self, *_):
        sys.stdout.write(f"{ESC}0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()

    @staticmethod
    def desenhar(linhas):
        largura, altura = tamanho()
        corpo = [cortar(linha, largura - 1) + f"{ESC}K" for linha in linhas[:altura - 1]]
        sys.stdout.write(f"{ESC}H" + "\n".join(corpo) + f"\n{ESC}J")
        sys.stdout.flush()


def tecla(espera=None, relogio=time.monotonic):
    """Le uma tecla: "cima", "baixo", "esq", "dir", "enter", "espaco", "esc", "sair", "apagar", "del", "tab" ou o
    caractere em minuscula. espera (s): devolve None se nada for apertado nesse tempo (o monitor se atualiza)."""
    import msvcrt
    if espera is not None:
        prazo = relogio() + espera
        while not msvcrt.kbhit():
            if relogio() >= prazo:
                return None
            time.sleep(PAUSA_TECLA_S)    # sleep proposital: espera de tecla com prazo (msvcrt nao bloqueia com prazo)
    c = msvcrt.getwch()
    if c in ("\x00", "\xe0"):
        return SETAS.get(msvcrt.getwch(), "?")
    return TECLAS.get(c, c.lower())


def perguntar(texto):
    """Pergunta com o cursor visivel (input normal). Ctrl+C ou fim da entrada: None."""
    sys.stdout.write("\x1b[?25h")
    try:
        return input(texto)
    except (EOFError, KeyboardInterrupt):
        return None
    finally:
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()


def barra(feito, total, largura=30):
    cheio = int(largura * feito / total) if total else largura
    return "█" * cheio + "░" * (largura - cheio)
