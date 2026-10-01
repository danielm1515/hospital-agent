import { useEffect, useState } from 'react'
import * as api from '../../api/client'
import type { Consistency } from '../../api/types'
import { Alert } from '../../components/Alert'
import { Button } from '../../components/Button'
import { Loading } from '../../components/Loading'
import { detailOf } from './labels'

/** The nine §9.2 queries in Hebrew, keyed by the description the API returns. */
export const CONSISTENCY_LABELS: Record<string, string> = {
  'OPA allows what Prolog blocks': 'OPA לא מאשר מה ש-Prolog חוסם',
  'Prolog allows what Temporal forbids': 'Prolog לא מאשר מה שכללי הזמן אוסרים',
  'OPA allows an unminimized flow': 'OPA לא מאשר זרימת שדות שלא עברה מזעור (Datalog)',
  'medical answer without approval': 'אין תשובה רפואית בלי אישור אנושי',
  'execution without patient context': 'אין ביצוע בלי פרטי מטופל, פנייה וביצוע',
  'high-risk action without policy override': 'אין פעולה בסיכון גבוה בלי אישור חריגה',
  'allow after attempts exhausted': 'אין אישור אחרי מיצוי הניסיונות',
  'unevaluated patient-facing output': 'אין פלט למטופל שלא עבר הערכה',
  'unapproved instruction source': 'אין הוראות ממקור לא מאושר',
}

/**
 * The spec §9.2 cross-layer consistency proofs (`GET /api/admin/consistency`): Z3 looks for
 * a situation in which two control layers disagree, or a layer lets through what the design
 * forbids; UNSAT - no such situation - proves the property. They are about the system, not a
 * case, so they live here and never in a case's audit journal. Run on demand, independent
 * of the metrics window.
 */
export function ConsistencyGroup() {
  const [data, setData] = useState<Consistency | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const run = () => {
    setRunning(true)
    setError(null)
    api
      .getConsistency()
      .then((result) => setData(result))
      .catch((failure: unknown) => setError(detailOf(failure)))
      .finally(() => setRunning(false))
  }

  useEffect(run, [])

  return (
    <section className="card metrics-group" aria-labelledby="consistency-h">
      <h2 className="section-h" id="consistency-h">
        עקביות בין שכבות הבקרה (Z3)
      </h2>
      <p className="metrics-scope">
        הוכחות §9.2: Z3 מחפש מצב שבו שתי שכבות סותרות זו את זו או מעבירות מה שאסור. UNSAT = אין מצב כזה, והתכונה
        מוכחת. ההוכחות הן על המערכת ולא על פנייה, ולכן אינן מופיעות ביומן של פנייה.
      </p>
      {error && (
        <Alert variant="error">
          ההוכחות לא רצו <span className="mono">{error}</span>
        </Alert>
      )}
      {data === null ? (
        !error && <Loading label="מריץ את Z3" size="inline" />
      ) : (
        <>
          <p className={`consistency-verdict ${data.all_proved ? 'proved' : 'failed'}`}>
            {data.all_proved
              ? `כל ${data.queries.length} השאילתות החזירו UNSAT: 7 התכונות מוכחות`
              : 'לפחות תכונה אחת לא הוכחה'}
          </p>
          <ul className="consistency-list">
            {data.queries.map((query) => (
              <li className={`consistency-item ${query.proved ? 'proved' : 'failed'}`} key={query.description}>
                <span className="consistency-prop mono">{query.property}</span>
                <span className="consistency-text">
                  {CONSISTENCY_LABELS[query.description] ?? query.description}
                  <span className="consistency-code">{query.description}</span>
                </span>
                <span className="consistency-result mono">{query.result.toUpperCase()}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <Button variant="secondary" onClick={run} disabled={running}>
        {running ? 'מריץ…' : 'הרצה חוזרת'}
      </Button>
    </section>
  )
}
