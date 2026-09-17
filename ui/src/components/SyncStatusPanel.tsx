import { useEffect, useState } from 'react'
import { fetchSyncStatus, type SyncStatus } from '../syncStatusApi'

const names: Record<string, string> = { alpaca: 'Alpaca SIP 备源', yahoo: 'Yahoo', nasdaq: 'Nasdaq 备源' }
const states: Record<string, string> = { running: '运行中', partial: '仍有缺口', success: '本次请求成功', failed: '本次存在失败', endpoint_complete_audit_pending: '末日更新完成，历史审查未完成' }
const time = (value?: string) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未记录'
const errors: Record<string, string> = { http_401: '凭证未获授权', http_403: '来源权限不足', http_429: '供应商限流，冷却后重试', response_date_outside_requested_window: '返回日期超出请求窗口', download_or_storage_failed: '下载或存储失败', download_or_validation_failed: '下载或数据校验失败', empty_history_unknown: '供应商返回空历史，原因未确定', symbol_request_rejected: '供应商拒绝该股票代码请求' }
export function SyncStatusPanel() {
  const [data, setData] = useState<SyncStatus | null>(null)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController(); setError('')
    fetchSyncStatus(controller.signal).then(setData).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '同步状态读取失败') })
    return () => controller.abort()
  }, [refresh])
  return <section className="data-card sync-status-panel" aria-label="每日数据同步进度"><header><div><h2>每日数据同步进度</h2><p>服务器定时执行，不需要保持网页打开。</p></div><button type="button" className="detail-action" onClick={() => setRefresh(value => value + 1)}>刷新同步进度</button></header>
    {error ? <p role="alert">{error}；此前显示的状态不作为最新证据。</p> : !data ? <p role="status">正在读取同步进度…</p> : !data.available ? <p>{data.message}</p> : <>
      <p>目标交易日：<strong>{data.target_date || '未知'}</strong> · 覆盖清单更新时间：{time(data.generated_at)}</p>
      <p>计划内活跃证券 {data.active_symbols?.toLocaleString()} 只；末日达到目标 {data.endpoint_current?.toLocaleString()} 只；末日仍待更新 {data.needs_update?.toLocaleString()} 只。历史退市等不要求日更的代码：{data.historical_only_symbols?.toLocaleString()}。</p>
      <p>总任务：{data.run_available ? states[data.run_status || ''] || data.run_status || '未知' : '任务摘要不可用'}。各来源记录可能来自不同批次，以各自行的结束时间为准。</p>
      {data.providers?.map(row => <div className="sync-provider-row" key={row.provider}><strong>{names[row.provider]}</strong><span>{states[row.status] || row.status} · 尝试 {row.attempted} / 成功 {row.success} / 失败 {row.failed}</span><small>结束：{time(row.finished_at)}{row.resume_after ? `；冷却至 ${time(row.resume_after)}` : ''}{row.last_error_code ? `；${errors[row.last_error_code] || '同步异常'}（${row.last_error_code}）` : ''}</small></div>)}
      <p className="data-note">末日达到目标不代表中间无缺口、证券身份已核验或可用于正式回测。个股最新日期在“行情浏览”查看；这些数字也不包含主表尚未收录的新上市证券。</p>
    </>}
  </section>
}
