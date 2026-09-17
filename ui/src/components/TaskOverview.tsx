import { useEffect, useState } from 'react'
import { AlertCircle, ClipboardList, FlaskConical, LoaderCircle, RefreshCw } from 'lucide-react'
import { fetchExperiments, fetchJobs, type Experiment, type Job } from '../api'

const format = (value: string | null) => value ? value.replace('T', ' ').replace(/\.\d+Z$/, ' UTC').replace('Z', ' UTC') : '未开始'
const statusLabel: Record<Job['status'], string> = { queued: '排队', running: '运行中', succeeded: '已发布', failed: '失败', cancelled: '已取消', blocked: '已阻断' }

export function TaskOverview({ view, pageTitle }: { view: 'jobs' | 'experiments'; pageTitle?: string }) {
  const [jobs, setJobs] = useState<Job[] | null>(null); const [experiments, setExperiments] = useState<Experiment[] | null>(null)
  const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    const load = () => (view === 'jobs' ? fetchJobs(controller.signal).then(setJobs) : fetchExperiments(controller.signal).then(setExperiments)).catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '读取状态失败') })
    setError(''); load(); const timer = window.setInterval(load, 5000)
    return () => { controller.abort(); window.clearInterval(timer) }
  }, [view, attempt])
  const loading = view === 'jobs' ? jobs === null : experiments === null
  const title = pageTitle ?? (view === 'jobs' ? '后台任务' : '研究记录')
  const description = view === 'jobs' ? '仅轮询持久化任务状态；关闭浏览器不会停止已提交任务。仅合成验收样例可从“新建回测”提交，正式回测仍受门禁保护。' : '仅显示已原子发布的可复现研究记录；失败与取消任务不被伪装为实验结果。'
  return <div className="task-overview"><section className="page-heading"><div><p className="eyebrow">{view === 'jobs' ? '历史回测 · 后台任务' : '研究工具 · 可复现记录'}</p><h1>{title}</h1><p>{description}</p></div><span className="static-boundary">只读轮询 · 5 秒</span></section>
    {error ? <section className="empty-state wide"><AlertCircle size={24}/><strong>无法读取{title}</strong><p>{error}</p><button className="detail-action data-retry" onClick={() => setAttempt(value => value + 1)}><RefreshCw size={15}/>重新读取</button></section> : loading ? <section className="empty-state wide"><LoaderCircle className="animate-spin" size={24}/><strong>正在读取{title}</strong><p>请求只读取 SQLite 中的任务与实验索引，不会启动或重试任务。</p></section> : view === 'jobs' ? <JobList jobs={jobs!}/> : <ExperimentList experiments={experiments!}/>}</div>
}

function JobList({ jobs }: { jobs: Job[] }) {
  if (!jobs.length) return <Empty icon={<ClipboardList size={25}/>} title="暂无任务" text="尚未提交研究记录任务。回测提交仍受 P3-03 运行器与成本模型门禁保护。" />
  return <section className="task-panel"><header><div><h2>任务队列</h2><p>状态与失败原因来自服务端持久化记录。</p></div><span>{jobs.length} 项</span></header><div className="task-list">{jobs.map(job => <article key={job.id} className="task-row"><div><strong>{job.kind === 'research' ? '研究记录' : '回测'} · {job.operation}</strong><p>{job.data_snapshot} · {job.code_version} · {format(job.created_at)}</p>{job.failure ? <p className="task-failure">{job.failure.code}：{job.failure.message}</p> : null}</div><div><span className={`task-status ${job.status}`}>{statusLabel[job.status]}</span><small>{job.cancel_requested ? '已请求取消' : job.artifacts.length ? `${job.artifacts.length} 个产物` : `尝试 ${job.attempts} 次`}</small></div></article>)}</div></section>
}
function ExperimentList({ experiments }: { experiments: Experiment[] }) {
  if (!experiments.length) return <Empty icon={<FlaskConical size={25}/>} title="暂无已完成研究记录" text="研究任务成功生成不可变产物索引后，才会出现在此处；不会显示示例收益、净值或虚构结论。" />
  return <section className="task-panel"><header><div><h2>已完成研究记录</h2><p>策略、参数、数据快照、代码版本及产物索引均可追溯。</p></div><span>{experiments.length} 项</span></header><div className="task-list">{experiments.map(item => <article key={item.id} className="task-row"><div><strong>{String(item.strategy.template || '未命名策略')}</strong><p>{item.data_snapshot} · {item.code_version} · {format(item.created_at)}</p><p>参数：{Object.keys(item.parameters).length ? Object.entries(item.parameters).map(([key, value]) => `${key}=${String(value)}`).join(' · ') : '无'}</p></div><div><span className="task-status succeeded">已发布</span><small>{item.artifacts.length} 个产物</small></div></article>)}</div></section>
}
function Empty({ icon, title, text }: { icon: React.ReactNode; title: string; text: string }) { return <section className="task-empty">{icon}<strong>{title}</strong><p>{text}</p><span>不会自动创建任务</span></section> }
