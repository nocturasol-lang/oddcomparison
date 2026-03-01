import { useMemo } from 'react'

/**
 * Single row: Time | Link | LS Link | Game | Market | Selection | Odds | LS1 | LS2 | LS3 | Diff
 * - is_value: LS1/LS2/LS3 cells red #cc0000, odds top line, liquidity below
 * - flash: yellow 800ms then normal
 * - diff > 0 green, diff < 0 red
 */
export function OddsRow({ entry, index, flash }) {
  const rowClass = index % 2 === 0 ? 'row-even' : 'row-odd'
  const flashClass = flash ? 'flash' : ''

  const diffClass = useMemo(() => {
    const d = entry.diff
    if (d == null) return 'diff-zero'
    if (d > 0) return 'diff-positive'
    if (d < 0) return 'diff-negative'
    return 'diff-zero'
  }, [entry.diff])

  const formatNum = (n) => (n != null && Number.isFinite(n) ? n.toFixed(2) : '–')

  const lsCell = (val, avail, isValue, colClass) => {
    if (isValue && val != null && val > 0) {
      return (
        <td className={`mono ${colClass} cell-value`}>
          {formatNum(val)}
          {avail != null && avail > 0 && (
            <span className="liquidity">€{formatNum(avail)}</span>
          )}
        </td>
      )
    }
    return <td className={`mono ${colClass}`}>{formatNum(val)}</td>
  }

  return (
    <tr className={`${rowClass} ${flashClass}`}>
      <td className="col-time mono">{entry.game_time || '–'}</td>
      <td className="col-link">–</td>
      <td className="col-lslink">–</td>
      <td className="col-game">{entry.game_name || '–'}</td>
      <td className="col-market">{entry.market || '–'}</td>
      <td className="col-selection">{entry.selection || '–'}</td>
      <td className="mono col-odds">{formatNum(entry.back_odds)}</td>
      {lsCell(entry.ls1, entry.lay_available, entry.is_value, 'col-ls1')}
      {lsCell(entry.ls2, null, entry.is_value, 'col-ls2')}
      {lsCell(entry.ls3, null, entry.is_value, 'col-ls3')}
      <td className={`mono col-diff ${diffClass}`}>{formatNum(entry.diff)}</td>
    </tr>
  )
}
