import { apiClient } from './client'
import type { LoginRequest, RegisterRequest, TokenResponse } from '../types'

export async function login(body: LoginRequest): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/auth/login', body)
  return data
}

export async function register(body: RegisterRequest): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>('/auth/register', body)
  return data
}
