#!/bin/sh
# Start the loopback gateway this sandbox needs to run the Claude Agent SDK, and export where it
# is. Sourced, not executed, because the authoring command that follows has to inherit the URL and
# the token: `. /opt/alk/start-sandbox-gateway.sh && python -m ...`.
#
# Silent and harmless on every other path. A run on the ADK backend never needs a gateway, so this
# returns immediately and the guest behaves exactly as it did before the file existed.

alk_gateway_wanted() {
    case "${ALK_HARNESS}" in
        claude-gemini | claude_gemini | claude-sdk) return 0 ;;
        *) return 1 ;;
    esac
}

if ! alk_gateway_wanted; then
    return 0 2>/dev/null || exit 0
fi

# An address already set means somebody else's gateway was chosen deliberately. Leave it alone
# rather than starting a second one and quietly talking to the wrong thing.
if [ -n "${ALK_HARNESS_GATEWAY_URL}" ]; then
    echo "sandbox gateway: using the address already configured" >&2
    return 0 2>/dev/null || exit 0
fi

# Minted here, used here. A token that never crosses the sandbox boundary cannot leak from one,
# and the port is loopback-only, so this guards against a stray guest process rather than anyone
# remote.
AGENTCC_SANDBOX_KEY="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export AGENTCC_SANDBOX_KEY

: "${GOOGLE_CLOUD_LOCATION:=global}"
export GOOGLE_CLOUD_LOCATION

# A region is a hostname prefix and `global` is not: `https://global-aiplatform.googleapis.com`
# answers 404. Worked out here rather than in the config, where one template cannot say both.
if [ "${GOOGLE_CLOUD_LOCATION}" = "global" ]; then
    AGENTCC_VERTEX_BASE_URL="https://aiplatform.googleapis.com/v1/projects/${GOOGLE_CLOUD_PROJECT}/locations/global"
else
    AGENTCC_VERTEX_BASE_URL="https://${GOOGLE_CLOUD_LOCATION}-aiplatform.googleapis.com/v1/projects/${GOOGLE_CLOUD_PROJECT}/locations/${GOOGLE_CLOUD_LOCATION}"
fi
export AGENTCC_VERTEX_BASE_URL

ALK_GATEWAY_LOG=/work/artifacts/sandbox-gateway.log
mkdir -p /work/artifacts 2>/dev/null || true

/opt/alk/agentcc-gateway --config /opt/alk/agentcc-sandbox.yaml >"${ALK_GATEWAY_LOG}" 2>&1 &
ALK_GATEWAY_PID=$!

# Wait for it to answer rather than assuming it is up: authoring starts immediately after this
# line, and a race here reads as the model refusing to respond.
ALK_GATEWAY_READY=0
i=0
while [ "$i" -lt 60 ]; do
    if ! kill -0 "${ALK_GATEWAY_PID}" 2>/dev/null; then
        echo "sandbox gateway: exited during startup, see ${ALK_GATEWAY_LOG}" >&2
        break
    fi
    if curl -sf -o /dev/null -H "x-api-key: ${AGENTCC_SANDBOX_KEY}" \
        http://127.0.0.1:8090/v1/models 2>/dev/null; then
        ALK_GATEWAY_READY=1
        break
    fi
    sleep 1
    i=$((i + 1))
done

if [ "${ALK_GATEWAY_READY}" = "1" ]; then
    ALK_HARNESS_GATEWAY_URL="http://127.0.0.1:8090"
    ALK_HARNESS_GATEWAY_TOKEN="${AGENTCC_SANDBOX_KEY}"
    export ALK_HARNESS_GATEWAY_URL ALK_HARNESS_GATEWAY_TOKEN
    echo "sandbox gateway: ready on ${ALK_HARNESS_GATEWAY_URL}" >&2
else
    # Deliberately not fatal here. The backend itself refuses to start without a gateway and says
    # so in the harness's own words, which is a better failure than a shell error nobody reads.
    echo "sandbox gateway: did not become ready, see ${ALK_GATEWAY_LOG}" >&2
fi
