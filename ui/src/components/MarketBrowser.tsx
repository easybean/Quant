import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, BarChart3, LoaderCircle, Search, ShieldAlert } from 'lucide-react'
import { CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, createChart, type CandlestickData, type Time } from 'lightweight-charts'
import { fetchDailyBars, searchDailySeries, type DailyBar, type DailyBarsResponse, type DailySeries } from '../api'

import { aggregateBars, intervalLabels, type CandleInterval } from '../marketBars'

async function fetchHistory(series: DailySeries): Promise<DailyBarsResponse> {
  const last = Date.parse(series.last_date)
  const first = Date.parse(series.first_date)
  if (!Number.isFinite(first) || !Number.isFinite(last) || first > last) throw new Error('该来源的行情覆盖日期无效')
  const day = 86400000
  const start = first
  const bars: DailyBar[] = []
  const sources = new Set<string>()
  const adjustments = new Set<string>()
  let result: DailyBarsResponse | null = null
  // The UI has no user-entered cutoff; retain the API's bounded chunk contract.
  for (let cursor = start; cursor <= last; cursor += 366 * day) {
    const end = Math.min(last, cursor + 365 * day)
    result = await fetchDailyBars(series.series_id, new Date(cursor).toISOString().slice(0, 10), new Date(end).toISOString().slice(0, 10), new AbortController().signal)
    bars.push(...result.bars)
    result.source.forEach(value => sources.add(value))
    result.adjustment_status.forEach(value => adjustments.add(value))
  }
  if (!result) throw new Error('没有可读取的行情区间')
  return { ...result, bars, source: [...sources], adjustment_status: [...adjustments], returned_rows: bars.length, requested_range: { start: new Date(start).toISOString().slice(0, 10), end: series.last_date } }
}

