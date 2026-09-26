#!/usr/bin/env bash
# ops/teste_headless.sh — critério de aceite 8: execução headless do Claude Code devolve o resumo.
#
# GERADO NA F5. Executar na VPS como o usuário `nexo` (ou via sudo -u nexo), com o Claude Code já
# autenticado para esse usuário e /srv/casos montado. Não roda nos testes automatizados (sem rede).
#
# Uso: ops/teste_headless.sh [CODINOME] [COMANDO]
#   CODINOME  padrão TESTE (criado com brutos sintéticos se não existir)
#   COMANDO   padrão "/status <COD>"; use "/analisar <COD>" para o pipeline completo
set -euo pipefail

COD="${1:-TESTE}"
CMD="${2:-/status ${COD}}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${AGENCIA_CASOS:-/srv/casos}"
LOG_DIR="${CASOS_DIR}/${COD}/log"
TS="$(date +%Y%m%d_%H%M%S)"

cd "${REPO_DIR}"

if [[ ! -f "${CASOS_DIR}/${COD}/estado.json" ]]; then
  echo ">> criando caso ${COD} com fixtures sintéticos"
  uv run python -m agencia caso novo "${COD}" >/dev/null
  uv run python - "${CASOS_DIR}/${COD}" <<'PY'
import shutil, sys
from pathlib import Path
destino = Path(sys.argv[1]) / "00_brutos"
fix = Path("tests/fixtures")
for nome in ("ccs_3contas.xlsx", "rif_12_sintetico.pdf", "simba_3contas.csv"):
    shutil.copy(fix / nome, destino / nome)
PY
fi
mkdir -p "${LOG_DIR}"

echo ">> claude -p \"${CMD}\" (timeout 30 min)"
SAIDA="${LOG_DIR}/headless_${TS}.json"
if timeout 1800 claude -p "${CMD}" --output-format json > "${SAIDA}" 2> "${LOG_DIR}/headless_${TS}.err"; then
  echo ">> terminou; resultado em ${SAIDA}"
else
  echo "FALHA: claude -p saiu com erro; veja ${LOG_DIR}/headless_${TS}.err" >&2
  exit 1
fi

# O campo `result` deve conter o resumo no formato do CLAUDE.md (sem nomes reais).
uv run python - "${SAIDA}" "${COD}" <<'PY'
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
