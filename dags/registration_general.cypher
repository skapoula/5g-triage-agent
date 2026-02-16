// Registration main DAG (TS 23.502 Fig. 4.2.2.2.2-1)
// Links to Authentication_5G_AKA sub-DAG at step 9.

MATCH (t:ReferenceTrace {name: "Registration_General"}) DETACH DELETE t;

CREATE (t:ReferenceTrace {
    name: "Registration_General",
    spec: "TS 23.502 4.2.2.2.2",
    version: "Rel-17",
    procedure: "registration"
});

UNWIND [
    {order: 1, nf: "UE", action: "Registration Request", keywords: ["Registration Request", "Initial Registration", "SUCI"], optional: false},
    {order: 2, nf: "AMF", action: "AMF selection", keywords: ["AMF selection"], optional: false},
    {order: 3, nf: "New AMF", action: "Registration Request", keywords: ["Registration Request"], optional: false},
    {order: 4, nf: "New AMF", action: "Namf_Communication_UEContextTransfer", keywords: ["UEContextTransfer"], optional: true},
    {order: 5, nf: "Old AMF", action: "Namf_Communication_UEContextTransfer response", keywords: ["UEContextTransfer response"], optional: true},
    {order: 6, nf: "AMF", action: "Identity Request (optional)", keywords: ["Identity Request"], optional: true},
    {order: 7, nf: "UE", action: "Identity Response", keywords: ["Identity Response"], optional: true},
    {order: 8, nf: "AMF", action: "AUSF selection", keywords: ["AUSF selection"], optional: false},
    {order: 9, nf: "AMF", action: "Authentication/Security", keywords: ["Authentication", "Security", "AUSF", "AKA"], sub_dag: "Authentication_5G_AKA", optional: false},
    {order:10, nf: "AMF", action: "Namf_Communication_RegistrationStatusUpdate", keywords: ["RegistrationStatusUpdate"], optional: false},
    {order:11, nf: "AMF", action: "Identity Request/Response (if needed)", keywords: ["Identity Request", "Identity Response"], optional: true},
    {order:12, nf: "AMF", action: "N5g-eir_EquipmentIdentityCheck_Get", keywords: ["EquipmentIdentityCheck"], optional: true},
    {order:13, nf: "AMF", action: "UDM selection", keywords: ["UDM selection"], optional: false},
    {order:14, nf: "UDM", action: "Nudm_UECM_Registration", keywords: ["Nudm_UECM_Registration"], optional: false},
    {order:15, nf: "UDM", action: "Nudm_SDM_Get", keywords: ["Nudm_SDM_Get"], optional: false},
    {order:16, nf: "UDM", action: "Nudm_SDM_Subscribe", keywords: ["Nudm_SDM_Subscribe"], optional: false},
    {order:17, nf: "AMF", action: "PCF selection", keywords: ["PCF selection"], optional: false},
    {order:18, nf: "AMF", action: "AM Policy Association Establishment/Modification", keywords: ["AM Policy", "Association"], optional: false},
    {order:19, nf: "AMF", action: "Nsmf_PDUSession_UpdateSMContext", keywords: ["Nsmf_PDUSession", "UpdateSMContext"], optional: false},
    {order:20, nf: "AMF", action: "UE Context Modification Request", keywords: ["UE Context Modification Request"], optional: false},
    {order:21, nf: "AMF", action: "Registration Accept", keywords: ["Registration Accept"], optional: false},
    {order:22, nf: "UE", action: "Registration Complete", keywords: ["Registration Complete"], optional: false},
    {order:23, nf: "AMF", action: "N2 message", keywords: ["N2 message"], optional: false},
    {order:24, nf: "UDM", action: "Nudm_UECM_Update", keywords: ["Nudm_UECM_Update"], optional: false}
] AS step

CREATE (e:RefEvent {
    order: step.order,
    nf: step.nf,
    action: step.action,
    keywords: step.keywords,
    sub_dag: step.sub_dag,
    optional: coalesce(step.optional, false)
});

MATCH (t:ReferenceTrace {name: "Registration_General"}), (e:RefEvent)
CREATE (t)-[:STEP {order: e.order}]->(e);

MATCH (t:ReferenceTrace {name: "Registration_General"})-[:STEP]->(e1:RefEvent)
MATCH (t)-[:STEP]->(e2:RefEvent)
WHERE e2.order = e1.order + 1
CREATE (e1)-[:NEXT {delta: "success"}]->(e2);

MATCH (reg:RefEvent {order: 9, action: "Authentication/Security"})
MATCH (auth:ReferenceTrace {name: "Authentication_5G_AKA"})
CREATE (reg)-[:USES_SUB_DAG]->(auth);