export function MarketBrowser() {
  const [query, setQuery] = useState('SPY')
  const [items, setItems] = useState<DailySeries[]>([])
  const [selected, setSelected] = useState<DailySeries | null>(null)
  const [interval, setInterval] = useState<CandleInterval>('daily')
  const [data, setData] = useState<DailyBarsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searched, setSearched] = useState(false)

  async function search() {
    if (loading) return
    const value = query.trim().toUpperCase()
    if (!/^[A-Z0-9.$_-]{1,32}$/.test(value)) { setError('请输入有效证券代码，例如 TSLA 或 ABR$D。'); return }
    setLoading(true); setError(''); setData(null); setSearched(true)
    try {
      const found = await searchDailySeries(value, new AbortController().signal)
      setItems(found); setSelected(found[0] ?? null)
      if (found[0]) setData(await fetchHistory(found[0]))
    } catch (reason) { setError(reason instanceof Error ? reason.message : '行情浏览暂不可用') }
    finally { setLoading(false) }
  }
  async function loadSeries(series: DailySeries) {
    if (loading) return
    setSelected(series); setLoading(true); setError(''); setData(null)
    try { setData(await fetchHistory(series)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '原始日线暂不可用') }
    finally { setLoading(false) }
  }
  const chartBars = useMemo(() => aggregateBars(data?.bars ?? [], interval), [data, interval])
  const title = useMemo(() => data ? `${data.series.symbol} · ${intervalLabels[interval]}行情` : '选择一只股票', [data, interval])

  return <div className="market-browser-page">
    <section className="page-heading"><div><p className="eyebrow">行情与资料 · 股票行情</p><h1>行情浏览</h1><p>同一只股票统一展示历史行情与每日更新；原始来源保留。浏览行情不代表复权、总回报或回测数据已验证。</p></div><span className="static-boundary">只读 · 行情浏览</span></section>
    <section className="quote-controls" aria-label="股票搜索">
      <label><Search size={16}/><span className="required-mark" aria-hidden="true">*</span><input required value={query} onChange={event => setQuery(event.target.value.toUpperCase())} maxLength={32} placeholder="输入证券代码，如 SPY" aria-label="搜索证券代码（必填）" onKeyDown={event => { if (event.key === 'Enter') void search() }} /></label>
      <button type="button" className="quote-primary" disabled={loading} onClick={() => void search()}>{loading ? '读取中…' : '搜索行情'}</button>
      {selected ? <button type="button" className="quote-secondary" disabled={loading} onClick={() => void search()}>刷新行情</button> : null}
    </section>
    {error ? <section className="empty-state wide"><AlertCircle size={23}/><strong>无法读取日线</strong><p>{error}</p></section> : loading ? <section className="empty-state wide"><LoaderCircle size={23} className="animate-spin"/><strong>正在读取行情</strong><p>自动读取这只股票的最新行情；全部历史分段加载。</p></section> : <section className="quote-grid">
      <aside className="quote-series"><header><strong>搜索结果</strong><span>{items.length} 只股票</span></header>{items.length ? items.map(item => <button type="button" key={item.series_id} className={selected?.series_id === item.series_id ? 'is-active' : ''} onClick={() => void loadSeries(item)}><strong>{item.symbol}</strong><span>日线行情</span><small>{item.first_date} — {item.last_date}</small></button>) : <p>{searched ? '没有匹配的已索引股票。' : '输入股票代码后搜索。'}</p>}</aside>
      <article className="quote-panel"><header><div><h2>{title}</h2><p>{data ? `${chartBars.length} 根${intervalLabels[interval]} · ${data.requested_range.start} 至 ${data.requested_range.end}` : '搜索后选择一只股票。'}</p></div><div className="quote-intervals" role="group" aria-label="K线周期">{(['daily', 'weekly', 'monthly'] as CandleInterval[]).map(value => <button key={value} type="button" aria-pressed={interval === value} className={interval === value ? 'is-active' : ''} onClick={() => setInterval(value)}>{intervalLabels[value]}</button>)}</div></header>{data ? <><CandleChart bars={chartBars}/>{interval !== 'daily' ? <p className="quote-attribution">由已有日线按自然周（周一开始）/自然月汇总；首尾周期可能未完整结束，缺失行情不补造，仅供浏览。</p> : null}<div className="quote-meta"><span>数据来源：{data.source.map(sourceName).join('、') || '未记录'}</span><span>口径标记：{data.adjustment_status.join('、') || '未记录'}（未经统一复权验证）</span></div><div className="boundary-callout"><ShieldAlert size={17}/><p>{data.quality_warning} {data.limitations.join(' ')}</p></div></> : <div className="empty-state"><Search size={22}/><strong>尚未选择股票</strong><p>无数据时不会使用演示 K 线替代。</p></div>}</article>
    </section>}
  </div>
}

function sourceName(value: string) { return value === 'massive' ? 'Massive（日线）' : value === 'nasdaq_web_unadjusted' ? 'Nasdaq（历史）' : value === 'yfinance' ? 'Yahoo（更新）' : value }

