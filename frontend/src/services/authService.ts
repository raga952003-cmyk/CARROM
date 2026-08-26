/**
 * Authentication Service
 * Handles login, signup, and token management
 */

import { apiClient } from '../utils/apiClient';

export interface SignUpData {
  email: string;
  password: string;
  name: string;
  role: 'admin' | 'player';
  club?: string;
  city?: string;
  phone?: string;
  rating?: number;
}

export interface LoginData {
  email: string;
  password: string;
  role: 'admin' | 'player';
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: {
    id: string;
    email: string;
    name: string;
    role: string;
    club?: string;
    city?: string;
    phone?: string;
    rating: number;
    created_at: string;
  };
}

export const authService = {
  /**
   * Sign up a new user
   */
  async signUp(data: SignUpData): Promise<AuthResponse> {
    const response = await apiClient.post<AuthResponse>('/auth/signup', data);
    
    // Store token
    apiClient.setToken(response.access_token);
    
    return response;
  },

  /**
   * Login user
   */
  async login(data: LoginData): Promise<AuthResponse> {
    const response = await apiClient.post<AuthResponse>('/auth/login', data);
    
    // Store token
    apiClient.setToken(response.access_token);
    
    return response;
  },

  /**
   * Logout user
   */
  logout() {
    apiClient.clearToken();
  },

  /**
   * Get current user info
   */
  async getCurrentUser() {
    return apiClient.get<AuthResponse['user']>('/auth/me');
  },

  /**
   * Check if user is authenticated
   */
  isAuthenticated(): boolean {
    const token = localStorage.getItem('auth_token');
    return !!token;
  }
};
