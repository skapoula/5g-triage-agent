// Authentication sub-DAG: 5G AKA (TS 33.501 Fig. 6.1.3.2-1)

MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"}) DETACH DELETE t;

CREATE (t:ReferenceTrace {
    name: "Authentication_5G_AKA",
    spec: "TS 33.501 6.1.3.2",
    version: "Rel-17",
    procedure: "authentication"
});

UNWIND [
    {order: 1,  nf: "UDM/ARPF", action: "Generate AV", keywords: ["Generate AV", "auth data", "generate-auth-data"], optional: false},
    {order: 2,  nf: "AUSF",     action: "Nudm_UEAuthentication_Get Response (5G HE AV, SUPI)", keywords: ["Nudm_UEAuthentication", "Get Response", "5G HE AV", "SUPI"], optional: false},
    {order: 3,  nf: "AUSF",     action: "Store XRES*, Calculate HXRES*", keywords: ["Store XRES*", "Calculate HXRES*", "HXRES"], optional: false},
    {order: 4,  nf: "AUSF",     action: "Nausf_UEAuthentication_Authenticate Response (5G SE AV)", keywords: ["Nausf_UEAuthentication", "Authenticate Response", "5G SE AV"], optional: false},
    {order: 5,  nf: "SEAF",     action: "Authentication Request", keywords: ["Authentication Request", "RAND", "AUTN"], optional: false},
    {order: 6,  nf: "UE",       action: "Calculate Authentication Response (RES*)", keywords: ["Calculate Auth", "RES*", "Authentication Response"], optional: false},
    {order: 7,  nf: "UE",       action: "Authentication Response", keywords: ["Authentication Response", "RES*"], optional: false},
    {order: 8,  nf: "SEAF",     action: "Calculate HRES* and compare to HXRES*", keywords: ["Calculate HRES*", "compare", "HXRES*"], optional: false},
    {order: 9,  nf: "SEAF",     action: "Nausf_UEAuthentication_Authenticate Request (RES*)", keywords: ["Nausf_UEAuthentication", "Authenticate Request", "RES*"], optional: false},
    {order:10,  nf: "UDM",      action: "RES* Verification", keywords: ["RES* Verification", "Verification", "XRES*"], optional: false},
    {order:11,  nf: "AUSF",     action: "Nausf_UEAuthentication_Authenticate Response (Result, SUPI, K_SEAF)", keywords: ["Authenticate Response", "Result", "SUPI", "K_SEAF"], optional: false}
] AS step

CREATE (e:RefEvent {
    order: step.order,
    nf: step.nf,
    action: step.action,
    keywords: step.keywords,
    optional: coalesce(step.optional, false)
});

MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"}), (e:RefEvent)
CREATE (t)-[:STEP {order: e.order}]->(e);

MATCH (t:ReferenceTrace {name: "Authentication_5G_AKA"})-[:STEP]->(e1:RefEvent)
MATCH (t)-[:STEP]->(e2:RefEvent)
WHERE e2.order = e1.order + 1
CREATE (e1)-[:NEXT {delta: "success"}]->(e2);
