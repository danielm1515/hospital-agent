import type { DataLogEntry } from '../../api/types'
import { formatDateTime } from './labels'

/**
 * The case's correspondence, from the Data Log of `docs/api.md` §5 - the only place
 * content lives (§12.3). Entries come back oldest first, already without the ones a
 * patient had deleted and the uploads the case never accepted, so the thread shows
 * exactly what was said and nothing the system rejected or a tombstone removed.
 *
 * Each bubble carries the start of its `content_hash`, which is what the Audit row for
 * the same moment holds - that is how a reader ties a message to the trace above.
 */

type Side = 'from-patient' | 'to-patient' | 'internal'

const KIND_TEXT: Record<string, { who: string; side: Side }> = {
  request_text: { who: 'המטופל כתב', side: 'from-patient' },
  uploaded_document: { who: 'המטופל העלה מסמך', side: 'from-patient' },
  outgoing_message: { who: 'נשלח למטופל', side: 'to-patient' },
  instructions: { who: 'נטען מהמקור המאושר', side: 'internal' },
}

/** A kind this version does not know is still shown, as an internal note. */
const UNKNOWN_KIND: { who: string; side: Side } = { who: 'רשומת תוכן', side: 'internal' }

export function PatientThread({ entries }: { entries: DataLogEntry[] }) {
  if (entries.length === 0) {
    return <p className="empty-note">לא נשמר תוכן לפנייה הזו, או שכל התוכן נמחק לבקשת המטופל.</p>
  }
  return (
    <ol className="thread">
      {entries.map((entry) => {
        const text = KIND_TEXT[entry.kind] ?? UNKNOWN_KIND
        return (
          <li className={`thread-item ${text.side}`} key={entry.entry_id}>
            <div className="thread-head">
              <span className="thread-who">{text.who}</span>
              <span className="thread-kind mono">{entry.kind}</span>
              <time className="thread-time" dateTime={entry.created_at}>
                {formatDateTime(entry.created_at)}
              </time>
            </div>
            <p className="thread-text">{entry.content}</p>
            <p className="thread-hash mono" title={entry.content_hash}>
              {entry.content_hash.slice(0, 12)}…
            </p>
          </li>
        )
      })}
    </ol>
  )
}
