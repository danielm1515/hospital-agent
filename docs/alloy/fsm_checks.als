-- The properties Alloy checks over the §3 state machine (docs/alloy/README.md).
-- fsm_model.als is generated from fsm.py; this file is written by hand.
--
-- Membership is written `some (x & S)` / `no (x & S)`, never `x in S`: a `lone` field is empty
-- before the first step, and in Alloy the empty set is `in` every set.
--
-- `check` = Alloy searches every trace up to the step bound for a counterexample; none found
--           means the property holds for every path of that length, for ANY guard outcome
--           (the guards are not modelled - see fsm_model.als).
-- `run`   = Alloy looks for one trace with the property - a witness that it is reachable.
module fsm_checks
open fsm_model

-- ============================================================================================
-- A. The table itself (structural: hold for every row)
-- ============================================================================================

-- Every non-final State has a way out: no case can get stuck with no row to fire.
assert A1_NoDeadEnd { all s: State - Terminal | some r: Row | r.src = s }
check A1_NoDeadEnd for 1 but 1 steps

-- Completed and Failed have no outgoing row at all.
assert A2_TerminalHasNoExit { no r: Row | some (r.src & Terminal) }
check A2_TerminalHasNoExit for 1 but 1 steps

-- T5: a medical question always goes to a person.
assert A3_MedicalQuestionGoesToHuman {
  all r: Row | r.ev = MEDICAL_QUESTION_DETECTED implies r.dst = AwaitingHumanReview
}
check A3_MedicalQuestionGoesToHuman for 1 but 1 steps

-- Every way into human review names why (an escalation kind) - except the return from a
-- patient's reply, which keeps the kind the case already carries.
assert A4_EveryEscalationNamesItsKind {
  all r: Row | (r.dst = AwaitingHumanReview and r.src != AwaitingPatientReply) implies some r.kinds
}
check A4_EveryEscalationNamesItsKind for 1 but 1 steps

-- A staff approval that resumes automation always needs a specific escalation kind.
assert A5_ApprovalNeedsAKind { all r: Row | r.ev = HUMAN_APPROVED implies some r.requires }
check A5_ApprovalNeedsAKind for 1 but 1 steps

-- ============================================================================================
-- B. Every trace (Alloy 6 temporal logic, up to 20 steps)
-- ============================================================================================

-- T11: from a final State only the same final State.
assert B1_T11_TerminalIsFinal { always all s: Terminal | Case.state = s implies always Case.state = s }
check B1_T11_TerminalIsFinal for 1 but 1..20 steps

-- A case under review, or waiting on the patient's reply to staff, always carries its reason.
assert B2_ReviewAlwaysCarriesAKind {
  always (some (Case.state & (AwaitingHumanReview + AwaitingPatientReply)) implies one Case.escalation)
}
check B2_ReviewAlwaysCarriesAKind for 1 but 1..20 steps

