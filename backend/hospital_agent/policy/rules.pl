% Hospital Patient Agent - authorization reasoning, spec §10 verbatim.
% The seven CASE-482 example facts of §10 live in backend/tests/fixtures/case_482.pl;
% at run time the Policy Service loads each request's facts into a fresh engine.
role(patient_agent, automation).
role(coordinator_nurse, clinical_staff).
role(admin_coordinator, admin_staff).
action_requires_role(check_appointment, automation).
action_requires_role(check_documents, automation).
action_requires_role(load_instructions, automation).
action_requires_role(send_status_update, automation).
action_requires_role(close_medical_case, clinical_staff).
action_requires_role(close_medical_case, admin_staff).
action_requires_role(answer_clinical_q, clinical_staff).
automatic_action(check_appointment).
automatic_action(check_documents).
automatic_action(load_instructions).
automatic_action(send_status_update).
human_workflow_action(close_medical_case).
medical_action(answer_clinical_q).
medical_action(send_status_update) :- outgoing_medical_content(true).
:- dynamic workflow_decision_valid/2. % (Case, Action), validated for the requested event
:- dynamic current_execution/1, current_patient/1, current_content_hash/1.
:- dynamic case_execution/2, case_patient/2, case_identity_verified/1, case_step/2.
:- dynamic outgoing_message_evaluated/0, outgoing_medical_content/1.
:- dynamic content_approval_valid/4. % (ExecId, PatientId, Action, ContentHash)
% Approval facts are supplied only after full Approval Validator checks.
% All request-local facts are cleared and reloaded in an isolated query context.
nonempty_atom(X) :- atom(X), X \== '', X \== null.
human_authorized(Action) :-
    content_approval_valid(E, P, Action, H),
    current_execution(E), current_patient(P), current_content_hash(H),
    nonempty_atom(E), nonempty_atom(P), nonempty_atom(H).
case_context_present(Case) :-
    nonempty_atom(Case), case_patient(Case, P), current_patient(P), nonempty_atom(P).
patient_context_present(Case) :-
    case_context_present(Case), case_execution(Case, E), current_execution(E), nonempty_atom(E).
message_evaluated :-
    outgoing_message_evaluated, outgoing_medical_content(Flag), memberchk(Flag,[true,false]),
    current_content_hash(H), nonempty_atom(H).
% A workflow closure may precede identity verification, plans and tool executions.
allowed(Actor, Action, Case) :-
    human_workflow_action(Action), role(Actor, Role), action_requires_role(Action, Role),
    case_context_present(Case), workflow_decision_valid(Case, Action).
allowed(Actor, Action, Case) :-
    \+ human_workflow_action(Action), role(Actor, Role), action_requires_role(Action, Role),
    case_identity_verified(Case), patient_context_present(Case), \+ blocked(Action, Case).
blocked(Action, _) :- medical_action(Action), \+ human_authorized(Action).
blocked(Action, Case) :- automatic_action(Action), \+ case_step(Case, Action).
blocked(Action, Case) :- automatic_action(Action), \+ patient_context_present(Case).
blocked(send_status_update, _) :- \+ message_evaluated.
blocked(Action, Case) :- human_workflow_action(Action), \+ workflow_decision_valid(Case, Action).
explain(A, Act, C, allowed) :- allowed(A, Act, C), !.
explain(A, Act, _, reason(actor_role_mismatch, Act)) :-
    \+ (role(A, R), action_requires_role(Act, R)), !.
explain(_, Act, _, reason(medical_action_requires_approval, Act)) :-
    medical_action(Act), \+ human_authorized(Act), !.
explain(_, Act, C, reason(action_not_current_step, Act)) :-
    automatic_action(Act), \+ case_step(C, Act), !.
explain(_, Act, C, reason(workflow_decision_missing, Act)) :-
    human_workflow_action(Act), \+ workflow_decision_valid(C, Act), !.
explain(_, send_status_update, _, reason(message_not_evaluated, send_status_update)) :-
    \+ message_evaluated, !.
explain(_, Act, C, reason(patient_context_missing, Act)) :-
    \+ patient_context_present(C), !.
explain(_, Act, _, reason(unspecified_block, Act)).
