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
  /** Used to renew the access token before it expires. */
  refresh_token?: string | null;
  expires_at?: number | null;
  expires_in?: number | null;
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
    
    // Store the whole session, not just the access token, so it can be renewed
    // before it expires.
    apiClient.setSession(response);
    
    return response;
  },

  /**
   * Login user
   */
  async login(data: LoginData): Promise<AuthResponse> {
    const response = await apiClient.post<AuthResponse>('/auth/login', data);
    
    // Store the whole session, not just the access token, so it can be renewed
    // before it expires.
    apiClient.setSession(response);
    
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
    return apiClient.hasSession();
  },

  /** Renew the access token; false means the session cannot be recovered. */
  async refresh(): Promise<boolean> {
    return apiClient.refreshSession();
  },

  /** Seconds until the access token expires, or null when unknown. */
  secondsUntilExpiry(): number | null {
    return apiClient.secondsUntilExpiry();
  }
};
