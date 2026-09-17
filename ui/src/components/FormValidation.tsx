import type { ReactNode } from 'react'

export type FormErrors = Record<string, string>

export function RequiredMark() {
  return <span className="required-mark" aria-hidden="true">*</span>
}

export function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <span id={id} className="field-error" role="alert">{message}</span> : null
}

export function labelledField(
  id: string,
  error?: string,
): { id: string; required: true; 'aria-invalid': boolean; 'aria-describedby'?: string } {
  return { id, required: true, 'aria-invalid': Boolean(error), 'aria-describedby': error ? `${id}-error` : undefined }
}

export function focusFirstError(errors: FormErrors, fieldIds: Record<string, string>) {
  const first = Object.keys(errors).find(key => errors[key] && fieldIds[key])
  if (first) requestAnimationFrame(() => document.getElementById(fieldIds[first])?.focus())
}

/** Map the backend's deliberately terse validation messages back to a known field. */
export function backendFieldErrors(reason: unknown, knownFields: readonly string[]): FormErrors {
  const message = reason instanceof Error ? reason.message : String(reason)
  const matched = knownFields.find(field => new RegExp(`\\b${field}\\b`, 'i').test(message))
  return matched ? { [matched]: message } : {}
}

export function withRequired(label: ReactNode) {
  return <>{label} <RequiredMark /></>
}
