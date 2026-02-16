
# Configuration
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
LOKI_URL="http://$NODE_IP:32002"
LIMIT=5000
# 1. HANDLE USER INPUT
TARGET_IMSI="$1"
SINCE="${2:-5m}"

if [ -z "$TARGET_IMSI" ]; then
    echo "Usage: $0 <IMSI> [SINCE]"
    exit 1
fi

echo "--- Tracing IMSI: $TARGET_IMSI (Window: $SINCE) ---"

# Step 1: Find AMF Context ID
echo "[1/4] Discovering AMF Context ID..."
CONTEXT_LOGS=$(curl -g -G -sS "$LOKI_URL/loki/api/v1/query_range" \
    --data-urlencode "query={service_name=\"amf\"} |= \"$TARGET_IMSI\"" \
    --data-urlencode "limit=10" \
    --data-urlencode "since=$SINCE" | \
    jq -r '.data.result[].values[][1]' 2>/dev/null)

CONTEXT_ID=$(echo "$CONTEXT_LOGS" | grep -o 'amf_ue_ngap_id:[^]]*' | head -n 1 | cut -d':' -f2-)
if [ -z "$CONTEXT_ID" ]; then
    echo "Warning: Context ID not found. Tracing might be incomplete."
    CONTEXT_ID=""
else
    echo "      Found Context ID: $CONTEXT_ID"
fi

# Step 2: Find UE IP Address (from SMF logs)
echo "[2/4] Discovering UE IP Address..."
# Look for "Allocated PDUAdress" or similar in SMF logs associated with this IMSI
SMF_LOGS=$(curl -g -G -sS "$LOKI_URL/loki/api/v1/query_range" \
    --data-urlencode "query={service_name=\"smf\"} |= \"$TARGET_IMSI\"" \
    --data-urlencode "limit=50" \
    --data-urlencode "since=$SINCE" | \
    jq -r '.data.result[].values[][1]' 2>/dev/null)

# Extract IP: Look for "Allocated PDUAdress[10.60.0.51]" or similar pattern
UE_IP=$(echo "$SMF_LOGS" | grep -o 'Allocated PDUAdress\[[^]]*\]' | grep -o '[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}' | head -n 1)

# Fallback: sometimes logged as "Release IP[...]" or just IP in other contexts
if [ -z "$UE_IP" ]; then
     # Try simpler grep if specific message format differs
     UE_IP=$(echo "$SMF_LOGS" | grep -oE "\b10\.60\.[0-9]{1,3}\.[0-9]{1,3}\b" | head -n 1)
fi

if [ ! -z "$UE_IP" ]; then
    echo "      Found UE IP: $UE_IP (Will correlate UPF logs)"
else
    echo "      No UE IP found in SMF logs (UPF logs might be missing)"
fi

# Step 3: Find SUCI (Optional)
SUCI=""
# ... (Same as before, skipped for brevity)

# Step 4: Magic Query
echo "[4/4] Retrieving End-to-End Trace (AMF+SMF+UPF)..."

REGEX_QUERY="$TARGET_IMSI"
if [ ! -z "$CONTEXT_ID" ]; then
    SAFE_CTX_ID=$(echo "$CONTEXT_ID" | sed 's/[]\/$*.^[]/\\&/g')
    REGEX_QUERY="$REGEX_QUERY|$SAFE_CTX_ID"
fi
if [ ! -z "$UE_IP" ]; then
    # Do NOT escape dots. Use the IP directly.
    REGEX_QUERY="$REGEX_QUERY|$UE_IP"
fi

echo "      Query Filter: $REGEX_QUERY"

curl -g -G -sS "$LOKI_URL/loki/api/v1/query_range" \
    --data-urlencode "query={service_name=~\"^.+\"} |~ \"$REGEX_QUERY\"" \
    --data-urlencode "limit=$LIMIT" \
    --data-urlencode "since=$SINCE" | \
    jq -r '.data.result[].values[][1]' | \
    sed 's/\x1b\[[0-9;]*m//g' | \
    sort

echo "--- Trace Complete ---"
