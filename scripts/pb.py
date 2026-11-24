"""Editor de protobuf sem esquema e sem perdas (wire format).

Le uma mensagem como lista de campos [numero, tipo, valor], deixa trocar ou apagar campos por
caminho e regrava recalculando os tamanhos. O que nao e mexido volta byte a byte igual.
"""

MAX_PROFUNDIDADE = 32


def _varint(b, i):
    r = s = 0
    while True:
        if i >= len(b):
            raise ValueError("varint truncado")
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if not x & 0x80:
            return r, i
        s += 7
        if s > 63:
            raise ValueError("varint longo demais")


def enc_varint(n):
    if n < 0:
        n &= (1 << 64) - 1
    out = bytearray()
    while True:
        x = n & 0x7F
        n >>= 7
        if n:
            out.append(x | 0x80)
        else:
            out.append(x)
            return bytes(out)


def ler(b):
    """bytes -> lista de [campo, tipo, valor]; valor e int (tipo 0) ou bytes (tipos 1, 2, 5)."""
    out, i = [], 0
    while i < len(b):
        chave, i = _varint(b, i)
        campo, tipo = chave >> 3, chave & 7
        if campo == 0:
            raise ValueError("campo 0")
        if tipo == 0:
            v, i = _varint(b, i)
        elif tipo == 1:
            v, i = b[i:i + 8], i + 8
        elif tipo == 5:
            v, i = b[i:i + 4], i + 4
        elif tipo == 2:
            n, i = _varint(b, i)
            if i + n > len(b):
                raise ValueError("tamanho estoura a mensagem")
            v, i = b[i:i + n], i + n
        else:
            raise ValueError(f"tipo de campo nao suportado: {tipo}")
        if tipo in (1, 5) and len(v) != (8 if tipo == 1 else 4):
            raise ValueError("campo fixo truncado")
        out.append([campo, tipo, v])
    return out


def gravar(campos):
    out = bytearray()
    for campo, tipo, v in campos:
        out += enc_varint((campo << 3) | tipo)
        if tipo == 0:
            out += enc_varint(v)
        elif tipo == 2:
            out += enc_varint(len(v)) + v
        else:
            out += v
    return bytes(out)


def obter(b, caminho):
    """Valor do primeiro campo no caminho (lista de numeros), ou None."""
    atual = b
    for prof, num in enumerate(caminho):
        achado = next((c for c in ler(atual) if c[0] == num), None)
        if achado is None:
            return None
        if prof == len(caminho) - 1:
            return achado[2]
        if achado[1] != 2:
            return None
        atual = achado[2]
    return None


def trocar(b, caminho, valor, tipo=None):
    """Troca o primeiro campo do caminho por valor (int -> tipo 0, bytes/str -> tipo 2).

    valor None apaga todas as ocorrencias do ultimo campo. Campo que falta no fim e acrescentado;
    submensagem que falta no meio e criada vazia.
    """
    if not caminho or len(caminho) > MAX_PROFUNDIDADE:
        raise ValueError("caminho invalido")
    if isinstance(valor, str):
        valor = valor.encode("utf-8")
    if tipo is None and valor is not None:
        tipo = 0 if isinstance(valor, int) else 2
    campos = ler(b)
    num = caminho[0]
    if len(caminho) == 1:
        if valor is None:
            return gravar([c for c in campos if c[0] != num])
        for c in campos:
            if c[0] == num:
                c[1], c[2] = tipo, valor
                return gravar(campos)
        campos.append([num, tipo, valor])
        return gravar(campos)
    for c in campos:
        if c[0] == num and c[1] == 2:
            c[2] = trocar(c[2], caminho[1:], valor, tipo)
            return gravar(campos)
    if valor is None:
        return b
    campos.append([num, 2, trocar(b"", caminho[1:], valor, tipo)])
    return gravar(campos)


def trocar_bytes_iguais(b, antigo, novo):
    """Troca uma sequencia por outra do mesmo tamanho (ids), sem mexer na estrutura."""
    if len(antigo) != len(novo):
        raise ValueError("so troca sequencias do mesmo tamanho")
    return b.replace(antigo, novo)


def timestamp(segundos, nanos=0):
    """google.protobuf.Timestamp {1: segundos, 2: nanos}."""
    campos = [[1, 0, int(segundos)]]
    if nanos:
        campos.append([2, 0, int(nanos)])
    return gravar(campos)
