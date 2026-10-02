-- The plan's position, added on top of the generated fsm_model.als (docs/alloy/README.md).
--
-- fsm_model.als abstracts every guard: on the table alone, "nothing is sent before Ready" and "no
-- planning without a plan" have counterexamples. This file models what the guards that protect
-- them actually read - where the plan pointer is - and the guards themselves, as guards.py
-- writes them, so both properties are checked against the guards.
--
-- It also replays the bug Alloy found (docs/spec_corrections.md row 99): CanAdvance used to ask
-- only for a next step, so STEP_ADVANCED could walk the plan into SendStatusUpdate and a case
-- reached Delivering with no data retrieved and no readiness check. P1 checks the old guard
-- (a counterexample is expected - that is the bug), P2 the guard as it is now (must hold).
--
-- Membership is `some (x & S)`, never `x in S`: an empty pointer is `in` every set.
-- Written by hand: the guard bodies below must follow guards.py. The plan is the demo's one
-- plan (§5, the Planner's fixed order): 1 CheckAppointment, 2 CheckDocuments, 3 LoadInstructions,
-- 4 SendStatusUpdate.
module fsm_plan
open fsm_model

abstract sig Step {}
one sig S1_CheckAppointment, S2_CheckDocuments, S3_LoadInstructions, S4_SendStatusUpdate extends Step {}
fun nextStep: Step -> Step {
  S1_CheckAppointment -> S2_CheckDocuments + S2_CheckDocuments -> S3_LoadInstructions
  + S3_LoadInstructions -> S4_SendStatusUpdate
}
fun Retrieval: set Step { S1_CheckAppointment + S2_CheckDocuments + S3_LoadInstructions }

one sig Plan { var step: lone Step }

-- Which row fired on this step (none on a stutter).
fun fired: lone Row { Case.moved' = True implies Case.last' else none }

-- The effects that move the pointer (fsm.py): RECORD_PLAN on PLAN_CREATED (R08) starts it at
-- step 1; ADVANCE_STEP on STEP_ADVANCED (R11) and DELIVERY_PLANNED (R32) moves it on; every
-- other row, and a stutter, leaves it where it is.
fact PlanPointer {
  no Plan.step
  always {
    fired = R08 implies Plan.step' = S1_CheckAppointment
    some (fired & (R11 + R32)) implies Plan.step' = Plan.step.nextStep
    no (fired & (R08 + R11 + R32)) implies Plan.step' = Plan.step
  }
}

-- The guards that read the pointer (guards.py), as preconditions of their rows.
pred PlanGuards {
  always {
    fired = R05 implies some Plan.step                          -- ReadinessInProgress: plan_hash is set
    fired = R12 implies some (Plan.step & Retrieval)            -- retrieval_action
    fired = R13 implies Plan.step = S4_SendStatusUpdate         -- delivery_action
    fired = R17 implies some (Plan.step & (S1_CheckAppointment + S2_CheckDocuments))   -- RetrievalStepsRemain
    fired = R18 implies Plan.step = S3_LoadInstructions         -- PreReadinessPhaseComplete
    fired = R32 implies Plan.step = S3_LoadInstructions         -- DeliveryStepPending (and CanAdvance)
  }
}

-- CanAdvance on STEP_ADVANCED (R11), before and after row 99.
pred CanAdvanceBefore { always (fired = R11 implies some Plan.step.nextStep) }
pred CanAdvanceNow {
  always (fired = R11 implies (some (Plan.step & Retrieval) and some (Plan.step.nextStep & Retrieval)))
}

-- Nothing is sent to the patient (Delivering) before the readiness check passed (T8).
pred NoDeliveryBeforeReady { always (Case.state = Delivering implies once Case.state = Ready) }
-- No planning without a complete plan.
pred PlanBeforePlanning { always (Case.state = Planning implies once Case.last.ev = PLAN_CREATED) }

-- P1: the guard as it was. EXPECTED counterexample - the bug: PLAN_CREATED, STEP_ADVANCED x3,
-- POLICY_ALLOWED -> Delivering.
assert P1_NoDeliveryBeforeReady_OldCanAdvance { (PlanGuards and CanAdvanceBefore) implies NoDeliveryBeforeReady }
check P1_NoDeliveryBeforeReady_OldCanAdvance for 1 but 1..20 steps

-- P2: the guard as it is now (row 99). Must hold.
assert P2_NoDeliveryBeforeReady_CanAdvanceNow { (PlanGuards and CanAdvanceNow) implies NoDeliveryBeforeReady }
check P2_NoDeliveryBeforeReady_CanAdvanceNow for 1 but 1..20 steps

-- P3: C3's property, now checked against ReadinessInProgress itself rather than assumed.
assert P3_PlanBeforePlanning_WithPlanGuards { (PlanGuards and CanAdvanceNow) implies PlanBeforePlanning }
check P3_PlanBeforePlanning_WithPlanGuards for 1 but 1..20 steps

-- ============================================================================================
-- Scenario traces under the guards as the code has them. E1 also shows P2 and P3 do not hold
-- merely because the guards forbid everything: the full scenario 1 still goes through.
-- ============================================================================================

pred Guards { PlanGuards and CanAdvanceNow }

-- Scenario 1: an automatic completion - plan, three retrievals, readiness, delivery.
run E1_AutomaticCompletion {
  Guards and eventually (Case.state = Completed and Case.last.ev = CASE_RESOLVED)
} for 1 but 1..20 steps

-- Scenario 2: a medical question reaches staff and is closed by them.
run E2_MedicalQuestionClosedByStaff {
  Guards and eventually (Case.last.ev = MEDICAL_QUESTION_DETECTED and eventually Case.last.ev = HUMAN_RESOLVED_CASE)
} for 1 but 1..20 steps

-- A missing document, the patient uploads it, and the case is classified again (T10).
run E3_MissingDocumentThenReclassified {
  Guards and eventually (Case.state = AwaitingPatientInput and eventually Case.state = Classifying)
} for 1 but 1..20 steps
