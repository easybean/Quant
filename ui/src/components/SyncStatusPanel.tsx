import { useEffect, useState } from 'react'
import { fetchSyncStatus, type SyncStatus } from '../syncStatusApi'

const names: Record<string, string> = { massive: 'Massive 全市场日线', 'alpaca-mapped': 'Alpaca 已核对代码映射', alpaca: 'Alpaca SIP 备源', yahoo: 'Yahoo', nasdaq: 'Nasdaq 备源' }
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
      {data.listing_evidence && <p>官方当前上市名单交叉核对（{time(data.listing_evidence.observed_at)}；目标日 {data.listing_evidence.target_date || '未知'}）：核对时的末日缺口中，{data.listing_evidence.present_endpoint_gaps} 个代码仍在当前名单；{data.listing_evidence.absent_endpoint_gaps_unknown} 个未在名单中找到，生命周期或代码映射待确认。后者不是“已退市”结论，也未从缺口中删除。</p>}
      {data.providers?.map(row => <div className="sync-provider-row" key={row.provider}><strong>{names[row.provider]}</strong><span>{states[row.status] || row.status} · {row.counts_available === false ? '阶段执行失败，未提供次数统计' : `尝试 ${row.attempted} / 成功 ${row.success} / 失败 ${row.failed}`}</span><small>结束：{time(row.finished_at)}{row.resume_after ? `；冷却至 ${time(row.resume_after)}` : ''}{row.last_error_code ? `；${errors[row.last_error_code] || '同步异常'}（${row.last_error_code}）` : ''}</small></div>)}
      {data.providers?.filter(row => row.provider === 'massive').map(row => <p key="massive-coverage">Massive 本批复用已完成记录 {row.skipped ?? 0} 个；返回代码未精确匹配 {row.unmatched ?? 0} 个；计划内未返回 {row.missing ?? 0} 个。未匹配或未返回不等于退市，也未从补数计划删除。</p>)}
      {data.massive_audit && <p>Massive 逐证券验证（目标日 {data.massive_audit.target_date}）：已测试 {data.massive_audit.tested.toLocaleString()} / {data.massive_audit.total.toLocaleString()}，剩余 {data.massive_audit.remaining.toLocaleString()}；目标日有返回 {data.massive_audit.target_returned.toLocaleString()}。验证状态：{data.massive_audit.status === 'complete' ? '查询完成' : data.massive_audit.status === 'blocked' ? '受阻，请检查错误' : '后台继续执行'}。窗口内无结果 {data.massive_audit.empty_unknown}，不等于退市。更新时间：{time(data.massive_audit.updated_at)}。</p>}
      <p className="data-note">末日达到目标不代表中间无缺口、证券身份已核验或可用于正式回测。官方新发现代码会追加进入采集清单，覆盖数字以实际清单更新时间为准；不能宣称已发现所有证券。个股最新日期在“行情浏览”查看。</p>
    </>}
  </section>
}
