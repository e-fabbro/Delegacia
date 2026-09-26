#!/usr/bin/env bash
# ops/nexo_exec.sh — executa uma ação do Nexo no Codex e devolve JSON com o campo `result`.
#
# Uso (como o usuário nexo):  ops/nexo_exec.sh <status|novo|ingerir|analisar|diligencias> <CODINOME> [pergunta...]
# Saída (stdout, uma linha):  {"status":"ok","caso":...,"acao":...,"result":"<resumo>"}
#                             {"status":"erro","caso":...,"acao":...,"codigo":<n>,"result":""}   (exit 1)
#
# Travas do Codex, sempre passadas aqui (não dependem do config.toml):
#   - sandbox workspace-write, sem rede; só /srv/casos gravável além do repositório (que é root:root,
#     só leitura para o nexo — o modelo não consegue editar hooks nem agentes);
#   - approval_policy=never (headless), unified_exec desligado (write_stdin não passaria pelo hook),
#     busca web desligada (ferramenta hospedada não passa pelo hook); apps, plugins, navegador,
#     computer use e geração de imagem desligados (não passam pelo hook de shell e buscam na rede);
#   - no máximo 3 subagentes simultâneos (cota ChatGPT compartilhada; ver AGENTS.md, Economia);
#   - hooks do projeto (.codex/hooks.json) ativos sem prompt de confiança; a origem é o repositório root:root.
# Autenticação: login ChatGPT do próprio nexo (~nexo/.codex). Chave de API nunca: removida do ambiente.
# Eventos JSONL e stderr do Codex ficam no log do caso (volume cifrado), nunca em /var/log.
set -euo pipefail

ACAO="${1:-}"; COD="${2:-}"; shift 2 2>/dev/null || shift $#
PERGUNTA="${*:-}"
REPO_DIR="${REPO_DIR:-/opt/agencia-nexo}"
CASOS_DIR="${AGENCIA_CASOS:-/srv/casos}"
AUDIT_LOG="${AGENCIA_AUDIT_LOG:-/var/log/agencia-nexo/auditoria.jsonl}"

erro() { printf '{"erro":"%s"}\n' "$1"; exit 1; }

[[ -n "${ACAO}" && -n "${COD}" ]] || erro "uso: nexo_exec <status|novo|ingerir|analisar|diligencias> <CODINOME> [pergunta]"
[[ "${COD}" =~ ^[A-Z][A-Z0-9_-]{1,31}$ ]] || erro "codinome inválido"
case "${ACAO}" in
  status)      MODELO="status.md" ;;
  novo)        MODELO="caso-novo.md" ;;
  diligencias) MODELO="diligencias.md" ;;
  ingerir)     MODELO="ingerir.md" ;;
  analisar)    MODELO="analisar.md" ;;
  *) erro "ação desconhecida" ;;
esac

# A pergunta é dado do Fabbro: uma linha, sem aspas, até 500 caracteres.
PERGUNTA="$(printf '%s' "${PERGUNTA}" | tr '\r\n' '  ' | tr -d '"' | cut -c1-500)"
ARGUMENTOS="${COD}${PERGUNTA:+ ${PERGUNTA}}"

PROMPT="$(MODELO="${REPO_DIR}/comandos/${MODELO}" ARGUMENTOS="${ARGUMENTOS}" python3 -c '
import os
print(open(os.environ["MODELO"], encoding="utf-8").read().replace("$ARGUMENTS", os.environ["ARGUMENTOS"]).strip())
')"

if [[ -d "${CASOS_DIR}/${COD}" ]]; then
  LOG_CASO="${CASOS_DIR}/${COD}/log"; mkdir -p "${LOG_CASO}"
  BASE="${LOG_CASO}/codex_${ACAO}_$(date +%Y%m%d_%H%M%S)"
  EVENTOS="${BASE}.jsonl"; ERROS="${BASE}.err"
else
  EVENTOS=/dev/null; ERROS=/dev/null
fi
ULTIMA="$(mktemp)"
trap 'rm -f "${ULTIMA}"' EXIT

set +e
printf '%s\n' "${PROMPT}" | env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY \
  PATH="${REPO_DIR}/.venv/bin:${PATH}" AGENCIA_AUDIT_LOG="${AUDIT_LOG}" AGENCIA_CASOS="${CASOS_DIR}" \
  codex exec \
    -C "${REPO_DIR}" \
    --add-dir "${CASOS_DIR}" \
    -s workspace-write \
    -c 'approval_policy="never"' \
    -c 'sandbox_workspace_write.network_access=false' \
    -c 'features.unified_exec=false' \
    -c 'web_search="disabled"' \
    -c 'agents.max_concurrent_threads_per_session=3' \
    -c 'features.apps=false' \
    -c 'features.plugins=false' \
    -c 'features.remote_plugin=false' \
    -c 'features.browser_use=false' \
    -c 'features.browser_use_external=false' \
    -c 'features.computer_use=false' \
    -c 'features.in_app_browser=false' \
    -c 'features.image_generation=false' \
    --dangerously-bypass-hook-trust \
    --json \
    -o "${ULTIMA}" \
    - > "${EVENTOS}" 2> "${ERROS}"
CODIGO=$?
set -e

ULTIMA="${ULTIMA}" CODIGO="${CODIGO}" COD="${COD}" ACAO="${ACAO}" python3 -c '
import json, os
codigo = int(os.environ["CODIGO"])
try:
    texto = open(os.environ["ULTIMA"], encoding="utf-8").read().strip()
except OSError:
    texto = ""
saida = {"status": "ok" if codigo == 0 and texto else "erro", "caso": os.environ["COD"],
         "acao": os.environ["ACAO"], "result": texto}
if saida["status"] == "erro":
    saida["codigo"] = codigo
print(json.dumps(saida, ensure_ascii=False))
'
[[ "${CODIGO}" -eq 0 ]] || exit 1