-- Only a staff decision (or a staff request to the patient) takes a case out of review.
assert B3_LeaveReviewOnlyByStaff {
  always ((Case.state = AwaitingHumanReview and Case.state' != AwaitingHumanReview) implies
          some (Case.last'.ev & (HUMAN_APPROVED + HUMAN_RESOLVED_CASE + HUMAN_REJECTED + PATIENT_REPLY_REQUESTED)))
}
check B3_LeaveReviewOnlyByStaff for 1 but 1..20 steps

-- A medical question, a policy denial or a safety escalation never goes back to automation:
-- staff may only answer, close or reject it.
assert B4_NoAutomationAfterMedicalDeniedOrUnsafe {
  always (some (Case.escalation & (MedicalQuestion + PolicyDenied + SafetyEscalation)) implies
          always no (Case.state & (Planning + RetrievingData + Delivering + Ready)))
}
check B4_NoAutomationAfterMedicalDeniedOrUnsafe for 1 but 1..20 steps

-- No classification without an identity-verified, validated request first.
assert B5_ValidatedBeforeClassification {
  always (Case.state = Classifying implies once Case.last.ev = REQUEST_VALIDATED)
}
check B5_ValidatedBeforeClassification for 1 but 1..20 steps

-- T1 (abstracted): an external call starts only from a policy approval.
assert B7_CallsOnlyAfterPolicyAllowed {
  always ((some (Case.state' & (RetrievingData + Delivering)) and no (Case.state & (RetrievingData + Delivering)))
          implies Case.last'.ev = POLICY_ALLOWED)
}
check B7_CallsOnlyAfterPolicyAllowed for 1 but 1..20 steps

-- T8: Ready only through a passed readiness check.
assert B8_T8_ReadyOnlyThroughReadiness { always (Case.state = Ready implies once Case.last.ev = READINESS_PASSED) }
check B8_T8_ReadyOnlyThroughReadiness for 1 but 1..20 steps

-- ============================================================================================
-- C. A property that needs a guard: shown both ways
-- ============================================================================================

-- "An automatic completion (CASE_RESOLVED) always went through Ready." With the guards
-- abstracted Alloy is EXPECTED to find a counterexample: Planning --POLICY_ALLOWED--> Delivering
-- (R13) is a row whatever the plan's step is, so nothing in the table alone stops a delivery
-- before readiness. The guard that does is `delivery_action`: the delivery step is reached only
-- through DELIVERY_PLANNED (from Ready). C2 states that guard as an explicit assumption.
assert C1_AutoCompletionNeedsReady_GuardsAbstracted {
  always ((Case.state = Completed and Case.last.ev = CASE_RESOLVED) implies once Case.state = Ready)
}
check C1_AutoCompletionNeedsReady_GuardsAbstracted for 1 but 1..20 steps

-- The delivery_action guard's contract (guards.py): a POLICY_ALLOWED into Delivering happens only
-- after the plan was moved to its delivery step, which DELIVERY_PLANNED alone does.
pred DeliveryActionGuard {
  always ((Case.state = Delivering and Case.last.ev = POLICY_ALLOWED) implies once Case.last.ev = DELIVERY_PLANNED)
}
assert C2_AutoCompletionNeedsReady_WithDeliveryGuard {
  DeliveryActionGuard implies
    always ((Case.state = Completed and Case.last.ev = CASE_RESOLVED) implies once Case.state = Ready)
}
check C2_AutoCompletionNeedsReady_WithDeliveryGuard for 1 but 1..20 steps

-- "No planning without a complete plan" (an Unsupported intent never gets one). Found by Alloy,
-- not foreseen: with the guards abstracted there IS a counterexample - Classifying
-- --INTENT_CLASSIFIED--> AssessingReadiness (R05) -> Ready -> DELIVERY_PLANNED -> Planning, with no
-- PLAN_CREATED anywhere. R05 is the re-classification row: its guard `ReadinessInProgress` holds
-- only when the case already has a plan and readiness data (§3.1), i.e. after a plan was made.
assert C3_PlanBeforePlanning_GuardsAbstracted {
  always (Case.state = Planning implies once Case.last.ev = PLAN_CREATED)
}
check C3_PlanBeforePlanning_GuardsAbstracted for 1 but 1..20 steps

-- The ReadinessInProgress guard's contract (guards.py): R05 fires only for a case that already
-- has a plan - one PLAN_CREATED made.
pred ReadinessInProgressGuard {
  always ((Case.last = R05) implies once Case.last.ev = PLAN_CREATED)
}
assert C4_PlanBeforePlanning_WithReadinessGuard {
  ReadinessInProgressGuard implies always (Case.state = Planning implies once Case.last.ev = PLAN_CREATED)
}
check C4_PlanBeforePlanning_WithReadinessGuard for 1 but 1..20 steps

-- ============================================================================================
-- D. Reachability: Alloy finds a trace into every State (no dead code in the table)
-- ============================================================================================

run D_reach_Received { eventually Case.state = Received } for 1 but 1..20 steps
run D_reach_Classifying { eventually Case.state = Classifying } for 1 but 1..20 steps
run D_reach_Classified { eventually Case.state = Classified } for 1 but 1..20 steps
run D_reach_Planning { eventually Case.state = Planning } for 1 but 1..20 steps
run D_reach_RetrievingData { eventually Case.state = RetrievingData } for 1 but 1..20 steps
run D_reach_Delivering { eventually Case.state = Delivering } for 1 but 1..20 steps
run D_reach_AssessingReadiness { eventually Case.state = AssessingReadiness } for 1 but 1..20 steps
run D_reach_AwaitingPatientInput { eventually Case.state = AwaitingPatientInput } for 1 but 1..20 steps
run D_reach_AwaitingHumanReview { eventually Case.state = AwaitingHumanReview } for 1 but 1..20 steps
run D_reach_Ready { eventually Case.state = Ready } for 1 but 1..20 steps
run D_reach_Completed { eventually Case.state = Completed } for 1 but 1..20 steps
run D_reach_Failed { eventually Case.state = Failed } for 1 but 1..20 steps
run D_reach_AwaitingPatientReply { eventually Case.state = AwaitingPatientReply } for 1 but 1..20 steps

-- ============================================================================================
-- E. Scenario witnesses, for the slides - under both guard contracts, so each trace is one the
--    real system can take (without them Alloy happily "skips" planning through R05, see C3)
-- ============================================================================================

pred Guards { DeliveryActionGuard and ReadinessInProgressGuard }

-- Scenario 1 shape: an automatic completion through readiness, under the delivery guard.
run E1_AutomaticCompletion {
  Guards and eventually (Case.state = Completed and Case.last.ev = CASE_RESOLVED)
} for 1 but 1..20 steps

-- Scenario 2 shape: a medical question reaches staff and is closed by them.
run E2_MedicalQuestionClosedByStaff {
  Guards and eventually (Case.last.ev = MEDICAL_QUESTION_DETECTED and eventually Case.last.ev = HUMAN_RESOLVED_CASE)
} for 1 but 1..20 steps

-- A missing document, the patient uploads it, and the case is re-classified (T10).
run E3_MissingDocumentThenReclassified {
  Guards and eventually (Case.state = AwaitingPatientInput and eventually Case.state = Classifying)
} for 1 but 1..20 steps
