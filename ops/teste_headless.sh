#!/usr/bin/env bash
# ops/teste_headless.sh — critério de aceite 8: execução headless do Nexo (Codex) devolve o resumo.
#
# Executar na VPS como o usuário `nexo` (sudo -u nexo -i ...), com `codex login` feito para esse
# usuário e /srv/casos montado. Não roda nos testes automatizados (sem rede).
#
# Uso: ops/teste_headless.sh [CODINOME] [ACAO] [pergunta...]
#   CODINOME  padrão TESTE (criado com brutos sintéticos se não existir)
#   ACAO      status (padrão) | analisar | ingerir | diligencias; aceita também "/analisar TESTE"
set -euo pipefail

COD="${1:-TESTE}"
ACAO="${2:-status}"; shift 2 2>/dev/null || shift $#
ACAO="${ACAO#/}"; ACAO="${ACAO%% *}"; [[ "${ACAO}" == "caso-novo" ]] && ACAO="novo"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${AGENCIA_CASOS:-/srv/casos}"
PY="${REPO_DIR}/.venv/bin/python"
LOG_DIR="${CASOS_DIR}/${COD}/log"
TS="$(date +%Y%m%d_%H%M%S)"

cd "${REPO_DIR}"

if [[ ! -f "${CASOS_DIR}/${COD}/estado.json" ]]; then
  echo ">> criando caso ${COD} com fixtures sintéticos"
  "${PY}" -m agencia caso novo "${COD}" >/dev/null
  "${PY}" - "${CASOS_DIR}/${COD}" <<'PY'
import shutil, sys
from pathlib import Path
destino = Path(sys.argv[1]) / "00_brutos"
fix = Path("tests/fixtures")
for nome in ("ccs_3contas.xlsx", "rif_12_sintetico.pdf", "simba_3contas.csv"):
    shutil.copy(fix / nome, destino / nome)
PY
fi
mkdir -p "${LOG_DIR}"

echo ">> nexo_exec ${ACAO} ${COD} (timeout 30 min)"
SAIDA="${LOG_DIR}/headless_${TS}.json"
if timeout 1800 bash ops/nexo_exec.sh "${ACAO}" "${COD}" "$@" > "${SAIDA}"; then
  echo ">> terminou; resultado em ${SAIDA} (eventos e stderr do Codex em ${LOG_DIR}/codex_${ACAO}_*)"
else
  echo "FALHA: nexo_exec saiu com erro; veja ${SAIDA} e ${LOG_DIR}/codex_${ACAO}_*.err" >&2
  exit 1
fi

# O campo `result` deve conter o resumo no formato do AGENTS.md (sem nomes reais).
"${PY}" - "${SAIDA}" "${COD}" <<'PY'
import json, re, sys
dados = json.load(open(sys.argv[1], encoding="utf-8"))
res = dados.get("result") if isinstance(dados, dict) else None
if not res:
    sys.exit("FALHA: JSON sem campo result")
print("---- result ----")
print(res)
print("----------------")
cod = sys.argv[2]
if cod not in res:
    sys.exit(f"FALHA: resumo não cita o caso {cod}")
if re.search(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", res):
    sys.exit("FALHA: resumo contém CPF/CNPJ em claro")
print("OK: execução headless devolveu o resumo")
PY
