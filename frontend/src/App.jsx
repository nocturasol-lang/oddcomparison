import { useOddsWebSocket } from './hooks/useOddsWebSocket.js'
import { BookmakerLogos } from './components/BookmakerLogos.jsx'
import { OddsTable } from './components/OddsTable.jsx'

export default function App() {
  const { odds, connected, lastUpdate, lastChangedIds } = useOddsWebSocket()

  return (
    <div className="app">
      <h1>Live Games</h1>
      <BookmakerLogos />
      <OddsTable
        odds={odds}
        connected={connected}
        lastUpdate={lastUpdate}
        lastChangedIds={lastChangedIds}
      />
    </div>
  )
}
