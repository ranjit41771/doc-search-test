import { apiClient } from './client'
import type { SearchResponse } from '../types'

export interface SearchParams {
  q: string
  page?: number
  size?: number
  signal?: AbortSignal
}

export async function searchDocuments(params: SearchParams): Promise<SearchResponse> {
  const { data } = await apiClient.get<SearchResponse>('/search', {
    params: {
      q: params.q,
      page: params.page ?? 1,
      size: params.size ?? 10,
    },
    signal: params.signal,
  })
  return data
}
