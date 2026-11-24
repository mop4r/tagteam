"""Status line do Claude Code: mostra contexto e uso dos limites, e grava o uso para a passagem automatica.

Recebe o JSON da sessao no stdin (rate_limits.five_hour/seven_day.used_percentage) e imprime uma linha curta.
Precisa ser rapido e nunca quebrar: qualquer erro vira uma linha vazia.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from passagem import LIMIAR  # noqa: E402


def linha(d):
    partes = []
    modelo = (d.get("model") or {}).get("display_name") if isinstance(d.get("model"), dict) else None
    if modelo:
        partes.append(str(modelo)[:30])
    ctx = (d.get("context_window") or {}).get("used_percentage") if isinstance(d.get("context_window"), dict) else None
    if isinstance(ctx, (int, float)):
        partes.append(f"contexto {ctx:.0f}%")
    rl = d.get("rate_limits") if isinstance(d.get("rate_limits"), dict) else {}
    for nome, rotulo in (("five_hour", "5h"), ("seven_day", "7d")):
        j = rl.get(nome)
        if isinstance(j, dict) and isinstance(j.get("used_percentage"), (int, float)):
            pct = j["used_percentage"]
            partes.append(f"{rotulo} {pct:.0f}%" + (" ⚠ passagem" if pct >= LIMIAR else ""))
    return " · ".join(partes)


def main():
    try:
        d = json.loads(sys.stdin.buffer.read(1024 * 1024).decode("utf-8-sig") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    if not isinstance(d, dict):
        return
    import passagem
    try:
        passagem.gravar_uso_claude(d, time.time())
        aviso = ""
    except OSError as e:         # sem gravar o uso so perde a passagem automatica; a linha avisa
        aviso = f" · uso nao gravado ({type(e).__name__})"
    sys.stdout.write(linha(d) + aviso)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - status line nunca pode quebrar a tela do Claude
        pass
