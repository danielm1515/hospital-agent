/**
 * Sub-project 18 (design D13, `docs/api.md` §5): the appointment the patient chose, the one
 * the appointment-service answered about, its department and exam type, and the instruction
 * source the case loaded - one fact group, shared by the Case Monitor's expanded row and the
 * review screen. Staff-only: every value keeps its code on screen, each code in its own
 * isolated element with the Hebrew label beside it (CLAUDE.md), and a missing value is a dash.
 */
import type { ReactNode } from 'react'
import type { CaseAppointmentFacts as Facts } from '../../api/types'
import { DEPARTMENT_LABELS } from '../../components/AppointmentsPanel'

function Code({ children }: { children: string }) {
  return (
    <span className="code" dir="ltr">
      {children}
    </span>
  )
}

function Fact({ label, code, value }: { label: string; code: string; value: ReactNode }) {
  return (
    <div className="fact">
      <dt className="fact-k">
        {label}
        <span className="fact-code mono">{code}</span>
      </dt>
      <dd className="fact-v">{value}</dd>
    </div>
  )
}

export function CaseAppointmentFacts({ facts }: { facts: Facts }) {
  const department = facts.department
  const departmentLabel = department ? DEPARTMENT_LABELS[department] : undefined

  return (
    <section className="fact-group">
      <h3 className="fact-group-h">תור והוראות הכנה</h3>
      <dl className="fact-list">
        <Fact
          label="התור שנבחר"
          code="appointment_id"
          value={facts.appointment_id ? <Code>{facts.appointment_id}</Code> : 'לא נבחר (התור הקרוב ביותר)'}
        />
        <Fact
          label="התור שנענה"
          code="answered_appointment_id"
          value={facts.answered_appointment_id ? <Code>{facts.answered_appointment_id}</Code> : '—'}
        />
        <Fact
          label="מחלקה"
          code="department"
          value={
            department ? (
              <>
                {departmentLabel && `${departmentLabel} `}
                <Code>{department}</Code>
              </>
            ) : (
              '—'
            )
          }
        />
        <Fact label="סוג בדיקה" code="exam_type_label" value={facts.exam_type_label ?? '—'} />
        <Fact
          label="מקור הוראות ההכנה"
          code="instruction_source_id"
          value={
            facts.instruction_source_id ? (
              <>
                <Code>{facts.instruction_source_id}</Code>
                {facts.instruction_version && ` גרסה ${facts.instruction_version}`}
              </>
            ) : (
              '—'
            )
          }
        />
      </dl>
    </section>
  )
}
