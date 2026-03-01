const BOOKMAKERS = [
  { id: 'novibet', letter: 'N' },
  { id: 'stoiximan', letter: 'S' },
  { id: 'betshop', letter: 'B' },
  { id: 'laystars', letter: 'L' },
  { id: 'other1', letter: 'O' },
  { id: 'other2', letter: 'X' },
]

export function BookmakerLogos() {
  return (
    <div className="bookmaker-row">
      {BOOKMAKERS.map((b) => (
        <div key={b.id} className="bookmaker-logo" title={b.id}>
          {b.letter}
        </div>
      ))}
    </div>
  )
}
