import { useState, useEffect, useRef, useCallback } from 'react'

const RECONNECT_MS = 3000

function getWsUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/odds`
}

/**
 * Connect to odds WebSocket.
 * - On "full": replace all odds state
 * - On "delta": merge changed rows, remove deleted game_ids; set lastChangedIds for 800ms for flash
 * - Auto-reconnect after 3s on disconnect
 * @returns {{ odds: array, connected: boolean, lastUpdate: Date | null, lastChangedIds: Set<string> }}
 */
export function useOddsWebSocket() {
  const [odds, setOdds] = useState([])
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [lastChangedIds, setLastChangedIds] = useState(() => new Set())
  const wsRef = useRef(null)
  const reconnectTimerRef = useRef(null)
  const flashClearRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(getWsUrl())
    wsRef.current = ws

    ws.onopen = () => setConnected(true)

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'full') {
          setOdds(Array.isArray(msg.odds) ? msg.odds : [])
          setLastUpdate(new Date())
          setLastChangedIds(new Set())
        } else if (msg.type === 'delta') {
          const changed = msg.changed || []
          const ids = new Set(changed.map((o) => o?.game_id).filter(Boolean))
          setLastChangedIds(ids)
          if (flashClearRef.current) clearTimeout(flashClearRef.current)
          flashClearRef.current = setTimeout(() => setLastChangedIds(new Set()), 800)

          setOdds((prev) => {
            const byId = new Map(prev.map((o) => [o.game_id, o]))
            ;(msg.removed || []).forEach((id) => byId.delete(id))
            changed.forEach((row) => {
              if (row?.game_id) byId.set(row.game_id, row)
            })
            return Array.from(byId.values())
          })
          setLastUpdate(new Date())
        }
      } catch (_) {}
    }

    ws.onclose = () => {
      setConnected(false)
      wsRef.current = null
      reconnectTimerRef.current = setTimeout(connect, RECONNECT_MS)
    }

    ws.onerror = () => {}
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (flashClearRef.current) clearTimeout(flashClearRef.current)
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  return { odds, connected, lastUpdate, lastChangedIds }
}
