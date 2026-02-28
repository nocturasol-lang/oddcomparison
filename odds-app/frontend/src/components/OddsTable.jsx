import { OddsRow } from './OddsRow.jsx'

export function OddsTable({ odds = [], connected, lastUpdate, lastChangedIds = new Set() }) {
  const flashSet = lastChangedIds instanceof Set ? lastChangedIds : new Set()

  return (
    <div className="table-wrap">
      <table className="odds-table">
        <thead>
          <tr>
            <th className="col-time">Time</th>
            <th className="col-link">Link</th>
            <th className="col-lslink">LS Link</th>
            <th className="col-game">Game</th>
            <th className="col-market">Market</th>
            <th className="col-selection">Selection</th>
            <th className="mono col-odds">Odds</th>
            <th className="mono col-ls1">LS1</th>
            <th className="mono col-ls2">LS2</th>
            <th className="mono col-ls3">LS3</th>
            <th className="mono col-diff">Diff</th>
          </tr>
        </thead>
        <tbody>
          {odds.map((entry, i) => (
            <OddsRow
              key={entry.game_id || i}
              entry={entry}
              index={i}
              flash={flashSet.has(entry.game_id)}
            />
          ))}
        </tbody>
      </table>
      <div className="status-bar">
        <span>
          <span className={`status-dot ${connected ? 'connected' : 'disconnected'}`} />
          {connected ? 'Connected' : 'Disconnected'}
        </span>
        <span>
          Last updated: {lastUpdate ? lastUpdate.toLocaleTimeString() : '–'}
        </span>
      </div>
    </div>
  )
}
