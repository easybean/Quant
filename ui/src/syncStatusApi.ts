export type SyncStatus = { available: boolean; message?: string; target_date?: string; generated_at?: string; active_symbols?: number; endpoint_current?: number; needs_update?: number; historical_only_symbols?: number; run_available?: boolean; run_status?: string; finished_at?: string; massive_audit?: { target_date?: string; total: number; tested: number; remaining: number; target_returned: number; empty_unknown: number; updated_at?: string; status?: string }; listing_evidence?: { observed_at?: string; target_date?: string; present_endpoint_gaps: number; absent_endpoint_gaps_unknown: number }; providers?: { provider: string; status: string; counts_available?: boolean; success: number | null; failed: number | null; attempted: number | null; skipped?: number; unmatched?: number; missing?: number; last_error_code?: string; finished_at?: string; resume_after?: string }[] }
export async function fetchSyncStatus(signal: AbortSignal): Promise<SyncStatus> {
  const base = (import.meta.env.VITE_API_BASE_URL || 'http://192.168.1.132:8511').replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/us-daily-sync-status`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error(`无法读取同步进度（HTTP ${response.status}）`)
  return response.json()
}
