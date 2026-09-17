import type { DailyBar } from './api'

export type CandleInterval = 'daily' | 'weekly' | 'monthly'
export const intervalLabels = { daily: '日线', weekly: '周线', monthly: '月线' }

/** Display-only aggregation: Monday-based weeks, calendar months, no fabricated days. */
export function aggregateBars(bars: DailyBar[], interval: CandleInterval): DailyBar[] {
  if (interval === 'daily') return bars
  const groups = new Map<string, DailyBar[]>()
  for (const bar of [...bars].sort((a, b) => a.date.localeCompare(b.date))) {
    const day = new Date(`${bar.date}T00:00:00Z`)
    if (interval === 'weekly') day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7)
    const key = interval === 'monthly' ? bar.date.slice(0, 7) : day.toISOString().slice(0, 10)
    const group = groups.get(key) ?? []
    group.push(bar); groups.set(key, group)
  }
  return [...groups.values()].map(group => {
    const complete = group.every(bar => [bar.open, bar.high, bar.low, bar.close].every(value => typeof value === 'number' && Number.isFinite(value)))
    return {
      date: group[0].date, open: complete ? group[0].open : null,
      high: complete ? Math.max(...group.map(bar => bar.high!)) : null,
      low: complete ? Math.min(...group.map(bar => bar.low!)) : null,
      close: complete ? group[group.length - 1].close : null,
      volume: group.every(bar => typeof bar.volume === 'number' && Number.isFinite(bar.volume)) ? group.reduce((sum, bar) => sum + bar.volume!, 0) : null,
      source: [...new Set(group.map(bar => bar.source))].join(' / '),
      adjustment_status: [...new Set(group.map(bar => bar.adjustment_status))].join(' / '),
    }
  })
}
