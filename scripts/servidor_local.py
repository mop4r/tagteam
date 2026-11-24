"""Fala com o servidor local do app Antigravity (language server, protocolo Connect em JSON).

Porta e token csrf vem do hub_<porta>.json do processo vivo em ~/.gemini/antigravity/daemon.
O token nunca e impresso nem gravado.
"""
import ctypes
import glob
import json
import os
import time
import urllib.error
import urllib.request

SERVICO = "exa.language_server_pb.LanguageServerService"
TIMEOUT_S = 20
TENTATIVAS_RECUSA = 3
PAUSA_RECUSA_S = 3
DONOS = ("antigravity.exe", "agy.exe")
_AINDA_RODANDO = 259


class ErroServidor(RuntimeError):
    pass


def _vivo(pid):
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        codigo = ctypes.c_ulong()
        return bool(k.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == _AINDA_RODANDO
    finally:
        k.CloseHandle(h)


class _Processo(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]


def _processos():
    """{pid: (pid do pai, nome do executavel em minusculas)}."""
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    foto = k.CreateToolhelp32Snapshot(0x2, 0)              # TH32CS_SNAPPROCESS
    if not foto or foto == ctypes.c_void_p(-1).value:
        return {}
    saida = {}
    try:
        p = _Processo()
        p.dwSize = ctypes.sizeof(_Processo)
        ok = k.Process32FirstW(ctypes.c_void_p(foto), ctypes.byref(p))
        while ok:
            saida[p.th32ProcessID] = (p.th32ParentProcessID, p.szExeFile.lower())
            ok = k.Process32NextW(ctypes.c_void_p(foto), ctypes.byref(p))
    finally:
        k.CloseHandle(ctypes.c_void_p(foto))
    return saida


def dono(pid, processos):
    """Programa que abriu o servidor: 'antigravity.exe' (app) ou 'agy.exe' (CLI), ou None."""
    atual = pid
    for _ in range(6):
        pai, _nome = processos.get(atual, (None, ""))
        if pai is None:
            return None
        nome_pai = processos.get(pai, (None, ""))[1]
        if nome_pai in DONOS:
            return nome_pai
        atual = pai
    return None


def achar(base, dono_esperado="antigravity.exe"):
    """Servidor vivo do app (o do CLI se registra na mesma pasta daemon: e filtrado pelo processo pai)."""
    vivos = []
    processos = _processos()
    for f in glob.glob(os.path.join(base, "daemon", "hub_*.json")):
        try:
            if os.path.getsize(f) > 4096:
                continue
            with open(f, encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        porta, pid, token = d.get("httpPort"), d.get("pid"), d.get("csrfToken")
        if isinstance(porta, int) and 0 < porta < 65536 and isinstance(pid, int) and isinstance(token, str) and _vivo(pid) \
                and dono(pid, processos) == dono_esperado:
            vivos.append((os.path.getmtime(f), porta, token))
    if not vivos:
        return None
    _, porta, token = max(vivos)
    return {"porta": porta, "token": token}


def _recusou(erro):
    """Conexao recusada: o pedido nem chegou (app ainda subindo). So esse caso e seguro repetir."""
    motivo = getattr(erro, "reason", None)
    return isinstance(motivo, ConnectionRefusedError) or getattr(motivo, "winerror", None) == 10061


def chamar(servidor, metodo, corpo, tentativas=TENTATIVAS_RECUSA, pausa=PAUSA_RECUSA_S, dormir=time.sleep,
           timeout=TIMEOUT_S):
    if not metodo.isidentifier():
        raise ValueError("metodo invalido")
    req = urllib.request.Request(
        f"http://127.0.0.1:{servidor['porta']}/{SERVICO}/{metodo}",
        data=json.dumps(corpo).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": servidor["token"]})
    for tentativa in range(tentativas + 1):
        try:
            # nosemgrep: dynamic-urllib-use-detected -- host fixo 127.0.0.1, porta validada como int em achar()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                dados = r.read(5 * 1024 * 1024)
                return json.loads(dados) if dados else {}
        except urllib.error.HTTPError as e:
            corpo_erro = e.read(2000).decode("utf-8", "replace")
            raise ErroServidor(f"{metodo}: HTTP {e.code}: {corpo_erro[:300]}") from e
        except urllib.error.URLError as e:
            if _recusou(e) and tentativa < tentativas:
                dormir(pausa)                       # servidor vivo mas ainda nao escuta: o app esta abrindo
                continue
            raise ErroServidor(f"{metodo}: {type(e).__name__}: {e}") from e
        except (TimeoutError, json.JSONDecodeError) as e:
            raise ErroServidor(f"{metodo}: {type(e).__name__}: {e}") from e
