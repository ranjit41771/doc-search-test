import { apiClient } from './client'
import type { DocumentDetail, DocumentUploadResponse } from '../types'

export interface UploadParams {
  file: File
  title?: string
  tags?: string
}

export async function uploadDocument(params: UploadParams): Promise<DocumentUploadResponse> {
  const form = new FormData()
  form.append('file', params.file)
  if (params.title) form.append('title', params.title)
  if (params.tags) form.append('tags', params.tags)

  const { data } = await apiClient.post<DocumentUploadResponse>('/documents', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

export async function getDocument(docId: string): Promise<DocumentDetail> {
  const { data } = await apiClient.get<DocumentDetail>(`/documents/${docId}`)
  return data
}

export async function deleteDocument(docId: string): Promise<void> {
  await apiClient.delete(`/documents/${docId}`)
}

export function getDownloadUrl(docId: string): string {
  const base = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
  return `${base}/documents/${docId}/download`
}
