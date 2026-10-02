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
--
-- The properties that depend on a guard, the bug Alloy found (spec_corrections row 99) and the
-- scenario traces are in fsm_plan.als, which models the guards that read the plan pointer.
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
assert B6_CallsOnlyAfterPolicyAllowed {
  always ((some (Case.state' & (RetrievingData + Delivering)) and no (Case.state & (RetrievingData + Delivering)))
          implies Case.last'.ev = POLICY_ALLOWED)
}
check B6_CallsOnlyAfterPolicyAllowed for 1 but 1..20 steps

-- T8: Ready only through a passed readiness check.
assert B7_T8_ReadyOnlyThroughReadiness { always (Case.state = Ready implies once Case.last.ev = READINESS_PASSED) }
check B7_T8_ReadyOnlyThroughReadiness for 1 but 1..20 steps

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
