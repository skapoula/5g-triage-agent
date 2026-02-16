// PDU Session Establishment DAG (TS 23.502 Fig. 4.3.2.2.1-1)

MATCH (t:ReferenceTrace {name: "PDU_Session_Establishment"}) DETACH DELETE t;

CREATE (t:ReferenceTrace {
    name: "PDU_Session_Establishment",
    spec: "TS 23.502 4.3.2.2.1",
    version: "Rel-17",
    procedure: "pdu_session_establishment"
});

UNWIND [
    {order: 1,  nf: "UE",       action: "PDU Session Establishment Request", keywords: ["PDU Session Establishment Request"], optional: false},
    {order: 2,  nf: "AMF",      action: "SMF selection", keywords: ["SMF selection"], optional: false},
    {order: 3,  nf: "AMF",      action: "Nsmf_PDUSession_CreateSMContext Request", keywords: ["CreateSMContext Request", "Nsmf_PDUSession"], optional: false},
    {order: 4,  nf: "SMF",      action: "Subscription retrieval / Subscription for updates", keywords: ["Subscription retrieval", "Nudm_SDM_Get", "Subscribe"], optional: false},
    {order: 5,  nf: "SMF",      action: "Nsmf_PDUSession_CreateSMContext Response", keywords: ["CreateSMContext Response"], optional: false},
    {order: 6,  nf: "SMF",      action: "PDU Session authentication/authorization", keywords: ["authentication", "authorization"], optional: true},
    {order: 7,  nf: "SMF",      action: "PCF selection", keywords: ["PCF selection"], optional: true},
    {order: 8,  nf: "SMF",      action: "SM Policy Association Establishment or Modification", keywords: ["SM Policy Association", "Npcf_SMPolicyControl_Create"], optional: true},
    {order: 9,  nf: "SMF",      action: "UPF selection", keywords: ["UPF selection"], optional: false},
    {order:10,  nf: "SMF",      action: "N4 Session Establishment/Modification Request", keywords: ["N4 Session Establishment", "PFCP Session Establishment Request"], optional: false},
    {order:11,  nf: "UPF",      action: "N4 Session Establishment/Modification Response", keywords: ["N4 Session Establishment Response"], optional: false},
    {order:12,  nf: "SMF",      action: "Namf_Communication_N1N2MessageTransfer", keywords: ["N1N2MessageTransfer", "N1 SM information", "N2 SM information"], optional: false},
    {order:13,  nf: "AMF",      action: "N2 PDU Session Request (NAS msg)", keywords: ["N2 PDU Session Request", "PDU SESSION ESTABLISHMENT ACCEPT"], optional: false},
    {order:14,  nf: "(R)AN",    action: "AN-specific resource setup", keywords: ["AN-specific resource setup", "RRC Reconfiguration"], optional: false},
    {order:15,  nf: "(R)AN",    action: "N2 PDU Session Response", keywords: ["N2 PDU Session Response"], optional: false},
    {order:16,  nf: "SMF",      action: "Nsmf_PDUSession_UpdateSMContext Request", keywords: ["UpdateSMContext Request"], optional: false},
    {order:17,  nf: "SMF",      action: "N4 Session Modification Request", keywords: ["N4 Session Modification Request"], optional: false},
    {order:18,  nf: "UPF",      action: "N4 Session Modification Response", keywords: ["N4 Session Modification Response"], optional: false},
    {order:19,  nf: "SMF",      action: "Nsmf_PDUSession_UpdateSMContext Response", keywords: ["UpdateSMContext Response"], optional: false},
    {order:20,  nf: "SMF",      action: "Nsmf_PDUSession_SMContextStatusNotify", keywords: ["SMContextStatusNotify"], optional: true},
    {order:21,  nf: "SMF",      action: "IPv6 Address Configuration", keywords: ["IPv6 Address Configuration"], optional: true},
    {order:22,  nf: "SMF",      action: "SMF initiated SM Policy Association Modification", keywords: ["SM Policy Association Modification"], optional: true}
] AS step

CREATE (e:RefEvent {
    order: step.order,
    nf: step.nf,
    action: step.action,
    keywords: step.keywords,
    optional: coalesce(step.optional, false)
});

MATCH (t:ReferenceTrace {name: "PDU_Session_Establishment"}), (e:RefEvent)
CREATE (t)-[:STEP {order: e.order}]->(e);

MATCH (t:ReferenceTrace {name: "PDU_Session_Establishment"})-[:STEP]->(e1:RefEvent)
MATCH (t)-[:STEP]->(e2:RefEvent)
WHERE e2.order = e1.order + 1
CREATE (e1)-[:NEXT {delta: "success"}]->(e2);