function CandleChart({ bars }: { bars: DailyBar[] }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [dark, setDark] = useState(() => document.documentElement.dataset.theme === 'dark')
  const valid = useMemo(() => bars.filter(bar => [bar.open, bar.high, bar.low, bar.close].every(value => typeof value === 'number')), [bars])
  const [crosshair, setCrosshair] = useState<CandlestickData<Time> | null>(() => toCandle(valid.at(-1)))

  useEffect(() => {
    const root = document.documentElement
    const observer = new MutationObserver(() => setDark(root.dataset.theme === 'dark'))
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  useEffect(() => setCrosshair(toCandle(valid.at(-1))), [valid])
  useEffect(() => {
    const host = hostRef.current
    if (!host || !valid.length) return
    const palette = dark
      ? { background: '#17212d', text: '#9eafc2', grid: '#2a3949', border: '#344557', up: '#26a69a', down: '#ef5350', volumeUp: 'rgba(38, 166, 154, .45)', volumeDown: 'rgba(239, 83, 80, .45)' }
      : { background: '#ffffff', text: '#64748b', grid: '#edf1f5', border: '#dbe3ec', up: '#0f9b8e', down: '#c25b57', volumeUp: 'rgba(15, 155, 142, .35)', volumeDown: 'rgba(194, 91, 87, .35)' }
const chart = createChart(host, { width: host.clientWidth, height: 390, layout: { background: { type: ColorType.Solid, color: palette.background }, textColor: palette.text, attributionLogo: true }, grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } }, rightPriceScale: { borderColor: palette.border }, timeScale: { borderColor: palette.border, timeVisible: false, secondsVisible: false, rightOffset: 4, barSpacing: 8, minBarSpacing: .01 }, crosshair: { mode: CrosshairMode.Normal, vertLine: { color: palette.border, labelBackgroundColor: palette.up }, horzLine: { color: palette.border, labelBackgroundColor: palette.up } }, handleScroll: true, handleScale: true })
    const candles = chart.addSeries(CandlestickSeries, { upColor: palette.up, downColor: palette.down, borderVisible: false, wickUpColor: palette.up, wickDownColor: palette.down, priceLineVisible: false })
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '', lastValueVisible: false, priceLineVisible: false })
    candles.setData(valid.map(toCandle).filter((bar): bar is CandlestickData<Time> => bar !== null))
    volume.setData(valid.flatMap(bar => typeof bar.volume === 'number' ? [{ time: bar.date as Time, value: bar.volume, color: (bar.close as number) >= (bar.open as number) ? palette.volumeUp : palette.volumeDown }] : []))
    candles.priceScale().applyOptions({ scaleMargins: { top: .08, bottom: .28 } })
    volume.priceScale().applyOptions({ scaleMargins: { top: .76, bottom: 0 } })
    chart.timeScale().fitContent()
    const onCrosshairMove = (event: { seriesData: Map<unknown, unknown> }) => { const bar = event.seriesData.get(candles) as CandlestickData<Time> | undefined; if (bar) setCrosshair(bar) }
    chart.subscribeCrosshairMove(onCrosshairMove)
    const resize = () => chart.applyOptions({ width: host.clientWidth })
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(host)
    return () => { resizeObserver.disconnect(); chart.unsubscribeCrosshairMove(onCrosshairMove); chart.remove() }
  }, [dark, valid])

  if (!bars.length) return <div className="empty-state"><BarChart3 size={22}/><strong>该日期区间没有已记录日线</strong><p>这不等同于停牌或没有交易；请查看来源覆盖范围。</p></div>
  if (!valid.length) return <div className="empty-state"><AlertCircle size={22}/><strong>该区间日线缺少可绘制 OHLC</strong><p>质量问题已保留，系统不会补造价格。</p></div>
  return <div className="quote-chart-shell"><div className="quote-crosshair" aria-live="polite"><strong>{formatTime(crosshair?.time)}</strong><span>开 {formatNumber(crosshair?.open)}</span><span>高 {formatNumber(crosshair?.high)}</span><span>低 {formatNumber(crosshair?.low)}</span><span>收 {formatNumber(crosshair?.close)}</span><span>量 {formatVolume(volumeForTime(bars, crosshair?.time))}</span><span>来源 {sourceName(bars.find(bar => bar.date === formatTime(crosshair?.time))?.source || '未记录')}</span></div><div ref={hostRef} className="quote-chart" aria-label="可缩放、可平移的K线蜡烛图与成交量"/><p className="quote-attribution">图表基于 <a href="https://www.tradingview.com/lightweight-charts/" target="_blank" rel="noreferrer">TradingView Lightweight Charts™</a>；行情只来自内网只读 API。</p></div>
}

function toCandle(bar: DailyBar | undefined): CandlestickData<Time> | null {
  if (!bar || [bar.open, bar.high, bar.low, bar.close].some(value => typeof value !== 'number')) return null
  return { time: bar.date as Time, open: bar.open as number, high: bar.high as number, low: bar.low as number, close: bar.close as number }
}
function formatTime(time: Time | undefined) { if (typeof time === 'string') return time; if (typeof time === 'number') return new Date(time * 1000).toISOString().slice(0, 10); return time ? `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}` : '—' }
function formatNumber(value: number | null | undefined) { return typeof value === 'number' ? value.toFixed(2) : '—' }
function formatVolume(value: number | null | undefined) { return typeof value === 'number' ? new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(value) : '—' }
function volumeForTime(bars: DailyBar[], time: Time | undefined) { return bars.find(bar => bar.date === time)?.volume }
