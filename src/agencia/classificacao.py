"""Classificação do tipo de documento por heurística de conteúdo (nome, cabeçalhos, texto)."""
import re

from agencia.cofre import sem_acentos

TIPOS = ("RIF", "SIMBA", "CCS", "PIX", "TELEMATICA", "ERB", "BILHETAGEM", "SOCIETARIO", "CRIPTO", "OUTRO")
MINIMO = 4  # pontuação abaixo disto -> OUTRO

PISTAS: dict[str, list[tuple[str, int]]] = {
    "RIF": [
        (r"RELATORIO DE INTELIGENCIA FINANCEIRA", 5), (r"\bCOAF\b", 3), (r"\bRIF\b", 2),
        (r"COMUNICACAO", 1), (r"COMUNICANTE", 2), (r"INFORMACOES ADICIONAIS", 2), (r"ENQUADRAMENTO", 1),
        (r"\b(COS|COA)\b", 1),
    ],
    "SIMBA": [
        (r"\bSIMBA\b", 5), (r"NATUREZA_LANCAMENTO", 4), (r"CPF_CNPJ_OD", 4), (r"NUMERO_BANCO", 2),
        (r"DATA_LANCAMENTO", 2), (r"VALOR_TRANSACAO", 2), (r"\bEXTRATO\b", 1),
    ],
    "CCS": [
        (r"\bCCS\b", 4), (r"CADASTRO DE CLIENTES", 5), (r"RELACIONAMENTO", 3), (r"\bPROCURADOR", 1),
    ],
    "PIX": [
        (r"\bPIX\b", 1), (r"CHAVE[ _]PIX", 2), (r"END[ _]?TO[ _]?END|\bE2E", 4), (r"\bPAGADOR\b|\bRECEBEDOR\b", 2),
    ],
    "TELEMATICA": [
        (r"USER[ _-]?AGENT", 4), (r"\bIP\b|ENDERECO IP|IP_ADDRESS", 2), (r"PORTA LOGICA|SOURCE PORT|\bPORT\b", 3),
        (r"\bLOGIN\b|LOGOUT|SESSION", 2), (r"\bUTC\b", 1), (r"GOOGLE|META PLATFORMS|APPLE|MICROSOFT", 1),
    ],
    "ERB": [
        (r"\bERB\b", 5), (r"CELL[ _]?ID|\bCGI\b|\bLAC\b", 3), (r"AZIMUTE|AZIMUTH", 3),
    ],
    "BILHETAGEM": [
        (r"BILHETAGEM", 5), (r"\bCHAMADA", 2), (r"\bDURACAO\b", 1), (r"ORIGINADOR|NUMERO_A|NUMERO_B", 3),
    ],
    "SOCIETARIO": [
        (r"\bQSA\b|QUADRO SOCIETARIO|QUADRO DE SOCIOS", 5), (r"CAPITAL SOCIAL", 3), (r"\bCNAE\b", 3),
        (r"CONTRATO SOCIAL|JUNTA COMERCIAL", 3), (r"\bSOCIO", 1),
    ],
    "CRIPTO": [
        (r"BITCOIN|\bBTC\b|\bETH\b|USDT|TETHER", 3), (r"EXCHANGE|CORRETORA DE CRIPTO", 3),
        (r"BLOCKCHAIN|WALLET|CARTEIRA DIGITAL|ENDERECO DA CARTEIRA", 3), (r"CRIPTO", 2),
    ],
}


def pontuar(texto: str) -> dict[str, int]:
    plano = sem_acentos(texto).upper()
    return {tipo: sum(peso for padrao, peso in pistas if re.search(padrao, plano)) for tipo, pistas in PISTAS.items()}


def classificar(nome_arquivo: str, texto: str, cabecalhos: list[str] | None = None) -> tuple[str, float]:
    """Devolve (tipo, confiança). Confiança = pontuação do 1º sobre 1º + 2º colocados."""
    amostra = "\n".join([nome_arquivo, " ".join(cabecalhos or []), texto[:30000]])
    pontos = pontuar(amostra)
    ordem = sorted(pontos.items(), key=lambda kv: -kv[1])
    (tipo, top), (_, segundo) = ordem[0], ordem[1]
    if top < MINIMO:
        return "OUTRO", 0.0
    return tipo, round(top / (top + segundo), 2)
